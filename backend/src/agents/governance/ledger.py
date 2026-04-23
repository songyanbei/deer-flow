"""Governance Ledger — structured, persistent audit trail for all governance decisions.

Every governance decision (allow, deny, require_intervention) produces exactly
one ``GovernanceLedgerEntry``.  The ledger is the single source of truth for
Stage 5B operator queue / history queries.

Storage: per-user JSONL files at ``{data_dir}/tenants/{tid}/users/{uid}/governance_ledger.jsonl``.
Entries without a valid ``tenant_id``/``user_id`` fall back to the global
``{data_dir}/governance_ledger.jsonl``.

In-memory index provides fast lookups by thread/run/request_id/status.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from .types import GovernanceDecision, GovernanceLedgerEntry, GovernanceLedgerStatus, RiskLevel

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", ".deer-flow")
_LEDGER_FILENAME = "governance_ledger.jsonl"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _generate_governance_id() -> str:
    return f"gov_{uuid.uuid4().hex[:16]}"


# ---------------------------------------------------------------------------
# Governance Ledger
# ---------------------------------------------------------------------------

class GovernanceLedger:
    """Thread-safe governance audit ledger with JSONL persistence.

    Entries are append-only and immutable once written, except for status
    transitions (pending_intervention → resolved/rejected/failed/expired)
    which update ``status``, ``resolved_at``, and ``resolved_by``.
    """

    def __init__(self, data_dir: str | None = None) -> None:
        # RLock so that read methods can call _refresh_from_disk_if_stale()
        # while still holding the lock themselves without deadlocking.
        self._lock = threading.RLock()
        self._entries: list[GovernanceLedgerEntry] = []
        # Indexes for fast lookup
        self._by_id: dict[str, GovernanceLedgerEntry] = {}
        self._by_request_id: dict[str, GovernanceLedgerEntry] = {}
        # Per-file mtimes recorded at last load; used to detect cross-process
        # writes (LangGraph server and Gateway run in separate processes and
        # share the same JSONL files).
        self._file_mtimes: dict[str, float] = {}

        self._data_dir = data_dir or _DEFAULT_DATA_DIR
        self._global_file_path = os.path.join(self._data_dir, _LEDGER_FILENAME)
        self._load_from_disk()

    # -- File path routing ----------------------------------------------------

    def _file_path_for_entry(self, entry: GovernanceLedgerEntry) -> str:
        """Return the JSONL file path an entry should be stored in.

        Entries with a valid ``tenant_id`` (not ``"default"``) **and**
        ``user_id`` are routed to per-user files.  Everything else falls
        back to the global file.
        """
        tenant_id = entry.get("tenant_id")
        user_id = entry.get("user_id")
        if tenant_id and tenant_id != "default" and user_id:
            return os.path.join(
                self._data_dir, "tenants", tenant_id, "users", user_id, _LEDGER_FILENAME,
            )
        return self._global_file_path

    # -- Persistence ----------------------------------------------------------

    def _discover_ledger_files(self) -> list[str]:
        """Return the current list of JSONL files to track.

        Always probes the filesystem (global file + per-user glob) so that
        files created by other processes after startup are picked up.
        """
        files: list[str] = []
        if os.path.isfile(self._global_file_path):
            files.append(self._global_file_path)
        pattern = os.path.join(self._data_dir, "tenants", "*", "users", "*", _LEDGER_FILENAME)
        for path in glob.glob(pattern):
            if path not in files:
                files.append(path)
        return files

    def _load_from_disk(self) -> None:
        """Load existing entries from all JSONL files on startup.

        Scans the global ledger file **and** per-user files under
        ``tenants/*/users/*/governance_ledger.jsonl``.
        """
        with self._lock:
            files_to_load = self._discover_ledger_files()
            for fp in files_to_load:
                self._load_file(fp)

            if self._entries:
                logger.info(
                    "[GovernanceLedger] Loaded %d entries from %d file(s)",
                    len(self._entries), len(files_to_load),
                )

    def _refresh_from_disk_if_stale(self) -> None:
        """Reload the in-memory index if any tracked JSONL file changed.

        Cross-process safety: LangGraph (port 2024) and Gateway (port 8001)
        run in separate processes and share the same ``.deer-flow/`` data
        directory.  Without this mtime-based refresh, Gateway's in-memory
        index never sees entries LangGraph appends, and the Phase 2.2
        ``governance:resolve`` / ``governance:resume`` endpoints 404 on
        freshly-emitted governance entries.

        Must be called while holding ``self._lock`` (the lock is an RLock,
        so callers that already hold it are safe).
        """
        with self._lock:
            current_files = self._discover_ledger_files()
            current_mtimes: dict[str, float] = {}
            stale = False
            for fp in current_files:
                try:
                    mt = os.path.getmtime(fp)
                except OSError:
                    continue
                current_mtimes[fp] = mt
                cached_mt = self._file_mtimes.get(fp)
                if cached_mt is None or mt > cached_mt:
                    stale = True
            # A file we had tracked previously has disappeared.
            if not stale and (set(self._file_mtimes) - set(current_mtimes)):
                stale = True
            if not stale:
                return

            # Full reload — the cost is bounded by total ledger size and
            # only triggers when a foreign process has written.
            self._entries.clear()
            self._by_id.clear()
            self._by_request_id.clear()
            self._file_mtimes.clear()
            for fp in current_files:
                self._load_file(fp)

    def _load_file(self, file_path: str) -> None:
        """Load entries from a single JSONL file into the in-memory index."""
        try:
            with open(file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry: GovernanceLedgerEntry = json.loads(line)
                    self._entries.append(entry)
                    self._by_id[entry["governance_id"]] = entry
                    req_id = entry.get("request_id")
                    if req_id:
                        self._by_request_id[req_id] = entry
            # Record mtime so subsequent refresh checks know this file's
            # current state is already reflected in the in-memory index.
            try:
                self._file_mtimes[file_path] = os.path.getmtime(file_path)
            except OSError:
                pass
        except Exception:
            logger.exception("[GovernanceLedger] Failed to load ledger from %s", file_path)

    def _append_to_disk(self, entry: GovernanceLedgerEntry) -> None:
        """Append a single entry to its scoped JSONL file."""
        file_path = self._file_path_for_entry(entry)
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            # Track our own write so the next refresh check doesn't
            # pointlessly reload the file we just extended.
            try:
                self._file_mtimes[file_path] = os.path.getmtime(file_path)
            except OSError:
                pass
        except Exception:
            logger.exception("[GovernanceLedger] Failed to append entry to %s", file_path)

    def _rewrite_file(self, file_path: str, entries: list[GovernanceLedgerEntry]) -> None:
        """Rewrite a single JSONL file from a list of entries."""
        try:
            if not entries:
                # No entries left — remove the file
                if os.path.isfile(file_path):
                    os.remove(file_path)
                self._file_mtimes.pop(file_path, None)
                return
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as f:
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
            try:
                self._file_mtimes[file_path] = os.path.getmtime(file_path)
            except OSError:
                pass
        except Exception:
            logger.exception("[GovernanceLedger] Failed to rewrite ledger at %s", file_path)

    # -- Write ----------------------------------------------------------------

    def record(
        self,
        *,
        thread_id: str,
        run_id: str,
        task_id: str,
        source_agent: str,
        hook_name: str,
        source_path: str,
        risk_level: str | RiskLevel,
        category: str,
        decision: str | GovernanceDecision,
        request_id: str | None = None,
        rule_id: str | None = None,
        action_summary: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> GovernanceLedgerEntry:
        """Create and persist a new governance ledger entry.

        Returns the created entry (including generated ``governance_id``).
        """
        risk_str = risk_level.value if isinstance(risk_level, RiskLevel) else str(risk_level)
        decision_str = decision.value if isinstance(decision, GovernanceDecision) else str(decision)

        # Derive initial status from decision
        if decision_str == GovernanceDecision.REQUIRE_INTERVENTION.value:
            status: GovernanceLedgerStatus = "pending_intervention"
        else:
            status = "decided"

        entry: GovernanceLedgerEntry = {
            "governance_id": _generate_governance_id(),
            "thread_id": thread_id,
            "run_id": run_id,
            "task_id": task_id,
            "source_agent": source_agent,
            "hook_name": hook_name,
            "source_path": source_path,
            "risk_level": risk_str,
            "category": category,
            "decision": decision_str,
            "status": status,
            "rule_id": rule_id,
            "request_id": request_id,
            "action_summary": action_summary,
            "reason": reason,
            "metadata": metadata,
            "tenant_id": tenant_id or "default",
            "user_id": user_id,
            "created_at": _utc_now_iso(),
        }

        with self._lock:
            self._entries.append(entry)
            self._by_id[entry["governance_id"]] = entry
            if request_id:
                self._by_request_id[request_id] = entry

        self._append_to_disk(entry)

        logger.info(
            "[GovernanceLedger] Recorded governance_id=%s decision=%s risk=%s hook=%s agent=%s",
            entry["governance_id"], decision_str, risk_str, hook_name, source_agent,
        )
        return entry

    # -- Status transitions ---------------------------------------------------

    def resolve(
        self,
        governance_id: str | None = None,
        request_id: str | None = None,
        *,
        status: GovernanceLedgerStatus = "resolved",
        resolved_by: str = "inline",
    ) -> GovernanceLedgerEntry | None:
        """Transition a pending_intervention entry to resolved/rejected/failed/expired.

        Lookup by *governance_id* or *request_id* (at least one required).
        Only transitions entries with ``status == "pending_intervention"``.
        Returns the updated entry, or None if not found / not pending.
        """
        with self._lock:
            # Pick up cross-process writes before snapshotting for rewrite.
            self._refresh_from_disk_if_stale()
            entry = None
            if governance_id:
                entry = self._by_id.get(governance_id)
            if entry is None and request_id:
                entry = self._by_request_id.get(request_id)
            if entry is None:
                return None

            # Only transition pending entries — guard against re-resolution
            if entry["status"] != "pending_intervention":
                logger.debug(
                    "[GovernanceLedger] Skipping resolve for governance_id=%s — status is '%s', not 'pending_intervention'",
                    entry["governance_id"], entry["status"],
                )
                return None

            entry["status"] = status
            entry["resolved_at"] = _utc_now_iso()
            entry["resolved_by"] = resolved_by

            # Rewrite only the file that contains this entry
            target_file = self._file_path_for_entry(entry)
            snapshot = [e for e in self._entries if self._file_path_for_entry(e) == target_file]

        self._rewrite_file(target_file, snapshot)

        logger.info(
            "[GovernanceLedger] Resolved governance_id=%s → status=%s resolved_by=%s",
            entry["governance_id"], status, resolved_by,
        )
        return entry

    # -- Query ----------------------------------------------------------------

    def get_by_id(self, governance_id: str) -> GovernanceLedgerEntry | None:
        with self._lock:
            self._refresh_from_disk_if_stale()
            return self._by_id.get(governance_id)

    def get_by_request_id(self, request_id: str) -> GovernanceLedgerEntry | None:
        with self._lock:
            self._refresh_from_disk_if_stale()
            return self._by_request_id.get(request_id)

    def query(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        status: GovernanceLedgerStatus | None = None,
        risk_level: str | None = None,
        source_agent: str | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        resolved_from: str | None = None,
        resolved_to: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[GovernanceLedgerEntry]:
        """Query ledger entries with optional filters.

        Pass ``limit=0`` to return all matching entries (no pagination cap).

        Time range filters use ISO-8601 string comparison on ``created_at``
        and ``resolved_at`` fields.  Entries without ``resolved_at`` are
        excluded when ``resolved_from`` or ``resolved_to`` is specified.
        """
        with self._lock:
            self._refresh_from_disk_if_stale()
            results = list(self._entries)

        if tenant_id:
            results = [e for e in results if e.get("tenant_id", "default") == tenant_id]
        if user_id:
            results = [e for e in results if e.get("user_id") == user_id]
        if thread_id:
            results = [e for e in results if e["thread_id"] == thread_id]
        if run_id:
            results = [e for e in results if e["run_id"] == run_id]
        if status:
            results = [e for e in results if e["status"] == status]
        if risk_level:
            results = [e for e in results if e["risk_level"] == risk_level]
        if source_agent:
            results = [e for e in results if e.get("source_agent", "").lower() == source_agent.lower()]

        # Time range filters — ISO strings are lexicographically comparable
        if created_from:
            results = [e for e in results if e["created_at"] >= created_from]
        if created_to:
            results = [e for e in results if e["created_at"] <= created_to]
        if resolved_from:
            results = [e for e in results if (e.get("resolved_at") or "") >= resolved_from]
        if resolved_to:
            results = [e for e in results if (e.get("resolved_at") or "") and e["resolved_at"] <= resolved_to]

        # Newest first
        results.reverse()
        if limit <= 0:
            return results[offset:]
        return results[offset:offset + limit]

    def pending_count(self, thread_id: str | None = None, tenant_id: str | None = None) -> int:
        """Count entries with status=pending_intervention."""
        with self._lock:
            self._refresh_from_disk_if_stale()
            entries = self._entries
            if tenant_id:
                entries = [e for e in entries if e.get("tenant_id", "default") == tenant_id]
            if thread_id:
                entries = [e for e in entries if e["thread_id"] == thread_id]
            return sum(1 for e in entries if e["status"] == "pending_intervention")

    @property
    def total_count(self) -> int:
        with self._lock:
            self._refresh_from_disk_if_stale()
            return len(self._entries)

    # ── lifecycle API (for admin / cleanup operations) ──────────────────

    def archive_by_user(self, tenant_id: str, user_id: str) -> int:
        """Archive (remove) all entries belonging to a specific user within a tenant.

        Returns the number of entries removed.
        """
        affected_files: dict[str, list[GovernanceLedgerEntry]] = {}
        removed = 0
        with self._lock:
            kept = []
            for entry in self._entries:
                if entry.get("tenant_id", "default") == tenant_id and entry.get("user_id") == user_id:
                    self._by_id.pop(entry["governance_id"], None)
                    req_id = entry.get("request_id")
                    if req_id:
                        self._by_request_id.pop(req_id, None)
                    fp = self._file_path_for_entry(entry)
                    affected_files.setdefault(fp, [])  # mark file as affected
                    removed += 1
                else:
                    kept.append(entry)
            if removed:
                self._entries = kept
                # Build remaining-entries snapshot per affected file
                for fp in affected_files:
                    affected_files[fp] = [e for e in self._entries if self._file_path_for_entry(e) == fp]
        for fp, remaining in affected_files.items():
            self._rewrite_file(fp, remaining)
        return removed

    def purge_by_tenant(self, tenant_id: str) -> int:
        """Purge all entries belonging to a specific tenant.

        Returns the number of entries removed.
        """
        affected_files: dict[str, list[GovernanceLedgerEntry]] = {}
        removed = 0
        with self._lock:
            kept = []
            for entry in self._entries:
                if entry.get("tenant_id", "default") == tenant_id:
                    self._by_id.pop(entry["governance_id"], None)
                    req_id = entry.get("request_id")
                    if req_id:
                        self._by_request_id.pop(req_id, None)
                    fp = self._file_path_for_entry(entry)
                    affected_files.setdefault(fp, [])
                    removed += 1
                else:
                    kept.append(entry)
            if removed:
                self._entries = kept
                for fp in affected_files:
                    affected_files[fp] = [e for e in self._entries if self._file_path_for_entry(e) == fp]
        for fp, remaining in affected_files.items():
            self._rewrite_file(fp, remaining)
        return removed

    def clear(self) -> None:
        """Remove all entries (testing only)."""
        with self._lock:
            self._entries.clear()
            self._by_id.clear()
            self._by_request_id.clear()
        # Also clear disk — global file + any per-user files
        for fp in [self._global_file_path] + glob.glob(
            os.path.join(self._data_dir, "tenants", "*", "users", "*", _LEDGER_FILENAME)
        ):
            try:
                os.remove(fp)
            except OSError:
                pass

    def __repr__(self) -> str:
        return f"<GovernanceLedger entries={self.total_count}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

governance_ledger = GovernanceLedger()

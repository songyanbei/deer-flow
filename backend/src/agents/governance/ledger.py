"""Governance Ledger — structured, persistent audit trail for all governance decisions.

Every governance decision (allow, deny, require_intervention) produces exactly
one ``GovernanceLedgerEntry``.  The ledger is the single source of truth for
Stage 5B operator queue / history queries.

Storage: JSON-lines file at ``{data_dir}/governance_ledger.jsonl``.
In-memory index provides fast lookups by thread/run/request_id/status.
"""

from __future__ import annotations

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
        self._lock = threading.Lock()
        self._entries: list[GovernanceLedgerEntry] = []
        # Indexes for fast lookup
        self._by_id: dict[str, GovernanceLedgerEntry] = {}
        self._by_request_id: dict[str, GovernanceLedgerEntry] = {}

        self._data_dir = data_dir or _DEFAULT_DATA_DIR
        self._file_path = os.path.join(self._data_dir, _LEDGER_FILENAME)
        self._load_from_disk()

    # -- Persistence ----------------------------------------------------------

    def _load_from_disk(self) -> None:
        """Load existing entries from JSONL file on startup."""
        if not os.path.isfile(self._file_path):
            return
        try:
            with open(self._file_path, encoding="utf-8") as f:
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
            logger.info("[GovernanceLedger] Loaded %d entries from %s", len(self._entries), self._file_path)
        except Exception:
            logger.exception("[GovernanceLedger] Failed to load ledger from %s", self._file_path)

    def _append_to_disk(self, entry: GovernanceLedgerEntry) -> None:
        """Append a single entry to the JSONL file."""
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.exception("[GovernanceLedger] Failed to append entry to %s", self._file_path)

    def _rewrite_disk(self, snapshot: list[GovernanceLedgerEntry]) -> None:
        """Rewrite the entire JSONL file from a snapshot taken under lock."""
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            with open(self._file_path, "w", encoding="utf-8") as f:
                for entry in snapshot:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except Exception:
            logger.exception("[GovernanceLedger] Failed to rewrite ledger at %s", self._file_path)

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

            # Take snapshot under lock for safe disk rewrite
            snapshot = list(self._entries)

        self._rewrite_disk(snapshot)

        logger.info(
            "[GovernanceLedger] Resolved governance_id=%s → status=%s resolved_by=%s",
            entry["governance_id"], status, resolved_by,
        )
        return entry

    # -- Query ----------------------------------------------------------------

    def get_by_id(self, governance_id: str) -> GovernanceLedgerEntry | None:
        with self._lock:
            return self._by_id.get(governance_id)

    def get_by_request_id(self, request_id: str) -> GovernanceLedgerEntry | None:
        with self._lock:
            return self._by_request_id.get(request_id)

    def query(
        self,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        status: GovernanceLedgerStatus | None = None,
        risk_level: str | None = None,
        source_agent: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[GovernanceLedgerEntry]:
        """Query ledger entries with optional filters.

        Pass ``limit=0`` to return all matching entries (no pagination cap).
        """
        with self._lock:
            results = list(self._entries)

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

        # Newest first
        results.reverse()
        if limit <= 0:
            return results[offset:]
        return results[offset:offset + limit]

    def pending_count(self, thread_id: str | None = None) -> int:
        """Count entries with status=pending_intervention."""
        with self._lock:
            entries = self._entries
            if thread_id:
                entries = [e for e in entries if e["thread_id"] == thread_id]
            return sum(1 for e in entries if e["status"] == "pending_intervention")

    @property
    def total_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        """Remove all entries (testing only)."""
        with self._lock:
            self._entries.clear()
            self._by_id.clear()
            self._by_request_id.clear()
        # Also clear disk
        if os.path.isfile(self._file_path):
            try:
                os.remove(self._file_path)
            except OSError:
                pass

    def __repr__(self) -> str:
        return f"<GovernanceLedger entries={self.total_count}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

governance_ledger = GovernanceLedger()

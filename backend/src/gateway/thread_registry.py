"""Thread-to-tenant ownership registry (SQLite backend).

Maintains a persistent mapping of ``thread_id → metadata`` so that access
control can verify a request's tenant against the thread's owner and store
platform binding information.

The registry is stored as a SQLite database at
``{base_dir}/thread_registry.db``.  SQLite provides multi-process safety
via its built-in file-level locking (WAL mode for concurrent readers).

Design constraints:
- WAL journal mode for concurrent read performance across gunicorn workers
- ``check_same_thread=False`` so the connection can be shared within a process
- An in-process ``threading.Lock`` guards write operations to avoid interleaved
  transactions from concurrent coroutines within the same process
- Unregistered threads are treated as denied (security: deny by default)

Migration:
- On first instantiation, if a legacy ``thread_registry.json`` exists alongside
  the DB path, all entries are imported and the JSON file is renamed to ``.json.bak``.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_paths

logger = logging.getLogger(__name__)

# Re-use the same validation pattern as paths.py.
_SAFE_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_\-]+$")

# Columns that map 1:1 to the ``threads`` table (excluding JSON-serialized ones).
_SCALAR_COLUMNS = (
    "thread_id", "tenant_id", "user_id", "portal_session_id",
    "group_key", "entry_agent", "requested_orchestration_mode",
    "created_at", "updated_at",
)
# Columns stored as JSON text in the DB.
_JSON_COLUMNS = ("allowed_agents", "metadata")

_INIT_SQL = """\
CREATE TABLE IF NOT EXISTS threads (
    thread_id   TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    user_id     TEXT,
    portal_session_id TEXT,
    group_key   TEXT,
    allowed_agents TEXT,
    entry_agent TEXT,
    requested_orchestration_mode TEXT,
    metadata    TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_threads_tenant ON threads(tenant_id);
CREATE INDEX IF NOT EXISTS idx_threads_tenant_user ON threads(tenant_id, user_id);
"""


class ThreadRegistry:
    """Persistent thread → tenant mapping with access-control checks."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self._file = registry_file  # resolved lazily if None
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    # ── connection management ─────────────────────────────────────────

    @property
    def _db_path(self) -> Path:
        if self._file is not None:
            # Accept both .json (legacy tests) and .db paths — normalise to .db
            p = self._file
            if p.suffix == ".json":
                p = p.with_suffix(".db")
            return p
        return get_paths().base_dir / "thread_registry.db"

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is not None:
            return self._conn
        db_path = self._db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Migrate legacy JSON if present
        self._maybe_migrate_json(db_path)

        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_INIT_SQL)
        self._conn = conn
        return conn

    def _maybe_migrate_json(self, db_path: Path) -> None:
        """Import entries from legacy JSON file if the DB is brand new."""
        json_path = db_path.with_suffix(".json")
        if not json_path.exists():
            return
        if db_path.exists():
            # DB already exists — skip migration, leave JSON alone
            return
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return
            # Create the DB and import
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(_INIT_SQL)
            for tid, val in raw.items():
                entry = {"tenant_id": val} if isinstance(val, str) else val
                self._upsert_entry(conn, tid, entry)
            conn.commit()
            conn.close()
            # Rename JSON to .bak
            bak_path = json_path.with_suffix(".json.bak")
            json_path.rename(bak_path)
            logger.info("Migrated %d entries from %s → %s", len(raw), json_path, db_path)
        except Exception:
            logger.warning("Failed to migrate legacy JSON registry", exc_info=True)

    @staticmethod
    def _upsert_entry(conn: sqlite3.Connection, thread_id: str, entry: dict[str, Any]) -> None:
        """Insert or replace a single entry into the threads table."""
        conn.execute(
            """INSERT OR REPLACE INTO threads
               (thread_id, tenant_id, user_id, portal_session_id,
                group_key, allowed_agents, entry_agent,
                requested_orchestration_mode, metadata, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                thread_id,
                entry.get("tenant_id"),
                entry.get("user_id"),
                entry.get("portal_session_id"),
                entry.get("group_key"),
                json.dumps(entry["allowed_agents"]) if entry.get("allowed_agents") is not None else None,
                entry.get("entry_agent"),
                entry.get("requested_orchestration_mode"),
                json.dumps(entry["metadata"]) if entry.get("metadata") is not None else None,
                entry.get("created_at"),
                entry.get("updated_at"),
            ),
        )

    def _row_to_dict(self, row: sqlite3.Row | tuple, columns: list[str]) -> dict[str, Any]:
        """Convert a DB row to a metadata dict, deserializing JSON columns.

        Keys with NULL values are included so that callers can distinguish
        "field is None" from "field was never set" — matches the behaviour
        of the legacy JSON backend.
        """
        d: dict[str, Any] = {}
        for i, col in enumerate(columns):
            val = row[i]
            if col in _JSON_COLUMNS and val is not None:
                try:
                    val = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
            d[col] = val
        return d

    def _fetch_entry(self, thread_id: str) -> dict[str, Any] | None:
        """Fetch a single thread entry as a dict, or None."""
        conn = self._get_conn()
        cur = conn.execute("SELECT * FROM threads WHERE thread_id = ?", (thread_id,))
        row = cur.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cur.description]
        return self._row_to_dict(row, columns)

    # ── public API (original, backward-compatible) ─────────────────────

    # Placeholder identity values that must never overwrite a real binding.
    _WEAK_TENANT_IDS = frozenset({"default"})
    _WEAK_USER_IDS = frozenset({"anonymous"})

    def register(self, thread_id: str, tenant_id: str, user_id: str | None = None) -> None:
        """Register or update the owner of a thread.

        Identity protection: if the thread already has a concrete (non-default)
        tenant_id or user_id, a weaker fallback value (``"default"`` /
        ``"anonymous"``) will **not** overwrite it.  This prevents middleware
        fallback paths from degrading an existing binding established by the
        Gateway layer.

        Skips disk I/O when the entry is unchanged.
        """
        if not _SAFE_ID_RE.match(thread_id):
            raise ValueError(f"Invalid thread_id: {thread_id!r}")
        with self._lock:
            existing = self._fetch_entry(thread_id)
            if existing is not None:
                existing_tenant = existing.get("tenant_id")
                existing_user = existing.get("user_id")

                effective_tenant = tenant_id
                if existing_tenant and existing_tenant not in self._WEAK_TENANT_IDS and tenant_id in self._WEAK_TENANT_IDS:
                    effective_tenant = existing_tenant

                effective_user = user_id
                if existing_user and existing_user not in self._WEAK_USER_IDS and (user_id is not None and user_id in self._WEAK_USER_IDS):
                    effective_user = existing_user

                changed = existing_tenant != effective_tenant
                if effective_user and existing_user != effective_user:
                    changed = True
                if not changed:
                    return

                updated = dict(existing)
                updated["tenant_id"] = effective_tenant
                if effective_user:
                    updated["user_id"] = effective_user
                if "created_at" not in updated or updated.get("created_at") is None:
                    updated["created_at"] = datetime.now(timezone.utc).isoformat()
                self._upsert_entry(self._get_conn(), thread_id, updated)
                self._get_conn().commit()
            else:
                entry: dict[str, Any] = {
                    "tenant_id": tenant_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                if user_id:
                    entry["user_id"] = user_id
                self._upsert_entry(self._get_conn(), thread_id, entry)
                self._get_conn().commit()

    def get_tenant(self, thread_id: str) -> str | None:
        """Return the owning tenant, or ``None`` if unregistered."""
        with self._lock:
            entry = self._fetch_entry(thread_id)
            if entry is None:
                return None
            return entry.get("tenant_id")

    def check_access(self, thread_id: str, tenant_id: str, user_id: str | None = None) -> bool:
        """Return ``True`` if the caller may access the thread.

        Performs tenant + optional user dual validation:
        - Unregistered threads are **rejected** (no silent fallback).
        - Tenant mismatch → False.
        - When *user_id* is provided and the thread has a recorded user_id,
          user mismatch → False.
        - When OIDC is enabled and *user_id* is provided but the binding
          has no recorded user_id, access is **denied** (legacy entries
          without user ownership are not trusted under OIDC).
        """
        with self._lock:
            entry = self._fetch_entry(thread_id)
        if entry is None:
            return False
        owner_tenant = entry.get("tenant_id")
        if owner_tenant is not None and owner_tenant != tenant_id:
            return False
        if user_id is not None:
            owner_user = entry.get("user_id")
            if owner_user is not None and owner_user != user_id:
                return False
            if owner_user is None:
                _oidc_enabled = os.getenv("OIDC_ENABLED", "false").lower() in ("true", "1", "yes")
                if _oidc_enabled:
                    return False
        return True

    def list_threads(self, tenant_id: str) -> list[str]:
        """Return all thread IDs belonging to a tenant."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("SELECT thread_id FROM threads WHERE tenant_id = ?", (tenant_id,))
            return [row[0] for row in cur.fetchall()]

    def unregister(self, thread_id: str) -> bool:
        """Remove a thread from the registry.  Returns ``True`` if it existed."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
            conn.commit()
            return cur.rowcount > 0

    def invalidate_cache(self) -> None:
        """No-op for SQLite backend (no in-memory cache).

        Kept for backward compatibility with callers that may invoke this.
        """
        pass

    # ── extended API for platform binding metadata ─────────────────────

    def get_binding(self, thread_id: str) -> dict[str, Any] | None:
        """Return the full metadata dict for a thread, or ``None``."""
        with self._lock:
            return self._fetch_entry(thread_id)

    def register_binding(
        self,
        thread_id: str,
        *,
        tenant_id: str,
        user_id: str,
        portal_session_id: str,
        group_key: str | None = None,
        allowed_agents: list[str] | None = None,
        entry_agent: str | None = None,
        requested_orchestration_mode: str | None = None,
    ) -> dict[str, Any]:
        """Create a new thread binding with full platform metadata.

        Returns the persisted metadata dict.
        """
        if not _SAFE_ID_RE.match(thread_id):
            raise ValueError(f"Invalid thread_id: {thread_id!r}")
        now = datetime.now(timezone.utc).isoformat()
        binding: dict[str, Any] = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "portal_session_id": portal_session_id,
            "created_at": now,
            "updated_at": now,
        }
        if group_key is not None:
            binding["group_key"] = group_key
        if allowed_agents is not None:
            binding["allowed_agents"] = allowed_agents
        if entry_agent is not None:
            binding["entry_agent"] = entry_agent
        if requested_orchestration_mode is not None:
            binding["requested_orchestration_mode"] = requested_orchestration_mode
        with self._lock:
            self._upsert_entry(self._get_conn(), thread_id, binding)
            self._get_conn().commit()
        return dict(binding)

    # Fields that callers are allowed to update via ``update_binding``.
    # Identity fields (tenant_id, user_id, portal_session_id) are set at
    # creation time and must NOT be overwritable through this method.
    _UPDATABLE_FIELDS = frozenset({
        "group_key",
        "allowed_agents",
        "entry_agent",
        "requested_orchestration_mode",
        "metadata",
    })

    def update_binding(self, thread_id: str, **fields: Any) -> dict[str, Any] | None:
        """Merge *fields* into existing thread metadata.

        Only fields listed in ``_UPDATABLE_FIELDS`` are accepted.  Attempting to
        update identity fields (``tenant_id``, ``user_id``, ``portal_session_id``)
        raises ``ValueError``.

        Automatically sets ``updated_at``.  Returns the updated metadata dict,
        or ``None`` if the thread is not registered.
        """
        rejected = set(fields) - self._UPDATABLE_FIELDS
        if rejected:
            raise ValueError(
                f"Cannot update protected binding field(s): {', '.join(sorted(rejected))}. "
                f"Allowed: {', '.join(sorted(self._UPDATABLE_FIELDS))}"
            )
        with self._lock:
            entry = self._fetch_entry(thread_id)
            if entry is None:
                return None
            entry.update(fields)
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._upsert_entry(self._get_conn(), thread_id, entry)
            self._get_conn().commit()
            return dict(entry)

    # ── lifecycle API (for admin / cleanup operations) ──────────────────

    def list_threads_by_user(self, tenant_id: str, user_id: str) -> list[str]:
        """Return all thread IDs belonging to a specific user within a tenant."""
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "SELECT thread_id FROM threads WHERE tenant_id = ? AND user_id = ?",
                (tenant_id, user_id),
            )
            return [row[0] for row in cur.fetchall()]

    def delete_threads_by_user(self, tenant_id: str, user_id: str) -> int:
        """Remove all threads belonging to a specific user within a tenant.

        Returns the number of threads removed.
        """
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(
                "DELETE FROM threads WHERE tenant_id = ? AND user_id = ?",
                (tenant_id, user_id),
            )
            conn.commit()
            return cur.rowcount

    def delete_threads_by_tenant(self, tenant_id: str) -> int:
        """Remove all threads belonging to a tenant.

        Returns the number of threads removed.
        """
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute("DELETE FROM threads WHERE tenant_id = ?", (tenant_id,))
            conn.commit()
            return cur.rowcount

    def list_expired_threads(self, max_age_seconds: int, tenant_id: str | None = None) -> list[str]:
        """Return thread IDs whose ``created_at`` is older than *max_age_seconds*.

        Threads without a ``created_at`` field are treated as expired.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_seconds
        with self._lock:
            conn = self._get_conn()
            if tenant_id is not None:
                cur = conn.execute(
                    "SELECT thread_id, created_at FROM threads WHERE tenant_id = ?",
                    (tenant_id,),
                )
            else:
                cur = conn.execute("SELECT thread_id, created_at FROM threads")
            result = []
            for row in cur.fetchall():
                tid, created = row
                if created is None:
                    result.append(tid)
                    continue
                try:
                    ts = datetime.fromisoformat(created).timestamp()
                    if ts < cutoff:
                        result.append(tid)
                except (ValueError, TypeError):
                    result.append(tid)
            return result


# ── Module-level singleton ──────────────────────────────────────────────

_thread_registry: ThreadRegistry | None = None


def get_thread_registry() -> ThreadRegistry:
    """Return the global ThreadRegistry singleton."""
    global _thread_registry
    if _thread_registry is None:
        _thread_registry = ThreadRegistry()
    return _thread_registry

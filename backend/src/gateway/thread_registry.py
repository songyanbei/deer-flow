"""Thread-to-tenant ownership registry.

Maintains a persistent mapping of ``thread_id → metadata`` so that access
control can verify a request's tenant against the thread's owner and store
platform binding information.

The registry is stored as a flat JSON file at
``{base_dir}/thread_registry.json``.  All mutations are guarded by a
threading lock to prevent interleaved writes from concurrent requests.

Design constraints:
- JSON file is small (one entry per thread), acceptable for O(100k) threads
- Thread-safe within a single process via ``threading.Lock``
- File-based locking not implemented — upgrade to SQLite or Redis if needed
- Unregistered threads are treated as accessible (backward compatibility)

Backward compatibility:
- Old format stored ``thread_id → tenant_id`` (plain string).
- New format stores ``thread_id → {tenant_id, ...metadata}``.
- On load, plain-string entries are transparently promoted to ``{"tenant_id": value}``.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_paths

logger = logging.getLogger(__name__)

# Re-use the same validation pattern as paths.py.
_SAFE_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_\-]+$")


class ThreadRegistry:
    """Persistent thread → tenant mapping with access-control checks."""

    def __init__(self, registry_file: Path | None = None) -> None:
        self._file = registry_file  # resolved lazily if None
        self._lock = threading.Lock()
        self._cache: dict[str, Any] | None = None

    @property
    def _registry_file(self) -> Path:
        if self._file is not None:
            return self._file
        return get_paths().base_dir / "thread_registry.json"

    # ── read / write helpers ───────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        """Load the registry from disk, with in-memory caching.

        Transparently promotes old-format string values to metadata dicts.
        """
        if self._cache is not None:
            return self._cache
        path = self._registry_file
        if not path.exists():
            self._cache = {}
            return self._cache
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raw = {}
            # Promote old string entries → metadata dicts (safe copy)
            data = {}
            for tid, val in raw.items():
                data[tid] = {"tenant_id": val} if isinstance(val, str) else val
            self._cache = data
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read thread registry %s: %s", path, exc)
            self._cache = {}
        return self._cache

    def _save(self, data: dict[str, Any]) -> None:
        """Atomic write to the registry file."""
        path = self._registry_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self._cache = data

    # ── public API (original, backward-compatible) ─────────────────────

    def register(self, thread_id: str, tenant_id: str) -> None:
        """Register or update the owner of a thread.

        Preserves any existing metadata fields when called on a thread that
        already has a metadata entry.  Skips disk I/O when the entry is
        unchanged (eliminates ~95% of redundant writes during agent execution).
        """
        if not _SAFE_ID_RE.match(thread_id):
            raise ValueError(f"Invalid thread_id: {thread_id!r}")
        with self._lock:
            data = dict(self._load())  # shallow copy
            existing = data.get(thread_id)
            if isinstance(existing, dict):
                if existing.get("tenant_id") == tenant_id:
                    return  # already registered with the same tenant → skip write
                updated = dict(existing)  # copy inner dict to avoid mutating cache
                updated["tenant_id"] = tenant_id
                data[thread_id] = updated
            else:
                data[thread_id] = {"tenant_id": tenant_id}
            self._save(data)

    def get_tenant(self, thread_id: str) -> str | None:
        """Return the owning tenant, or ``None`` if unregistered."""
        with self._lock:
            entry = self._load().get(thread_id)
            if entry is None:
                return None
            if isinstance(entry, dict):
                return entry.get("tenant_id")
            # Shouldn't happen after _load promotion, but be safe
            return str(entry)

    def check_access(self, thread_id: str, tenant_id: str, user_id: str | None = None) -> bool:
        """Return ``True`` if the caller may access the thread.

        Performs tenant + optional user dual validation:
        - Unregistered threads are **rejected** (no silent fallback).
        - Tenant mismatch → False.
        - When *user_id* is provided and the thread has a recorded user_id,
          user mismatch → False.
        """
        with self._lock:
            entry = self._load().get(thread_id)
        if entry is None:
            return False  # unregistered thread → deny
        if isinstance(entry, dict):
            owner_tenant = entry.get("tenant_id")
            if owner_tenant is not None and owner_tenant != tenant_id:
                return False
            if user_id is not None:
                owner_user = entry.get("user_id")
                if owner_user is not None and owner_user != user_id:
                    return False
            return True
        # Legacy string entry — tenant only
        return str(entry) == tenant_id

    def list_threads(self, tenant_id: str) -> list[str]:
        """Return all thread IDs belonging to a tenant."""
        with self._lock:
            result = []
            for tid, entry in self._load().items():
                owner = entry.get("tenant_id") if isinstance(entry, dict) else entry
                if owner == tenant_id:
                    result.append(tid)
            return result

    def unregister(self, thread_id: str) -> bool:
        """Remove a thread from the registry.  Returns ``True`` if it existed."""
        with self._lock:
            data = dict(self._load())
            existed = data.pop(thread_id, None) is not None
            if existed:
                self._save(data)
            return existed

    def invalidate_cache(self) -> None:
        """Force next read to reload from disk."""
        with self._lock:
            self._cache = None

    # ── extended API for platform binding metadata ─────────────────────

    def get_binding(self, thread_id: str) -> dict[str, Any] | None:
        """Return the full metadata dict for a thread, or ``None``."""
        with self._lock:
            entry = self._load().get(thread_id)
            if entry is None:
                return None
            if isinstance(entry, dict):
                return dict(entry)
            return {"tenant_id": str(entry)}

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
            data = dict(self._load())
            data[thread_id] = binding
            self._save(data)
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
            data = dict(self._load())
            entry = data.get(thread_id)
            if entry is None:
                return None
            # Copy inner dict to avoid mutating cache before save
            entry = dict(entry) if isinstance(entry, dict) else {"tenant_id": str(entry)}
            entry.update(fields)
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            data[thread_id] = entry
            self._save(data)
            return dict(entry)


# ── Module-level singleton ──────────────────────────────────────────────

_thread_registry: ThreadRegistry | None = None


def get_thread_registry() -> ThreadRegistry:
    """Return the global ThreadRegistry singleton."""
    global _thread_registry
    if _thread_registry is None:
        _thread_registry = ThreadRegistry()
    return _thread_registry

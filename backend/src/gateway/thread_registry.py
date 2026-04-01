"""Thread-to-tenant ownership registry.

Maintains a persistent mapping of ``thread_id → tenant_id`` so that access
control can verify a request's tenant against the thread's owner.

The registry is stored as a flat JSON file at
``{base_dir}/thread_registry.json``.  All mutations are guarded by a
threading lock to prevent interleaved writes from concurrent requests.

Design constraints:
- JSON file is small (one entry per thread), acceptable for O(100k) threads
- Thread-safe within a single process via ``threading.Lock``
- File-based locking not implemented — upgrade to SQLite or Redis if needed
- Unregistered threads are treated as accessible (backward compatibility)
"""

from __future__ import annotations

import json
import logging
import threading
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
        self._cache: dict[str, str] | None = None

    @property
    def _registry_file(self) -> Path:
        if self._file is not None:
            return self._file
        return get_paths().base_dir / "thread_registry.json"

    # ── read / write helpers ───────────────────────────────────────────

    def _load(self) -> dict[str, str]:
        """Load the registry from disk, with in-memory caching."""
        if self._cache is not None:
            return self._cache
        path = self._registry_file
        if not path.exists():
            self._cache = {}
            return self._cache
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._cache = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read thread registry %s: %s", path, exc)
            self._cache = {}
        return self._cache

    def _save(self, data: dict[str, str]) -> None:
        """Atomic write to the registry file."""
        path = self._registry_file
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self._cache = data

    # ── public API ─────────────────────────────────────────────────────

    def register(self, thread_id: str, tenant_id: str) -> None:
        """Register or update the owner of a thread."""
        if not _SAFE_ID_RE.match(thread_id):
            raise ValueError(f"Invalid thread_id: {thread_id!r}")
        with self._lock:
            data = dict(self._load())  # shallow copy
            data[thread_id] = tenant_id
            self._save(data)

    def get_tenant(self, thread_id: str) -> str | None:
        """Return the owning tenant, or ``None`` if unregistered."""
        with self._lock:
            return self._load().get(thread_id)

    def check_access(self, thread_id: str, tenant_id: str) -> bool:
        """Return ``True`` if the tenant may access the thread.

        Unregistered threads are allowed through (backward compatibility with
        threads created before multi-tenancy was enabled).
        """
        owner = self.get_tenant(thread_id)
        return owner is None or owner == tenant_id

    def list_threads(self, tenant_id: str) -> list[str]:
        """Return all thread IDs belonging to a tenant."""
        with self._lock:
            return [tid for tid, owner in self._load().items() if owner == tenant_id]

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


# ── Module-level singleton ──────────────────────────────────────────────

_thread_registry: ThreadRegistry | None = None


def get_thread_registry() -> ThreadRegistry:
    """Return the global ThreadRegistry singleton."""
    global _thread_registry
    if _thread_registry is None:
        _thread_registry = ThreadRegistry()
    return _thread_registry

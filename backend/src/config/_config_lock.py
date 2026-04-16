"""Per-tenant configuration write lock and atomic file I/O.

All MCP / Skill / Agent config mutations MUST go through these helpers
to guarantee:
 1. Single-worker mutual exclusion via ``asyncio.Lock`` (per tenant+kind).
 2. Cross-worker mutual exclusion via file-level advisory lock (POSIX
    ``fcntl.flock`` / Windows ``msvcrt.locking``).
 3. Crash-safe writes via temp-file → fsync → ``os.replace``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import yaml

logger = logging.getLogger(__name__)

ResourceKind = Literal["mcp", "skill", "agent"]

_locks: dict[tuple[str, ResourceKind], asyncio.Lock] = {}
_locks_guard = asyncio.Lock()


async def _get_lock(tenant_id: str, kind: ResourceKind) -> asyncio.Lock:
    key = (tenant_id, kind)
    async with _locks_guard:
        if key not in _locks:
            _locks[key] = asyncio.Lock()
        return _locks[key]


def _flock_acquire(fd: int) -> None:
    """Acquire an exclusive advisory lock on *fd*."""
    if sys.platform == "win32":
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)


def _flock_release(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)


@asynccontextmanager
async def tenant_config_lock(
    tenant_id: str,
    kind: ResourceKind,
    lockfile: Path | None = None,
) -> AsyncIterator[None]:
    """Acquire per-tenant, per-kind configuration lock.

    Usage::

        async with tenant_config_lock(tid, "mcp", lockfile=config_path.parent / ".mcp.lock"):
            data = load(...)
            data["new"] = value
            atomic_write_json(config_path, data)

    Parameters
    ----------
    tenant_id:
        Tenant identifier (``"default"`` for platform-level).
    kind:
        Resource type being mutated.
    lockfile:
        Optional filesystem path for cross-worker advisory lock.  When
        *None*, only in-process mutual exclusion is applied.
    """
    lock = await _get_lock(tenant_id, kind)
    t0 = time.monotonic()
    await lock.acquire()
    wait_s = time.monotonic() - t0
    if wait_s > 0.05:
        logger.info("config_lock acquired tenant=%s kind=%s wait=%.3fs", tenant_id, kind, wait_s)

    fd: int | None = None
    try:
        if lockfile is not None:
            lockfile.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lockfile), os.O_CREAT | os.O_RDWR)
            await asyncio.to_thread(_flock_acquire, fd)
        yield
    finally:
        if fd is not None:
            try:
                _flock_release(fd)
            finally:
                os.close(fd)
        lock.release()


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as JSON to *path* atomically (temp + fsync + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Write *data* as YAML to *path* atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

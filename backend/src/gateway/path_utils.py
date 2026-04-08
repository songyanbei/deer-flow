"""Shared path resolution for thread virtual paths (e.g. mnt/user-data/outputs/...)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from src.config.paths import get_paths

if TYPE_CHECKING:
    from src.gateway.thread_context import ThreadContext


def resolve_thread_virtual_path_ctx(ctx: ThreadContext, virtual_path: str) -> Path:
    """Resolve a virtual path using a validated ThreadContext.

    Uses the tenant/user/thread hierarchy for path resolution.

    Raises:
        HTTPException: If the path is invalid or outside allowed directories.
    """
    try:
        return get_paths().resolve_virtual_path_ctx(ctx, virtual_path)
    except ValueError as e:
        status = 403 if "traversal" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))


def resolve_thread_virtual_path(thread_id: str, virtual_path: str) -> Path:  # DEPRECATED
    """DEPRECATED: Use resolve_thread_virtual_path_ctx instead.

    Resolve a virtual path to the actual filesystem path under thread user-data.
    """
    try:
        return get_paths().resolve_virtual_path(thread_id, virtual_path)
    except ValueError as e:
        status = 403 if "traversal" in str(e) else 400
        raise HTTPException(status_code=status, detail=str(e))

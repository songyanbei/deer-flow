"""Windows fallback for MCP stdio process creation.

In some locked-down Windows environments, async subprocess pipe creation can fail
with `PermissionError: [WinError 5] Access is denied` (e.g. when creating
overlapped named pipes). The upstream MCP Python SDK attempts to use
`anyio.open_process()` first and only falls back to `subprocess.Popen()` for
`NotImplementedError`.

DeerFlow uses MCP stdio transports for per-agent tool servers. To keep stdio MCP
usable on Windows in these environments, we patch the MCP SDK at runtime to
fallback to the `subprocess.Popen()` implementation when we detect WinError 5.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

_PATCHED = False


def apply_win32_stdio_fallback_patch() -> bool:
    """Patch MCP SDK to fallback to subprocess on WinError 5.

    Returns:
        True if patch applied (or already applied), False otherwise.
    """
    global _PATCHED
    if _PATCHED:
        return True

    if sys.platform != "win32":
        return False

    try:
        import mcp.client.stdio as stdio_mod  # type: ignore[import-not-found]
        import mcp.os.win32.utilities as win32_utils  # type: ignore[import-not-found]
    except Exception as e:
        logger.debug("[MCP] Win32 patch skipped (imports failed): %s", e)
        return False

    original = getattr(win32_utils, "create_windows_process", None)
    if original is None:
        return False

    async def patched_create_windows_process(
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        errlog: Any = None,
        cwd: Any = None,
    ):
        try:
            return await original(command, args, env, errlog, cwd)
        except PermissionError as e:
            if getattr(e, "winerror", None) != 5:
                raise

            # Fallback to the SDK's subprocess.Popen implementation.
            logger.warning(
                "[MCP] WinError 5 when creating async stdio process for %r; falling back to subprocess.Popen(). "
                "If this persists, check Windows security policy for named-pipe/overlapped I/O restrictions.",
                command,
            )

            job = getattr(win32_utils, "_create_job_object", lambda: None)()
            fallback = await win32_utils._create_windows_fallback_process(command, args, env, errlog, cwd)  # type: ignore[attr-defined]
            maybe_assign = getattr(win32_utils, "_maybe_assign_process_to_job", None)
            if maybe_assign is not None:
                try:
                    maybe_assign(fallback, job)
                except Exception as assign_err:
                    logger.debug("[MCP] Job-object assignment skipped: %s", assign_err)
            return fallback

    # Patch both the module attribute and the imported symbol inside stdio module.
    win32_utils.create_windows_process = patched_create_windows_process  # type: ignore[assignment]
    stdio_mod.create_windows_process = patched_create_windows_process  # type: ignore[assignment]

    _PATCHED = True
    logger.info("[MCP] Applied Windows stdio fallback patch (WinError 5 -> subprocess.Popen).")
    return True


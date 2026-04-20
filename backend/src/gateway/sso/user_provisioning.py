"""First-login provisioning + subsequent update of ``USER.md``.

Layout (see ``src.config.paths.Paths``)::

    tenants/{tenant_id}/users/{safe_user_id}/USER.md

Semantics:

- First login  → create the directory tree, write frontmatter + empty body,
  ``first_login_at == last_login_at == now``.
- Later logins → preserve ``first_login_at`` and everything outside the
  frontmatter block, refresh ``last_login_at`` and mutable fields (``name``,
  ``employee_no``, ``target_system``).
- Writes are atomic: a temporary file is flushed and ``os.replace``'d into
  place, so a partial write never leaves a truncated ``USER.md`` behind.

Frontmatter is parsed/emitted with ``yaml.safe_{load,dump}`` — the body is
treated as opaque bytes and never re-indented.
"""

from __future__ import annotations

import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.config.paths import get_paths
from src.gateway.sso.models import ProvisionedSsoUser

logger = logging.getLogger(__name__)

_FRONTMATTER_DELIM = "---"
_SOURCE = "moss-hub-sso"

# Per-file lock keyed by resolved path — prevents concurrent upserts on the
# same USER.md from interleaving temp-file writes.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve()) if path.exists() else str(path)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
    return lock


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_existing(raw: str) -> tuple[dict[str, Any], str]:
    """Split existing ``USER.md`` into (frontmatter_dict, body_str).

    Accepts files without frontmatter (returns empty dict + full body).
    Malformed YAML is logged and treated as an empty frontmatter, body preserved.
    """
    if not raw.startswith(_FRONTMATTER_DELIM):
        return {}, raw
    # Find closing delimiter on its own line.
    lines = raw.splitlines(keepends=True)
    if not lines:
        return {}, raw
    close_idx = None
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == _FRONTMATTER_DELIM:
            close_idx = i
            break
    if close_idx is None:
        return {}, raw
    fm_text = "".join(lines[1:close_idx])
    body = "".join(lines[close_idx + 1 :])
    try:
        loaded = yaml.safe_load(fm_text) or {}
        if not isinstance(loaded, dict):
            logger.warning("USER.md frontmatter is not a mapping; treating as empty")
            loaded = {}
    except yaml.YAMLError as exc:
        logger.warning("USER.md frontmatter YAML parse error: %s", exc)
        loaded = {}
    return loaded, body


def _render(frontmatter: dict[str, Any], body: str) -> str:
    dumped = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).rstrip() + "\n"
    if body and not body.startswith("\n"):
        body = "\n" + body
    return f"{_FRONTMATTER_DELIM}\n{dumped}{_FRONTMATTER_DELIM}{body}"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".USER.md.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def upsert_user_md(user: ProvisionedSsoUser) -> Path:
    """Create or refresh the USER.md for the given provisioned user.

    Returns the absolute path of the USER.md file.
    """
    paths = get_paths()
    target = paths.tenant_user_md_file_for_user(user.tenant_id, user.safe_user_id)
    now = _utcnow_iso()

    lock = _lock_for(target)
    with lock:
        existing_frontmatter: dict[str, Any] = {}
        body = ""
        if target.exists():
            try:
                raw = target.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("Failed to read existing USER.md %s: %s", target, exc)
                raw = ""
            existing_frontmatter, body = _parse_existing(raw)

        first_login = existing_frontmatter.get("first_login_at") or now

        frontmatter: dict[str, Any] = {
            "user_id": user.safe_user_id,
            "raw_user_id": user.raw_user_id,
            "employee_no": user.employee_no,
            "name": user.name,
            "tenant_id": user.tenant_id,
            "target_system": user.target_system,
            "first_login_at": first_login,
            "last_login_at": now,
            "source": _SOURCE,
        }

        # Preserve any caller-authored frontmatter keys that we do not own.
        for key, value in existing_frontmatter.items():
            if key not in frontmatter:
                frontmatter[key] = value

        rendered = _render(frontmatter, body)
        _atomic_write(target, rendered)
        return target

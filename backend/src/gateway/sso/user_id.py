"""``safe_user_id`` derivation.

Strategy A (locked decision — see feature doc ADR-lite):

``safe_user_id = "u_" + base32(sha256(raw_user_id))[:24]``

The result is always 26 chars, consists only of ``[A-Z2-7_]`` plus the
``"u_"`` prefix, and matches the path-safe regex used by
``src.config.paths._SAFE_THREAD_ID_RE``.
"""

from __future__ import annotations

import base64
import hashlib
import re

_SAFE_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_PREFIX = "u_"
_DIGEST_CHARS = 24


def derive_safe_user_id(raw_user_id: str) -> str:
    """Return a deterministic, path-safe user id from a moss-hub ``userId``.

    Raises ``ValueError`` if ``raw_user_id`` is empty/whitespace.
    """
    if not raw_user_id or not raw_user_id.strip():
        raise ValueError("raw_user_id must be a non-empty string")
    digest = hashlib.sha256(raw_user_id.strip().encode("utf-8")).digest()
    # base32 avoids padding issues and produces upper-case + 2-7 only.
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=")
    safe = _PREFIX + encoded[:_DIGEST_CHARS]
    if not _SAFE_RE.match(safe):
        raise ValueError(f"Derived safe_user_id is not path-safe: {safe!r}")
    return safe

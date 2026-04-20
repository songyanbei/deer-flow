"""AuthAuditLedger — append-only JSONL log for SSO / identity events.

Intentionally decoupled from :class:`src.agents.governance.ledger.GovernanceLedger`
so that authentication failures (including failures that lack a known user
context) cannot contaminate workflow governance state.

File layout:

- With ``tenant_id`` and ``user_id``:
  ``tenants/{tenant_id}/users/{user_id}/auth_audit.jsonl``
- Without a known user context (e.g. invalid bearer token, no ``sub`` claim):
  ``tenants/_unknown/auth_audit.jsonl``

Event types (per backend checklist §8):

- ``sso_login``
- ``sso_login_failed``
- ``sso_token_invalid``
- ``identity_override``

The payload must never persist raw ticket strings, secrets, or full JWTs.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.config.paths import get_paths


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if raw.lstrip("-").isdigit():
        try:
            return int(raw)
        except ValueError:  # pragma: no cover
            pass
    return default

logger = logging.getLogger(__name__)

UNKNOWN_AUTH_AUDIT_TENANT = "_unknown"


def extract_client_ip(headers: Any, client_host: str | None = None) -> str | None:
    """Return the caller IP, preferring the first ``X-Forwarded-For`` entry.

    Accepts any object that supports ``headers.get("x-forwarded-for")`` —
    Starlette request ``headers`` and plain dicts both qualify.  ``client_host``
    is the fallback when no forwarded header is present.
    """
    forwarded = headers.get("x-forwarded-for") if headers is not None else None
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return client_host or None

_EVENT_SSO_LOGIN = "sso_login"
_EVENT_SSO_LOGIN_FAILED = "sso_login_failed"
_EVENT_SSO_TOKEN_INVALID = "sso_token_invalid"
_EVENT_IDENTITY_OVERRIDE = "identity_override"

_VALID_EVENTS = frozenset(
    {
        _EVENT_SSO_LOGIN,
        _EVENT_SSO_LOGIN_FAILED,
        _EVENT_SSO_TOKEN_INVALID,
        _EVENT_IDENTITY_OVERRIDE,
    }
)

# Forbidden keys that must never be written into the audit payload.
_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {"ticket", "raw_ticket", "app_secret", "jwt", "token", "df_session"}
)


@dataclass(frozen=True)
class AuthEvent:
    """Single entry appended to an ``auth_audit.jsonl`` file."""

    event: str
    tenant_id: str | None
    user_id: str | None
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    reason: str | None = None
    client_ip: str | None = None
    user_agent: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AuthAuditLedger:
    """Thread-safe append-only writer for ``auth_audit.jsonl``."""

    _file_locks: dict[str, threading.Lock] = {}
    _file_locks_guard = threading.Lock()
    # Sliding-window counters for identity_override rate (per user/hour).
    # A secondary ``_override_last_seen`` map supports periodic sweeping so
    # that idle users do not accumulate empty deque entries forever.
    _override_history: dict[tuple[str, str], deque[float]] = defaultdict(deque)
    _override_last_seen: dict[tuple[str, str], float] = {}
    _override_guard = threading.Lock()
    _override_last_sweep: float = 0.0

    IDENTITY_OVERRIDE_THRESHOLD_PER_HOUR: int = _env_int(
        "SSO_OVERRIDE_ALERT_THRESHOLD_PER_HOUR", 5
    )
    # Maximum users tracked simultaneously — defensive cap in case the sweep
    # below misses a pathological burst.
    _OVERRIDE_HISTORY_MAX_USERS: int = _env_int(
        "SSO_OVERRIDE_HISTORY_MAX_USERS", 10_000
    )
    _OVERRIDE_SWEEP_INTERVAL_SECONDS: float = 900.0

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir  # None → derive from get_paths()

    def _path_for(self, tenant_id: str | None, user_id: str | None) -> Path:
        paths = get_paths()
        if tenant_id and user_id:
            try:
                return paths.tenant_user_dir(tenant_id, user_id) / "auth_audit.jsonl"
            except ValueError:
                # user_id / tenant_id failed safety check — fall through to unknown.
                logger.warning(
                    "AuthAuditLedger: unsafe tenant/user ids, routing to _unknown"
                )
        return paths.tenant_dir(UNKNOWN_AUTH_AUDIT_TENANT) / "auth_audit.jsonl"

    @classmethod
    def _lock_for(cls, path: Path) -> threading.Lock:
        key = str(path)
        with cls._file_locks_guard:
            lock = cls._file_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                cls._file_locks[key] = lock
        return lock

    @staticmethod
    def _scrub(payload: dict[str, Any] | None) -> dict[str, Any]:
        if not payload:
            return {}
        cleaned: dict[str, Any] = {}
        for key, value in payload.items():
            if key in _FORBIDDEN_PAYLOAD_KEYS:
                continue
            cleaned[key] = value
        return cleaned

    def append(self, event: AuthEvent) -> Path:
        """Append a validated event and return the target file path."""
        if event.event not in _VALID_EVENTS:
            raise ValueError(f"Unknown auth audit event type: {event.event!r}")

        record = {
            "event": event.event,
            "ts": event.ts,
            "tenant_id": event.tenant_id,
            "user_id": event.user_id,
            "reason": event.reason,
            "client_ip": event.client_ip,
            "user_agent": event.user_agent,
            "payload": self._scrub(event.payload),
        }
        target = self._path_for(event.tenant_id, event.user_id)

        lock = self._lock_for(target)
        with lock:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        if event.event == _EVENT_IDENTITY_OVERRIDE and event.tenant_id and event.user_id:
            self._note_override_and_maybe_alert(event.tenant_id, event.user_id)

        return target

    # -- convenience constructors -----------------------------------------

    def record_sso_login(
        self,
        *,
        tenant_id: str,
        user_id: str,
        employee_no: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> Path:
        return self.append(
            AuthEvent(
                event=_EVENT_SSO_LOGIN,
                tenant_id=tenant_id,
                user_id=user_id,
                client_ip=client_ip,
                user_agent=user_agent,
                payload={"employee_no": employee_no} if employee_no else {},
            )
        )

    def record_sso_login_failed(
        self,
        *,
        reason: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
    ) -> Path:
        return self.append(
            AuthEvent(
                event=_EVENT_SSO_LOGIN_FAILED,
                tenant_id=tenant_id,
                user_id=user_id,
                reason=reason,
                client_ip=client_ip,
                user_agent=user_agent,
            )
        )

    def record_token_invalid(
        self,
        *,
        reason: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_ip: str | None = None,
        user_agent: str | None = None,
        kid: str | None = None,
    ) -> Path:
        return self.append(
            AuthEvent(
                event=_EVENT_SSO_TOKEN_INVALID,
                tenant_id=tenant_id,
                user_id=user_id,
                reason=reason,
                client_ip=client_ip,
                user_agent=user_agent,
                payload={"kid": kid} if kid else {},
            )
        )

    def record_identity_override(
        self,
        *,
        tenant_id: str,
        user_id: str,
        tool_name: str,
        field_name: str,
        attempted_value: Any,
        enforced_value: Any,
    ) -> Path:
        return self.append(
            AuthEvent(
                event=_EVENT_IDENTITY_OVERRIDE,
                tenant_id=tenant_id,
                user_id=user_id,
                reason=f"tool={tool_name} field={field_name}",
                payload={
                    "tool": tool_name,
                    "field": field_name,
                    "attempted": attempted_value,
                    "enforced": enforced_value,
                },
            )
        )

    # -- override-rate accounting -----------------------------------------

    @classmethod
    def _note_override_and_maybe_alert(cls, tenant_id: str, user_id: str) -> None:
        now = time.time()
        horizon = now - 3600.0
        key = (tenant_id, user_id)
        with cls._override_guard:
            history = cls._override_history[key]
            while history and history[0] < horizon:
                history.popleft()
            history.append(now)
            count = len(history)
            cls._override_last_seen[key] = now
            cls._maybe_sweep_locked(now)
        if count > cls.IDENTITY_OVERRIDE_THRESHOLD_PER_HOUR:
            # A dedicated metrics/alert system consumes this warning log.
            logger.warning(
                "identity_override rate exceeded threshold "
                "(tenant=%s user=%s count=%d/hour)",
                tenant_id,
                user_id,
                count,
            )

    @classmethod
    def _maybe_sweep_locked(cls, now: float) -> None:
        """Drop stale per-user entries.  Caller must hold ``_override_guard``."""
        if (now - cls._override_last_sweep) < cls._OVERRIDE_SWEEP_INTERVAL_SECONDS:
            if len(cls._override_history) <= cls._OVERRIDE_HISTORY_MAX_USERS:
                return
        cls._override_last_sweep = now
        horizon = now - 3600.0
        stale = [
            k
            for k, last in cls._override_last_seen.items()
            if last < horizon and (not cls._override_history.get(k))
        ]
        for k in stale:
            cls._override_history.pop(k, None)
            cls._override_last_seen.pop(k, None)
        # Hard cap — evict oldest-seen entries until under the ceiling.
        overflow = len(cls._override_history) - cls._OVERRIDE_HISTORY_MAX_USERS
        if overflow > 0:
            by_last_seen = sorted(
                cls._override_last_seen.items(), key=lambda kv: kv[1]
            )
            for k, _ in by_last_seen[:overflow]:
                cls._override_history.pop(k, None)
                cls._override_last_seen.pop(k, None)


_default_ledger: AuthAuditLedger | None = None


def get_default_ledger() -> AuthAuditLedger:
    """Return a process-wide :class:`AuthAuditLedger` instance."""
    global _default_ledger
    if _default_ledger is None:
        _default_ledger = AuthAuditLedger()
    return _default_ledger

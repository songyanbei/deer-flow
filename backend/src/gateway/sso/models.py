"""Shared data classes and exceptions for the SSO integration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MossHubTicketProfile:
    """Profile returned by moss-hub verify-ticket.

    Only contains fields that come directly from moss-hub's response envelope.
    ``safe_user_id`` is *not* derived here — it is assigned by the callback
    router before the object is handed to downstream code.
    """

    raw_user_id: str
    employee_no: str
    name: str
    target_system: str


@dataclass(frozen=True)
class ProvisionedSsoUser:
    """Authenticated SSO user, assembled by the callback router.

    This is the single object passed to USER.md provisioning, JWT signing,
    and auth audit — callers must never use the raw moss-hub DTO for side
    effects.
    """

    tenant_id: str
    safe_user_id: str
    raw_user_id: str
    employee_no: str
    name: str
    target_system: str
    role: str = "member"


class SsoError(RuntimeError):
    """Base class for SSO-related errors surfaced to the callback router."""


class SsoTicketInvalid(SsoError):
    """Ticket rejected by moss-hub (B002 / B003 / B004) — maps to HTTP 401."""


class SsoUpstreamError(SsoError):
    """Upstream / config / network failure — maps to HTTP 500."""

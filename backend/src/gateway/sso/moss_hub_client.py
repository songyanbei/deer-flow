"""Moss-hub verify-ticket S2S client.

Protocol (from feature doc §Moss Hub Verify Ticket):

- ``POST {MOSS_HUB_BASE_URL}/api/open/sso/luliu/verify-ticket``
- Headers: ``X-App-Key``, ``X-Timestamp``, ``X-Nonce``, ``X-Sign``.
- Signature: ``sha256(appKey + ticket + timestamp + nonce + appSecret)``.
- Timeout: 5 seconds. No automatic retry.

Response envelope: ``{"code", "message", "data"}``.

Error mapping (must match the feature doc table):

- ``0000``                     → success.
- ``B002`` / ``B003`` / ``B004`` → ``SsoTicketInvalid`` (401).
- everything else / timeout    → ``SsoUpstreamError`` (500).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Any

import httpx

from src.gateway.sso.config import SSOConfig
from src.gateway.sso.models import (
    MossHubTicketProfile,
    SsoTicketInvalid,
    SsoUpstreamError,
)

logger = logging.getLogger(__name__)

_VERIFY_PATH = "/api/open/sso/luliu/verify-ticket"
_HTTP_TIMEOUT_SECONDS = 5.0
_EXPECTED_TARGET_SYSTEM = "luliu"

# Per the error-mapping table, these are the only "invalid ticket" codes.
_INVALID_TICKET_CODES = frozenset({"B002", "B003", "B004"})


def _sign(app_key: str, ticket: str, timestamp: str, nonce: str, app_secret: str) -> str:
    raw = f"{app_key}{ticket}{timestamp}{nonce}{app_secret}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _mask(value: str, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "***"
    return value[:keep] + "***"


def _parse_profile(data: dict[str, Any]) -> MossHubTicketProfile:
    """Validate and unpack the ``data`` sub-object returned by moss-hub."""
    required = ("userId", "employeeNo", "name", "targetSystem")
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise SsoUpstreamError(
            f"moss-hub response missing required fields: {', '.join(missing)}"
        )
    target_system = str(data["targetSystem"]).strip()
    if target_system != _EXPECTED_TARGET_SYSTEM:
        raise SsoUpstreamError(
            f"moss-hub returned unexpected targetSystem={target_system!r}"
        )
    return MossHubTicketProfile(
        raw_user_id=str(data["userId"]).strip(),
        employee_no=str(data["employeeNo"]).strip(),
        name=str(data["name"]).strip(),
        target_system=target_system,
    )


async def verify_ticket(ticket: str, *, config: SSOConfig) -> MossHubTicketProfile:
    """Verify a moss-hub ticket S2S and return the parsed profile.

    Raises:
        SsoTicketInvalid: moss-hub rejected the ticket (B002 / B003 / B004).
        SsoUpstreamError: network / timeout / unexpected envelope / other codes.
    """
    if not ticket or not ticket.strip():
        raise SsoTicketInvalid("empty ticket")
    ticket = ticket.strip()

    if not config.enabled:
        raise SsoUpstreamError("SSO is not enabled")
    if not config.moss_hub_base_url or not config.moss_hub_app_key or not config.moss_hub_app_secret:
        raise SsoUpstreamError("moss-hub client is not configured")

    timestamp = str(int(time.time() * 1000))
    nonce = secrets.token_hex(8)
    signature = _sign(
        config.moss_hub_app_key,
        ticket,
        timestamp,
        nonce,
        config.moss_hub_app_secret,
    )
    headers = {
        "Content-Type": "application/json",
        "X-App-Key": config.moss_hub_app_key,
        "X-Timestamp": timestamp,
        "X-Nonce": nonce,
        "X-Sign": signature,
    }
    url = f"{config.moss_hub_base_url}{_VERIFY_PATH}"

    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_SECONDS,
            verify=config.moss_hub_verify_ssl,
        ) as client:
            resp = await client.post(url, json={"ticket": ticket}, headers=headers)
    except httpx.TimeoutException as exc:
        logger.warning(
            "moss-hub verify-ticket timed out (ticket=%s): %s",
            _mask(ticket),
            exc,
        )
        raise SsoUpstreamError("moss-hub verify-ticket timed out") from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "moss-hub verify-ticket network error (ticket=%s): %s",
            _mask(ticket),
            exc,
        )
        raise SsoUpstreamError(f"moss-hub verify-ticket network error: {exc}") from exc

    if resp.status_code >= 500:
        logger.warning(
            "moss-hub verify-ticket upstream %s (ticket=%s)",
            resp.status_code,
            _mask(ticket),
        )
        raise SsoUpstreamError(f"moss-hub verify-ticket HTTP {resp.status_code}")

    try:
        envelope = resp.json()
    except ValueError as exc:
        raise SsoUpstreamError("moss-hub verify-ticket returned non-JSON body") from exc

    if not isinstance(envelope, dict):
        raise SsoUpstreamError("moss-hub verify-ticket envelope is not a JSON object")

    code = str(envelope.get("code", "")).strip()
    message = str(envelope.get("message", "")).strip()

    if code == "0000":
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise SsoUpstreamError("moss-hub success envelope missing 'data' object")
        return _parse_profile(data)

    if code in _INVALID_TICKET_CODES:
        logger.info(
            "moss-hub rejected ticket (code=%s, message=%s, ticket=%s)",
            code,
            message,
            _mask(ticket),
        )
        raise SsoTicketInvalid(f"moss-hub code={code}: {message}")

    logger.warning(
        "moss-hub upstream error (code=%s, message=%s, ticket=%s)",
        code or "<missing>",
        message,
        _mask(ticket),
    )
    raise SsoUpstreamError(f"moss-hub code={code or '<missing>'}: {message}")

"""SSO callback router.

Mounted as ``/api/sso`` on the gateway. The only endpoint is
``POST /api/sso/callback`` — it accepts a moss-hub ticket, verifies it,
provisions the user, mints a ``df_session`` cookie, and returns a redirect
target for the front-end SPA (``/chat`` by default).

The callback path must also appear in the auth-middleware exempt list so it
can be reached before the user has a ``df_session``.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from src.gateway.sso.audit import extract_client_ip, get_default_ledger
from src.gateway.sso.config import get_sso_config
from src.gateway.sso.jwt_signer import sign_df_session
from src.gateway.sso.models import (
    MossHubTicketProfile,
    ProvisionedSsoUser,
    SsoTicketInvalid,
    SsoUpstreamError,
)
from src.gateway.sso.moss_hub_client import verify_ticket
from src.gateway.sso.user_id import derive_safe_user_id
from src.gateway.sso.user_provisioning import upsert_user_md

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sso", tags=["sso"])


class SsoCallbackRequest(BaseModel):
    ticket: str = Field(..., description="moss-hub ticket")
    targetSystem: Optional[str] = Field(  # noqa: N815 — mirrors moss-hub naming
        default=None,
        description="Optional target system echoed by the frontend; NOT trusted.",
    )
    # NOTE: ``targetSystem`` is accepted for wire-level compatibility with the
    # frontend callback page but intentionally never read — the authoritative
    # target system is the value moss-hub returns from ``verify-ticket``.


class SsoCallbackResponse(BaseModel):
    redirect: str = "/chat"


def _client_ip(request: Request) -> str | None:
    return extract_client_ip(
        request.headers,
        request.client.host if request.client else None,
    )


def _assemble_user(profile: MossHubTicketProfile, tenant_id: str) -> ProvisionedSsoUser:
    safe_user_id = derive_safe_user_id(profile.raw_user_id)
    return ProvisionedSsoUser(
        tenant_id=tenant_id,
        safe_user_id=safe_user_id,
        raw_user_id=profile.raw_user_id,
        employee_no=profile.employee_no,
        name=profile.name,
        target_system=profile.target_system,
    )


@router.post(
    "/callback",
    response_model=SsoCallbackResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Ticket invalid or expired"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "SSO unavailable"},
    },
)
async def sso_callback(
    payload: SsoCallbackRequest,
    request: Request,
    response: Response,
) -> SsoCallbackResponse:
    """Verify a moss-hub ticket and mint the ``df_session`` cookie."""
    config = get_sso_config()
    ledger = get_default_ledger()

    if not config.enabled:
        logger.warning("SSO callback invoked while SSO_ENABLED=false")
        ledger.record_sso_login_failed(
            reason="sso_disabled",
            client_ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
        raise HTTPException(status_code=500, detail="SSO unavailable")

    if not payload.ticket or not payload.ticket.strip():
        raise HTTPException(status_code=400, detail="ticket is required")

    client_ip = _client_ip(request)
    user_agent = request.headers.get("user-agent")

    try:
        profile = await verify_ticket(payload.ticket, config=config)
    except SsoTicketInvalid as exc:
        ledger.record_sso_login_failed(
            reason=f"ticket_invalid: {exc}",
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=401, detail="login link expired") from exc
    except SsoUpstreamError as exc:
        ledger.record_sso_login_failed(
            reason=f"upstream_error: {exc}",
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=500, detail="SSO unavailable") from exc

    try:
        user = _assemble_user(profile, tenant_id=config.tenant_id)
    except ValueError as exc:
        ledger.record_sso_login_failed(
            reason=f"safe_user_id_error: {exc}",
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=500, detail="SSO unavailable") from exc

    try:
        upsert_user_md(user)
    except Exception as exc:  # pragma: no cover — storage failure path
        logger.exception("USER.md upsert failed for %s", user.safe_user_id)
        ledger.record_sso_login_failed(
            reason=f"provisioning_error: {exc}",
            tenant_id=user.tenant_id,
            user_id=user.safe_user_id,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=500, detail="SSO unavailable") from exc

    try:
        token = sign_df_session(user, config=config)
    except Exception as exc:  # pragma: no cover — signing failure path
        logger.exception("JWT signing failed for %s", user.safe_user_id)
        ledger.record_sso_login_failed(
            reason=f"jwt_signing_error: {exc}",
            tenant_id=user.tenant_id,
            user_id=user.safe_user_id,
            client_ip=client_ip,
            user_agent=user_agent,
        )
        raise HTTPException(status_code=500, detail="SSO unavailable") from exc

    response.set_cookie(
        key=config.cookie_name,
        value=token,
        max_age=config.jwt_ttl,
        httponly=True,
        secure=config.cookie_secure,
        samesite="lax",
        domain=config.cookie_domain or None,
        path="/",
    )

    ledger.record_sso_login(
        tenant_id=user.tenant_id,
        user_id=user.safe_user_id,
        employee_no=user.employee_no,
        client_ip=client_ip,
        user_agent=user_agent,
    )

    return SsoCallbackResponse(redirect="/chat")

"""DeerFlow internal JWT signing / verification (``df_session``).

Tokens are minted by the SSO callback and carried by the browser as the
``df_session`` cookie. They are HS256-signed with ``DEERFLOW_JWT_SECRET``
and include the header ``kid=df-internal-v1`` so that the auth middleware
can distinguish them from external JWKS-verified tokens.

Claims (per backend checklist §4):

- ``iss``                = ``deer-flow``
- ``sub``                = ``safe_user_id``
- ``tenant_id``          = ``moss-hub`` (per config)
- ``preferred_username`` = display name
- ``employee_no``        = moss-hub ``employeeNo``
- ``target_system``      = moss-hub ``targetSystem``
- ``role``               = ``member`` (default)
- ``iat`` / ``exp``      = issued / expiry timestamps

Silent refresh is intentionally not implemented.
"""

from __future__ import annotations

import time
from typing import Any

from jose import JWTError, jwt

from src.gateway.sso.config import SSOConfig
from src.gateway.sso.models import ProvisionedSsoUser

INTERNAL_KID = "df-internal-v1"
INTERNAL_ISS = "deer-flow"
INTERNAL_ALG = "HS256"


def sign_df_session(user: ProvisionedSsoUser, *, config: SSOConfig, now: float | None = None) -> str:
    """Mint an internal HS256 JWT for the provisioned SSO user."""
    if not config.jwt_secret:
        raise RuntimeError("DEERFLOW_JWT_SECRET is not configured")
    issued_at = int(now if now is not None else time.time())
    payload: dict[str, Any] = {
        "iss": INTERNAL_ISS,
        "sub": user.safe_user_id,
        "tenant_id": user.tenant_id,
        "preferred_username": user.name,
        "employee_no": user.employee_no,
        "target_system": user.target_system,
        "role": user.role,
        "iat": issued_at,
        "exp": issued_at + int(config.jwt_ttl),
    }
    return jwt.encode(
        payload,
        config.jwt_secret,
        algorithm=INTERNAL_ALG,
        headers={"kid": INTERNAL_KID},
    )


def verify_df_session(token: str, *, config: SSOConfig) -> dict[str, Any]:
    """Verify an internal ``df_session`` token and return its claims.

    Only tokens with ``kid=df-internal-v1`` and ``alg=HS256`` are accepted.
    ``iss`` is validated against ``deer-flow``.

    Raises ``jose.JWTError`` on any failure.
    """
    if not config.jwt_secret:
        raise JWTError("DEERFLOW_JWT_SECRET is not configured")
    unverified = jwt.get_unverified_header(token)
    if unverified.get("kid") != INTERNAL_KID:
        raise JWTError("kid is not df-internal-v1")
    if unverified.get("alg") != INTERNAL_ALG:
        raise JWTError("internal token alg must be HS256")
    return jwt.decode(
        token,
        config.jwt_secret,
        algorithms=[INTERNAL_ALG],
        issuer=INTERNAL_ISS,
        options={"verify_exp": True, "verify_iss": True, "verify_aud": False},
    )


def is_internal_token(token: str) -> bool:
    """Return ``True`` if the token header advertises ``kid=df-internal-v1``.

    Used by the auth middleware to branch between local HS256 verification
    and the external JWKS flow. Never raises — returns ``False`` on any
    header-parse failure.
    """
    try:
        header = jwt.get_unverified_header(token)
    except JWTError:
        return False
    return header.get("kid") == INTERNAL_KID

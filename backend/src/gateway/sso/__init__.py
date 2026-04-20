"""Moss Hub SSO integration for DeerFlow gateway.

Public submodules:

- ``config``          — load/validate SSO environment configuration.
- ``models``          — data classes and exceptions shared across SSO modules.
- ``moss_hub_client`` — S2S verify-ticket client.
- ``user_id``         — safe_user_id derivation.
- ``jwt_signer``      — internal HS256 JWT sign/verify.
- ``user_provisioning`` — USER.md upsert.
- ``audit``           — AuthAuditLedger for sso_* and identity_* events.
"""

"""Runtime identity guard for tool invocations.

Wraps every tool exposed to the model so that a user-supplied value for any
identity-bearing argument (``caller``, ``employeeNo``, ``userId``,
``organizer`` …) cannot override the authenticated principal. The guard:

1. Replaces identity fields in ``tool_args`` with values from
   :class:`AuthenticatedUser` before invoking the underlying tool.
2. Records an ``identity_override`` event whenever a supplied value
   disagreed with the enforced value (likely social-engineering attempt).
3. Fails closed when no authenticated principal is available and the
   invoked tool has any identity field — callers must not "just pass
   through" anonymous requests to tools whose contract expects a caller.

MCP ``args_schema`` filtering lives alongside this guard: removing identity
fields from the schema keeps the model from *thinking* they exist, but the
runtime enforcement here is the hard boundary.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable

from langchain_core.tools import BaseTool, StructuredTool

from src.gateway.sso.audit import get_default_ledger

logger = logging.getLogger(__name__)

# Canonical identity fields. Any tool argument whose name matches one of
# these (case-sensitive) is considered identity-bearing and is overwritten
# from the authenticated principal before the tool runs.
IDENTITY_FIELDS: dict[str, str] = {
    "employeeNo": "employee_no",
    "employee_no": "employee_no",
    "organizer": "employee_no",
    "caller": "employee_no",
    "userId": "user_id",
    "user_id": "user_id",
    "operator": "employee_no",
    "createdBy": "employee_no",
    "on_behalf_of": "employee_no",
}


class IdentityMissingError(RuntimeError):
    """Raised when a tool requires identity fields but none is available."""


def _auth_value(auth_user: Any, attr: str) -> Any:
    """Return the enforced value for the given canonical identity attribute.

    ``auth_user`` can be a :class:`AuthenticatedUser` dataclass, a dict, or
    any object that exposes the expected attributes. We accept duck-typing so
    the guard can be used both inside FastAPI request context and inside
    LangGraph ``config.configurable``.
    """
    if auth_user is None:
        return None
    if isinstance(auth_user, dict):
        return auth_user.get(attr)
    return getattr(auth_user, attr, None)


def _identity_fields_in_schema(args_schema: Any) -> list[str]:
    if args_schema is None:
        return []
    found: set[str] = set()
    model_fields = getattr(args_schema, "model_fields", None)
    if isinstance(model_fields, dict):
        for field_name in model_fields.keys():
            if field_name in IDENTITY_FIELDS:
                found.add(field_name)
    for attr in ("schema_", "schema", "json_schema"):
        raw = getattr(args_schema, attr, None)
        if isinstance(raw, dict):
            props = raw.get("properties") or {}
            for field_name in props.keys():
                if field_name in IDENTITY_FIELDS:
                    found.add(field_name)
    return sorted(found)


def enforce_identity(
    tool_name: str,
    tool_args: dict[str, Any] | None,
    auth_user: Any,
    *,
    declared_identity_fields: Iterable[str] = (),
) -> dict[str, Any]:
    """Return a new ``tool_args`` dict with identity fields enforced.

    ``declared_identity_fields`` — identity fields that the tool's schema
    declares (passed through from :func:`wrap_tool`). Even if the model did
    not supply them, they are injected from ``auth_user`` to keep the tool
    callable without the model having any lever.

    Audits any override and raises :class:`IdentityMissingError` when the
    tool carries an identity field but the principal cannot supply a value.
    """
    if tool_args is None:
        tool_args = {}
    # Dev mode: no real principal — skip enforcement.  Production SSO /
    # OIDC paths always supply a concrete principal via
    # ``config.configurable["auth_user"]``.
    if _is_dev_mode_principal(auth_user):
        return dict(tool_args)
    fields_to_enforce = set(declared_identity_fields)
    for key in tool_args.keys():
        if key in IDENTITY_FIELDS:
            fields_to_enforce.add(key)
    if not fields_to_enforce:
        return dict(tool_args)
    overrides_in_args = list(fields_to_enforce)

    if auth_user is None:
        raise IdentityMissingError(
            f"Tool {tool_name!r} received identity fields {overrides_in_args} "
            "but no authenticated principal is available"
        )

    tenant_id = _auth_value(auth_user, "tenant_id")
    user_id = _auth_value(auth_user, "user_id")
    ledger = get_default_ledger()
    enforced = dict(tool_args)

    for field_name in overrides_in_args:
        canonical = IDENTITY_FIELDS[field_name]
        enforced_value = _auth_value(auth_user, canonical)
        if enforced_value in (None, ""):
            raise IdentityMissingError(
                f"Tool {tool_name!r} requires identity field {field_name!r} "
                f"but authenticated principal has no {canonical}"
            )
        attempted = tool_args.get(field_name)
        if attempted != enforced_value:
            logger.info(
                "identity_override: tool=%s field=%s attempted=%r enforced=%r",
                tool_name,
                field_name,
                attempted,
                enforced_value,
            )
            if tenant_id and user_id:
                try:
                    ledger.record_identity_override(
                        tenant_id=str(tenant_id),
                        user_id=str(user_id),
                        tool_name=tool_name,
                        field_name=field_name,
                        attempted_value=attempted,
                        enforced_value=enforced_value,
                    )
                except Exception:  # pragma: no cover
                    logger.exception("Failed to record identity_override audit")
        enforced[field_name] = enforced_value

    return enforced


def _is_dev_mode_principal(auth_user: Any) -> bool:
    """Return True for the dev-mode fallback principal.

    When neither ``OIDC_ENABLED`` nor ``SSO_ENABLED`` is set the gateway
    dependencies return ``AuthenticatedUser(user_id="anonymous", ...)``
    with no ``employee_no``. In that case we cannot fail-closed — there is
    no real principal to enforce against and the feature is disabled.
    """
    if auth_user is None:
        return False
    user_id = _auth_value(auth_user, "user_id")
    employee_no = _auth_value(auth_user, "employee_no")
    tenant_id = _auth_value(auth_user, "tenant_id")
    return (
        (user_id in (None, "", "anonymous"))
        and (employee_no in (None, ""))
        and (tenant_id in (None, "", "default"))
    )


def wrap_tool(tool: BaseTool, auth_user: Any) -> BaseTool:
    """Wrap a tool so identity fields in its args are enforced before run.

    The wrapper is a :class:`StructuredTool` that preserves the inner tool's
    ``name``, ``description``, ``args_schema`` and ``return_direct`` so the
    model sees an identical contract.

    In dev mode (auth disabled) the guard is a no-op — wrapping the tool
    would otherwise fail-closed on every identity field since the anonymous
    principal carries no ``employee_no`` to enforce.
    """
    if _is_dev_mode_principal(auth_user):
        return tool
    inner = tool
    inner_name = getattr(inner, "name", inner.__class__.__name__)
    declared = _identity_fields_in_schema(getattr(inner, "args_schema", None))

    def _sync_run(**kwargs: Any) -> Any:
        enforced = enforce_identity(
            inner_name, kwargs, auth_user, declared_identity_fields=declared
        )
        return inner.invoke(enforced)

    async def _async_run(**kwargs: Any) -> Any:
        enforced = enforce_identity(
            inner_name, kwargs, auth_user, declared_identity_fields=declared
        )
        return await inner.ainvoke(enforced)

    kwargs: dict[str, Any] = {
        "func": _sync_run,
        "coroutine": _async_run,
        "name": inner_name,
        "description": getattr(inner, "description", inner_name),
    }
    args_schema = getattr(inner, "args_schema", None)
    if args_schema is not None:
        kwargs["args_schema"] = args_schema
    return_direct = getattr(inner, "return_direct", False)
    if return_direct:
        kwargs["return_direct"] = True
    return StructuredTool.from_function(**kwargs)


def wrap_tools(tools: Iterable[BaseTool], auth_user: Any) -> list[BaseTool]:
    """Wrap a list of tools with :func:`wrap_tool`.

    Tools without any identity-bearing argument in their signature are still
    wrapped — the guard is a no-op in that case but keeps the contract
    uniform (and lets us audit regardless of which tool happened to expose
    the field).
    """
    return [wrap_tool(tool, auth_user) for tool in tools]


# ── MCP schema filtering ────────────────────────────────────────────────


def filter_mcp_schema(tool: BaseTool) -> BaseTool:
    """Drop identity fields from an MCP tool's ``args_schema``.

    Best-effort: when the tool exposes a pydantic ``args_schema``, remove
    matching fields from ``model_fields`` and the JSON-schema ``required``
    list. Runtime enforcement in :func:`enforce_identity` remains
    authoritative — this filter just removes the temptation from the model.
    """
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is None:
        return tool

    changed = False
    # pydantic v2 model_fields
    model_fields = getattr(args_schema, "model_fields", None)
    if isinstance(model_fields, dict):
        for field_name in list(model_fields.keys()):
            if field_name in IDENTITY_FIELDS:
                try:
                    del model_fields[field_name]
                    changed = True
                except Exception:
                    logger.debug("Unable to remove field %s from %s", field_name, args_schema)

    # Some MCP adapters carry a raw JSON schema dict alongside the pydantic model.
    for attr in ("schema_", "schema", "json_schema"):
        raw = getattr(args_schema, attr, None)
        if isinstance(raw, dict):
            props = raw.get("properties")
            if isinstance(props, dict):
                for field_name in list(props.keys()):
                    if field_name in IDENTITY_FIELDS:
                        props.pop(field_name, None)
                        changed = True
            required = raw.get("required")
            if isinstance(required, list):
                filtered = [f for f in required if f not in IDENTITY_FIELDS]
                if len(filtered) != len(required):
                    raw["required"] = filtered
                    changed = True

    if changed:
        note = "\n(Identity fields are injected by the system and MUST NOT be supplied by the model.)"
        desc = getattr(tool, "description", None)
        if desc and note not in desc:
            try:
                tool.description = desc + note
            except Exception:
                pass
    return tool


def filter_mcp_schemas(tools: Iterable[BaseTool]) -> list[BaseTool]:
    return [filter_mcp_schema(t) for t in tools]

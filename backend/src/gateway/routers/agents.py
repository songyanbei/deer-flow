"""CRUD API for custom agents.

When OIDC is enabled, agent storage is scoped per-tenant under
``tenants/{tenant_id}/agents/``.  When OIDC is disabled (``tenant_id ==
"default"``), the global ``agents/`` directory is used for backward
compatibility.
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.agents.lead_agent.engine_registry import normalize_engine_type
from src.config.agents_config import AgentConfig, McpBindingConfig, list_custom_agents, load_agent_config, load_agent_soul
from src.config.paths import get_paths
from src.gateway.dependencies import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["agents"])

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
DEFAULT_PROMPT_FILE = "SOUL.md"
RequestedOrchestrationMode = Literal["auto", "leader", "workflow"]


class AgentResponse(BaseModel):
    """Response model for a custom agent."""

    name: str = Field(..., description="Agent name (hyphen-case)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    engine_type: str | None = Field(default=None, description="Engine type (canonical value: default, react, read_only_explorer, sop)")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    domain: str | None = Field(default=None, description="Domain handled by this agent")
    system_prompt_file: str | None = Field(default=None, description="Prompt file used for the agent system prompt")
    hitl_keywords: list[str] | None = Field(default=None, description="Human-in-the-loop escalation keywords")
    max_tool_calls: int | None = Field(default=None, description="Maximum tool calls allowed for the agent")
    mcp_binding: McpBindingConfig | None = Field(default=None, description="Declarative MCP binding (references servers in extensions_config.json)")
    available_skills: list[str] | None = Field(default=None, description="Optional skill allowlist for this agent")
    requested_orchestration_mode: RequestedOrchestrationMode | None = Field(default=None, description="Default orchestration mode for the agent")
    soul: str | None = Field(default=None, description="System prompt content (included on GET /{name})")


class AgentsListResponse(BaseModel):
    """Response model for listing all custom agents."""

    agents: list[AgentResponse]


class AgentCreateRequest(BaseModel):
    """Request body for creating a custom agent."""

    name: str = Field(..., description="Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    engine_type: str | None = Field(default=None, description="Engine type (accepts alias, persisted as canonical value)")
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    domain: str | None = Field(default=None, description="Domain handled by this agent")
    system_prompt_file: str | None = Field(default=None, description="Prompt file used for the agent system prompt")
    hitl_keywords: list[str] | None = Field(default=None, description="Human-in-the-loop escalation keywords")
    max_tool_calls: int | None = Field(default=None, description="Maximum tool calls allowed for the agent")
    mcp_binding: McpBindingConfig | None = Field(default=None, description="Declarative MCP binding (references servers in extensions_config.json)")
    available_skills: list[str] | None = Field(default=None, description="Optional skill allowlist for this agent")
    requested_orchestration_mode: RequestedOrchestrationMode | None = Field(default=None, description="Default orchestration mode for the agent")
    soul: str = Field(default="", description="System prompt content for the agent")


class AgentUpdateRequest(BaseModel):
    """Request body for updating a custom agent."""

    description: str | None = Field(default=None, description="Updated description")
    model: str | None = Field(default=None, description="Updated model override")
    engine_type: str | None = Field(default=None, description="Updated engine type (accepts alias, persisted as canonical value)")
    tool_groups: list[str] | None = Field(default=None, description="Updated tool group whitelist")
    domain: str | None = Field(default=None, description="Updated domain")
    system_prompt_file: str | None = Field(default=None, description="Updated prompt file name")
    hitl_keywords: list[str] | None = Field(default=None, description="Updated HITL keywords")
    max_tool_calls: int | None = Field(default=None, description="Updated maximum tool calls")
    mcp_binding: McpBindingConfig | None = Field(default=None, description="Updated MCP binding")
    available_skills: list[str] | None = Field(default=None, description="Updated skill allowlist")
    requested_orchestration_mode: RequestedOrchestrationMode | None = Field(default=None, description="Updated default orchestration mode")
    soul: str | None = Field(default=None, description="Updated system prompt content")


class UserProfileResponse(BaseModel):
    """Response model for the global user profile (USER.md)."""

    content: str | None = Field(default=None, description="USER.md content, or null if not yet created")


class UserProfileUpdateRequest(BaseModel):
    """Request body for setting the global user profile."""

    content: str = Field(default="", description="USER.md content that describes the user's background and preferences")


def _resolve_agents_dir(tenant_id: str) -> Path:
    """Return the agents directory for the given tenant.

    When *tenant_id* is ``"default"`` (i.e. OIDC is disabled), falls back to the
    global ``agents/`` directory for backward compatibility.
    """
    paths = get_paths()
    if tenant_id and tenant_id != "default":
        return paths.tenant_agents_dir(tenant_id)
    return paths.agents_dir


def _resolve_agent_dir(tenant_id: str, name: str) -> Path:
    """Return a specific agent's directory under the tenant scope."""
    return _resolve_agents_dir(tenant_id) / name.lower()


def _validate_agent_name(name: str) -> None:
    if not AGENT_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid agent name '{name}'. Must match ^[A-Za-z0-9-]+$ (letters, digits, and hyphens only).",
        )


def _normalize_agent_name(name: str) -> str:
    return name.lower()


def _prompt_filename(filename: str | None) -> str:
    normalized = (filename or DEFAULT_PROMPT_FILE).strip()
    if not normalized:
        return DEFAULT_PROMPT_FILE
    if Path(normalized).name != normalized:
        raise HTTPException(status_code=422, detail="system_prompt_file must be a simple filename")
    return normalized


def _build_config_data(
    *,
    name: str,
    description: str,
    model: str | None,
    engine_type: str | None,
    tool_groups: list[str] | None,
    domain: str | None,
    system_prompt_file: str | None,
    hitl_keywords: list[str] | None,
    max_tool_calls: int | None,
    mcp_binding: McpBindingConfig | None,
    available_skills: list[str] | None,
    requested_orchestration_mode: RequestedOrchestrationMode | None,
) -> dict[str, Any]:
    config_data: dict[str, Any] = {"name": name, "description": description}
    if model is not None:
        config_data["model"] = model
    if engine_type is not None:
        # Persist as canonical value
        config_data["engine_type"] = normalize_engine_type(engine_type) or engine_type
    if tool_groups is not None:
        config_data["tool_groups"] = tool_groups
    if domain is not None:
        config_data["domain"] = domain
    if system_prompt_file is not None:
        config_data["system_prompt_file"] = _prompt_filename(system_prompt_file)
    if hitl_keywords is not None:
        config_data["hitl_keywords"] = hitl_keywords
    if max_tool_calls is not None:
        config_data["max_tool_calls"] = max_tool_calls
    if mcp_binding is not None:
        config_data["mcp_binding"] = mcp_binding.model_dump()
    if available_skills is not None:
        config_data["available_skills"] = available_skills
    if requested_orchestration_mode is not None:
        config_data["requested_orchestration_mode"] = requested_orchestration_mode
    return config_data


def _write_config(agent_dir: Path, config_data: dict[str, Any]) -> None:
    config_file = agent_dir / "config.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _write_prompt_file(agent_dir: Path, prompt_file: str | None, content: str) -> None:
    (agent_dir / _prompt_filename(prompt_file)).write_text(content, encoding="utf-8")


def _migrate_prompt_file_if_needed(agent_dir: Path, old_name: str | None, new_name: str | None) -> None:
    old_prompt = _prompt_filename(old_name)
    new_prompt = _prompt_filename(new_name)
    if old_prompt == new_prompt:
        return

    old_path = agent_dir / old_prompt
    new_path = agent_dir / new_prompt
    if new_path.exists() or not old_path.exists():
        return
    new_path.write_text(old_path.read_text(encoding="utf-8"), encoding="utf-8")


def _agent_config_to_response(agent_cfg: AgentConfig, include_soul: bool = False, agent_dir: Path | None = None) -> AgentResponse:
    soul: str | None = None
    if include_soul:
        if agent_dir is not None:
            # Tenant-scoped: read soul directly from the agent_dir
            prompt_file = agent_cfg.system_prompt_file or DEFAULT_PROMPT_FILE
            soul_path = agent_dir / prompt_file
            soul = soul_path.read_text(encoding="utf-8").strip() if soul_path.exists() else ""
        else:
            soul = load_agent_soul(agent_cfg.name, agents_dir=agents_dir) or ""

    return AgentResponse(
        name=agent_cfg.name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        engine_type=normalize_engine_type(agent_cfg.engine_type),
        tool_groups=agent_cfg.tool_groups,
        domain=agent_cfg.domain,
        system_prompt_file=agent_cfg.system_prompt_file,
        hitl_keywords=agent_cfg.hitl_keywords,
        max_tool_calls=agent_cfg.max_tool_calls,
        mcp_binding=agent_cfg.mcp_binding,
        available_skills=agent_cfg.available_skills,
        requested_orchestration_mode=agent_cfg.requested_orchestration_mode,
        soul=soul,
    )


@router.get(
    "/agents",
    response_model=AgentsListResponse,
    summary="List Custom Agents",
    description="List all custom agents available in the agents directory.",
)
async def list_agents(
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> AgentsListResponse:
    try:
        agents_dir = _resolve_agents_dir(tenant_id)
        agents = list_custom_agents(agents_dir=agents_dir)
        return AgentsListResponse(agents=[_agent_config_to_response(a) for a in agents])
    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list agents: {str(e)}")


@router.get(
    "/agents/check",
    summary="Check Agent Name",
    description="Validate an agent name and check if it is available (case-insensitive).",
)
async def check_agent_name(
    name: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> dict:
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    available = not _resolve_agent_dir(tenant_id, normalized).exists()
    return {"available": available, "name": normalized}


@router.get(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Get Custom Agent",
    description="Retrieve details and prompt content for a specific custom agent.",
)
async def get_agent(
    name: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> AgentResponse:
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    agents_dir = _resolve_agents_dir(tenant_id)

    try:
        agent_cfg = load_agent_config(name, agents_dir=agents_dir)
        return _agent_config_to_response(agent_cfg, include_soul=True, agent_dir=agents_dir / name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as e:
        logger.error(f"Failed to get agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get agent: {str(e)}")


@router.post(
    "/agents",
    response_model=AgentResponse,
    status_code=201,
    summary="Create Custom Agent",
    description="Create a new custom agent with its config and system prompt file.",
)
async def create_agent_endpoint(
    body: AgentCreateRequest,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> AgentResponse:
    _validate_agent_name(body.name)
    normalized_name = _normalize_agent_name(body.name)
    agents_dir = _resolve_agents_dir(tenant_id)

    agent_dir = agents_dir / normalized_name
    if agent_dir.exists():
        raise HTTPException(status_code=409, detail=f"Agent '{normalized_name}' already exists")

    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        config_data = _build_config_data(
            name=normalized_name,
            description=body.description,
            model=body.model,
            engine_type=body.engine_type,
            tool_groups=body.tool_groups,
            domain=body.domain,
            system_prompt_file=body.system_prompt_file,
            hitl_keywords=body.hitl_keywords,
            max_tool_calls=body.max_tool_calls,
            mcp_binding=body.mcp_binding,
            available_skills=body.available_skills,
            requested_orchestration_mode=body.requested_orchestration_mode,
        )
        _write_config(agent_dir, config_data)
        _write_prompt_file(agent_dir, body.system_prompt_file, body.soul)

        logger.info("Created agent '%s' at %s (tenant=%s)", normalized_name, agent_dir, tenant_id)
        agent_cfg = load_agent_config(normalized_name, agents_dir=agents_dir)
        return _agent_config_to_response(agent_cfg, include_soul=True, agent_dir=agent_dir)
    except HTTPException:
        raise
    except Exception as e:
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
        logger.error(f"Failed to create agent '{body.name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")


@router.put(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Update Custom Agent",
    description="Update an existing custom agent's config and/or prompt file.",
)
async def update_agent(
    name: str,
    body: AgentUpdateRequest,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> AgentResponse:
    _validate_agent_name(name)
    name = _normalize_agent_name(name)
    agents_dir = _resolve_agents_dir(tenant_id)

    try:
        agent_cfg = load_agent_config(name, agents_dir=agents_dir)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    agent_dir = agents_dir / name
    requested_mode_provided = "requested_orchestration_mode" in body.model_fields_set

    try:
        next_prompt_file = body.system_prompt_file if body.system_prompt_file is not None else agent_cfg.system_prompt_file
        engine_type_provided = "engine_type" in body.model_fields_set
        config_changed = any(
            value is not None
            for value in [
                body.description,
                body.model,
                body.tool_groups,
                body.domain,
                body.system_prompt_file,
                body.hitl_keywords,
                body.max_tool_calls,
                body.mcp_binding,
                body.available_skills,
            ]
        ) or requested_mode_provided or engine_type_provided

        if config_changed:
            updated = _build_config_data(
                name=agent_cfg.name,
                description=body.description if body.description is not None else agent_cfg.description,
                model=body.model if body.model is not None else agent_cfg.model,
                engine_type=body.engine_type if engine_type_provided else agent_cfg.engine_type,
                tool_groups=body.tool_groups if body.tool_groups is not None else agent_cfg.tool_groups,
                domain=body.domain if body.domain is not None else agent_cfg.domain,
                system_prompt_file=next_prompt_file,
                hitl_keywords=body.hitl_keywords if body.hitl_keywords is not None else agent_cfg.hitl_keywords,
                max_tool_calls=body.max_tool_calls if body.max_tool_calls is not None else agent_cfg.max_tool_calls,
                mcp_binding=body.mcp_binding if body.mcp_binding is not None else agent_cfg.mcp_binding,
                available_skills=body.available_skills if body.available_skills is not None else agent_cfg.available_skills,
                requested_orchestration_mode=body.requested_orchestration_mode if requested_mode_provided else agent_cfg.requested_orchestration_mode,
            )
            _write_config(agent_dir, updated)
            _migrate_prompt_file_if_needed(agent_dir, agent_cfg.system_prompt_file, next_prompt_file)

        if body.soul is not None:
            _write_prompt_file(agent_dir, next_prompt_file, body.soul)

        logger.info("Updated agent '%s' (tenant=%s)", name, tenant_id)
        refreshed_cfg = load_agent_config(name, agents_dir=agents_dir)
        return _agent_config_to_response(refreshed_cfg, include_soul=True, agent_dir=agent_dir)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update agent: {str(e)}")


@router.get(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Get User Profile",
    description="Read the global USER.md file that is injected into all custom agents.",
)
async def get_user_profile() -> UserProfileResponse:
    try:
        user_md_path = get_paths().user_md_file
        if not user_md_path.exists():
            return UserProfileResponse(content=None)
        raw = user_md_path.read_text(encoding="utf-8").strip()
        return UserProfileResponse(content=raw or None)
    except Exception as e:
        logger.error(f"Failed to read user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read user profile: {str(e)}")


@router.put(
    "/user-profile",
    response_model=UserProfileResponse,
    summary="Update User Profile",
    description="Write the global USER.md file that is injected into all custom agents.",
)
async def update_user_profile(request: UserProfileUpdateRequest) -> UserProfileResponse:
    try:
        paths = get_paths()
        paths.base_dir.mkdir(parents=True, exist_ok=True)
        paths.user_md_file.write_text(request.content, encoding="utf-8")
        logger.info(f"Updated USER.md at {paths.user_md_file}")
        return UserProfileResponse(content=request.content or None)
    except Exception as e:
        logger.error(f"Failed to update user profile: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update user profile: {str(e)}")


@router.delete(
    "/agents/{name}",
    status_code=204,
    summary="Delete Custom Agent",
    description="Delete a custom agent and all its files (config, prompt file, memory).",
)
async def delete_agent(
    name: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> None:
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    agent_dir = _resolve_agent_dir(tenant_id, name)
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    try:
        shutil.rmtree(agent_dir)
        logger.info(f"Deleted agent '{name}' from {agent_dir} (tenant={tenant_id})")
    except Exception as e:
        logger.error(f"Failed to delete agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete agent: {str(e)}")


# ── Batch sync ──────────────────────────────────────────────────────────


class AgentSyncItem(BaseModel):
    """A single agent definition inside a batch sync request."""

    name: str = Field(..., description="Agent name (must match ^[A-Za-z0-9-]+$, stored as lowercase)")
    description: str = Field(default="", description="Agent description")
    model: str | None = Field(default=None, description="Optional model override")
    engine_type: str | None = Field(default=None, description="Engine type")
    tool_groups: list[str] | None = Field(default=None, description="Tool group whitelist")
    domain: str | None = Field(default=None, description="Domain handled by this agent")
    system_prompt_file: str | None = Field(default=None, description="Prompt file name")
    hitl_keywords: list[str] | None = Field(default=None, description="HITL keywords")
    max_tool_calls: int | None = Field(default=None, description="Maximum tool calls")
    mcp_binding: McpBindingConfig | None = Field(default=None, description="MCP binding")
    available_skills: list[str] | None = Field(default=None, description="Skill allowlist")
    requested_orchestration_mode: RequestedOrchestrationMode | None = Field(default=None, description="Orchestration mode")
    soul: str = Field(default="", description="System prompt content")


class AgentSyncRequest(BaseModel):
    """Batch sync request: push a set of agent definitions at once."""

    agents: list[AgentSyncItem] = Field(..., description="Agent definitions to sync")
    mode: Literal["upsert", "replace"] = Field(
        default="upsert",
        description="upsert: create/update listed agents only. replace: additionally delete agents not in the list.",
    )


class AgentSyncItemResult(BaseModel):
    """Outcome for a single agent inside a sync operation."""

    name: str
    action: Literal["created", "updated", "deleted", "failed"]
    error: str | None = None


class AgentSyncResponse(BaseModel):
    """Summary of a batch sync operation."""

    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    errors: list[AgentSyncItemResult] = Field(default_factory=list)


def _sync_upsert_agent(
    agents_dir: Path,
    name: str,
    item: AgentSyncItem,
    existing_names: set[str],
) -> AgentSyncItemResult:
    """Create or update a single agent on disk. Returns the outcome."""
    agent_dir = agents_dir / name
    try:
        config_data = _build_config_data(
            name=name,
            description=item.description,
            model=item.model,
            engine_type=item.engine_type,
            tool_groups=item.tool_groups,
            domain=item.domain,
            system_prompt_file=item.system_prompt_file,
            hitl_keywords=item.hitl_keywords,
            max_tool_calls=item.max_tool_calls,
            mcp_binding=item.mcp_binding,
            available_skills=item.available_skills,
            requested_orchestration_mode=item.requested_orchestration_mode,
        )
        agent_dir.mkdir(parents=True, exist_ok=True)
        _write_config(agent_dir, config_data)
        _write_prompt_file(agent_dir, item.system_prompt_file, item.soul)

        action: Literal["created", "updated"] = "updated" if name in existing_names else "created"
        return AgentSyncItemResult(name=name, action=action)
    except Exception as e:
        logger.warning("Sync failed for agent '%s': %s", name, e, exc_info=True)
        return AgentSyncItemResult(name=name, action="failed", error=str(e))


@router.post(
    "/agents/sync",
    response_model=AgentSyncResponse,
    summary="Batch Sync Agents",
    description=(
        "Batch-create/update agents in a single call. "
        "In 'upsert' mode, existing agents are updated and new ones created. "
        "In 'replace' mode, agents not in the list are also deleted."
    ),
)
async def sync_agents(
    body: AgentSyncRequest,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> AgentSyncResponse:
    # --- Validate all names up-front so callers get immediate feedback ---
    seen_names: set[str] = set()
    for item in body.agents:
        _validate_agent_name(item.name)
        lower = _normalize_agent_name(item.name)
        if lower in seen_names:
            raise HTTPException(
                status_code=422,
                detail=f"Duplicate agent name '{lower}' in sync request",
            )
        seen_names.add(lower)

    agents_dir = _resolve_agents_dir(tenant_id)
    existing_agents = list_custom_agents(agents_dir=agents_dir)
    existing_names = {a.name.lower() for a in existing_agents}
    incoming_names: set[str] = set()

    created: list[str] = []
    updated: list[str] = []
    deleted: list[str] = []
    errors: list[AgentSyncItemResult] = []

    for item in body.agents:
        name = _normalize_agent_name(item.name)
        incoming_names.add(name)
        result = _sync_upsert_agent(agents_dir, name, item, existing_names)
        if result.action == "created":
            created.append(name)
        elif result.action == "updated":
            updated.append(name)
        else:
            errors.append(result)

    # In replace mode, remove agents not in the incoming list — but ONLY if
    # the upsert phase completed without errors.  If any incoming agent failed
    # to sync, deleting existing agents would cause data loss: the caller
    # intended those existing agents to be *replaced* by the incoming set, but
    # if part of that set failed the replacement is incomplete.
    if body.mode == "replace":
        if errors:
            logger.warning(
                "Skipping replace-mode deletions because %d upsert error(s) occurred (tenant=%s)",
                len(errors), tenant_id,
            )
        else:
            for name in sorted(existing_names - incoming_names):
                agent_dir = agents_dir / name
                try:
                    if agent_dir.exists():
                        shutil.rmtree(agent_dir)
                        deleted.append(name)
                        logger.info("Sync-deleted agent '%s' (tenant=%s)", name, tenant_id)
                except Exception as e:
                    logger.warning("Sync-delete failed for agent '%s': %s", name, e, exc_info=True)
                    errors.append(AgentSyncItemResult(name=name, action="failed", error=str(e)))

    logger.info(
        "Agent sync completed (tenant=%s, mode=%s): created=%d, updated=%d, deleted=%d, errors=%d",
        tenant_id, body.mode, len(created), len(updated), len(deleted), len(errors),
    )
    return AgentSyncResponse(created=created, updated=updated, deleted=deleted, errors=errors)

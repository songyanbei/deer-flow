"""CRUD API for custom agents."""

import logging
import re
import shutil
from pathlib import Path
from typing import Literal
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config.agents_config import AgentConfig, McpServerEntry, list_custom_agents, load_agent_config, load_agent_soul
from src.config.paths import get_paths

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
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    domain: str | None = Field(default=None, description="Domain handled by this agent")
    system_prompt_file: str | None = Field(default=None, description="Prompt file used for the agent system prompt")
    hitl_keywords: list[str] | None = Field(default=None, description="Human-in-the-loop escalation keywords")
    max_tool_calls: int | None = Field(default=None, description="Maximum tool calls allowed for the agent")
    mcp_servers: list[McpServerEntry] | None = Field(default=None, description="Optional per-agent MCP server definitions")
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
    tool_groups: list[str] | None = Field(default=None, description="Optional tool group whitelist")
    domain: str | None = Field(default=None, description="Domain handled by this agent")
    system_prompt_file: str | None = Field(default=None, description="Prompt file used for the agent system prompt")
    hitl_keywords: list[str] | None = Field(default=None, description="Human-in-the-loop escalation keywords")
    max_tool_calls: int | None = Field(default=None, description="Maximum tool calls allowed for the agent")
    mcp_servers: list[McpServerEntry] | None = Field(default=None, description="Optional per-agent MCP server definitions")
    available_skills: list[str] | None = Field(default=None, description="Optional skill allowlist for this agent")
    requested_orchestration_mode: RequestedOrchestrationMode | None = Field(default=None, description="Default orchestration mode for the agent")
    soul: str = Field(default="", description="System prompt content for the agent")


class AgentUpdateRequest(BaseModel):
    """Request body for updating a custom agent."""

    description: str | None = Field(default=None, description="Updated description")
    model: str | None = Field(default=None, description="Updated model override")
    tool_groups: list[str] | None = Field(default=None, description="Updated tool group whitelist")
    domain: str | None = Field(default=None, description="Updated domain")
    system_prompt_file: str | None = Field(default=None, description="Updated prompt file name")
    hitl_keywords: list[str] | None = Field(default=None, description="Updated HITL keywords")
    max_tool_calls: int | None = Field(default=None, description="Updated maximum tool calls")
    mcp_servers: list[McpServerEntry] | None = Field(default=None, description="Updated MCP server definitions")
    available_skills: list[str] | None = Field(default=None, description="Updated skill allowlist")
    requested_orchestration_mode: RequestedOrchestrationMode | None = Field(default=None, description="Updated default orchestration mode")
    soul: str | None = Field(default=None, description="Updated system prompt content")


class UserProfileResponse(BaseModel):
    """Response model for the global user profile (USER.md)."""

    content: str | None = Field(default=None, description="USER.md content, or null if not yet created")


class UserProfileUpdateRequest(BaseModel):
    """Request body for setting the global user profile."""

    content: str = Field(default="", description="USER.md content that describes the user's background and preferences")


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
    tool_groups: list[str] | None,
    domain: str | None,
    system_prompt_file: str | None,
    hitl_keywords: list[str] | None,
    max_tool_calls: int | None,
    mcp_servers: list[McpServerEntry] | None,
    available_skills: list[str] | None,
    requested_orchestration_mode: RequestedOrchestrationMode | None,
) -> dict[str, Any]:
    config_data: dict[str, Any] = {"name": name, "description": description}
    if model is not None:
        config_data["model"] = model
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
    if mcp_servers is not None:
        config_data["mcp_servers"] = [server.model_dump() for server in mcp_servers]
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


def _agent_config_to_response(agent_cfg: AgentConfig, include_soul: bool = False) -> AgentResponse:
    soul: str | None = None
    if include_soul:
        soul = load_agent_soul(agent_cfg.name) or ""

    return AgentResponse(
        name=agent_cfg.name,
        description=agent_cfg.description,
        model=agent_cfg.model,
        tool_groups=agent_cfg.tool_groups,
        domain=agent_cfg.domain,
        system_prompt_file=agent_cfg.system_prompt_file,
        hitl_keywords=agent_cfg.hitl_keywords,
        max_tool_calls=agent_cfg.max_tool_calls,
        mcp_servers=agent_cfg.mcp_servers,
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
async def list_agents() -> AgentsListResponse:
    try:
        agents = list_custom_agents()
        return AgentsListResponse(agents=[_agent_config_to_response(a) for a in agents])
    except Exception as e:
        logger.error(f"Failed to list agents: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list agents: {str(e)}")


@router.get(
    "/agents/check",
    summary="Check Agent Name",
    description="Validate an agent name and check if it is available (case-insensitive).",
)
async def check_agent_name(name: str) -> dict:
    _validate_agent_name(name)
    normalized = _normalize_agent_name(name)
    available = not get_paths().agent_dir(normalized).exists()
    return {"available": available, "name": normalized}


@router.get(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Get Custom Agent",
    description="Retrieve details and prompt content for a specific custom agent.",
)
async def get_agent(name: str) -> AgentResponse:
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    try:
        agent_cfg = load_agent_config(name)
        return _agent_config_to_response(agent_cfg, include_soul=True)
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
async def create_agent_endpoint(request: AgentCreateRequest) -> AgentResponse:
    _validate_agent_name(request.name)
    normalized_name = _normalize_agent_name(request.name)

    agent_dir = get_paths().agent_dir(normalized_name)
    if agent_dir.exists():
        raise HTTPException(status_code=409, detail=f"Agent '{normalized_name}' already exists")

    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        config_data = _build_config_data(
            name=normalized_name,
            description=request.description,
            model=request.model,
            tool_groups=request.tool_groups,
            domain=request.domain,
            system_prompt_file=request.system_prompt_file,
            hitl_keywords=request.hitl_keywords,
            max_tool_calls=request.max_tool_calls,
            mcp_servers=request.mcp_servers,
            available_skills=request.available_skills,
            requested_orchestration_mode=request.requested_orchestration_mode,
        )
        _write_config(agent_dir, config_data)
        _write_prompt_file(agent_dir, request.system_prompt_file, request.soul)

        logger.info("Created agent '%s' at %s", normalized_name, agent_dir)
        agent_cfg = load_agent_config(normalized_name)
        return _agent_config_to_response(agent_cfg, include_soul=True)
    except HTTPException:
        raise
    except Exception as e:
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
        logger.error(f"Failed to create agent '{request.name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {str(e)}")


@router.put(
    "/agents/{name}",
    response_model=AgentResponse,
    summary="Update Custom Agent",
    description="Update an existing custom agent's config and/or prompt file.",
)
async def update_agent(name: str, request: AgentUpdateRequest) -> AgentResponse:
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    try:
        agent_cfg = load_agent_config(name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    agent_dir = get_paths().agent_dir(name)
    requested_mode_provided = "requested_orchestration_mode" in request.model_fields_set

    try:
        next_prompt_file = request.system_prompt_file if request.system_prompt_file is not None else agent_cfg.system_prompt_file
        config_changed = any(
            value is not None
            for value in [
                request.description,
                request.model,
                request.tool_groups,
                request.domain,
                request.system_prompt_file,
                request.hitl_keywords,
                request.max_tool_calls,
                request.mcp_servers,
                request.available_skills,
            ]
        ) or requested_mode_provided

        if config_changed:
            updated = _build_config_data(
                name=agent_cfg.name,
                description=request.description if request.description is not None else agent_cfg.description,
                model=request.model if request.model is not None else agent_cfg.model,
                tool_groups=request.tool_groups if request.tool_groups is not None else agent_cfg.tool_groups,
                domain=request.domain if request.domain is not None else agent_cfg.domain,
                system_prompt_file=next_prompt_file,
                hitl_keywords=request.hitl_keywords if request.hitl_keywords is not None else agent_cfg.hitl_keywords,
                max_tool_calls=request.max_tool_calls if request.max_tool_calls is not None else agent_cfg.max_tool_calls,
                mcp_servers=request.mcp_servers if request.mcp_servers is not None else agent_cfg.mcp_servers,
                available_skills=request.available_skills if request.available_skills is not None else agent_cfg.available_skills,
                requested_orchestration_mode=request.requested_orchestration_mode if requested_mode_provided else agent_cfg.requested_orchestration_mode,
            )
            _write_config(agent_dir, updated)
            _migrate_prompt_file_if_needed(agent_dir, agent_cfg.system_prompt_file, next_prompt_file)

        if request.soul is not None:
            _write_prompt_file(agent_dir, next_prompt_file, request.soul)

        logger.info("Updated agent '%s'", name)
        refreshed_cfg = load_agent_config(name)
        return _agent_config_to_response(refreshed_cfg, include_soul=True)
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
async def delete_agent(name: str) -> None:
    _validate_agent_name(name)
    name = _normalize_agent_name(name)

    agent_dir = get_paths().agent_dir(name)
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    try:
        shutil.rmtree(agent_dir)
        logger.info(f"Deleted agent '{name}' from {agent_dir}")
    except Exception as e:
        logger.error(f"Failed to delete agent '{name}': {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete agent: {str(e)}")

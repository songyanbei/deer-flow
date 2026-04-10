"""Personal resource CRUD API — ``/api/me/*``.

Enables authenticated users to manage their own resources (agents, skills,
MCP config) without admin privileges.  All endpoints require an identified
user (``user_id != "anonymous"``); when OIDC is disabled the guard returns
403 so that callers know personal resources are unavailable.

Resource storage layout (under the tenant-user directory)::

    tenants/{tenant_id}/users/{user_id}/
        agents/{name}/config.yaml + SOUL.md
        skills/{name}/SKILL.md + ...
        extensions_config.json          # user-layer MCP overrides
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.agents.lead_agent.engine_registry import normalize_engine_type
from src.config.agents_config import AgentConfig, McpBindingConfig, load_agent_config
from src.config.extensions_config import ExtensionsConfig
from src.config.paths import get_paths
from src.gateway.dependencies import get_tenant_id, get_user_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/me", tags=["me"])

AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9-]+$")
DEFAULT_PROMPT_FILE = "SOUL.md"
RequestedOrchestrationMode = Literal["auto", "leader", "workflow"]


# ── Dependency guard ────────────────────────────────────────────────────


def _require_identified_user(
    tenant_id: str = Depends(get_tenant_id),
    user_id: str = Depends(get_user_id),
) -> tuple[str, str]:
    """Return ``(tenant_id, user_id)`` or raise 403 for anonymous users.

    Personal resources are only available when OIDC is enabled and an
    actual user identity is present.  In dev mode (OIDC off) both values
    are ``"default"``/``"anonymous"`` — we reject that to avoid writing
    personal data into the shared tenant directory.
    """
    if not tenant_id or tenant_id == "default" or not user_id or user_id == "anonymous":
        raise HTTPException(
            status_code=403,
            detail="Personal resource endpoints require an identified user (enable OIDC)",
        )
    return tenant_id, user_id


# ── Path helpers ────────────────────────────────────────────────────────


def _user_agents_dir(tenant_id: str, user_id: str) -> Path:
    return get_paths().tenant_user_agents_dir(tenant_id, user_id)


def _user_agent_dir(tenant_id: str, user_id: str, name: str) -> Path:
    return get_paths().tenant_user_agent_dir(tenant_id, user_id, name)


def _user_skills_dir(tenant_id: str, user_id: str) -> Path:
    return get_paths().tenant_user_skills_dir(tenant_id, user_id)


def _user_extensions_config_path(tenant_id: str, user_id: str) -> Path:
    return get_paths().tenant_user_extensions_config(tenant_id, user_id)


# ── Validation helpers ──────────────────────────────────────────────────


def _validate_agent_name(name: str) -> str:
    if not AGENT_NAME_PATTERN.match(name):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid agent name '{name}'. Must match ^[A-Za-z0-9-]+$.",
        )
    return name.lower()


def _validate_skill_name(name: str) -> str:
    trimmed = name.strip().lower()
    if not SKILL_NAME_PATTERN.match(trimmed):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid skill name '{name}'. Must be hyphen-case (lowercase letters, digits, hyphens).",
        )
    if trimmed.startswith("-") or trimmed.endswith("-") or "--" in trimmed:
        raise HTTPException(
            status_code=422,
            detail=f"Skill name '{name}' cannot start/end with hyphen or contain consecutive hyphens.",
        )
    return trimmed


def _validate_path_safe(name: str, kind: str = "name") -> None:
    """Reject path-traversal attempts."""
    if ".." in name or "/" in name or "\\" in name:
        raise HTTPException(status_code=400, detail=f"{kind} contains illegal path characters")


# ── Agent Pydantic models (reuse shape from agents.py) ──────────────────


class PersonalAgentResponse(BaseModel):
    name: str
    description: str = ""
    model: str | None = None
    engine_type: str | None = None
    tool_groups: list[str] | None = None
    domain: str | None = None
    system_prompt_file: str | None = None
    hitl_keywords: list[str] | None = None
    max_tool_calls: int | None = None
    mcp_binding: McpBindingConfig | None = None
    available_skills: list[str] | None = None
    requested_orchestration_mode: RequestedOrchestrationMode | None = None
    soul: str | None = None
    source: str = "personal"


class PersonalAgentsListResponse(BaseModel):
    agents: list[PersonalAgentResponse]


class PersonalAgentCreateRequest(BaseModel):
    name: str = Field(..., description="Agent name (^[A-Za-z0-9-]+$, stored lowercase)")
    description: str = ""
    model: str | None = None
    engine_type: str | None = None
    tool_groups: list[str] | None = None
    domain: str | None = None
    system_prompt_file: str | None = None
    hitl_keywords: list[str] | None = None
    max_tool_calls: int | None = None
    mcp_binding: McpBindingConfig | None = None
    available_skills: list[str] | None = None
    requested_orchestration_mode: RequestedOrchestrationMode | None = None
    soul: str = ""


class PersonalAgentUpdateRequest(BaseModel):
    description: str | None = None
    model: str | None = None
    engine_type: str | None = None
    tool_groups: list[str] | None = None
    domain: str | None = None
    system_prompt_file: str | None = None
    hitl_keywords: list[str] | None = None
    max_tool_calls: int | None = None
    mcp_binding: McpBindingConfig | None = None
    available_skills: list[str] | None = None
    requested_orchestration_mode: RequestedOrchestrationMode | None = None
    soul: str | None = None


# ── Agent helpers ───────────────────────────────────────────────────────


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


def _agent_config_to_response(cfg: AgentConfig, agent_dir: Path, include_soul: bool = False) -> PersonalAgentResponse:
    soul: str | None = None
    if include_soul:
        prompt_file = cfg.system_prompt_file or DEFAULT_PROMPT_FILE
        soul_path = agent_dir / prompt_file
        soul = soul_path.read_text(encoding="utf-8").strip() if soul_path.exists() else ""

    return PersonalAgentResponse(
        name=cfg.name,
        description=cfg.description,
        model=cfg.model,
        engine_type=normalize_engine_type(cfg.engine_type),
        tool_groups=cfg.tool_groups,
        domain=cfg.domain,
        system_prompt_file=cfg.system_prompt_file,
        hitl_keywords=cfg.hitl_keywords,
        max_tool_calls=cfg.max_tool_calls,
        mcp_binding=cfg.mcp_binding,
        available_skills=cfg.available_skills,
        requested_orchestration_mode=cfg.requested_orchestration_mode,
        soul=soul,
        source="personal",
    )


# ── Skill Pydantic models ──────────────────────────────────────────────


class PersonalSkillResponse(BaseModel):
    name: str
    description: str = ""
    license: str | None = None
    category: str = "personal"
    enabled: bool = True
    source: str = "personal"


class PersonalSkillsListResponse(BaseModel):
    skills: list[PersonalSkillResponse]


class PersonalSkillUpdateRequest(BaseModel):
    enabled: bool = Field(..., description="Whether to enable or disable the skill")


class PersonalSkillInstallRequest(BaseModel):
    thread_id: str = Field(..., description="Thread ID where the .skill file is located")
    path: str = Field(..., description="Virtual path to the .skill file")


# ── MCP Pydantic models ────────────────────────────────────────────────


class PersonalMcpConfigResponse(BaseModel):
    mcp_servers: dict[str, Any] = Field(default_factory=dict)


class PersonalMcpConfigUpdateRequest(BaseModel):
    mcp_servers: dict[str, Any] = Field(..., description="MCP server configurations")


# ══════════════════════════════════════════════════════════════════════
#  AGENT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


@router.get("/agents", response_model=PersonalAgentsListResponse)
async def list_personal_agents(
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalAgentsListResponse:
    """List all personal agents for the current user."""
    tenant_id, user_id = identity
    agents_dir = _user_agents_dir(tenant_id, user_id)

    if not agents_dir.exists():
        return PersonalAgentsListResponse(agents=[])

    agents: list[PersonalAgentResponse] = []
    for child in sorted(agents_dir.iterdir()):
        if not child.is_dir():
            continue
        config_file = child / "config.yaml"
        if not config_file.exists():
            continue
        try:
            cfg = load_agent_config(child.name, agents_dir=agents_dir)
            agents.append(_agent_config_to_response(cfg, child))
        except Exception:
            logger.warning("Skipping malformed personal agent '%s'", child.name)
    return PersonalAgentsListResponse(agents=agents)


@router.post("/agents", response_model=PersonalAgentResponse, status_code=201)
async def create_personal_agent(
    body: PersonalAgentCreateRequest,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalAgentResponse:
    """Create a new personal agent."""
    tenant_id, user_id = identity
    _validate_path_safe(body.name, "agent name")
    name = _validate_agent_name(body.name)

    agent_dir = _user_agent_dir(tenant_id, user_id, name)
    if agent_dir.exists():
        raise HTTPException(status_code=409, detail=f"Personal agent '{name}' already exists")

    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        config_data = _build_config_data(
            name=name,
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

        logger.info("Created personal agent '%s' (tenant=%s, user=%s)", name, tenant_id, user_id)
        cfg = load_agent_config(name, agents_dir=_user_agents_dir(tenant_id, user_id))
        return _agent_config_to_response(cfg, agent_dir, include_soul=True)
    except HTTPException:
        raise
    except Exception as e:
        if agent_dir.exists():
            shutil.rmtree(agent_dir)
        logger.error("Failed to create personal agent '%s': %s", body.name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create agent: {e}")


@router.get("/agents/{name}", response_model=PersonalAgentResponse)
async def get_personal_agent(
    name: str,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalAgentResponse:
    """Get a specific personal agent by name."""
    tenant_id, user_id = identity
    _validate_path_safe(name, "agent name")
    name = _validate_agent_name(name)

    agents_dir = _user_agents_dir(tenant_id, user_id)
    agent_dir = agents_dir / name
    if not agent_dir.exists() or not (agent_dir / "config.yaml").exists():
        raise HTTPException(status_code=404, detail=f"Personal agent '{name}' not found")

    try:
        cfg = load_agent_config(name, agents_dir=agents_dir)
        return _agent_config_to_response(cfg, agent_dir, include_soul=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Personal agent '{name}' not found")
    except Exception as e:
        logger.error("Failed to get personal agent '%s': %s", name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to get agent: {e}")


@router.put("/agents/{name}", response_model=PersonalAgentResponse)
async def update_personal_agent(
    name: str,
    body: PersonalAgentUpdateRequest,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalAgentResponse:
    """Update an existing personal agent."""
    tenant_id, user_id = identity
    _validate_path_safe(name, "agent name")
    name = _validate_agent_name(name)

    agents_dir = _user_agents_dir(tenant_id, user_id)
    agent_dir = agents_dir / name

    try:
        cfg = load_agent_config(name, agents_dir=agents_dir)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Personal agent '{name}' not found")

    try:
        next_prompt_file = body.system_prompt_file if body.system_prompt_file is not None else cfg.system_prompt_file
        engine_type_provided = "engine_type" in body.model_fields_set
        requested_mode_provided = "requested_orchestration_mode" in body.model_fields_set

        config_data = _build_config_data(
            name=cfg.name,
            description=body.description if body.description is not None else cfg.description,
            model=body.model if body.model is not None else cfg.model,
            engine_type=body.engine_type if engine_type_provided else cfg.engine_type,
            tool_groups=body.tool_groups if body.tool_groups is not None else cfg.tool_groups,
            domain=body.domain if body.domain is not None else cfg.domain,
            system_prompt_file=next_prompt_file,
            hitl_keywords=body.hitl_keywords if body.hitl_keywords is not None else cfg.hitl_keywords,
            max_tool_calls=body.max_tool_calls if body.max_tool_calls is not None else cfg.max_tool_calls,
            mcp_binding=body.mcp_binding if body.mcp_binding is not None else cfg.mcp_binding,
            available_skills=body.available_skills if body.available_skills is not None else cfg.available_skills,
            requested_orchestration_mode=body.requested_orchestration_mode if requested_mode_provided else cfg.requested_orchestration_mode,
        )
        _write_config(agent_dir, config_data)

        if body.soul is not None:
            _write_prompt_file(agent_dir, next_prompt_file, body.soul)

        logger.info("Updated personal agent '%s' (tenant=%s, user=%s)", name, tenant_id, user_id)
        refreshed = load_agent_config(name, agents_dir=agents_dir)
        return _agent_config_to_response(refreshed, agent_dir, include_soul=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to update personal agent '%s': %s", name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to update agent: {e}")


@router.delete("/agents/{name}", status_code=204)
async def delete_personal_agent(
    name: str,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> None:
    """Delete a personal agent."""
    tenant_id, user_id = identity
    _validate_path_safe(name, "agent name")
    name = _validate_agent_name(name)

    agent_dir = _user_agent_dir(tenant_id, user_id, name)
    if not agent_dir.exists():
        raise HTTPException(status_code=404, detail=f"Personal agent '{name}' not found")

    try:
        shutil.rmtree(agent_dir)
        logger.info("Deleted personal agent '%s' (tenant=%s, user=%s)", name, tenant_id, user_id)
    except Exception as e:
        logger.error("Failed to delete personal agent '%s': %s", name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete agent: {e}")


# ══════════════════════════════════════════════════════════════════════
#  SKILL ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


def _load_personal_skills(tenant_id: str, user_id: str) -> list[PersonalSkillResponse]:
    """Scan the user skills directory and return personal skill metadata."""
    skills_dir = _user_skills_dir(tenant_id, user_id)
    if not skills_dir.exists():
        return []

    # Load user-level extensions config for enabled state
    ext_cfg = _user_extensions_config_path(tenant_id, user_id)
    skill_states: dict[str, bool] = {}
    if ext_cfg.exists():
        try:
            data = json.loads(ext_cfg.read_text(encoding="utf-8"))
            for sname, sval in data.get("skills", {}).items():
                if isinstance(sval, dict):
                    skill_states[sname] = sval.get("enabled", True)
        except Exception:
            pass

    results: list[PersonalSkillResponse] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
            # Parse YAML frontmatter
            if content.startswith("---"):
                import re as _re
                match = _re.match(r"^---\n(.*?)\n---", content, _re.DOTALL)
                if match:
                    fm = yaml.safe_load(match.group(1))
                    if isinstance(fm, dict):
                        sname = fm.get("name", child.name)
                        results.append(PersonalSkillResponse(
                            name=sname,
                            description=fm.get("description", ""),
                            license=fm.get("license"),
                            category="personal",
                            enabled=skill_states.get(sname, True),
                            source="personal",
                        ))
                        continue
            # Fallback: directory name
            results.append(PersonalSkillResponse(name=child.name, description="", category="personal", source="personal"))
        except Exception:
            logger.warning("Skipping malformed personal skill '%s'", child.name)
    return results


@router.get("/skills", response_model=PersonalSkillsListResponse)
async def list_personal_skills(
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalSkillsListResponse:
    """List all personal skills for the current user."""
    tenant_id, user_id = identity
    return PersonalSkillsListResponse(skills=_load_personal_skills(tenant_id, user_id))


@router.get("/skills/{skill_name}", response_model=PersonalSkillResponse)
async def get_personal_skill(
    skill_name: str,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalSkillResponse:
    """Get a specific personal skill by name."""
    tenant_id, user_id = identity
    _validate_path_safe(skill_name, "skill name")
    skills = _load_personal_skills(tenant_id, user_id)
    skill = next((s for s in skills if s.name == skill_name), None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Personal skill '{skill_name}' not found")
    return skill


@router.put("/skills/{skill_name}", response_model=PersonalSkillResponse)
async def update_personal_skill(
    skill_name: str,
    body: PersonalSkillUpdateRequest,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalSkillResponse:
    """Enable or disable a personal skill."""
    tenant_id, user_id = identity
    _validate_path_safe(skill_name, "skill name")

    # Verify skill exists
    skills = _load_personal_skills(tenant_id, user_id)
    skill = next((s for s in skills if s.name == skill_name), None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Personal skill '{skill_name}' not found")

    # Update enabled state in user-level extensions_config.json
    cfg_path = _user_extensions_config_path(tenant_id, user_id)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    skills_section = existing.setdefault("skills", {})
    skills_section[skill_name] = {"enabled": body.enabled}

    cfg_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    logger.info("Updated personal skill '%s' enabled=%s (tenant=%s, user=%s)", skill_name, body.enabled, tenant_id, user_id)

    skill.enabled = body.enabled
    return skill


@router.post("/skills/install", response_model=PersonalSkillResponse, status_code=201)
async def install_personal_skill(
    body: PersonalSkillInstallRequest,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalSkillResponse:
    """Install a personal skill from a .skill archive in a thread."""
    import tempfile
    import zipfile

    tenant_id, user_id = identity

    from src.gateway.path_utils import resolve_thread_virtual_path_ctx
    from src.gateway.thread_context import resolve_thread_context

    ctx = resolve_thread_context(body.thread_id, tenant_id, user_id)
    skill_file_path = resolve_thread_virtual_path_ctx(ctx, body.path)

    if not skill_file_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill file not found: {body.path}")
    if not skill_file_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {body.path}")
    if not skill_file_path.suffix == ".skill":
        raise HTTPException(status_code=400, detail="File must have .skill extension")
    if not zipfile.is_zipfile(skill_file_path):
        raise HTTPException(status_code=400, detail="File is not a valid ZIP archive")

    skills_dir = _user_skills_dir(tenant_id, user_id)
    skills_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        with zipfile.ZipFile(skill_file_path, "r") as zf:
            zf.extractall(temp_path)

        extracted = list(temp_path.iterdir())
        if not extracted:
            raise HTTPException(status_code=400, detail="Skill archive is empty")

        skill_src = extracted[0] if len(extracted) == 1 and extracted[0].is_dir() else temp_path
        skill_md = skill_src / "SKILL.md"
        if not skill_md.exists():
            raise HTTPException(status_code=400, detail="Invalid skill: SKILL.md not found")

        content = skill_md.read_text(encoding="utf-8")
        skill_name: str | None = None
        description = ""
        license_val: str | None = None
        if content.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
            if match:
                fm = yaml.safe_load(match.group(1))
                if isinstance(fm, dict):
                    skill_name = fm.get("name")
                    description = fm.get("description", "")
                    license_val = fm.get("license")

        if not skill_name:
            raise HTTPException(status_code=400, detail="Could not determine skill name from SKILL.md")

        _validate_skill_name(skill_name)
        target = skills_dir / skill_name
        if target.exists():
            raise HTTPException(status_code=409, detail=f"Personal skill '{skill_name}' already exists")

        shutil.copytree(skill_src, target)

    logger.info("Installed personal skill '%s' (tenant=%s, user=%s)", skill_name, tenant_id, user_id)
    return PersonalSkillResponse(
        name=skill_name,
        description=description,
        license=license_val,
        category="personal",
        enabled=True,
        source="personal",
    )


@router.delete("/skills/{skill_name}", status_code=204)
async def delete_personal_skill(
    skill_name: str,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> None:
    """Delete (uninstall) a personal skill."""
    tenant_id, user_id = identity
    _validate_path_safe(skill_name, "skill name")

    skill_dir = _user_skills_dir(tenant_id, user_id) / skill_name
    if not skill_dir.exists():
        raise HTTPException(status_code=404, detail=f"Personal skill '{skill_name}' not found")

    try:
        shutil.rmtree(skill_dir)
        logger.info("Deleted personal skill '%s' (tenant=%s, user=%s)", skill_name, tenant_id, user_id)
    except Exception as e:
        logger.error("Failed to delete personal skill '%s': %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete skill: {e}")


# ══════════════════════════════════════════════════════════════════════
#  MCP CONFIG ENDPOINTS
# ══════════════════════════════════════════════════════════════════════


@router.get("/mcp/config", response_model=PersonalMcpConfigResponse)
async def get_personal_mcp_config(
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalMcpConfigResponse:
    """Read the user-layer MCP extensions config (only personal overrides)."""
    tenant_id, user_id = identity
    cfg_path = _user_extensions_config_path(tenant_id, user_id)

    if not cfg_path.exists():
        return PersonalMcpConfigResponse(mcp_servers={})

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return PersonalMcpConfigResponse(mcp_servers=data.get("mcpServers", {}))
    except Exception as e:
        logger.error("Failed to read personal MCP config: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to read MCP config: {e}")


@router.put("/mcp/config", response_model=PersonalMcpConfigResponse)
async def update_personal_mcp_config(
    body: PersonalMcpConfigUpdateRequest,
    identity: tuple[str, str] = Depends(_require_identified_user),
) -> PersonalMcpConfigResponse:
    """Write only the ``mcpServers`` section of the user-layer extensions config.

    Preserves any existing ``skills`` section in the user config file.
    """
    tenant_id, user_id = identity
    cfg_path = _user_extensions_config_path(tenant_id, user_id)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    existing["mcpServers"] = body.mcp_servers
    cfg_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    # Invalidate MCP cache for this user so next tool load picks up changes
    try:
        from src.mcp.cache import reset_mcp_tools_cache
        reset_mcp_tools_cache(tenant_id=tenant_id, user_id=user_id)
    except Exception:
        pass

    logger.info("Updated personal MCP config (tenant=%s, user=%s)", tenant_id, user_id)
    return PersonalMcpConfigResponse(mcp_servers=body.mcp_servers)

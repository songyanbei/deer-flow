"""Configuration and loaders for custom agents."""

import logging
import os
import re
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from src.config.paths import get_paths, resolve_tenant_agents_dir, resolve_tenant_user_agents_dir

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
RUNBOOK_FILENAME = "RUNBOOK.md"
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


def _resolve_env_variables(value: Any) -> Any:
    """Recursively resolve `$ENV_VAR` strings inside agent config values."""
    if isinstance(value, str):
        if value.startswith("$"):
            env_name = value[1:]
            env_value = os.getenv(env_name)
            if env_value is None:
                raise ValueError(f"Environment variable {env_name} not found for agent config value {value}")
            return env_value
        return value

    if isinstance(value, dict):
        return {k: _resolve_env_variables(v) for k, v in value.items()}

    if isinstance(value, list):
        return [_resolve_env_variables(item) for item in value]

    return value


class McpBindingConfig(BaseModel):
    """Declarative MCP binding for a domain agent.

    Instead of embedding full server configs, agents reference server *names*
    registered in ``extensions_config.json`` (or the legacy ``mcp_servers`` list).

    Fields:
        use_global: Whether this agent inherits global-category MCP servers.
        domain: Server names scoped exclusively to this agent's domain.
        shared: Server names shared across multiple agents.
        ephemeral: Server names created on-the-fly per run (reserved).
    """

    use_global: bool = Field(default=False, description="Inherit global MCP servers")
    domain: list[str] = Field(default_factory=list, description="Domain-scoped server names")
    shared: list[str] = Field(default_factory=list, description="Shared server names across agents")
    ephemeral: list[str] = Field(default_factory=list, description="Ephemeral server names (reserved)")


class AgentConfig(BaseModel):
    """Configuration for a custom agent."""

    name: str
    description: str = ""
    model: str | None = None
    engine_type: str | None = None
    tool_groups: list[str] | None = None

    # Multi-agent orchestration fields
    domain: str | None = None                      # Business domain label (e.g. "hr"). Set to be discovered by Router.
    system_prompt_file: str | None = None          # Optional override for the default SOUL.md prompt file.
    persistent_memory_enabled: bool = False        # Stage 2 pilot toggle for per-domain persistent memory.
    persistent_runbook_file: str | None = None     # Optional override for the default RUNBOOK.md file.
    hitl_keywords: list[str] = Field(default_factory=list)  # Keywords triggering Human-in-the-Loop approval (backward-compatible fallback)
    intervention_policies: dict[str, Any] = Field(default_factory=dict)  # Per-tool intervention policies (Phase 1)
    max_tool_calls: int = 20                       # Per-agent safety limit for tool usage inside one task execution.
    mcp_binding: McpBindingConfig | None = None    # Declarative MCP binding (references servers in extensions_config.json)
    available_skills: list[str] | None = None      # Skill names to expose; None = all enabled skills
    requested_orchestration_mode: Literal["auto", "leader", "workflow"] | None = None

    # Runtime-only metadata (not persisted to config.yaml)
    source: Literal["platform", "tenant", "personal"] | None = Field(default=None, exclude=True)

    # Output guardrail settings
    guardrail_structured_completion: bool = True    # Enforce terminal tool calling via nudge retry
    guardrail_max_retries: int = 1                  # Max nudge re-invocations (0 = safe default only, no nudge)
    guardrail_safe_default: str = "complete"        # Fallback when nudge exhausted: "complete" or "fail"

    def get_effective_mcp_binding(self) -> McpBindingConfig:
        """Return the effective MCP binding, or an empty binding if not set."""
        return self.mcp_binding or McpBindingConfig()


def load_agent_config(name: str | None, *, agents_dir: "Path | None" = None) -> AgentConfig | None:
    """Load the custom or default agent's config from its directory.

    Args:
        name: Agent name.
        agents_dir: Optional base directory to resolve the agent from.
                    When provided, uses ``agents_dir/{name}/config.yaml``
                    instead of the global agents directory.  This is used for
                    tenant-scoped agent storage.
    """
    if name is None:
        return None

    if not AGENT_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
    if agents_dir is not None:
        agent_dir = agents_dir / name.lower()
    else:
        agent_dir = get_paths().agent_dir(name)
    config_file = agent_dir / "config.yaml"

    if not agent_dir.exists():
        raise FileNotFoundError(f"Agent directory not found: {agent_dir}")

    if not config_file.exists():
        raise FileNotFoundError(f"Agent config not found: {config_file}")

    try:
        with open(config_file, encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Failed to parse agent config {config_file}: {e}") from e

    data = _resolve_env_variables(data)

    if "name" not in data:
        data["name"] = name

    known_fields = set(AgentConfig.model_fields.keys())
    data = {k: v for k, v in data.items() if k in known_fields}

    return AgentConfig(**data)


def _get_agent_prompt_path(agent_name: str | None, *, agents_dir=None):
    """Resolve the effective system prompt file path for an agent."""
    if agent_name and agents_dir is not None:
        agent_dir = agents_dir / agent_name.lower()
    else:
        agent_dir = get_paths().agent_dir(agent_name) if agent_name else get_paths().base_dir
    agent_cfg = load_agent_config(agent_name, agents_dir=agents_dir) if agent_name else None
    prompt_filename = agent_cfg.system_prompt_file if agent_cfg and agent_cfg.system_prompt_file else SOUL_FILENAME
    return prompt_filename, agent_dir / prompt_filename


def load_agent_soul(agent_name: str | None, *, agents_dir=None) -> str | None:
    """Read the configured prompt file for a custom agent, if it exists."""
    _, soul_path = _get_agent_prompt_path(agent_name, agents_dir=agents_dir)
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    return content or None


def load_agent_runbook(agent_name: str | None, *, agents_dir=None) -> str | None:
    """Read the configured runbook file for a custom agent, if it exists.

    Returns the runbook content when the agent has a ``persistent_runbook_file``
    configured **or** a default ``RUNBOOK.md`` exists in its directory.
    This is independent of ``persistent_memory_enabled`` — the runbook profile
    is a standalone capability that does not require persistent memory.
    """
    if agent_name is None:
        return None

    if agents_dir is not None:
        agent_dir = agents_dir / agent_name.lower()
    else:
        agent_dir = get_paths().agent_dir(agent_name)
    agent_cfg = load_agent_config(agent_name, agents_dir=agents_dir)
    if not agent_cfg:
        return None

    runbook_filename = agent_cfg.persistent_runbook_file or RUNBOOK_FILENAME
    runbook_path = agent_dir / runbook_filename

    # Load the runbook when ANY of these is true:
    #   1. persistent_runbook_file is explicitly configured
    #   2. persistent_memory_enabled is set (backward compat)
    #   3. the default RUNBOOK.md exists on disk
    # This ensures domain_runbook_support works independently of persistent memory.
    has_explicit_config = bool(agent_cfg.persistent_runbook_file)
    has_memory_enabled = bool(agent_cfg.persistent_memory_enabled)
    has_file_on_disk = runbook_path.exists()

    if not (has_explicit_config or has_memory_enabled or has_file_on_disk):
        return None

    if not has_file_on_disk:
        return None

    content = runbook_path.read_text(encoding="utf-8").strip()
    return content or None


def list_custom_agents(*, agents_dir: "Path | None" = None) -> list[AgentConfig]:
    """Scan the agents directory and return all valid custom agents.

    Args:
        agents_dir: Optional override for the base agents directory.
                    When provided, scans this directory instead of the global
                    ``agents/`` directory.  Used for tenant-scoped storage.
    """
    scan_dir = agents_dir or get_paths().agents_dir

    if not scan_dir.exists():
        return []

    agents: list[AgentConfig] = []

    for entry in sorted(scan_dir.iterdir()):
        if not entry.is_dir():
            continue

        config_file = entry / "config.yaml"
        if not config_file.exists():
            logger.debug(f"Skipping {entry.name}: no config.yaml")
            continue

        try:
            agent_cfg = load_agent_config(entry.name, agents_dir=scan_dir)
            agents.append(agent_cfg)
        except Exception as e:
            logger.warning(f"Skipping agent '{entry.name}': {e}")

    return agents


def list_domain_agents(
    *,
    agents_dir: "Path | None" = None,
    allowed_agents: list[str] | None = None,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> list[AgentConfig]:
    """Return all agents that have a ``domain`` field set.

    Args:
        agents_dir: Optional override for the base agents directory (legacy single-layer).
        allowed_agents: When provided, only agents whose *name* (case-insensitive)
            appears in this list are returned.  This implements the runtime
            allowlist contract: the platform declares which agents may participate
            in a given execution, and planner/router honour that constraint.
        tenant_id: When provided (and ``agents_dir`` is not), perform a three-layer
            (platform + tenant + personal) merge via :func:`list_domain_agents_layered`.
        user_id: User scope for the personal layer (only used with ``tenant_id``).
    """
    # Three-layer mode: caller provided tenant_id (and optionally user_id)
    if agents_dir is None and tenant_id is not None:
        return list_domain_agents_layered(
            tenant_id=tenant_id,
            user_id=user_id,
            allowed_agents=allowed_agents,
        )

    candidates = [a for a in list_custom_agents(agents_dir=agents_dir) if a.domain]
    if allowed_agents is not None:
        allowed_set = {name.lower() for name in allowed_agents}
        candidates = [a for a in candidates if a.name.lower() in allowed_set]
    return candidates


def _load_agent_config_quiet(name: str, agents_dir: "Path") -> AgentConfig | None:
    """Load agent config from a specific directory, returning None if not found."""
    agent_dir = agents_dir / name.lower()
    config_file = agent_dir / "config.yaml"
    if not agent_dir.exists() or not config_file.exists():
        return None
    try:
        return load_agent_config(name, agents_dir=agents_dir)
    except Exception:
        logger.debug("Failed to load agent '%s' from %s", name, agents_dir, exc_info=True)
        return None


def load_agent_config_layered(
    name: str,
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> AgentConfig | None:
    """Load agent config with three-layer priority: personal > tenant > platform.

    Looks up the agent in each layer and returns the first match found.
    The returned config has ``source`` set to indicate which layer it came from.
    """
    if name is None:
        return None

    # Layer 1: personal (highest priority)
    user_agents_dir = resolve_tenant_user_agents_dir(tenant_id, user_id)
    if user_agents_dir:
        cfg = _load_agent_config_quiet(name, user_agents_dir)
        if cfg:
            cfg.source = "personal"
            return cfg

    # Layer 2: tenant
    tenant_agents_dir = resolve_tenant_agents_dir(tenant_id)
    if tenant_agents_dir:
        cfg = _load_agent_config_quiet(name, tenant_agents_dir)
        if cfg:
            cfg.source = "tenant"
            return cfg

    # Layer 3: platform (lowest priority)
    cfg = _load_agent_config_quiet(name, get_paths().agents_dir)
    if cfg:
        cfg.source = "platform"
        return cfg

    return None


def list_all_agents(
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
) -> list[AgentConfig]:
    """List agents merged across three layers: platform + tenant + personal.

    Same-name agents are resolved by priority (personal > tenant > platform).
    Each returned config has ``source`` set.
    """
    seen: dict[str, AgentConfig] = {}

    # Layer 3 (lowest): platform
    for cfg in list_custom_agents(agents_dir=get_paths().agents_dir):
        cfg.source = "platform"
        seen[cfg.name.lower()] = cfg

    # Layer 2: tenant (overrides platform)
    tenant_agents_dir = resolve_tenant_agents_dir(tenant_id)
    if tenant_agents_dir:
        for cfg in list_custom_agents(agents_dir=tenant_agents_dir):
            cfg.source = "tenant"
            seen[cfg.name.lower()] = cfg

    # Layer 1 (highest): personal (overrides tenant & platform)
    user_agents_dir = resolve_tenant_user_agents_dir(tenant_id, user_id)
    if user_agents_dir:
        for cfg in list_custom_agents(agents_dir=user_agents_dir):
            cfg.source = "personal"
            seen[cfg.name.lower()] = cfg

    return sorted(seen.values(), key=lambda a: a.name.lower())


def list_domain_agents_layered(
    *,
    tenant_id: str | None = None,
    user_id: str | None = None,
    allowed_agents: list[str] | None = None,
) -> list[AgentConfig]:
    """Return all agents with a ``domain`` field, merged across three layers.

    This is the three-layer equivalent of :func:`list_domain_agents`.
    """
    candidates = [a for a in list_all_agents(tenant_id=tenant_id, user_id=user_id) if a.domain]
    if allowed_agents is not None:
        allowed_set = {name.lower() for name in allowed_agents}
        candidates = [a for a in candidates if a.name.lower() in allowed_set]
    return candidates


def validate_agent_platform_readiness(config: AgentConfig) -> dict:
    """Run onboarding + platform core wiring + active profile admission checks.

    Returns a dict with ``onboarding``, ``platform_core``, and ``profiles`` reports.
    This is a convenience wrapper that combines:

    * :func:`src.config.onboarding.validate_onboarding`
    * :func:`src.config.capability_profiles.validate_platform_core_wiring`
    * :func:`src.config.capability_profiles.validate_all_active_profiles`

    Usage::

        report = validate_agent_platform_readiness(config)
        if not report["ok"]:
            for issue_str in report["all_issues"]:
                logger.warning(issue_str)
    """
    from src.config.capability_profiles import validate_all_active_profiles, validate_platform_core_wiring
    from src.config.onboarding import validate_onboarding

    onboarding = validate_onboarding(config)
    platform_core = validate_platform_core_wiring(config)
    profiles = validate_all_active_profiles(config)

    all_issues: list[str] = [str(i) for i in onboarding.issues]
    all_issues.extend(str(i) for i in platform_core.issues)
    for pr in profiles:
        all_issues.extend(str(i) for i in pr.issues)

    ok = onboarding.ok and platform_core.ok and all(pr.ok for pr in profiles)

    return {
        "ok": ok,
        "agent_name": config.name,
        "onboarding": {
            "ok": onboarding.ok,
            "issues": [str(i) for i in onboarding.issues],
        },
        "platform_core": platform_core.to_dict(),
        "profiles": [pr.to_dict() for pr in profiles],
        "all_issues": all_issues,
    }

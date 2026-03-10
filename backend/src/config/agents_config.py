"""Configuration and loaders for custom agents."""

import logging
import re
from typing import Any
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from src.config.paths import get_paths

logger = logging.getLogger(__name__)

SOUL_FILENAME = "SOUL.md"
AGENT_NAME_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")


class McpServerEntry(BaseModel):
    """Single MCP server config for a domain agent (stdio transport)."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    """Configuration for a custom agent."""

    name: str
    description: str = ""
    model: str | None = None
    tool_groups: list[str] | None = None

    # Multi-agent orchestration fields
    domain: str | None = None                      # Business domain label (e.g. "hr"). Set to be discovered by Router.
    system_prompt_file: str | None = None          # Optional override for the default SOUL.md prompt file.
    hitl_keywords: list[str] = Field(default_factory=list)  # Keywords triggering Human-in-the-Loop approval (Phase 3)
    max_tool_calls: int = 20                       # Per-agent safety limit for tool usage inside one task execution.
    mcp_servers: list[McpServerEntry] = Field(default_factory=list)  # Domain-specific MCP servers (stdio connections)
    available_skills: list[str] | None = None      # Skill names to expose; None = all enabled skills
    requested_orchestration_mode: Literal["auto", "leader", "workflow"] | None = None


def load_agent_config(name: str | None) -> AgentConfig | None:
    """Load the custom or default agent's config from its directory."""
    if name is None:
        return None

    if not AGENT_NAME_PATTERN.match(name):
        raise ValueError(f"Invalid agent name '{name}'. Must match pattern: {AGENT_NAME_PATTERN.pattern}")
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

    if "name" not in data:
        data["name"] = name

    known_fields = set(AgentConfig.model_fields.keys())
    data = {k: v for k, v in data.items() if k in known_fields}

    return AgentConfig(**data)


def _get_agent_prompt_path(agent_name: str | None):
    """Resolve the effective system prompt file path for an agent."""
    agent_dir = get_paths().agent_dir(agent_name) if agent_name else get_paths().base_dir
    agent_cfg = load_agent_config(agent_name) if agent_name else None
    prompt_filename = agent_cfg.system_prompt_file if agent_cfg and agent_cfg.system_prompt_file else SOUL_FILENAME
    return prompt_filename, agent_dir / prompt_filename


def load_agent_soul(agent_name: str | None) -> str | None:
    """Read the configured prompt file for a custom agent, if it exists."""
    _, soul_path = _get_agent_prompt_path(agent_name)
    if not soul_path.exists():
        return None
    content = soul_path.read_text(encoding="utf-8").strip()
    return content or None


def list_custom_agents() -> list[AgentConfig]:
    """Scan the agents directory and return all valid custom agents."""
    agents_dir = get_paths().agents_dir

    if not agents_dir.exists():
        return []

    agents: list[AgentConfig] = []

    for entry in sorted(agents_dir.iterdir()):
        if not entry.is_dir():
            continue

        config_file = entry / "config.yaml"
        if not config_file.exists():
            logger.debug(f"Skipping {entry.name}: no config.yaml")
            continue

        try:
            agent_cfg = load_agent_config(entry.name)
            agents.append(agent_cfg)
        except Exception as e:
            logger.warning(f"Skipping agent '{entry.name}': {e}")

    return agents


def list_domain_agents() -> list[AgentConfig]:
    """Return all agents that have a `domain` field set."""
    return [a for a in list_custom_agents() if a.domain]

"""Subagent registry for managing available subagents."""

import logging
from dataclasses import replace

from src.subagents.builtins import BUILTIN_SUBAGENTS
from src.subagents.config import SubagentConfig

logger = logging.getLogger(__name__)


def _build_custom_subagent_config(name: str) -> SubagentConfig | None:
    """Adapt a custom agent config into a lightweight subagent config."""
    try:
        from src.config.agents_config import load_agent_config, load_agent_soul

        agent_config = load_agent_config(name)
        if agent_config is None:
            return None
    except Exception as exc:
        logger.warning("Failed to load custom agent '%s' for subagent registry: %s", name, exc)
        return None

    soul = load_agent_soul(name)
    system_prompt = (
        f"You are the custom agent '{agent_config.name}'. Complete the delegated task autonomously and stay focused on your domain expertise."
    )
    if soul:
        system_prompt += f"\n\n{soul}"

    return SubagentConfig(
        name=agent_config.name,
        description=agent_config.description or f"Custom agent '{agent_config.name}' loaded from agents directory.",
        system_prompt=system_prompt,
        model=agent_config.model or "inherit",
    )


def _get_registered_subagent_config(name: str) -> SubagentConfig | None:
    """Return the base config before timeout overrides are applied."""
    builtin = BUILTIN_SUBAGENTS.get(name)
    if builtin is not None:
        return builtin

    return _build_custom_subagent_config(name)


def get_subagent_config(name: str) -> SubagentConfig | None:
    """Get a subagent configuration by name, with config.yaml overrides applied.

    Args:
        name: The name of the subagent.

    Returns:
        SubagentConfig if found (with any config.yaml overrides applied), None otherwise.
    """
    config = _get_registered_subagent_config(name)
    if config is None:
        return None

    # Apply timeout override from config.yaml (lazy import to avoid circular deps)
    from src.config.subagents_config import get_subagents_app_config

    app_config = get_subagents_app_config()
    effective_timeout = app_config.get_timeout_for(name)
    if effective_timeout != config.timeout_seconds:
        logger.debug(f"Subagent '{name}': timeout overridden by config.yaml ({config.timeout_seconds}s -> {effective_timeout}s)")
        config = replace(config, timeout_seconds=effective_timeout)

    return config


def list_subagents() -> list[SubagentConfig]:
    """List all available subagent configurations (with config.yaml overrides applied).

    Returns:
        List of all registered SubagentConfig instances.
    """
    return [config for name in get_subagent_names() if (config := get_subagent_config(name)) is not None]


def get_subagent_names() -> list[str]:
    """Get all available subagent names.

    Returns:
        List of subagent names.
    """
    return list(BUILTIN_SUBAGENTS.keys())

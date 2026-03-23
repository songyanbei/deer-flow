"""Base engine builder interface and build-time hook contract.

Each engine builder encapsulates the build-time differences for a specific
engine type: prompt mode, tool filtering, and any extra runtime options.

Build-time hooks provide extension points around the agent construction
lifecycle, allowing downstream consumers (security, governance, verification)
to observe or modify the build process without touching core build logic.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine Builder dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EnginePromptKwargs:
    """Prompt-related parameters produced by an engine builder."""

    engine_mode: str = "default"


@dataclass(frozen=True)
class EngineRuntimeOptions:
    """Extra runtime options produced by an engine builder."""

    filter_read_only_tools: bool = False


# ---------------------------------------------------------------------------
# Build-time Hook contract
# ---------------------------------------------------------------------------


@dataclass
class BuildContext:
    """Mutable context passed through build-time hooks.

    Hooks may read any field. Writable fields are explicitly documented;
    hooks MUST NOT modify fields marked as read-only.

    Read-only fields:
        agent_name, engine_type, model_name, is_domain_agent, is_bootstrap

    Writable fields:
        available_skills  — hooks may add/remove skill names before resolve
        extra_tools       — hooks may add/remove tools before agent creation
        metadata          — hooks may attach audit / observability data
    """

    # Read-only identifiers (set once by make_lead_agent)
    agent_name: str | None = None
    engine_type: str | None = None
    model_name: str | None = None
    is_domain_agent: bool = False
    is_bootstrap: bool = False

    # Writable fields
    available_skills: set[str] | None = None
    extra_tools: list[Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class BuildTimeHooks:
    """Build-time hook interface with default no-op implementations.

    All hooks receive a ``BuildContext`` and may inspect or modify its
    writable fields. The call order is guaranteed:

    1. ``before_agent_build``  — after config resolution, before any build work
    2. ``before_skill_resolve`` — before available_skills are resolved
    3. ``before_mcp_bind``      — before MCP tools are fetched and filtered
    4. ``after_agent_build``    — after the agent object is created

    Default implementations are intentional no-ops so that not wiring any
    hooks preserves the existing behavior exactly.
    """

    def before_agent_build(self, ctx: BuildContext) -> None:
        """Called after config/engine resolution, before build work begins."""

    def after_agent_build(self, ctx: BuildContext) -> None:
        """Called after the agent object has been created."""

    def before_skill_resolve(self, ctx: BuildContext) -> None:
        """Called before available_skills are resolved from agent config."""

    def before_mcp_bind(self, ctx: BuildContext) -> None:
        """Called before MCP tools are fetched for the agent."""


# Module-level default (no-op) hooks instance
_default_hooks = BuildTimeHooks()

# Active hooks — replaced via set_build_time_hooks() for extension
_active_hooks: BuildTimeHooks = _default_hooks


def get_build_time_hooks() -> BuildTimeHooks:
    """Return the currently active build-time hooks."""
    return _active_hooks


def set_build_time_hooks(hooks: BuildTimeHooks | None) -> None:
    """Replace the active build-time hooks. Pass None to reset to no-op defaults."""
    global _active_hooks
    _active_hooks = hooks if hooks is not None else _default_hooks


# ---------------------------------------------------------------------------
# Engine Builder base class
# ---------------------------------------------------------------------------


class BaseEngineBuilder(ABC):
    """Abstract base for engine builders.

    Each concrete builder declares:
    - ``canonical_name``: the official engine type string
    - ``aliases``: alternative input strings that map to this engine
    - ``build_prompt_kwargs``: prompt-layer parameters
    - ``prepare_extra_tools``: post-process MCP / extra tools
    - ``prepare_runtime_options``: runtime flags
    """

    @property
    @abstractmethod
    def canonical_name(self) -> str:
        """The canonical engine type identifier (e.g. 'react')."""

    @property
    def aliases(self) -> list[str]:
        """Alternative names that resolve to this engine (case-insensitive)."""
        return []

    def build_prompt_kwargs(self) -> EnginePromptKwargs:
        """Return prompt-layer parameters for this engine."""
        return EnginePromptKwargs(engine_mode=self.canonical_name)

    def prepare_extra_tools(self, tools: list[BaseTool]) -> list[BaseTool]:
        """Post-process extra tools (e.g. filter write-like tools).

        Default implementation passes tools through unchanged.
        """
        return tools

    def prepare_runtime_options(self) -> EngineRuntimeOptions:
        """Return runtime option flags for this engine."""
        return EngineRuntimeOptions()

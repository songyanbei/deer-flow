"""Platform capability inventory — canonical classification of DeerFlow capabilities.

Every runtime capability is assigned to exactly one of three tiers:

* **Platform Core** — inherited by all agents; platform-managed wiring; users
  never configure internals.
* **Capability Profile** — opt-in advanced capability; requires admission check
  before enablement.
* **Pilot / Experimental** — validated in a single domain only; must *not* be
  generalised to new agents without first graduating to Capability Profile.

This module is the single source of truth consumed by onboarding validators,
admission checkers, and documentation generators.
"""

from __future__ import annotations

import enum
from typing import Any

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

class CapabilityTier(str, enum.Enum):
    """The three mutually-exclusive maturity levels for a platform capability."""

    PLATFORM_CORE = "platform_core"
    CAPABILITY_PROFILE = "capability_profile"
    PILOT_EXPERIMENTAL = "pilot_experimental"


# ---------------------------------------------------------------------------
# Capability descriptor
# ---------------------------------------------------------------------------

class CapabilityDescriptor:
    """Immutable metadata for one platform capability."""

    __slots__ = (
        "key",
        "tier",
        "display_name",
        "description",
        "open_strategy",
        "evidence",
        "notes",
    )

    def __init__(
        self,
        *,
        key: str,
        tier: CapabilityTier,
        display_name: str,
        description: str = "",
        open_strategy: str = "default",
        evidence: str = "",
        notes: str = "",
    ) -> None:
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "tier", tier)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "open_strategy", open_strategy)
        object.__setattr__(self, "evidence", evidence)
        object.__setattr__(self, "notes", notes)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("CapabilityDescriptor is immutable")

    def to_dict(self) -> dict[str, str]:
        result = {}
        for slot in self.__slots__:
            val = getattr(self, slot)
            result[slot] = val.value if isinstance(val, enum.Enum) else val
        return result

    def __repr__(self) -> str:
        return f"CapabilityDescriptor(key={self.key!r}, tier={self.tier.value!r})"


# ---------------------------------------------------------------------------
# Canonical inventory
# ---------------------------------------------------------------------------

_CAPABILITIES: tuple[CapabilityDescriptor, ...] = (
    # ── Platform Core ──────────────────────────────────────────────────
    CapabilityDescriptor(
        key="engine_registry",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Engine Registry",
        description="engine_type + registry/builder; resolved globally for all agents.",
        open_strategy="default",
        evidence="engine_type + registry/builder already global",
        notes="New agents should not redeclare internal builder rules.",
    ),
    CapabilityDescriptor(
        key="workflow_runtime",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Workflow Runtime",
        description="Planner / router / executor / task_pool main pipeline.",
        open_strategy="default",
        evidence="planner / router / executor / task_pool already global",
        notes="Platform backbone.",
    ),
    CapabilityDescriptor(
        key="intervention_protocol",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Intervention / Clarification / Resume Protocol",
        description="Unified thread_state / workflow_resume / gateway resolve protocol.",
        open_strategy="default",
        evidence="thread_state / workflow_resume / gateway resolve unified",
        notes="Must not be exposed as per-agent protocol config.",
    ),
    CapabilityDescriptor(
        key="runtime_hook_harness",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Runtime Hook Harness",
        description="Interrupt / state-commit hooks unified via hook registry + runner.",
        open_strategy="default",
        evidence="interrupt/state-commit hooks unified",
        notes="Platform control plane.",
    ),
    CapabilityDescriptor(
        key="parallel_scheduler",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Parallel Scheduler",
        description="Dependency-aware batch scheduler for workflow runtime.",
        open_strategy="default",
        evidence="scheduler + graph paths applied to workflow runtime",
        notes="Not a single-agent pilot.",
    ),
    CapabilityDescriptor(
        key="governance_core",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Governance Core",
        description="Governance engine / ledger / audit middleware.",
        open_strategy="default",
        evidence="governance engine / ledger / middleware present",
        notes="Default governance path is a platform capability.",
    ),
    CapabilityDescriptor(
        key="observability_base",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Observability Base Path",
        description="Workflow structured observability.",
        open_strategy="default",
        evidence="workflow structured observability present",
        notes="Agents should not handle trace wiring individually.",
    ),
    CapabilityDescriptor(
        key="verifier_runtime",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Verifier Runtime Integration",
        description="Verifier hooks integrated into runtime pipeline.",
        open_strategy="default",
        evidence="verifier attached via runtime hooks",
        notes="Profiles only define domain-specific verifier packs.",
    ),
    CapabilityDescriptor(
        key="output_guardrails",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Output Guardrails",
        description=(
            "Structured-output enforcement with verdict-based evaluation, "
            "nudge retry, and safe-default override. Ensures all domain agents "
            "terminate via explicit tool calls (task_complete / task_fail / request_help)."
        ),
        open_strategy="default",
        evidence="GuardrailVerdict ABC + StructuredCompletionGuardrail + runner in executor",
        notes=(
            "Three AgentConfig fields (guardrail_structured_completion, guardrail_max_retries, "
            "guardrail_safe_default) are platform-internal; agents should not override."
        ),
    ),
    CapabilityDescriptor(
        key="mcp_binding_runtime",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="MCP Binding & Runtime Manager",
        description=(
            "Declarative MCP binding resolution (global / domain / shared / ephemeral) "
            "and scope-aware connection lifecycle with lazy initialization, OAuth support, "
            "and health checking."
        ),
        open_strategy="default",
        evidence="binding_resolver + McpRuntimeManager singleton + scope-based isolation",
        notes=(
            "Agents declare mcp_binding (business-optional); platform resolves to concrete "
            "server connections. Runtime manager handles connect/cache/disconnect lifecycle."
        ),
    ),
    CapabilityDescriptor(
        key="subagent_delegation",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Subagent Delegation System",
        description=(
            "Background task delegation to specialized subagents with dual thread pool "
            "execution, timeout management, concurrency control, and real-time progress streaming."
        ),
        open_strategy="default",
        evidence="SubagentExecutor + registry + SubagentLimitMiddleware + task tool",
        notes=(
            "Enabled via runtime config (subagent_enabled). Agents do not configure delegation "
            "internals; the platform manages thread pools, concurrency limits, and tool filtering."
        ),
    ),
    CapabilityDescriptor(
        key="middleware_chain",
        tier=CapabilityTier.PLATFORM_CORE,
        display_name="Agent Middleware Chain",
        description=(
            "Ordered middleware pipeline for agent lifecycle (pre-agent, pre-model, "
            "tool interception, post-model, post-agent). 14 middlewares with strict "
            "ordering and conditional activation based on agent type and config."
        ),
        open_strategy="default",
        evidence="_build_middlewares() in agent.py composes chain; always-on + conditional middlewares",
        notes=(
            "Middleware composition is fully platform-managed. New agents inherit the correct "
            "middleware set automatically based on is_domain_agent, model capabilities, and config."
        ),
    ),

    # ── Capability Profile ─────────────────────────────────────────────
    CapabilityDescriptor(
        key="persistent_domain_memory",
        tier=CapabilityTier.CAPABILITY_PROFILE,
        display_name="Persistent Domain Memory Runtime Entry",
        description="Per-domain persistent memory with generic toggle and injection entry point.",
        open_strategy="admission_required",
        evidence="Generic toggle and injection entry already exist",
        notes="Entry point is platform-ready; domain-specific onboarding requires admission.",
    ),
    CapabilityDescriptor(
        key="domain_runbook_support",
        tier=CapabilityTier.CAPABILITY_PROFILE,
        display_name="Domain Runbook Support",
        description="Runbook loader and injection into domain agent prompt.",
        open_strategy="admission_required",
        evidence="runbook loader present",
        notes="Must clarify which profiles require a runbook.",
    ),
    CapabilityDescriptor(
        key="domain_verifier_pack",
        tier=CapabilityTier.CAPABILITY_PROFILE,
        display_name="Domain Verifier Pack",
        description="Domain-specific verifier families registered via verifier registry.",
        open_strategy="admission_required",
        evidence="runtime verifier integration present",
        notes="Concrete domain packs opened via admission standards.",
    ),
    CapabilityDescriptor(
        key="governance_strict_mode",
        tier=CapabilityTier.CAPABILITY_PROFILE,
        display_name="Governance Strict Mode",
        description="Per-domain stricter governance policies beyond base path.",
        open_strategy="admission_required",
        evidence="governance engine supports domain-scoped rules",
        notes="Profile, not default for all agents.",
    ),

    # ── Pilot / Experimental ───────────────────────────────────────────
    CapabilityDescriptor(
        key="meeting_persistent_memory_hints",
        tier=CapabilityTier.PILOT_EXPERIMENTAL,
        display_name="Meeting Persistent Memory Hint Extraction",
        description="Domain-specific hint extraction rules for meeting-agent.",
        open_strategy="do_not_generalize",
        evidence="meeting-agent pilot validated",
        notes="Still domain-specific logic; must not be copied directly.",
    ),
    CapabilityDescriptor(
        key="meeting_memory_writeback_boundary",
        tier=CapabilityTier.PILOT_EXPERIMENTAL,
        display_name="Meeting Memory Write-back Boundary",
        description="meeting-agent specific persistence boundary definition.",
        open_strategy="do_not_generalize",
        evidence="meeting pilot validated",
        notes="Requires admission contract abstraction before generalisation.",
    ),
)

# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

_BY_KEY: dict[str, CapabilityDescriptor] = {c.key: c for c in _CAPABILITIES}
_BY_TIER: dict[CapabilityTier, tuple[CapabilityDescriptor, ...]] = {}
for _cap in _CAPABILITIES:
    _BY_TIER.setdefault(_cap.tier, ())
    _BY_TIER[_cap.tier] = (*_BY_TIER[_cap.tier], _cap)


def get_capability(key: str) -> CapabilityDescriptor | None:
    """Return the descriptor for *key*, or ``None`` if not registered."""
    return _BY_KEY.get(key)


def list_capabilities(tier: CapabilityTier | None = None) -> tuple[CapabilityDescriptor, ...]:
    """Return capabilities, optionally filtered to a single tier."""
    if tier is None:
        return _CAPABILITIES
    return _BY_TIER.get(tier, ())


def get_capability_matrix() -> list[dict[str, str]]:
    """Return a JSON-serialisable list suitable for docs / API responses."""
    return [c.to_dict() for c in _CAPABILITIES]

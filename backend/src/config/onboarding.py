"""Agent minimum onboarding contract — validation for new agent configurations.

The onboarding contract enforces that new agents only need to declare
*business-identity* fields. Platform-internal wiring (hooks, scheduler,
intervention protocol, governance ledger, verifier runtime, persistent memory
injection) is provided automatically by the platform and must never appear as
required user input.

Usage::

    from src.config.onboarding import validate_onboarding, OnboardingReport
    report = validate_onboarding(agent_config)
    if not report.ok:
        for issue in report.issues:
            print(issue)
"""

from __future__ import annotations

import dataclasses
import enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config.agents_config import AgentConfig


# ---------------------------------------------------------------------------
# Field classification
# ---------------------------------------------------------------------------

class FieldCategory(str, enum.Enum):
    """How a config field relates to the onboarding contract."""

    REQUIRED = "required"          # Must be provided by the user.
    BUSINESS_OPTIONAL = "optional" # User may provide; has a sensible default.
    PLATFORM_INTERNAL = "internal" # Managed by platform; should not leak to users.


@dataclasses.dataclass(frozen=True, slots=True)
class FieldSpec:
    """Metadata for one ``AgentConfig`` field from an onboarding perspective."""

    name: str
    category: FieldCategory
    description: str


# The canonical onboarding field list — derived from the design doc.
ONBOARDING_FIELDS: tuple[FieldSpec, ...] = (
    # ── Required (business identity) ───────────────────────────────────
    FieldSpec("name", FieldCategory.REQUIRED, "Agent unique identifier"),
    FieldSpec("domain", FieldCategory.REQUIRED, "Business domain label for router discovery"),

    # ── Business optional ──────────────────────────────────────────────
    FieldSpec("description", FieldCategory.BUSINESS_OPTIONAL, "Human-readable description"),
    FieldSpec("system_prompt_file", FieldCategory.BUSINESS_OPTIONAL, "SOUL.md or equivalent system prompt file"),
    FieldSpec("available_skills", FieldCategory.BUSINESS_OPTIONAL, "Skill names to expose"),
    FieldSpec("mcp_binding", FieldCategory.BUSINESS_OPTIONAL, "Declarative MCP server binding"),
    FieldSpec("tool_groups", FieldCategory.BUSINESS_OPTIONAL, "Tool group names"),
    FieldSpec("engine_type", FieldCategory.BUSINESS_OPTIONAL, "Runtime engine selector"),
    FieldSpec("requested_orchestration_mode", FieldCategory.BUSINESS_OPTIONAL, "Orchestration mode hint"),
    FieldSpec("model", FieldCategory.BUSINESS_OPTIONAL, "Model override"),

    # ── Platform internal (never required from users) ──────────────────
    FieldSpec("persistent_memory_enabled", FieldCategory.PLATFORM_INTERNAL, "Managed via capability profile admission"),
    FieldSpec("persistent_runbook_file", FieldCategory.PLATFORM_INTERNAL, "Managed via capability profile admission"),
    FieldSpec("hitl_keywords", FieldCategory.PLATFORM_INTERNAL, "Phase 1 backward-compat; platform managed"),
    FieldSpec("intervention_policies", FieldCategory.PLATFORM_INTERNAL, "Platform governance wiring"),
    FieldSpec("max_tool_calls", FieldCategory.PLATFORM_INTERNAL, "Platform safety default"),
    FieldSpec("guardrail_structured_completion", FieldCategory.PLATFORM_INTERNAL, "Platform guardrail default"),
    FieldSpec("guardrail_max_retries", FieldCategory.PLATFORM_INTERNAL, "Platform guardrail default"),
    FieldSpec("guardrail_safe_default", FieldCategory.PLATFORM_INTERNAL, "Platform guardrail default"),
    FieldSpec("source", FieldCategory.PLATFORM_INTERNAL, "Runtime layer marker (platform/tenant/personal); set by loader"),
)


# ---------------------------------------------------------------------------
# Validation report
# ---------------------------------------------------------------------------

@dataclasses.dataclass(slots=True)
class OnboardingIssue:
    """One onboarding validation finding."""

    field: str
    severity: str   # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.field}: {self.message}"


@dataclasses.dataclass(slots=True)
class OnboardingReport:
    """Aggregated result of onboarding validation."""

    agent_name: str
    issues: list[OnboardingIssue] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[OnboardingIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[OnboardingIssue]:
        return [i for i in self.issues if i.severity == "warning"]


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

_INTERNAL_FIELD_NAMES = frozenset(
    f.name for f in ONBOARDING_FIELDS if f.category == FieldCategory.PLATFORM_INTERNAL
)


def _get_default_value(field_name: str):
    """Return the Pydantic default for an ``AgentConfig`` field."""
    from pydantic_core import PydanticUndefined

    from src.config.agents_config import AgentConfig
    field_info = AgentConfig.model_fields[field_name]
    if field_info.default_factory is not None:
        return field_info.default_factory()
    if field_info.default is not PydanticUndefined:
        return field_info.default
    return None


def validate_onboarding(config: AgentConfig) -> OnboardingReport:
    """Validate an ``AgentConfig`` against the minimum onboarding contract.

    Returns an :class:`OnboardingReport` whose ``.ok`` property tells whether
    the config satisfies the minimum requirements for platform onboarding.
    """
    report = OnboardingReport(agent_name=config.name)

    # 1. Required fields must be present and non-empty.
    if not config.name or not config.name.strip():
        report.issues.append(OnboardingIssue("name", "error", "Agent name is required"))
    if not config.domain or not config.domain.strip():
        report.issues.append(OnboardingIssue("domain", "error", "Business domain is required for platform onboarding"))

    # 2. Warn if ANY platform-internal field carries a non-default value.
    #    This signals that the user is reaching into platform internals,
    #    which should instead be managed via capability profile admission.
    for field_name in _INTERNAL_FIELD_NAMES:
        current = getattr(config, field_name, None)
        default = _get_default_value(field_name)
        if current != default:
            report.issues.append(
                OnboardingIssue(
                    field_name,
                    "warning",
                    f"Platform-internal field '{field_name}' has a non-default value. "
                    f"This field is managed by the platform; "
                    f"use capability_profiles.validate_profile_admission() to verify readiness.",
                )
            )

    return report


def get_onboarding_matrix() -> list[dict[str, str]]:
    """Return a JSON-serialisable onboarding field matrix."""
    return [
        {
            "field": f.name,
            "category": f.category.value,
            "should_user_provide": "yes" if f.category == FieldCategory.REQUIRED else (
                "optional" if f.category == FieldCategory.BUSINESS_OPTIONAL else "no"
            ),
            "description": f.description,
        }
        for f in ONBOARDING_FIELDS
    ]

"""Governance Core shared types — risk levels, decision outcomes, ledger entries."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal, NotRequired, TypedDict

# ---------------------------------------------------------------------------
# Risk Taxonomy
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    """Structured risk grades for governance-controlled actions.

    Severity order: MEDIUM < HIGH < CRITICAL.
    Every governance decision MUST carry one of these levels so that the ledger,
    policy engine, and future operator queue can filter / sort consistently.
    """

    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def severity(self) -> int:
        """Numeric severity for comparison (higher = more severe)."""
        return _SEVERITY_MAP[self]


_SEVERITY_MAP: dict[RiskLevel, int] = {
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def parse_risk_level(value: str | None, *, default: RiskLevel = RiskLevel.MEDIUM) -> RiskLevel:
    """Parse a string into a ``RiskLevel``, falling back to *default*."""
    if value is None:
        return default
    try:
        return RiskLevel(value.lower().strip())
    except ValueError:
        return default


# ---------------------------------------------------------------------------
# Decision Outcomes
# ---------------------------------------------------------------------------

class GovernanceDecision(str, Enum):
    """Finite set of governance decision outcomes.

    All governance entry points MUST return one of these — no ad-hoc booleans.
    """

    ALLOW = "allow"
    REQUIRE_INTERVENTION = "require_intervention"
    DENY = "deny"
    CONTINUE_AFTER_RESOLUTION = "continue_after_resolution"


# ---------------------------------------------------------------------------
# Policy Types
# ---------------------------------------------------------------------------

GovernanceDecisionMode = Literal["allow", "require_intervention", "deny"]


class PolicyRule(TypedDict):
    """One governance policy rule.

    Scope matching: a rule applies when ALL non-None scope fields match the
    action context.  ``None`` / missing fields act as wildcards.
    """

    # Identity
    rule_id: str

    # Scope selectors (all optional — None = wildcard)
    tool: NotRequired[str | None]
    agent: NotRequired[str | None]
    category: NotRequired[str | None]
    source_path: NotRequired[str | None]

    # Governance parameters
    risk_level: str  # must be a valid RiskLevel value
    decision: GovernanceDecisionMode

    # Human-readable overrides (optional)
    reason: NotRequired[str | None]
    title: NotRequired[str | None]
    display_overrides: NotRequired[dict[str, Any] | None]

    # Priority — lower runs first, default 100
    priority: NotRequired[int | None]


# ---------------------------------------------------------------------------
# Governance Ledger Entry
# ---------------------------------------------------------------------------

GovernanceLedgerStatus = Literal[
    "decided",              # immediate allow / deny
    "pending_intervention", # waiting for human
    "resolved",             # intervention resolved (approved)
    "rejected",             # intervention rejected
    "failed",               # downstream failure after decision
    "expired",              # timed out without resolution
]


class GovernanceLedgerEntry(TypedDict):
    """One immutable governance audit record.

    Every governance decision — whether allow, deny, or require_intervention —
    produces exactly one entry.  This is the Stage 5B queue/history truth source.
    """

    governance_id: str
    thread_id: str
    run_id: str
    task_id: str
    source_agent: str
    hook_name: str          # e.g. "before_tool", "before_interrupt_emit"
    source_path: str        # call-site identifier
    risk_level: str         # RiskLevel value
    category: str
    decision: str           # GovernanceDecision value
    status: GovernanceLedgerStatus
    rule_id: NotRequired[str | None]       # which policy matched (if any)
    request_id: NotRequired[str | None]    # intervention request_id (if applicable)
    action_summary: NotRequired[str | None]
    reason: NotRequired[str | None]
    metadata: NotRequired[dict[str, Any] | None]
    tenant_id: NotRequired[str]             # owning tenant ("default" when unset)
    created_at: str
    resolved_at: NotRequired[str | None]
    resolved_by: NotRequired[str | None]   # "operator" | "inline" | "system"

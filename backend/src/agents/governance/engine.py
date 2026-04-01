"""Governance Decision Engine — unified entry point for all governance decisions.

All governance decisions flow through this engine so that:
1. Policy rules are evaluated consistently
2. Every decision is recorded in the ledger
3. No call site assembles its own ad-hoc allow/deny logic

Entry points:
- ``evaluate_before_tool``  — called by intervention middleware before tool execution
- ``record_interrupt_emit`` — called at before_interrupt_emit hook for audit
- ``record_interrupt_resolve`` — called at after_interrupt_resolve hook for audit
- ``record_state_commit_audit`` — called at state-commit hooks for audit
"""

from __future__ import annotations

import logging
from typing import Any

from .ledger import GovernanceLedger, governance_ledger
from .policy import PolicyRegistry, policy_registry
from .types import GovernanceDecision, RiskLevel, parse_risk_level

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Engine result
# ---------------------------------------------------------------------------

class GovernanceEvaluation:
    """Result of a governance engine evaluation."""

    __slots__ = ("decision", "risk_level", "reason", "rule_id", "governance_id", "policy_matched", "title", "display_overrides")

    def __init__(
        self,
        *,
        decision: GovernanceDecision,
        risk_level: RiskLevel,
        reason: str | None = None,
        rule_id: str | None = None,
        governance_id: str | None = None,
        policy_matched: bool = False,
        title: str | None = None,
        display_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.decision = decision
        self.risk_level = risk_level
        self.reason = reason
        self.rule_id = rule_id
        self.governance_id = governance_id
        self.policy_matched = policy_matched
        self.title = title
        self.display_overrides = display_overrides


# ---------------------------------------------------------------------------
# Governance Decision Engine
# ---------------------------------------------------------------------------

class GovernanceEngine:
    """Unified governance decision engine.

    Uses the policy registry for rule-based decisions and the ledger for
    audit persistence.  When no policy matches, returns a ``no_match``
    evaluation so the caller can fall back to existing behavior.
    """

    def __init__(
        self,
        registry: PolicyRegistry | None = None,
        ledger: GovernanceLedger | None = None,
    ) -> None:
        self._registry = registry or policy_registry
        self._ledger = ledger or governance_ledger

    # -- before_tool entry point ----------------------------------------------

    def evaluate_before_tool(
        self,
        *,
        tool_name: str,
        tool_args: dict[str, Any],
        agent_name: str,
        task_id: str,
        run_id: str,
        thread_id: str,
        category: str = "tool_execution",
        request_id: str | None = None,
        tenant_id: str | None = None,
    ) -> GovernanceEvaluation:
        """Evaluate governance policy for a tool call (before execution).

        If a policy rule matches, the decision is recorded in the ledger and
        returned.  If no policy matches, returns a ``GovernanceEvaluation``
        with ``policy_matched=False`` so the caller can fall back to existing
        intervention middleware logic.
        """
        match = self._registry.evaluate(
            tool=tool_name,
            agent=agent_name,
            category=category,
            source_path="before_tool",
        )

        if not match.matched:
            return GovernanceEvaluation(
                decision=GovernanceDecision.ALLOW,
                risk_level=RiskLevel.MEDIUM,
                policy_matched=False,
            )

        # Record terminal decisions (allow / deny) in ledger immediately.
        # require_intervention is NOT recorded here — the audit hook on
        # BEFORE_INTERRUPT_EMIT will create the ledger entry when the
        # interrupt is actually emitted, avoiding duplicate entries.
        governance_id: str | None = None
        if match.decision != GovernanceDecision.REQUIRE_INTERVENTION:
            entry = self._ledger.record(
                thread_id=thread_id,
                run_id=run_id,
                task_id=task_id,
                source_agent=agent_name,
                hook_name="before_tool",
                source_path="middleware.intervention",
                risk_level=match.risk_level,
                category=category,
                decision=match.decision,
                request_id=request_id,
                rule_id=match.rule.get("rule_id") if match.rule else None,
                action_summary=f"Tool call: {tool_name}",
                reason=match.reason,
                metadata={"tool_name": tool_name, "tool_args_keys": list(tool_args.keys())},
                tenant_id=tenant_id,
            )
            governance_id = entry["governance_id"]

        return GovernanceEvaluation(
            decision=match.decision,
            risk_level=match.risk_level,
            reason=match.reason,
            rule_id=match.rule.get("rule_id") if match.rule else None,
            governance_id=governance_id,
            policy_matched=True,
            title=match.title,
            display_overrides=match.display_overrides,
        )

    # -- Audit entry points (no policy evaluation, just ledger recording) -----

    def record_interrupt_emit(
        self,
        *,
        thread_id: str,
        run_id: str,
        task_id: str,
        source_agent: str,
        interrupt_type: str,
        source_path: str,
        risk_level: str | RiskLevel = RiskLevel.MEDIUM,
        category: str = "interrupt_emit",
        request_id: str | None = None,
        action_summary: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Record an interrupt emission in the governance ledger.

        Returns the ``governance_id`` of the created entry.
        """
        risk = risk_level if isinstance(risk_level, RiskLevel) else parse_risk_level(str(risk_level))

        entry = self._ledger.record(
            thread_id=thread_id,
            run_id=run_id,
            task_id=task_id,
            source_agent=source_agent,
            hook_name="before_interrupt_emit",
            source_path=source_path,
            risk_level=risk,
            category=category,
            decision=GovernanceDecision.REQUIRE_INTERVENTION,
            request_id=request_id,
            action_summary=action_summary or f"Interrupt: {interrupt_type}",
            reason=reason,
            metadata={**(metadata or {}), "interrupt_type": interrupt_type},
            tenant_id=tenant_id,
        )
        return entry["governance_id"]

    def record_interrupt_resolve(
        self,
        *,
        thread_id: str,
        run_id: str,
        task_id: str,
        source_agent: str,
        source_path: str,
        request_id: str | None = None,
        action_key: str = "",
        resolution_behavior: str = "",
        resolved_by: str = "inline",
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> str | None:
        """Record an interrupt resolution in the governance ledger.

        If a pending_intervention entry exists for *request_id*, transitions
        it to resolved/rejected.  Otherwise creates a new audit entry.
        Returns the ``governance_id``.
        """
        # Try to resolve existing pending entry
        if request_id:
            status = "rejected" if action_key == "reject" else "resolved"
            existing = self._ledger.resolve(
                request_id=request_id,
                status=status,
                resolved_by=resolved_by,
            )
            if existing:
                return existing["governance_id"]

        # No existing entry — record a new one
        entry = self._ledger.record(
            thread_id=thread_id,
            run_id=run_id,
            task_id=task_id,
            source_agent=source_agent,
            hook_name="after_interrupt_resolve",
            source_path=source_path,
            risk_level=RiskLevel.MEDIUM,
            category="interrupt_resolve",
            decision=GovernanceDecision.CONTINUE_AFTER_RESOLUTION,
            request_id=request_id,
            action_summary=f"Resolved: action_key={action_key}",
            metadata={
                **(metadata or {}),
                "action_key": action_key,
                "resolution_behavior": resolution_behavior,
            },
            tenant_id=tenant_id,
        )
        return entry["governance_id"]

    def record_state_commit_audit(
        self,
        *,
        thread_id: str,
        run_id: str,
        source_path: str,
        commit_type: str,
        category: str = "state_commit",
        metadata: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> str:
        """Record a state-commit governance audit entry.

        Lightweight audit record for task_pool / verified_facts commits.
        Returns the ``governance_id``.
        """
        entry = self._ledger.record(
            thread_id=thread_id,
            run_id=run_id,
            task_id="",
            source_agent="system",
            hook_name=f"before_{commit_type}_commit",
            source_path=source_path,
            risk_level=RiskLevel.MEDIUM,
            category=category,
            decision=GovernanceDecision.ALLOW,
            action_summary=f"State commit: {commit_type}",
            metadata=metadata,
            tenant_id=tenant_id,
        )
        return entry["governance_id"]


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

governance_engine = GovernanceEngine()

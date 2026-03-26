"""Policy Schema and Registry — structured governance rules with scope matching.

The registry loads policy rules and evaluates them against action contexts.
When the registry is empty, the governance engine falls back to the existing
intervention middleware behavior (backward-compatible).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .types import GovernanceDecision, GovernanceDecisionMode, PolicyRule, RiskLevel, parse_risk_level

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy match result
# ---------------------------------------------------------------------------

class PolicyMatchResult:
    """Result of evaluating an action against the policy registry."""

    __slots__ = ("matched", "rule", "decision", "risk_level", "reason", "title", "display_overrides")

    def __init__(
        self,
        *,
        matched: bool = False,
        rule: PolicyRule | None = None,
        decision: GovernanceDecision = GovernanceDecision.ALLOW,
        risk_level: RiskLevel = RiskLevel.MEDIUM,
        reason: str | None = None,
        title: str | None = None,
        display_overrides: dict[str, Any] | None = None,
    ) -> None:
        self.matched = matched
        self.rule = rule
        self.decision = decision
        self.risk_level = risk_level
        self.reason = reason
        self.title = title
        self.display_overrides = display_overrides

    @classmethod
    def no_match(cls) -> PolicyMatchResult:
        """No policy matched — caller should fall back to default behavior."""
        return cls(matched=False)


# ---------------------------------------------------------------------------
# Scope matching
# ---------------------------------------------------------------------------

def _scope_matches(rule: PolicyRule, *, tool: str | None, agent: str | None, category: str | None, source_path: str | None) -> bool:
    """Return True if *rule* scope selectors match the given action context.

    A ``None`` / missing scope field in the rule acts as a wildcard.
    All non-None rule fields must match the corresponding context value.
    """
    rule_tool = rule.get("tool")
    if rule_tool is not None and rule_tool != tool:
        return False

    rule_agent = rule.get("agent")
    if rule_agent is not None:
        if agent is None:
            return False
        if rule_agent.lower() != agent.lower():
            return False

    rule_category = rule.get("category")
    if rule_category is not None and rule_category != category:
        return False

    rule_source = rule.get("source_path")
    if rule_source is not None and rule_source != source_path:
        return False

    return True


def _decision_mode_to_governance(mode: GovernanceDecisionMode) -> GovernanceDecision:
    """Convert a policy decision mode string to GovernanceDecision enum."""
    return GovernanceDecision(mode)


# ---------------------------------------------------------------------------
# Policy Registry
# ---------------------------------------------------------------------------

class PolicyRegistry:
    """Thread-safe registry of governance policy rules.

    Rules are sorted by priority (lower first).  The first matching rule wins.
    When the registry is empty, ``evaluate()`` returns ``PolicyMatchResult.no_match()``.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: list[PolicyRule] = []

    # -- Mutation -------------------------------------------------------------

    def load(self, rules: list[PolicyRule]) -> None:
        """Replace all rules with *rules*, sorted by priority."""
        with self._lock:
            self._rules = sorted(rules, key=lambda r: r.get("priority") or 100)
        logger.info("[PolicyRegistry] Loaded %d governance rules", len(rules))

    def add_rule(self, rule: PolicyRule) -> None:
        """Add a single rule and re-sort."""
        with self._lock:
            self._rules.append(rule)
            self._rules.sort(key=lambda r: r.get("priority") or 100)
        logger.debug("[PolicyRegistry] Added rule '%s'", rule.get("rule_id"))

    def clear(self) -> None:
        """Remove all rules (testing / hot-reload)."""
        with self._lock:
            self._rules.clear()
        logger.debug("[PolicyRegistry] Cleared all rules")

    # -- Query ----------------------------------------------------------------

    def evaluate(
        self,
        *,
        tool: str | None = None,
        agent: str | None = None,
        category: str | None = None,
        source_path: str | None = None,
    ) -> PolicyMatchResult:
        """Find the first matching rule and return its governance decision.

        Returns ``PolicyMatchResult.no_match()`` when no rule matches.
        Short-circuits immediately when the registry is empty (zero overhead).
        """
        with self._lock:
            if not self._rules:
                return PolicyMatchResult.no_match()
            rules_snapshot = list(self._rules)

        for rule in rules_snapshot:
            if _scope_matches(rule, tool=tool, agent=agent, category=category, source_path=source_path):
                risk = parse_risk_level(rule.get("risk_level"))
                decision = _decision_mode_to_governance(rule["decision"])
                logger.info(
                    "[PolicyRegistry] Rule '%s' matched: tool=%s agent=%s → decision=%s risk=%s",
                    rule.get("rule_id"), tool, agent, decision.value, risk.value,
                )
                return PolicyMatchResult(
                    matched=True,
                    rule=rule,
                    decision=decision,
                    risk_level=risk,
                    reason=rule.get("reason"),
                    title=rule.get("title"),
                    display_overrides=rule.get("display_overrides"),
                )

        return PolicyMatchResult.no_match()

    @property
    def is_empty(self) -> bool:
        with self._lock:
            return len(self._rules) == 0

    @property
    def rule_count(self) -> int:
        with self._lock:
            return len(self._rules)

    def list_rules(self) -> list[PolicyRule]:
        """Return a snapshot of all rules (for introspection)."""
        with self._lock:
            return list(self._rules)

    def __repr__(self) -> str:
        return f"<PolicyRegistry rules={self.rule_count}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

policy_registry = PolicyRegistry()

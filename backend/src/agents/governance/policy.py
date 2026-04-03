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

_DEFAULT_TENANT = "default"


class PolicyRegistry:
    """Thread-safe registry of governance policy rules, bucketed by tenant.

    Rules are sorted by priority (lower first).  The first matching rule wins.
    When the tenant bucket is empty, ``evaluate()`` returns ``PolicyMatchResult.no_match()``.

    Callers that do not pass ``tenant_id`` operate on the ``"default"`` bucket
    (backward-compatible with single-tenant mode).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tenant_rules: dict[str, list[PolicyRule]] = {}

    def _bucket(self, tenant_id: str | None) -> str:
        return tenant_id if tenant_id and tenant_id != _DEFAULT_TENANT else _DEFAULT_TENANT

    # -- Mutation -------------------------------------------------------------

    def load(self, rules: list[PolicyRule], tenant_id: str | None = None) -> None:
        """Replace all rules for *tenant_id* with *rules*, sorted by priority."""
        bid = self._bucket(tenant_id)
        with self._lock:
            self._tenant_rules[bid] = sorted(rules, key=lambda r: r.get("priority") or 100)
        logger.info("[PolicyRegistry] Loaded %d governance rules for tenant=%s", len(rules), bid)

    def add_rule(self, rule: PolicyRule, tenant_id: str | None = None) -> None:
        """Add a single rule to the tenant bucket and re-sort."""
        bid = self._bucket(tenant_id)
        with self._lock:
            bucket = self._tenant_rules.setdefault(bid, [])
            bucket.append(rule)
            bucket.sort(key=lambda r: r.get("priority") or 100)
        logger.debug("[PolicyRegistry] Added rule '%s' to tenant=%s", rule.get("rule_id"), bid)

    def clear(self, tenant_id: str | None = None) -> None:
        """Remove all rules for a tenant (or all tenants if None)."""
        with self._lock:
            if tenant_id is None:
                self._tenant_rules.clear()
                logger.debug("[PolicyRegistry] Cleared all rules (all tenants)")
            else:
                bid = self._bucket(tenant_id)
                self._tenant_rules.pop(bid, None)
                logger.debug("[PolicyRegistry] Cleared rules for tenant=%s", bid)

    # -- Query ----------------------------------------------------------------

    def evaluate(
        self,
        *,
        tool: str | None = None,
        agent: str | None = None,
        category: str | None = None,
        source_path: str | None = None,
        tenant_id: str | None = None,
    ) -> PolicyMatchResult:
        """Find the first matching rule and return its governance decision.

        Evaluates the tenant-specific bucket first, then falls back to the
        default bucket.  Returns ``PolicyMatchResult.no_match()`` when no
        rule matches in either bucket.
        """
        bid = self._bucket(tenant_id)

        with self._lock:
            # Tenant-specific rules first, then default fallback
            rules_snapshot: list[PolicyRule] = []
            if bid != _DEFAULT_TENANT:
                rules_snapshot.extend(self._tenant_rules.get(bid, []))
            rules_snapshot.extend(self._tenant_rules.get(_DEFAULT_TENANT, []))

            if not rules_snapshot:
                return PolicyMatchResult.no_match()

        for rule in rules_snapshot:
            if _scope_matches(rule, tool=tool, agent=agent, category=category, source_path=source_path):
                risk = parse_risk_level(rule.get("risk_level"))
                decision = _decision_mode_to_governance(rule["decision"])
                logger.info(
                    "[PolicyRegistry] Rule '%s' matched (tenant=%s): tool=%s agent=%s → decision=%s risk=%s",
                    rule.get("rule_id"), bid, tool, agent, decision.value, risk.value,
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
            return all(len(v) == 0 for v in self._tenant_rules.values())

    @property
    def rule_count(self) -> int:
        with self._lock:
            return sum(len(v) for v in self._tenant_rules.values())

    def list_rules(self, tenant_id: str | None = None) -> list[PolicyRule]:
        """Return a snapshot of rules for a tenant (or all if None)."""
        with self._lock:
            if tenant_id is None:
                result: list[PolicyRule] = []
                for v in self._tenant_rules.values():
                    result.extend(v)
                return result
            bid = self._bucket(tenant_id)
            return list(self._tenant_rules.get(bid, []))

    def __repr__(self) -> str:
        return f"<PolicyRegistry rules={self.rule_count} tenants={len(self._tenant_rules)}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

policy_registry = PolicyRegistry()

"""Tests for governance policy schema and registry."""


from src.agents.governance.policy import PolicyMatchResult, PolicyRegistry
from src.agents.governance.types import GovernanceDecision, PolicyRule, RiskLevel


def _make_rule(
    rule_id: str = "test_rule",
    tool: str | None = None,
    agent: str | None = None,
    category: str | None = None,
    source_path: str | None = None,
    risk_level: str = "high",
    decision: str = "require_intervention",
    reason: str | None = None,
    title: str | None = None,
    priority: int | None = None,
) -> PolicyRule:
    rule: PolicyRule = {
        "rule_id": rule_id,
        "risk_level": risk_level,
        "decision": decision,
    }
    if tool is not None:
        rule["tool"] = tool
    if agent is not None:
        rule["agent"] = agent
    if category is not None:
        rule["category"] = category
    if source_path is not None:
        rule["source_path"] = source_path
    if reason is not None:
        rule["reason"] = reason
    if title is not None:
        rule["title"] = title
    if priority is not None:
        rule["priority"] = priority
    return rule


class TestPolicyMatchResult:
    def test_no_match(self):
        result = PolicyMatchResult.no_match()
        assert result.matched is False
        assert result.rule is None

    def test_matched(self):
        result = PolicyMatchResult(matched=True, decision=GovernanceDecision.DENY, risk_level=RiskLevel.CRITICAL)
        assert result.matched is True
        assert result.decision == GovernanceDecision.DENY
        assert result.risk_level == RiskLevel.CRITICAL


class TestPolicyRegistry:
    def setup_method(self):
        self.registry = PolicyRegistry()

    def test_empty_registry_no_match(self):
        result = self.registry.evaluate(tool="create_event")
        assert result.matched is False
        assert self.registry.is_empty is True

    def test_load_and_match_by_tool(self):
        self.registry.load([
            _make_rule(rule_id="r1", tool="create_event", risk_level="high", decision="require_intervention"),
        ])
        result = self.registry.evaluate(tool="create_event")
        assert result.matched is True
        assert result.decision == GovernanceDecision.REQUIRE_INTERVENTION
        assert result.risk_level == RiskLevel.HIGH

    def test_no_match_different_tool(self):
        self.registry.load([
            _make_rule(rule_id="r1", tool="create_event"),
        ])
        result = self.registry.evaluate(tool="list_events")
        assert result.matched is False

    def test_match_by_agent(self):
        self.registry.load([
            _make_rule(rule_id="r1", agent="meeting-agent", decision="deny", risk_level="critical"),
        ])
        result = self.registry.evaluate(agent="meeting-agent")
        assert result.matched is True
        assert result.decision == GovernanceDecision.DENY

    def test_agent_match_case_insensitive(self):
        self.registry.load([
            _make_rule(rule_id="r1", agent="Meeting-Agent", decision="deny"),
        ])
        result = self.registry.evaluate(agent="meeting-agent")
        assert result.matched is True

    def test_match_by_category(self):
        self.registry.load([
            _make_rule(rule_id="r1", category="tool_execution", decision="allow"),
        ])
        result = self.registry.evaluate(category="tool_execution")
        assert result.matched is True
        assert result.decision == GovernanceDecision.ALLOW

    def test_multi_scope_match(self):
        self.registry.load([
            _make_rule(rule_id="r1", tool="cancel_meeting", agent="meeting-agent", decision="require_intervention", risk_level="critical"),
        ])
        # Both must match
        result = self.registry.evaluate(tool="cancel_meeting", agent="meeting-agent")
        assert result.matched is True
        assert result.risk_level == RiskLevel.CRITICAL

        # Only tool matches — agent doesn't
        result = self.registry.evaluate(tool="cancel_meeting", agent="other-agent")
        assert result.matched is False

    def test_wildcard_scope(self):
        # Rule with only tool set — agent/category are wildcards
        self.registry.load([
            _make_rule(rule_id="r1", tool="delete_file", decision="deny"),
        ])
        result = self.registry.evaluate(tool="delete_file", agent="any-agent", category="any-cat")
        assert result.matched is True

    def test_priority_ordering(self):
        self.registry.load([
            _make_rule(rule_id="r_low", tool="create_event", decision="deny", priority=200),
            _make_rule(rule_id="r_high", tool="create_event", decision="allow", priority=10),
        ])
        result = self.registry.evaluate(tool="create_event")
        assert result.matched is True
        assert result.decision == GovernanceDecision.ALLOW
        assert result.rule["rule_id"] == "r_high"

    def test_add_rule(self):
        self.registry.add_rule(_make_rule(rule_id="r1", tool="test_tool", decision="deny"))
        assert self.registry.rule_count == 1
        result = self.registry.evaluate(tool="test_tool")
        assert result.matched is True

    def test_clear(self):
        self.registry.load([_make_rule(rule_id="r1")])
        self.registry.clear()
        assert self.registry.is_empty is True

    def test_list_rules(self):
        rules = [
            _make_rule(rule_id="r1", tool="a"),
            _make_rule(rule_id="r2", tool="b"),
        ]
        self.registry.load(rules)
        listed = self.registry.list_rules()
        assert len(listed) == 2
        assert {r["rule_id"] for r in listed} == {"r1", "r2"}

    def test_reason_and_title_returned(self):
        self.registry.load([
            _make_rule(rule_id="r1", tool="risky", reason="Too dangerous", title="Blocked!"),
        ])
        result = self.registry.evaluate(tool="risky")
        assert result.reason == "Too dangerous"
        assert result.title == "Blocked!"

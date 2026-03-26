"""Tests for governance types — risk taxonomy, decision outcomes, ledger entry types."""


from src.agents.governance.types import (
    GovernanceDecision,
    RiskLevel,
    parse_risk_level,
)


class TestRiskLevel:
    def test_values(self):
        assert RiskLevel.MEDIUM.value == "medium"
        assert RiskLevel.HIGH.value == "high"
        assert RiskLevel.CRITICAL.value == "critical"

    def test_severity_ordering(self):
        assert RiskLevel.MEDIUM.severity < RiskLevel.HIGH.severity
        assert RiskLevel.HIGH.severity < RiskLevel.CRITICAL.severity

    def test_severity_numeric(self):
        assert RiskLevel.MEDIUM.severity == 1
        assert RiskLevel.HIGH.severity == 2
        assert RiskLevel.CRITICAL.severity == 3

    def test_is_str_enum(self):
        assert isinstance(RiskLevel.MEDIUM, str)
        assert RiskLevel.MEDIUM == "medium"


class TestParseRiskLevel:
    def test_valid_values(self):
        assert parse_risk_level("medium") == RiskLevel.MEDIUM
        assert parse_risk_level("high") == RiskLevel.HIGH
        assert parse_risk_level("critical") == RiskLevel.CRITICAL

    def test_case_insensitive(self):
        assert parse_risk_level("HIGH") == RiskLevel.HIGH
        assert parse_risk_level("Critical") == RiskLevel.CRITICAL

    def test_whitespace_stripped(self):
        assert parse_risk_level("  high  ") == RiskLevel.HIGH

    def test_none_returns_default(self):
        assert parse_risk_level(None) == RiskLevel.MEDIUM
        assert parse_risk_level(None, default=RiskLevel.HIGH) == RiskLevel.HIGH

    def test_invalid_returns_default(self):
        assert parse_risk_level("unknown") == RiskLevel.MEDIUM
        assert parse_risk_level("low") == RiskLevel.MEDIUM
        assert parse_risk_level("") == RiskLevel.MEDIUM


class TestGovernanceDecision:
    def test_values(self):
        assert GovernanceDecision.ALLOW.value == "allow"
        assert GovernanceDecision.REQUIRE_INTERVENTION.value == "require_intervention"
        assert GovernanceDecision.DENY.value == "deny"
        assert GovernanceDecision.CONTINUE_AFTER_RESOLUTION.value == "continue_after_resolution"

    def test_is_str_enum(self):
        assert isinstance(GovernanceDecision.ALLOW, str)
        assert GovernanceDecision.ALLOW == "allow"

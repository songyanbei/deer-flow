"""Tests for the agent minimum onboarding contract."""

from __future__ import annotations

from src.config.agents_config import AgentConfig
from src.config.onboarding import (
    ONBOARDING_FIELDS,
    FieldCategory,
    get_onboarding_matrix,
    validate_onboarding,
)

# ---------------------------------------------------------------------------
# Field classification completeness
# ---------------------------------------------------------------------------

def test_onboarding_fields_cover_all_agent_config_fields():
    """Every AgentConfig field should appear in the onboarding field list."""
    config_fields = set(AgentConfig.model_fields.keys())
    onboarding_fields = {f.name for f in ONBOARDING_FIELDS}
    uncovered = config_fields - onboarding_fields
    assert not uncovered, f"AgentConfig fields not covered by onboarding contract: {uncovered}"


def test_required_fields_are_name_and_domain():
    required = {f.name for f in ONBOARDING_FIELDS if f.category == FieldCategory.REQUIRED}
    assert required == {"name", "domain"}


def test_platform_internal_fields_include_hook_wiring():
    internal = {f.name for f in ONBOARDING_FIELDS if f.category == FieldCategory.PLATFORM_INTERNAL}
    assert "persistent_memory_enabled" in internal
    assert "hitl_keywords" in internal
    assert "intervention_policies" in internal
    assert "max_tool_calls" in internal


# ---------------------------------------------------------------------------
# Validation — happy path
# ---------------------------------------------------------------------------

def test_valid_domain_agent_passes():
    config = AgentConfig(name="test-agent", domain="test")
    report = validate_onboarding(config)
    assert report.ok
    assert len(report.errors) == 0


def test_valid_minimal_domain_agent():
    config = AgentConfig(name="simple-agent", domain="simple")
    report = validate_onboarding(config)
    assert report.ok


# ---------------------------------------------------------------------------
# Validation — missing required fields
# ---------------------------------------------------------------------------

def test_missing_name_is_error():
    config = AgentConfig(name="", domain="test")
    report = validate_onboarding(config)
    assert not report.ok
    assert any(i.field == "name" for i in report.errors)


def test_missing_domain_is_error():
    config = AgentConfig(name="orphan-agent")
    report = validate_onboarding(config)
    assert not report.ok
    assert any(i.field == "domain" for i in report.errors)


# ---------------------------------------------------------------------------
# Validation — warnings for internal fields
# ---------------------------------------------------------------------------

def test_persistent_memory_without_admission_warns():
    config = AgentConfig(name="warned-agent", domain="test", persistent_memory_enabled=True)
    report = validate_onboarding(config)
    # Not an error, but should warn
    assert report.ok  # warnings don't block onboarding
    assert any(i.field == "persistent_memory_enabled" for i in report.warnings)


def test_intervention_policies_non_default_warns():
    config = AgentConfig(name="policy-agent", domain="test", intervention_policies={"tool_x": "require_approval"})
    report = validate_onboarding(config)
    assert report.ok
    assert any(i.field == "intervention_policies" for i in report.warnings)


def test_max_tool_calls_non_default_warns():
    config = AgentConfig(name="limit-agent", domain="test", max_tool_calls=50)
    report = validate_onboarding(config)
    assert report.ok
    assert any(i.field == "max_tool_calls" for i in report.warnings)


def test_guardrail_fields_non_default_warns():
    config = AgentConfig(name="guard-agent", domain="test", guardrail_max_retries=5)
    report = validate_onboarding(config)
    assert report.ok
    assert any(i.field == "guardrail_max_retries" for i in report.warnings)


def test_all_internal_fields_at_default_no_warnings():
    """A plain domain agent with all defaults should produce zero warnings."""
    config = AgentConfig(name="clean-agent", domain="test")
    report = validate_onboarding(config)
    assert report.ok
    assert len(report.warnings) == 0


# ---------------------------------------------------------------------------
# Matrix export
# ---------------------------------------------------------------------------

def test_onboarding_matrix_structure():
    matrix = get_onboarding_matrix()
    assert isinstance(matrix, list)
    assert len(matrix) > 0
    for entry in matrix:
        assert "field" in entry
        assert "category" in entry
        assert "should_user_provide" in entry
        assert entry["should_user_provide"] in {"yes", "optional", "no"}

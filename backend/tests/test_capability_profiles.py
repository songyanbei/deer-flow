"""Tests for the capability profile admission contract."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.config.agents_config import AgentConfig
from src.config.capability_profiles import (
    PROFILE_DEFINITIONS,
    get_profile_admission_matrix,
    validate_all_active_profiles,
    validate_platform_core_wiring,
    validate_profile_admission,
)

# ---------------------------------------------------------------------------
# Profile definition completeness
# ---------------------------------------------------------------------------

def test_all_four_profiles_registered():
    expected = {
        "persistent_domain_memory",
        "domain_runbook_support",
        "domain_verifier_pack",
        "governance_strict_mode",
    }
    assert expected == set(PROFILE_DEFINITIONS.keys())


def test_profile_definitions_have_required_fields():
    for key, defn in PROFILE_DEFINITIONS.items():
        assert defn.key == key
        assert defn.display_name, f"{key}: missing display_name"
        assert defn.goal, f"{key}: missing goal"
        assert defn.why_not_core, f"{key}: missing why_not_core"
        assert defn.rollback_doc, f"{key}: missing rollback_doc"


# ---------------------------------------------------------------------------
# persistent_domain_memory admission
# ---------------------------------------------------------------------------

def test_pdm_admission_fails_without_domain():
    config = AgentConfig(name="no-domain-agent", persistent_memory_enabled=True)
    report = validate_profile_admission("persistent_domain_memory", config)
    assert not report.ok
    assert any(i.check == "domain_required" for i in report.errors)


def test_pdm_admission_fails_without_enable_switch():
    config = AgentConfig(name="no-switch-agent", domain="test", persistent_memory_enabled=False)
    report = validate_profile_admission("persistent_domain_memory", config)
    assert not report.ok
    assert any(i.check == "enable_switch" for i in report.errors)


def test_pdm_admission_fails_without_runbook(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "test-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text("name: test-agent\ndomain: test\n")
    # No RUNBOOK.md created

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(name="test-agent", domain="test", persistent_memory_enabled=True)
        report = validate_profile_admission("persistent_domain_memory", config)

    assert not report.ok
    assert any(i.check == "runbook_exists" for i in report.errors)


def test_pdm_admission_passes_with_valid_runbook(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "test-agent"
    agent_dir.mkdir(parents=True)
    runbook_content = """# Test Runbook
## Allowed Reuse
- preferred city
## Must Stay In Current Thread Truth
- booking IDs
## Conflict Resolution
1. user instruction
2. verified facts
3. persistent memory
## Safety Rules
- never skip verifier
"""
    (agent_dir / "RUNBOOK.md").write_text(runbook_content)

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(name="test-agent", domain="test", persistent_memory_enabled=True)
        report = validate_profile_admission("persistent_domain_memory", config)

    assert report.ok, f"Unexpected errors: {[str(i) for i in report.errors]}"


def test_pdm_admission_warns_on_incomplete_runbook(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "test-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "RUNBOOK.md").write_text("# Minimal runbook\nSome content\n")

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(name="test-agent", domain="test", persistent_memory_enabled=True)
        report = validate_profile_admission("persistent_domain_memory", config)

    # Should pass (incomplete runbook is a warning, not an error)
    assert report.ok
    # But should have warnings about missing sections
    assert len(report.warnings) > 0


# ---------------------------------------------------------------------------
# domain_runbook_support admission
# ---------------------------------------------------------------------------

def test_runbook_support_fails_without_runbook_file(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "test-agent"
    agent_dir.mkdir(parents=True)

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(name="test-agent", domain="test", persistent_runbook_file="RUNBOOK.md")
        report = validate_profile_admission("domain_runbook_support", config)

    assert not report.ok
    assert any(i.check == "runbook_exists" for i in report.errors)


def test_runbook_support_passes_with_runbook_file(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "test-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "RUNBOOK.md").write_text("# My Runbook\n")

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(name="test-agent", domain="test", persistent_runbook_file="RUNBOOK.md")
        report = validate_profile_admission("domain_runbook_support", config)

    assert report.ok


# ---------------------------------------------------------------------------
# domain_verifier_pack admission
# ---------------------------------------------------------------------------

def test_verifier_pack_fails_without_domain():
    config = AgentConfig(name="no-domain-agent")
    report = validate_profile_admission("domain_verifier_pack", config)
    assert not report.ok
    assert any(i.check == "domain_required" for i in report.errors)


def test_verifier_pack_warns_for_unregistered_domain():
    config = AgentConfig(name="new-agent", domain="unregistered-domain")
    report = validate_profile_admission("domain_verifier_pack", config)
    # Should not be an error (registration can happen later), but warn
    assert report.ok  # no errors
    verifier_warnings = [i for i in report.warnings if i.check == "verifier_registered"]
    assert len(verifier_warnings) >= 1


# ---------------------------------------------------------------------------
# governance_strict_mode admission
# ---------------------------------------------------------------------------

def test_governance_strict_fails_without_domain():
    config = AgentConfig(name="no-domain-agent")
    report = validate_profile_admission("governance_strict_mode", config)
    assert not report.ok
    assert any(i.check == "domain_required" for i in report.errors)


def test_governance_strict_passes_with_domain():
    config = AgentConfig(name="strict-agent", domain="finance")
    report = validate_profile_admission("governance_strict_mode", config)
    assert report.ok


# ---------------------------------------------------------------------------
# Unknown profile
# ---------------------------------------------------------------------------

def test_unknown_profile_raises_value_error():
    config = AgentConfig(name="test-agent", domain="test")
    try:
        validate_profile_admission("nonexistent_profile", config)
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "nonexistent_profile" in str(e)


# ---------------------------------------------------------------------------
# Auto-detection of active profiles
# ---------------------------------------------------------------------------

def test_validate_all_active_profiles_detects_pdm(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "mem-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "RUNBOOK.md").write_text("# Runbook\n## Allowed\n## Must Stay\n## Conflict\n## Safety\n")

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(name="mem-agent", domain="test", persistent_memory_enabled=True)
        reports = validate_all_active_profiles(config)

    profile_keys = [r.profile for r in reports]
    assert "persistent_domain_memory" in profile_keys
    # RUNBOOK.md also exists → domain_runbook_support should also be detected
    assert "domain_runbook_support" in profile_keys


def test_validate_all_active_profiles_empty_for_plain_agent():
    config = AgentConfig(name="plain-agent", domain="test")
    reports = validate_all_active_profiles(config)
    assert len(reports) == 0


def test_validate_all_active_profiles_detects_runbook_support_via_default_file(tmp_path: Path):
    """When a default RUNBOOK.md exists on disk, domain_runbook_support is auto-detected."""
    agent_dir = tmp_path / "agents" / "runbook-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "RUNBOOK.md").write_text("# Runbook\n")

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(name="runbook-agent", domain="test")
        reports = validate_all_active_profiles(config)

    profile_keys = [r.profile for r in reports]
    assert "domain_runbook_support" in profile_keys


def test_validate_all_active_profiles_detects_runbook_support_via_config():
    """When persistent_runbook_file is set, domain_runbook_support is auto-detected."""
    config = AgentConfig(name="explicit-runbook-agent", domain="test", persistent_runbook_file="CUSTOM.md")
    reports = validate_all_active_profiles(config)
    profile_keys = [r.profile for r in reports]
    assert "domain_runbook_support" in profile_keys


def test_validate_all_active_profiles_detects_governance_strict():
    """intervention_policies or hitl_keywords trigger governance_strict_mode detection."""
    config = AgentConfig(name="gov-agent", domain="finance", intervention_policies={"tool_x": "require_approval"})
    reports = validate_all_active_profiles(config)
    profile_keys = [r.profile for r in reports]
    assert "governance_strict_mode" in profile_keys


def test_validate_all_active_profiles_detects_governance_via_hitl():
    """hitl_keywords also trigger governance_strict_mode detection."""
    config = AgentConfig(name="hitl-agent", domain="hr", hitl_keywords=["delete", "terminate"])
    reports = validate_all_active_profiles(config)
    profile_keys = [r.profile for r in reports]
    assert "governance_strict_mode" in profile_keys


# ---------------------------------------------------------------------------
# Matrix export
# ---------------------------------------------------------------------------

def test_admission_matrix_structure():
    matrix = get_profile_admission_matrix()
    assert isinstance(matrix, list)
    assert len(matrix) == len(PROFILE_DEFINITIONS)
    for entry in matrix:
        assert "profile" in entry
        assert "goal" in entry
        assert "rollback" in entry


# ---------------------------------------------------------------------------
# Platform core wiring validation
# ---------------------------------------------------------------------------

def test_platform_core_passes_for_default_agent():
    config = AgentConfig(name="clean-agent", domain="test")
    report = validate_platform_core_wiring(config)
    assert report.ok
    assert len(report.issues) == 0


def test_platform_core_rejects_negative_guardrail_retries():
    config = AgentConfig(name="bad-agent", domain="test", guardrail_max_retries=-1)
    report = validate_platform_core_wiring(config)
    assert not report.ok
    assert any(i.check == "guardrail_max_retries" for i in report.issues)


def test_platform_core_warns_high_guardrail_retries():
    config = AgentConfig(name="high-retry-agent", domain="test", guardrail_max_retries=10)
    report = validate_platform_core_wiring(config)
    assert report.ok  # warning, not error
    assert any(i.check == "guardrail_max_retries" and i.severity == "warning" for i in report.issues)


def test_platform_core_rejects_invalid_safe_default():
    config = AgentConfig(name="bad-default-agent", domain="test", guardrail_safe_default="ignore")
    report = validate_platform_core_wiring(config)
    assert not report.ok
    assert any(i.check == "guardrail_safe_default" for i in report.issues)


def test_platform_core_accepts_valid_safe_defaults():
    for value in ("complete", "fail"):
        config = AgentConfig(name="agent", domain="test", guardrail_safe_default=value)
        report = validate_platform_core_wiring(config)
        assert not any(i.check == "guardrail_safe_default" for i in report.issues)


def test_platform_core_rejects_empty_mcp_binding_ref():
    from src.config.agents_config import McpBindingConfig
    config = AgentConfig(name="mcp-agent", domain="test", mcp_binding=McpBindingConfig(domain=[""]))
    report = validate_platform_core_wiring(config)
    assert not report.ok
    assert any(i.check == "mcp_binding_empty_ref" for i in report.issues)


def test_platform_core_warns_ephemeral_mcp_binding():
    from src.config.agents_config import McpBindingConfig
    config = AgentConfig(name="eph-agent", domain="test", mcp_binding=McpBindingConfig(ephemeral=["temp-server"]))
    report = validate_platform_core_wiring(config)
    assert report.ok  # warning only
    assert any(i.check == "mcp_binding_ephemeral" for i in report.issues)


def test_platform_core_rejects_zero_max_tool_calls():
    config = AgentConfig(name="no-tools-agent", domain="test", max_tool_calls=0)
    report = validate_platform_core_wiring(config)
    assert not report.ok
    assert any(i.check == "max_tool_calls" for i in report.issues)


def test_platform_core_warns_high_max_tool_calls():
    config = AgentConfig(name="many-tools-agent", domain="test", max_tool_calls=200)
    report = validate_platform_core_wiring(config)
    assert report.ok  # warning only
    assert any(i.check == "max_tool_calls" and i.severity == "warning" for i in report.issues)

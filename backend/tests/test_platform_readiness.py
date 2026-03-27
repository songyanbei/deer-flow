"""Tests for the validate_agent_platform_readiness convenience wrapper."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.config.agents_config import AgentConfig, validate_agent_platform_readiness


def test_plain_domain_agent_is_ready():
    config = AgentConfig(name="simple-agent", domain="simple")
    result = validate_agent_platform_readiness(config)
    assert result["ok"] is True
    assert result["agent_name"] == "simple-agent"
    assert result["onboarding"]["ok"] is True
    assert result["platform_core"]["ok"] is True
    assert len(result["profiles"]) == 0


def test_agent_without_domain_fails():
    config = AgentConfig(name="orphan-agent")
    result = validate_agent_platform_readiness(config)
    assert result["ok"] is False
    assert result["onboarding"]["ok"] is False
    assert any("domain" in issue for issue in result["onboarding"]["issues"])


def test_meeting_agent_with_valid_setup(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "meeting-agent"
    agent_dir.mkdir(parents=True)
    runbook = "# Runbook\n## Allowed\n## Must Stay\n## Conflict\n## Safety\n"
    (agent_dir / "RUNBOOK.md").write_text(runbook)

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(
            name="meeting-agent",
            domain="meeting",
            persistent_memory_enabled=True,
            persistent_runbook_file="RUNBOOK.md",
        )
        result = validate_agent_platform_readiness(config)

    assert result["ok"] is True
    profile_keys = [p["profile"] for p in result["profiles"]]
    assert "persistent_domain_memory" in profile_keys
    # persistent_runbook_file set + RUNBOOK.md exists → domain_runbook_support also detected
    assert "domain_runbook_support" in profile_keys


def test_agent_with_pdm_but_no_runbook_fails(tmp_path: Path):
    agent_dir = tmp_path / "agents" / "bad-agent"
    agent_dir.mkdir(parents=True)
    # No RUNBOOK.md

    with patch("src.config.capability_profiles.get_paths") as mock_paths:
        mock_paths.return_value.agent_dir.return_value = agent_dir
        config = AgentConfig(
            name="bad-agent",
            domain="test",
            persistent_memory_enabled=True,
        )
        result = validate_agent_platform_readiness(config)

    assert result["ok"] is False
    assert len(result["profiles"]) >= 1
    assert result["profiles"][0]["ok"] is False

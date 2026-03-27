"""Tests for the persistent domain memory hint extractor registry."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest.mock import patch

from src.agents.persistent_domain_memory import (
    DomainHintExtractor,
    MeetingHintExtractor,
    collect_allowed_hints,
    dedupe_hint_items,
    get_hint_extractor,
    get_persistent_domain_runbook,
    list_registered_extractors,
    register_hint_extractor,
)
from src.config.agents_config import AgentConfig
from src.agents.thread_state import TaskStatus

# ---------------------------------------------------------------------------
# Registry basics
# ---------------------------------------------------------------------------

def test_meeting_extractor_auto_registered():
    """MeetingHintExtractor should be auto-registered at module load."""
    extractor = get_hint_extractor("meeting")
    assert extractor is not None
    assert isinstance(extractor, MeetingHintExtractor)


def test_list_registered_extractors_includes_meeting():
    extractors = list_registered_extractors()
    assert "meeting" in extractors
    assert extractors["meeting"] == "MeetingHintExtractor"


def test_get_hint_extractor_returns_none_for_unknown():
    assert get_hint_extractor("nonexistent") is None
    assert get_hint_extractor(None) is None


def test_register_custom_extractor():
    class TestExtractor(DomainHintExtractor):
        @property
        def domain(self) -> str:
            return "test-domain"

        def extract(self, task: TaskStatus, verified_fact: Mapping[str, Any]) -> list[tuple[str, str]]:
            return [("test_label", "test_value")]

    register_hint_extractor(TestExtractor())
    extractor = get_hint_extractor("test-domain")
    assert extractor is not None
    result = extractor.extract({}, {})
    assert result == [("test_label", "test_value")]

    # Clean up to avoid polluting other tests
    from src.agents.persistent_domain_memory import _hint_extractors
    _hint_extractors.pop("test-domain", None)


# ---------------------------------------------------------------------------
# Meeting extractor behaviour
# ---------------------------------------------------------------------------

def test_meeting_extractor_extracts_city_from_resolved_inputs():
    extractor = MeetingHintExtractor()
    task = {
        "resolved_inputs": {"city": "Shanghai", "openId": "ou_secret"},
    }
    verified_fact = {"payload": {}}
    hints = extractor.extract(task, verified_fact)
    labels = [label for label, _ in hints]
    values = [value for _, value in hints]
    assert "Preferred booking city" in labels
    assert "Shanghai" in values


def test_meeting_extractor_filters_transactional_fields():
    extractor = MeetingHintExtractor()
    task = {"resolved_inputs": {}}
    verified_fact = {
        "payload": {
            "city": "Shanghai",
            "meeting_id": "mtg-123",
            "attendees": ["Alice"],
            "openId": "ou_secret",
        }
    }
    hints = extractor.extract(task, verified_fact)
    hint_text = " ".join(f"{label}: {val}" for label, val in hints)
    assert "Shanghai" in hint_text
    assert "mtg-123" not in hint_text
    assert "ou_secret" not in hint_text
    assert "Alice" not in hint_text


def test_meeting_extractor_deduplicates():
    extractor = MeetingHintExtractor()
    task = {"resolved_inputs": {"city": "Shanghai"}}
    verified_fact = {"payload": {"city": "Shanghai"}}
    hints = extractor.extract(task, verified_fact)
    city_hints = [(label, val) for label, val in hints if val == "Shanghai"]
    assert len(city_hints) == 1


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------

def test_collect_allowed_hints_nested():
    collected: list[tuple[str, str]] = []
    data = {
        "outer": {
            "city": "Beijing",
            "irrelevant": "ignored",
        }
    }
    collect_allowed_hints(data, allow_fields={"city": "City"}, collected=collected)
    assert ("City", "Beijing") in collected
    assert len(collected) == 1


def test_dedupe_hint_items_preserves_order():
    items = [("A", "1"), ("B", "2"), ("A", "1"), ("C", "3")]
    result = dedupe_hint_items(items)
    assert result == [("A", "1"), ("B", "2"), ("C", "3")]


# ---------------------------------------------------------------------------
# get_persistent_domain_runbook — independent of persistent_memory_enabled
# ---------------------------------------------------------------------------

def test_runbook_returned_when_only_persistent_runbook_file_set(tmp_path: Path):
    """domain_runbook_support should work without persistent_memory_enabled."""
    agent_dir = tmp_path / "agents" / "runbook-only-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "CUSTOM_RUNBOOK.md").write_text("# Custom Runbook\nSome rules\n")

    agent_cfg = AgentConfig(
        name="runbook-only-agent",
        domain="test",
        persistent_runbook_file="CUSTOM_RUNBOOK.md",
        persistent_memory_enabled=False,
    )

    with (
        patch("src.agents.persistent_domain_memory.load_agent_config", return_value=agent_cfg),
        patch("src.config.agents_config.load_agent_config", return_value=agent_cfg),
        patch("src.config.agents_config.get_paths") as mock_paths,
    ):
        mock_paths.return_value.agent_dir.return_value = agent_dir
        result = get_persistent_domain_runbook("runbook-only-agent")

    assert "Custom Runbook" in result


def test_runbook_returned_when_default_runbook_exists_on_disk(tmp_path: Path):
    """A default RUNBOOK.md on disk should be injected even without explicit config."""
    agent_dir = tmp_path / "agents" / "default-rb-agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "RUNBOOK.md").write_text("# Default Runbook\nDefault rules\n")
    (agent_dir / "config.yaml").write_text("name: default-rb-agent\ndomain: test\n")

    agent_cfg = AgentConfig(
        name="default-rb-agent",
        domain="test",
        # Neither persistent_runbook_file nor persistent_memory_enabled
    )

    with (
        patch("src.config.agents_config.load_agent_config", return_value=agent_cfg),
        patch("src.config.agents_config.get_paths") as mock_paths,
    ):
        mock_paths.return_value.agent_dir.return_value = agent_dir
        result = get_persistent_domain_runbook("default-rb-agent")

    assert "Default Runbook" in result


def test_runbook_empty_when_no_file_and_no_memory(tmp_path: Path):
    """Without persistent_runbook_file, persistent_memory_enabled, or RUNBOOK.md on disk, no runbook."""
    agent_dir = tmp_path / "agents" / "plain-agent"
    agent_dir.mkdir(parents=True)
    # No RUNBOOK.md on disk

    agent_cfg = AgentConfig(name="plain-agent", domain="test")

    with (
        patch("src.config.agents_config.load_agent_config", return_value=agent_cfg),
        patch("src.config.agents_config.get_paths") as mock_paths,
    ):
        mock_paths.return_value.agent_dir.return_value = agent_dir
        result = get_persistent_domain_runbook("plain-agent")

    assert result == ""

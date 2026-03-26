"""Tests for the persistent domain memory hint extractor registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.agents.persistent_domain_memory import (
    DomainHintExtractor,
    MeetingHintExtractor,
    collect_allowed_hints,
    dedupe_hint_items,
    get_hint_extractor,
    list_registered_extractors,
    register_hint_extractor,
)
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

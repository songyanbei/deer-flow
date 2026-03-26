"""Tests for the platform capability inventory registry."""

from __future__ import annotations

from src.config.platform_capabilities import (
    CapabilityTier,
    get_capability,
    get_capability_matrix,
    list_capabilities,
)

# ---------------------------------------------------------------------------
# Tier completeness
# ---------------------------------------------------------------------------

def test_all_capabilities_assigned_to_exactly_one_tier():
    """Every capability must belong to exactly one tier — no duplicates, no gaps."""
    all_caps = list_capabilities()
    keys = [c.key for c in all_caps]
    assert len(keys) == len(set(keys)), "Duplicate capability keys detected"

    for cap in all_caps:
        assert cap.tier in CapabilityTier, f"{cap.key} has invalid tier"


def test_platform_core_count():
    core = list_capabilities(CapabilityTier.PLATFORM_CORE)
    assert len(core) >= 8, "Expected at least 8 Platform Core capabilities"


def test_capability_profile_count():
    profiles = list_capabilities(CapabilityTier.CAPABILITY_PROFILE)
    assert len(profiles) >= 4, "Expected at least 4 Capability Profile capabilities"


def test_pilot_experimental_count():
    pilots = list_capabilities(CapabilityTier.PILOT_EXPERIMENTAL)
    assert len(pilots) >= 2, "Expected at least 2 Pilot/Experimental capabilities"


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def test_get_capability_by_key():
    cap = get_capability("engine_registry")
    assert cap is not None
    assert cap.tier == CapabilityTier.PLATFORM_CORE
    assert cap.display_name == "Engine Registry"


def test_get_capability_returns_none_for_unknown():
    assert get_capability("nonexistent_capability") is None


def test_get_persistent_domain_memory_is_capability_profile():
    cap = get_capability("persistent_domain_memory")
    assert cap is not None
    assert cap.tier == CapabilityTier.CAPABILITY_PROFILE
    assert cap.open_strategy == "admission_required"


def test_meeting_hints_is_pilot():
    cap = get_capability("meeting_persistent_memory_hints")
    assert cap is not None
    assert cap.tier == CapabilityTier.PILOT_EXPERIMENTAL
    assert cap.open_strategy == "do_not_generalize"


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------

def test_capability_descriptor_is_immutable():
    cap = get_capability("engine_registry")
    try:
        cap.tier = CapabilityTier.PILOT_EXPERIMENTAL
        assert False, "Should have raised AttributeError"
    except AttributeError:
        pass


# ---------------------------------------------------------------------------
# Matrix export
# ---------------------------------------------------------------------------

def test_get_capability_matrix_returns_dicts():
    matrix = get_capability_matrix()
    assert isinstance(matrix, list)
    assert len(matrix) > 0
    first = matrix[0]
    assert "key" in first
    assert "tier" in first
    assert "display_name" in first


def test_matrix_tiers_are_valid_strings():
    matrix = get_capability_matrix()
    valid_tiers = {t.value for t in CapabilityTier}
    for entry in matrix:
        assert entry["tier"] in valid_tiers, f"Invalid tier: {entry['tier']}"


# ---------------------------------------------------------------------------
# Tier filter
# ---------------------------------------------------------------------------

def test_list_capabilities_no_filter_returns_all():
    all_caps = list_capabilities()
    by_tier = (
        list_capabilities(CapabilityTier.PLATFORM_CORE)
        + list_capabilities(CapabilityTier.CAPABILITY_PROFILE)
        + list_capabilities(CapabilityTier.PILOT_EXPERIMENTAL)
    )
    assert len(all_caps) == len(by_tier)

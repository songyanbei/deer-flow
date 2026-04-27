"""Phase 3 / D3.2 + D3.3 regression tests.

These tests pin the LangGraph channel-constraint dependency comment and the
probe-script documentation in place so future refactors / dependency bumps
cannot silently drop the institutional knowledge that prevents the LG1.x
``Cannot specify both configurable and context`` 400 from regressing.
"""

from __future__ import annotations

from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parent.parent


# ── D3.2: pyproject.toml dependency comment ──────────────────────────


def _read_pyproject() -> str:
    return (BACKEND_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_pyproject_has_langgraph_channel_comment():
    """The langgraph pin must be preceded by a comment explaining the LG1.x
    configurable-vs-context channel constraint."""
    text = _read_pyproject()
    assert "LangGraph channel constraint" in text, (
        "Phase 3 D3.2: missing channel-constraint comment in pyproject.toml. "
        "Reviewers of LangGraph version bumps must see the dual-channel rule "
        "without reopening the bug investigation."
    )


def test_pyproject_comment_mentions_both_channels():
    text = _read_pyproject()
    # The comment must literally name both channels so a grep-based reviewer
    # can find it from either keyword.
    assert "configurable" in text
    assert "context" in text
    # And it must call out the actual error string LG returns, so a future
    # debugger searching the codebase for the 400 message lands here.
    assert "Cannot specify both configurable and context" in text


def test_pyproject_comment_immediately_precedes_langgraph_pin():
    """Guard against the comment drifting away from its dependency.

    If somebody reorders or splits the dependency list and the comment ends
    up far from the ``langgraph>=`` pin, future bump reviewers will miss it.
    """
    text = _read_pyproject()
    idx_comment = text.index("LangGraph channel constraint")
    idx_pin = text.index('"langgraph>=')
    assert idx_pin > idx_comment, "channel comment must precede the pin"
    # Tolerate a few intermediate lines (the multi-line block comment) but
    # not arbitrary distance — keep them visually adjacent.
    distance = idx_pin - idx_comment
    assert distance < 1500, (
        f"channel comment drifted {distance} chars away from the langgraph pin; "
        "move them back together."
    )


def test_pyproject_comment_points_to_probe_doc():
    """The dependency comment must point bump reviewers at the probe docs."""
    text = _read_pyproject()
    assert "langgraph_channel_probes.md" in text, (
        "Phase 3 D3.2: pyproject comment must point at the probe-script "
        "documentation so reviewers know how to re-validate channel behavior."
    )


# ── D3.3: probe-script documentation ─────────────────────────────────


PROBE_DOC = BACKEND_ROOT / "docs" / "langgraph_channel_probes.md"


def test_probe_doc_exists():
    assert PROBE_DOC.exists(), (
        "Phase 3 D3.3: backend/docs/langgraph_channel_probes.md is missing. "
        "Future LangGraph upgraders rely on this doc to know when and how "
        "to rerun the probe scripts."
    )


@pytest.mark.parametrize(
    "filename",
    [
        "_probe_channels.py",
        "probe_lg_channels.py",
        "probe_local_pregel.py",
    ],
)
def test_probe_doc_documents_each_script(filename: str):
    text = PROBE_DOC.read_text(encoding="utf-8")
    assert filename in text, (
        f"Phase 3 D3.3: {filename} is not documented in the probe doc. "
        "Every diagnostic script under backend/scripts/ that participates in "
        "the channel probe flow must be listed so a future debugger can "
        "discover it without reading source."
    )


def test_probe_doc_explains_when_to_rerun():
    text = PROBE_DOC.read_text(encoding="utf-8").lower()
    # Must contain at least one cue that triggers a rerun.
    assert "rerun" in text or "re-run" in text or "when" in text
    # Must mention dependency bumps as a trigger — the primary use case.
    assert "bump" in text or "upgrade" in text


def test_probe_doc_describes_result_interpretation():
    """The doc must teach the reader how to read .probe_out artifacts.

    Without this, results are just opaque JSON files and the original bug
    investigation has to be reopened to make sense of them.
    """
    text = PROBE_DOC.read_text(encoding="utf-8").lower()
    assert ".probe_out" in text
    # The doc lays out the expected matrix of variants — at least one of
    # the variant tags must appear by name so the reader can map output
    # files to the table.
    assert any(tag in text for tag in ("config_only", "context_only"))


def test_probe_scripts_link_to_probe_doc():
    """Each probe script's docstring should backlink the doc.

    Goal: a developer who opens one of the scripts directly sees a pointer
    to the central documentation, instead of having to grep CLAUDE.md.
    """
    scripts_dir = BACKEND_ROOT / "scripts"
    for name in ("_probe_channels.py", "probe_lg_channels.py", "probe_local_pregel.py"):
        text = (scripts_dir / name).read_text(encoding="utf-8")
        assert "langgraph_channel_probes.md" in text, (
            f"Phase 3 D3.3: {name} does not link back to the probe doc. "
            "Add a one-line pointer in the module docstring."
        )


def test_claude_md_points_at_probe_doc():
    """CLAUDE.md should list the probe doc so onboarding developers find it."""
    text = (BACKEND_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "langgraph_channel_probes.md" in text, (
        "Phase 3 D3.3: backend/CLAUDE.md must list the probe doc under the "
        "Documentation section so it is discoverable from the entry doc."
    )

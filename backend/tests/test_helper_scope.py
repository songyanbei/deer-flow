"""Tests for helper task scope limiting in semantic_router.

Three layers of protection against helper scope leakage:
  1. Prompt layer  — _build_helper_context / _build_helper_description
  2. Scope-loop detection — _detect_scope_loop
  3. Force-complete      — _force_complete_helper_scope_loop / _extract_helper_partial_result
"""

from src.agents.router.semantic_router import (
    _build_helper_context,
    _build_helper_description,
    _detect_scope_loop,
    _extract_helper_partial_result,
)


# =============================================================================
# Layer 1 — Prompt: _build_helper_description
# =============================================================================

def test_description_prefers_expected_output():
    payload = {
        "problem": "查询孙琦本月考勤汇总，缺少openId",
        "required_capability": "Look up employee openId",
        "expected_output": "The openId string for employee 孙琦",
        "reason": "...",
    }
    assert _build_helper_description(payload) == "The openId string for employee 孙琦"


def test_description_falls_back_to_required_capability():
    payload = {
        "problem": "查询孙琦本月考勤汇总",
        "required_capability": "Look up employee openId",
        "expected_output": "",
        "reason": "...",
    }
    assert _build_helper_description(payload) == "Look up employee openId"


def test_description_falls_back_to_problem():
    payload = {
        "problem": "需要协助",
        "required_capability": "",
        "expected_output": "",
        "reason": "",
    }
    assert _build_helper_description(payload) == "需要协助"


def test_description_default_when_all_empty():
    payload = {"problem": "", "required_capability": "", "expected_output": "", "reason": ""}
    assert _build_helper_description(payload) == "协助处理依赖任务"


# =============================================================================
# Layer 1 — Prompt: _build_helper_context
# =============================================================================

def test_context_problem_is_read_only_reference():
    """problem should appear as reference_context (read-only), not as actionable."""
    payload = {
        "problem": "查询孙琦本月考勤汇总，缺少openId",
        "required_capability": "Look up employee openId by name",
        "expected_output": "The openId string for employee 孙琦",
        "reason": "My tools only handle attendance and require openId.",
    }
    ctx = _build_helper_context(payload)
    # problem present but clearly labelled read-only
    assert "reference_context" in ctx
    assert "read-only" in ctx.lower() or "read-only" in ctx
    assert "考勤汇总" in ctx  # entity info preserved
    # core task fields present
    assert "required_capability: Look up employee openId by name" in ctx
    assert "expected_output: The openId string for employee 孙琦" in ctx
    assert "background:" in ctx


def test_context_bilingual_scope_constraint():
    payload = {
        "problem": "some parent goal",
        "required_capability": "fetch X",
        "expected_output": "value of X",
        "reason": "",
    }
    ctx = _build_helper_context(payload)
    # Chinese constraint
    assert "范围约束" in ctx
    assert "立即返回" in ctx
    # English constraint
    assert "SCOPE" in ctx
    assert "Do NOT pursue" in ctx


def test_context_omits_empty_fields():
    payload = {
        "problem": "",
        "required_capability": "fetch X",
        "expected_output": "",
        "reason": "",
    }
    ctx = _build_helper_context(payload)
    assert "expected_output: " not in ctx
    assert "background:" not in ctx
    # Empty problem → no "reference_context (read-only, ...): " line
    assert "reference_context (read-only" not in ctx
    assert "required_capability: fetch X" in ctx


def test_context_preserves_entity_from_problem():
    """Even though problem is read-only, entity names (e.g. 孙琦, 2026年3月) are visible."""
    payload = {
        "problem": "需要查询孙琦2026年3月的考勤",
        "required_capability": "Look up openId",
        "expected_output": "openId for 孙琦",
        "reason": "",
    }
    ctx = _build_helper_context(payload)
    assert "孙琦" in ctx
    assert "2026年3月" in ctx


# =============================================================================
# Layer 2 — Structural: _detect_scope_loop
# =============================================================================

def _make_task(task_id, *, assigned_agent=None, parent_task_id=None, **extra):
    """Minimal TaskStatus factory for testing."""
    t = {"task_id": task_id, "description": f"task-{task_id}", "status": "WAITING_DEPENDENCY"}
    if assigned_agent:
        t["assigned_agent"] = assigned_agent
    if parent_task_id:
        t["parent_task_id"] = parent_task_id
    t.update(extra)
    return t


def test_scope_loop_direct_parent():
    """hr-agent → contacts-agent(helper) trying to route back to hr-agent."""
    grandparent = _make_task("gp", assigned_agent="hr-agent")
    helper = _make_task("h1", assigned_agent="contacts-agent", parent_task_id="gp")
    pool = [grandparent, helper]
    assert _detect_scope_loop(helper, "hr-agent", pool) is True


def test_scope_loop_deep_ancestor():
    """Three levels: A → B(helper) → C(sub-helper) trying to route back to A."""
    root = _make_task("root", assigned_agent="hr-agent")
    h1 = _make_task("h1", assigned_agent="contacts-agent", parent_task_id="root")
    h2 = _make_task("h2", assigned_agent="meeting-agent", parent_task_id="h1")
    pool = [root, h1, h2]
    assert _detect_scope_loop(h2, "hr-agent", pool) is True


def test_no_scope_loop_different_agent():
    """contacts-agent(helper) routing to meeting-agent — no loop."""
    grandparent = _make_task("gp", assigned_agent="hr-agent")
    helper = _make_task("h1", assigned_agent="contacts-agent", parent_task_id="gp")
    pool = [grandparent, helper]
    assert _detect_scope_loop(helper, "meeting-agent", pool) is False


def test_no_scope_loop_root_task():
    """Root tasks (no parent_task_id) should never detect a scope loop."""
    root = _make_task("root", assigned_agent="hr-agent")
    pool = [root]
    assert _detect_scope_loop(root, "contacts-agent", pool) is False


def test_no_scope_loop_missing_ancestor():
    """If ancestor task is not in pool (e.g. already cleaned up), no loop."""
    helper = _make_task("h1", assigned_agent="contacts-agent", parent_task_id="missing")
    pool = [helper]
    assert _detect_scope_loop(helper, "hr-agent", pool) is False


def test_scope_loop_does_not_infinite_loop_on_cycle():
    """Guard against a hypothetical cycle in parent_task_id pointers."""
    t1 = _make_task("t1", assigned_agent="agent-a", parent_task_id="t2")
    t2 = _make_task("t2", assigned_agent="agent-b", parent_task_id="t1")
    pool = [t1, t2]
    # Should terminate without infinite loop; "agent-b" is ancestor of t1
    assert _detect_scope_loop(t1, "agent-b", pool) is True
    # Non-existent agent should return False, not loop forever
    assert _detect_scope_loop(t1, "agent-c", pool) is False


# =============================================================================
# Layer 3 — Force-complete: _extract_helper_partial_result
# =============================================================================

def test_extract_partial_result_from_context_payload():
    task = _make_task("h1", request_help={
        "problem": "...",
        "reason": "some reason",
        "context_payload": {"openId": "ou_mock_10033", "name": "孙琦"},
    })
    result = _extract_helper_partial_result(task)
    assert "ou_mock_10033" in result
    assert "孙琦" in result


def test_extract_partial_result_from_reason():
    task = _make_task("h1", request_help={
        "problem": "...",
        "reason": "I have the employee's openId (ou_mock_10033) from previous lookup",
        "context_payload": None,
    })
    result = _extract_helper_partial_result(task)
    assert "ou_mock_10033" in result


def test_extract_partial_result_fallback():
    task = _make_task("h1", request_help={
        "problem": "...",
        "reason": "",
        "context_payload": None,
    })
    result = _extract_helper_partial_result(task)
    assert "scope-loop" in result.lower() or "partial" in result.lower()


def test_extract_partial_result_no_request_help():
    task = _make_task("h1")
    result = _extract_helper_partial_result(task)
    assert isinstance(result, str) and len(result) > 0

"""Fixture runtime for deterministic benchmark execution.

Provides stub LLM and agent implementations so that the *real* compiled
workflow graph (planner → router → executor) can run without calling real
LLMs, MCP servers, or external services.
"""

from __future__ import annotations

import contextlib
import json
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .schema import BenchmarkCase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fixture profile registry
# ---------------------------------------------------------------------------

_PROFILES: dict[str, "FixtureProfile"] = {}


class FixtureProfile:
    """A deterministic fixture profile that drives planner, router, and executor stubs."""

    def __init__(
        self,
        name: str,
        *,
        planner_tasks: list[dict[str, Any]],
        route_map: dict[str, str],
        agent_results: dict[str, dict[str, Any]],
        clarification_triggers: list[str] | None = None,
        intervention_triggers: list[str] | None = None,
    ):
        self.name = name
        self.planner_tasks = planner_tasks
        self.route_map = route_map  # task description pattern -> agent name
        self.agent_results = agent_results  # agent name -> {result, verified_facts, ...}
        self.clarification_triggers = clarification_triggers or []
        self.intervention_triggers = intervention_triggers or []
        self._task_execution_index = 0  # tracks which task the executor is handling

    def get_agent_for_task(self, description: str) -> str:
        """Resolve agent name for a task description."""
        for pattern, agent in self.route_map.items():
            if pattern.lower() in description.lower():
                return agent
        return "SYSTEM_FALLBACK"

    def get_agent_result(self, agent_name: str) -> dict[str, Any]:
        """Get the stub result for an agent."""
        return self.agent_results.get(agent_name, {"result": f"[stub] {agent_name} completed"})

    def should_clarify(self, task_description: str) -> bool:
        return any(t.lower() in task_description.lower() for t in self.clarification_triggers)

    def should_intervene(self, task_description: str) -> bool:
        return any(t.lower() in task_description.lower() for t in self.intervention_triggers)

    def reset(self) -> None:
        self._task_execution_index = 0


def register_profile(profile: FixtureProfile) -> None:
    _PROFILES[profile.name] = profile


def get_profile(name: str) -> FixtureProfile:
    if name not in _PROFILES:
        raise KeyError(f"Unknown fixture profile: {name!r}. Registered: {sorted(_PROFILES)}")
    return _PROFILES[name]


# ---------------------------------------------------------------------------
# Stub LLM classes
# ---------------------------------------------------------------------------


class _DummyResponse:
    """Mimics a ChatModel response with .content attribute."""
    def __init__(self, content: str):
        self.content = content


class _PlannerStubLLM:
    """Stub LLM for planner_node.  Returns deterministic task decomposition JSON."""

    def __init__(self, profile: FixtureProfile, *, validate_done_summary: str | None = None):
        self._profile = profile
        self._call_count = 0
        self._validate_done_summary = validate_done_summary

    async def ainvoke(self, messages: list) -> _DummyResponse:
        self._call_count += 1
        # Detect if this is a validation call (second+ invocation after tasks exist)
        is_validate = any("evaluate whether" in str(getattr(m, "content", "")).lower() for m in messages)
        if is_validate:
            summary = self._validate_done_summary or "任务已完成。"
            return _DummyResponse(json.dumps({"done": True, "summary": summary}, ensure_ascii=False))
        tasks = [{"description": t["description"]} for t in self._profile.planner_tasks]
        return _DummyResponse(json.dumps({"done": False, "tasks": tasks}, ensure_ascii=False))


class _RouterStubLLM:
    """Stub LLM for router_node.  Returns ``<route>AGENT_NAME</route>`` based on the fixture profile."""

    def __init__(self, profile: FixtureProfile):
        self._profile = profile

    async def ainvoke(self, messages: list) -> _DummyResponse:
        # The router prompt includes the task description in the user message
        user_content = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                user_content = str(getattr(m, "content", ""))
                break
        agent = self._profile.get_agent_for_task(user_content)
        return _DummyResponse(f"<route>{agent}</route>")


class _StubDomainAgent:
    """Stub domain agent that returns task_complete tool messages with the fixture result."""

    def __init__(self, profile: FixtureProfile, case: BenchmarkCase):
        self._profile = profile
        self._case = case
        self._clarification_count = 0
        self._intervention_count = 0

    async def ainvoke(self, input_dict: dict, config: Any = None) -> dict:
        messages = input_dict.get("messages", [])
        config_dict = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
        agent_name = config_dict.get("agent_name", "unknown")

        # Extract task description from the last human message
        task_desc = ""
        for m in reversed(messages):
            if isinstance(m, HumanMessage):
                task_desc = str(getattr(m, "content", ""))
                break

        # Check clarification trigger
        if self._profile.should_clarify(task_desc) and self._clarification_count == 0:
            self._clarification_count += 1
            ask_msg = AIMessage(
                content="",
                tool_calls=[{"id": "tc_clarify", "name": "ask_clarification", "args": {"question": f"请补充信息: {task_desc[:50]}"}}],
            )
            tool_msg = ToolMessage(content=f"请补充信息: {task_desc[:50]}", tool_call_id="tc_clarify", name="ask_clarification")
            return {"messages": messages + [ask_msg, tool_msg]}

        # Check intervention trigger
        if self._profile.should_intervene(task_desc) and self._intervention_count == 0:
            self._intervention_count += 1
            resolutions = self._case.input.intervention_resolutions or []
            resolution = resolutions[0] if resolutions else {"action": "approve"}
            action = resolution.get("action", "approve")
            if action == "reject":
                ai_msg = AIMessage(content="操作已被用户拒绝。")
                fail_tool_call = AIMessage(
                    content="",
                    tool_calls=[{"id": "tc_fail", "name": "task_fail", "args": {}}],
                )
                fail_result = ToolMessage(
                    content=json.dumps({"error": resolution.get("reason", "Intervention rejected"), "error_message": resolution.get("reason", "Intervention rejected")}, ensure_ascii=False),
                    tool_call_id="tc_fail",
                    name="task_fail",
                )
                return {"messages": messages + [ai_msg, fail_tool_call, fail_result]}
            # Approved intervention: emit intervention_required signal so the real
            # executor transitions the task to WAITING_INTERVENTION / INTERRUPTED.
            import uuid
            from datetime import datetime, UTC
            req_id = f"stub_{uuid.uuid4().hex[:8]}"
            intervention_payload = json.dumps({
                "request_id": req_id,
                "fingerprint": f"fp_{req_id}",
                "intervention_type": "before_tool",
                "title": f"确认操作: {task_desc[:30]}",
                "reason": f"需要确认操作: {task_desc[:50]}",
                "source_agent": agent_name,
                "source_task_id": "stub_task",
                "action_schema": {
                    "actions": [
                        {"key": "approve", "label": "确认", "kind": "confirm", "resolution_behavior": "resume_current_task"},
                        {"key": "reject", "label": "拒绝", "kind": "confirm", "resolution_behavior": "fail_current_task"},
                    ]
                },
                "created_at": datetime.now(UTC).isoformat(),
            }, ensure_ascii=False)
            ai_msg = AIMessage(
                content="",
                tool_calls=[{"id": "tc_intervention", "name": "intervention_required", "args": {"reason": f"需要确认: {task_desc[:50]}"}}],
            )
            tool_msg = ToolMessage(content=intervention_payload, tool_call_id="tc_intervention", name="intervention_required")
            return {"messages": messages + [ai_msg, tool_msg]}

        # Normal completion
        agent_result = self._profile.get_agent_result(agent_name)
        result_text = agent_result.get("result", f"[stub] {agent_name} completed")
        fact_payload = agent_result.get("verified_facts", {})

        tc_payload = json.dumps({
            "result_text": result_text,
            "fact_payload": fact_payload,
        }, ensure_ascii=False)

        ai_msg = AIMessage(
            content="",
            tool_calls=[{"id": "tc_complete", "name": "task_complete", "args": {"result": result_text}}],
        )
        tool_msg = ToolMessage(content=tc_payload, tool_call_id="tc_complete", name="task_complete")
        return {"messages": messages + [ai_msg, tool_msg]}


# ---------------------------------------------------------------------------
# Stub agent config
# ---------------------------------------------------------------------------

def _make_stub_agent_config(name: str):
    """Build a minimal stub agent config object."""
    return SimpleNamespace(
        name=name,
        description=f"Stub {name}",
        mcp_binding=None,
        intervention_policies={},
        hitl_keywords=[],
        get_effective_mcp_binding=lambda: None,
    )


def _stub_domain_agents():
    return [
        _make_stub_agent_config("meeting-agent"),
        _make_stub_agent_config("contacts-agent"),
        _make_stub_agent_config("hr-agent"),
    ]


# ---------------------------------------------------------------------------
# Patch context manager for running the real graph with stubs
# ---------------------------------------------------------------------------


def build_fixture_patches(profile: FixtureProfile, case: BenchmarkCase):
    """Return a context manager that patches all external dependencies so the
    real compiled graph can run deterministically.

    Usage::

        with build_fixture_patches(profile, case) as event_collector:
            graph = build_multi_agent_graph_for_test()
            final_state = await graph.ainvoke(initial_state, config)
            events = event_collector  # list of captured custom events
    """
    profile.reset()

    # Build final summary from all task results
    all_results = []
    for t in profile.planner_tasks:
        agent = profile.get_agent_for_task(t.get("description", ""))
        result = profile.get_agent_result(agent)
        all_results.append(result.get("result", ""))
    done_summary = "\n".join(r for r in all_results if r) or "任务已完成。"

    planner_llm = _PlannerStubLLM(profile, validate_done_summary=done_summary)
    router_llm = _RouterStubLLM(profile)
    stub_agent = _StubDomainAgent(profile, case)

    events: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def _patches():
        with (
            patch("src.agents.planner.node.create_chat_model", return_value=planner_llm),
            patch("src.agents.planner.node.list_domain_agents", return_value=_stub_domain_agents()),
            patch("src.agents.router.semantic_router.create_chat_model", return_value=router_llm),
            patch("src.agents.router.semantic_router.list_domain_agents", return_value=_stub_domain_agents()),
            patch("src.agents.lead_agent.agent.make_lead_agent", return_value=stub_agent),
            patch("src.agents.executor.executor._ensure_mcp_ready", new_callable=AsyncMock),
            patch("src.agents.executor.executor.load_agent_config", side_effect=lambda name: _make_stub_agent_config(name)),
            patch("src.agents.planner.node.get_stream_writer", return_value=events.append),
            patch("src.agents.router.semantic_router.get_stream_writer", return_value=events.append),
            patch("src.agents.executor.executor.get_stream_writer", return_value=events.append),
        ):
            yield events

    return _patches()


# ---------------------------------------------------------------------------
# Built-in fixture profiles
# ---------------------------------------------------------------------------

def _register_builtin_profiles() -> None:
    """Register built-in fixture profiles for Phase 0 baseline cases."""

    # --- Meeting profiles ---
    register_profile(FixtureProfile(
        name="meeting_happy_path",
        planner_tasks=[{"description": "预定会议室"}],
        route_map={"预定会议": "meeting-agent", "会议": "meeting-agent"},
        agent_results={
            "meeting-agent": {
                "result": "已成功预定明天下午3点的B栋301会议室，参与人数5人。",
                "verified_facts": {"meeting_booked": {"summary": "B栋301会议室已预定", "payload": {"room": "B301", "time": "15:00"}}},
            }
        },
    ))

    register_profile(FixtureProfile(
        name="meeting_clarification_missing_time",
        planner_tasks=[{"description": "预定会议室，缺少时间信息"}],
        route_map={"预定会议": "meeting-agent", "会议": "meeting-agent"},
        agent_results={
            "meeting-agent": {
                "result": "已根据补充信息预定会议室成功。会议室：A201，时间：明天上午10点。",
                "verified_facts": {"meeting_booked": "A201会议室已预定"},
            }
        },
        clarification_triggers=["缺少时间"],
    ))

    register_profile(FixtureProfile(
        name="meeting_dependency_contacts",
        planner_tasks=[
            {"description": "查询参会人联系方式"},
            {"description": "预定会议室并通知参会人", "depends_on": ["查询参会人"]},
        ],
        route_map={"联系": "contacts-agent", "查询参会人": "contacts-agent", "预定会议": "meeting-agent", "会议": "meeting-agent"},
        agent_results={
            "contacts-agent": {
                "result": "查询到张三的工号为EMP001，邮箱 zhangsan@example.com。",
                "verified_facts": {"contact_info": {"summary": "张三 EMP001", "payload": {"name": "张三", "emp_id": "EMP001"}}},
            },
            "meeting-agent": {
                "result": "已成功预定会议室并通知参会人张三(EMP001)。",
                "verified_facts": {"meeting_booked": "会议室已预定并通知"},
            },
        },
    ))

    register_profile(FixtureProfile(
        name="meeting_conflict",
        planner_tasks=[{"description": "预定已被占用的会议室"}],
        route_map={"会议": "meeting-agent"},
        agent_results={
            "meeting-agent": {
                "result": "抱歉，B301会议室在该时段已被占用。建议选择B302或改换时间。",
            }
        },
    ))

    register_profile(FixtureProfile(
        name="meeting_governance_cancel",
        planner_tasks=[{"description": "取消已预定的会议"}],
        route_map={"会议": "meeting-agent", "取消": "meeting-agent"},
        agent_results={
            "meeting-agent": {
                "result": "会议已成功取消，已通知所有参与人。",
            }
        },
        intervention_triggers=["取消"],
    ))

    register_profile(FixtureProfile(
        name="meeting_governance_cancel_rejected",
        planner_tasks=[{"description": "取消已预定的会议"}],
        route_map={"会议": "meeting-agent", "取消": "meeting-agent"},
        agent_results={
            "meeting-agent": {
                "result": "会议取消请求已被拒绝。",
            }
        },
        intervention_triggers=["取消"],
    ))

    # --- Contacts profiles ---
    register_profile(FixtureProfile(
        name="contacts_by_name",
        planner_tasks=[{"description": "按姓名查询员工信息"}],
        route_map={"查询员工": "contacts-agent", "姓名": "contacts-agent"},
        agent_results={
            "contacts-agent": {
                "result": "查询到员工王明，工号EMP002，部门：技术部。",
                "verified_facts": {"employee_info": {"summary": "王明 EMP002 技术部", "payload": {"name": "王明", "emp_id": "EMP002", "dept": "技术部"}}},
            }
        },
    ))

    register_profile(FixtureProfile(
        name="contacts_query_openid",
        planner_tasks=[{"description": "查询员工openId"}],
        route_map={"openId": "contacts-agent", "查询": "contacts-agent"},
        agent_results={
            "contacts-agent": {
                "result": "王明的openId为ou_abc123def456。",
                "verified_facts": {"openid_info": {"summary": "王明 openId ou_abc123def456", "payload": {"openId": "ou_abc123def456"}}},
            }
        },
    ))

    register_profile(FixtureProfile(
        name="contacts_ambiguity",
        planner_tasks=[{"description": "查询同名员工张伟"}],
        route_map={"查询": "contacts-agent", "张伟": "contacts-agent"},
        agent_results={
            "contacts-agent": {
                "result": "找到两位张伟：张伟(EMP003, 技术部)和张伟(EMP004, 市场部)。请确认您要查询哪一位？",
            }
        },
        clarification_triggers=["同名"],
    ))

    register_profile(FixtureProfile(
        name="contacts_not_found",
        planner_tasks=[{"description": "查询不存在的员工"}],
        route_map={"查询": "contacts-agent"},
        agent_results={
            "contacts-agent": {"result": "未找到名为'李不存在'的员工信息。"}
        },
    ))

    register_profile(FixtureProfile(
        name="contacts_read_only",
        planner_tasks=[{"description": "只读查询员工列表"}],
        route_map={"查询": "contacts-agent", "员工": "contacts-agent"},
        agent_results={
            "contacts-agent": {
                "result": "当前技术部共有15名员工。",
                "verified_facts": {"dept_count": "技术部15人"},
            }
        },
    ))

    # --- HR profiles ---
    register_profile(FixtureProfile(
        name="hr_attendance",
        planner_tasks=[{"description": "查询考勤记录"}],
        route_map={"考勤": "hr-agent"},
        agent_results={
            "hr-agent": {
                "result": "王明本月考勤正常，无迟到早退记录。出勤22天。",
                "verified_facts": {"attendance": {"summary": "王明本月出勤22天", "payload": {"name": "王明", "days": 22}}},
            }
        },
    ))

    register_profile(FixtureProfile(
        name="hr_leave_balance",
        planner_tasks=[{"description": "查询请假和假期余额"}],
        route_map={"请假": "hr-agent", "假期": "hr-agent"},
        agent_results={
            "hr-agent": {
                "result": "王明剩余年假5天，病假3天，事假已用2天。",
                "verified_facts": {"leave_balance": {"summary": "年假5天 病假3天", "payload": {"annual": 5, "sick": 3}}},
            }
        },
    ))

    register_profile(FixtureProfile(
        name="hr_clarification_identity",
        planner_tasks=[{"description": "查询考勤但缺少身份信息"}],
        route_map={"考勤": "hr-agent"},
        agent_results={
            "hr-agent": {
                "result": "已查询到张三(EMP001)的考勤记录，本月出勤20天。",
                "verified_facts": {"attendance": "张三本月出勤20天"},
            }
        },
        clarification_triggers=["缺少身份"],
    ))

    register_profile(FixtureProfile(
        name="hr_unsupported",
        planner_tasks=[{"description": "执行无权限的HR操作"}],
        route_map={"HR": "hr-agent", "操作": "hr-agent"},
        agent_results={
            "hr-agent": {"result": "抱歉，当前权限不足以执行此操作。请联系HR管理员。"}
        },
    ))

    # --- Cross-domain workflow profiles ---
    register_profile(FixtureProfile(
        name="contacts_to_meeting_basic",
        planner_tasks=[
            {"description": "查询王明的员工编号"},
            {"description": "预定明天下午三点的会议室", "depends_on": ["查询王明"]},
        ],
        route_map={"员工编号": "contacts-agent", "查询王明": "contacts-agent", "会议室": "meeting-agent", "预定": "meeting-agent"},
        agent_results={
            "contacts-agent": {
                "result": "王明的员工编号为EMP002。",
                "verified_facts": {"employee_id": {"summary": "王明 EMP002", "payload": {"name": "王明", "emp_id": "EMP002"}}},
            },
            "meeting-agent": {
                "result": "已为王明(EMP002)预定明天下午3点的会议室C101。",
                "verified_facts": {"meeting_booked": {"summary": "C101会议室已预定", "payload": {"room": "C101"}}},
            },
        },
    ))

    register_profile(FixtureProfile(
        name="contacts_to_hr_basic",
        planner_tasks=[
            {"description": "查询李四的员工信息"},
            {"description": "查询李四的考勤记录", "depends_on": ["查询李四"]},
        ],
        route_map={"考勤": "hr-agent", "员工信息": "contacts-agent"},
        agent_results={
            "contacts-agent": {
                "result": "李四，工号EMP005，部门：产品部。",
                "verified_facts": {"employee_info": {"summary": "李四 EMP005 产品部", "payload": {"name": "李四", "emp_id": "EMP005"}}},
            },
            "hr-agent": {
                "result": "李四(EMP005)本月考勤正常，出勤21天。",
                "verified_facts": {"attendance": {"summary": "李四本月出勤21天", "payload": {"days": 21}}},
            },
        },
    ))

    register_profile(FixtureProfile(
        name="workflow_clarification_resume",
        planner_tasks=[
            {"description": "查询员工信息（缺少具体姓名）"},
            {"description": "预定会议室", "depends_on": ["查询员工"]},
        ],
        route_map={"员工": "contacts-agent", "查询": "contacts-agent", "会议": "meeting-agent"},
        agent_results={
            "contacts-agent": {
                "result": "已查询到王五(EMP006)的信息。",
                "verified_facts": {"employee_info": "王五 EMP006"},
            },
            "meeting-agent": {
                "result": "已为王五预定会议室。",
                "verified_facts": {"meeting_booked": "会议室已预定"},
            },
        },
        clarification_triggers=["缺少具体"],
    ))

    register_profile(FixtureProfile(
        name="workflow_dependency_helper_resume",
        planner_tasks=[
            {"description": "查询联系人赵六"},
            {"description": "查询赵六的假期余额", "depends_on": ["查询联系人"]},
        ],
        route_map={"联系人": "contacts-agent", "查询联系人": "contacts-agent", "假期": "hr-agent"},
        agent_results={
            "contacts-agent": {
                "result": "赵六，工号EMP007，部门：财务部。",
                "verified_facts": {"employee_info": {"summary": "赵六 EMP007", "payload": {"name": "赵六", "emp_id": "EMP007"}}},
            },
            "hr-agent": {
                "result": "赵六(EMP007)剩余年假8天。",
                "verified_facts": {"leave_balance": {"summary": "赵六年假8天", "payload": {"annual": 8}}},
            },
        },
    ))


# Auto-register on import
_register_builtin_profiles()

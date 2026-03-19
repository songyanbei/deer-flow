"""Middleware for intercepting tool calls that require user intervention before execution.

Phase 1: tool-originated intervention only. Intercepts risky tool calls before they
execute side effects, emits an `intervention_required` ToolMessage, and returns
Command(goto=END) so the executor can write WAITING_INTERVENTION state.
"""

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.graph import END
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from src.agents.intervention.decision_cache import (
    increment_cache_reuse_count,
    is_intervention_cache_valid,
)
from src.agents.intervention.display_projection import build_display_projection
from src.agents.intervention.fingerprint import generate_tool_semantic_fingerprint
from src.agents.thread_state import InterventionActionSchema, InterventionRequest

logger = logging.getLogger(__name__)

# Default write/side-effect keywords for tool risk detection (structured parser rules)
_RISKY_TOOL_KEYWORDS = {
    "write", "create", "update", "delete", "send", "cancel",
    "insert", "modify", "book", "reserve", "schedule", "submit",
    "approve", "reject", "confirm", "execute", "run", "deploy",
    "publish", "release", "remove", "drop", "transfer", "pay",
}

# Read-only tools are excluded by default
_READ_ONLY_PREFIXES = ("get_", "list_", "read_", "search_", "query_", "fetch_", "view_", "check_")


class InterventionMiddlewareState(AgentState):
    """Compatible with ThreadState."""

    pass


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _generate_fingerprint(
    run_id: str,
    task_id: str,
    agent_name: str,
    tool_name: str,
    tool_args: dict[str, Any],
) -> str:
    """Deterministic fingerprint to prevent duplicate interventions in the same run."""
    # Normalize tool args by sorting keys
    normalized_args = json.dumps(tool_args, sort_keys=True, ensure_ascii=False, default=str)
    raw = f"{run_id}:{task_id}:{agent_name}:{tool_name}:{normalized_args}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _tool_matches_keywords(tool_name: str, keywords: list[str]) -> bool:
    """Check if a tool name matches any hitl_keywords (backward-compatible fallback)."""
    name_lower = tool_name.lower()
    for keyword in keywords:
        if keyword.lower() in name_lower:
            return True
    return False


def _tool_is_risky_by_parser(tool_name: str) -> bool:
    """Structured parser: check if the tool name contains risky keywords."""
    name_lower = tool_name.lower()
    # Exclude read-only tools first
    if any(name_lower.startswith(prefix) for prefix in _READ_ONLY_PREFIXES):
        return False
    return any(keyword in name_lower for keyword in _RISKY_TOOL_KEYWORDS)


def _build_default_action_schema(tool_name: str) -> InterventionActionSchema:
    """Build default approve/reject action schema for a risky tool."""
    return {
        "actions": [
            {
                "key": "approve",
                "label": "批准执行",
                "kind": "button",
                "resolution_behavior": "resume_current_task",
            },
            {
                "key": "reject",
                "label": "拒绝执行",
                "kind": "button",
                "resolution_behavior": "fail_current_task",
            },
            {
                "key": "provide_input",
                "label": "修改后执行",
                "kind": "input",
                "resolution_behavior": "resume_current_task",
                "placeholder": "请输入修改意见...",
            },
        ]
    }


def _generate_idempotency_key(
    run_id: str,
    task_id: str,
    tool_name: str,
    tool_call_id: str,
) -> str:
    """Generate a unique idempotency key for a pending tool call.

    The key is deterministic for the same tool call in the same run context,
    enabling dedup of duplicate resume submissions.
    """
    raw = f"idem:{run_id}:{task_id}:{tool_name}:{tool_call_id}:{uuid.uuid4().hex[:8]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _build_intervention_request(
    run_id: str,
    task_id: str,
    agent_name: str,
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    policy: dict[str, Any] | None = None,
    tool_call_id: str = "",
) -> InterventionRequest:
    """Build a structured InterventionRequest for a tool call."""
    request_id = f"intv_{uuid.uuid4().hex[:16]}"
    fingerprint = _generate_fingerprint(run_id, task_id, agent_name, tool_name, tool_args)
    idempotency_key = _generate_idempotency_key(run_id, task_id, tool_name, tool_call_id)

    # Use policy overrides if available, otherwise defaults
    title = (policy or {}).get("title", f"工具 {tool_name} 需要确认")
    reason = (policy or {}).get("reason", f"Agent {agent_name} 尝试执行工具 {tool_name}，该操作可能产生副作用，需要您确认后才能继续。")
    risk_level = (policy or {}).get("risk_level", "medium")
    category = (policy or {}).get("category", "tool_execution")
    action_schema = (policy or {}).get("action_schema") or _build_default_action_schema(tool_name)

    # Build display projection (user-readable content)
    display = build_display_projection(tool_name, tool_args, agent_name)

    # Override display action labels from action_schema if display didn't set them
    if display and action_schema:
        actions = action_schema.get("actions", [])
        for action in actions:
            kind = action.get("kind")
            behavior = action.get("resolution_behavior")
            if kind == "button" and behavior == "resume_current_task" and not display.get("primary_action_label"):
                display["primary_action_label"] = action.get("label")
            elif kind == "button" and behavior == "fail_current_task" and not display.get("secondary_action_label"):
                display["secondary_action_label"] = action.get("label")
            elif kind == "input" and not display.get("respond_action_label"):
                display["respond_action_label"] = action.get("label")
                if action.get("placeholder") and not display.get("respond_placeholder"):
                    display["respond_placeholder"] = action["placeholder"]

    request: InterventionRequest = {
        "request_id": request_id,
        "fingerprint": fingerprint,
        "intervention_type": "before_tool",
        "title": title,
        "reason": reason,
        "source_agent": agent_name,
        "source_task_id": task_id,
        "tool_name": tool_name,
        "risk_level": risk_level,
        "category": category,
        "context": {
            "tool_args": tool_args,
            "idempotency_key": idempotency_key,
            "tool_call_id": tool_call_id,
        },
        "action_summary": f"执行 {tool_name}",
        "action_schema": action_schema,
        "display": display,
        "created_at": _utc_now_iso(),
    }
    return request


class InterventionMiddleware(AgentMiddleware[InterventionMiddlewareState]):
    """Intercept tool calls requiring user intervention before execution.

    Trigger priority (Phase 1):
    1. Explicit metadata on tool (tool_metadata.requires_intervention)
    2. Structured parser rules (risky keyword detection)
    3. hitl_keywords fallback from agent config

    When intervention is triggered:
    - Emits ToolMessage(name="intervention_required") with the serialized InterventionRequest
    - Returns Command(goto=END) to halt the domain agent
    """

    state_schema = InterventionMiddlewareState

    def __init__(
        self,
        *,
        intervention_policies: dict[str, Any] | None = None,
        hitl_keywords: list[str] | None = None,
        run_id: str = "",
        task_id: str = "",
        agent_name: str = "",
        resolved_fingerprints: set[str] | None = None,
        intervention_cache: dict[str, dict[str, Any]] | None = None,
    ):
        self._intervention_policies = intervention_policies or {}
        self._hitl_keywords = hitl_keywords or []
        self._run_id = run_id
        self._task_id = task_id
        self._agent_name = agent_name
        self._resolved_fingerprints = resolved_fingerprints or set()
        self._intervention_cache = intervention_cache if intervention_cache is not None else {}

    def _should_intervene(self, tool_name: str, tool_args: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
        """Determine whether a tool call should trigger intervention.

        Returns:
            (should_intervene, policy_override_or_none)
        """
        name_lower = tool_name.lower()

        # 1. Check explicit per-tool policy from intervention_policies config
        tool_policy = self._intervention_policies.get(tool_name)
        if isinstance(tool_policy, dict):
            if tool_policy.get("requires_intervention", False):
                return True, tool_policy
            if tool_policy.get("skip_intervention", False):
                return False, None

        # Read-only tools stay exempt unless explicitly overridden by policy.
        if any(name_lower.startswith(prefix) for prefix in _READ_ONLY_PREFIXES):
            return False, None

        # 2. Structured parser rules
        if _tool_is_risky_by_parser(tool_name):
            return True, None

        # 3. hitl_keywords fallback
        if self._hitl_keywords and _tool_matches_keywords(tool_name, self._hitl_keywords):
            return True, None

        return False, None

    def _check_already_resolved(self, tool_name: str, tool_args: dict[str, Any]) -> bool:
        """Check if the same intervention fingerprint was already resolved in this run."""
        fingerprint = _generate_fingerprint(
            self._run_id, self._task_id, self._agent_name, tool_name, tool_args
        )
        return fingerprint in self._resolved_fingerprints

    def _check_cached_resolution(self, tool_name: str, tool_args: dict[str, Any]) -> bool:
        semantic_fp = generate_tool_semantic_fingerprint(self._agent_name, tool_name, tool_args)
        cached = self._intervention_cache.get(semantic_fp)
        if not cached:
            return False
        if not is_intervention_cache_valid(cached, require_resume_behavior=True):
            max_reuse = cached.get("max_reuse", -1)
            reuse_count = cached.get("reuse_count", 0)
            if max_reuse != -1 and reuse_count >= max_reuse:
                logger.info(
                    "[InterventionMiddleware] [Cache EXPIRED] tool='%s' semantic_fp=%s reuse_count=%s reached max_reuse=%s",
                    tool_name,
                    semantic_fp,
                    reuse_count,
                    max_reuse,
                )
            return False

        updated_entry = increment_cache_reuse_count(cached)
        self._intervention_cache[semantic_fp] = updated_entry
        logger.info(
            "[InterventionMiddleware] [Cache HIT] tool='%s' semantic_fp=%s reuse_count=%s/%s",
            tool_name,
            semantic_fp,
            updated_entry.get("reuse_count", 0),
            updated_entry.get("max_reuse", -1),
        )
        return True

    def _handle_intervention(self, request: ToolCallRequest, tool_name: str, tool_args: dict[str, Any], policy: dict[str, Any] | None) -> Command:
        """Build intervention request and return Command to halt execution."""
        tool_call_id = request.tool_call.get("id", "")
        intervention_request = _build_intervention_request(
            run_id=self._run_id,
            task_id=self._task_id,
            agent_name=self._agent_name,
            tool_name=tool_name,
            tool_args=tool_args,
            policy=policy,
            tool_call_id=tool_call_id,
        )

        logger.info(
            "[InterventionMiddleware] Intervention triggered for tool '%s' by agent '%s'. request_id=%s fingerprint=%s",
            tool_name,
            self._agent_name,
            intervention_request["request_id"],
            intervention_request["fingerprint"],
        )

        tool_call_id = request.tool_call.get("id", "")
        payload = json.dumps(intervention_request, ensure_ascii=False, default=str)
        tool_message = ToolMessage(
            content=payload,
            tool_call_id=tool_call_id,
            name="intervention_required",
        )
        return Command(update={"messages": [tool_message]}, goto=END)

    def _process_tool_call(self, request: ToolCallRequest, handler: Callable) -> ToolMessage | Command:
        """Common logic for sync/async tool call processing."""
        tool_name = request.tool_call.get("name", "")

        should_intervene, policy = self._should_intervene(tool_name, request.tool_call.get("args", {}))
        if not should_intervene:
            return None  # signal to call handler

        tool_args = request.tool_call.get("args", {})

        # Dedup: skip if already resolved in this run
        if self._check_already_resolved(tool_name, tool_args):
            logger.info(
                "[InterventionMiddleware] Skipping intervention for tool '%s' - already resolved in run '%s'.",
                tool_name,
                self._run_id,
            )
            return None  # signal to call handler

        if self._check_cached_resolution(tool_name, tool_args):
            return None

        return self._handle_intervention(request, tool_name, tool_args, policy)

    @override
    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = self._process_tool_call(request, handler)
        if result is None:
            return handler(request)
        return result

    @override
    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        result = self._process_tool_call(request, handler)
        if result is None:
            return await handler(request)
        return result

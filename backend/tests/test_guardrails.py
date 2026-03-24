"""Tests for the output guardrail framework."""
from __future__ import annotations

import asyncio

from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agents.executor.guardrails.base import (
    GuardrailContext,
    GuardrailMetadata,
    GuardrailResult,
    GuardrailVerdict,
)
from src.agents.executor.guardrails.nudge import build_nudge_message, _OUTPUT_PREVIEW_MAX_CHARS
from src.agents.executor.guardrails.safe_default import apply_safe_default
from src.agents.executor.guardrails.structured_completion import StructuredCompletionGuardrail
from src.agents.executor.guardrails import run_output_guardrails
from src.agents.executor.outcome import (
    CompleteOutcome,
    RequestClarificationOutcome,
    FailOutcome,
    normalize_agent_outcome,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _task(**overrides) -> dict:
    base = {"task_id": "task-1", "description": "test task", "status": "RUNNING"}
    base.update(overrides)
    return base


def _complete_outcome(**overrides) -> CompleteOutcome:
    base = CompleteOutcome(
        kind="complete",
        messages=[],
        new_messages_start=0,
        result_text="done",
        fact_payload={"text": "done"},
    )
    base.update(overrides)
    return base


def _clarification_outcome(prompt: str = "请问您要查哪个月？") -> RequestClarificationOutcome:
    return RequestClarificationOutcome(
        kind="request_clarification",
        messages=[],
        new_messages_start=0,
        prompt=prompt,
    )


def _fail_outcome(msg: str = "error") -> FailOutcome:
    return FailOutcome(
        kind="fail",
        messages=[],
        new_messages_start=0,
        error_message=msg,
        retryable=False,
    )


def _ctx(
    outcome=None,
    used_fallback=True,
    attempt=0,
    max_retries=1,
    **kw,
) -> GuardrailContext:
    return GuardrailContext(
        task=kw.get("task", _task()),
        agent_name=kw.get("agent_name", "hr-agent"),
        messages=kw.get("messages", []),
        new_messages_start=kw.get("new_messages_start", 0),
        outcome=outcome or _clarification_outcome(),
        used_fallback=used_fallback,
        attempt=attempt,
        agent_config={},
        max_retries=max_retries,
    )


# ===========================================================================
# StructuredCompletionGuardrail tests
# ===========================================================================


class TestStructuredCompletionGuardrail:
    guardrail = StructuredCompletionGuardrail()

    def test_accepts_when_no_fallback(self):
        ctx = _ctx(outcome=_complete_outcome(), used_fallback=False)
        result = self.guardrail.evaluate(ctx)
        assert result.verdict == GuardrailVerdict.ACCEPT

    def test_nudges_on_fallback_attempt_0(self):
        ctx = _ctx(outcome=_clarification_outcome(), used_fallback=True, attempt=0)
        result = self.guardrail.evaluate(ctx)
        assert result.verdict == GuardrailVerdict.NUDGE_RETRY
        assert result.nudge_message is not None
        assert "STRUCTURED OUTPUT REQUIRED" in result.nudge_message.content

    def test_overrides_on_fallback_attempt_1(self):
        ctx = _ctx(outcome=_clarification_outcome(), used_fallback=True, attempt=1)
        result = self.guardrail.evaluate(ctx)
        assert result.verdict == GuardrailVerdict.OVERRIDE
        assert result.override_outcome is not None
        assert result.override_outcome["kind"] == "complete"

    def test_nudge_includes_agent_output_from_result_text(self):
        outcome = _complete_outcome(result_text="考勤结果如下：出勤20天")
        ctx = _ctx(outcome=outcome, used_fallback=True, attempt=0)
        result = self.guardrail.evaluate(ctx)
        assert "考勤结果如下" in result.nudge_message.content

    def test_nudge_includes_agent_output_from_prompt(self):
        outcome = _clarification_outcome("请选择一个会议室")
        ctx = _ctx(outcome=outcome, used_fallback=True, attempt=0)
        result = self.guardrail.evaluate(ctx)
        assert "请选择一个会议室" in result.nudge_message.content

    def test_override_extracts_text_from_clarification_prompt(self):
        prompt = "请问您要查哪个月的考勤？"
        outcome = _clarification_outcome(prompt)
        ctx = _ctx(outcome=outcome, used_fallback=True, attempt=1)
        result = self.guardrail.evaluate(ctx)
        assert result.override_outcome["result_text"] == prompt

    def test_accepts_explicit_task_complete(self):
        """task_complete tool signal should never trigger guardrail."""
        messages = [
            ToolMessage(name="task_complete", tool_call_id="tc-1", content='{"result_text":"done"}'),
        ]
        outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)
        assert used_fallback is False
        ctx = _ctx(outcome=outcome, used_fallback=False)
        result = self.guardrail.evaluate(ctx)
        assert result.verdict == GuardrailVerdict.ACCEPT

    def test_accepts_explicit_request_help(self):
        messages = [
            ToolMessage(
                name="request_help", tool_call_id="rh-1",
                content='{"problem":"p","required_capability":"c","reason":"r","expected_output":"o"}',
            ),
        ]
        outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)
        assert used_fallback is False
        ctx = _ctx(outcome=outcome, used_fallback=False)
        result = self.guardrail.evaluate(ctx)
        assert result.verdict == GuardrailVerdict.ACCEPT

    def test_accepts_explicit_task_fail(self):
        messages = [
            ToolMessage(name="task_fail", tool_call_id="tf-1", content='{"error_message":"boom","retryable":false}'),
        ]
        outcome, used_fallback = normalize_agent_outcome(task=_task(), messages=messages, new_messages_start=0)
        assert used_fallback is False
        ctx = _ctx(outcome=outcome, used_fallback=False)
        result = self.guardrail.evaluate(ctx)
        assert result.verdict == GuardrailVerdict.ACCEPT

    def test_respects_max_retries_0(self):
        """max_retries=0 means skip nudge, go straight to override."""
        ctx = _ctx(outcome=_clarification_outcome(), used_fallback=True, attempt=0, max_retries=0)
        result = self.guardrail.evaluate(ctx)
        assert result.verdict == GuardrailVerdict.OVERRIDE

    def test_guardrail_name_is_set(self):
        ctx = _ctx(used_fallback=True, attempt=0)
        result = self.guardrail.evaluate(ctx)
        assert result.guardrail_name == "structured_completion"


# ===========================================================================
# Nudge message tests
# ===========================================================================


class TestNudgeMessage:
    def test_includes_structured_output_header(self):
        msg = build_nudge_message("some output")
        assert "[STRUCTURED OUTPUT REQUIRED]" in msg.content

    def test_includes_agent_output_preview(self):
        msg = build_nudge_message("考勤20天")
        assert "考勤20天" in msg.content

    def test_truncates_long_output(self):
        long_output = "x" * 1000
        msg = build_nudge_message(long_output)
        assert "..." in msg.content
        # The full 1000-char output should NOT appear verbatim in the message
        assert long_output not in msg.content

    def test_lists_terminal_tools(self):
        msg = build_nudge_message("output")
        assert "task_complete" in msg.content
        assert "task_fail" in msg.content
        assert "request_help" in msg.content

    def test_returns_human_message(self):
        msg = build_nudge_message("output")
        assert isinstance(msg, HumanMessage)


# ===========================================================================
# Safe default tests
# ===========================================================================


class TestSafeDefault:
    def test_converts_clarification_to_complete(self):
        outcome = _clarification_outcome("请选择")
        result = apply_safe_default(outcome=outcome, messages=[], new_messages_start=0)
        assert result["kind"] == "complete"
        assert result["result_text"] == "请选择"

    def test_converts_fail_to_complete(self):
        outcome = _fail_outcome("error occurred")
        result = apply_safe_default(outcome=outcome, messages=[], new_messages_start=0)
        assert result["kind"] == "complete"

    def test_extracts_text_from_result_text(self):
        outcome = _complete_outcome(result_text="已查到")
        result = apply_safe_default(outcome=outcome, messages=[], new_messages_start=0)
        assert result["result_text"] == "已查到"

    def test_produces_valid_fact_payload_for_text(self):
        outcome = _clarification_outcome("plain text")
        result = apply_safe_default(outcome=outcome, messages=[], new_messages_start=0)
        assert result["fact_payload"] == {"text": "plain text"}

    def test_produces_valid_fact_payload_for_json(self):
        outcome = _clarification_outcome('{"key": "value"}')
        result = apply_safe_default(outcome=outcome, messages=[], new_messages_start=0)
        assert result["fact_payload"] == {"key": "value"}

    def test_falls_back_to_message_extraction_when_outcome_empty(self):
        outcome = _clarification_outcome("")
        msgs = [AIMessage(content="extracted from messages")]
        result = apply_safe_default(outcome=outcome, messages=msgs, new_messages_start=0)
        assert result["result_text"] == "extracted from messages"


# ===========================================================================
# run_output_guardrails integration tests
# ===========================================================================


def _run(coro):
    """Helper to run async coroutines in sync tests."""
    return asyncio.run(coro)


class TestRunOutputGuardrails:
    def test_no_op_when_no_fallback(self):
        outcome = _complete_outcome()
        result_outcome, result_fallback, meta = _run(run_output_guardrails(
            task=_task(),
            agent_name="hr-agent",
            messages=[],
            new_messages_start=0,
            outcome=outcome,
            used_fallback=False,
            agent_config={},
            make_agent_fn=MagicMock(),
        ))
        assert result_outcome["kind"] == "complete"
        assert not meta.guardrail_triggered

    def test_no_op_when_disabled(self):
        outcome = _clarification_outcome()
        result_outcome, _, meta = _run(run_output_guardrails(
            task=_task(),
            agent_name="hr-agent",
            messages=[],
            new_messages_start=0,
            outcome=outcome,
            used_fallback=True,
            agent_config={},
            make_agent_fn=MagicMock(),
            enabled=False,
        ))
        assert result_outcome["kind"] == "request_clarification"
        assert not meta.guardrail_triggered

    def test_nudge_succeeds_when_agent_complies(self):
        """Simulate: nudge re-invocation produces task_complete."""
        nudge_messages = [
            AIMessage(content="考勤结果"),
            HumanMessage(content="[nudge]"),
            AIMessage(content="", tool_calls=[{"id": "tc-1", "name": "task_complete", "args": {}}]),
            ToolMessage(name="task_complete", tool_call_id="tc-1", content='{"result_text":"考勤结果"}'),
        ]
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"messages": nudge_messages}
        mock_make = MagicMock(return_value=mock_agent)

        original_outcome = _clarification_outcome("考勤结果...请告诉我")
        original_messages = [AIMessage(content="考勤结果...请告诉我")]

        result_outcome, result_fallback, meta = _run(run_output_guardrails(
            task=_task(),
            agent_name="hr-agent",
            messages=original_messages,
            new_messages_start=0,
            outcome=original_outcome,
            used_fallback=True,
            agent_config={},
            make_agent_fn=mock_make,
        ))

        assert meta.guardrail_triggered
        assert meta.nudge_attempted
        assert meta.nudge_succeeded
        assert not meta.safe_default_applied
        assert result_outcome["kind"] == "complete"
        mock_make.assert_called_once()

    def test_safe_default_when_nudge_fails(self):
        """Simulate: nudge re-invocation still produces plain text."""
        nudge_messages = [
            AIMessage(content="考勤结果"),
            HumanMessage(content="[nudge]"),
            AIMessage(content="好的，还需要什么帮助吗？"),
        ]
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"messages": nudge_messages}
        mock_make = MagicMock(return_value=mock_agent)

        original_outcome = _clarification_outcome("考勤结果...请告诉我")
        original_messages = [AIMessage(content="考勤结果...请告诉我")]

        result_outcome, _, meta = _run(run_output_guardrails(
            task=_task(),
            agent_name="hr-agent",
            messages=original_messages,
            new_messages_start=0,
            outcome=original_outcome,
            used_fallback=True,
            agent_config={},
            make_agent_fn=mock_make,
        ))

        assert meta.guardrail_triggered
        assert meta.nudge_attempted
        assert not meta.nudge_succeeded
        assert meta.safe_default_applied
        assert result_outcome["kind"] == "complete"

    def test_safe_default_when_nudge_raises_exception(self):
        """If nudge invocation crashes, fall through to safe default."""
        mock_agent = AsyncMock()
        mock_agent.ainvoke.side_effect = RuntimeError("agent crashed")
        mock_make = MagicMock(return_value=mock_agent)

        original_outcome = _clarification_outcome("请选择")
        original_messages = [AIMessage(content="请选择")]

        result_outcome, _, meta = _run(run_output_guardrails(
            task=_task(),
            agent_name="hr-agent",
            messages=original_messages,
            new_messages_start=0,
            outcome=original_outcome,
            used_fallback=True,
            agent_config={},
            make_agent_fn=mock_make,
        ))

        assert meta.guardrail_triggered
        assert meta.nudge_attempted
        assert not meta.nudge_succeeded
        assert meta.safe_default_applied
        assert result_outcome["kind"] == "complete"

    def test_max_retries_0_skips_nudge(self):
        """max_retries=0 means go straight to safe default."""
        original_outcome = _clarification_outcome("请选择")

        result_outcome, _, meta = _run(run_output_guardrails(
            task=_task(),
            agent_name="hr-agent",
            messages=[AIMessage(content="请选择")],
            new_messages_start=0,
            outcome=original_outcome,
            used_fallback=True,
            agent_config={},
            make_agent_fn=MagicMock(),
            max_retries=0,
        ))

        assert meta.guardrail_triggered
        assert not meta.nudge_attempted
        assert meta.safe_default_applied
        assert result_outcome["kind"] == "complete"

    def test_metadata_tracks_outcome_kinds(self):
        """Metadata should record both original and final outcome kinds."""
        nudge_messages = [
            AIMessage(content="", tool_calls=[{"id": "tc-1", "name": "task_complete", "args": {}}]),
            ToolMessage(name="task_complete", tool_call_id="tc-1", content='{"result_text":"done"}'),
        ]
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"messages": nudge_messages}
        mock_make = MagicMock(return_value=mock_agent)

        original_outcome = _clarification_outcome("请选择")

        _, _, meta = _run(run_output_guardrails(
            task=_task(),
            agent_name="hr-agent",
            messages=[AIMessage(content="请选择")],
            new_messages_start=0,
            outcome=original_outcome,
            used_fallback=True,
            agent_config={},
            make_agent_fn=mock_make,
        ))

        assert meta.original_outcome_kind == "request_clarification"
        assert meta.final_outcome_kind == "complete"

    def test_nudge_converts_question_to_request_help(self):
        """Nudge should allow agent to call request_help if it was genuinely asking."""
        nudge_messages = [
            AIMessage(content="请问您要查哪个月？"),
            HumanMessage(content="[nudge]"),
            AIMessage(content="", tool_calls=[{"id": "rh-1", "name": "request_help", "args": {}}]),
            ToolMessage(
                name="request_help", tool_call_id="rh-1",
                content='{"problem":"需要确认月份","required_capability":"user input",'
                        '"reason":"用户未指定月份","expected_output":"月份",'
                        '"resolution_strategy":"user_clarification",'
                        '"clarification_question":"请问您要查哪个月的考勤？"}',
            ),
        ]
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"messages": nudge_messages}
        mock_make = MagicMock(return_value=mock_agent)

        original_outcome = _clarification_outcome("请问您要查哪个月？")

        result_outcome, _, meta = _run(run_output_guardrails(
            task=_task(),
            agent_name="hr-agent",
            messages=[AIMessage(content="请问您要查哪个月？")],
            new_messages_start=0,
            outcome=original_outcome,
            used_fallback=True,
            agent_config={},
            make_agent_fn=mock_make,
        ))

        assert meta.nudge_succeeded
        assert result_outcome["kind"] == "request_dependency"


# ===========================================================================
# End-to-end scenario tests (normalize_agent_outcome + guardrail)
# ===========================================================================


class TestEndToEndScenarios:
    """Test the full path: normalize -> guardrail -> final outcome."""

    def test_substantial_result_with_trailing_question_becomes_complete(self):
        """The classic bug: HR result + trailing '请告诉我' should be complete."""
        body = "以下是孙琦2026年3月考勤汇总：\n\n" + "出勤天数：20天\n缺勤天数：1天\n迟到：0次\n早退：0次\n" * 8
        trailing = "\n\n如需查看具体的异常日期或需要处理漏打卡补签，请告诉我。"
        full_output = body + trailing
        messages = [AIMessage(content=full_output)]

        outcome, used_fallback = normalize_agent_outcome(
            task=_task(), messages=messages, new_messages_start=0,
        )

        if used_fallback and outcome["kind"] != "complete":
            # Guardrail should rescue this
            nudge_messages = [
                AIMessage(content=full_output),
                HumanMessage(content="[nudge]"),
                AIMessage(content="", tool_calls=[{"id": "tc-1", "name": "task_complete", "args": {}}]),
                ToolMessage(name="task_complete", tool_call_id="tc-1",
                            content=f'{{"result_text":"{full_output[:100]}"}}'),
            ]
            mock_agent = AsyncMock()
            mock_agent.ainvoke.return_value = {"messages": nudge_messages}
            mock_make = MagicMock(return_value=mock_agent)

            result_outcome, _, meta = _run(run_output_guardrails(
                task=_task(), agent_name="hr-agent",
                messages=messages, new_messages_start=0,
                outcome=outcome, used_fallback=used_fallback,
                agent_config={}, make_agent_fn=mock_make,
            ))
            assert result_outcome["kind"] == "complete"
            assert meta.guardrail_triggered
        else:
            # _is_trailing_followup already caught it — also fine
            assert outcome["kind"] == "complete"

    def test_short_question_with_failed_nudge_still_becomes_complete(self):
        """Even a genuine short question defaults to complete after exhausted nudge."""
        messages = [AIMessage(content="请问您要查哪个月的考勤？")]
        outcome, used_fallback = normalize_agent_outcome(
            task=_task(), messages=messages, new_messages_start=0,
        )
        assert used_fallback is True
        assert outcome["kind"] == "request_clarification"

        # Simulate nudge failure
        nudge_messages = [
            AIMessage(content="请问您要查哪个月的考勤？"),
            HumanMessage(content="[nudge]"),
            AIMessage(content="请告诉我月份"),
        ]
        mock_agent = AsyncMock()
        mock_agent.ainvoke.return_value = {"messages": nudge_messages}
        mock_make = MagicMock(return_value=mock_agent)

        result_outcome, _, meta = _run(run_output_guardrails(
            task=_task(), agent_name="hr-agent",
            messages=messages, new_messages_start=0,
            outcome=outcome, used_fallback=used_fallback,
            agent_config={}, make_agent_fn=mock_make,
        ))

        assert meta.safe_default_applied
        assert result_outcome["kind"] == "complete"

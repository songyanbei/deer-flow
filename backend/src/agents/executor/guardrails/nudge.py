"""Nudge message templates for the structured-completion guardrail.

The nudge is a directive message injected into the agent conversation when
the agent failed to call a terminal tool.  It gives the agent exactly one
chance to self-correct by calling ``task_complete``, ``task_fail``, or
``request_help``.
"""

from __future__ import annotations

from langchain_core.messages import HumanMessage

# Maximum chars of the agent's original output to include in the nudge.
_OUTPUT_PREVIEW_MAX_CHARS = 500

NUDGE_TEMPLATE = """\
[STRUCTURED OUTPUT REQUIRED]

Your response was received but you did NOT call a required terminal tool.
Every task MUST end with exactly ONE of these tool calls:

1. task_complete(result_text="...") -- if your work is done
2. task_fail(error_message="...", retryable=True/False) -- if you cannot complete the task
3. request_help(problem="...", required_capability="...", reason="...", expected_output="...") -- if you need external data or user input

Your current output (first {max_chars} chars):
---
{agent_output_preview}
---

Instructions:
- If the above output represents a completed result, call task_complete now with the output as result_text.
- If the above output is asking a question that requires user input, call request_help with the appropriate resolution_strategy (e.g. "user_clarification").
- If the above output indicates a failure, call task_fail with a clear error_message.

IMPORTANT:
- Do NOT repeat your previous work or make additional tool calls.
- Do NOT add new content.
- Simply call the ONE appropriate terminal tool now."""


def build_nudge_message(agent_output: str) -> HumanMessage:
    """Build a nudge ``HumanMessage`` from the agent's plain-text output."""
    preview = agent_output[:_OUTPUT_PREVIEW_MAX_CHARS]
    if len(agent_output) > _OUTPUT_PREVIEW_MAX_CHARS:
        preview += "..."

    content = NUDGE_TEMPLATE.format(
        max_chars=_OUTPUT_PREVIEW_MAX_CHARS,
        agent_output_preview=preview,
    )
    return HumanMessage(content=content)

"""LangChain async callback handler for LLM observability metrics."""

import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

from src.observability.metrics import WorkflowMetrics


class ObservabilityCallbackHandler(AsyncCallbackHandler):
    """Records LLM call duration and token usage to WorkflowMetrics."""

    def __init__(self, node_hint: str = "default") -> None:
        super().__init__()
        self._node_hint = node_hint
        self._start_times: dict[UUID, float] = {}

    async def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: UUID, **kwargs: Any) -> None:
        self._start_times[run_id] = time.perf_counter()

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        t0 = self._start_times.pop(run_id, None)
        duration_ms = (time.perf_counter() - t0) * 1000 if t0 is not None else 0

        input_tokens, output_tokens = _extract_token_usage(response)
        model = _extract_model_name(response)

        WorkflowMetrics.get().record_llm_call(
            model=model,
            node=self._node_hint,
            duration_ms=duration_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        self._start_times.pop(run_id, None)


def _extract_token_usage(response: LLMResult) -> tuple[int, int]:
    """Extract token usage from various LLM response formats."""
    # OpenAI format: response.llm_output.token_usage
    llm_output = getattr(response, "llm_output", None) or {}
    if isinstance(llm_output, dict):
        usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        if isinstance(usage, dict):
            input_t = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            output_t = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            if input_t or output_t:
                return int(input_t), int(output_t)

    # Alternative: generation_info.usage
    try:
        generations = response.generations
        if generations and generations[0]:
            gen_info = getattr(generations[0][0], "generation_info", None) or {}
            usage = gen_info.get("usage") or {}
            if isinstance(usage, dict):
                input_t = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
                output_t = usage.get("completion_tokens") or usage.get("output_tokens") or 0
                if input_t or output_t:
                    return int(input_t), int(output_t)
    except Exception:
        pass

    return 0, 0


def _extract_model_name(response: LLMResult) -> str:
    llm_output = getattr(response, "llm_output", None) or {}
    if isinstance(llm_output, dict):
        model = llm_output.get("model_name") or llm_output.get("model") or ""
        if model:
            return str(model)
    return "unknown"

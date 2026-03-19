import json

from langchain.tools import tool


@tool("task_complete", parse_docstring=True, return_direct=True)
def task_complete_tool(
    result_text: str,
    fact_payload: dict | str | None = None,
) -> str:
    """Mark the current workflow task as successfully completed.

    Call this tool when you have finished your assigned task and have a
    final result to report.  The workflow executor will persist the result
    and advance the workflow.

    Do NOT call this tool for partial progress.  Only call it when the task
    is fully done and the result is ready.

    Args:
        result_text: A concise human-readable summary of the task result.
        fact_payload: Optional structured data payload (dict or JSON string) to store as a verified fact for downstream tasks.
    """
    # Normalize fact_payload: if the LLM passed a JSON string instead of a dict,
    # parse it here so the downstream outcome normalizer gets a clean dict.
    normalized_payload = None
    if isinstance(fact_payload, str):
        try:
            parsed = json.loads(fact_payload)
            if isinstance(parsed, dict):
                normalized_payload = parsed
        except (json.JSONDecodeError, TypeError):
            pass
    elif isinstance(fact_payload, dict):
        normalized_payload = fact_payload

    # Return structured JSON so outcome normalizer can extract real values
    # instead of falling back to AI free-text heuristics.
    return json.dumps(
        {"result_text": result_text, "fact_payload": normalized_payload},
        ensure_ascii=False,
    )

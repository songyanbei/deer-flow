from langchain.tools import tool


@tool("task_complete", parse_docstring=True, return_direct=True)
def task_complete_tool(
    result_text: str,
    fact_payload: dict | None = None,
) -> str:
    """Mark the current workflow task as successfully completed.

    Call this tool when you have finished your assigned task and have a
    final result to report.  The workflow executor will persist the result
    and advance the workflow.

    Do NOT call this tool for partial progress.  Only call it when the task
    is fully done and the result is ready.

    Args:
        result_text: A concise human-readable summary of the task result.
        fact_payload: Optional structured data payload to store as a verified fact for downstream tasks.
    """
    return "Task completion recorded by executor"

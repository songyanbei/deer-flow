from langchain.tools import tool


@tool("task_fail", parse_docstring=True, return_direct=True)
def task_fail_tool(
    error_message: str,
    retryable: bool = False,
) -> str:
    """Mark the current workflow task as failed.

    Call this tool when the task cannot be completed due to an
    unrecoverable error or missing prerequisite that cannot be obtained
    through ``request_help`` or ``ask_clarification``.

    Args:
        error_message: A concise description of why the task failed.
        retryable: Whether the failure is transient and the task could succeed on retry.
    """
    return "Task failure recorded by executor"

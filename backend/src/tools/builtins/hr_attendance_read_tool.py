"""Mock HR attendance lookup tool derived from Laifu agent core."""

from langchain.tools import tool


@tool("hr_attendance_read", parse_docstring=True)
def hr_attendance_read_tool(
    employee_name: str,
    time_period: str = "tomorrow morning",
) -> str:
    """Look up attendance and leave status for an employee during a time period.

    Args:
        employee_name: Employee name to check.
        time_period: Natural-language time period such as "tomorrow morning".

    Returns:
        A mock attendance result describing whether leave exists.
    """

    return f"Attendance check result: {employee_name} has no leave recorded for {time_period}."

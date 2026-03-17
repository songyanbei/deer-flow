from langchain.tools import tool


@tool("request_help", parse_docstring=True, return_direct=True)
def request_help_tool(
    problem: str,
    required_capability: str,
    reason: str,
    expected_output: str,
    resolution_strategy: str | None = None,
    clarification_question: str | None = None,
    clarification_options: list[str] | None = None,
    clarification_context: str | None = None,
    context_payload: dict | None = None,
    candidate_agents: list[str] | None = None,
) -> str:
    """Escalate a workflow subtask back to the top-level router when the current domain agent hits a capability boundary.

    Use this tool only inside workflow domain-agent execution when:
    - You need a capability or fact that is outside your tool boundary
    - Another domain agent may be able to help
    - You need the top-level workflow router to decide the next handler
    - The blocker is a user-owned choice that only the top-level workflow may ask

    Do not use this tool:
    - To directly execute another agent yourself
    - For facts you can already obtain with your own tools

    Args:
        problem: Short, user-friendly description of what is blocked, in the user's language. Do NOT include internal field names or API parameter names. Describe in plain business terms.
        required_capability: Capability or fact type the current agent is missing.
        reason: Why the current agent cannot continue without help.
        expected_output: What useful result should be returned to resume the parent task.
        resolution_strategy: Optional router hint. Use "user_clarification" when the blocker is a user decision rather than another agent capability.
        clarification_question: Optional direct question for the user when resolution_strategy is "user_clarification".
        clarification_options: Optional viable options to present for a clarification request.
        clarification_context: Optional short explanation for why the clarification is needed.
        context_payload: Optional structured context that may help the top-level router or helper.
        candidate_agents: Optional candidate agent names to hint possible helpers. The router makes the final decision.
    """
    return "Help request processed by middleware"

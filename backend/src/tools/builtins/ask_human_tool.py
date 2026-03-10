"""Laifu-compatible user clarification tool."""

from langchain.tools import tool


@tool("ask_human", parse_docstring=True, return_direct=True)
def ask_human_tool(question: str, options: str = "") -> str:
    """Ask the user to confirm or provide information.

    Args:
        question: The exact question that should be shown to the user.
        options: Optional choices separated by `|`.
    """
    cleaned_options = [option.strip() for option in options.split("|") if option.strip()]
    if cleaned_options:
        return question + "\n" + "\n".join(f"- {option}" for option in cleaned_options)
    return question

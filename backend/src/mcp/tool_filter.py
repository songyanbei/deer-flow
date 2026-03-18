"""MCP tool filtering for read-only agents.

Centralises the write-operation keyword list so that both ``lead_agent``
and any future consumer share the same logic.
"""

import logging

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

# Minimum set of keywords that indicate a write / mutating operation.
WRITE_KEYWORDS: frozenset[str] = frozenset(
    {
        "write",
        "create",
        "update",
        "delete",
        "cancel",
        "insert",
        "modify",
        "submit",
    }
)


def is_read_only_tool(tool: BaseTool) -> bool:
    """Return ``True`` if the tool name does not contain any write keyword."""
    name_lower = getattr(tool, "name", "").lower()
    return not any(kw in name_lower for kw in WRITE_KEYWORDS)


def filter_read_only_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Return only the tools whose names do not match any write keyword.

    Args:
        tools: Full list of MCP tools.

    Returns:
        Filtered list containing only read-safe tools.
    """
    filtered = [t for t in tools if is_read_only_tool(t)]
    removed = len(tools) - len(filtered)
    if removed:
        logger.info("[McpToolFilter] Filtered out %d write-like tool(s), keeping %d read-only tool(s).", removed, len(filtered))
    return filtered

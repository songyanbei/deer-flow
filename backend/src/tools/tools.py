import logging
import asyncio

from langchain.tools import BaseTool

from src.config import get_app_config
from src.reflection import resolve_variable
from src.tools.private_tool_names import PRIVATE_SUBAGENT_TOOL_NAMES
from src.tools.builtins import (
    ask_clarification_tool,
    ask_human_tool,
    hr_attendance_read_tool,
    present_file_tool,
    task_tool,
    view_image_tool,
    yield_for_help_tool,
)

logger = logging.getLogger(__name__)

BUILTIN_TOOLS = [
    present_file_tool,
    ask_human_tool,
    ask_clarification_tool,
    yield_for_help_tool,
    hr_attendance_read_tool,
]

SUBAGENT_TOOLS = [
    task_tool,
    # task_status_tool is no longer exposed to LLM (backend handles polling internally)
]


def get_available_tools(
    groups: list[str] | None = None,
    include_mcp: bool = True,
    model_name: str | None = None,
    subagent_enabled: bool = False,
    include_private_tools: bool = False,
) -> list[BaseTool]:
    """Get all available tools from config.

    Note: MCP tools should be initialized at application startup using
    `initialize_mcp_tools()` from src.mcp module.

    Args:
        groups: Optional list of tool groups to filter by.
        include_mcp: Whether to include tools from MCP servers (default: True).
        model_name: Optional model name to determine if vision tools should be included.
        subagent_enabled: Whether to include subagent tools (task, task_status).
        include_private_tools: Whether to expose subagent-only domain tools directly.

    Returns:
        List of available tools.
    """
    config = get_app_config()
    loaded_tools = [resolve_variable(tool.use, BaseTool) for tool in config.tools if groups is None or tool.group in groups]

    # Get cached MCP tools if enabled
    # NOTE: We use ExtensionsConfig.from_file() instead of config.extensions
    # to always read the latest configuration from disk. This ensures that changes
    # made through the Gateway API (which runs in a separate process) are immediately
    # reflected when loading MCP tools.
    mcp_tools = []
    if include_mcp:
        try:
            from src.config.extensions_config import ExtensionsConfig
            from src.mcp.cache import get_cached_mcp_tools

            extensions_config = ExtensionsConfig.from_file()
            if extensions_config.get_enabled_mcp_servers():
                mcp_tools = get_cached_mcp_tools()
                if mcp_tools:
                    logger.info(f"Using {len(mcp_tools)} cached MCP tool(s)")
        except ImportError:
            logger.warning("MCP module not available. Install 'langchain-mcp-adapters' package to enable MCP tools.")
        except Exception as e:
            logger.error(f"Failed to get cached MCP tools: {e}")

    # Conditionally add tools based on config
    builtin_tools = BUILTIN_TOOLS.copy()

    # Add subagent tools only if enabled via runtime parameter
    if subagent_enabled:
        builtin_tools.extend(SUBAGENT_TOOLS)
        logger.info("Including subagent tools (task)")

    # If no model_name specified, use the first model (default)
    if model_name is None and config.models:
        model_name = config.models[0].name

    # Add view_image_tool only if the model supports vision
    model_config = config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        builtin_tools.append(view_image_tool)
        logger.info(f"Including view_image_tool for model '{model_name}' (supports_vision=True)")

    tools = loaded_tools + builtin_tools + mcp_tools
    if include_private_tools:
        return tools

    filtered_tools = [tool for tool in tools if tool.name not in PRIVATE_SUBAGENT_TOOL_NAMES]
    hidden_count = len(tools) - len(filtered_tools)
    if hidden_count:
        logger.info("Hiding %s subagent-only tool(s) from direct agent visibility", hidden_count)
    return filtered_tools


def get_fresh_private_subagent_tools() -> list[BaseTool]:
    """Return a fresh set of subagent-only tools.

    This avoids reusing cached MCP StructuredTool instances across subagent worker
    threads, which can lead to closed transport/session errors.
    """
    private_tools = [tool for tool in BUILTIN_TOOLS if tool.name in PRIVATE_SUBAGENT_TOOL_NAMES]

    try:
        from src.mcp.tools import get_mcp_tools

        fresh_mcp_tools = asyncio.run(get_mcp_tools())
        private_tools.extend(
            tool for tool in fresh_mcp_tools if tool.name in PRIVATE_SUBAGENT_TOOL_NAMES
        )
    except Exception as exc:
        logger.error("Failed to load fresh private MCP tools for subagent execution: %s", exc)

    return private_tools

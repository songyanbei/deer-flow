import logging

from langchain.tools import BaseTool

from src.config import get_app_config
from src.reflection import resolve_variable
from src.tools.builtins import ask_clarification_tool, present_file_tool, request_help_tool, task_complete_tool, task_fail_tool, task_tool, view_image_tool

logger = logging.getLogger(__name__)

BUILTIN_TOOLS = [
    present_file_tool,
    ask_clarification_tool,
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
    is_domain_agent: bool = False,
    tenant_id: str | None = None,
) -> list[BaseTool]:
    """Get all available tools from config.

    Note: MCP tools should be initialized at application startup using
    `initialize_mcp_tools()` from src.mcp module.

    Args:
        groups: Optional list of tool groups to filter by.
        include_mcp: Whether to include tools from MCP servers (default: True).
        model_name: Optional model name to determine if vision tools should be included.
        subagent_enabled: Whether to include subagent tools (task, task_status).
        is_domain_agent: Whether the caller is a workflow domain agent.

    Returns:
        List of available tools.
    """
    config = get_app_config()
    loaded_tools = [resolve_variable(tool.use, BaseTool) for tool in config.tools if groups is None or tool.group in groups]

    # Get cached MCP tools if enabled.
    # For the main agent, only global-category MCP servers are loaded.
    # Domain agents do NOT go through this path — they get tools via
    # the runtime manager in lead_agent/agent.py.
    mcp_tools = []
    if include_mcp:
        try:
            from src.config.extensions_config import ExtensionsConfig
            from src.mcp.cache import get_cached_mcp_tools

            extensions_config = ExtensionsConfig.from_tenant(tenant_id)
            # Only consider global-category servers for the main agent
            global_servers = extensions_config.get_servers_by_category("global")
            if global_servers:
                mcp_tools = get_cached_mcp_tools(tenant_id=tenant_id)
                if mcp_tools:
                    logger.info(f"Using {len(mcp_tools)} cached MCP tool(s) (global scope)")
            elif extensions_config.get_enabled_mcp_servers():
                # Backward compatibility: if no servers have category set,
                # fall back to loading all enabled servers (old behavior).
                has_any_categorized = any(s.category != "global" for s in extensions_config.mcp_servers.values())
                if not has_any_categorized:
                    mcp_tools = get_cached_mcp_tools(tenant_id=tenant_id)
                    if mcp_tools:
                        logger.info(f"Using {len(mcp_tools)} cached MCP tool(s) (legacy, no categories)")
        except ImportError:
            logger.warning("MCP module not available. Install 'langchain-mcp-adapters' package to enable MCP tools.")
        except Exception as e:
            logger.error(f"Failed to get cached MCP tools: {e}")

    # Conditionally add tools based on config
    builtin_tools = [present_file_tool]

    if not is_domain_agent:
        builtin_tools.append(ask_clarification_tool)
    else:
        builtin_tools.append(request_help_tool)
        builtin_tools.append(task_complete_tool)
        builtin_tools.append(task_fail_tool)

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

    return loaded_tools + builtin_tools + mcp_tools

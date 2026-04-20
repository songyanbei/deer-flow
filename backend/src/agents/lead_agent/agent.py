import logging

from langchain.agents import create_agent
from langchain.agents.middleware import SummarizationMiddleware, TodoListMiddleware
from langchain_core.runnables import RunnableConfig

from src.agents.lead_agent.engine_registry import get_engine_builder
from src.agents.lead_agent.engines.base import BuildContext, get_build_time_hooks
from src.agents.lead_agent.prompt import apply_prompt_template
from src.agents.middlewares.clarification_middleware import ClarificationMiddleware
from src.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware
from src.agents.middlewares.help_request_middleware import HelpRequestMiddleware
from src.agents.middlewares.intervention_middleware import InterventionMiddleware
from src.agents.middlewares.memory_middleware import MemoryMiddleware
from src.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from src.agents.middlewares.thread_data_middleware import ThreadDataMiddleware
from src.agents.middlewares.title_middleware import TitleMiddleware
from src.agents.middlewares.tool_call_limit_middleware import ToolCallLimitMiddleware
from src.agents.middlewares.uploads_middleware import UploadsMiddleware
from src.agents.middlewares.view_image_middleware import ViewImageMiddleware
from src.agents.thread_state import ThreadState
from src.config.agents_config import load_agent_config, load_agent_config_layered
from src.config.paths import resolve_tenant_agents_dir, resolve_tenant_user_agents_dir
from src.config.app_config import get_app_config
from src.config.summarization_config import get_summarization_config
from src.models import create_chat_model
from src.sandbox.middleware import SandboxMiddleware

logger = logging.getLogger(__name__)

from src.mcp.tool_filter import is_read_only_tool


def _is_read_only_tool(tool) -> bool:
    return is_read_only_tool(tool)


def _resolve_model_name(requested_model_name: str | None = None) -> str:
    """Resolve a runtime model name safely, falling back to default if invalid. Returns None if no models are configured."""
    app_config = get_app_config()
    default_model_name = app_config.models[0].name if app_config.models else None
    if default_model_name is None:
        raise ValueError("No chat models are configured. Please configure at least one model in config.yaml.")

    if requested_model_name and app_config.get_model_config(requested_model_name):
        return requested_model_name

    if requested_model_name and requested_model_name != default_model_name:
        logger.warning(f"Model '{requested_model_name}' not found in config; fallback to default model '{default_model_name}'.")
    return default_model_name


def _create_summarization_middleware() -> SummarizationMiddleware | None:
    """Create and configure the summarization middleware from config."""
    config = get_summarization_config()

    if not config.enabled:
        return None

    # Prepare trigger parameter
    trigger = None
    if config.trigger is not None:
        if isinstance(config.trigger, list):
            trigger = [t.to_tuple() for t in config.trigger]
        else:
            trigger = config.trigger.to_tuple()

    # Prepare keep parameter
    keep = config.keep.to_tuple()

    # Prepare model parameter
    if config.model_name:
        model = config.model_name
    else:
        # Use a lightweight model for summarization to save costs
        # Falls back to default model if not explicitly specified
        model = create_chat_model(thinking_enabled=False)

    # Prepare kwargs
    kwargs = {
        "model": model,
        "trigger": trigger,
        "keep": keep,
    }

    if config.trim_tokens_to_summarize is not None:
        kwargs["trim_tokens_to_summarize"] = config.trim_tokens_to_summarize

    if config.summary_prompt is not None:
        kwargs["summary_prompt"] = config.summary_prompt

    return SummarizationMiddleware(**kwargs)


def _create_todo_list_middleware(is_plan_mode: bool) -> TodoListMiddleware | None:
    """Create and configure the TodoList middleware.

    Args:
        is_plan_mode: Whether to enable plan mode with TodoList middleware.

    Returns:
        TodoListMiddleware instance if plan mode is enabled, None otherwise.
    """
    if not is_plan_mode:
        return None

    # Custom prompts matching DeerFlow's style
    system_prompt = """
<todo_list_system>
You have access to the `write_todos` tool to help you manage and track complex multi-step objectives.

**CRITICAL RULES:**
- Mark todos as completed IMMEDIATELY after finishing each step - do NOT batch completions
- Keep EXACTLY ONE task as `in_progress` at any time (unless tasks can run in parallel)
- Update the todo list in REAL-TIME as you work - this gives users visibility into your progress
- DO NOT use this tool for simple tasks (< 3 steps) - just complete them directly

**When to Use:**
This tool is designed for complex objectives that require systematic tracking:
- Complex multi-step tasks requiring 3+ distinct steps
- Non-trivial tasks needing careful planning and execution
- User explicitly requests a todo list
- User provides multiple tasks (numbered or comma-separated list)
- The plan may need revisions based on intermediate results

**When NOT to Use:**
- Single, straightforward tasks
- Trivial tasks (< 3 steps)
- Purely conversational or informational requests
- Simple tool calls where the approach is obvious

**Best Practices:**
- Break down complex tasks into smaller, actionable steps
- Use clear, descriptive task names
- Remove tasks that become irrelevant
- Add new tasks discovered during implementation
- Don't be afraid to revise the todo list as you learn more

**Task Management:**
Writing todos takes time and tokens - use it when helpful for managing complex problems, not for simple requests.
</todo_list_system>
"""

    tool_description = """Use this tool to create and manage a structured task list for complex work sessions.

**IMPORTANT: Only use this tool for complex tasks (3+ steps). For simple requests, just do the work directly.**

## When to Use

Use this tool in these scenarios:
1. **Complex multi-step tasks**: When a task requires 3 or more distinct steps or actions
2. **Non-trivial tasks**: Tasks requiring careful planning or multiple operations
3. **User explicitly requests todo list**: When the user directly asks you to track tasks
4. **Multiple tasks**: When users provide a list of things to be done
5. **Dynamic planning**: When the plan may need updates based on intermediate results

## When NOT to Use

Skip this tool when:
1. The task is straightforward and takes less than 3 steps
2. The task is trivial and tracking provides no benefit
3. The task is purely conversational or informational
4. It's clear what needs to be done and you can just do it

## How to Use

1. **Starting a task**: Mark it as `in_progress` BEFORE beginning work
2. **Completing a task**: Mark it as `completed` IMMEDIATELY after finishing
3. **Updating the list**: Add new tasks, remove irrelevant ones, or update descriptions as needed
4. **Multiple updates**: You can make several updates at once (e.g., complete one task and start the next)

## Task States

- `pending`: Task not yet started
- `in_progress`: Currently working on (can have multiple if tasks run in parallel)
- `completed`: Task finished successfully

## Task Completion Requirements

**CRITICAL: Only mark a task as completed when you have FULLY accomplished it.**

Never mark a task as completed if:
- There are unresolved issues or errors
- Work is partial or incomplete
- You encountered blockers preventing completion
- You couldn't find necessary resources or dependencies
- Quality standards haven't been met

If blocked, keep the task as `in_progress` and create a new task describing what needs to be resolved.

## Best Practices

- Create specific, actionable items
- Break complex tasks into smaller, manageable steps
- Use clear, descriptive task names
- Update task status in real-time as you work
- Mark tasks complete IMMEDIATELY after finishing (don't batch completions)
- Remove tasks that are no longer relevant
- **IMPORTANT**: When you write the todo list, mark your first task(s) as `in_progress` immediately
- **IMPORTANT**: Unless all tasks are completed, always have at least one task `in_progress` to show progress

Being proactive with task management demonstrates thoroughness and ensures all requirements are completed successfully.

**Remember**: If you only need a few tool calls to complete a task and it's clear what to do, it's better to just do the task directly and NOT use this tool at all.
"""

    return TodoListMiddleware(system_prompt=system_prompt, tool_description=tool_description)


# ThreadDataMiddleware must be before SandboxMiddleware to ensure thread_id is available
# UploadsMiddleware should be after ThreadDataMiddleware to access thread_id
# DanglingToolCallMiddleware patches missing ToolMessages before model sees the history
# SummarizationMiddleware should be early to reduce context before other processing
# TodoListMiddleware should be before ClarificationMiddleware to allow todo management
# TitleMiddleware generates title after first exchange
# MemoryMiddleware queues conversation for memory update (after TitleMiddleware)
# ViewImageMiddleware should be before ClarificationMiddleware to inject image details before LLM
# ClarificationMiddleware should be last to intercept clarification requests after model calls
def _build_middlewares(config: RunnableConfig, model_name: str | None, agent_name: str | None = None):
    """Build middleware chain based on runtime configuration.

    Args:
        config: Runtime configuration containing configurable options like is_plan_mode.
        agent_name: If provided, MemoryMiddleware will use per-agent memory storage.

    Returns:
        List of middleware instances.
    """
    middlewares = [ThreadDataMiddleware(), UploadsMiddleware(), SandboxMiddleware(), DanglingToolCallMiddleware()]

    # Add summarization middleware if enabled
    summarization_middleware = _create_summarization_middleware()
    if summarization_middleware is not None:
        middlewares.append(summarization_middleware)

    # Add TodoList middleware if plan mode is enabled
    is_plan_mode = config.get("configurable", {}).get("is_plan_mode", False)
    todo_list_middleware = _create_todo_list_middleware(is_plan_mode)
    if todo_list_middleware is not None:
        middlewares.append(todo_list_middleware)
    is_domain_agent = config.get("configurable", {}).get("is_domain_agent", False)
    if not is_domain_agent:
        # Add TitleMiddleware
        middlewares.append(TitleMiddleware())

        # Add MemoryMiddleware (after TitleMiddleware)
        middlewares.append(MemoryMiddleware(agent_name=agent_name))

    max_tool_calls = config.get("configurable", {}).get("max_tool_calls")
    if isinstance(max_tool_calls, int):
        middlewares.append(ToolCallLimitMiddleware(max_tool_calls=max_tool_calls))

    # Add ViewImageMiddleware only if the current model supports vision.
    # Use the resolved runtime model_name from make_lead_agent to avoid stale config values.
    app_config = get_app_config()
    model_config = app_config.get_model_config(model_name) if model_name else None
    if model_config is not None and model_config.supports_vision:
        middlewares.append(ViewImageMiddleware())

    # Add SubagentLimitMiddleware to truncate excess parallel task calls
    subagent_enabled = config.get("configurable", {}).get("subagent_enabled", False)
    if subagent_enabled:
        max_concurrent_subagents = config.get("configurable", {}).get("max_concurrent_subagents", 3)
        middlewares.append(SubagentLimitMiddleware(max_concurrent=max_concurrent_subagents))

    if is_domain_agent:
        # InterventionMiddleware intercepts risky tool calls before execution.
        # Must be before HelpRequestMiddleware so intervention takes priority.
        intervention_policies = config.get("configurable", {}).get("intervention_policies") or {}
        hitl_keywords = config.get("configurable", {}).get("hitl_keywords") or []
        run_id = config.get("configurable", {}).get("run_id") or ""
        task_id = config.get("configurable", {}).get("task_id") or ""
        intervention_agent_name = config.get("configurable", {}).get("agent_name") or ""
        resolved_fingerprints = config.get("configurable", {}).get("resolved_fingerprints") or set()
        intervention_cache = config.get("configurable", {}).get("intervention_cache")
        intervention_thread_id = config.get("configurable", {}).get("thread_id") or ""
        intervention_tenant_id = config.get("configurable", {}).get("tenant_id")
        intervention_user_id = config.get("configurable", {}).get("user_id")
        middlewares.append(
            InterventionMiddleware(
                intervention_policies=intervention_policies,
                hitl_keywords=hitl_keywords,
                run_id=run_id,
                task_id=task_id,
                agent_name=intervention_agent_name,
                thread_id=intervention_thread_id,
                resolved_fingerprints=resolved_fingerprints,
                intervention_cache=intervention_cache,
                tenant_id=intervention_tenant_id,
                user_id=intervention_user_id,
            )
        )
        middlewares.append(HelpRequestMiddleware())

    # ClarificationMiddleware should always be last
    middlewares.append(ClarificationMiddleware())
    return middlewares


def make_lead_agent(config: RunnableConfig):
    # Lazy import to avoid circular dependency
    from src.tools import get_available_tools
    from src.tools.builtins import setup_agent

    cfg = config.get("configurable", {})

    thinking_enabled = cfg.get("thinking_enabled", True)
    reasoning_effort = cfg.get("reasoning_effort", None)
    requested_model_name: str | None = cfg.get("model_name") or cfg.get("model")
    is_plan_mode = cfg.get("is_plan_mode", False)
    subagent_enabled = cfg.get("subagent_enabled", False)
    max_concurrent_subagents = cfg.get("max_concurrent_subagents", 3)
    is_bootstrap = cfg.get("is_bootstrap", False)
    agent_name = cfg.get("agent_name")
    tenant_id = cfg.get("tenant_id", "default")
    user_id = cfg.get("user_id")
    auth_user = cfg.get("auth_user")
    agents_dir = resolve_tenant_user_agents_dir(tenant_id, user_id) or resolve_tenant_agents_dir(tenant_id)

    agent_config = load_agent_config_layered(agent_name, tenant_id=tenant_id, user_id=user_id) if not is_bootstrap else None
    engine_builder = get_engine_builder(agent_config.engine_type if agent_config else None)
    engine_prompt_kwargs = engine_builder.build_prompt_kwargs()

    # Build-time hook context
    hooks = get_build_time_hooks()
    build_ctx = BuildContext(
        agent_name=agent_name,
        engine_type=engine_builder.canonical_name,
        model_name=cfg.get("model_name") or cfg.get("model"),
        is_domain_agent=bool(cfg.get("is_domain_agent", False)),
        is_bootstrap=is_bootstrap,
    )
    hooks.before_agent_build(build_ctx)

    # Custom agent model or fallback to global/default model resolution
    agent_model_name = agent_config.model if agent_config and agent_config.model else _resolve_model_name()

    # Final model name resolution with request override, then agent config, then global default
    model_name = requested_model_name or agent_model_name

    app_config = get_app_config()
    model_config = app_config.get_model_config(model_name) if model_name else None

    if model_config is None:
        raise ValueError("No chat model could be resolved. Please configure at least one model in config.yaml or provide a valid 'model_name'/'model' in the request.")
    if thinking_enabled and not model_config.supports_thinking:
        logger.warning(f"Thinking mode is enabled but model '{model_name}' does not support it; fallback to non-thinking mode.")
        thinking_enabled = False

    logger.info(
        "Create Agent(%s) -> thinking_enabled: %s, reasoning_effort: %s, model_name: %s, is_plan_mode: %s, subagent_enabled: %s, max_concurrent_subagents: %s",
        agent_name or "default",
        thinking_enabled,
        reasoning_effort,
        model_name,
        is_plan_mode,
        subagent_enabled,
        max_concurrent_subagents,
    )

    if agent_config and "max_tool_calls" not in cfg:
        cfg["max_tool_calls"] = agent_config.max_tool_calls

    # Inject run metadata for LangSmith trace tagging
    if "metadata" not in config:
        config["metadata"] = {}

    config["metadata"].update(
        {
            "agent_name": agent_name or "default",
            "model_name": model_name or "default",
            "thinking_enabled": thinking_enabled,
            "reasoning_effort": reasoning_effort,
            "is_plan_mode": is_plan_mode,
            "subagent_enabled": subagent_enabled,
        }
    )

    if is_bootstrap:
        # Special bootstrap agent with minimal prompt for initial custom agent creation flow
        system_prompt = apply_prompt_template(subagent_enabled=subagent_enabled, max_concurrent_subagents=max_concurrent_subagents, available_skills=set(["bootstrap"]), auth_user=auth_user)

        from src.agents.security.identity_guard import wrap_tools as _wrap_identity_tools
        agent = create_agent(
            model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled),
            tools=_wrap_identity_tools(
                get_available_tools(model_name=model_name, subagent_enabled=subagent_enabled, is_domain_agent=False, tenant_id=tenant_id) + [setup_agent],
                auth_user,
            ),
            middleware=_build_middlewares(config, model_name=model_name),
            system_prompt=system_prompt,
            state_schema=ThreadState,
        )
        hooks.after_agent_build(build_ctx)
        return agent

    # Resolve available_skills from agent config (domain agents may restrict which skills are exposed)
    available_skills: set[str] | None = None
    if agent_config and agent_config.available_skills is not None:
        available_skills = set(agent_config.available_skills)
    build_ctx.available_skills = available_skills
    hooks.before_skill_resolve(build_ctx)
    available_skills = build_ctx.available_skills

    # Fetch per-agent MCP tools from the unified runtime manager (already connected by executor).
    # We look up tools here at agent-build time rather than reading from config.configurable
    # to avoid StructuredTool objects being serialized during LangGraph checkpointing.
    hooks.before_mcp_bind(build_ctx)
    extra_tools: list = list(build_ctx.extra_tools)
    if agent_name:
        try:
            from src.mcp.runtime_manager import mcp_runtime

            scope_key = mcp_runtime.scope_key_for_user_agent(agent_name, tenant_id=tenant_id, user_id=user_id)
            mcp_tools = mcp_runtime.get_tools_sync(scope_key)

            mcp_tools = engine_builder.prepare_extra_tools(mcp_tools)
            extra_tools.extend(mcp_tools)
            if mcp_tools:
                logger.info("Injecting %d MCP tool(s) for agent '%s'.", len(mcp_tools), agent_name)
        except Exception as e:
            logger.warning("Failed to get MCP tools for agent '%s': %s", agent_name, e)

    # Default lead agent (unchanged behavior)
    from src.agents.security.identity_guard import wrap_tools as _wrap_identity_tools
    _all_tools = (
        get_available_tools(
            model_name=model_name,
            groups=agent_config.tool_groups if agent_config else None,
            include_mcp=not bool(cfg.get("is_domain_agent", False)),
            subagent_enabled=subagent_enabled,
            is_domain_agent=bool(cfg.get("is_domain_agent", False)),
            tenant_id=tenant_id,
            user_id=user_id,
        )
        + extra_tools
    )
    agent = create_agent(
        model=create_chat_model(name=model_name, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort),
        tools=_wrap_identity_tools(_all_tools, auth_user),
        middleware=_build_middlewares(config, model_name=model_name, agent_name=agent_name),
        system_prompt=apply_prompt_template(
            subagent_enabled=subagent_enabled,
            max_concurrent_subagents=max_concurrent_subagents,
            agent_name=agent_name,
            available_skills=available_skills,
            is_domain_agent=bool(cfg.get("is_domain_agent", False)),
            engine_mode=engine_prompt_kwargs.engine_mode,
            tenant_id=tenant_id,
            user_id=user_id,
            agents_dir=agents_dir,
            auth_user=auth_user,
        ),
        state_schema=ThreadState,
    )
    hooks.after_agent_build(build_ctx)
    return agent

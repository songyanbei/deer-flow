# Backend Architecture Notes

## Scope

Backend ownership is the Python service under `backend/`.

This side is responsible for:

- LangGraph runtime and agent graphs
- workflow orchestration
- thread state and task state
- gateway APIs for uploads, artifacts, agents, skills, memory, models, MCP
- sandbox-backed file/runtime environment
- tool loading and domain-agent execution

## Runtime Topology

The backend is not a single process. It is split into two main runtime roles:

### 1. LangGraph Server

This is the agent runtime.

Relevant files:

- `backend/langgraph.json`
- `backend/src/agents/__init__.py`
- `backend/src/agents/entry_graph.py`
- `backend/src/agents/graph.py`
- `backend/src/agents/lead_agent/agent.py`

Current registered graphs:

- `entry_graph`
  - default entry for orchestration selection
- `lead_agent`
  - direct single-agent path
- `multi_agent`
  - direct workflow graph path

Responsibilities:

- load thread state
- run middleware chain
- execute leader mode or workflow mode
- stream values, messages, and custom events

### 2. Gateway API

This is the REST layer for non-LangGraph operations.

Relevant files:

- `backend/src/gateway/app.py`
- `backend/src/gateway/routers/`

Responsibilities:

- uploads API
- artifacts API
- custom agents CRUD
- skills API
- models API
- MCP config API
- memory API

Important split:

- chat execution, thread runs, and streaming are handled by LangGraph
- file management and auxiliary APIs are handled by Gateway

## Core Backend Areas

### Agent Graph Layer

Relevant files:

- `backend/src/agents/entry_graph.py`
- `backend/src/agents/graph.py`
- `backend/src/agents/orchestration/selector.py`
- `backend/src/agents/planner/node.py`
- `backend/src/agents/router/semantic_router.py`
- `backend/src/agents/executor/executor.py`

This layer decides how one user turn is executed.

#### Entry Graph

`entry_graph` is the main orchestration entry.

Flow:

1. `orchestration_selector`
2. route to:
   - `leader_entry`, or
   - `workflow_planner`
3. workflow path then loops through:
   - planner
   - router
   - executor

This means current frontend behavior can differ depending on whether the same
user message was resolved to `leader` or `workflow`.

#### Leader Mode

Leader mode is built by `make_lead_agent(...)` in:

- `backend/src/agents/lead_agent/agent.py`

It behaves like a direct agent run with middleware and tools.

Typical use:

- open-ended tasks
- low-structure requests
- non-workflow conversations

#### Workflow Mode

Workflow mode is a state-machine style graph:

1. planner decomposes or validates work
2. router assigns pending tasks or resumes blocked tasks
3. executor dispatches a running task to a domain agent
4. loop continues until planner marks the goal done or errors out

This is the core path for multi-step orchestration.

### State Layer

Relevant file:

- `backend/src/agents/thread_state.py`

`ThreadState` extends the base LangGraph agent state and is the shared schema
across workflow nodes.

Important state groups:

- execution/runtime:
  - `sandbox`
  - `thread_data`
  - `run_id`
  - `execution_state`
- thread-level metadata:
  - `title`
  - `artifacts`
  - `uploaded_files`
  - `viewed_images`
- orchestration:
  - `requested_orchestration_mode`
  - `resolved_orchestration_mode`
  - `orchestration_reason`
  - `workflow_stage`
  - `workflow_stage_detail`
  - `workflow_stage_updated_at`
- workflow blackboard:
  - `planner_goal`
  - `task_pool`
  - `verified_facts`
  - `route_count`
  - `final_result`

#### Why `task_pool` Matters

`task_pool` is the backend source of truth for workflow task state.

Each task may include:

- `task_id`
- `description`
- `run_id`
- `assigned_agent`
- `status`
- `status_detail`
- `clarification_prompt`
- `request_help`
- `resolved_inputs`
- `result`
- `error`
- `updated_at`

Frontend workflow cards are effectively projections of this backend task state.

### Middleware Layer

Relevant file:

- `backend/src/agents/lead_agent/agent.py`

The middleware chain is constructed in `_build_middlewares(...)`.

Current important middlewares:

- `ThreadDataMiddleware`
  - populates thread-scoped workspace/uploads/outputs paths
- `UploadsMiddleware`
  - injects uploaded file context into the latest human message
- `SandboxMiddleware`
  - binds execution to a sandbox provider
- `DanglingToolCallMiddleware`
  - repairs missing tool-message history issues
- `SummarizationMiddleware`
  - context trimming when enabled
- `TodoListMiddleware`
  - only for plan mode
- `TitleMiddleware`
  - thread title generation
- `MemoryMiddleware`
  - queues memory updates
- `ToolCallLimitMiddleware`
  - runtime tool cap
- `ViewImageMiddleware`
  - vision support when the model supports it
- `SubagentLimitMiddleware`
  - caps parallel subagent calls
- `HelpRequestMiddleware`
  - only for workflow domain agents
- `ClarificationMiddleware`
  - intercepts clarification and ends the run for user input

#### Important Collaboration Detail

Many frontend-visible behaviors are not emitted by the graph nodes directly.
They are the result of middleware decisions, especially:

- uploads injection
- clarification interruption
- title generation
- memory side effects

When a frontend behavior looks odd, check middleware before changing graph code.

### Tool Layer

Relevant files:

- `backend/src/tools/tools.py`
- `backend/src/tools/builtins/`

Tool loading is centralized in `get_available_tools(...)`.

Tool groups:

- configured tools from `config.yaml`
- builtin tools
- MCP tools
- subagent tools when enabled

Current builtins exposed conditionally include:

- `present_files`
- `ask_clarification`
- `request_help`
- `task`
- `view_image`

Important split:

- non-domain agents can ask clarification
- workflow domain agents use `request_help` instead of `ask_clarification`

### Domain Agent Layer

Relevant files:

- `backend/src/config/agents_config.py`
- `backend/src/gateway/routers/agents.py`
- `backend/src/agents/executor/executor.py`

Custom/domain agents are configured under thread-independent agent directories.

Per-agent config may define:

- description
- model override
- tool group allowlist
- domain
- max tool calls
- MCP servers
- available skills
- default orchestration mode
- prompt file

During workflow execution, `executor_node` dispatches a running task to a
domain agent by calling `make_lead_agent(...)` with domain-agent runtime flags.

## Request And Execution Flow

### A. Normal Chat Run

High-level path:

1. frontend submits a thread run to LangGraph
2. LangGraph loads thread state
3. middleware chain runs
4. orchestration selector chooses `leader` or `workflow`
5. response streams back as values/messages/custom events

### B. Workflow Run

More detailed path:

1. selector resolves orchestration mode
2. planner either:
   - decomposes the original goal into tasks, or
   - validates whether the goal is already complete
3. router:
   - assigns pending tasks
   - resumes tasks after dependency resolution
   - interrupts for clarification if needed
4. executor:
   - runs the assigned task via a domain agent
   - writes task result/failure back into `task_pool`
   - writes verified facts for completed tasks
5. planner runs again to validate or produce further tasks

### C. File Upload Flow

Relevant files:

- `backend/src/gateway/routers/uploads.py`
- `backend/src/agents/middlewares/uploads_middleware.py`
- `backend/src/config/paths.py`

Flow:

1. frontend uploads a file through gateway
2. gateway writes it into the thread-scoped uploads directory
3. convertible files may also be transformed to markdown
4. frontend submits the next run with file metadata in message
5. uploads middleware prepends an `<uploaded_files>` block to the human message
6. agent sees both newly uploaded and historical files

## Thread Data And Storage Layout

Relevant file:

- `backend/src/config/paths.py`

Current host-side data root is resolved by `Paths`.

Important layout:

- `{base_dir}/memory.json`
- `{base_dir}/USER.md`
- `{base_dir}/agents/{agent_name}/...`
- `{base_dir}/threads/{thread_id}/user-data/workspace`
- `{base_dir}/threads/{thread_id}/user-data/uploads`
- `{base_dir}/threads/{thread_id}/user-data/outputs`

Inside sandbox, thread data is exposed as:

- `/mnt/user-data/workspace`
- `/mnt/user-data/uploads`
- `/mnt/user-data/outputs`

This matters for frontend/backend collaboration because:

- upload metadata often surfaces as virtual paths
- artifact URLs are derived from these thread-scoped locations
- path handling bugs are usually boundary bugs, not UI bugs

## Real-Time Streaming Architecture

### What The Backend Already Streams Well

Workflow mode already emits custom events using `get_stream_writer()`.

Current important event families:

- `orchestration_mode_resolved`
- `workflow_stage_changed`
- `task_started`
- `task_running`
- `task_waiting_dependency`
- `task_help_requested`
- `task_resumed`
- `task_completed`
- `task_failed`
- `task_timed_out`

Primary emit points:

- `backend/src/agents/orchestration/selector.py`
- `backend/src/agents/planner/node.py`
- `backend/src/agents/router/semantic_router.py`
- `backend/src/agents/executor/executor.py`
- `backend/src/tools/builtins/task_tool.py`

### What The Backend Does Not Yet Stream Richly In Workflow Mode

This is the current limitation most relevant to frontend chat UX:

- workflow executor currently dispatches the domain agent through `ainvoke(...)`
- it emits status-level events around the task lifecycle
- it does not forward fine-grained intermediate domain-agent output as chat-like
  timeline events

By contrast, the legacy `task_tool` path polls background subagent state and can
forward new AI messages incrementally.

Practical meaning:

- current workflow backend is good at structured progress state
- current workflow backend is not yet a rich subtask narrative stream

## Clarification And Help-Request Model

There are two different interruption mechanisms:

### User Clarification

Relevant file:

- `backend/src/agents/middlewares/clarification_middleware.py`

When the model uses `ask_clarification`, the middleware converts that tool call
into a formatted tool message and ends the run. The frontend then resumes later
with the user answer.

### Workflow Dependency Resolution

Relevant files:

- `backend/src/agents/router/semantic_router.py`
- `backend/src/agents/executor/executor.py`

In workflow mode, a domain agent can emit `request_help`. The workflow router
then decides whether to:

- route to another helper agent
- resume a parent task when dependency results are ready
- interrupt and ask the user for clarification

This means some frontend-visible “waiting” states come from router logic, not
from the domain agent directly.

## Current Backend Change Hotspots

These are the main files to inspect before implementing cross-boundary changes.

### If The Requirement Is About Orchestration Choice

- `backend/src/agents/orchestration/selector.py`

Examples:

- force workflow mode
- add acknowledgement earlier
- expose orchestration reason

### If The Requirement Is About Planning / Stage Copy

- `backend/src/agents/planner/node.py`

Examples:

- planning stage wording
- summarizing stage wording
- task creation payload

### If The Requirement Is About Task Assignment / Blocking / Resume

- `backend/src/agents/router/semantic_router.py`

Examples:

- helper routing
- clarification on dependency failure
- task resume payload

### If The Requirement Is About Domain-Agent Execution

- `backend/src/agents/executor/executor.py`

Examples:

- task execution status
- forwarding richer subtask progress
- mapping final task output into thread-visible state

### If The Requirement Needs Persisted State

- `backend/src/agents/thread_state.py`

Examples:

- new task-level fields
- new workflow-level state
- new per-run metadata

### If The Requirement Needs Upload / Artifact Changes

- `backend/src/gateway/routers/uploads.py`
- `backend/src/gateway/routers/artifacts.py`
- `backend/src/config/paths.py`

### If The Requirement Needs Tool Availability Changes

- `backend/src/tools/tools.py`
- `backend/src/tools/builtins/`
- `backend/src/config/agents_config.py`

## Collaboration Guidance For Frontend-Facing Changes

If frontend needs a new backend field or event, backend should document all of
the following in the relevant feature file before implementation:

- emit point
- payload shape
- whether the field is in thread state, task state, or stream-only event
- whether it is stable across reconnect/hydration
- whether it should replace an optimistic frontend message
- dedup expectation if both task cards and timeline messages show similar info

## Current Summary For The Workflow Chat UX Topic

For the current workflow real-time chat discussion, the backend already supports:

- workflow-mode detection
- workflow stage events
- task lifecycle events
- clarification interruption
- task_pool hydration source for frontend cards

The backend does not yet fully support:

- a dedicated chat-facing workflow timeline event contract
- fine-grained forwarding of intermediate domain-agent output inside workflow
  mode

That boundary is likely where future frontend/backend collaboration will
concentrate.

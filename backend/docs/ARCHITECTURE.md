# Architecture Overview

This document provides a comprehensive overview of the DeerFlow backend architecture.

> **Last updated**: 2026-04-10

## System Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                              Client (Browser)                             │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                          Nginx (Port 2026)                               │
│                    Unified Reverse Proxy Entry Point                      │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  /api/langgraph/*  →  LangGraph Server (2024)                      │  │
│  │  /api/*            →  Gateway API (8001)                           │  │
│  │  /*                →  Frontend (3000)                               │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐
│   LangGraph Server  │ │    Gateway API      │ │     Frontend        │
│     (Port 2024)     │ │    (Port 8001)      │ │    (Port 3000)      │
│                     │ │                     │ │                     │
│  - Agent Runtime    │ │  - Models API       │ │  - Next.js App      │
│  - Dual-Mode Orch.  │ │  - MCP Config       │ │  - React UI         │
│  - SSE Streaming    │ │  - Skills Mgmt      │ │  - Chat Interface   │
│  - Multi-Agent      │ │  - Agent CRUD       │ │                     │
│    Workflow          │ │  - Runtime Threads  │ │                     │
│                     │ │  - Governance       │ │                     │
│                     │ │  - Interventions    │ │                     │
│                     │ │  - Personal (me)    │ │                     │
│                     │ │  - Promotions       │ │                     │
│                     │ │  - File Uploads     │ │                     │
│                     │ │  - Artifacts        │ │                     │
│                     │ │  - Memory           │ │                     │
└─────────────────────┘ └─────────────────────┘ └─────────────────────┘
          │                       │
          │     ┌─────────────────┘
          │     │
          ▼     ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         Shared Configuration                              │
│  ┌──────────────┐  ┌──────────────────┐  ┌────────────────────────────┐ │
│  │ config.yaml  │  │ extensions_      │  │  agents_config (3-layer)   │ │
│  │ - Models     │  │ config.json      │  │  - Platform agents         │ │
│  │ - Tools      │  │ - MCP Servers    │  │  - Tenant agents           │ │
│  │ - Sandbox    │  │ - Skills State   │  │  - Personal agents         │ │
│  │ - Memory     │  │                  │  │                            │ │
│  └──────────────┘  └──────────────────┘  └────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

## Component Details

### LangGraph Server

The LangGraph server is the core agent runtime, built on LangGraph. It supports **dual-mode orchestration**: a single lead-agent mode for open-ended tasks and a multi-agent workflow mode for structured multi-step tasks.

**Entry Point**: `src/agents/entry_graph.py` (orchestration graph) / `src/agents/lead_agent/agent.py:make_lead_agent` (single-agent factory)

**Key Responsibilities**:
- Dual-mode orchestration (leader / workflow)
- Multi-agent task decomposition, routing, and execution
- Agent creation and configuration with engine registry
- Thread state management (multi-agent aware)
- Middleware chain execution
- Tool execution orchestration
- SSE streaming for real-time responses

**Configuration**: `langgraph.json`

```json
{
  "agent": {
    "type": "agent",
    "path": "src.agents:make_lead_agent"
  }
}
```

### Gateway API

FastAPI application providing REST endpoints for non-agent operations.

**Entry Point**: `src/gateway/app.py`

**Middleware**: CORSMiddleware, OIDCAuthMiddleware (conditionally enabled)

**Built-in Endpoints**: `GET /health`, `GET /debug/metrics`

**Routers** (14 router modules):

| Router | Prefix | Key Endpoints |
|--------|--------|---------------|
| `models.py` | `/api/models` | List models, get model details |
| `mcp.py` | `/api/mcp` | Get/update MCP config |
| `memory.py` | `/api/memory` | Get/reload memory, config, status |
| `skills.py` | `/api/skills` | List/get/update/install skills |
| `artifacts.py` | `/api/threads/{id}/artifacts` | Serve artifact files |
| `uploads.py` | `/api/threads/{id}/uploads` | Upload/list/delete files |
| `agents.py` | `/api/agents` | CRUD agents, sync, user-profile |
| `runtime.py` | `/api/runtime` | Create threads, stream messages |
| `interventions.py` | `/api/threads/{id}/interventions` | Resolve interventions |
| `governance.py` | `/api/governance` | Queue, history, resolve governance items |
| `me.py` | `/api/me` | Personal agents, skills, MCP config |
| `promotions.py` | `/api/promotions`, `/api/me` | Admin/user promotion requests |
| `admin/router.py` | `/api/admin` | Admin lifecycle management |

### Agent Architecture — Dual-Mode Orchestration

The system supports two orchestration modes, selected dynamically by `orchestration_selector`:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          entry_graph.py                                   │
│                    Orchestration Entry Point                              │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                          orchestration_selector
                          (auto / leader / workflow)
                                     │
                  ┌──────────────────┴──────────────────┐
                  │                                      │
                  ▼                                      ▼
    ┌──────────────────────┐              ┌──────────────────────────────┐
    │    Leader Mode       │              │      Workflow Mode           │
    │  (Single Agent)      │              │  (Multi-Agent Pipeline)      │
    │                      │              │                              │
    │  make_lead_agent()   │              │  Planner → Router → Executor │
    │  Open-ended tasks    │              │  Structured multi-step tasks │
    └──────────────────────┘              └──────────────────────────────┘
```

#### Leader Mode (Single Agent)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           make_lead_agent(config)                        │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            Middleware Chain                              │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │  1. ThreadDataMiddleware   - Per-tenant workspace paths          │   │
│  │  2. UploadsMiddleware      - Process uploaded files              │   │
│  │  3. SandboxMiddleware      - Acquire sandbox environment        │   │
│  │  4. DanglingToolCallMiddleware - Repair missing ToolMessages    │   │
│  │  5. SummarizationMiddleware - Context reduction (if enabled)    │   │
│  │  6. TodoListMiddleware     - Task tracking (if plan_mode)       │   │
│  │  7. TitleMiddleware        - Auto-generate titles               │   │
│  │  8. MemoryMiddleware       - Queue async memory updates         │   │
│  │  9. ToolCallLimitMiddleware - Rate limit (if configured)        │   │
│  │ 10. ViewImageMiddleware    - Vision model support               │   │
│  │ 11. SubagentLimitMiddleware - Parallel task limits              │   │
│  │ 12. InterventionMiddleware - Safety gates (domain agents)       │   │
│  │ 13. HelpRequestMiddleware  - Dependency resolution (domain)     │   │
│  │ 14. ClarificationMiddleware - Handle clarifications (last)      │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              Agent Core                                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐   │
│  │  Model + Engine  │  │      Tools       │  │    System Prompt     │   │
│  │  (engine_registry│  │  (config + MCP   │  │  (with skills,       │   │
│  │   resolves mode) │  │   + builtin)     │  │   memory, runbook)   │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

**Engine Types**: `Default`, `ReAct`, `SOP`, `ReadOnly_Explorer`

#### Workflow Mode (Multi-Agent Pipeline)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       Workflow Mode Pipeline                              │
└─────────────────────────────────────────────────────────────────────────┘

  ┌─────────────┐      ┌─────────────┐      ┌─────────────────┐
  │   Planner   │ ───▶ │   Router    │ ───▶ │    Executor     │
  │  (node.py)  │      │ (semantic_  │      │  (executor.py)  │
  │             │      │  router.py) │      │                 │
  │ Decompose   │      │ Match tasks │      │ Run domain      │
  │ user goal   │      │ to domain   │      │ agents with     │
  │ into tasks  │      │ agents      │      │ MCP + context   │
  └──────┬──────┘      └──────┬──────┘      └────────┬────────┘
         │                    │                       │
         │              ◀─────┘                       │
         │    (re-plan if needed)              ◀──────┘
         │                              (loop back for remaining tasks)
```

**Planner** (`src/agents/planner/node.py`): LLM-based goal validation and task decomposition into domain-specific subtasks.

**Router** (`src/agents/router/semantic_router.py`): LLM-based semantic matching between tasks and domain agents. Supports clarification interrupts, help request escalation, and intervention cache validation.

**Executor** (`src/agents/executor/executor.py`): Creates domain agent instances with MCP warmup, context building (verified facts, persistent domain memory, dependency inputs). Classifies outcomes: `Complete`, `RequestDependency`, `RequestClarification`, `RequestIntervention`, `Fail`.

### Thread State

The `ThreadState` extends LangGraph's `AgentState` with fields for both single-agent and multi-agent modes:

```python
class ThreadState(AgentState):
    # Core
    messages: list[BaseMessage]

    # DeerFlow base extensions
    sandbox: dict                    # Sandbox environment info
    artifacts: list[str]             # Generated file paths (deduplicated)
    thread_data: dict                # {workspace, uploads, outputs} paths
    title: str | None                # Auto-generated conversation title
    todos: list[dict]                # Task tracking (plan mode)
    uploaded_files: list[dict]       # Uploaded file metadata
    viewed_images: dict              # Vision model image data

    # Orchestration
    requested_orchestration_mode: str  # "auto" | "leader" | "workflow"
    resolved_orchestration_mode: str   # "leader" | "workflow"
    orchestration_reason: str
    workflow_stage: str                # queued | planning | routing | executing | summarizing
    run_id: str

    # Multi-Agent Workflow
    planner_goal: str                # Original user goal
    task_pool: list[TaskStatus]      # Tasks with status, assignment, result
    route_count: int
    execution_state: str             # QUEUED | PLANNING_NEEDED | INTERRUPTED | DONE | ERROR
    verified_facts: dict             # Shared knowledge blackboard
    intervention_cache: dict         # Reusable intervention decisions

    # Verification (Phase 4)
    verification_feedback: str
    verification_retry_count: int
    workflow_verification_status: str  # pending | verified | failed
    workflow_verification_report: str
```

### Multi-Tenant Isolation

The system implements three-layer tenant/user/thread isolation:

```
Platform (global)
└── Tenant (tenant_id)
    ├── agents/              # Tenant-scoped agent configs
    ├── skills/              # Tenant-scoped skills
    ├── memory.json          # Tenant-scoped memory
    └── User (user_id)
        ├── agents/          # Personal agent configs
        ├── skills/          # Personal skills
        ├── memory.json      # User-scoped memory
        └── Thread (thread_id)
            └── user-data/
                ├── workspace/
                ├── uploads/
                └── outputs/
```

**Key Components**:
- `ThreadContext` (frozen dataclass): Carries validated `tenant_id`/`user_id`/`thread_id` through the system
- `Paths` singleton (`src/config/paths.py`): Resolves tenant-scoped file paths with traversal protection
- `LifecycleManager` (`src/admin/lifecycle_manager.py`): Orchestrates tenant decommission and user deletion
- Three-layer config loading for agents, skills, and extensions: `personal > tenant > platform`

### Sandbox System

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Sandbox Architecture                           │
└─────────────────────────────────────────────────────────────────────────┘

                      ┌─────────────────────────┐
                      │    SandboxProvider      │ (Abstract)
                      │  - acquire()            │
                      │  - get()                │
                      │  - release()            │
                      └────────────┬────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                                         │
              ▼                                         ▼
┌─────────────────────────┐              ┌─────────────────────────┐
│  LocalSandboxProvider   │              │  AioSandboxProvider     │
│  (src/sandbox/local.py) │              │  (src/community/)       │
│                         │              │                         │
│  - Singleton instance   │              │  - Docker-based         │
│  - Direct execution     │              │  - Isolated containers  │
│  - Development only     │              │  - Production use       │
│  - OIDC → RuntimeError  │              │                         │
└─────────────────────────┘              └─────────────────────────┘

                      ┌─────────────────────────┐
                      │        Sandbox          │ (Abstract)
                      │  - execute_command()    │
                      │  - read_file()          │
                      │  - write_file()         │
                      │  - list_dir()           │
                      └─────────────────────────┘
```

**Virtual Path Mapping** (multi-tenant aware):

| Virtual Path | Physical Path |
|-------------|---------------|
| `/mnt/user-data/workspace` | `backend/.deer-flow/tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/user-data/workspace` |
| `/mnt/user-data/uploads` | `backend/.deer-flow/tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/user-data/uploads` |
| `/mnt/user-data/outputs` | `backend/.deer-flow/tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/user-data/outputs` |
| `/mnt/skills` | `deer-flow/skills/` |

Sandbox state stored independently: `backend/.deer-flow/sandbox_state/{thread_id}/sandbox.json`

### Tool System

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Tool Sources                                  │
└─────────────────────────────────────────────────────────────────────────┘

┌──────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│   Built-in Tools     │ │  Configured Tools    │ │     MCP Tools        │
│  (src/tools/)        │ │  (config.yaml)       │ │  (scope-based)       │
├──────────────────────┤ ├──────────────────────┤ ├──────────────────────┤
│ Main agent:          │ │ - web_search         │ │ Global scope:        │
│ - present_files      │ │ - web_fetch          │ │ - github             │
│ - ask_clarification  │ │ - bash               │ │ - filesystem         │
│ - view_image         │ │ - read_file          │ │ Domain scope:        │
│                      │ │ - write_file         │ │ - contacts-server    │
│ Domain agents:       │ │ - str_replace        │ │ - meeting-server     │
│ - request_help       │ │ - ls                 │ │ Tenant/User scope:   │
│ - task_complete      │ │                      │ │ - personal MCP       │
│ - task_fail          │ │                      │ │                      │
└──────────────────────┘ └──────────────────────┘ └──────────────────────┘
           │                       │                       │
           └───────────────────────┴───────────────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │   get_available_tools() │
                      │   (src/tools/tools.py)  │
                      │   tenant/domain aware   │
                      └─────────────────────────┘
```

`get_available_tools()` parameters include `is_domain_agent`, `tenant_id`, `user_id` for scoped tool assembly.

### MCP Integration

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     MCP Runtime Manager                                  │
│                   (src/mcp/runtime_manager.py)                           │
│              Scope-Based Multi-Tenant MCP Lifecycle                       │
└─────────────────────────────────────────────────────────────────────────┘

Scope Keys:
  "global"                                  ← platform-wide
  "domain:{agent_name}"                     ← domain-agent scoped
  "tenant:{tenant_id}:global"               ← tenant-specific global
  "tenant:{tenant_id}:domain:{agent_name}"  ← tenant + agent
  "tenant:{tenant_id}:user:{user_id}:global"← user-level personal
  "run:{run_id}"                            ← per-run ephemeral (reserved)

┌─────────────────────────────────────────────────────────────────────────┐
│  McpRuntimeManager (process-level singleton)                             │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │ _ScopedMCPClient per scope key                                   │    │
│  │  - Independent tool cache                                        │    │
│  │  - Async lock-protected connection                               │    │
│  │  - Idle time monitoring + eviction                               │    │
│  └─────────────────────────────────────────────────────────────────┘    │
│                                                                          │
│  Binding Resolver (src/mcp/binding_resolver.py):                         │
│  AgentConfig.mcp_bindings → { use_global, domain, shared, ephemeral }   │
│  Resolves declarative server names to concrete configs                   │
│                                                                          │
│  Transports: stdio | SSE | HTTP                                          │
│  OAuth: client_credentials / refresh_token with auto-refresh             │
└─────────────────────────────────────────────────────────────────────────┘
```

### Model Factory

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Model Factory                                   │
│                     (src/models/factory.py)                              │
└─────────────────────────────────────────────────────────────────────────┘

config.yaml:
┌─────────────────────────────────────────────────────────────────────────┐
│ models:                                                                  │
│   - name: gpt-4                                                         │
│     display_name: GPT-4                                                 │
│     use: langchain_openai:ChatOpenAI                                    │
│     model: gpt-4                                                        │
│     api_key: $OPENAI_API_KEY                                            │
│     max_tokens: 4096                                                    │
│     supports_thinking: false                                            │
│     supports_vision: true                                               │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │   create_chat_model()   │
                      │  - name: str            │
                      │  - thinking_enabled     │
                      └────────────┬────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │   resolve_class()       │
                      │  (reflection system)    │
                      └────────────┬────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │   BaseChatModel         │
                      │  (LangChain instance)   │
                      └─────────────────────────┘
```

**Supported Providers**:
- OpenAI (`langchain_openai:ChatOpenAI`)
- Anthropic (`langchain_anthropic:ChatAnthropic`)
- DeepSeek (`langchain_deepseek:ChatDeepSeek`)
- Custom via LangChain integrations

### Skills System

Three-layer loading with tenant/user overrides:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          Skills System                                   │
│                       (src/skills/loader.py)                             │
│                    Three-Layer Merge Loading                              │
└─────────────────────────────────────────────────────────────────────────┘

  Platform layer:  skills/{public,custom}/
  Tenant layer:    .deer-flow/tenants/{tenant_id}/skills/     (overrides platform)
  User layer:      .deer-flow/tenants/{tid}/users/{uid}/skills/ (overrides tenant)

SKILL.md Format:
┌─────────────────────────────────────────────────────────────────────────┐
│ ---                                                                      │
│ name: PDF Processing                                                     │
│ description: Handle PDF documents efficiently                            │
│ license: MIT                                                            │
│ allowed-tools:                                                          │
│   - read_file                                                           │
│   - write_file                                                          │
│   - bash                                                                │
│ ---                                                                      │
│                                                                          │
│ # Skill Instructions                                                     │
│ Content injected into system prompt...                                   │
└─────────────────────────────────────────────────────────────────────────┘

Skill Metadata: name, description, license, category (public/custom),
                source (platform/tenant/personal), enabled state
```

### Platform Capability Standardization

The platform classifies all runtime capabilities into three tiers (`src/config/`):

| Tier | Count | Examples |
|------|-------|---------|
| **Platform Core** | 14 | engine_registry, workflow_runtime, intervention_protocol, governance_core, mcp_binding_runtime, middleware_chain, sandbox_workspace_runtime |
| **Capability Profile** | 4 | persistent_domain_memory, domain_runbook_support, domain_verifier_pack, governance_strict_mode |
| **Pilot / Experimental** | 2 | meeting_persistent_memory_hints, meeting_memory_writeback_boundary |

**Modules**: `platform_capabilities.py` (inventory), `onboarding.py` (agent minimum contract), `capability_profiles.py` (admission checks), `agents_config.py` (readiness validation).

### Governance & Intervention Systems

**Governance** (`src/agents/governance/`): Policy enforcement engine with ledger-based audit trail. Provides `engine.py`, `policy.py`, `ledger.py`, `audit_hooks.py`.

**Intervention** (`src/agents/intervention/`): Safety gates for domain agents. Decision caching with reuse limits, semantic fingerprinting for cache keys, help request structuring, and display projection for user-facing formatting.

**Hooks** (`src/agents/hooks/`): Pluggable lifecycle hooks with registry-based discovery. Supports `before_agent_build`, `before_skill_resolve`, `before_mcp_bind`, `after_agent_build` extension points.

### Request Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Request Flow (Leader Mode)                             │
└─────────────────────────────────────────────────────────────────────────┘

1. Client → Nginx → LangGraph Server (2024)
   POST /api/langgraph/threads/{thread_id}/runs

2. LangGraph Server — entry_graph.py
   a. orchestration_selector decides: leader or workflow
   b. If leader: execute middleware chain → single agent → stream SSE

┌─────────────────────────────────────────────────────────────────────────┐
│                    Request Flow (Workflow Mode)                           │
└─────────────────────────────────────────────────────────────────────────┘

1. Same entry via orchestration_selector → workflow mode

2. Planner: decompose user goal into task_pool
3. Router:  match each task to a domain agent
4. Executor: run domain agents with MCP warmup + context
5. Loop:    route remaining tasks or re-plan on failure
6. Stream:  SSE events for task_started, task_running, task_completed/failed
```

## Data Flow

### File Upload Flow

```
1. Client uploads file
   POST /api/threads/{thread_id}/uploads
   Content-Type: multipart/form-data

2. Gateway receives file
   - Validates file
   - Stores in tenant-isolated directory
   - If document: converts to Markdown via markitdown

3. Returns response with virtual_path and artifact_url

4. Next agent run
   - UploadsMiddleware lists files
   - Injects file list into messages
   - Agent can access via virtual_path
```

### Configuration Reload

```
1. Client updates MCP config (PUT /api/mcp/config)
2. Gateway writes extensions_config.json
3. MCP RuntimeManager detects mtime change
4. Affected scopes reload on next access
5. Next agent run uses updated tools
```

## Security Considerations

### Multi-Tenant Isolation

- Three-layer path isolation: tenant → user → thread
- `ThreadContext` validates tenant_id/user_id/thread_id at system boundary
- Path traversal protection in `Paths` singleton
- `ThreadRegistry` uses SQLite (WAL mode) for multi-process safety across gunicorn workers; legacy JSON registries auto-migrated on first access
- `LifecycleManager` ensures complete cleanup on tenant decommission / user deletion
- OIDCAuthMiddleware for token-based authentication (conditionally enabled)

### Sandbox Isolation

- Agent code executes within sandbox boundaries
- Local sandbox: Direct execution (development only); raises `RuntimeError` when OIDC is enabled to prevent production misuse
- Docker sandbox: Container isolation (production)
- `SandboxMiddleware` rejects sandbox acquisition when `thread_context` is missing and OIDC is enabled (no silent degradation)
- Sandbox state isolated from user data

### MCP Security

- Scope-based MCP isolation (global / domain / tenant / user)
- OAuth with automatic token refresh for HTTP/SSE transports
- Idle scope eviction for resource cleanup

### API Security

- Thread isolation: Each thread has tenant-scoped data directories
- File validation: Uploads checked for path safety
- Environment variable resolution: Secrets not stored in config

## Performance Considerations

### Caching

- MCP tools cached per scope with multi-layer mtime invalidation
- Configuration loaded once, reloaded on file change
- Skills parsed once at startup, cached in memory

### Streaming

- SSE used for real-time response streaming
- Workflow mode streams task lifecycle events (started/running/completed/failed)
- Reduces time to first token

### Context Management

- Summarization middleware reduces context when limits approached
- Configurable triggers: tokens, messages, or fraction
- Preserves recent messages while summarizing older ones
- Verified facts blackboard shared across workflow agents

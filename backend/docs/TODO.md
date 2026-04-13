# TODO List

> **Last updated**: 2026-04-10

## Completed Features

### Foundation (Phase 0)
- [x] Launch the sandbox only after the first file system or bash tool is called
- [x] Add Clarification Process for the whole process
- [x] Implement Context Summarization Mechanism to avoid context explosion
- [x] Integrate MCP (Model Context Protocol) for extensible tools
- [x] Add file upload support with automatic document conversion
- [x] Implement automatic thread title generation
- [x] Add Plan Mode with TodoList middleware
- [x] Add vision model support with ViewImageMiddleware
- [x] Skills system with SKILL.md format
- [x] Subagent delegation system (task tool with general-purpose / bash agents)
- [x] Memory system with LLM-based fact extraction and async updates

### Multi-Agent Architecture
- [x] Dual-mode orchestration: leader (single-agent) + workflow (multi-agent)
- [x] Orchestration selector with auto/leader/workflow mode decision
- [x] LLM-based task decomposition (Planner)
- [x] Semantic task-to-agent routing (Router)
- [x] Domain agent execution with outcome classification (Executor)
- [x] Engine registry with multiple engine types (Default, ReAct, SOP, ReadOnly_Explorer)
- [x] Verified facts blackboard for cross-agent knowledge sharing
- [x] Persistent domain memory with pluggable hint extractors
- [x] Build-time extension hooks (before/after agent build, skill resolve, MCP bind)

### Multi-Tenant Isolation
- [x] Three-layer tenant/user/thread path isolation
- [x] ThreadContext for validated identity propagation
- [x] Three-layer config loading for agents, skills, extensions (personal > tenant > platform)
- [x] OIDC authentication middleware (conditionally enabled)
- [x] Lifecycle manager for tenant decommission and user deletion

### Platform Capabilities
- [x] Platform capability standardization (14 Core + 4 Profile + 2 Pilot)
- [x] Agent onboarding contract and validation
- [x] Capability profile admission system
- [x] Scope-based MCP runtime manager with tenant/domain/user isolation
- [x] Governance engine with policy enforcement and audit ledger
- [x] Intervention system with decision caching and reuse limits
- [x] Runtime hook harness
- [x] Workflow verification (Phase 4)

### Gateway API Expansion
- [x] Agent CRUD endpoints (`/api/agents`)
- [x] Runtime thread management (`/api/runtime`)
- [x] Governance queue and resolution (`/api/governance`)
- [x] Intervention resolution (`/api/threads/{id}/interventions`)
- [x] Personal resource management (`/api/me` — agents, skills, MCP)
- [x] Promotion workflow (`/api/promotions`)
- [x] Admin lifecycle endpoints (`/api/admin`)

## Planned Features

- [ ] Sandbox resource pooling to reduce container count
- [ ] Rate limiting at API gateway level
- [ ] Observability dashboard integration (metrics and monitoring)
- [ ] Skill marketplace / remote skill installation
- [ ] Per-run ephemeral MCP scope (reserved in runtime manager)
- [ ] Additional engine types beyond ReAct/SOP/ReadOnly_Explorer

## Resolved Issues

- [x] Make sure that no duplicated files in `state.artifacts`
- [x] Long thinking but with empty content (answer inside thinking process)
- [x] Dangling tool calls after user interruption (DanglingToolCallMiddleware)
- [x] MCP tool cache invalidation across tenant layers

# Feature: Workflow Platform Capability Standardization

- Status: `implemented`
- Last aligned with code: `2026-03-27`
- Owner suggestion: `backend` + `test`
- Related area: workflow runtime, agent onboarding, capability rollout, governance, persistent domain memory, verifier, MCP runtime, middleware, sandbox
- Frontend impact: `none required in this phase`

## Goal

This work standardizes existing DeerFlow runtime capabilities so that new agents are onboarded through a lightweight, repeatable path instead of one-off internal wiring.

The three goals remain:

1. define which capabilities are already platform-grade and which are still pilot-only
2. define the minimum onboarding surface for a new agent
3. define explicit admission rules for advanced capabilities

## Current State

As of the current codebase, DeerFlow has a canonical capability inventory in `backend/src/config/platform_capabilities.py`.

Current totals:

- `20` total capabilities
- `14` Platform Core
- `4` Capability Profile
- `2` Pilot / Experimental

### Platform Core

Current Platform Core capabilities are:

1. `engine_registry`
2. `workflow_runtime`
3. `intervention_protocol`
4. `runtime_hook_harness`
5. `parallel_scheduler`
6. `governance_core`
7. `observability_base`
8. `verifier_runtime`
9. `output_guardrails`
10. `mcp_binding_runtime`
11. `subagent_delegation`
12. `middleware_chain`
13. `build_time_extension_hooks`
14. `sandbox_workspace_runtime`

Notable current-state clarifications:

- `intervention_protocol` now explicitly covers three paths:
  - risky-tool intervention
  - `ask_clarification`
  - `request_help`
- `build_time_extension_hooks` is formalized as Platform Core with a 4-phase contract
- `sandbox_workspace_runtime` is formalized as Platform Core with thread workspace, virtual-path translation, and sandbox lifecycle management

### Capability Profile

Current capability profiles are:

1. `persistent_domain_memory`
2. `domain_runbook_support`
3. `domain_verifier_pack`
4. `governance_strict_mode`

### Pilot / Experimental

Current pilot-only capabilities are:

1. `meeting_persistent_memory_hints`
2. `meeting_memory_writeback_boundary`

These are still not open directly to new agents.

## Design Principles

### 1. New Agent Onboarding Must Stay Lightweight

A new agent should mainly declare:

- who it is
- what domain it owns
- what business-facing capabilities it can see

It should not need to manually wire:

- scheduler behavior
- intervention / resume internals
- middleware ordering
- sandbox / workspace lifecycle
- verifier runtime hooks
- guardrail execution path

### 2. Advanced Capability Access Should Be Profile-Based

Advanced capability enablement should happen through:

- a named profile
- validator-backed admission
- platform-supplied default wiring
- explicit rollback semantics

It should not happen through scattered internal config knobs.

### 3. Standardization Must Be Checkable

Every advanced capability must have:

- a clear goal
- target domain characteristics
- required config
- required artifacts or docs
- required regressions
- rollback semantics

## Scope

### In Scope

1. define capability layering
2. define the minimum onboarding model for a new agent
3. define the first batch of Capability Profile admission contracts
4. expose a unified readiness validator
5. align documentation with the current code path

### Out Of Scope

1. adding new business agents
2. rolling advanced capabilities out to every domain
3. redesigning scheduler / governance / intervention runtime
4. frontend onboarding UI
5. Knowledge Harness / Improvement Harness

## Functional Requirements

### 1. Capability Layering Must Be Canonical

Each capability must belong to exactly one of:

- `Platform Core`
- `Capability Profile`
- `Pilot / Experimental`

### 2. New-Agent Onboarding Must Be Minimal

Current onboarding contract in code:

- Required:
  - `name`
  - `domain`
- Business Optional:
  - `description`
  - `system_prompt_file`
  - `available_skills`
  - `mcp_binding`
  - `tool_groups`
  - `engine_type`
  - `requested_orchestration_mode`
  - `model`
- Platform Internal:
  - `persistent_memory_enabled`
  - `persistent_runbook_file`
  - `hitl_keywords`
  - `intervention_policies`
  - `max_tool_calls`
  - `guardrail_structured_completion`
  - `guardrail_max_retries`
  - `guardrail_safe_default`

### 3. Capability Profiles Must Have Explicit Admission

Current admission contracts are implemented in `backend/src/config/capability_profiles.py`.

Each profile definition includes:

- goal
- why it is not Platform Core
- target domains
- required config
- required artifacts
- required tests
- rollback behavior

### 4. Readiness Must Be Verifiable from One Entry Point

Current unified entry:

- `validate_agent_platform_readiness(config)`

It aggregates:

1. onboarding validation
2. platform core wiring validation
3. all active profile admission checks

## Proposed Model, Now Implemented

### A. Platform Core

Definition:

- inherited by all agents
- platform-managed
- not part of the normal agent onboarding burden

### B. Capability Profile

Definition:

- optional advanced capability
- enabled through explicit admission
- accompanied by acceptance and rollback semantics

### C. Pilot / Experimental

Definition:

- valuable but not yet generalized
- must not be copied directly to a new agent

## Agent Minimum Onboarding Model

The normal onboarding path for a new agent is now:

1. identity
   - `name`
   - `domain`
2. prompt identity
   - `SOUL.md` or configured `system_prompt_file`
3. capability exposure
   - `available_skills`
   - `mcp_binding`
   - `tool_groups`
4. optional runtime selectors
   - `engine_type`
   - `requested_orchestration_mode`
   - `model`

Everything else should default to platform behavior unless the agent is explicitly applying for a profile.

## Capability Profile Admission Model

Current first-batch profiles:

### `persistent_domain_memory`

Requires:

- non-empty `domain`
- `persistent_memory_enabled=true`
- runbook file present
- persistence boundary documentation in runbook

### `domain_runbook_support`

Requires:

- configured runbook file or default `RUNBOOK.md`

### `domain_verifier_pack`

Requires:

- non-empty `domain`
- verifier family registration for that domain

### `governance_strict_mode`

Requires:

- non-empty `domain`
- stricter domain policy intent

## Deliverables

This standardization work now has these canonical docs:

1. main feature doc:
   - [workflow-platform-capability-standardization.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization.md)
2. backend checklist:
   - [workflow-platform-capability-standardization-backend-checklist.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization-backend-checklist.md)
3. test checklist:
   - [workflow-platform-capability-standardization-test-checklist.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization-test-checklist.md)
4. capability matrix:
   - [workflow-platform-capability-standardization-capability-matrix.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization-capability-matrix.md)
5. new onboarding guide:
   - [workflow-new-agent-onboarding-guide.md](E:/work/deer-flow/collaboration/features/workflow-new-agent-onboarding-guide.md)

## Current Code Deliverables

| Module | Path | Current Responsibility |
|---|---|---|
| capability inventory | `backend/src/config/platform_capabilities.py` | canonical tier inventory and export helpers |
| onboarding contract | `backend/src/config/onboarding.py` | classify `AgentConfig` fields and validate onboarding |
| profile admission | `backend/src/config/capability_profiles.py` | profile definitions, profile validators, platform-core wiring validation |
| readiness aggregation | `backend/src/config/agents_config.py` | aggregate onboarding + platform-core wiring + profile admission |
| build-time hooks | `backend/src/agents/lead_agent/engines/base.py` | `BuildContext` + `BuildTimeHooks` contract |
| sandbox/workspace runtime | `backend/src/config/paths.py`, `backend/src/sandbox/tools.py`, `backend/src/sandbox/middleware.py` | thread workspace, virtual-path translation, lazy sandbox lifecycle |

## Current Test Deliverables

| Test File | Current Count | Focus |
|---|---:|---|
| `backend/tests/test_platform_capabilities.py` | 19 | tier inventory, promoted Platform Core entries, matrix export |
| `backend/tests/test_onboarding.py` | 13 | onboarding field classification and internal-field warnings |
| `backend/tests/test_capability_profiles.py` | 30 | admission contracts and platform-core wiring validation |
| `backend/tests/test_platform_readiness.py` | 4 | unified readiness entry |
| `backend/tests/test_build_time_hooks.py` | 22 | build-time extension hook runtime contract |
| `backend/tests/test_sandbox_workspace_runtime.py` | 8 | sandbox/workspace runtime safety and lazy behavior |
| `backend/tests/test_hint_extractor_registry.py` | 8 | persistent-memory extractor registry |

## Acceptance Summary

1. Capability layering is now canonical in code.
2. New-agent onboarding is now explicitly lightweight.
3. Advanced capability enablement is now profile-based.
4. Runtime alignment for build hooks, sandbox/workspace runtime, and intervention/help/clarification is reflected in the standardization docs.
5. Current docs are sufficient to guide new-agent onboarding and readiness checks.

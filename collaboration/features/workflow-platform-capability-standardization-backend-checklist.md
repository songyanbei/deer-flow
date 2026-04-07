# Workflow Platform Capability Standardization Backend Checklist

- Audience: `backend`
- Status: `implemented`
- Last aligned with code: `2026-03-27`
- Goal: treat validated runtime capabilities as reusable platform standards instead of per-agent one-off wiring

## Backend Scope

### In Scope

1. Standardize platform capability layering.
2. Define the minimum onboarding contract for new agents.
3. Define admission contracts for the first batch of capability profiles.
4. Expose one readiness entry point that aggregates onboarding, platform-core wiring, and profile admission.

### Out Of Scope

1. Adding a new business-domain agent.
2. Rewriting scheduler, governance, or intervention runtime.
3. Rolling persistent memory out to every domain.
4. Frontend onboarding UI.

## Delivered

### 1. Platform Capability Inventory

- Code: `backend/src/config/platform_capabilities.py`
- Tests: `backend/tests/test_platform_capabilities.py` (`19` tests)

Current inventory in code:

- `20` total capabilities
- `14` Platform Core
- `4` Capability Profile
- `2` Pilot / Experimental

Current `Platform Core` list:

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

Current `Capability Profile` list:

1. `persistent_domain_memory`
2. `domain_runbook_support`
3. `domain_verifier_pack`
4. `governance_strict_mode`

Current `Pilot / Experimental` list:

1. `meeting_persistent_memory_hints`
2. `meeting_memory_writeback_boundary`

### 2. Agent Minimum Onboarding Contract

- Code: `backend/src/config/onboarding.py`
- Tests: `backend/tests/test_onboarding.py` (`13` tests)

Current `AgentConfig` onboarding classification:

- Required: `name`, `domain`
- Business Optional: `description`, `system_prompt_file`, `available_skills`, `mcp_binding`, `tool_groups`, `engine_type`, `requested_orchestration_mode`, `model`
- Platform Internal: `persistent_memory_enabled`, `persistent_runbook_file`, `hitl_keywords`, `intervention_policies`, `max_tool_calls`, `guardrail_structured_completion`, `guardrail_max_retries`, `guardrail_safe_default`

Current backend behavior:

- `validate_onboarding()` errors on missing `name` or `domain`
- `validate_onboarding()` warns on any platform-internal field carrying a non-default value
- onboarding field coverage is tied to `AgentConfig`, so new fields must be classified explicitly

### 3. Capability Profile Admission Contract

- Code: `backend/src/config/capability_profiles.py`
- Tests: `backend/tests/test_capability_profiles.py` (`30` tests)

Delivered admission model:

- each profile has a `ProfileDefinition`
- each profile has a validator
- each validator returns structured issues with severity
- `validate_all_active_profiles()` auto-detects active profiles
- `validate_platform_core_wiring()` validates platform-core config integrity

Current admission checks:

- `persistent_domain_memory`
  - requires non-empty `domain`
  - requires `persistent_memory_enabled=true`
  - requires runbook file
  - warns on incomplete runbook sections
  - warns when no domain hint extractor is registered
- `domain_runbook_support`
  - requires configured or default runbook file to exist
- `domain_verifier_pack`
  - requires non-empty `domain`
  - warns when verifier family is not registered
- `governance_strict_mode`
  - requires non-empty `domain`

### 4. Platform Core Wiring Validation

- Code: `backend/src/config/capability_profiles.py`
- Covered by:
  - `backend/tests/test_capability_profiles.py`
  - `backend/tests/test_platform_readiness.py`

Current checks in `validate_platform_core_wiring()`:

- output guardrail retry range
- output guardrail safe default legality
- MCP binding empty reference detection
- MCP binding `ephemeral` warning
- `max_tool_calls` bounds and unusually high values

### 5. Unified Readiness Entry

- Code: `backend/src/config/agents_config.py`
- Tests: `backend/tests/test_platform_readiness.py` (`4` tests)

Delivered entry point:

- `validate_agent_platform_readiness(config)`

Current aggregation:

1. onboarding
2. platform core wiring
3. all active capability profiles

### 6. Supporting Runtime Alignment

Key runtime behaviors now aligned with the standardization docs:

- `load_agent_runbook()` supports explicit runbook config and default `RUNBOOK.md`
- runbook injection works independently from persistent memory
- intervention protocol now explicitly includes:
  - risky-tool intervention
  - `ask_clarification`
  - `request_help`
- build-time hooks are formalized as Platform Core
- sandbox/workspace runtime is formalized as Platform Core

## Change Surface

### Core Standardization Modules

- `backend/src/config/platform_capabilities.py`
- `backend/src/config/onboarding.py`
- `backend/src/config/capability_profiles.py`
- `backend/src/config/agents_config.py`

### Related Runtime Modules

- `backend/src/agents/lead_agent/engines/base.py`
- `backend/src/agents/lead_agent/agent.py`
- `backend/src/agents/persistent_domain_memory.py`
- `backend/src/sandbox/middleware.py`
- `backend/src/sandbox/tools.py`
- `backend/src/config/paths.py`

### Relevant Test Files

- `backend/tests/test_platform_capabilities.py` — `19`
- `backend/tests/test_onboarding.py` — `13`
- `backend/tests/test_capability_profiles.py` — `30`
- `backend/tests/test_platform_readiness.py` — `4`
- `backend/tests/test_hint_extractor_registry.py` — `8`
- `backend/tests/test_persistent_domain_memory.py` — existing regression coverage
- `backend/tests/test_build_time_hooks.py` — `22`
- `backend/tests/test_sandbox_workspace_runtime.py` — `8`

## Backend Acceptance Criteria

1. Capability layering in backend matches the current runtime and is mutually exclusive.
2. New-agent onboarding is lightweight and centered on business identity plus exposure surface.
3. Platform-internal fields are not presented as the normal onboarding path.
4. Capability Profile admission is explicit, checkable, and rollback-aware.
5. Readiness can be assessed from one backend API/helper call.
6. Newly promoted Platform Core capabilities are reflected in the canonical inventory and supporting docs.

## Related Docs

- [workflow-platform-capability-standardization.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization.md)
- [workflow-platform-capability-standardization-test-checklist.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization-test-checklist.md)
- [workflow-platform-capability-standardization-capability-matrix.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization-capability-matrix.md)
- [workflow-new-agent-onboarding-guide.md](E:/work/deer-flow/collaboration/features/workflow-new-agent-onboarding-guide.md)

# Workflow Platform Capability Standardization Test Checklist

- Audience: `test`
- Status: `implemented`
- Last aligned with code: `2026-03-27`
- Goal: verify that capability layering, onboarding rules, admission contracts, and rollback semantics are executable and not just descriptive

## Test Scope

### In Scope

1. Capability classification consistency.
2. Agent minimum onboarding contract consistency.
3. Capability Profile admission behavior.
4. Unified readiness reporting.
5. Newly promoted Platform Core runtime coverage for build hooks, sandbox/workspace runtime, and intervention protocol alignment.

### Out Of Scope

1. New business-agent functional testing.
2. Frontend onboarding UI testing.
3. Knowledge Harness / Improvement Harness testing.
4. Re-validating the entire scheduler or governance stack beyond what the platform-standardization contract needs.

## Current Regression Coverage

### 1. Capability Classification Consistency

- File: `backend/tests/test_platform_capabilities.py`
- Current count: `19`

Covers:

1. total inventory lookup and tier integrity
2. Platform Core / Capability Profile / Pilot counts
3. immutability of `CapabilityDescriptor`
4. matrix export shape
5. promoted Platform Core entries such as:
   - `output_guardrails`
   - `mcp_binding_runtime`
   - `subagent_delegation`
   - `middleware_chain`
   - `build_time_extension_hooks`
   - `sandbox_workspace_runtime`
6. expanded `intervention_protocol` semantics including clarification and help escalation

### 2. Agent Minimum Onboarding Model

- File: `backend/tests/test_onboarding.py`
- Current count: `13`

Covers:

1. required fields are only `name` and `domain`
2. every `AgentConfig` field is classified
3. platform-internal field set is complete and current
4. missing `name` is an error
5. missing `domain` is an error
6. explicit non-default platform-internal fields trigger warnings, including:
   - `persistent_memory_enabled`
   - `intervention_policies`
   - `max_tool_calls`
   - guardrail fields

### 3. Capability Profile Admission

- File: `backend/tests/test_capability_profiles.py`
- Current count: `30`

Covers:

1. all four profiles are registered
2. profile definitions contain required metadata
3. `persistent_domain_memory` errors on missing domain / switch / runbook
4. `persistent_domain_memory` warns on incomplete runbook
5. `domain_runbook_support` validates explicit and default runbook paths
6. `domain_verifier_pack` checks domain presence and verifier registration status
7. `governance_strict_mode` checks domain presence
8. `validate_all_active_profiles()` auto-detects active profiles
9. `validate_platform_core_wiring()` validates guardrail, MCP binding, and tool-call safety configuration

### 4. Readiness Aggregation

- File: `backend/tests/test_platform_readiness.py`
- Current count: `4`

Covers:

1. plain domain agent readiness
2. sad path for invalid profile setup
3. valid profile setup
4. readiness matrix / export structure

### 5. Persistent Memory Supporting Coverage

- File: `backend/tests/test_hint_extractor_registry.py`
- Current count: `8`

Covers:

1. `DomainHintExtractor` registry contract
2. custom extractor registration
3. unknown-domain lookup behavior
4. meeting extractor auto-registration

Related runtime regression:

- `backend/tests/test_persistent_domain_memory.py`

### 6. Build-Time Extension Hooks

- File: `backend/tests/test_build_time_hooks.py`
- Current count: `22`

Covers:

1. `BuildContext` writable/read-only surface
2. default no-op behavior
3. 4-phase hook order
4. singleton lifecycle via `set_build_time_hooks()` / `get_build_time_hooks()`
5. skill injection
6. extra tool injection
7. bootstrap path behavior
8. integration with `make_lead_agent()`

### 7. Sandbox + Workspace Runtime

- Files:
  - `backend/tests/test_sandbox_workspace_runtime.py`
  - `backend/tests/test_uploads_middleware_core_logic.py`
  - `backend/tests/test_local_sandbox.py`
- Current dedicated count:
  - `test_sandbox_workspace_runtime.py` = `8`

Dedicated coverage includes:

1. virtual-path exact-prefix validation
2. path traversal rejection
3. multi-path command rewriting
4. lazy sandbox initialization reuse
5. local thread directory creation idempotency
6. non-local sandbox directory-creation skip behavior

### 8. Intervention / Clarification / Help Escalation Alignment

- Files:
  - `backend/tests/test_clarification_middleware.py`
  - `backend/tests/test_executor_intervention_normalization.py`
  - `backend/tests/test_multi_agent_core.py`
  - `backend/tests/test_multi_agent_graph.py`

Coverage focus:

1. clarification option normalization
2. `request_help` structured payload normalization
3. user-owned clarification metadata preservation
4. router / executor round-trip behavior
5. domain-agent tool exposure vs top-level tool exposure

## Recommended Validation View

Treat current testing as three layers:

### Platform Standard Regression

- capability tiering is accurate
- onboarding contract is stable
- docs match code-entry points

### Admission Regression

- each profile has explicit admission checks
- invalid setups fail loudly
- incomplete setups warn clearly

### Runtime Alignment Regression

- promoted Platform Core capabilities really behave as platform-managed runtime features
- no hidden dependency remains on per-agent manual wiring

## Current Acceptance Criteria

1. A tester can determine the current capability layer from code and canonical docs alone.
2. A tester can determine the minimum onboarding surface from code and canonical docs alone.
3. A tester can determine the admission and rollback rule for each profile from code and canonical docs alone.
4. Newly promoted Platform Core capabilities have at least one dedicated regression anchor.
5. The project no longer relies on undocumented pilot behavior being treated as platform-ready.

## Related Docs

- [workflow-platform-capability-standardization.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization.md)
- [workflow-platform-capability-standardization-backend-checklist.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization-backend-checklist.md)
- [workflow-platform-capability-standardization-capability-matrix.md](E:/work/deer-flow/collaboration/features/workflow-platform-capability-standardization-capability-matrix.md)
- [workflow-new-agent-onboarding-guide.md](E:/work/deer-flow/collaboration/features/workflow-new-agent-onboarding-guide.md)

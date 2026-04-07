# Workflow Platform Capability Standardization Capability Matrix

- Status: `implemented`
- Last aligned with code: `2026-03-27`
- Source of truth:
  - `backend/src/config/platform_capabilities.py`
  - `backend/src/config/onboarding.py`
  - `backend/src/config/capability_profiles.py`
  - `backend/src/config/agents_config.py`
- Related guide:
  - [workflow-new-agent-onboarding-guide.md](E:/work/deer-flow/collaboration/features/workflow-new-agent-onboarding-guide.md)

## Layer Definitions

### Platform Core

- All agents inherit by default.
- Wiring is platform-managed.
- New agent onboarding should not require users to understand the internals.

### Capability Profile

- Optional advanced capability.
- Enabled through profile-level admission instead of ad hoc internal knobs.
- Must have explicit admission, acceptance, and rollback semantics.

### Pilot / Experimental

- Validated in one domain or one implementation slice only.
- Not ready to open directly to new agents.
- Must not be generalized until a reusable contract exists.

## Capability Inventory

Current canonical inventory: `20` capabilities.

- `14` Platform Core
- `4` Capability Profile
- `2` Pilot / Experimental

### Platform Core

| Capability | Evidence | Open Strategy | Notes |
|---|---|---|---|
| `engine_registry` | `engine_type` + registry/builder are global | Default | New agents should not redefine builder wiring |
| `workflow_runtime` | planner / router / executor / task_pool are global | Default | Core workflow backbone |
| `intervention_protocol` | `InterventionMiddleware` + `ClarificationMiddleware` + `HelpRequestMiddleware` converge on `thread_state` / `workflow_resume` / gateway resolve | Default | Now explicitly covers risky-tool intervention, user clarification, and help escalation |
| `runtime_hook_harness` | interrupt / state-commit hooks unified by registry and runner | Default | Platform control-plane capability |
| `parallel_scheduler` | dependency-aware scheduler integrated into workflow runtime | Default | Not a single-agent pilot |
| `governance_core` | governance engine / ledger / middleware already exist | Default | Base governance path is platform behavior |
| `observability_base` | workflow structured observability exists | Default | Agents should not wire traces individually |
| `verifier_runtime` | verifier hooks integrated into runtime | Default | Domain-specific verifier packs stay in profiles |
| `output_guardrails` | structured completion guardrail + retry + safe default already wired in executor | Default | Guardrail internals should not be hand-configured per agent |
| `mcp_binding_runtime` | declarative binding + scope-aware runtime manager | Default | Agents declare references only; runtime owns lifecycle |
| `subagent_delegation` | subagent executor + concurrency control + task tool | Default | Delegation internals are platform-managed |
| `middleware_chain` | `_build_middlewares()` auto-composes ordered middleware pipeline | Default | New agents inherit correct chain automatically |
| `build_time_extension_hooks` | `BuildContext` + `BuildTimeHooks` 4-phase contract + singleton registration | Default | Writable hook surface limited to `available_skills`, `extra_tools`, `metadata` |
| `sandbox_workspace_runtime` | `Paths` singleton + `ThreadDataMiddleware` + `UploadsMiddleware` + `SandboxMiddleware` + virtual-path translation | Default | Thread workspace and sandbox lifecycle are platform-owned |

### Capability Profile

| Capability | Evidence | Open Strategy | Notes |
|---|---|---|---|
| `persistent_domain_memory` | generic toggle + prompt injection entry exist | Admission required | Entry is platformized; domain boundary still needs admission |
| `domain_runbook_support` | runbook loader + prompt injection exist | Admission required | Independent profile; does not require persistent memory |
| `domain_verifier_pack` | runtime verifier integration exists | Admission required | Domain verifier family must be explicitly registered |
| `governance_strict_mode` | base governance exists and can be tightened per domain | Admission required | Stricter than default governance path |

### Pilot / Experimental

| Capability | Evidence | Open Strategy | Notes |
|---|---|---|---|
| `meeting_persistent_memory_hints` | meeting hint extraction rules validated only in meeting domain | Do not generalize directly | Still domain-specific |
| `meeting_memory_writeback_boundary` | meeting write-back boundary validated only in meeting domain | Do not generalize directly | Needs reusable admission contract first |

## Agent Minimum Onboarding Matrix

Programmatic export:

- `src.config.onboarding.get_onboarding_matrix()`
- `src.config.onboarding.validate_onboarding(config)`

### Required

| Field | Should user provide? | Notes |
|---|---|---|
| `name` | Yes | Agent identity |
| `domain` | Yes | Router discovery and business ownership |

### Business Optional

| Field | Should user provide? | Notes |
|---|---|---|
| `description` | Optional | Human-readable description |
| `system_prompt_file` | Optional | Defaults to `SOUL.md` |
| `available_skills` | Optional | Skill allowlist; `None` means all enabled skills |
| `mcp_binding` | Optional | Declarative MCP references only |
| `tool_groups` | Optional | Tool exposure surface |
| `engine_type` | Optional | Runtime engine selector |
| `requested_orchestration_mode` | Optional | Runtime routing hint |
| `model` | Optional | Per-agent model override |

### Platform Internal

| Field | Should user provide? | Notes |
|---|---|---|
| `persistent_memory_enabled` | No | Managed via profile admission |
| `persistent_runbook_file` | No | Managed via profile admission |
| `hitl_keywords` | No | Platform governance wiring |
| `intervention_policies` | No | Platform governance wiring |
| `max_tool_calls` | No | Platform safety default |
| `guardrail_structured_completion` | No | Platform guardrail default |
| `guardrail_max_retries` | No | Platform guardrail default |
| `guardrail_safe_default` | No | Platform guardrail default |

## Capability Profile Admission Matrix

Programmatic entry points:

- `src.config.capability_profiles.validate_profile_admission(profile, config)`
- `src.config.capability_profiles.validate_all_active_profiles(config)`
- `src.config.agents_config.validate_agent_platform_readiness(config)`

| Profile | Activation Signal | Required Config | Required Artifacts | Rollback |
|---|---|---|---|---|
| `persistent_domain_memory` | `persistent_memory_enabled=true` | non-empty `domain` + enable switch | `RUNBOOK.md` and persistence boundary docs | disable switch and revert to thread-truth-only behavior |
| `domain_runbook_support` | explicit runbook file or default `RUNBOOK.md` exists | runbook config optional; file must exist | `RUNBOOK.md` or configured runbook file | remove runbook config/file and stop injecting runbook |
| `domain_verifier_pack` | verifier registry contains current `domain` | non-empty `domain` | verifier family registered + verifier contract docs | unregister or disable domain verifier pack |
| `governance_strict_mode` | `intervention_policies` or `hitl_keywords` present | non-empty `domain` | domain policy / guard boundary docs | remove stricter domain-scoped rules |

## Standardization Rule

Only capabilities that satisfy all of the following may graduate from `Pilot / Experimental` to `Capability Profile`:

1. Stable platform entry point exists.
2. Admission requirements are explicit and checkable.
3. Acceptance and regression expectations are explicit.
4. Rollback semantics are explicit.
5. The capability no longer depends on one domain's hard-coded implementation details.

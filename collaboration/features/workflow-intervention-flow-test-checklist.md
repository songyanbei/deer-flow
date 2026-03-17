# Workflow Intervention Flow Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature: `workflow-intervention-flow.md`

## 1. Backend Unit And Integration Coverage

- [ ] Verify implementation stays inside frozen Phase 1 scope:
  - workflow only
  - tool-originated intervention only
  - `button` and `input` actions only
- [ ] Add dedicated middleware tests for
  `backend/src/agents/middlewares/intervention_middleware.py`
- [ ] Cover trigger priority:
  - metadata hit
  - parser hit
  - keyword fallback
- [ ] Cover bypass behavior:
  - same fingerprint already resolved in the same run
  - stale resolution does not reopen or misapply
- [ ] Cover exclusion:
  - read-only tool should not enter intervention unless explicitly configured
- Done when:
  - intervention trigger logic is deterministic under test

## 2. Executor And Resume Regression Coverage

- [ ] Extend `backend/tests/test_multi_agent_core.py`
- [ ] Cover:
  - `intervention_required` -> `WAITING_INTERVENTION`
  - `task_waiting_intervention` event emission
  - action resolves to current-task resume
  - action resolves to current-task failure
  - same run resume does not reopen the same fingerprint
  - refresh-safe state still contains intervention payload
- [ ] Reserve-only verification for Phase 1:
  - `replan_from_resolution` may exist in protocol but must not be wired into
    runtime execution yet
- [ ] Re-run clarification/request_help regression paths to avoid cross-feature
  breakage
- Done when:
  - intervention does not regress current workflow interruption behavior

## 3. Config And API Coverage

- [ ] Add tests for `intervention_policies` parsing and persistence
- [ ] Verify backward compatibility with agents that still only use
  `hitl_keywords`
- [ ] If dedicated resolve endpoint is added, cover:
  - correct path:
    `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
  - valid resolution
  - duplicate submit
  - stale request id / fingerprint mismatch
  - invalid payload for chosen action
  - success response shape
- Done when:
  - configuration and action APIs are stable enough for handoff

## 4. Frontend Unit Coverage

- [ ] Extend `frontend/src/core/tasks/adapters.test.ts`
- [ ] Extend `frontend/src/core/threads/hooks.orchestration.test.tsx`
- [ ] Extend `frontend/src/components/workspace/workflow-progress.test.ts`
- [ ] Extend `frontend/src/components/workspace/workflow-footer-bar.test.tsx`
- [ ] Add/extend intervention card tests for:
  - schema-driven render
  - button action submission
  - input payload submission
  - context rendering
- [ ] Assert that unsupported Phase 1 action kinds do not silently render as
  fake working UI
- Done when:
  - intervention state can be rendered from both hydration and live stream
    paths

## 5. Browser Or Manual Validation

- [ ] Real workflow run reaches intervention before a risky side effect executes
- [ ] Footer and task panel both show intervention state
- [ ] Approval-style action resumes the same task/run and does not reopen the
  same prompt
- [ ] Override-style action can change the resumed instruction, such as
  switching to another meeting room
- [ ] Reject-style action prevents execution and produces a clear failed state
- [ ] Refresh during `WAITING_INTERVENTION` restores the same intervention card
- [ ] Retry or duplicate click does not create duplicated intervention cards
- Done when:
  - the main user journey works without inspecting raw backend logs

## 6. Suggested Validation Matrix

- [ ] Case A: explicit metadata risky tool
- [ ] Case B: parser-detected risky tool with no metadata
- [ ] Case C: keyword fallback only
- [ ] Case D: read-only tool
- [ ] Case E: same-run resolved fingerprint
- [ ] Case F: action mapped to fail
- [ ] Case G: action mapped to re-plan with custom payload
- [ ] Case H: refresh during pending intervention
- [ ] Case I: new run should not inherit old run intervention state

## 7. Final Sign-Off

- [ ] Backend checklist items required for the first vertical slice are closed
- [ ] Frontend checklist items required for the first vertical slice are closed
- [ ] Feature doc contract section matches final implementation
- [ ] Any unresolved framework/API gaps are written back to:
  - `collaboration/features/workflow-intervention-flow.md`
  - or `collaboration/handoffs/*.md` if cross-team follow-up is required

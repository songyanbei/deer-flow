# Workflow Entry Feedback And Runtime Polish Backend Checklist

- Status: `in_progress`
- Owner: `backend`
- Related feature: `workflow-entry-feedback-and-runtime-polish.md`

## 1. Runtime Entry Repair

- [x] Keep the enqueue-time hook at run creation time instead of moving queue
  staging into planner
- [x] Remove the old `conn.store`-dependent runtime staging path as the source
  of truth
- [x] Patch the actual run-creation entrypoints that may already hold a bound
  `create_valid_run` reference
- Done when:
  - backend repair runs before worker startup
  - no page-only state source is introduced

## 2. Authoritative Thread Persistence

- [x] Read the current thread through existing thread ops before writing queue
  state
- [x] Persist the same enqueue-time `queued` state into checkpoint history, not
  only the thread-row mirror
- [x] Persist enqueue-time `queued` through `Threads.set_status`
- [x] Reuse existing workflow authority fields only:
  - `run_id`
  - `workflow_stage`
  - `workflow_stage_detail`
  - `workflow_stage_updated_at`
- [x] Reset only run-bound workflow shell fields:
  - `task_pool`
  - `verified_facts`
  - `final_result`
  - `route_count`
- Done when:
  - refresh can hydrate `queued` from reconnect-safe history/thread state
  - messages/history are preserved

## 3. Selector Guardrail

- [x] Preserve the current selector rule that same-run authoritative `queued`
  must not regress back to `acknowledged`
- [x] Keep selector responsibility limited to mode decision and early ack
- Done when:
  - runtime-staged `queued` and graph stages share the same `run_id`
  - selector does not overwrite a pre-staged queued shell

## 4. Tests

- [x] Replace fake `conn.store` direct-write assertions with helper-level
  persistence assertions
- [x] Keep selector regression coverage for same-run queue preservation
- [x] Cover:
  - backlog present -> enqueue-time `queued`
  - no backlog -> no enqueue-time `queued`
  - persisted queue state keeps the existing custom event contract
- Validation:
  - `backend/tests/test_runtime_queue_stage.py`
  - `backend/tests/test_multi_agent_core.py`

## 5. Live Validation

- [ ] Deploy/restart the live backend with the repaired runtime patch
- [ ] Re-run the real two-run busy-worker workflow scenario
- [ ] Confirm the second run shows `queued` before `Starting background run`
- [ ] Confirm queued survives refresh and advances to planning with the same
  `run_id`
- Done when:
  - frontend can verify the queued shell in the real browser flow
  - handoff status can move from `open` to `closed`

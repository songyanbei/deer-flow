# Frontend To Backend Handoffs

Use this file when frontend is blocked by missing backend behavior, payloads, or
contracts.

## Entry Template

```md
## [open] Short title
- Date:
- Related feature:
- Blocking area:
- Current frontend assumption:
- What is missing from backend:
- Needed response:
- Suggested payload or API:
- Notes:
```

## Open Items

## [closed] Refresh during true queued window now hydrates queued shell
- Date: 2026-03-14
- Related feature: `features/workflow-entry-feedback-and-runtime-polish.md`
- Blocking area: live `queued` workflow shell recovery during refresh/reconnect
- Current frontend assumption:
  when a workflow run is enqueued behind another active run, backend will both
  render a visible `queued` stage before planner start and persist enough
  thread state for refresh/reconnect to recover that same shell.
- What was missing from backend:
  refresh/reconnect during the queued window previously dropped the shell back
  to a bare `Workflow` placeholder instead of hydrating queued from
  thread state/history.
- Backend response:
  backend now persists enqueue-time `queued` into reconnect-safe checkpoint
  history as well as the thread-row mirror, so the same queued shell survives
  refresh before worker startup.
- Suggested payload or API:
  keep the current contract; no new frontend-only fields are required if
  backend truly emits at enqueue time:
  `run_id`, `workflow_stage=queued`, `workflow_stage_detail`,
  `workflow_stage_updated_at`.
- Validation standard:
  - reproduce with two real `Workflow` submissions while a single worker is
    busy
  - second run must show `workflow_stage=queued` with visible queued shell copy
    before its `Starting background run` moment
  - second run must keep the same `run_id` when transitioning
    `queued -> planning`
  - refresh during that queued window must still hydrate the queued shell from
    thread state
- Notes:
  real browser verification on 2026-03-13 first reproduced a genuine backend
  queue:
  - second run created at `2026-03-13T08:57:50Z`
  - second background run started at `2026-03-13T08:59:10Z`
  - backend logged `run_queue_ms=79602`
  - frontend did not show a visible queued shell during that wait window
  backend documentation later marked this as repaired, but frontend reran the
  live scenario on 2026-03-14 after the stack was restarted and initially
  still could not verify the fix:
  - first run thread:
    - `0ac6af7a-53f8-42cc-8187-537a8620fac6`
  - second run thread:
    - `315680a3-fbce-49dc-b52f-ebe02cad27ab`
  - second run page behavior:
    - around `2.2s` after submit, the real thread page opened and showed only
      the user request plus `Workflow`, with no visible `acknowledged` or
      `queued` shell copy
    - around `60.2s`, the first visible workflow shell copy was already
      `planning`:
      `正在理解你的需求，规划执行步骤…`
    - the page never showed:
      `已提交，正在排队启动...`
  - because the page never entered a visible queued shell, frontend could not
    complete the required refresh-during-queued verification either
  backend reworked the runtime path again, and frontend reran the live
  scenario after another service restart on 2026-03-14:
  - first run thread:
    - `c847675a-000c-4a97-89da-78fd9f66b16c`
  - second run thread:
    - `bd65c991-c735-446d-8b77-c05f0fd1f820`
  - second run page behavior:
    - around `2.2s` after submit, the page showed the expected queued copy:
      `已提交，正在排队启动...`
    - frontend refreshed immediately during that queued window
    - after refresh, the page showed only `Workflow` and did not restore the
      queued copy
    - around `56.9s`, the same run later advanced into `planning`
  frontend status:
  - current frontend merge/render logic already supports authoritative
    `queued` for the same `run_id`
  - no frontend contract rename is needed
  - live enqueue-time `queued` visibility is now verified
  backend confirmed the previous root cause on 2026-03-14:
  - the earlier repair matched a test-double style `conn.store` path and did
    not reliably hit the live runtime persistence chain
  - patching only `langgraph_api.models.run.create_valid_run` was not enough
    to guarantee interception of already-bound run-creation entrypoints
  backend repair status after the 2026-03-14 rework:
  - enqueue-time `queued` now targets the existing `Threads.get ->
    Threads.set_status` persistence path
  - the same `workflow_stage_changed` payload contract is preserved
  - frontend had verified live queued visibility
  backend follow-up after the latest 2026-03-14 investigation:
  - frontend refresh was confirmed to hydrate from `threads.getHistory(limit=1)`
    rather than from the thread-row mirror alone
  - backend repaired enqueue-time queue staging again so the same queued shell
    now writes into checkpoint history before worker startup, while preserving
    the existing thread-row mirror and `workflow_stage_changed` payload
  - backend regression result for the repaired helper path is now:
    `56 passed in 27.87s`
  frontend close-out after the redeployed backend was re-tested in a real
  browser session:
  - second run thread `44d2a074-ad29-4522-a554-9adcf5b12110` visibly showed
    `已提交，正在排队启动...` around `2.1s` after submit
  - immediate refresh during that queued window preserved the same queued copy
  - a follow-up live run on refreshed state later advanced into `planning`
  - frontend therefore considers this blocker resolved and closed

## [open] Workflow timeline event contract
- Date: 2026-03-13
- Related feature: `features/workflow-realtime-chat.md`
- Blocking area: main chat timeline rendering in workflow mode
- Current frontend assumption:
  frontend can consume custom stream events, but there is no dedicated
  chat-facing workflow timeline event yet.
- What is missing from backend:
  a stable event type and payload for timeline messages, plus replacement/dedup
  rules.
- Needed response:
  confirm whether backend will emit a new event, and from which nodes.
- Suggested payload or API:
  `workflow_chat_message` with `run_id`, `message_id`, `phase`, `text`,
  optional `task_id`, optional `agent_name`, `replace`, `created_at`.
- Notes:
  status-only task events are enough for cards, but not ideal for main timeline
  text generation.

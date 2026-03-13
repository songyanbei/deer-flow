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

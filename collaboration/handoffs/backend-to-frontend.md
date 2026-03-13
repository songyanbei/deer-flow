# Backend To Frontend Handoffs

Use this file when backend needs frontend rendering rules, UX decisions, or
clarification on how a payload will be displayed.

## Entry Template

```md
## [open] Short title
- Date:
- Related feature:
- Blocking area:
- Backend question:
- Frontend decision needed:
- Suggested UI behavior:
- Notes:
```

## Open Items

## [open] Workflow timeline duplication rule
- Date: 2026-03-13
- Related feature: `features/workflow-realtime-chat.md`
- Blocking area: workflow timeline projection
- Backend question:
  when a workflow event is shown in the main timeline, should the frontend also
  keep showing the same task detail in the workflow card at full verbosity?
- Frontend decision needed:
  define the duplication strategy between main timeline and task panel.
- Suggested UI behavior:
  main timeline shows only high-signal summaries; task panel keeps full task
  detail.
- Notes:
  this affects how verbose backend message text should be.

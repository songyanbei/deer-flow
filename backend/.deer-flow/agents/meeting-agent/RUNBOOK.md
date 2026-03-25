# Meeting Persistent Runbook

Use persistent domain memory only as advisory hints that improve repeat booking workflows.

## Allowed Reuse

- preferred booking city or city fallback order
- recurring organizer name or department hints
- preferred room characteristics, booking windows, and routine meeting patterns
- stable user preferences that reduce repeated clarification

## Must Stay In Current Thread Truth Sources

- current organizer `openId`
- room availability and conflict state
- meeting IDs, attendee lists, and final booking outputs
- any fact that is already present in current `verified_facts` or resolved dependency inputs

## Conflict Resolution Order

1. current user instruction
2. resolved dependency inputs
3. current-thread `verified_facts`
4. persistent domain memory

If persistent memory conflicts with current-thread truth, ignore the memory and continue with current facts.

## Safety Rules

- Never use persistent memory to skip mandatory verifier gates.
- Never treat remembered hints as proof that a booking can succeed.
- If organizer identity or `openId` is required, still resolve it through the current workflow.
- Use memory to reduce repeated clarification, not to bypass domain boundaries.

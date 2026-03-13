# Frontend/Backend Collaboration

This directory is for frontend/backend collaboration only. It is intentionally
separate from `docs/`, which remains project/product documentation.

## Goals

- Keep frontend and backend ownership clear.
- Record feature contracts before both sides start coding.
- Let each side communicate through documents instead of editing the other
  side's code.
- Preserve short-lived blockers and missing capabilities without burying them
  in chat history.

## Suggested Ownership

- Frontend only changes files under `frontend/`.
- Backend only changes files under `backend/`.
- Shared decisions are recorded here first, then implemented on each side.

## Directory Layout

- `architecture/`
  - Stable architecture notes for each side.
- `features/`
  - One shared document per feature or requirement.
- `handoffs/`
  - Short request logs when one side is blocked by the other.
- `templates/`
  - Reusable templates for new collaboration items.

## How To Use

1. Before coding a cross-boundary feature, create or update one file in
   `features/`.
2. Write the goal, current behavior, contract, backend changes, frontend
   changes, and acceptance criteria in the same file.
3. If frontend finds a missing backend capability, append a short item to
   `handoffs/frontend-to-backend.md`.
4. If backend needs a frontend decision or rendering behavior clarified, append
   a short item to `handoffs/backend-to-frontend.md`.
5. Once the issue is resolved, mark the handoff item as closed and link back to
   the feature file.

## Status Labels

- `draft`: still being clarified
- `ready`: both sides can start implementation
- `blocked`: waiting on another side
- `in_progress`: at least one side is implementing
- `done`: implemented and accepted

## Recommended Rule

For cross-boundary work, prefer:

- Separate architecture docs by side
- One shared feature doc per requirement
- Separate handoff logs by direction

Do not keep separate frontend and backend copies of the same feature spec. That
usually drifts.

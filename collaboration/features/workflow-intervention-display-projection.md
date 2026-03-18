# Feature: Workflow Intervention Display Projection

- Status: `draft`
- Owner suggestion: `backend` for display projection payload and fallback strategy, `frontend` for rendering and interaction polish, `test` for schema/fallback/regression validation
- Related area: workflow mode, intervention card UX, user-readable action confirmation

## Goal

Turn the current developer-facing intervention card into a user-facing
experience.

The intervention mechanism itself is already available. This feature focuses on
how intervention content is projected into a readable, scenario-aware display
model so users see meaningful business information instead of raw tool names,
IDs, timestamps, and JSON arguments.

Primary objectives:

1. intervention UI should default to user-readable content
2. raw internal protocol fields should not be the primary visible surface
3. the display mechanism must remain generic and reusable across agents/tools
4. fallback behavior must still work even when no scenario-specific projection
   exists

## Dependency

This feature is built on top of the already-completed intervention protocol and
Phase 1 interaction flow.

It should not redesign:

- intervention state machine
- intervention resolve endpoint
- action schema or resolution behavior

## Why This Needs Frontend/Backend Collaboration

Backend must provide a stable display projection payload and fallback rules.
Frontend must render that payload consistently and gracefully degrade when only
fallback content is available.

If backend alone implements this, it may ship fields that are still awkward in
UI. If frontend alone implements this, it will end up reverse-engineering tool
payloads and hard-coding business logic into rendering code.

## Current Behavior

### Backend

- intervention payload currently exposes internal execution details such as:
  - `source_agent`
  - `tool_name`
  - raw args
  - internal IDs
  - timestamps
- this is useful for debugging but not suitable as the primary user-facing
  surface

### Frontend

- current intervention card can show the mechanism and actions
- current presentation is still close to raw protocol/debug data
- users are required to infer business intent from tool names and raw JSON

## Design Principle

Do not solve this by adding more frontend `if tool_name === ...` branches.

The correct model is:

1. framework keeps generic intervention protocol
2. backend produces a generic display projection structure
3. frontend renders one reusable display-oriented card
4. scenario-specific differences are expressed as data, not bespoke UI logic

## Contract To Confirm First

- Event/API:
  - intervention payload adds a stable `display` section
- Payload shape:
  - `display` contains user-readable title, summary, sections, and action copy
- Persistence:
  - `display` travels with `intervention_request`
- Error behavior:
  - if specialized projection is unavailable, backend must return a readable
    fallback display
- Dedup/replacement:
  - display projection does not change intervention identity or fingerprint

## Frozen Decisions For This Feature

### 1. Frontend Renders `display` First

Primary rendering must come from:

- `intervention_request.display`

Frontend must not use raw `tool_name`, raw args, internal IDs, or timestamps as
the default visible card content.

### 2. Raw Internal Details Are Debug-Only

The following content must not be part of the primary user-facing display:

1. `source_agent`
2. `tool_name`
3. raw JSON args
4. internal IDs like `openId`, `roomId`
5. epoch timestamps

If product still needs technical visibility, raw details may exist only in a
collapsed debug/details area and must not be the default expanded content.

### 3. Display Projection Uses One Generic Schema

Different scenarios may provide different values, but they must map into the
same display schema. We are not introducing per-scenario frontend component
types in this iteration.

## Proposed Display Schema

```ts
type InterventionDisplay = {
  title: string;
  summary?: string;
  sections?: Array<{
    title?: string;
    items: Array<{
      label: string;
      value: string;
    }>;
  }>;
  risk_tip?: string;
  primary_action_label?: string;
  secondary_action_label?: string;
  respond_action_label?: string;
  respond_placeholder?: string;
  debug?: {
    source_agent?: string;
    tool_name?: string;
    raw_args?: Record<string, unknown>;
  };
};
```

`InterventionRequest` should gain:

```ts
display?: InterventionDisplay;
```

## Projection Strategy

Projection should be layered:

1. scenario-specific projection
   - for high-value flows such as meeting booking or message sending
2. operation-type projection
   - for reusable categories such as:
     - create resource
     - send notification
     - change permission
3. generic fallback projection
   - if no specialized mapping exists, backend still returns readable summary

This keeps the framework generic while still allowing high-quality UX in key
scenarios.

## Phase 1 Scope

Phase 1 for this display feature includes:

1. one generic `display` schema
2. backend fallback projection
3. at least one polished scenario projection for the currently validated
   meeting-room booking flow
4. frontend `InterventionCard` rendering based on `display`
5. optional collapsed debug area for internal details

Phase 1 does **not** require:

1. every tool or every agent to have a handcrafted projection
2. multiple frontend card component families
3. localization of every backend-generated display phrase beyond current app
   conventions

## Backend Changes

1. extend `InterventionRequest` with `display`
2. implement projection builder/adapters on backend
3. provide fallback readable display for unknown scenarios
4. provide polished projection for the meeting-booking intervention flow
5. normalize common values before rendering:
   - timestamp -> human-readable time
   - roomId -> display room name where possible
   - ID fields -> hidden from primary view

See:

- `workflow-intervention-display-projection-backend-checklist.md`

## Frontend Changes

1. update `InterventionCard` to prioritize `display`
2. render sections/summary/action copy from display payload
3. keep raw details out of primary content
4. add collapsed debug/details rendering only if needed
5. preserve generic action submission flow

See:

- `workflow-intervention-display-projection-frontend-checklist.md`

## Test Changes

1. backend projection tests
2. frontend rendering tests for projected display and fallback display
3. manual validation in the real meeting-booking scenario

See:

- `workflow-intervention-display-projection-test-checklist.md`

## Risks

1. if frontend keeps reading raw protocol fields directly, display quality will
   regress as new scenarios are added
2. if backend only ships hand-crafted per-scenario projections without fallback,
   unknown scenarios will still expose raw JSON
3. if display strings are generated without normalization, users will still see
   IDs and timestamps disguised as “friendly” content

## Acceptance Criteria

1. the meeting-booking intervention card no longer primarily shows tool names
   or raw args
2. users can understand what will happen, when, and with what target from the
   card alone
3. frontend can render unknown intervention scenarios through fallback display
   without exposing raw JSON as the default surface
4. intervention identity, fingerprint, and resolution behavior remain unchanged
5. the new display layer does not break the existing resolve flow

## Open Questions

1. whether the collapsed debug/details area should be shown to all users or
   only in internal/debug environments

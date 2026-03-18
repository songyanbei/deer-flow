# Workflow Intervention Display Projection Backend Checklist

- Status: `implemented`
- Owner: `backend`
- Related feature: `workflow-intervention-display-projection.md`

## 1. Display Payload Contract

- [x] Extend intervention request protocol with `display`
- [x] Keep existing intervention identity fields unchanged:
  - `request_id`
  - `fingerprint`
  - `action_schema`
  - `resolution_behavior`
- [x] Ensure `display` is additive and does not replace execution-critical
  protocol fields
- Done when:
  - frontend can render user-facing content from `display` without losing
    runtime semantics

## 2. Projection Builder

- [x] Add a backend projection builder/adapter layer for intervention display
- [x] Implement layered projection:
  - scenario-specific projection
  - operation-type projection
  - generic fallback projection
- [x] Keep projection logic outside framework core runtime state machine
- Done when:
  - new scenarios can add better display content without requiring intervention
    protocol redesign

## 3. Fallback Readability Rules

- [x] Define fallback rules that produce readable content even without a
  specialized scenario adapter
- [x] Hide or transform internal fields from primary display:
  - internal IDs
  - raw timestamps
  - raw tool names
  - raw agent names
- [x] Normalize values where possible:
  - epoch -> readable time
  - enum/code -> readable label
- Done when:
  - unknown scenarios still look user-readable by default

## 4. Meeting Booking Projection

- [x] Implement a polished projection for the current meeting-booking flow
- [x] Display should include user-readable business fields such as:
  - meeting topic
  - meeting room name
  - meeting time
  - organizer
  - reminder summary
- [x] Primary action copy should be user-facing, not tool-facing
- Done when:
  - current validated scenario no longer exposes raw `meeting_createMeeting`
    semantics in the main card

## 5. Debug Details Strategy

- [x] Decide what raw/internal details remain available for debugging
- [x] If raw details are kept, put them under `display.debug`
- [x] Ensure debug details are optional and not required by frontend primary
  render path
- Done when:
  - developers still have access to debugging data without polluting the user
    experience

## 6. Backend Validation

- [x] Add tests for:
  - scenario-specific projection
  - operation-type projection
  - fallback projection
  - timestamp/ID normalization
- [x] Ensure no regression in existing intervention resolve flow
- Validation:
  - intervention display projection tests
  - existing intervention middleware/executor tests still pass
  - 392 tests passed, 0 new failures (3 pre-existing unrelated failures)

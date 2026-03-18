# Workflow Intervention Display Projection Frontend Checklist

- Status: `draft`
- Owner: `frontend`
- Related feature: `workflow-intervention-display-projection.md`

## 1. Intervention Card Rendering

- [ ] Update `InterventionCard` to render `intervention_request.display`
  first
- [ ] Stop using raw protocol fields as the primary visible card content
- [ ] Keep rendering generic so one component can support all scenarios
- Done when:
  - the main intervention card is understandable without reading technical
    fields

## 2. Display Sections And Summary

- [ ] Render:
  - `display.title`
  - `display.summary`
  - `display.sections`
  - `display.risk_tip`
- [ ] Ensure section layout works for:
  - short confirmation cards
  - detail-rich cards like meeting booking
- Done when:
  - cards present business information cleanly and consistently

## 3. Action Copy

- [ ] Prefer action labels from `display` where provided
- [ ] Keep fallback action labels when display-specific labels are absent
- [ ] Support:
  - primary action label
  - secondary/reject action label
  - respond action label
  - respond placeholder
- Done when:
  - actions read like user decisions, not system operations

## 4. Debug Details

- [ ] Do not show raw JSON/tool args by default
- [ ] If debug/details UI is kept, make it collapsed by default
- [ ] Ensure debug visibility does not block normal user flow
- Done when:
  - internal fields are no longer the first thing users see

## 5. Visual Polish

- [ ] Make meeting-booking intervention visually read as a confirmation result,
  not a tool call
- [ ] Use user-facing labels for time, room, subject, and reminders
- [ ] Ensure mobile and desktop readability both hold up
- Done when:
  - the intervention card looks like a product UI, not a debug panel

## 6. Frontend Validation

- [ ] Add tests for:
  - display-first rendering
  - fallback rendering
  - collapsed debug/details mode
  - action copy rendering
- Validation:
  - intervention card related tests
  - workflow footer/task surface regression tests

# Workflow Intervention Display Projection Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature: `workflow-intervention-display-projection.md`

## 1. Backend Projection Validation

- [ ] Verify scenario-specific meeting-booking projection produces readable
  display payload
- [ ] Verify fallback projection works for scenarios without a custom adapter
- [ ] Verify raw internal fields are not required for primary display
- [ ] Verify timestamps and IDs are normalized where expected
- Done when:
  - backend can consistently emit user-readable display payloads

## 2. Frontend Rendering Validation

- [ ] Verify intervention card renders `display.title/summary/sections`
- [ ] Verify action labels use display copy when present
- [ ] Verify raw JSON is not shown by default
- [ ] Verify debug/details area is collapsed by default if present
- Done when:
  - frontend shows product-facing content instead of developer-facing payloads

## 3. End-To-End Or Manual Validation

- [ ] Validate the real meeting-booking scenario end to end
- [ ] Confirm users see:
  - what will happen
  - when it will happen
  - which room/resource is involved
  - what choices they have
- [ ] Confirm users do not primarily see:
  - tool names
  - agent names
  - raw args
  - internal IDs
- Done when:
  - the actual intervention experience is understandable without technical
    knowledge

## 4. Regression Guardrails

- [ ] Confirm intervention resolve flow still works after adding display
  projection
- [ ] Confirm fallback display does not break unknown scenarios
- [ ] Confirm existing workflow intervention tests still pass
- Done when:
  - UX improvement does not regress core functionality

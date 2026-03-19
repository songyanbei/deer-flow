# Feature: Workflow Intervention Card Component Refactor

- Status: `draft`
- Owner suggestion: `frontend` for component refactor and rendering, `test` for regression and interaction validation
- Related area: workflow mode, intervention card rendering, human-in-the-loop UX

## Goal

Refactor the current monolithic `InterventionCard` component (~1500 lines,
single file) into a layered architecture that cleanly separates **interaction
mode** (how to render) from **business semantics** (what to display).

After refactor:

1. adding a new action kind (e.g. `date_picker`, `file_upload`) requires only a
   new form component, not modifying existing forms
2. adding a new intervention type (e.g. `after_tool`, `escalation`) requires
   only updating the shell's context section, not touching any form
3. the original `before_tool` confirmation card is restored to its intended
   design, unaffected by clarification-specific changes
4. every form component is independently testable

**This is a frontend-only refactor. No backend changes are required.**

The backend `InterventionRequest` schema, the `/api/threads/{thread_id}/interventions/{request_id}:resolve`
endpoint, and all payload validation rules remain frozen.

## Problem Statement

### What Broke

After three commits (`ace2e8b`, `05b13f5`, `aa6c32b`) that added the user
clarification UI, the original `before_tool` confirmation card was affected:

| Aspect                    | Original (e4ca3ce)               | Current                                |
|---------------------------|----------------------------------|----------------------------------------|
| Context fallback          | Only shown when no `display`     | Always shown                           |
| `shouldRenderDisplayItem` | Filters out "reminder" labels    | Deleted                                |
| Header font               | `text-[14px]`                    | `text-[13px]`                          |
| Decision area icon        | `ArrowRightIcon`                 | `AlertTriangleIcon` with risk color    |
| Input rows                | `rows={1}`                       | `rows={3}`                             |
| Console logging           | Detailed                         | Removed                                |

### Root Cause

One 1500-line component handles all interaction modes through interleaved
if/else branching. Any change to any branch risks side-effects on all other
branches.

### Industry Reference

Research across 6 major agent frameworks confirms:

| Framework         | Approach                                                                 |
|-------------------|--------------------------------------------------------------------------|
| LangGraph         | Single `HumanInterrupt` schema, `config` flags drive conditional render  |
| OpenAI Agents SDK | `needsApproval` boolean, approve/reject only, no clarification           |
| Dify              | Unified `HumanInputNode` with configurable form fields + action buttons  |
| Coze              | `QuestionNode` with options -> branch ports                              |
| AutoGen           | Freeform text via `UserProxyAgent`, no structured schema                 |
| CrewAI            | Two separate systems (`human_input` flag + `@human_feedback` decorator)  |

**Key insight**: business semantics (why) and interaction mode (how) should be
orthogonal. LangGraph uses config flags for this; Dify uses form field types.
DeerFlow's backend already has both `intervention_type` and `action.kind` but
the frontend conflates them.

## Design Principle

**Business semantics determine display text. Interaction mode determines form
structure. The two are orthogonal.**

```
intervention_type / category   ──→  shell context (title, summary, icon, color)
action.kind / questions[]      ──→  form selection (which form component to render)
```

A `before_tool` intervention with `kind: "single_select"` and a `clarification`
intervention with `kind: "single_select"` render the **same form** but with
**different context**.

## Architecture

### Three Layers

```
┌───────────────────────────────────────────────────────────────┐
│  Layer 1: Dispatcher                                          │
│  intervention-card.tsx — resolveStrategy() + select form      │
└──────────────────────────────┬────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────┐
│  Layer 2: Shell                                               │
│  intervention-shell.tsx — unified card chrome                 │
│    ├── Header: icon + title + risk badge                     │
│    ├── Context: summary + sections + meta (varies by type)   │
│    ├── [slot: form content]                                  │
│    └── (submit is owned by the form, not the shell)          │
└──────────────────────────────┬────────────────────────────────┘
                               │
┌──────────────────────────────▼────────────────────────────────┐
│  Layer 3: Forms + Primitives                                  │
│  forms/      — one component per interaction mode             │
│  primitives/ — shared atomic UI pieces                        │
└───────────────────────────────────────────────────────────────┘
```

### File Structure

All new files go under:

```
frontend/src/components/workspace/messages/intervention/
```

The existing `intervention-card.tsx` becomes a thin re-export after refactor.

```
intervention/
├── intervention-card.tsx              ← public entry (thin dispatcher)
├── resolve-strategy.ts               ← pure function: request → strategy enum
│
├── shell/
│   ├── intervention-shell.tsx         ← card chrome: header + context + children
│   ├── intervention-header.tsx        ← BotIcon + title + risk badge
│   └── intervention-context.tsx       ← summary / sections / meta display
│
├── forms/
│   ├── action-bar-form.tsx            ← buttons + optional input (original tool approval)
│   ├── confirm-form.tsx               ← simple confirm card
│   ├── select-form.tsx                ← single select + custom input
│   ├── multi-select-form.tsx          ← multi select + custom input
│   ├── input-form.tsx                 ← freeform text input
│   └── composite-stepper-form.tsx     ← multi-question step-by-step
│
├── primitives/
│   ├── option-list.tsx                ← radio / checkbox option list
│   ├── custom-input-section.tsx       ← "custom / supplementary input" block
│   └── submit-button.tsx              ← submit with loading state
│
└── utils.ts                           ← pure functions (shared across all layers)
```

### Strategy Resolution

`resolve-strategy.ts` is a **pure function** that maps `InterventionRequest`
to a strategy string. The mapping is based on **interaction mode**, not business
semantics:

```typescript
type InterventionStrategy =
  | "composite-stepper"  // questions.length > 1
  | "select-form"        // primaryKind is single_select or select
  | "multi-select-form"  // primaryKind is multi_select
  | "confirm-form"       // primaryKind is confirm
  | "input-form"         // primaryKind is input (single action)
  | "action-bar";        // buttons + optional input (default / tool approval)

function resolveStrategy(request: InterventionRequest): InterventionStrategy {
  // 1. multi-question composite takes priority
  const questions = (request.questions ?? []).filter(isRenderableQuestion);
  if (questions.length > 1) {
    return "composite-stepper";
  }

  // 2. single-question or single-action: dispatch by primary action kind
  const primaryKind = request.action_schema.actions[0]?.kind;

  if (primaryKind === "single_select" || primaryKind === "select") {
    return "select-form";
  }
  if (primaryKind === "multi_select") {
    return "multi-select-form";
  }
  if (primaryKind === "confirm") {
    return "confirm-form";
  }

  // 3. single input-only action (no buttons alongside)
  const actions = request.action_schema.actions;
  if (actions.length === 1 && primaryKind === "input") {
    return "input-form";
  }

  // 4. default: button group + optional input (original approval pattern)
  return "action-bar";
}
```

### Shell Component

`intervention-shell.tsx` provides the card chrome. It accepts `children` as a
slot for the form content.

**Context rendering rules** (driven by `intervention_type` / `category`):

| Field                | `before_tool`               | `clarification`                |
|----------------------|-----------------------------|--------------------------------|
| Header subtitle      | `interventionRequiredLabel`  | `waiting_clarification`        |
| Summary              | `display.summary` or reason  | question label or title        |
| Source agent / tool   | Shown                       | Hidden                         |
| Display sections     | Shown                       | Hidden                         |
| Context entries      | Shown only when no `display` | Hidden                         |
| Risk tip             | Shown                       | Hidden                         |
| Risk badge           | Shown                       | Hidden                         |
| `description` / `action_summary` | Shown            | Hidden                         |

This is the **only place** where `intervention_type` affects rendering.

### Form Components

Each form receives the intervention request and a shared `onSubmit` callback.
Forms do **not** know about `intervention_type`.

#### action-bar-form.tsx

Renders the original `e4ca3ce` tool approval pattern:

- Filter actions by `kind === "button"` → render as button group
- Filter actions by `kind === "input"` → render as optional textarea
- Button label: `getDisplayActionLabel(display, action, fallback)`
- Primary/destructive styling by `resolution_behavior`
- Textarea: `rows={1}`, display placeholder or fallback
- Submit payload: `{}` for buttons, `{ comment: text }` for input

**This form must restore the original `e4ca3ce` visual design:**

- Decision area icon: `ArrowRightIcon` (not `AlertTriangleIcon`)
- Decision area background: `bg-muted/4`
- `shouldRenderDisplayItem` filter reapplied

#### confirm-form.tsx

- Shows action label and hint text
- Single confirm button
- Submit payload: `{ confirmed: true }`

#### select-form.tsx

- Renders `OptionList` (radio buttons)
- Renders `CustomInputSection` ("custom / supplementary")
- Submit payload: `{ selected, custom, custom_text }`
- Options sourced from: `action.options` → fallback `extractNumberedOptions()`
- Option limit: first 3 for clarification, unlimited for tool interventions

#### multi-select-form.tsx

- Renders `OptionList` (checkboxes)
- Renders custom values textarea
- Submit payload: `{ selected[], custom, custom_values[] }`
- Respects `min_select` / `max_select`

#### input-form.tsx

- Single text input + submit button (inline layout)
- Submit payload: `{ text, comment }`

#### composite-stepper-form.tsx

- Step indicator (1/2/3...) in header area
- Sequential question rendering (delegates to sub-forms per question kind)
- Accumulates answers across steps
- Final submit payload: `{ answers: { [key]: answer_payload } }`

### Primitives

#### option-list.tsx

Props:

```typescript
interface OptionListProps {
  options: InterventionOption[];
  mode: "single" | "multi";
  selected: string | string[];
  onChange: (value: string | string[]) => void;
  disabled?: boolean;
}
```

Renders radio buttons (single) or checkboxes (multi) with label, value,
optional description.

#### custom-input-section.tsx

Props:

```typescript
interface CustomInputSectionProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  title?: string;
  mode: "single-line" | "multi-line";
}
```

Renders the dashed-border "custom / supplementary input" block with
`PlusCircleIcon`.

#### submit-button.tsx

Props:

```typescript
interface SubmitButtonProps {
  label: string;
  disabled?: boolean;
  loading?: boolean;
  onClick: () => void;
  variant?: "primary" | "outline";
}
```

### Shared Utils

`utils.ts` contains **only pure functions** extracted from the current
component:

- `safeDisplayText(value, fallback)` — null/broken text guard
- `isBrokenDisplayText(value)` — detect `???` or empty
- `normalizeText(value)` — trim string
- `formatContextValue(value)` / `formatContextLabel(label)` — display helpers
- `extractNumberedOptions(text)` / `stripNumberedOptions(text)` — parse options
  from free text
- `getActionOptions(action, request)` — resolve options from action or request
- `getQuestionOptions(question)` — resolve options from question
- `getVisibleOptions(options, limit)` — truncate options
- `isRenderableQuestion(question)` — filter boilerplate questions
- `isExplanatoryQuestion(question)` — detect preamble questions
- `getDisplayActionLabel(display, action, fallback)` — resolve button label
- `getActionHint(action, fallback)` — resolve hint text
- `shouldRenderDisplayItem(label)` — filter "reminder" labels (restored)
- `resolveInterventionTone(riskLevel)` — risk-based color/icon mapping

### Resume Flow

The `handleResumeSubmit` logic (submit `thread.submit()` with checkpoint after
resolution) is **shared across all forms**. It lives in the dispatcher layer
(`intervention-card.tsx`) and is passed to forms via props or context.

Forms call: `onSubmit(actionKey, payload)`.

The dispatcher handles: resolve mutation → toast → resume submit if needed.

## Contract To Confirm First

- Event/API: **No changes.** Existing `InterventionRequest` schema and
  `/api/threads/{thread_id}/interventions/{request_id}:resolve` endpoint remain
  frozen.
- Payload shapes: **No changes.** All payload validation rules in
  `interventions.py` remain as-is.
- Persistence: **No changes.** Task state machine unchanged.
- Error behavior: **No changes.** 404/409/422 handling unchanged.

## Backend Changes

**None.** This is a frontend-only refactor.

The backend `InterventionRequest` type, `InterventionActionSchema`,
`InterventionDisplay`, and all intervention builders remain untouched.

## Frontend Changes

### Phase 1: Extract Utils and Primitives (non-breaking)

1. Create `intervention/utils.ts` — move all pure functions out
2. Create `intervention/primitives/option-list.tsx`
3. Create `intervention/primitives/custom-input-section.tsx`
4. Create `intervention/primitives/submit-button.tsx`
5. Existing `intervention-card.tsx` imports from new locations
6. **Zero visual change.** Verify via screenshot comparison.

### Phase 2: Extract Shell (non-breaking)

1. Create `intervention/shell/intervention-header.tsx`
2. Create `intervention/shell/intervention-context.tsx`
3. Create `intervention/shell/intervention-shell.tsx` — composes header +
   context + children slot
4. Existing `intervention-card.tsx` wraps content in `InterventionShell`
5. **Zero visual change.** Verify via screenshot comparison.

### Phase 3: Extract Forms (may adjust visuals)

1. Create `intervention/forms/action-bar-form.tsx` — **restore original e4ca3ce
   design** for the tool approval path
2. Create `intervention/forms/confirm-form.tsx`
3. Create `intervention/forms/select-form.tsx`
4. Create `intervention/forms/multi-select-form.tsx`
5. Create `intervention/forms/input-form.tsx`
6. Create `intervention/forms/composite-stepper-form.tsx`

### Phase 4: Wire Dispatcher

1. Create `intervention/resolve-strategy.ts`
2. Rewrite `intervention-card.tsx` as thin dispatcher (~50 lines)
3. Old file becomes re-export from new location for backward compatibility
4. **Full regression pass.**

## Risks

- **Phase 1/2 must not change behavior.** These phases are pure extraction.
  Any visual diff indicates a bug in extraction.
- **Phase 3 intentionally restores the original `before_tool` card design.**
  The current (post-ace2e8b) tool confirmation card has visual regressions that
  should be fixed, not preserved.
- **Composite stepper has the most complex state.** Extracting it requires
  careful transfer of multi-step state management (activeQuestionIndex,
  questionAnswers, per-question drafts/selections).
- **i18n keys** used across forms must remain consistent. All `interactionCopy`
  and `t.subtasks` references should be audited during extraction.

## Acceptance Criteria

### Functional

- [ ] `before_tool` intervention card renders identically to commit `e4ca3ce`
  design (font size, icon, context display rules, input rows)
- [ ] `clarification` single-action card (input, single_select, multi_select,
  confirm) renders correctly
- [ ] `clarification` composite multi-question stepper renders correctly with
  step indicators
- [ ] All payload formats match existing backend validation:
  - button: `{}`
  - input: `{ text, comment }`
  - confirm: `{ confirmed: true }`
  - single_select: `{ selected, custom, custom_text }`
  - multi_select: `{ selected[], custom, custom_values[] }`
  - composite: `{ answers: { [key]: payload } }`
- [ ] Resume flow (checkpoint submit) works for all form types
- [ ] Error handling (409 stale, 422 invalid) works for all form types
- [ ] Option limit (first 3 for clarification) applied correctly

### Structural

- [ ] `intervention-card.tsx` is under 80 lines (dispatcher only)
- [ ] No form component imports or references `intervention_type` or `category`
- [ ] Each form component is under 200 lines
- [ ] `utils.ts` contains only pure functions (no React hooks, no side effects)
- [ ] All primitives accept props, no internal data fetching

### Non-functional

- [ ] `pnpm check` passes (lint + typecheck)
- [ ] Existing unit tests pass without modification
- [ ] New unit tests for `resolveStrategy` covering all 6 strategy paths
- [ ] New unit tests for each form component's submit payload shape

## Open Questions

- [ ] Should the composite stepper step indicator live in the shell header
  (current location) or in the form itself? Recommendation: shell header,
  since it replaces the risk badge area.
- [ ] Should `handleResumeSubmit` be passed as a prop or provided via React
  context? Recommendation: prop, since only the dispatcher knows about thread
  submission.

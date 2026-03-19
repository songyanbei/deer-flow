# Workflow Intervention Card Refactor Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature: `workflow-intervention-card-refactor.md`

## Test Scope

This refactor is frontend-only. All tests are unit tests (Vitest) and manual
interaction tests. No backend test changes are needed.

## 1. Unit Tests: `resolve-strategy.ts`

Target: `resolveStrategy()` pure function.

- [ ] returns `"composite-stepper"` when `questions` has 2+ renderable entries
- [ ] returns `"composite-stepper"` when questions has 3 entries but 1 is
  filtered by `isRenderableQuestion`, leaving 2
- [ ] returns `"select-form"` when single action has `kind: "single_select"`
- [ ] returns `"select-form"` when single action has `kind: "select"`
- [ ] returns `"multi-select-form"` when single action has `kind: "multi_select"`
- [ ] returns `"confirm-form"` when single action has `kind: "confirm"`
- [ ] returns `"input-form"` when single action has `kind: "input"` and only 1
  action
- [ ] returns `"action-bar"` when actions contain `kind: "button"` + `kind: "input"`
- [ ] returns `"action-bar"` when actions array is empty
- [ ] returns `"action-bar"` when action has unknown kind value
- [ ] returns `"action-bar"` when 2 actions both have `kind: "button"`
- [ ] `questions` with 1 renderable entry does NOT trigger composite-stepper
  (falls through to action kind check)

## 2. Unit Tests: `utils.ts`

### 2.1 Text Helpers

- [ ] `safeDisplayText("valid")` returns `"valid"`
- [ ] `safeDisplayText("")` returns fallback
- [ ] `safeDisplayText("???")` returns fallback
- [ ] `safeDisplayText(null)` returns fallback
- [ ] `safeDisplayText(undefined, "fb")` returns `"fb"`
- [ ] `isBrokenDisplayText("???")` returns `true`
- [ ] `isBrokenDisplayText("ok")` returns `false`
- [ ] `normalizeText("  hello  ")` returns `"hello"`
- [ ] `normalizeText(42)` returns `""`

### 2.2 Option Extraction

- [ ] `extractNumberedOptions("1. foo\n2. bar")` returns 2 options
- [ ] `extractNumberedOptions("no numbers here")` returns empty array
- [ ] `extractNumberedOptions("1. dup\n2. dup")` deduplicates to 1 option
- [ ] `getVisibleOptions([a,b,c,d,e], true)` returns first 3
- [ ] `getVisibleOptions([a,b,c,d,e], false)` returns all 5
- [ ] `getVisibleOptions([a,b], true)` returns all 2 (under limit)

### 2.3 Label Resolution

- [ ] `getDisplayActionLabel` prefers `display.primary_action_label` for
  non-destructive actions
- [ ] `getDisplayActionLabel` prefers `display.secondary_action_label` for
  `fail_current_task` actions
- [ ] `getDisplayActionLabel` falls back to `action.confirm_text` then
  `action.label` then fallback string
- [ ] `shouldRenderDisplayItem("提醒")` returns `false`
- [ ] `shouldRenderDisplayItem("reminder")` returns `false`
- [ ] `shouldRenderDisplayItem("会议主题")` returns `true`

### 2.4 Question Filters

- [ ] `isRenderableQuestion` returns `false` for label starting with
  "需要这些信息"
- [ ] `isRenderableQuestion` returns `false` for input kind without `?` or
  recognized prefix
- [ ] `isRenderableQuestion` returns `true` for label "请填写会议主题"
- [ ] `isExplanatoryQuestion` returns `true` for "我需要了解一些基本信息"
- [ ] `isExplanatoryQuestion` returns `false` for "请填写日期"

## 3. Unit Tests: Primitives

### 3.1 `option-list.tsx`

- [ ] Renders correct number of option items
- [ ] Single mode: clicking an option calls `onChange` with that value
- [ ] Single mode: renders `CircleDotIcon` for selected, `CircleIcon` for
  unselected
- [ ] Multi mode: clicking toggles selection
- [ ] Multi mode: renders `CheckSquareIcon` for selected, `SquareIcon` for
  unselected
- [ ] Shows option description when provided
- [ ] Handles empty options array (renders nothing)

### 3.2 `custom-input-section.tsx`

- [ ] Single-line mode renders `Input` element
- [ ] Multi-line mode renders `Textarea` element
- [ ] Displays title with `PlusCircleIcon`
- [ ] `onChange` fires on input change

### 3.3 `submit-button.tsx`

- [ ] Renders label text
- [ ] Shows `Loader2Icon` when `loading` is true
- [ ] Button is disabled when `disabled` is true
- [ ] Calls `onClick` on click

## 4. Unit Tests: Form Components

### 4.1 `action-bar-form.tsx`

- [ ] Renders button for each `kind: "button"` action
- [ ] First non-destructive button has primary styling
- [ ] Destructive button (`fail_current_task`) has outline styling
- [ ] Renders textarea for `kind: "input"` action with `rows={1}`
- [ ] Button click calls `onSubmit(actionKey, {})`
- [ ] Input submit calls `onSubmit(actionKey, { comment: "text" })`
- [ ] Input submit button disabled when textarea is empty

### 4.2 `confirm-form.tsx`

- [ ] Renders confirm button with label
- [ ] Click calls `onSubmit(actionKey, { confirmed: true })`
- [ ] Button disabled during pending state

### 4.3 `select-form.tsx`

- [ ] Renders options via `OptionList` in single mode
- [ ] Renders custom input section
- [ ] Selecting option clears custom input
- [ ] Typing custom input clears option selection
- [ ] Submit sends `{ selected: "value", custom: false }`
- [ ] Submit with custom sends `{ selected: "custom", custom: true, custom_text: "custom" }`
- [ ] Submit disabled when nothing selected and no custom input
- [ ] When `limitOptions` is true and 5 options, only 3 shown

### 4.4 `multi-select-form.tsx`

- [ ] Renders options via `OptionList` in multi mode
- [ ] Multiple options can be selected
- [ ] Custom values parsed from comma/newline separated text
- [ ] Submit sends `{ selected: ["a","b"], custom: false }`
- [ ] Submit with custom sends merged + deduplicated values
- [ ] Submit disabled when below `min_select`
- [ ] Selecting beyond `max_select` prevents further selection or disables submit

### 4.5 `input-form.tsx`

- [ ] Renders single Input element
- [ ] Submit sends `{ text: "input", comment: "input" }`
- [ ] Submit disabled when input is empty

### 4.6 `composite-stepper-form.tsx`

- [ ] Renders step indicator with correct count
- [ ] Shows active question content
- [ ] "Next" button advances to next question
- [ ] Last question shows final submit label
- [ ] Input question: saves text answer
- [ ] Single select question: saves selected value
- [ ] Multi select question: saves selected array
- [ ] Confirm question: saves `{ confirmed: true }`
- [ ] Final submit sends `{ answers: { key1: ..., key2: ... } }`
- [ ] Clicking step indicator navigates to that step
- [ ] Previously answered step shows saved value

## 5. Unit Tests: Shell

### 5.1 `intervention-shell.tsx`

- [ ] Renders header, context, and children
- [ ] `before_tool` type: shows context sections
- [ ] `clarification` type: hides context sections

### 5.2 `intervention-context.tsx`

- [ ] `before_tool` with `display`: shows summary, sections, risk_tip
- [ ] `before_tool` with `display`: hides raw context entries
- [ ] `before_tool` without `display`: shows context entries as fallback
- [ ] `before_tool` without `display`: shows source_agent and tool_name
- [ ] Sections filter items via `shouldRenderDisplayItem`
- [ ] `clarification` type: renders nothing

## 6. Integration / Manual Tests

### 6.1 Tool Execution Approval (`before_tool`)

- [ ] Trigger a risky tool call (e.g. create meeting, send email)
- [ ] Card shows: title, summary, display sections, risk badge
- [ ] Card does NOT show raw context when `display` is present
- [ ] Click "Approve" -> workflow resumes, tool executes
- [ ] Click "Reject" -> task fails with rejection
- [ ] Type modification + submit -> workflow resumes with input
- [ ] Visual match: card looks like commit e4ca3ce design

### 6.2 Single-Action Clarification (`clarification`)

- [ ] Trigger a clarification with `kind: "input"` -> input form shown
- [ ] Trigger a clarification with `kind: "single_select"` -> select form with
  options shown
- [ ] Trigger a clarification with `kind: "multi_select"` -> multi-select form
  shown
- [ ] Trigger a clarification with `kind: "confirm"` -> confirm button shown
- [ ] Submit each type -> workflow resumes with correct payload

### 6.3 Composite Clarification (`clarification` with questions)

- [ ] Trigger a multi-question clarification (e.g. meeting booking needing
  room + time + subject)
- [ ] Step indicator shows correct total (e.g. 1/2/3)
- [ ] Each step renders correct question kind
- [ ] Advancing to next step preserves previous answers
- [ ] Final submit sends composite payload
- [ ] Workflow resumes correctly after composite submit

### 6.4 Error and Edge Cases

- [ ] Submit on a stale intervention -> 409 toast ("intervention stale")
- [ ] Submit invalid payload -> 422 toast ("invalid")
- [ ] Page refresh while intervention is pending -> card re-renders from state
- [ ] Two interventions on different tasks -> each renders independently
- [ ] Intervention with zero actions -> card renders title/summary only, no
  crash
- [ ] Intervention with unknown `action.kind` -> falls back to `action-bar`

### 6.5 Resume Flow

- [ ] After resolution with `resume_action: "submit_resume"` -> thread.submit
  fires with checkpoint
- [ ] Workflow stream resumes and new events appear
- [ ] After resolution without `resume_action` -> no extra submit, card
  dismissed

## 7. Regression Guards

- [ ] `pnpm check` passes (lint + typecheck)
- [ ] `pnpm test:unit` passes (existing + new tests)
- [ ] No console errors in browser during all manual test scenarios
- [ ] Workflow task panel correctly reflects intervention status transitions:
  `WAITING_INTERVENTION` -> `RUNNING` or `FAILED`
- [ ] Workflow footer correctly reflects pending intervention count

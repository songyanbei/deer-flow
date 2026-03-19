# Workflow Intervention Card Refactor Frontend Checklist

- Status: `draft`
- Owner: `frontend`
- Related feature: `workflow-intervention-card-refactor.md`

## Phase 1: Extract Utils and Primitives

### 1.1 Create `intervention/utils.ts`

- [ ] Move all pure functions from current `intervention-card.tsx`:
  - `safeDisplayText`, `isBrokenDisplayText`, `normalizeText`
  - `formatContextValue`, `formatContextLabel`
  - `extractNumberedOptions`, `stripNumberedOptions`
  - `getActionOptions`, `getQuestionOptions`, `getVisibleOptions`
  - `isRenderableQuestion`, `isExplanatoryQuestion`
  - `getDisplayActionLabel`, `getActionHint`, `getActionButtonLabel`
  - `shouldRenderDisplayItem` (restore from e4ca3ce, was deleted)
  - `getRiskTone`
- [ ] Move `submitInterventionAction` async helper
- [ ] Existing `intervention-card.tsx` imports from `./intervention/utils`
- Done when:
  - all pure functions are in `utils.ts`
  - `intervention-card.tsx` no longer defines any helper functions inline
  - zero visual change (screenshot comparison)

### 1.2 Create `intervention/primitives/option-list.tsx`

- [ ] Extract radio/checkbox option rendering into standalone component
- [ ] Props: `options`, `mode` ("single" | "multi"), `selected`, `onChange`,
  `disabled`
- [ ] Single mode: `CircleDotIcon` / `CircleIcon`
- [ ] Multi mode: `CheckSquareIcon` / `SquareIcon`
- [ ] Each option shows label, value fallback, optional description
- Done when:
  - `select-form` and `multi-select-form` can both use this primitive
  - component renders identically to current inline option rendering

### 1.3 Create `intervention/primitives/custom-input-section.tsx`

- [ ] Extract the "custom / supplementary input" dashed-border block
- [ ] Props: `value`, `onChange`, `placeholder`, `title`, `mode`
  ("single-line" | "multi-line")
- [ ] Single-line: renders `Input`
- [ ] Multi-line: renders `Textarea`
- [ ] Shows `PlusCircleIcon` + title label
- Done when:
  - used by `select-form`, `multi-select-form`, and `composite-stepper-form`

### 1.4 Create `intervention/primitives/submit-button.tsx`

- [ ] Extract submit button with loading spinner
- [ ] Props: `label`, `disabled`, `loading`, `onClick`, `variant`
- [ ] Primary variant: `bg-foreground text-background`
- [ ] Outline variant: `border-border bg-background text-foreground`
- [ ] Shows `Loader2Icon` spinner when loading
- Done when:
  - all forms use this primitive for their submit buttons

## Phase 2: Extract Shell

### 2.1 Create `intervention/shell/intervention-header.tsx`

- [ ] Extract card header: BotIcon + risk dot + title area
- [ ] Props: `title`, `subtitle`, `riskLevel`, `rightSlot` (for step indicator
  or risk badge)
- [ ] Subtitle text varies by intervention type:
  - `before_tool` -> `t.subtasks.interventionRequiredLabel`
  - `clarification` -> `t.subtasks.waiting_clarification`
- Done when:
  - header renders identically to current for both intervention types

### 2.2 Create `intervention/shell/intervention-context.tsx`

- [ ] Extract context/detail display area
- [ ] For `before_tool` type, renders:
  - source agent / tool name meta line (when no `display`)
  - summary text
  - display sections (with `shouldRenderDisplayItem` filter restored)
  - description / action_summary
  - context entries (only when no `display`)
  - risk tip
- [ ] For `clarification` type, renders:
  - nothing (context is handled within the form)
- [ ] Props: `request`, plus `display`, `interventionType`, `contextEntries`
- Done when:
  - `before_tool` card shows full context as designed in e4ca3ce
  - `clarification` card shows no extraneous context

### 2.3 Create `intervention/shell/intervention-shell.tsx`

- [ ] Compose header + context + `{children}` slot inside card chrome
- [ ] Card chrome: `rounded-xl border border-border/70 bg-background shadow-...`
- [ ] Accepts `request` and renders header + context automatically
- [ ] `children` is the form slot
- Done when:
  - wrapping any form in `<InterventionShell>` produces a complete card

## Phase 3: Extract Forms

### 3.1 Create `intervention/forms/action-bar-form.tsx`

- [ ] Restore original e4ca3ce tool approval rendering:
  - Decision section icon: `ArrowRightIcon` (not `AlertTriangleIcon`)
  - Decision section background: `bg-muted/4` (not `bg-muted/6`)
  - Decision label: `t.subtasks.interventionDecisionLabel`
  - Button styling: primary (first non-destructive) + outline (destructive)
  - Button label: `getDisplayActionLabel(display, action.key, fallback)`
    matching by action key name (`approve`/`reject`/`confirm`/...)
  - Input textarea: `rows={1}` (not `rows={3}`)
  - Input placeholder: `display.respond_placeholder` or fallback
- [ ] Filter actions: `kind === "button"` for button group, `kind === "input"`
  for textarea
- [ ] Submit: buttons send `{}`, input sends `{ comment: text }`
- Done when:
  - tool approval card is visually identical to e4ca3ce commit
  - existing `before_tool` interventions render correctly

### 3.2 Create `intervention/forms/confirm-form.tsx`

- [ ] Show action label + hint text
- [ ] Single confirm button
- [ ] Submit payload: `{ confirmed: true }`
- [ ] i18n: `confirmTitle` for `before_tool`, `clarificationConfirmTitle` for
  `clarification` (passed via props, not read from intervention_type)
- Done when:
  - confirm actions render as a clean single-button card

### 3.3 Create `intervention/forms/select-form.tsx`

- [ ] Render `OptionList` in single mode
- [ ] Render `CustomInputSection` in single-line mode
- [ ] When custom input has value, clear radio selection (and vice versa)
- [ ] Option limit: first 3 when `limitOptions` prop is true
- [ ] Submit payload: `{ selected, custom, custom_text }`
- [ ] Hint text from `getActionHint(action, singleSelectHintText)`
- Done when:
  - single select with 5 options shows 3 + custom input
  - selecting an option then typing custom clears the option

### 3.4 Create `intervention/forms/multi-select-form.tsx`

- [ ] Render `OptionList` in multi mode
- [ ] Render `CustomInputSection` in multi-line mode (comma/newline separated)
- [ ] Merge selected options + custom values, deduplicate
- [ ] Respect `min_select` / `max_select`
- [ ] Option limit: first 3 when `limitOptions` prop is true
- [ ] Submit payload: `{ selected[], custom, custom_text, custom_values[] }`
- Done when:
  - multi select respects min/max constraints
  - custom values are parsed and merged with selections

### 3.5 Create `intervention/forms/input-form.tsx`

- [ ] Single `Input` + inline submit button (grid layout)
- [ ] Submit payload: `{ text, comment }` (both fields set to same value)
- [ ] Placeholder from question or action
- Done when:
  - single input actions render as compact inline form

### 3.6 Create `intervention/forms/composite-stepper-form.tsx`

- [ ] Step indicator in shell header `rightSlot` (numbered buttons 1/2/3...)
- [ ] Active question rendering: delegates to sub-renderers by `question.kind`
  - `input` -> Input + submit
  - `single_select` / `select` -> OptionList + CustomInput + submit
  - `multi_select` -> OptionList + Textarea + submit
  - `confirm` -> confirm button
- [ ] State management: `activeQuestionIndex`, `questionAnswers`,
  per-question `drafts`, `selectedValues`, `customValues`,
  `multiSelectedValues`
- [ ] Non-last steps: "Next" button, saves answer to local state
- [ ] Last step: submit button, sends `{ answers: allAnswers }`
- [ ] Explanatory question (preamble): shown as title, not as a step
- Done when:
  - multi-question interventions step through correctly
  - going back to a previous step shows saved answer
  - final submit sends all accumulated answers

## Phase 4: Wire Dispatcher

### 4.1 Create `intervention/resolve-strategy.ts`

- [ ] Implement `resolveStrategy(request): InterventionStrategy`
- [ ] Strategy mapping:
  - `questions.filter(isRenderableQuestion).length > 1` -> `composite-stepper`
  - `primaryKind === "single_select" | "select"` -> `select-form`
  - `primaryKind === "multi_select"` -> `multi-select-form`
  - `primaryKind === "confirm"` -> `confirm-form`
  - single action with `kind === "input"` -> `input-form`
  - default -> `action-bar`
- Done when:
  - unit tests cover all 6 strategy paths
  - edge cases (empty actions, unknown kind) fall to `action-bar`

### 4.2 Rewrite `intervention-card.tsx` as Dispatcher

- [ ] Under 80 lines total
- [ ] Reads `task.interventionRequest`, calls `resolveStrategy()`
- [ ] Contains `handleResumeSubmit` (thread.submit with checkpoint)
- [ ] Contains shared `onSubmit(actionKey, payload)` that calls
  `submitInterventionAction` then `handleResumeSubmit`
- [ ] Switches on strategy, renders `<InterventionShell>` + selected form
- [ ] Old file at `messages/intervention-card.tsx` re-exports from new location
- Done when:
  - dispatcher file is under 80 lines
  - all form-specific logic is gone from this file

### 4.3 Backward Compatibility

- [ ] Ensure existing import path `messages/intervention-card` still works
- [ ] Verify all parent components that reference `InterventionCard` compile
- Done when:
  - `pnpm check` passes
  - no import errors in the workspace

## Phase 5: Regression Validation

- [ ] Screenshot comparison: `before_tool` card vs e4ca3ce commit
- [ ] Screenshot comparison: `clarification` single-action cards
- [ ] Screenshot comparison: `clarification` composite stepper
- [ ] Manual test: approve a tool execution (e.g. meeting booking)
- [ ] Manual test: reject a tool execution
- [ ] Manual test: provide input on a tool execution
- [ ] Manual test: answer a single clarification question
- [ ] Manual test: complete a multi-step clarification
- [ ] Manual test: select an option + custom input
- [ ] Manual test: multi-select with custom values
- [ ] Manual test: verify resume flow (workflow continues after resolution)
- [ ] Manual test: verify stale intervention (409) toast
- [ ] Manual test: verify page refresh preserves intervention state
- [ ] `pnpm check` passes
- [ ] `pnpm test:unit` passes

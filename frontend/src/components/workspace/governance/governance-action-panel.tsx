"use client";

import {
  CheckSquareIcon,
  CircleDotIcon,
  CircleIcon,
  Loader2Icon,
  PlusCircleIcon,
  SquareIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { GovernanceItem } from "@/core/governance";
import {
  buildGovernanceQuestionPayload,
  getGovernanceQuestions,
  isRenderableGovernanceQuestion,
  parseGovernanceCustomValues,
} from "@/core/governance";
import { useI18n } from "@/core/i18n/hooks";
import type {
  InterventionActionSchema,
  InterventionOption,
  InterventionQuestion,
} from "@/core/threads";
import { cn } from "@/lib/utils";

type GovernanceActionPanelProps = {
  item: GovernanceItem;
  isPending: boolean;
  onSubmit: (
    actionKey: string,
    payload: Record<string, unknown>,
    fingerprint?: string,
  ) => Promise<void>;
};

function normalizeText(value: unknown) {
  if (typeof value !== "string") {
    return "";
  }
  return value.trim();
}

function getActionLabel(
  item: GovernanceItem,
  actionKey: string,
  fallback: string,
) {
  if (actionKey === "approve") {
    return item.intervention_display?.primary_action_label ?? fallback;
  }
  if (actionKey === "reject") {
    return item.intervention_display?.secondary_action_label ?? fallback;
  }
  if (actionKey === "provide_input") {
    return item.intervention_display?.respond_action_label ?? fallback;
  }
  return fallback;
}

function getActionOptions(
  action: InterventionActionSchema["actions"][number] | InterventionQuestion,
) {
  return Array.isArray(action.options) ? action.options : [];
}

function isQuestionComplete(
  question: InterventionQuestion,
  payload: Record<string, unknown> | null,
) {
  if (!question.required) {
    return true;
  }
  return payload !== null;
}

function renderOptionLabel(option: InterventionOption) {
  return normalizeText(option.label) || normalizeText(option.value);
}

export function GovernanceActionPanel({
  item,
  isPending,
  onSubmit,
}: GovernanceActionPanelProps) {
  const { t } = useI18n();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [selectedValues, setSelectedValues] = useState<Record<string, string>>(
    {},
  );
  const [customValues, setCustomValues] = useState<Record<string, string>>({});
  const [multiSelectedValues, setMultiSelectedValues] = useState<
    Record<string, string[]>
  >({});
  const display = item.intervention_display;
  const actionSchema = item.intervention_action_schema;

  const questions = useMemo(
    () =>
      getGovernanceQuestions(item).filter((question) =>
        isRenderableGovernanceQuestion(question),
      ),
    [item],
  );

  if (!actionSchema || actionSchema.actions.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border/70 bg-muted/10 p-4 text-sm text-muted-foreground">
        {t.governance.states.noActionSchema}
      </div>
    );
  }

  const compositeAction = actionSchema.actions.find(
    (action) => action.kind === "composite",
  );
  const plainButtonActions = actionSchema.actions.filter(
    (action) => action.kind === "button",
  );
  const confirmActions = actionSchema.actions.filter(
    (action) => action.kind === "confirm",
  );
  const inputActions = actionSchema.actions.filter(
    (action) => action.kind === "input",
  );
  const singleSelectActions = actionSchema.actions.filter(
    (action) => action.kind === "select" || action.kind === "single_select",
  );
  const multiSelectActions = actionSchema.actions.filter(
    (action) => action.kind === "multi_select",
  );

  const questionPayloads = questions.reduce<Record<string, Record<string, unknown> | null>>(
    (result, question) => {
      result[question.key] = buildGovernanceQuestionPayload(
        question,
        drafts,
        selectedValues,
        customValues,
        multiSelectedValues,
      );
      return result;
    },
    {},
  );

  const canSubmitComposite =
    Boolean(compositeAction) &&
    questions.every((question) =>
      isQuestionComplete(question, questionPayloads[question.key] ?? null),
    ) &&
    !isPending;

  return (
    <div className="space-y-4">
      {compositeAction && questions.length > 0 ? (
        <section className="space-y-3 rounded-xl border border-border/70 bg-background/90 p-4">
          <div className="space-y-1">
            <div className="text-sm font-semibold text-foreground">
              {getActionLabel(
                item,
                compositeAction.key,
                compositeAction.label || t.governance.actions.resolve,
              )}
            </div>
            {compositeAction.description ? (
              <div className="text-sm leading-6 text-muted-foreground">
                {compositeAction.description}
              </div>
            ) : null}
          </div>

          <div className="space-y-4">
            {questions.map((question) => {
              const options = getActionOptions(question);
              const selectedSingle = selectedValues[question.key] ?? "";
              const customSingle = customValues[question.key] ?? "";
              const selectedMulti = multiSelectedValues[question.key] ?? [];
              const customMulti = customValues[question.key] ?? "";
              const combinedMulti = Array.from(
                new Set([
                  ...selectedMulti,
                  ...parseGovernanceCustomValues(customMulti),
                ]),
              );

              return (
                <div
                  key={question.key}
                  className="space-y-3 rounded-xl border border-border/60 bg-muted/12 p-3"
                >
                  <div className="space-y-1">
                    <div className="text-sm font-medium text-foreground">
                      {question.label}
                    </div>
                    {question.description ? (
                      <div className="text-sm leading-6 text-muted-foreground">
                        {question.description}
                      </div>
                    ) : null}
                  </div>

                  {question.kind === "input" ? (
                    <Textarea
                      value={drafts[question.key] ?? ""}
                      placeholder={
                        question.placeholder ??
                        display?.respond_placeholder ??
                        question.label
                      }
                      rows={3}
                      className="min-h-0"
                      onChange={(event) =>
                        setDrafts((current) => ({
                          ...current,
                          [question.key]: event.target.value,
                        }))
                      }
                    />
                  ) : null}

                  {(question.kind === "select" ||
                    question.kind === "single_select") &&
                  options.length > 0 ? (
                    <div className="space-y-2">
                      {options.map((option) => {
                        const checked =
                          !normalizeText(customSingle) &&
                          selectedSingle === option.value;
                        return (
                          <button
                            key={option.value}
                            type="button"
                            className={cn(
                              "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                              checked
                                ? "border-primary bg-primary/5"
                                : "border-border/70 bg-background hover:bg-muted/40",
                            )}
                            onClick={() => {
                              setSelectedValues((current) => ({
                                ...current,
                                [question.key]: option.value,
                              }));
                              setCustomValues((current) => ({
                                ...current,
                                [question.key]: "",
                              }));
                            }}
                          >
                            <span className="mt-0.5 shrink-0 text-primary">
                              {checked ? (
                                <CircleDotIcon className="size-4" />
                              ) : (
                                <CircleIcon className="size-4" />
                              )}
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block text-sm font-medium text-foreground">
                                {renderOptionLabel(option)}
                              </span>
                              {option.description ? (
                                <span className="mt-1 block text-xs leading-5 text-muted-foreground">
                                  {option.description}
                                </span>
                              ) : null}
                            </span>
                          </button>
                        );
                      })}
                      <div className="space-y-2 rounded-xl border border-dashed border-border/70 bg-background/70 p-3">
                        <div className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
                          <PlusCircleIcon className="size-3.5" />
                          {t.governance.labels.detail}
                        </div>
                        <Input
                          value={customSingle}
                          placeholder={question.placeholder ?? question.label}
                          onChange={(event) => {
                            const nextValue = event.target.value;
                            setCustomValues((current) => ({
                              ...current,
                              [question.key]: nextValue,
                            }));
                            if (normalizeText(nextValue)) {
                              setSelectedValues((current) => ({
                                ...current,
                                [question.key]: "",
                              }));
                            }
                          }}
                        />
                      </div>
                    </div>
                  ) : null}

                  {question.kind === "multi_select" && options.length > 0 ? (
                    <div className="space-y-2">
                      {options.map((option) => {
                        const checked = selectedMulti.includes(option.value);
                        return (
                          <button
                            key={option.value}
                            type="button"
                            className={cn(
                              "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                              checked
                                ? "border-primary bg-primary/5"
                                : "border-border/70 bg-background hover:bg-muted/40",
                            )}
                            onClick={() => {
                              setMultiSelectedValues((current) => {
                                const existingValues = current[question.key] ?? [];
                                const nextValues = existingValues.includes(
                                  option.value,
                                )
                                  ? existingValues.filter(
                                      (value) => value !== option.value,
                                    )
                                  : [...existingValues, option.value];
                                return {
                                  ...current,
                                  [question.key]: nextValues,
                                };
                              });
                            }}
                          >
                            <span className="mt-0.5 shrink-0 text-primary">
                              {checked ? (
                                <CheckSquareIcon className="size-4" />
                              ) : (
                                <SquareIcon className="size-4" />
                              )}
                            </span>
                            <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
                              {renderOptionLabel(option)}
                            </span>
                          </button>
                        );
                      })}
                      <Textarea
                        value={customMulti}
                        placeholder={question.placeholder ?? question.label}
                        rows={3}
                        className="min-h-0"
                        onChange={(event) =>
                          setCustomValues((current) => ({
                            ...current,
                            [question.key]: event.target.value,
                          }))
                        }
                      />
                      {combinedMulti.length > 0 ? (
                        <div className="flex flex-wrap gap-2">
                          {combinedMulti.map((value) => (
                            <span
                              key={value}
                              className="rounded-full border border-border/70 bg-background px-2.5 py-1 text-xs text-foreground/80"
                            >
                              {value}
                            </span>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {question.kind === "confirm" ? (
                    <div className="rounded-xl border border-border/60 bg-background/70 px-3 py-2 text-sm text-foreground/80">
                      {question.confirm_text ?? question.label}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>

          <Button
            type="button"
            disabled={!canSubmitComposite}
            onClick={() => {
              if (!compositeAction) {
                return;
              }

              const answers = questions.reduce<Record<string, Record<string, unknown>>>(
                (result, question) => {
                  const payload = questionPayloads[question.key];
                  if (payload) {
                    result[question.key] = payload;
                  }
                  return result;
                },
                {},
              );

              void onSubmit(
                compositeAction.key,
                { answers },
                item.intervention_fingerprint ?? undefined,
              );
            }}
          >
            {isPending ? <Loader2Icon className="animate-spin" /> : null}
            {getActionLabel(
              item,
              compositeAction.key,
              compositeAction.label || t.governance.actions.resolve,
            )}
          </Button>
        </section>
      ) : null}

      {confirmActions.length > 0 ? (
        <section className="space-y-3 rounded-xl border border-border/70 bg-background/90 p-4">
          {confirmActions.map((action) => (
            <div key={action.key} className="space-y-2">
              <div className="text-sm font-semibold text-foreground">
                {getActionLabel(item, action.key, action.label)}
              </div>
              {action.description ? (
                <div className="text-sm leading-6 text-muted-foreground">
                  {action.description}
                </div>
              ) : null}
              <Button
                type="button"
                disabled={isPending}
                onClick={() =>
                  void onSubmit(
                    action.key,
                    { confirmed: true },
                    item.intervention_fingerprint ?? undefined,
                  )
                }
              >
                {isPending ? <Loader2Icon className="animate-spin" /> : null}
                {getActionLabel(item, action.key, action.label)}
              </Button>
            </div>
          ))}
        </section>
      ) : null}

      {plainButtonActions.length > 0 ? (
        <section className="space-y-3 rounded-xl border border-border/70 bg-background/90 p-4">
          <div className="flex flex-wrap gap-2">
            {plainButtonActions.map((action) => (
              <Button
                key={action.key}
                type="button"
                variant={
                  action.resolution_behavior === "fail_current_task"
                    ? "outline"
                    : "default"
                }
                disabled={isPending}
                onClick={() =>
                  void onSubmit(
                    action.key,
                    {},
                    item.intervention_fingerprint ?? undefined,
                  )
                }
              >
                {isPending ? <Loader2Icon className="animate-spin" /> : null}
                {getActionLabel(item, action.key, action.label)}
              </Button>
            ))}
          </div>
        </section>
      ) : null}

      {inputActions.length > 0 ? (
        <section className="space-y-3 rounded-xl border border-border/70 bg-background/90 p-4">
          {inputActions.map((action) => {
            const draftValue = normalizeText(drafts[action.key]);
            return (
              <div key={action.key} className="space-y-2">
                <Textarea
                  value={drafts[action.key] ?? ""}
                  placeholder={
                    display?.respond_placeholder ??
                    action.placeholder ??
                    action.label
                  }
                  rows={3}
                  className="min-h-0"
                  onChange={(event) =>
                    setDrafts((current) => ({
                      ...current,
                      [action.key]: event.target.value,
                    }))
                  }
                />
                <Button
                  type="button"
                  disabled={isPending || !draftValue}
                  onClick={() =>
                    void onSubmit(
                      action.key,
                      {
                        text: draftValue,
                        comment: draftValue,
                      },
                      item.intervention_fingerprint ?? undefined,
                    )
                  }
                >
                  {isPending ? <Loader2Icon className="animate-spin" /> : null}
                  {getActionLabel(item, action.key, action.label)}
                </Button>
              </div>
            );
          })}
        </section>
      ) : null}

      {singleSelectActions.length > 0 ? (
        <section className="space-y-4 rounded-xl border border-border/70 bg-background/90 p-4">
          {singleSelectActions.map((action) => {
            const options = getActionOptions(action);
            const selectedValue = selectedValues[action.key] ?? "";
            const customValue = normalizeText(customValues[action.key]);
            const effectiveValue = customValue || normalizeText(selectedValue);

            return (
              <div key={action.key} className="space-y-3">
                <div className="text-sm font-semibold text-foreground">
                  {getActionLabel(item, action.key, action.label)}
                </div>
                <div className="space-y-2">
                  {options.map((option) => {
                    const checked =
                      !customValue && selectedValue === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        className={cn(
                          "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                          checked
                            ? "border-primary bg-primary/5"
                            : "border-border/70 bg-background hover:bg-muted/40",
                        )}
                        onClick={() => {
                          setSelectedValues((current) => ({
                            ...current,
                            [action.key]: option.value,
                          }));
                          setCustomValues((current) => ({
                            ...current,
                            [action.key]: "",
                          }));
                        }}
                      >
                        <span className="mt-0.5 shrink-0 text-primary">
                          {checked ? (
                            <CircleDotIcon className="size-4" />
                          ) : (
                            <CircleIcon className="size-4" />
                          )}
                        </span>
                        <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
                          {renderOptionLabel(option)}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <Input
                  value={customValues[action.key] ?? ""}
                  placeholder={action.placeholder ?? action.label}
                  onChange={(event) => {
                    const nextValue = event.target.value;
                    setCustomValues((current) => ({
                      ...current,
                      [action.key]: nextValue,
                    }));
                    if (normalizeText(nextValue)) {
                      setSelectedValues((current) => ({
                        ...current,
                        [action.key]: "",
                      }));
                    }
                  }}
                />
                <Button
                  type="button"
                  disabled={isPending || !effectiveValue}
                  onClick={() =>
                    void onSubmit(
                      action.key,
                      {
                        selected: effectiveValue,
                        custom: Boolean(customValue),
                        custom_text: customValue || undefined,
                      },
                      item.intervention_fingerprint ?? undefined,
                    )
                  }
                >
                  {isPending ? <Loader2Icon className="animate-spin" /> : null}
                  {getActionLabel(item, action.key, action.label)}
                </Button>
              </div>
            );
          })}
        </section>
      ) : null}

      {multiSelectActions.length > 0 ? (
        <section className="space-y-4 rounded-xl border border-border/70 bg-background/90 p-4">
          {multiSelectActions.map((action) => {
            const options = getActionOptions(action);
            const selectedValue = multiSelectedValues[action.key] ?? [];
            const customValue = customValues[action.key] ?? "";
            const parsedCustomValues = parseGovernanceCustomValues(customValue);
            const mergedValue = Array.from(
              new Set([...selectedValue, ...parsedCustomValues]),
            );
            const minSelect = action.min_select ?? (action.required ? 1 : 0);
            const maxSelect = action.max_select;
            const disabled =
              isPending ||
              mergedValue.length < minSelect ||
              (typeof maxSelect === "number" && mergedValue.length > maxSelect);

            return (
              <div key={action.key} className="space-y-3">
                <div className="text-sm font-semibold text-foreground">
                  {getActionLabel(item, action.key, action.label)}
                </div>
                <div className="space-y-2">
                  {options.map((option) => {
                    const checked = selectedValue.includes(option.value);
                    return (
                      <button
                        key={option.value}
                        type="button"
                        className={cn(
                          "flex w-full items-start gap-3 rounded-xl border px-3 py-3 text-left transition-colors",
                          checked
                            ? "border-primary bg-primary/5"
                            : "border-border/70 bg-background hover:bg-muted/40",
                        )}
                        onClick={() => {
                          setMultiSelectedValues((current) => {
                            const existingValues = current[action.key] ?? [];
                            const nextValues = existingValues.includes(option.value)
                              ? existingValues.filter(
                                  (value) => value !== option.value,
                                )
                              : [...existingValues, option.value];
                            return {
                              ...current,
                              [action.key]: nextValues,
                            };
                          });
                        }}
                      >
                        <span className="mt-0.5 shrink-0 text-primary">
                          {checked ? (
                            <CheckSquareIcon className="size-4" />
                          ) : (
                            <SquareIcon className="size-4" />
                          )}
                        </span>
                        <span className="min-w-0 flex-1 text-sm font-medium text-foreground">
                          {renderOptionLabel(option)}
                        </span>
                      </button>
                    );
                  })}
                </div>
                <Textarea
                  value={customValue}
                  placeholder={action.placeholder ?? action.label}
                  rows={3}
                  className="min-h-0"
                  onChange={(event) =>
                    setCustomValues((current) => ({
                      ...current,
                      [action.key]: event.target.value,
                    }))
                  }
                />
                {mergedValue.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {mergedValue.map((value) => (
                      <span
                        key={value}
                        className="rounded-full border border-border/70 bg-background px-2.5 py-1 text-xs text-foreground/80"
                      >
                        {value}
                      </span>
                    ))}
                  </div>
                ) : null}
                <Button
                  type="button"
                  disabled={disabled}
                  onClick={() =>
                    void onSubmit(
                      action.key,
                      {
                        selected: mergedValue,
                        custom: parsedCustomValues.length > 0,
                        custom_text: normalizeText(customValue) || undefined,
                        custom_values:
                          parsedCustomValues.length > 0
                            ? parsedCustomValues
                            : undefined,
                      },
                      item.intervention_fingerprint ?? undefined,
                    )
                  }
                >
                  {isPending ? <Loader2Icon className="animate-spin" /> : null}
                  {getActionLabel(item, action.key, action.label)}
                </Button>
              </div>
            );
          })}
        </section>
      ) : null}
    </div>
  );
}

import type { InterventionQuestion } from "@/core/threads";

export function parseGovernanceCustomValues(value: string) {
  return value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function buildGovernanceQuestionPayload(
  question: InterventionQuestion,
  drafts: Record<string, string>,
  selectedValues: Record<string, string>,
  customValues: Record<string, string>,
  multiSelectedValues: Record<string, string[]>,
) {
  const draftValue = drafts[question.key]?.trim() ?? "";
  const selectedValue = selectedValues[question.key]?.trim() ?? "";
  const customValue = customValues[question.key]?.trim() ?? "";
  const selectedMultiValue = multiSelectedValues[question.key] ?? [];
  const customMultiValues = parseGovernanceCustomValues(
    customValues[question.key] ?? "",
  );
  const mergedMultiValues = Array.from(
    new Set([...selectedMultiValue, ...customMultiValues]),
  );

  if (question.kind === "confirm") {
    return { confirmed: true };
  }

  if (question.kind === "input") {
    if (!draftValue) {
      return null;
    }
    return {
      text: draftValue,
      comment: draftValue,
    };
  }

  if (question.kind === "select" || question.kind === "single_select") {
    const effectiveValue = customValue || selectedValue;
    if (!effectiveValue) {
      return null;
    }
    return {
      selected: effectiveValue,
      custom: Boolean(customValue),
      custom_text: customValue || undefined,
    };
  }

  if (question.kind === "multi_select") {
    const minSelect = question.min_select ?? (question.required ? 1 : 0);
    const maxSelect = question.max_select;
    if (mergedMultiValues.length < minSelect) {
      return null;
    }
    if (
      typeof maxSelect === "number" &&
      mergedMultiValues.length > maxSelect
    ) {
      return null;
    }
    return {
      selected: mergedMultiValues,
      custom: customMultiValues.length > 0,
      custom_text: customValue || undefined,
      custom_values: customMultiValues.length > 0 ? customMultiValues : undefined,
    };
  }

  return null;
}

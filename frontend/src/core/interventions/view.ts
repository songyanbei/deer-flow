import type { InterventionRequest } from "@/core/threads";

type InterventionDisplaySource = Pick<
  InterventionRequest,
  "title" | "reason" | "description" | "action_summary" | "display"
>;

function normalizeInterventionText(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }

  const text = value.trim();
  if (!text || /^[?锛焆+\s]+$/.test(text)) {
    return undefined;
  }
  return text;
}

function pickFirstText(values: unknown[]) {
  for (const value of values) {
    const text = normalizeInterventionText(value);
    if (text) {
      return text;
    }
  }
  return undefined;
}

export function getInterventionDisplayTitle(
  request: InterventionDisplaySource,
  fallback?: string,
) {
  return (
    pickFirstText([request.display?.title, request.title]) ??
    normalizeInterventionText(fallback)
  );
}

export function getInterventionDisplaySummary(
  request: InterventionDisplaySource,
) {
  return pickFirstText([
    request.display?.summary,
    request.reason,
    request.description,
    request.action_summary,
    request.display?.risk_tip,
  ]);
}

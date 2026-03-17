import type { Translations } from "@/core/i18n/locales/types";

function extractStatusToken(value: string | undefined) {
  const normalized = value?.trim();
  if (!normalized?.startsWith("@")) {
    return undefined;
  }

  return normalized.slice(1);
}

export function localizeStatusDetail(
  value: string | undefined,
  t: Translations,
): string | undefined {
  const token = extractStatusToken(value);
  if (!token) {
    return undefined;
  }

  const statusDetail = t.subtasks.statusDetail;
  if (!statusDetail) {
    return undefined;
  }

  if (token === "task_started") {
    return statusDetail.taskStarted;
  }
  if (token === "dispatching") {
    return statusDetail.dispatching;
  }
  if (token === "waiting_dependency") {
    return statusDetail.waitingDependency;
  }
  if (token === "waiting_clarification") {
    return statusDetail.waitingClarification;
  }
  if (token === "completed") {
    return statusDetail.completed;
  }
  if (token === "failed") {
    return statusDetail.failed;
  }
  if (token === "dependency_resolved") {
    return statusDetail.dependencyResolved;
  }

  const assignedMatch = token.match(/^assigned:(.+)$/);
  if (assignedMatch) {
    const agent = assignedMatch[1]?.trim();
    if (agent) {
      return statusDetail.assigned(agent);
    }
  }

  const waitingHelperMatch = token.match(/^waiting_helper:(.+)$/);
  if (waitingHelperMatch) {
    const agent = waitingHelperMatch[1]?.trim();
    if (agent) {
      return statusDetail.waitingHelper(agent);
    }
  }

  const retryingHelperMatch = token.match(/^retrying_helper:(.+)$/);
  if (retryingHelperMatch) {
    const agent = retryingHelperMatch[1]?.trim();
    if (agent) {
      return statusDetail.retryingHelper(agent);
    }
  }

  return undefined;
}

import { getBackendBaseURL } from "@/core/config";
import type { GovernanceItem } from "@/core/governance/types";
import {
  getInterventionDisplaySummary,
  getInterventionDisplayTitle,
} from "@/core/interventions/view";
import type { LocalSettings } from "@/core/settings";
import type { InterventionQuestion } from "@/core/threads";

export type GovernanceItemKind =
  | "clarification"
  | "dependency"
  | "approval"
  | "review";

export type GovernanceActionTarget = "console" | "thread" | "history";

function normalizeGovernanceSignal(value: unknown) {
  if (typeof value !== "string") {
    return "";
  }

  return value.trim().toLowerCase();
}

function collectGovernanceSignals(item: GovernanceItem) {
  return [
    item.category,
    item.hook_name,
    item.action_summary,
    item.reason,
    item.intervention_title,
    item.intervention_display?.title,
    item.intervention_display?.summary,
    item.intervention_display?.risk_tip,
  ]
    .map((value) => normalizeGovernanceSignal(value))
    .filter(Boolean)
    .join(" ");
}

export function getGovernanceDisplayTitle(item: GovernanceItem) {
  return getInterventionDisplayTitle({
    title: item.intervention_title ?? "",
    reason: item.reason ?? "",
    description: undefined,
    action_summary: item.action_summary ?? undefined,
    display: item.intervention_display ?? undefined,
  });
}

export function getGovernanceDisplaySummary(item: GovernanceItem) {
  return getInterventionDisplaySummary({
    title: item.intervention_title ?? "",
    reason: item.reason ?? "",
    description: undefined,
    action_summary: item.action_summary ?? undefined,
    display: item.intervention_display ?? undefined,
  });
}

export function getGovernanceItemKind(item: GovernanceItem): GovernanceItemKind {
  const signals = collectGovernanceSignals(item);

  if (signals.includes("clarification")) {
    return "clarification";
  }

  if (
    signals.includes("dependency") ||
    signals.includes("help request") ||
    signals.includes("waiting_dependency")
  ) {
    return "dependency";
  }

  if (
    item.decision === "require_intervention" ||
    item.status === "pending_intervention" ||
    (item.intervention_action_schema?.actions.length ?? 0) > 0
  ) {
    return "approval";
  }

  return "review";
}

export function getGovernanceActionTarget(
  item: GovernanceItem,
): GovernanceActionTarget {
  if (item.status !== "pending_intervention") {
    return "history";
  }

  if ((item.intervention_action_schema?.actions.length ?? 0) > 0) {
    return "console";
  }

  return "thread";
}

export function getGovernanceQuestions(item: GovernanceItem) {
  const questions = item.metadata?.intervention_questions;
  return Array.isArray(questions) ? questions : [];
}

export function getGovernanceThreadAgentName(item: GovernanceItem) {
  const agentName = item.metadata?.hook_metadata?.agent_name;
  if (typeof agentName !== "string") {
    return undefined;
  }

  const normalizedAgentName = agentName.trim();
  return normalizedAgentName || undefined;
}

export function pathOfGovernanceThread(item: GovernanceItem) {
  const agentName = getGovernanceThreadAgentName(item);
  if (agentName) {
    return `/workspace/agents/${agentName}/chats/${item.thread_id}`;
  }
  return `/workspace/chats/${item.thread_id}`;
}

export function toGovernanceFilterStartISO(value?: string) {
  const normalizedValue = value?.trim();
  if (!normalizedValue) {
    return undefined;
  }

  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(normalizedValue);
  if (!match) {
    return undefined;
  }
  const [, year, month, day] = match;
  const date = new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    0,
    0,
    0,
    0,
  );
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

export function toGovernanceFilterEndISO(value?: string) {
  const normalizedValue = value?.trim();
  if (!normalizedValue) {
    return undefined;
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(normalizedValue);
  if (!match) {
    return undefined;
  }
  const [, year, month, day] = match;
  const date = new Date(
    Number(year),
    Number(month) - 1,
    Number(day),
    23,
    59,
    59,
    999,
  );
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
}

/**
 * Body shape accepted by `POST /api/runtime/threads/{id}/governance:resume`.
 *
 * Gateway pins this contract with `AppRuntimeContext.extra="forbid"` — any
 * identity-bearing field (tenant_id/user_id/thread_context/auth_user/…) or
 * unknown key under `app_context` triggers a 422. The browser only sends
 * safe app-level flags derived from local settings; routing (group_key /
 * allowed_agents / agent_name / requested_orchestration_mode) and the
 * streamMode / streamSubgraphs / streamResumable / recursion_limit knobs
 * are server-owned.
 */
export type GovernanceResumeRequestBody = {
  message: string;
  governance_id: string;
  workflow_resume_run_id?: string;
  workflow_resume_task_id?: string;
  app_context?: {
    thinking_enabled?: boolean;
    is_plan_mode?: boolean;
    subagent_enabled?: boolean;
  };
};

export type GovernanceResumeRequest = {
  threadId: string;
  body: GovernanceResumeRequestBody;
};

export function buildGovernanceResumeRequest(
  item: GovernanceItem,
  settings: LocalSettings["context"],
  resumeMessage: string,
): GovernanceResumeRequest {
  const normalizedResumeMessage = resumeMessage.trim();
  const body: GovernanceResumeRequestBody = {
    message: normalizedResumeMessage,
    governance_id: item.governance_id,
    app_context: {
      thinking_enabled: settings.mode !== "flash",
      is_plan_mode: settings.mode === "pro" || settings.mode === "ultra",
      subagent_enabled: settings.mode === "ultra",
    },
  };
  if (item.run_id) {
    body.workflow_resume_run_id = item.run_id;
  }
  if (item.task_id) {
    body.workflow_resume_task_id = item.task_id;
  }

  return {
    threadId: item.thread_id,
    body,
  };
}

/**
 * Submit a governance resume through the Gateway (Phase 2.2).
 *
 * Replaces the legacy direct `client.runs.create(...)` call against the
 * LangGraph SDK. The Gateway validates the governance ledger, constructs
 * the trusted LangGraph `context`, and forwards to the upstream run.
 *
 * The response is a long-lived SSE stream shared with `messages:stream` /
 * `resume`. This helper is fire-and-forget: we wait for the HTTP ack,
 * surface non-2xx as an error, then cancel the body so the live subscriber
 * (`useThreadStream`) handles the event stream.
 */
export async function resumeGovernanceThread(
  item: GovernanceItem,
  settings: LocalSettings["context"],
  resumeMessage: string,
) {
  const request = buildGovernanceResumeRequest(item, settings, resumeMessage);
  const response = await fetch(
    `${getBackendBaseURL()}/api/runtime/threads/${encodeURIComponent(
      request.threadId,
    )}/governance:resume`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      credentials: "include",
      body: JSON.stringify(request.body),
    },
  );
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Failed to resume governance thread (${response.status})${
        detail ? `: ${detail}` : ""
      }`,
    );
  }
  // Fire-and-forget — the live thread subscriber consumes the SSE stream.
  void response.body?.cancel().catch(() => undefined);
}

export function isRenderableGovernanceQuestion(
  question: InterventionQuestion,
) {
  return (
    question.kind === "confirm" ||
    question.kind === "input" ||
    question.kind === "select" ||
    question.kind === "single_select" ||
    question.kind === "multi_select"
  );
}

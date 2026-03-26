import type { StreamMode } from "@langchain/langgraph-sdk";

import { getAPIClient } from "@/core/api";
import type { GovernanceItem } from "@/core/governance/types";
import {
  getInterventionDisplaySummary,
  getInterventionDisplayTitle,
} from "@/core/interventions/view";
import type { LocalSettings } from "@/core/settings";
import type { InterventionQuestion } from "@/core/threads";

type RunsCreatePayload = Parameters<
  ReturnType<typeof getAPIClient>["runs"]["create"]
>[2];

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

export function buildGovernanceResumeRequest(
  item: GovernanceItem,
  settings: LocalSettings["context"],
  resumeMessage: string,
) {
  const agentName = getGovernanceThreadAgentName(item);
  const normalizedResumeMessage = resumeMessage.trim();
  const streamMode: StreamMode[] = ["values", "messages-tuple", "custom"];
  const payload: RunsCreatePayload = {
    input: {
      messages: [
        {
          type: "human",
          content: [
            {
              type: "text",
              text: normalizedResumeMessage,
            },
          ],
        },
      ],
    },
    streamMode,
    streamSubgraphs: settings.requested_orchestration_mode !== "workflow",
    streamResumable: true,
    config: {
      recursion_limit: 1000,
    },
    context: {
      ...settings,
      ...(agentName ? { agent_name: agentName } : {}),
      thinking_enabled: settings.mode !== "flash",
      is_plan_mode: settings.mode === "pro" || settings.mode === "ultra",
      subagent_enabled: settings.mode === "ultra",
      thread_id: item.thread_id,
      workflow_clarification_resume: true,
      workflow_resume_run_id: item.run_id || undefined,
      workflow_resume_task_id: item.task_id || undefined,
    },
  };

  return {
    threadId: item.thread_id,
    assistantId: "entry_graph" as const,
    payload,
  };
}

export async function resumeGovernanceThread(
  item: GovernanceItem,
  settings: LocalSettings["context"],
  resumeMessage: string,
) {
  const client = getAPIClient();
  const request = buildGovernanceResumeRequest(item, settings, resumeMessage);
  await client.runs.create(
    request.threadId,
    request.assistantId,
    request.payload,
  );
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

import type { AIMessage, Message } from "@langchain/langgraph-sdk";

import { extractTextFromMessage } from "../messages/utils";
import type { ThreadTaskState } from "../threads/types";

import type { TaskStatus, TaskUpsert, TaskViewModel } from "./types";

type LegacyTaskToolCall = {
  id?: string;
  name?: string;
  args?: Record<string, unknown>;
};

type BaseTaskEvent = {
  type:
    | "task_started"
    | "task_running"
    | "task_waiting_intervention"
    | "task_waiting_dependency"
    | "task_help_requested"
    | "task_resumed"
    | "task_completed"
    | "task_failed"
    | "task_timed_out";
  task_id: string;
  message?: AIMessage | string;
  result?: string;
  error?: string;
};

export type MultiAgentTaskEvent = BaseTaskEvent & {
  source: "multi_agent";
  run_id?: string;
  agent_name?: string;
  description?: string;
  parent_task_id?: string;
  requested_by_agent?: string;
  request_help?: {
    problem: string;
    required_capability: string;
    reason: string;
    expected_output: string;
    context_payload?: Record<string, unknown> | null;
    candidate_agents?: string[] | null;
  };
  resolved_inputs?: Record<string, unknown>;
  blocked_reason?: string;
  resume_count?: number;
  status?: string;
  status_detail?: string;
  clarification_prompt?: string;
  intervention_request?: ThreadTaskState["intervention_request"];
  intervention_status?: ThreadTaskState["intervention_status"];
  intervention_fingerprint?: string;
};

export type LegacyTaskEvent = BaseTaskEvent & {
  source?: "legacy_subagent";
};

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function mapThreadTaskStatus(
  status: ThreadTaskState["status"],
  clarificationPrompt?: string | null,
): TaskStatus {
  if (status === "PENDING") {
    return "pending";
  }
  if (status === "WAITING_DEPENDENCY") {
    return "waiting_dependency";
  }
  if (status === "WAITING_INTERVENTION") {
    return "waiting_intervention";
  }
  if (status === "RUNNING" && clarificationPrompt) {
    return "waiting_clarification";
  }
  if (status === "RUNNING") {
    return "in_progress";
  }
  if (status === "DONE") {
    return "completed";
  }
  return "failed";
}

function mapEventStatus(event: MultiAgentTaskEvent): TaskStatus {
  if (event.status === "waiting_clarification") {
    return "waiting_clarification";
  }
  if (
    event.type === "task_waiting_intervention" ||
    event.status === "waiting_intervention"
  ) {
    return "waiting_intervention";
  }
  if (
    event.type === "task_waiting_dependency" ||
    event.type === "task_help_requested" ||
    event.status === "waiting_dependency"
  ) {
    return "waiting_dependency";
  }
  if (event.type === "task_completed") {
    return "completed";
  }
  if (event.type === "task_failed") {
    return "failed";
  }
  return "in_progress";
}

function getTaskUpdateText(message?: AIMessage | string): string | undefined {
  return typeof message === "string" && message.trim() ? message : undefined;
}

export function fromLegacyTaskToolCall(
  toolCall: LegacyTaskToolCall,
  threadId?: string,
): TaskViewModel | null {
  if (toolCall.name !== "task" || !toolCall.id) {
    return null;
  }

  return {
    id: toolCall.id,
    source: "legacy_subagent",
    threadId,
    subagentType: asString(toolCall.args?.subagent_type),
    description: asString(toolCall.args?.description),
    prompt: asString(toolCall.args?.prompt),
    status: "in_progress",
  };
}

export function fromLegacyToolMessage(
  message: Message,
): TaskUpsert | null {
  if (message.type !== "tool" || !message.tool_call_id) {
    return null;
  }

  const result = extractTextFromMessage(message);
  if (result.startsWith("Task Succeeded. Result:")) {
    return {
      id: message.tool_call_id,
      source: "legacy_subagent",
      status: "completed",
      result: result.split("Task Succeeded. Result:")[1]?.trim(),
      latestUpdate: undefined,
    };
  }

  if (result.startsWith("Task failed.")) {
    return {
      id: message.tool_call_id,
      source: "legacy_subagent",
      status: "failed",
      error: result.split("Task failed.")[1]?.trim(),
      latestUpdate: undefined,
    };
  }

  if (result.startsWith("Task timed out")) {
    return {
      id: message.tool_call_id,
      source: "legacy_subagent",
      status: "failed",
      error: result,
      latestUpdate: undefined,
    };
  }

  return {
    id: message.tool_call_id,
    source: "legacy_subagent",
    status: "in_progress",
  };
}

export function fromMultiAgentTaskState(
  task: ThreadTaskState,
  threadId?: string,
): TaskViewModel {
  const status = mapThreadTaskStatus(task.status, task.clarification_prompt);
  return {
    id: task.task_id,
    source: "multi_agent",
    runId: task.run_id ?? undefined,
    threadId,
    description: task.description,
    prompt: task.description,
    agentName: task.assigned_agent ?? undefined,
    parentTaskId: task.parent_task_id ?? undefined,
    requestedByAgent: task.requested_by_agent ?? undefined,
    requestHelp: task.request_help
      ? {
          problem: task.request_help.problem,
          requiredCapability: task.request_help.required_capability,
          reason: task.request_help.reason,
          expectedOutput: task.request_help.expected_output,
          contextPayload: task.request_help.context_payload ?? undefined,
          candidateAgents: task.request_help.candidate_agents ?? undefined,
        }
      : undefined,
    resolvedInputs: task.resolved_inputs ?? undefined,
    blockedReason: task.blocked_reason ?? undefined,
    resumeCount: task.resume_count ?? undefined,
    subagentType: "domain-agent",
    status,
    statusDetail: task.status_detail ?? undefined,
    clarificationPrompt: task.clarification_prompt ?? undefined,
    interventionRequest: task.intervention_request ?? undefined,
    interventionStatus: task.intervention_status ?? undefined,
    interventionFingerprint: task.intervention_fingerprint ?? undefined,
    latestUpdate:
      task.intervention_request?.reason ??
      task.clarification_prompt ??
      task.status_detail ??
      undefined,
    result: task.result ?? undefined,
    error: task.error ?? undefined,
    updatedAt: task.updated_at ?? undefined,
  };
}

export function fromMultiAgentTaskEvent(
  event: MultiAgentTaskEvent,
  threadId?: string,
): TaskUpsert {
  return {
    id: event.task_id,
    source: "multi_agent",
    runId: event.run_id ?? undefined,
    threadId,
    description: event.description ?? "",
    prompt: event.description ?? "",
    agentName: event.agent_name ?? undefined,
    parentTaskId: event.parent_task_id ?? undefined,
    requestedByAgent: event.requested_by_agent ?? undefined,
    requestHelp: event.request_help
      ? {
          problem: event.request_help.problem,
          requiredCapability: event.request_help.required_capability,
          reason: event.request_help.reason,
          expectedOutput: event.request_help.expected_output,
          contextPayload: event.request_help.context_payload ?? undefined,
          candidateAgents: event.request_help.candidate_agents ?? undefined,
        }
      : undefined,
    resolvedInputs: event.resolved_inputs ?? undefined,
    blockedReason: event.blocked_reason ?? undefined,
    resumeCount: event.resume_count ?? undefined,
    subagentType: "domain-agent",
    status: mapEventStatus(event),
    statusDetail: event.status_detail ?? getTaskUpdateText(event.message),
    clarificationPrompt: event.clarification_prompt ?? undefined,
    interventionRequest: event.intervention_request ?? undefined,
    interventionStatus: event.intervention_status ?? undefined,
    interventionFingerprint: event.intervention_fingerprint ?? undefined,
    latestMessage:
      typeof event.message === "object" && event.message !== null
        ? event.message
        : undefined,
    latestUpdate: getTaskUpdateText(event.message),
    result: event.result ?? undefined,
    error: event.error ?? undefined,
  };
}

export function fromLegacyTaskEvent(
  event: LegacyTaskEvent,
): TaskUpsert {
  return {
    id: event.task_id,
    source: "legacy_subagent",
    status:
      event.type === "task_completed"
        ? "completed"
        : event.type === "task_failed" || event.type === "task_timed_out"
          ? "failed"
          : "in_progress",
    latestMessage:
      typeof event.message === "object" && event.message !== null
        ? event.message
        : undefined,
    latestUpdate: getTaskUpdateText(event.message),
    result: event.result ?? undefined,
    error: event.error ?? undefined,
  };
}

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
  status?: string;
  status_detail?: string;
  clarification_prompt?: string;
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
    subagentType: "domain-agent",
    status,
    statusDetail: task.status_detail ?? undefined,
    clarificationPrompt: task.clarification_prompt ?? undefined,
    latestUpdate:
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
): TaskUpsert {
  return {
    id: event.task_id,
    source: "multi_agent",
    runId: event.run_id ?? undefined,
    description: event.description ?? "",
    prompt: event.description ?? "",
    agentName: event.agent_name ?? undefined,
    subagentType: "domain-agent",
    status: mapEventStatus(event),
    statusDetail: event.status_detail ?? getTaskUpdateText(event.message),
    clarificationPrompt: event.clarification_prompt ?? undefined,
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

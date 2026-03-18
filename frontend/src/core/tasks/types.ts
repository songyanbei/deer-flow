import type { AIMessage } from "@langchain/langgraph-sdk";

import type { ClarificationRequest, InterventionRequest } from "../threads/types";

export type TaskSource = "legacy_subagent" | "multi_agent";

export type TaskStatus =
  | "pending"
  | "in_progress"
  | "waiting_dependency"
  | "waiting_intervention"
  | "waiting_clarification"
  | "completed"
  | "failed";

export interface TaskViewModel {
  id: string;
  source: TaskSource;
  runId?: string;
  threadId?: string;
  description: string;
  prompt?: string;
  agentName?: string;
  parentTaskId?: string;
  requestedByAgent?: string;
  requestHelp?: {
    problem: string;
    requiredCapability: string;
    reason: string;
    expectedOutput: string;
    contextPayload?: Record<string, unknown>;
    candidateAgents?: string[];
  };
  resolvedInputs?: Record<string, unknown>;
  blockedReason?: string;
  resumeCount?: number;
  subagentType?: string;
  status: TaskStatus;
  statusDetail?: string;
  clarificationPrompt?: string;
  clarificationRequest?: ClarificationRequest;
  interventionRequest?: InterventionRequest;
  interventionStatus?: "pending" | "resolved" | "consumed" | "rejected";
  interventionFingerprint?: string;
  latestMessage?: AIMessage;
  latestUpdate?: string;
  result?: string;
  error?: string;
  createdAt?: string;
  updatedAt?: string;
}

export type TaskUpsert =
  | TaskViewModel
  | (Partial<TaskViewModel> & {
      id: string;
      source?: TaskSource;
    });

export type Subtask = TaskViewModel;

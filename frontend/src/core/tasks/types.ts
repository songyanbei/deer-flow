import type { AIMessage } from "@langchain/langgraph-sdk";

export type TaskSource = "legacy_subagent" | "multi_agent";

export type TaskStatus =
  | "pending"
  | "in_progress"
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
  subagentType?: string;
  status: TaskStatus;
  statusDetail?: string;
  clarificationPrompt?: string;
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

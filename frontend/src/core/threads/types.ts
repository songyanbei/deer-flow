import type { Message, Thread } from "@langchain/langgraph-sdk";

import type { Todo } from "../todos";

export interface ThreadTaskState {
  task_id: string;
  description: string;
  run_id?: string | null;
  assigned_agent?: string | null;
  status: "PENDING" | "RUNNING" | "DONE" | "FAILED";
  status_detail?: string | null;
  clarification_prompt?: string | null;
  updated_at?: string | null;
  result?: string | null;
  error?: string | null;
}

export interface AgentThreadState extends Record<string, unknown> {
  title: string;
  messages: Message[];
  artifacts: string[];
  todos?: Todo[];
  original_input?: string | null;
  run_id?: string | null;
  planner_goal?: string | null;
  task_pool?: ThreadTaskState[];
  verified_facts?: Record<string, unknown>;
  route_count?: number;
  execution_state?: string | null;
  final_result?: string | null;
}

export interface AgentThread extends Thread<AgentThreadState> {}

export interface AgentThreadContext extends Record<string, unknown> {
  thread_id: string;
  model_name: string | undefined;
  thinking_enabled: boolean;
  is_plan_mode: boolean;
  subagent_enabled: boolean;
  reasoning_effort?: "minimal" | "low" | "medium" | "high";
  agent_name?: string;
}

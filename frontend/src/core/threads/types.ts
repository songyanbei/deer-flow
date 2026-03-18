import type { Message, Thread } from "@langchain/langgraph-sdk";

import type { Todo } from "../todos";

export type RequestedOrchestrationMode = "auto" | "leader" | "workflow";
export type ResolvedOrchestrationMode = "leader" | "workflow";
export type WorkflowStage =
  | "queued"
  | "acknowledged"
  | "planning"
  | "routing"
  | "executing"
  | "summarizing";

export type InterventionResolutionBehavior =
  | "resume_current_task"
  | "fail_current_task"
  | "replan_from_resolution";

export type InterventionActionKind =
  | "button"
  | "input"
  | "select"
  | "composite"
  | "confirm"
  | "single_select"
  | "multi_select";

export interface InterventionOption {
  label: string;
  value: string;
  description?: string;
}

export interface InterventionQuestion {
  key: string;
  label: string;
  kind: InterventionActionKind;
  required?: boolean;
  placeholder?: string;
  description?: string;
  confirm_text?: string;
  options?: InterventionOption[];
  min_select?: number;
  max_select?: number;
  default_value?: unknown;
}

export interface InterventionActionSchema {
  actions: Array<{
    key: string;
    label: string;
    kind: InterventionActionKind;
    resolution_behavior: InterventionResolutionBehavior;
    payload_schema?: Record<string, unknown>;
    placeholder?: string;
    description?: string;
    confirm_text?: string;
    required?: boolean;
    options?: InterventionOption[];
    min_select?: number;
    max_select?: number;
    default_value?: unknown;
  }>;
}

export interface InterventionDisplay {
  title: string;
  summary?: string;
  sections?: Array<{
    title?: string;
    items: Array<{
      label: string;
      value: string;
    }>;
  }>;
  risk_tip?: string;
  primary_action_label?: string;
  secondary_action_label?: string;
  respond_action_label?: string;
  respond_placeholder?: string;
  debug?: {
    source_agent?: string;
    tool_name?: string;
    raw_args?: Record<string, unknown>;
  };
}

export interface InterventionRequest {
  request_id: string;
  fingerprint: string;
  intervention_type: string;
  title: string;
  reason: string;
  description?: string;
  source_agent: string;
  source_task_id: string;
  tool_name?: string;
  risk_level?: "medium" | "high" | "critical";
  category?: string;
  context?: Record<string, unknown>;
  action_summary?: string;
  questions?: InterventionQuestion[];
  display?: InterventionDisplay;
  action_schema: InterventionActionSchema;
  created_at: string;
}

export interface ClarificationQuestion {
  key: string;
  label: string;
  kind: "input";
  required?: boolean;
  placeholder?: string;
  help_text?: string;
}

export interface ClarificationRequest {
  title: string;
  description?: string;
  questions: ClarificationQuestion[];
}

export interface ThreadTaskState {
  task_id: string;
  description: string;
  run_id?: string | null;
  parent_task_id?: string | null;
  depends_on_task_ids?: string[] | null;
  assigned_agent?: string | null;
  requested_by_agent?: string | null;
  request_help?: {
    problem: string;
    required_capability: string;
    reason: string;
    expected_output: string;
    context_payload?: Record<string, unknown> | null;
    candidate_agents?: string[] | null;
  } | null;
  resolved_inputs?: Record<string, unknown> | null;
  blocked_reason?: string | null;
  resume_count?: number | null;
  help_depth?: number | null;
  status:
    | "PENDING"
    | "RUNNING"
    | "WAITING_DEPENDENCY"
    | "WAITING_INTERVENTION"
    | "DONE"
    | "FAILED";
  status_detail?: string | null;
  clarification_prompt?: string | null;
  clarification_request?: ClarificationRequest | null;
  intervention_request?: InterventionRequest | null;
  intervention_status?: "pending" | "resolved" | "consumed" | "rejected" | null;
  intervention_fingerprint?: string | null;
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
  requested_orchestration_mode?: RequestedOrchestrationMode | null;
  resolved_orchestration_mode?: ResolvedOrchestrationMode | null;
  orchestration_reason?: string | null;
  workflow_stage?: WorkflowStage | null;
  workflow_stage_detail?: string | null;
  workflow_stage_updated_at?: string | null;
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
  requested_orchestration_mode?: RequestedOrchestrationMode;
  agent_name?: string;
}

import type {
  InterventionActionSchema,
  InterventionDisplay,
  InterventionQuestion,
} from "@/core/threads";

export interface GovernanceHookMetadata {
  agent_name?: string;
  [key: string]: unknown;
}

export interface GovernanceMetadata {
  hook_metadata?: GovernanceHookMetadata;
  intervention_questions?: InterventionQuestion[];
  [key: string]: unknown;
}

export interface GovernanceItem {
  governance_id: string;
  thread_id: string;
  run_id: string;
  task_id: string;
  source_agent: string;
  hook_name: string;
  source_path: string;
  risk_level: string;
  category: string;
  decision: string;
  status: string;
  rule_id?: string | null;
  request_id?: string | null;
  action_summary?: string | null;
  reason?: string | null;
  metadata?: GovernanceMetadata | null;
  created_at: string;
  resolved_at?: string | null;
  resolved_by?: string | null;
  intervention_title?: string | null;
  intervention_tool_name?: string | null;
  intervention_display?: InterventionDisplay | null;
  intervention_action_schema?: InterventionActionSchema | null;
  intervention_fingerprint?: string | null;
}

export interface GovernanceListResponse {
  items: GovernanceItem[];
  total: number;
  limit: number;
  offset: number;
}

export interface GovernanceQueueFilters {
  threadId?: string;
  runId?: string;
  riskLevel?: string;
  sourceAgent?: string;
  createdFrom?: string;
  createdTo?: string;
  limit?: number;
  offset?: number;
}

export interface GovernanceHistoryFilters extends GovernanceQueueFilters {
  status?: string;
  resolvedFrom?: string;
  resolvedTo?: string;
}

export interface ResolveGovernancePayload {
  governanceId: string;
  actionKey: string;
  payload: Record<string, unknown>;
  fingerprint?: string;
}

export interface ResolveGovernanceResponse {
  ok: boolean;
  governance_id: string;
  status: string;
  resume_action?: "submit_resume" | null;
  resume_payload?: {
    message?: string;
    [key: string]: unknown;
  } | null;
}

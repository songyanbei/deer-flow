export interface Agent {
  name: string;
  description: string;
  model: string | null;
  tool_groups: string[] | null;
  requested_orchestration_mode?: "auto" | "leader" | "workflow" | null;
  soul?: string | null;
}

export interface CreateAgentRequest {
  name: string;
  description?: string;
  model?: string | null;
  tool_groups?: string[] | null;
  requested_orchestration_mode?: "auto" | "leader" | "workflow" | null;
  soul?: string;
}

export interface UpdateAgentRequest {
  description?: string | null;
  model?: string | null;
  tool_groups?: string[] | null;
  requested_orchestration_mode?: "auto" | "leader" | "workflow" | null;
  soul?: string | null;
}

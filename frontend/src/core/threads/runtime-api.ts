import { getBackendBaseURL } from "../config";

export type RuntimeThreadCreateResponse = {
  thread_id: string;
  portal_session_id: string;
  tenant_id: string;
  user_id: string;
  created_at: string;
};

/**
 * Create a Gateway-registered runtime thread.
 *
 * Phase 1 D1.1: main chat threads must be created through the Gateway so
 * `ThreadRegistry` has a binding before the first message stream. The Gateway
 * internally creates the upstream LangGraph thread and returns its id; the
 * browser never generates or passes identity fields.
 *
 * `portal_session_id` is intentionally omitted — the backend fills the
 * `deerflow-web:{thread_id}` default (α-scheme) for main chat.
 */
export async function createRuntimeThread(): Promise<RuntimeThreadCreateResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/runtime/threads`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({}),
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new Error(
      `Failed to create runtime thread (${response.status})${detail ? `: ${detail}` : ""}`,
    );
  }
  return (await response.json()) as RuntimeThreadCreateResponse;
}

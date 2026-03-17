import { getBackendBaseURL } from "@/core/config";

export type ResolveInterventionPayload = {
  threadId: string;
  requestId: string;
  fingerprint: string;
  actionKey: string;
  payload: Record<string, unknown>;
};

export async function resolveIntervention({
  threadId,
  requestId,
  fingerprint,
  actionKey,
  payload,
}: ResolveInterventionPayload) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/threads/${threadId}/interventions/${requestId}:resolve`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        fingerprint,
        action_key: actionKey,
        payload,
      }),
    },
  );

  let data: unknown = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }

  if (!response.ok) {
    const message =
      typeof data === "object" &&
      data !== null &&
      "detail" in data &&
      typeof data.detail === "string"
        ? data.detail
        : `Failed to resolve intervention (${response.status})`;
    const error = new Error(message) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }

  return data;
}

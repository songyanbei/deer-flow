import { getBackendBaseURL } from "@/core/config";

import type {
  GovernanceHistoryFilters,
  GovernanceItem,
  GovernanceListResponse,
  GovernanceQueueFilters,
  ResolveGovernancePayload,
  ResolveGovernanceResponse,
} from "./types";

function appendIfPresent(
  searchParams: URLSearchParams,
  key: string,
  value: string | number | undefined,
) {
  if (value === undefined) {
    return;
  }

  const normalizedValue =
    typeof value === "string" ? value.trim() : String(value);
  if (!normalizedValue) {
    return;
  }
  searchParams.set(key, normalizedValue);
}

function buildGovernanceListURL(
  path: string,
  filters: GovernanceQueueFilters | GovernanceHistoryFilters,
) {
  const searchParams = new URLSearchParams();

  appendIfPresent(searchParams, "thread_id", filters.threadId);
  appendIfPresent(searchParams, "run_id", filters.runId);
  appendIfPresent(searchParams, "risk_level", filters.riskLevel);
  appendIfPresent(searchParams, "source_agent", filters.sourceAgent);
  appendIfPresent(searchParams, "created_from", filters.createdFrom);
  appendIfPresent(searchParams, "created_to", filters.createdTo);
  appendIfPresent(searchParams, "limit", filters.limit);
  appendIfPresent(searchParams, "offset", filters.offset);
  if ("status" in filters) {
    appendIfPresent(searchParams, "status", filters.status);
    appendIfPresent(searchParams, "resolved_from", filters.resolvedFrom);
    appendIfPresent(searchParams, "resolved_to", filters.resolvedTo);
  }

  const suffix = searchParams.size > 0 ? `?${searchParams.toString()}` : "";
  return `${getBackendBaseURL()}${path}${suffix}`;
}

async function parseGovernanceResponse<T>(response: Response): Promise<T> {
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
        : `Governance request failed (${response.status})`;
    const error = new Error(message) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }

  return data as T;
}

export async function fetchGovernanceQueue(
  filters: GovernanceQueueFilters = {},
) {
  const response = await fetch(
    buildGovernanceListURL("/api/governance/queue", filters),
  );
  return parseGovernanceResponse<GovernanceListResponse>(response);
}

export async function fetchGovernanceHistory(
  filters: GovernanceHistoryFilters = {},
) {
  const response = await fetch(
    buildGovernanceListURL("/api/governance/history", filters),
  );
  return parseGovernanceResponse<GovernanceListResponse>(response);
}

export async function fetchGovernanceDetail(governanceId: string) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/governance/${governanceId}`,
  );
  return parseGovernanceResponse<GovernanceItem>(response);
}

export async function resolveGovernanceItem({
  governanceId,
  actionKey,
  payload,
  fingerprint,
}: ResolveGovernancePayload) {
  const response = await fetch(
    `${getBackendBaseURL()}/api/governance/${governanceId}:resolve`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        action_key: actionKey,
        payload,
        fingerprint,
      }),
    },
  );
  return parseGovernanceResponse<ResolveGovernanceResponse>(response);
}

import { useMutation, useQuery } from "@tanstack/react-query";

import {
  fetchGovernanceDetail,
  fetchGovernanceHistory,
  fetchGovernanceQueue,
  resolveGovernanceItem,
} from "./api";
import type { GovernanceHistoryFilters, GovernanceQueueFilters } from "./types";

export function useGovernanceQueue(filters: GovernanceQueueFilters) {
  return useQuery({
    queryKey: ["governance", "queue", filters],
    queryFn: () => fetchGovernanceQueue(filters),
  });
}

export function useGovernanceHistory(filters: GovernanceHistoryFilters) {
  return useQuery({
    queryKey: ["governance", "history", filters],
    queryFn: () => fetchGovernanceHistory(filters),
  });
}

export function useGovernanceDetail(governanceId: string | null) {
  return useQuery({
    queryKey: ["governance", "detail", governanceId],
    queryFn: () => fetchGovernanceDetail(governanceId!),
    enabled: typeof governanceId === "string" && governanceId.trim().length > 0,
  });
}

export function useResolveGovernanceItem() {
  return useMutation({
    mutationFn: resolveGovernanceItem,
  });
}

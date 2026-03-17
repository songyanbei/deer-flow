import { useMutation } from "@tanstack/react-query";

import { resolveIntervention } from "./api";

export function useResolveIntervention() {
  return useMutation({
    mutationFn: resolveIntervention,
  });
}

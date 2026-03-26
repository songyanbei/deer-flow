import { describe, expect, it } from "vitest";

import type { InterventionRequest } from "@/core/threads";

import {
  getInterventionDisplaySummary,
  getInterventionDisplayTitle,
} from "./view";

function createRequest(
  overrides: Partial<InterventionRequest> = {},
): InterventionRequest {
  return {
    request_id: "req-1",
    fingerprint: "fp-1",
    intervention_type: "approval",
    title: "Need approval",
    reason: "Please approve the risky action.",
    source_agent: "ops-agent",
    source_task_id: "task-1",
    action_schema: {
      actions: [],
    },
    created_at: "2026-03-26T10:00:00.000Z",
    ...overrides,
  };
}

describe("intervention display helpers", () => {
  it("prefers display title and summary when provided", () => {
    const request = createRequest({
      display: {
        title: "Confirm outbound email",
        summary: "An external email is ready to send.",
      },
    });

    expect(getInterventionDisplayTitle(request)).toBe("Confirm outbound email");
    expect(getInterventionDisplaySummary(request)).toBe(
      "An external email is ready to send.",
    );
  });

  it("falls back to protocol fields when display copy is absent", () => {
    const request = createRequest({
      description: "The action touches an external system.",
      action_summary: "Send the email after approval.",
      display: {
        title: "",
      },
    });

    expect(getInterventionDisplayTitle(request)).toBe("Need approval");
    expect(getInterventionDisplaySummary(request)).toBe(
      "Please approve the risky action.",
    );
  });

  it("falls back to risk tip only when no other readable summary exists", () => {
    const request = createRequest({
      reason: "",
      description: "",
      action_summary: "",
      display: {
        title: "Confirm privileged action",
        risk_tip: "This operation changes production data.",
      },
    });

    expect(getInterventionDisplaySummary(request)).toBe(
      "This operation changes production data.",
    );
  });
});

import { describe, expect, it } from "vitest";

import {
  buildGovernanceQuestionPayload,
  parseGovernanceCustomValues,
} from "./forms";

describe("governance form helpers", () => {
  it("builds input question payloads with text and comment", () => {
    expect(
      buildGovernanceQuestionPayload(
        {
          key: "notes",
          label: "Why is this safe?",
          kind: "input",
          required: true,
        },
        { notes: "Need additional operator context" },
        {},
        {},
        {},
      ),
    ).toEqual({
      text: "Need additional operator context",
      comment: "Need additional operator context",
    });
  });

  it("builds multi-select payloads with selected and custom values", () => {
    expect(
      buildGovernanceQuestionPayload(
        {
          key: "targets",
          label: "Which targets are in scope?",
          kind: "multi_select",
          required: true,
          min_select: 1,
        },
        {},
        {},
        { targets: "custom-a, custom-b" },
        { targets: ["existing"] },
      ),
    ).toEqual({
      selected: ["existing", "custom-a", "custom-b"],
      custom: true,
      custom_text: "custom-a, custom-b",
      custom_values: ["custom-a", "custom-b"],
    });
  });

  it("splits custom values on both newlines and Chinese commas", () => {
    expect(parseGovernanceCustomValues("alpha，beta\ngamma")).toEqual([
      "alpha",
      "beta",
      "gamma",
    ]);
  });
});

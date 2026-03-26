import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { GovernanceItem } from "@/core/governance";
import { I18nProvider } from "@/core/i18n/context";

import { GovernanceActionPanel } from "./governance-action-panel";

function createGovernanceItem(
  overrides: Partial<GovernanceItem> = {},
): GovernanceItem {
  return {
    governance_id: overrides.governance_id ?? "gov-1",
    thread_id: overrides.thread_id ?? "thread-1",
    run_id: overrides.run_id ?? "run-1",
    task_id: overrides.task_id ?? "task-1",
    source_agent: overrides.source_agent ?? "planner",
    hook_name: overrides.hook_name ?? "before_interrupt_emit",
    source_path: overrides.source_path ?? "workflow.router",
    risk_level: overrides.risk_level ?? "high",
    category: overrides.category ?? "approval",
    decision: overrides.decision ?? "require_intervention",
    status: overrides.status ?? "pending_intervention",
    created_at: overrides.created_at ?? "2026-03-26T09:00:00.000Z",
    action_summary: overrides.action_summary ?? "Review the next step",
    reason: overrides.reason ?? "Need approval before continuing",
    metadata: overrides.metadata ?? null,
    intervention_display: overrides.intervention_display ?? null,
    intervention_action_schema: overrides.intervention_action_schema ?? null,
    intervention_fingerprint: overrides.intervention_fingerprint ?? "fp-1",
    intervention_title: overrides.intervention_title ?? "Governance approval",
    intervention_tool_name: overrides.intervention_tool_name ?? "browser_click",
    request_id: overrides.request_id ?? "req-1",
    resolved_at: overrides.resolved_at ?? null,
    resolved_by: overrides.resolved_by ?? null,
    rule_id: overrides.rule_id ?? null,
  };
}

function renderPanel(
  item: GovernanceItem,
  onSubmit: ReturnType<typeof vi.fn>,
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      <I18nProvider initialLocale="en-US">
        <GovernanceActionPanel item={item} isPending={false} onSubmit={onSubmit} />
      </I18nProvider>,
    );
  });

  return {
    container,
    cleanup() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

describe("GovernanceActionPanel", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("submits plain button actions with an empty payload", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const rendered = renderPanel(
      createGovernanceItem({
        intervention_action_schema: {
          actions: [
            {
              key: "approve",
              label: "Approve",
              kind: "button",
              resolution_behavior: "resume_current_task",
            },
          ],
        },
      }),
      onSubmit,
    );

    const button = Array.from(rendered.container.querySelectorAll("button")).find(
      (element) => element.textContent?.includes("Approve"),
    );

    expect(button).not.toBeNull();

    await act(async () => {
      button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onSubmit).toHaveBeenCalledWith(
      "approve",
      {},
      "fp-1",
    );

    rendered.cleanup();
  });

  it("submits confirm actions with a confirmed payload", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const rendered = renderPanel(
      createGovernanceItem({
        intervention_action_schema: {
          actions: [
            {
              key: "confirm",
              label: "Confirm action",
              kind: "confirm",
              resolution_behavior: "resume_current_task",
            },
          ],
        },
      }),
      onSubmit,
    );

    const button = Array.from(rendered.container.querySelectorAll("button")).find(
      (element) => element.textContent?.includes("Confirm action"),
    );

    expect(button).not.toBeNull();

    await act(async () => {
      button!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(onSubmit).toHaveBeenCalledWith(
      "confirm",
      {
        confirmed: true,
      },
      "fp-1",
    );

    rendered.cleanup();
  });
});

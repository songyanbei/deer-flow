import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/core/i18n/context";
import {
  SubtasksProvider,
  useSubtaskContext,
} from "@/core/tasks/context";
import type { TaskViewModel } from "@/core/tasks/types";
import type { AgentThreadState } from "@/core/threads";

import { WorkflowFooterBar } from "./workflow-footer-bar";

vi.mock("./flip-display", () => ({
  FlipDisplay: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

function createTask(overrides: Partial<TaskViewModel>): TaskViewModel {
  return {
    id: overrides.id ?? "task-1",
    source: overrides.source ?? "multi_agent",
    runId: overrides.runId ?? "run-1",
    description: overrides.description ?? "Task",
    status: overrides.status ?? "pending",
    ...overrides,
  };
}

function renderWorkflowFooter(tasks: TaskViewModel[]) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  let context!: ReturnType<typeof useSubtaskContext>;

  const thread = {
    values: {
      resolved_orchestration_mode: "workflow",
      execution_state: "RUNNING",
      run_id: "run-1",
    } satisfies Partial<AgentThreadState>,
    isLoading: true,
  } as never;

  function Harness() {
    context = useSubtaskContext();
    return <WorkflowFooterBar thread={thread} />;
  }

  act(() => {
    root.render(
      <I18nProvider initialLocale="en-US">
        <SubtasksProvider>
          <Harness />
        </SubtasksProvider>
      </I18nProvider>,
    );
  });

  act(() => {
    tasks.forEach((task) => context.upsertTask(task));
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

describe("WorkflowFooterBar", () => {
  it("shows a compact progress summary and expands into a task list", () => {
    const rendered = renderWorkflowFooter([
      createTask({
        id: "task-1",
        status: "completed",
        description: "Confirm launch audience and release date",
      }),
      createTask({
        id: "task-2",
        status: "in_progress",
        description: "Draft the homepage announcement and launch email",
        statusDetail: "Writing copy and tightening the headline",
      }),
      createTask({
        id: "task-3",
        status: "pending",
        description: "Prepare the support FAQ",
      }),
    ]);

    expect(rendered.container.textContent).toContain("1 of 3 tasks completed");
    const trigger = rendered.container.querySelector("button");
    if (!trigger) {
      throw new Error("Workflow footer trigger not found.");
    }

    act(() => {
      trigger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(rendered.container.textContent).toContain(
      "Writing copy and tightening the headline",
    );
    expect(rendered.container.textContent).toContain("Prepare the support FAQ");

    rendered.cleanup();
  });
});

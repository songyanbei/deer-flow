import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/core/i18n/context";
import { SubtasksProvider, useSubtaskContext } from "@/core/tasks/context";
import type { TaskViewModel } from "@/core/tasks/types";
import type { AgentThreadState } from "@/core/threads";

import { WorkflowFooterBar } from "./workflow-footer-bar";

vi.mock("./flip-display", () => ({
  FlipDisplay: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock("./messages/intervention-card", () => ({
  InterventionCard: ({ task }: { task: TaskViewModel }) => (
    <div data-testid="footer-intervention-card">
      intervention:{task.interventionRequest?.title}
    </div>
  ),
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

function renderWorkflowFooter(
  tasks: TaskViewModel[],
  threadValues?: Partial<AgentThreadState>,
  isLoading = true,
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  let context!: ReturnType<typeof useSubtaskContext>;

  const thread = {
    values: {
      resolved_orchestration_mode: "workflow",
      execution_state: "RUNNING",
      run_id: "run-1",
      ...threadValues,
    } satisfies Partial<AgentThreadState>,
    isLoading,
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

    expect(rendered.container.textContent).toContain("1 of 3 done");
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

  it("shows a queued workflow shell before subtasks exist", () => {
    const rendered = renderWorkflowFooter([], {
      workflow_stage: "queued",
      workflow_stage_detail: "Book the meeting room",
    });

    expect(rendered.container.textContent).toContain(
      "Queued and waiting to start...",
    );

    const trigger = rendered.container.querySelector("button");
    if (!trigger) {
      throw new Error("Workflow footer trigger not found.");
    }

    act(() => {
      trigger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(rendered.container.textContent).toContain(
      "Queued and waiting to start...",
    );

    rendered.cleanup();
  });

  it("shows an acknowledged workflow shell immediately before subtasks exist", () => {
    const rendered = renderWorkflowFooter([], {
      workflow_stage: "acknowledged",
      workflow_stage_detail: "Prepare a workflow for the launch checklist",
    });

    expect(rendered.container.textContent).toContain(
      "Workflow started, understanding your request...",
    );

    const trigger = rendered.container.querySelector("button");
    if (!trigger) {
      throw new Error("Workflow footer trigger not found.");
    }

    act(() => {
      trigger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(rendered.container.textContent).toContain(
      "Workflow started, understanding your request...",
    );

    rendered.cleanup();
  });

  it("prioritizes intervention tasks in the compact summary", () => {
    const rendered = renderWorkflowFooter([
      createTask({
        id: "task-1",
        threadId: "thread-1",
        status: "waiting_intervention",
        description: "Approve sending the email",
        interventionRequest: {
          request_id: "req-1",
          fingerprint: "fp-1",
          intervention_type: "approval",
          title: "Need approval",
          reason: "Please approve the risky action.",
          display: {
            title: "Confirm outbound email",
            summary: "An external email is ready to send.",
          },
          source_agent: "ops-agent",
          source_task_id: "task-1",
          action_schema: { actions: [] },
          created_at: "2026-03-17T10:00:00.000Z",
        },
      }),
      createTask({
        id: "task-2",
        status: "in_progress",
        description: "Prepare the final email body",
      }),
    ]);

    expect(rendered.container.textContent).toContain("Approve sending the email");

    const trigger = rendered.container.querySelector("button");
    if (!trigger) {
      throw new Error("Workflow footer trigger not found.");
    }

    act(() => {
      trigger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(rendered.container.textContent).toContain(
      "An external email is ready to send.",
    );

    rendered.cleanup();
  });

  it("recovers a planning shell after refresh before subtasks hydrate", () => {
    const rendered = renderWorkflowFooter([], {
      workflow_stage: "planning",
      workflow_stage_detail: "Breaking the launch work into subtasks",
      execution_state: "PLANNING_DONE",
    });

    expect(rendered.container.textContent).toContain(
      "Understanding your request, planning steps",
    );

    const trigger = rendered.container.querySelector("button");
    if (!trigger) {
      throw new Error("Workflow footer trigger not found.");
    }

    act(() => {
      trigger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(rendered.container.textContent).toContain(
      "Understanding your request, planning steps",
    );

    rendered.cleanup();
  });

  it("keeps the routing stage visible until execution starts", () => {
    const rendered = renderWorkflowFooter(
      [
        createTask({
          id: "task-routing",
          status: "pending",
          description: "Reserve conference room A",
        }),
      ],
      {
        workflow_stage: "routing",
        workflow_stage_detail: "Reserve conference room A",
      },
    );

    expect(rendered.container.textContent).toContain(
      "Plan ready, dispatching subtasks...",
    );

    rendered.cleanup();
  });

  it("shows summarizing detail after the last task completes", () => {
    const rendered = renderWorkflowFooter(
      [
        createTask({
          id: "task-done",
          status: "completed",
          description: "Reserve conference room A",
          result: "Conference room A is booked",
        }),
      ],
      {
        workflow_stage: "summarizing",
        workflow_stage_detail: "Conference room A is booked",
        execution_state: "EXECUTING_DONE",
      },
    );

    expect(rendered.container.textContent).toContain(
      "Tasks done, summarizing results",
    );

    const trigger = rendered.container.querySelector("button");
    if (!trigger) {
      throw new Error("Workflow footer trigger not found.");
    }

    act(() => {
      trigger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(rendered.container.textContent).toContain(
      "Tasks done, summarizing results",
    );

    rendered.cleanup();
  });
});

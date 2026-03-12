import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";

import {
  SubtasksProvider,
  mergeHydratedTask,
  useSubtaskContext,
} from "./context";
import type { TaskViewModel } from "./types";

function renderWithProvider(
  onRender: (value: ReturnType<typeof useSubtaskContext>) => void,
) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  function Harness({ children }: { children?: ReactNode }) {
    const context = useSubtaskContext();
    onRender(context);
    return <>{children}</>;
  }

  act(() => {
    root.render(
      <SubtasksProvider>
        <Harness />
      </SubtasksProvider>,
    );
  });

  return {
    cleanup() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

function createTask(overrides: Partial<TaskViewModel>): TaskViewModel {
  return {
    id: overrides.id ?? "task-1",
    source: overrides.source ?? "multi_agent",
    description: overrides.description ?? "Task",
    status: overrides.status ?? "in_progress",
    ...overrides,
  };
}

describe("SubtasksProvider", () => {
  it("hydrates multi_agent tasks by merging persisted snapshots into live state", () => {
    let context!: ReturnType<typeof useSubtaskContext>;
    const rendered = renderWithProvider((value) => {
      context = value;
    });

    act(() => {
      context.upsertTask(
        createTask({
          id: "task-1",
          source: "multi_agent",
          runId: "run-1",
          latestUpdate: "Streaming step",
          statusDetail: "Streaming detail",
        }),
      );
    });

    act(() => {
      context.hydrateTasks(
        [
          createTask({
            id: "task-1",
            source: "multi_agent",
            runId: "run-1",
            status: "in_progress",
            statusDetail: "Persisted detail",
            updatedAt: "2026-03-09T10:00:00Z",
          }),
        ],
        { source: "multi_agent", runId: "run-1" },
      );
    });

    expect(context.tasksById["task-1"]?.latestUpdate).toBe("Streaming step");
    expect(context.tasksById["task-1"]?.statusDetail).toBe("Streaming detail");

    rendered.cleanup();
  });

  it("does not clear legacy tasks when resetting multi_agent tasks", () => {
    let context!: ReturnType<typeof useSubtaskContext>;
    const rendered = renderWithProvider((value) => {
      context = value;
    });

    act(() => {
      context.upsertTask(
        createTask({
          id: "legacy-1",
          source: "legacy_subagent",
          status: "in_progress",
        }),
      );
      context.upsertTask(
        createTask({
          id: "task-1",
          source: "multi_agent",
          runId: "run-1",
        }),
      );
    });

    act(() => {
      context.resetTasksBySource("multi_agent");
    });

    expect(context.tasksById["legacy-1"]?.source).toBe("legacy_subagent");
    expect(context.tasksById["task-1"]).toBeUndefined();

    rendered.cleanup();
  });

  it("replaces only the previous multi_agent run when a new run is hydrated", () => {
    let context!: ReturnType<typeof useSubtaskContext>;
    const rendered = renderWithProvider((value) => {
      context = value;
    });

    act(() => {
      context.upsertTask(
        createTask({
          id: "task-run-1",
          source: "multi_agent",
          runId: "run-1",
        }),
      );
      context.upsertTask(
        createTask({
          id: "legacy-1",
          source: "legacy_subagent",
        }),
      );
    });

    act(() => {
      context.resetTasksBySource("multi_agent");
      context.hydrateTasks(
        [
          createTask({
            id: "task-run-2",
            source: "multi_agent",
            runId: "run-2",
            status: "pending",
          }),
        ],
        { source: "multi_agent", runId: "run-2" },
      );
    });

    expect(context.tasksById["task-run-1"]).toBeUndefined();
    expect(context.tasksById["task-run-2"]?.runId).toBe("run-2");
    expect(context.tasksById["legacy-1"]?.source).toBe("legacy_subagent");

    rendered.cleanup();
  });

  it("preserves dependency metadata when a stale hydration snapshot omits it", () => {
    const merged = mergeHydratedTask(
      createTask({
        id: "task-1",
        source: "multi_agent",
        status: "waiting_dependency",
        requestedByAgent: "meeting-agent",
        blockedReason: "Need organizer openId",
        requestHelp: {
          problem: "Missing organizer openId",
          requiredCapability: "contact lookup",
          reason: "Meeting API requires an openId",
          expectedOutput: "Organizer openId and city",
        },
        resumeCount: 1,
        resolvedInputs: {
          "helper-1": {
            openId: "ou_123",
          },
        },
      }),
      createTask({
        id: "task-1",
        source: "multi_agent",
        status: "waiting_dependency",
        updatedAt: "2026-03-09T10:00:00Z",
      }),
    );

    expect(merged.requestedByAgent).toBe("meeting-agent");
    expect(merged.blockedReason).toBe("Need organizer openId");
    expect(merged.requestHelp?.requiredCapability).toBe("contact lookup");
    expect(merged.resumeCount).toBe(1);
    expect(merged.resolvedInputs).toEqual({
      "helper-1": {
        openId: "ou_123",
      },
    });
  });

  it("keeps a newer live status when hydration regresses to an older workflow state", () => {
    const merged = mergeHydratedTask(
      createTask({
        id: "task-1",
        source: "multi_agent",
        status: "waiting_clarification",
        clarificationPrompt: "Please choose the booking city.",
      }),
      createTask({
        id: "task-1",
        source: "multi_agent",
        status: "waiting_dependency",
        blockedReason: "Need city selection",
      }),
    );

    expect(merged.status).toBe("waiting_clarification");
    expect(merged.clarificationPrompt).toBe("Please choose the booking city.");
  });
});

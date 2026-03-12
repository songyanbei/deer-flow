import { describe, expect, it } from "vitest";

import type { Translations } from "@/core/i18n/locales/types";
import type { TaskViewModel } from "@/core/tasks/types";

import {
  filterWorkflowTasks,
  getWorkflowProgressSummary,
} from "./workflow-progress";

const t = {
  workflowStatus: {
    initializing: "Planning",
    planning: "Understanding your request, planning steps…",
    resuming: "Resuming previous progress…",
    processing: "Working on your request…",
    summarizing: "Tasks done, summarizing results…",
    waitingClarification: "Need more information from you",
    waitingDependency: "Waiting for a related task to finish…",
    running: (count: number) =>
      `Running ${count} subtask${count === 1 ? "" : "s"}`,
  },
} as Pick<Translations, "workflowStatus"> as Translations;

function createTask(overrides: Partial<TaskViewModel>): TaskViewModel {
  return {
    id: overrides.id ?? "task-1",
    source: overrides.source ?? "multi_agent",
    description: overrides.description ?? "Task",
    status: overrides.status ?? "pending",
    ...overrides,
  };
}

describe("workflow progress helpers", () => {
  it("filters workflow tasks by run id", () => {
    const tasks = filterWorkflowTasks(
      {
        "task-1": createTask({ id: "task-1", runId: "run-1" }),
        "task-2": createTask({ id: "task-2", runId: "run-2" }),
        legacy: createTask({ id: "legacy", source: "legacy_subagent" }),
      },
      ["task-1", "task-2", "legacy"],
      "run-1",
    );

    expect(tasks.map((task) => task.id)).toEqual(["task-1"]);
  });

  it("returns planning progress before tasks are hydrated", () => {
    const summary = getWorkflowProgressSummary({
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        execution_state: "PLANNING_DONE",
        planner_goal: "Compare three vendors and summarize tradeoffs",
      },
      tasks: [],
      t,
    });

    expect(summary).toEqual({
      title: "Understanding your request, planning steps…",
      detail: "Compare three vendors and summarize tradeoffs",
      activeTaskCount: 0,
      totalTaskCount: 0,
      isWaitingClarification: false,
    });
  });

  it("prefers live task updates while workflow is running", () => {
    const summary = getWorkflowProgressSummary({
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        execution_state: "RESUMING",
      },
      tasks: [
        createTask({
          id: "task-1",
          status: "in_progress",
          latestUpdate: "Dispatching task to domain agent",
        }),
        createTask({
          id: "task-2",
          status: "pending",
        }),
      ],
      t,
    });

    expect(summary?.title).toBe("Running 2 subtasks");
    expect(summary?.detail).toBe("Dispatching task to domain agent");
  });

  it("surfaces clarification state instead of a generic spinner label", () => {
    const summary = getWorkflowProgressSummary({
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "workflow",
      },
      tasks: [
        createTask({
          id: "task-1",
          status: "waiting_clarification",
          clarificationPrompt: "Which data source should I use?",
        }),
      ],
      t,
    });

    expect(summary?.title).toBe("Need more information from you");
    expect(summary?.detail).toBe("Which data source should I use?");
    expect(summary?.isWaitingClarification).toBe(true);
  });

  it("surfaces dependency waiting state with blocked reason", () => {
    const summary = getWorkflowProgressSummary({
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "workflow",
      },
      tasks: [
        createTask({
          id: "task-1",
          status: "waiting_dependency",
          blockedReason: "Need contact lookup for the meeting organizer",
        }),
      ],
      t,
    });

    expect(summary?.title).toBe("Waiting for a related task to finish…");
    expect(summary?.detail).toBe(
      "Need contact lookup for the meeting organizer",
    );
    expect(summary?.isWaitingClarification).toBe(false);
  });

  it("stays hidden for non-workflow streams", () => {
    const summary = getWorkflowProgressSummary({
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "leader",
      },
      tasks: [],
      t,
    });

    expect(summary).toBeNull();
  });
});

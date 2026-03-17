import { describe, expect, it } from "vitest";

import type { Translations } from "@/core/i18n/locales/types";
import type { TaskViewModel } from "@/core/tasks/types";

import {
  filterWorkflowTasks,
  getWorkflowProgressSummary,
} from "./workflow-progress";

const t = {
  subtasks: {
    statusDetail: {
      dispatching: "Dispatching task to domain agent",
    },
  },
  workflowStatus: {
    initializing: "Planning",
    queued: "Queued and waiting to start...",
    acknowledged: "Workflow started, understanding your request...",
    planning: "Understanding your request, planning steps…",
    routing: "Plan ready, dispatching subtasks...",
    resuming: "Resuming previous progress…",
    processing: "Working on your request…",
    executing: "Subtasks are underway...",
    summarizing: "Tasks done, summarizing results…",
    waitingIntervention: "Waiting for your decision",
    waitingClarification: "Need more information from you",
    waitingDependency: "Waiting for a related task to finish…",
    running: (count: number) =>
      `Running ${count} subtask${count === 1 ? "" : "s"}`,
    completedSummary: (completed: number, total: number) =>
      `${completed} of ${total} done`,
  },
} as unknown as Translations;

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
      isWaitingIntervention: false,
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
          statusDetail: "@dispatching",
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
    expect(summary?.isWaitingIntervention).toBe(false);
  });

  it("prioritizes intervention over other waiting states", () => {
    const summary = getWorkflowProgressSummary({
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "workflow",
      },
      tasks: [
        createTask({
          id: "task-1",
          status: "waiting_intervention",
          interventionRequest: {
            request_id: "req-1",
            fingerprint: "fp-1",
            intervention_type: "approval",
            title: "Need approval",
            reason: "Please approve the risky action.",
            source_agent: "ops-agent",
            source_task_id: "task-1",
            action_schema: { actions: [] },
            created_at: "2026-03-17T10:00:00.000Z",
          },
        }),
        createTask({
          id: "task-2",
          status: "waiting_dependency",
          blockedReason: "Need organizer info",
        }),
      ],
      t,
    });

    expect(summary?.title).toBe("Waiting for your decision");
    expect(summary?.detail).toBe("Please approve the risky action.");
    expect(summary?.isWaitingIntervention).toBe(true);
  });

  it("keeps the workflow shell title while using clarification as detail", () => {
    const summary = getWorkflowProgressSummary({
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        workflow_stage: "executing",
        workflow_stage_detail: "Running the active room booking task",
      },
      tasks: [
        createTask({
          id: "task-1",
          status: "waiting_clarification",
          clarificationPrompt: "Which room should I reserve?",
        }),
      ],
      t,
    });

    expect(summary?.title).toBe("Subtasks are underway...");
    expect(summary?.detail).toBe("Which room should I reserve?");
    expect(summary?.isWaitingClarification).toBe(true);
  });

  it("prefers active execution over queued-style shell when resuming after clarification", () => {
    const summary = getWorkflowProgressSummary({
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        execution_state: "RESUMING",
        workflow_stage: "queued",
        workflow_stage_detail: "Book the meeting room",
      },
      tasks: [
        createTask({
          id: "task-1",
          status: "in_progress",
          description: "Book the meeting room",
          statusDetail: "@dispatching",
        }),
      ],
      t,
    });

    expect(summary?.title).toBe("Running 1 subtask");
    expect(summary?.detail).toBe("Dispatching task to domain agent");
    expect(summary?.workflowStage).toBe("queued");
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
    expect(summary?.isWaitingIntervention).toBe(false);
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

import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskUpsert, TaskViewModel } from "../tasks/types";

import { useThreadStream } from "./hooks";

const useStreamMock = vi.fn();
const useQueryClientMock = vi.fn(() => ({
  invalidateQueries: vi.fn(),
}));
const hydrateTasksMock = vi.fn();
const resetTasksBySourceMock = vi.fn();
const upsertTaskMock = vi.fn<(task: TaskUpsert) => void>();
let mockedTasksById: Record<string, TaskViewModel> = {};
let mockedOrderedTaskIds: string[] = [];

function resetMockedTasks() {
  mockedTasksById = {};
  mockedOrderedTaskIds = [];
}

function upsertMockedTask(task: TaskUpsert) {
  const existing = mockedTasksById[task.id];
  const nextTask = {
    ...existing,
    ...task,
    id: task.id,
    source: task.source ?? existing?.source ?? "multi_agent",
    description: task.description ?? existing?.description ?? "",
    status: task.status ?? existing?.status ?? "pending",
  } as TaskViewModel;
  mockedTasksById = {
    ...mockedTasksById,
    [task.id]: nextTask,
  };
  if (!mockedOrderedTaskIds.includes(task.id)) {
    mockedOrderedTaskIds = [...mockedOrderedTaskIds, task.id];
  }
}

vi.mock("@langchain/langgraph-sdk/react", () => ({
  useStream: (...args: unknown[]) => useStreamMock(...args),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: vi.fn(),
  useQuery: vi.fn(),
  useQueryClient: () => useQueryClientMock(),
}));

vi.mock("../api", () => ({
  getAPIClient: vi.fn(() => ({})),
}));

vi.mock("../i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      uploads: {
        uploadingFiles: "Uploading files",
      },
    },
  }),
}));

vi.mock("../tasks/context", () => ({
  useSubtaskContext: () => ({
    tasksById: mockedTasksById,
    orderedTaskIds: mockedOrderedTaskIds,
  }),
  useTaskActions: () => ({
    hydrateTasks: hydrateTasksMock,
    resetTasksBySource: resetTasksBySourceMock,
    upsertTask: (task: TaskUpsert) => {
      upsertMockedTask(task);
      upsertTaskMock(task);
    },
  }),
}));

type HarnessProps = {
  assistantId: "entry_graph" | "lead_agent" | "multi_agent";
  threadValues?: Record<string, unknown>;
  isLoading?: boolean;
  requestedOrchestrationMode?: "auto" | "leader" | "workflow";
  submitImpl?: ReturnType<typeof vi.fn>;
};

let lastStreamOptions: Record<string, unknown> = {};
let latestThread: {
  values: Record<string, unknown>;
  submit?: ReturnType<typeof vi.fn>;
} | null = null;
let latestSendMessage:
  | ((threadId: string, message: { text: string; files: [] }) => Promise<void>)
  | null = null;

function renderHook(initialProps: HarnessProps) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  let currentProps = initialProps;

  function Harness() {
    const [thread, sendMessage] = useThreadStream({
      assistantId: currentProps.assistantId,
      threadId: "thread-1",
      context: {
        mode: "flash",
        requested_orchestration_mode:
          currentProps.requestedOrchestrationMode ?? "auto",
      },
    });
    latestThread = thread as unknown as {
      values: Record<string, unknown>;
      submit?: ReturnType<typeof vi.fn>;
    };
    latestSendMessage = sendMessage as typeof latestSendMessage;
    return null;
  }

  useStreamMock.mockImplementation((options: unknown) => {
    lastStreamOptions = options as Record<string, unknown>;
    const submit =
      currentProps.submitImpl ?? vi.fn().mockResolvedValue(undefined);
    return {
      messages: [],
      values: currentProps.threadValues ?? {},
      isLoading: currentProps.isLoading ?? false,
      isThreadLoading: false,
      submit,
      stop: vi.fn(),
    };
  });

  act(() => {
    root.render(<Harness />);
  });

  return {
    rerender(nextProps: HarnessProps) {
      currentProps = nextProps;
      act(() => {
        root.render(<Harness />);
      });
    },
    cleanup() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

describe("useThreadStream orchestration hydration", () => {
  beforeEach(() => {
    useStreamMock.mockReset();
    hydrateTasksMock.mockReset();
    resetTasksBySourceMock.mockReset();
    upsertTaskMock.mockReset();
    resetMockedTasks();
    lastStreamOptions = {};
    latestThread = null;
    latestSendMessage = null;
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("hydrates workflow state for entry_graph threads when resolved mode is workflow", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-1",
        task_pool: [
          {
            task_id: "task-1",
            description: "Task",
            status: "PENDING",
          },
        ],
      },
    });

    expect(lastStreamOptions.assistantId).toBe("entry_graph");
    expect(hydrateTasksMock).toHaveBeenCalledTimes(1);
    expect(hydrateTasksMock.mock.calls[0]?.[1]).toEqual({
      source: "multi_agent",
      runId: "run-1",
    });

    rendered.cleanup();
  });

  it("does not hydrate leader threads without task_pool and clears stale workflow state on mode change", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-1",
        task_pool: [
          {
            task_id: "task-1",
            description: "Task",
            status: "PENDING",
          },
        ],
      },
    });

    hydrateTasksMock.mockClear();
    resetTasksBySourceMock.mockClear();

    rendered.rerender({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "leader",
        run_id: null,
      },
    });

    expect(hydrateTasksMock).not.toHaveBeenCalled();
    expect(resetTasksBySourceMock).toHaveBeenCalledWith("multi_agent", "run-1");

    rendered.cleanup();
  });

  it("hydrates task_pool snapshots even before resolved mode is present", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        run_id: "run-2",
        task_pool: [
          {
            task_id: "task-2",
            description: "Recovered task",
            status: "RUNNING",
          },
        ],
      },
    });

    expect(hydrateTasksMock).toHaveBeenCalledTimes(1);
    expect(hydrateTasksMock.mock.calls[0]?.[1]).toEqual({
      source: "multi_agent",
      runId: "run-2",
    });

    rendered.cleanup();
  });

  it("syncs resolved mode and reason from custom events before values hydration catches up", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "task_running",
        source: "multi_agent",
        task_id: "task-3",
        run_id: "run-3",
        resolved_orchestration_mode: "workflow",
        orchestration_reason: "Detected multiple parallel subtasks.",
        description: "Parallel task",
        status: "waiting_clarification",
        status_detail: "Waiting for clarification",
        clarification_prompt: "Which dataset should I use?",
      });
    });

    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.orchestration_reason).toBe(
      "Detected multiple parallel subtasks.",
    );
    expect(latestThread?.values.run_id).toBe("run-3");
    expect(upsertTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "task-3",
        source: "multi_agent",
        status: "waiting_clarification",
        clarificationPrompt: "Which dataset should I use?",
        statusDetail: "Waiting for clarification",
      }),
    );

    rendered.rerender({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: null,
        orchestration_reason: null,
        run_id: null,
      },
      isLoading: false,
    });

    expect(latestThread?.values.resolved_orchestration_mode).toBeNull();
    expect(latestThread?.values.orchestration_reason).toBeNull();
    expect(latestThread?.values.run_id).toBeNull();

    rendered.cleanup();
  });

  it("syncs resolved mode and reason from non-task orchestration events", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "orchestration_mode_resolved",
        resolved_orchestration_mode: "workflow",
        orchestration_reason: "Agent default routed to workflow",
      });
    });

    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.orchestration_reason).toBe(
      "Agent default routed to workflow",
    );
    expect(upsertTaskMock).not.toHaveBeenCalled();

    rendered.cleanup();
  });

  it("preserves thread id on multi-agent intervention events so the card can submit resolutions", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "task_waiting_intervention",
        source: "multi_agent",
        task_id: "task-int-1",
        run_id: "run-int-1",
        description: "Approve the room booking",
        status: "waiting_intervention",
        intervention_fingerprint: "fp-1",
        intervention_status: "pending",
        intervention_request: {
          request_id: "req-1",
          fingerprint: "fp-1",
          intervention_type: "before_tool",
          title: "Need approval",
          reason: "This action will create a meeting",
          source_agent: "meeting-agent",
          source_task_id: "task-int-1",
          action_schema: {
            actions: [
              {
                key: "approve",
                label: "Approve",
                kind: "button",
                resolution_behavior: "resume_current_task",
              },
            ],
          },
          created_at: "2026-03-17T10:00:00.000Z",
        },
      });
    });

    expect(upsertTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "task-int-1",
        source: "multi_agent",
        threadId: "thread-1",
        status: "waiting_intervention",
      }),
    );

    rendered.cleanup();
  });

  it("syncs workflow stage from non-task stage events before values hydration catches up", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "workflow_stage_changed",
        run_id: "run-stage-1",
        workflow_stage: "planning",
        workflow_stage_detail: "Book the meeting room",
        workflow_stage_updated_at: "2026-03-13T10:00:00.000Z",
      });
    });

    expect(latestThread?.values.workflow_stage).toBe("planning");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book the meeting room",
    );
    expect(latestThread?.values.run_id).toBe("run-stage-1");

    rendered.rerender({
      assistantId: "entry_graph",
      threadValues: {
        workflow_stage: null,
        workflow_stage_detail: null,
        run_id: null,
      },
      isLoading: false,
    });

    expect(latestThread?.values.workflow_stage).toBeNull();
    expect(latestThread?.values.workflow_stage_detail).toBeNull();

    rendered.cleanup();
  });

  it("keeps workflow mode patched while loading if streamed values temporarily clear", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "task_running",
        source: "multi_agent",
        task_id: "task-4",
        run_id: "run-4",
        resolved_orchestration_mode: "workflow",
        orchestration_reason: "Run workflow subtasks in parallel.",
      });
    });

    rendered.rerender({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: null,
        orchestration_reason: null,
        run_id: null,
      },
      isLoading: true,
    });

    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.orchestration_reason).toBe(
      "Run workflow subtasks in parallel.",
    );
    expect(latestThread?.values.run_id).toBe("run-4");

    rendered.cleanup();
  });

  it("applies newer stage patches for the same run after hydration", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-5",
        workflow_stage: "planning",
        workflow_stage_detail: "Planning the room booking",
        workflow_stage_updated_at: "2026-03-13T10:00:00.000Z",
      },
      isLoading: false,
    });

    expect(latestThread?.values.workflow_stage).toBe("planning");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Planning the room booking",
    );

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "workflow_stage_changed",
        run_id: "run-5",
        workflow_stage: "routing",
        workflow_stage_detail: "Dispatching the room booking task",
        workflow_stage_updated_at: "2026-03-13T10:01:00.000Z",
      });
    });

    expect(latestThread?.values.workflow_stage).toBe("routing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Dispatching the room booking task",
    );
    expect(latestThread?.values.run_id).toBe("run-5");

    rendered.cleanup();
  });

  it("ignores out-of-order older stage patches for the same run", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-old-stage-1",
        workflow_stage: "routing",
        workflow_stage_detail: "Dispatching the task",
        workflow_stage_updated_at: "2026-03-13T10:05:00.000Z",
      },
      isLoading: false,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "workflow_stage_changed",
        run_id: "run-old-stage-1",
        workflow_stage: "planning",
        workflow_stage_detail: "Older planning update",
        workflow_stage_updated_at: "2026-03-13T10:04:00.000Z",
      });
    });

    expect(latestThread?.values.workflow_stage).toBe("routing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Dispatching the task",
    );
    expect(latestThread?.values.workflow_stage_updated_at).toBe(
      "2026-03-13T10:05:00.000Z",
    );

    rendered.cleanup();
  });

  it("transitions an optimistic acknowledged shell into backend queued for the same run", async () => {
    let resolveSubmit: (() => void) | undefined;
    const submitPromise = new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    });
    const submitImpl = vi.fn(() => submitPromise);
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      submitImpl,
      isLoading: true,
    });

    await act(async () => {
      void latestSendMessage?.("thread-1", {
        text: "Reserve the board room",
        files: [],
      });
      await Promise.resolve();
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "workflow_stage_changed",
        run_id: "run-queued-1",
        resolved_orchestration_mode: "workflow",
        workflow_stage: "queued",
        workflow_stage_detail: "Waiting for the workflow worker to start",
        workflow_stage_updated_at: "2026-03-13T10:04:00.000Z",
      });
    });

    expect(latestThread?.values.run_id).toBe("run-queued-1");
    expect(latestThread?.values.workflow_stage).toBe("queued");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Waiting for the workflow worker to start",
    );

    if (resolveSubmit) {
      resolveSubmit();
    }
    await act(async () => {
      await submitPromise;
    });

    rendered.cleanup();
  });

  it("replaces stale stage state when a newer run starts streaming", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-6",
        workflow_stage: "summarizing",
        workflow_stage_detail: "Conference room A is booked",
        workflow_stage_updated_at: "2026-03-13T10:02:00.000Z",
      },
      isLoading: false,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "workflow_stage_changed",
        run_id: "run-7",
        resolved_orchestration_mode: "workflow",
        workflow_stage: "acknowledged",
        workflow_stage_detail: "Book the next meeting room",
        workflow_stage_updated_at: "2026-03-13T10:03:00.000Z",
      });
    });

    expect(latestThread?.values.run_id).toBe("run-7");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book the next meeting room",
    );

    rendered.cleanup();
  });

  it("ignores late stage events from a run that was already replaced", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-6",
        workflow_stage: "summarizing",
        workflow_stage_detail: "Conference room A is booked",
        workflow_stage_updated_at: "2026-03-13T10:02:00.000Z",
      },
      isLoading: false,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "workflow_stage_changed",
        run_id: "run-7",
        resolved_orchestration_mode: "workflow",
        workflow_stage: "acknowledged",
        workflow_stage_detail: "Book conference room B",
        workflow_stage_updated_at: "2026-03-13T10:03:00.000Z",
      });
    });

    expect(latestThread?.values.run_id).toBe("run-7");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "workflow_stage_changed",
        run_id: "run-6",
        resolved_orchestration_mode: "workflow",
        workflow_stage: "summarizing",
        workflow_stage_detail: "Late old run event",
        workflow_stage_updated_at: "2026-03-13T10:04:00.000Z",
      });
    });

    expect(latestThread?.values.run_id).toBe("run-7");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book conference room B",
    );

    rendered.cleanup();
  });

  it("clears the previous run task scope before hydrating a new workflow run", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-task-1",
        task_pool: [
          {
            task_id: "task-1",
            description: "Old workflow task",
            status: "RUNNING",
          },
        ],
      },
      isLoading: false,
    });

    hydrateTasksMock.mockClear();
    resetTasksBySourceMock.mockClear();

    rendered.rerender({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-task-2",
        task_pool: [
          {
            task_id: "task-2",
            description: "New workflow task",
            status: "PENDING",
          },
        ],
      },
      isLoading: false,
    });

    expect(resetTasksBySourceMock).toHaveBeenCalledWith(
      "multi_agent",
      "run-task-1",
    );
    expect(hydrateTasksMock).toHaveBeenCalledTimes(1);
    expect(hydrateTasksMock.mock.calls[0]?.[1]).toEqual({
      source: "multi_agent",
      runId: "run-task-2",
    });

    rendered.cleanup();
  });

  it("recovers a terminal summarizing shell from thread state until the next run replaces it", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-summary-1",
        workflow_stage: "summarizing",
        workflow_stage_detail: "Conference room A is booked",
        workflow_stage_updated_at: "2026-03-13T10:06:00.000Z",
      },
      isLoading: false,
    });

    expect(latestThread?.values.workflow_stage).toBe("summarizing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Conference room A is booked",
    );

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "workflow_stage_changed",
        run_id: "run-summary-2",
        resolved_orchestration_mode: "workflow",
        workflow_stage: "acknowledged",
        workflow_stage_detail: "Book conference room B",
        workflow_stage_updated_at: "2026-03-13T10:07:00.000Z",
      });
    });

    expect(latestThread?.values.run_id).toBe("run-summary-2");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book conference room B",
    );

    rendered.cleanup();
  });

  it("clears the previous run stage when only the new run id has arrived", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-6",
        workflow_stage: "summarizing",
        workflow_stage_detail: "Conference room A is booked",
        workflow_stage_updated_at: "2026-03-13T10:02:00.000Z",
      },
      isLoading: false,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "orchestration_mode_resolved",
        run_id: "run-7",
        resolved_orchestration_mode: "workflow",
        orchestration_reason: "Structured task detected",
      });
    });

    expect(latestThread?.values.run_id).toBe("run-7");
    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.workflow_stage).toBeNull();
    expect(latestThread?.values.workflow_stage_detail).toBeNull();

    rendered.cleanup();
  });

  it("disables subgraph streaming for explicit workflow mode submissions", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
    });

    const submitMock = latestThread?.submit;
    if (!submitMock) {
      throw new Error("Expected submit mock to be available.");
    }

    await act(async () => {
      await latestSendMessage?.("thread-1", {
        text: "Plan this in workflow mode.",
        files: [],
      });
    });

    expect(submitMock).toHaveBeenCalledTimes(1);
    expect(submitMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        streamSubgraphs: false,
      }),
    );

    rendered.cleanup();
  });

  it("shows an optimistic workflow shell immediately for explicit workflow submissions", async () => {
    let resolveSubmit: (() => void) | undefined;
    const submitPromise = new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    });
    const submitImpl = vi.fn(() => submitPromise);
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      submitImpl,
    });

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestSendMessage?.("thread-1", {
        text: "Book the meeting room",
        files: [],
      });
      await Promise.resolve();
    });

    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book the meeting room",
    );

    if (resolveSubmit) {
      resolveSubmit();
    }
    await act(async () => {
      await sendPromise;
    });

    rendered.cleanup();
  });

  it("does not replace an active clarification resume with a fresh acknowledged shell", async () => {
    let resolveSubmit: (() => void) | undefined;
    const submitPromise = new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    });
    const submitImpl = vi.fn(() => submitPromise);
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      submitImpl,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-clarify-1",
        workflow_stage: "executing",
        workflow_stage_detail: "Booking the meeting room",
        execution_state: "INTERRUPTED",
        task_pool: [
          {
            task_id: "task-clarify-1",
            description: "Book the meeting room",
            run_id: "run-clarify-1",
            assigned_agent: "meeting-agent",
            status: "RUNNING",
            status_detail: "@waiting_clarification",
            clarification_prompt: "Which city should I use?",
          },
        ],
      },
    });

    await act(async () => {
      void latestSendMessage?.("thread-1", {
        text: "Shenzhen",
        files: [],
      });
      await Promise.resolve();
    });

    expect(latestThread?.values.workflow_stage).toBe("executing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Booking the meeting room",
    );
    expect(upsertTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "task-clarify-1",
        source: "multi_agent",
        runId: "run-clarify-1",
        status: "in_progress",
        clarificationPrompt: undefined,
      }),
    );
    expect(submitImpl).toHaveBeenCalledTimes(1);
    expect((submitImpl.mock.calls[0] as unknown[] | undefined)?.[1]).toEqual(
      expect.objectContaining({
        config: expect.objectContaining({
          recursion_limit: 1000,
        }),
        context: expect.objectContaining({
          workflow_clarification_resume: true,
          workflow_resume_run_id: "run-clarify-1",
          workflow_resume_task_id: "task-clarify-1",
        }),
      }),
    );

    if (resolveSubmit) {
      resolveSubmit();
    }
    await act(async () => {
      await submitPromise;
    });

    rendered.cleanup();
  });

  it("resumes from clarification stored in task context without creating a queued shell", async () => {
    let resolveSubmit: (() => void) | undefined;
    const submitPromise = new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    });
    const submitImpl = vi.fn(() => submitPromise);
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      submitImpl,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-store-1",
        workflow_stage: "executing",
        workflow_stage_detail: "Booking the meeting room",
        execution_state: "INTERRUPTED",
      },
      isLoading: true,
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "task_running",
        source: "multi_agent",
        task_id: "task-store-1",
        run_id: "run-store-1",
        description: "Book the meeting room",
        agent_name: "meeting-agent",
        status: "waiting_clarification",
        status_detail: "@waiting_clarification",
        clarification_prompt: "Which city should I use?",
      });
    });

    upsertTaskMock.mockClear();

    await act(async () => {
      void latestSendMessage?.("thread-1", {
        text: "Beijing",
        files: [],
      });
      await Promise.resolve();
    });

    expect(latestThread?.values.workflow_stage).toBe("executing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Booking the meeting room",
    );
    expect(upsertTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "task-store-1",
        status: "in_progress",
        clarificationPrompt: undefined,
      }),
    );
    expect(submitImpl).toHaveBeenCalledTimes(1);
    expect((submitImpl.mock.calls[0] as unknown[] | undefined)?.[1]).toEqual(
      expect.objectContaining({
        config: expect.objectContaining({
          recursion_limit: 1000,
        }),
        context: expect.objectContaining({
          workflow_clarification_resume: true,
          workflow_resume_run_id: "run-store-1",
          workflow_resume_task_id: "task-store-1",
        }),
      }),
    );

    if (resolveSubmit) {
      resolveSubmit();
    }
    await act(async () => {
      await submitPromise;
    });

    rendered.cleanup();
  });

  it("treats interrupted workflow replies as resumes even before task_pool rehydrates", async () => {
    let resolveSubmit: (() => void) | undefined;
    const submitPromise = new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    });
    const submitImpl = vi.fn(() => submitPromise);
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      submitImpl,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-clarify-2",
        workflow_stage: "executing",
        workflow_stage_detail: "Resuming the meeting room booking",
        execution_state: "INTERRUPTED",
      },
    });

    await act(async () => {
      void latestSendMessage?.("thread-1", {
        text: "Shenzhen",
        files: [],
      });
      await Promise.resolve();
    });

    expect(latestThread?.values.workflow_stage).toBe("executing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Resuming the meeting room booking",
    );
    expect(submitImpl).toHaveBeenCalledTimes(1);
    expect((submitImpl.mock.calls[0] as unknown[] | undefined)?.[1]).toEqual(
      expect.objectContaining({
        config: expect.objectContaining({
          recursion_limit: 1000,
        }),
        context: expect.objectContaining({
          workflow_clarification_resume: true,
          workflow_resume_run_id: "run-clarify-2",
        }),
      }),
    );

    if (resolveSubmit) {
      resolveSubmit();
    }
    await act(async () => {
      await submitPromise;
    });

    rendered.cleanup();
  });

  it("keeps the optimistic shell when the thread still holds the previous run stage", async () => {
    let resolveSubmit: (() => void) | undefined;
    const submitPromise = new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    });
    const submitImpl = vi.fn(() => submitPromise);
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      submitImpl,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-old",
        workflow_stage: "summarizing",
        workflow_stage_detail: "Conference room A is booked",
        workflow_stage_updated_at: "2026-03-13T10:02:00.000Z",
      },
    });

    await act(async () => {
      void latestSendMessage?.("thread-1", {
        text: "Book conference room B",
        files: [],
      });
      await Promise.resolve();
    });

    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book conference room B",
    );

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "orchestration_mode_resolved",
        run_id: "run-9",
        resolved_orchestration_mode: "workflow",
        orchestration_reason: "Structured task detected",
      });
    });

    expect(latestThread?.values.run_id).toBe("run-9");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book conference room B",
    );

    if (resolveSubmit) {
      resolveSubmit();
    }
    await act(async () => {
      await submitPromise;
    });

    rendered.cleanup();
  });

  it("preserves hydrated workflow tasks during same-run loading gaps", () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-gap-1",
        workflow_stage: "executing",
        task_pool: [
          {
            task_id: "task-gap-1",
            description: "Book the meeting room",
            status: "RUNNING",
          },
        ],
      },
    });

    expect(hydrateTasksMock).toHaveBeenCalledTimes(1);
    hydrateTasksMock.mockClear();
    resetTasksBySourceMock.mockClear();

    rendered.rerender({
      assistantId: "entry_graph",
      isLoading: true,
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-gap-1",
        workflow_stage: "executing",
        workflow_stage_detail: "Resuming booking",
        task_pool: [],
      },
    });

    expect(hydrateTasksMock).not.toHaveBeenCalled();
    expect(resetTasksBySourceMock).not.toHaveBeenCalled();

    rendered.cleanup();
  });

  it("keeps the optimistic acknowledged shell until an authoritative stage arrives", async () => {
    let resolveSubmit: (() => void) | undefined;
    const submitPromise = new Promise<void>((resolve) => {
      resolveSubmit = resolve;
    });
    const submitImpl = vi.fn(() => submitPromise);
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      submitImpl,
    });

    await act(async () => {
      void latestSendMessage?.("thread-1", {
        text: "Book the board room",
        files: [],
      });
      await Promise.resolve();
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "orchestration_mode_resolved",
        run_id: "run-8",
        resolved_orchestration_mode: "workflow",
        orchestration_reason: "Structured task detected",
      });
    });

    expect(latestThread?.values.run_id).toBe("run-8");
    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book the board room",
    );

    if (resolveSubmit) {
      resolveSubmit();
    }
    await act(async () => {
      await submitPromise;
    });

    rendered.cleanup();
  });
});

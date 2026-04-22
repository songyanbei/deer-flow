import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskUpsert, TaskViewModel } from "../tasks/types";

import { useThreadStream } from "./hooks";
import type { RuntimeStreamEvent } from "./runtime-stream";

const useStreamMock = vi.fn();
const useQueryClientMock = vi.fn(() => ({
  invalidateQueries: vi.fn(),
  setQueriesData: vi.fn(),
}));
const streamRuntimeMessageMock =
  vi.fn<
    (
      threadId: string,
      body: unknown,
      options?: { signal?: AbortSignal },
    ) => AsyncGenerator<unknown, void, void>
  >();

async function* emptyGatewayStream(): AsyncGenerator<never, void, void> {
  /* yields nothing */
}

function streamEvents(
  events: RuntimeStreamEvent[],
): AsyncGenerator<RuntimeStreamEvent, void, void> {
  return (async function* () {
    for (const event of events) {
      yield event;
    }
  })();
}

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

vi.mock("./runtime-stream", async () => {
  const actual =
    await vi.importActual<typeof import("./runtime-stream")>("./runtime-stream");
  return {
    ...actual,
    streamRuntimeMessage: (
      threadId: string,
      body: unknown,
      options?: { signal?: AbortSignal },
    ) => streamRuntimeMessageMock(threadId, body, options),
  };
});

vi.mock("@langchain/langgraph-sdk/react", () => ({
  useStream: (...args: unknown[]) => useStreamMock(...args),
}));

vi.mock("@tanstack/react-query", () => ({
  useMutation: vi.fn(),
  useQuery: vi.fn(),
  useQueryClient: () => useQueryClientMock(),
}));

vi.mock("../api", () => ({
  getAPIClient: vi.fn(() => ({
    threads: {
      getState: vi.fn(async () => ({ values: {} })),
    },
  })),
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
  // Override the ``threadId`` passed to ``useThreadStream``. Defaults to
  // ``"thread-1"`` to preserve the historical harness contract; pass
  // ``null`` to simulate the /workspace/chats/[id] first-submit window
  // where the hook is invoked with ``threadId: undefined`` until
  // ``onStart`` flips ``isNewThread`` to false.
  threadIdOverride?: string | null;
};

let lastStreamOptions: Record<string, unknown> = {};
let latestThread: {
  values: Record<string, unknown>;
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
    const threadIdProp =
      currentProps.threadIdOverride === null
        ? undefined
        : (currentProps.threadIdOverride ?? "thread-1");
    const [thread, sendMessage] = useThreadStream({
      assistantId: currentProps.assistantId,
      threadId: threadIdProp,
      context: {
        mode: "flash",
        requested_orchestration_mode:
          currentProps.requestedOrchestrationMode ?? "auto",
      },
    });
    latestThread = thread as unknown as {
      values: Record<string, unknown>;
    };
    latestSendMessage = sendMessage as typeof latestSendMessage;
    return null;
  }

  useStreamMock.mockImplementation((options: unknown) => {
    lastStreamOptions = options as Record<string, unknown>;
    return {
      messages: [],
      values: currentProps.threadValues ?? {},
      isLoading: currentProps.isLoading ?? false,
      isThreadLoading: false,
      submit: vi.fn(),
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

async function driveGatewayStream(events: RuntimeStreamEvent[]) {
  streamRuntimeMessageMock.mockImplementationOnce(() => streamEvents(events));
  await act(async () => {
    await latestSendMessage?.("thread-1", { text: "noop", files: [] });
  });
}

describe("useThreadStream orchestration hydration", () => {
  beforeEach(() => {
    useStreamMock.mockReset();
    hydrateTasksMock.mockReset();
    resetTasksBySourceMock.mockReset();
    upsertTaskMock.mockReset();
    streamRuntimeMessageMock.mockReset();
    streamRuntimeMessageMock.mockImplementation(() => emptyGatewayStream());
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

  it("syncs resolved mode and reason from custom events before values hydration catches up", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-3",
          resolved_orchestration_mode: "workflow",
          orchestration_reason: "Detected multiple parallel subtasks.",
        },
      },
      {
        type: "task_running",
        data: {
          thread_id: "thread-1",
          run_id: "run-3",
          source: "multi_agent",
          task_id: "task-3",
          description: "Parallel task",
          status: "waiting_clarification",
          status_detail: "Waiting for clarification",
          clarification_prompt: "Which dataset should I use?",
        },
      },
    ]);

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

    // Post-Phase 1 semantics: Gateway state_snapshot writes
    // resolved_orchestration_mode / orchestration_reason / run_id into
    // liveValuesPatch, which takes precedence over ``thread.values`` in
    // ``mergedThread.values``. This is required so workflow UI surfaces
    // (clarification / intervention cards) appear during a live run
    // before the backend-persisted state has rehydrated ``thread.values``.
    // Stale/out-of-order guards live inside the onStateSnapshot setter
    // (staleRunIdsRef + workflow_stage_updated_at ordering); see the
    // run-replacement / out-of-order tests below.
    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.orchestration_reason).toBe(
      "Detected multiple parallel subtasks.",
    );
    expect(latestThread?.values.run_id).toBe("run-3");

    rendered.cleanup();
  });

  it("syncs resolved mode and reason from non-task orchestration events", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: null,
          resolved_orchestration_mode: "workflow",
          orchestration_reason: "Agent default routed to workflow",
        },
      },
    ]);

    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.orchestration_reason).toBe(
      "Agent default routed to workflow",
    );
    expect(upsertTaskMock).not.toHaveBeenCalled();

    rendered.cleanup();
  });

  it("preserves thread id on multi-agent intervention events so the card can submit resolutions", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    await driveGatewayStream([
      {
        type: "task_waiting_intervention",
        data: {
          thread_id: "thread-1",
          run_id: "run-int-1",
          source: "multi_agent",
          task_id: "task-int-1",
          description: "Approve the room booking",
          status: "waiting_intervention",
          intervention_fingerprint: "fp-1",
          intervention_status: "pending",
          pending_interrupt: {
            interrupt_type: "intervention",
            interrupt_kind: "before_tool",
            request_id: "req-1",
            fingerprint: "fp-1",
            semantic_key: "meeting_createMeeting:confirm",
            source_signal: "intervention_required",
            source_agent: "meeting-agent",
            created_at: "2026-03-17T10:00:00.000Z",
          },
          intervention_request: {
            request_id: "req-1",
            fingerprint: "fp-1",
            intervention_type: "before_tool",
            interrupt_kind: "before_tool",
            semantic_key: "meeting_createMeeting:confirm",
            source_signal: "intervention_required",
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
        },
      },
    ]);

    expect(upsertTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "task-int-1",
        source: "multi_agent",
        threadId: "thread-1",
        status: "waiting_intervention",
        pendingInterrupt: expect.objectContaining({
          interrupt_kind: "before_tool",
          semantic_key: "meeting_createMeeting:confirm",
        }),
        interventionRequest: expect.objectContaining({
          source_signal: "intervention_required",
        }),
      }),
    );

    rendered.cleanup();
  });

  it("syncs workflow stage from non-task stage events before values hydration catches up", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-stage-1",
          workflow_stage: "planning",
          workflow_stage_detail: "Book the meeting room",
          workflow_stage_updated_at: "2026-03-13T10:00:00.000Z",
        },
      },
    ]);

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

    // Post-Phase 1 semantics: workflow_stage / workflow_stage_detail from
    // Gateway state_snapshot survive a useStream rehydration that briefly
    // returns null values. Stale-run guards (run replacement + timestamp
    // ordering) still apply inside the onStateSnapshot setter.
    expect(latestThread?.values.workflow_stage).toBe("planning");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book the meeting room",
    );

    rendered.cleanup();
  });

  it("keeps workflow mode patched while loading if streamed values temporarily clear", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadValues: {},
      isLoading: true,
    });

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-4",
          resolved_orchestration_mode: "workflow",
          orchestration_reason: "Run workflow subtasks in parallel.",
        },
      },
      {
        type: "task_running",
        data: {
          thread_id: "thread-1",
          run_id: "run-4",
          source: "multi_agent",
          task_id: "task-4",
        },
      },
    ]);

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

  it("applies newer stage patches for the same run after hydration", async () => {
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

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-5",
          workflow_stage: "routing",
          workflow_stage_detail: "Dispatching the room booking task",
          workflow_stage_updated_at: "2026-03-13T10:01:00.000Z",
        },
      },
    ]);

    expect(latestThread?.values.workflow_stage).toBe("routing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Dispatching the room booking task",
    );
    expect(latestThread?.values.run_id).toBe("run-5");

    rendered.cleanup();
  });

  it("ignores out-of-order older stage patches for the same run", async () => {
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

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-old-stage-1",
          workflow_stage: "planning",
          workflow_stage_detail: "Older planning update",
          workflow_stage_updated_at: "2026-03-13T10:04:00.000Z",
        },
      },
    ]);

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
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      isLoading: true,
    });

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-queued-1",
          resolved_orchestration_mode: "workflow",
          workflow_stage: "queued",
          workflow_stage_detail: "Waiting for the workflow worker to start",
          workflow_stage_updated_at: "2026-03-13T10:04:00.000Z",
        },
      },
    ]);

    expect(latestThread?.values.run_id).toBe("run-queued-1");
    expect(latestThread?.values.workflow_stage).toBe("queued");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Waiting for the workflow worker to start",
    );

    rendered.cleanup();
  });

  it("replaces stale stage state when a newer run starts streaming", async () => {
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

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-7",
          resolved_orchestration_mode: "workflow",
          workflow_stage: "acknowledged",
          workflow_stage_detail: "Book the next meeting room",
          workflow_stage_updated_at: "2026-03-13T10:03:00.000Z",
        },
      },
    ]);

    expect(latestThread?.values.run_id).toBe("run-7");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book the next meeting room",
    );

    rendered.cleanup();
  });

  it("ignores late stage events from a run that was already replaced", async () => {
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

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-7",
          resolved_orchestration_mode: "workflow",
          workflow_stage: "acknowledged",
          workflow_stage_detail: "Book conference room B",
          workflow_stage_updated_at: "2026-03-13T10:03:00.000Z",
        },
      },
    ]);

    expect(latestThread?.values.run_id).toBe("run-7");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-6",
          resolved_orchestration_mode: "workflow",
          workflow_stage: "summarizing",
          workflow_stage_detail: "Late old run event",
          workflow_stage_updated_at: "2026-03-13T10:04:00.000Z",
        },
      },
    ]);

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

  it("recovers a terminal summarizing shell from thread state until the next run replaces it", async () => {
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

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-summary-2",
          resolved_orchestration_mode: "workflow",
          workflow_stage: "acknowledged",
          workflow_stage_detail: "Book conference room B",
          workflow_stage_updated_at: "2026-03-13T10:07:00.000Z",
        },
      },
    ]);

    expect(latestThread?.values.run_id).toBe("run-summary-2");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book conference room B",
    );

    rendered.cleanup();
  });

  it("clears the previous run stage when only the new run id has arrived", async () => {
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

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-7",
          resolved_orchestration_mode: "workflow",
          orchestration_reason: "Structured task detected",
        },
      },
    ]);

    expect(latestThread?.values.run_id).toBe("run-7");
    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.workflow_stage).toBeNull();
    expect(latestThread?.values.workflow_stage_detail).toBeNull();

    rendered.cleanup();
  });

  it("routes explicit workflow mode submissions through the Gateway stream with workflow orchestration", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
    });

    await act(async () => {
      await latestSendMessage?.("thread-1", {
        text: "Plan this in workflow mode.",
        files: [],
      });
    });

    expect(streamRuntimeMessageMock).toHaveBeenCalledTimes(1);
    expect(streamRuntimeMessageMock.mock.calls[0]?.[0]).toBe("thread-1");
    expect(streamRuntimeMessageMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        message: "Plan this in workflow mode.",
        requested_orchestration_mode: "workflow",
      }),
    );

    rendered.cleanup();
  });

  it("shows an optimistic workflow shell immediately for explicit workflow submissions", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
    });

    // Hold the stream open until after the assertion so the optimistic shell
    // is visible before any backend event arrives.
    let resolveGate: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      resolveGate = resolve;
    });
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      (async function* () {
        await gate;
      })(),
    );

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

    resolveGate?.();
    await act(async () => {
      await sendPromise;
    });

    rendered.cleanup();
  });

  it("does not replace an active clarification resume with a fresh acknowledged shell", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
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

    let resolveGate: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      resolveGate = resolve;
    });
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      (async function* () {
        await gate;
      })(),
    );

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestSendMessage?.("thread-1", {
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
    expect(streamRuntimeMessageMock).toHaveBeenCalledTimes(1);
    expect(streamRuntimeMessageMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        requested_orchestration_mode: "workflow",
        app_context: expect.objectContaining({
          workflow_clarification_resume: true,
          workflow_resume_run_id: "run-clarify-1",
          workflow_resume_task_id: "task-clarify-1",
        }),
      }),
    );

    resolveGate?.();
    await act(async () => {
      await sendPromise;
    });

    rendered.cleanup();
  });

  it("resumes from clarification stored in task context without creating a queued shell", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-store-1",
        workflow_stage: "executing",
        workflow_stage_detail: "Booking the meeting room",
        execution_state: "INTERRUPTED",
      },
      isLoading: true,
    });

    // First send: stream a task_running that deposits the clarification task
    // into the mocked task store.
    await driveGatewayStream([
      {
        type: "task_running",
        data: {
          thread_id: "thread-1",
          run_id: "run-store-1",
          source: "multi_agent",
          task_id: "task-store-1",
          description: "Book the meeting room",
          agent_name: "meeting-agent",
          status: "waiting_clarification",
          status_detail: "@waiting_clarification",
          clarification_prompt: "Which city should I use?",
        },
      },
    ]);

    upsertTaskMock.mockClear();

    // Second send: clarification resume — the hook should pick up the stored
    // clarification task from the mocked task context.
    let resolveGate: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      resolveGate = resolve;
    });
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      (async function* () {
        await gate;
      })(),
    );

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestSendMessage?.("thread-1", {
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
    expect(streamRuntimeMessageMock).toHaveBeenCalledTimes(2);
    expect(streamRuntimeMessageMock.mock.calls[1]?.[1]).toEqual(
      expect.objectContaining({
        requested_orchestration_mode: "workflow",
        app_context: expect.objectContaining({
          workflow_clarification_resume: true,
          workflow_resume_run_id: "run-store-1",
          workflow_resume_task_id: "task-store-1",
        }),
      }),
    );

    resolveGate?.();
    await act(async () => {
      await sendPromise;
    });

    rendered.cleanup();
  });

  it("treats interrupted workflow replies as resumes even before task_pool rehydrates", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-clarify-2",
        workflow_stage: "executing",
        workflow_stage_detail: "Resuming the meeting room booking",
        execution_state: "INTERRUPTED",
      },
    });

    let resolveGate: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      resolveGate = resolve;
    });
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      (async function* () {
        await gate;
      })(),
    );

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestSendMessage?.("thread-1", {
        text: "Shenzhen",
        files: [],
      });
      await Promise.resolve();
    });

    expect(latestThread?.values.workflow_stage).toBe("executing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Resuming the meeting room booking",
    );
    expect(streamRuntimeMessageMock).toHaveBeenCalledTimes(1);
    expect(streamRuntimeMessageMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        requested_orchestration_mode: "workflow",
        app_context: expect.objectContaining({
          workflow_clarification_resume: true,
          workflow_resume_run_id: "run-clarify-2",
        }),
      }),
    );

    resolveGate?.();
    await act(async () => {
      await sendPromise;
    });

    rendered.cleanup();
  });

  it("keeps the optimistic shell when the thread still holds the previous run stage", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-old",
        workflow_stage: "summarizing",
        workflow_stage_detail: "Conference room A is booked",
        workflow_stage_updated_at: "2026-03-13T10:02:00.000Z",
      },
    });

    // Stream gate: hold the generator open, then yield a state_snapshot
    // carrying the orchestration patch, all within the same sendMessage call
    // so the optimistic shell (from text="Book conference room B") remains
    // authoritative.
    let resolveGate: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      resolveGate = resolve;
    });
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      (async function* () {
        await gate;
        yield {
          type: "state_snapshot",
          data: {
            thread_id: "thread-1",
            run_id: "run-9",
            resolved_orchestration_mode: "workflow",
            orchestration_reason: "Structured task detected",
          },
        } as RuntimeStreamEvent;
      })(),
    );

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestSendMessage?.("thread-1", {
        text: "Book conference room B",
        files: [],
      });
      await Promise.resolve();
    });

    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book conference room B",
    );

    resolveGate?.();
    await act(async () => {
      await sendPromise;
    });

    expect(latestThread?.values.run_id).toBe("run-9");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book conference room B",
    );

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
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
    });

    // Gate the generator so the optimistic shell (text="Book the board room")
    // is observable before the orchestration patch arrives; then yield the
    // state_snapshot inside the same sendMessage call.
    let resolveGate: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      resolveGate = resolve;
    });
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      (async function* () {
        await gate;
        yield {
          type: "state_snapshot",
          data: {
            thread_id: "thread-1",
            run_id: "run-8",
            resolved_orchestration_mode: "workflow",
            orchestration_reason: "Structured task detected",
          },
        } as RuntimeStreamEvent;
      })(),
    );

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestSendMessage?.("thread-1", {
        text: "Book the board room",
        files: [],
      });
      await Promise.resolve();
    });

    resolveGate?.();
    await act(async () => {
      await sendPromise;
    });

    expect(latestThread?.values.run_id).toBe("run-8");
    expect(latestThread?.values.resolved_orchestration_mode).toBe("workflow");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Book the board room",
    );

    rendered.cleanup();
  });

  it("hydrates task_pool with the sendMessage thread id even when the hook's threadId prop is still undefined", async () => {
    // Simulates /workspace/chats/[id] first-submit window: ``isNewThread``
    // is still true, so ``useThreadStream`` is called with
    // ``threadId: undefined``. The Gateway SSE stream fires before the
    // prop-to-state sync catches up, and without the ``liveThreadId``
    // fallback the hydrated task's ``threadId`` is ``undefined`` and
    // ``message-list.tsx`` silently suppresses the intervention card.
    const rendered = renderHook({
      assistantId: "entry_graph",
      threadIdOverride: null,
    });

    hydrateTasksMock.mockClear();

    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-live-1",
          run_id: "run-live-1",
          resolved_orchestration_mode: "workflow",
          task_pool: [
            {
              task_id: "task-live-1",
              description: "Please confirm the booking slot",
              status: "WAITING_INTERVENTION",
              clarification_prompt: "Morning or afternoon?",
              run_id: "run-live-1",
            },
          ],
        },
      },
    ]);

    expect(hydrateTasksMock).toHaveBeenCalled();
    const [firstHydrationCallArgs] = hydrateTasksMock.mock.calls;
    expect(firstHydrationCallArgs).toBeDefined();
    const hydratedTasks = firstHydrationCallArgs![0] as Array<{
      id: string;
      threadId?: string;
    }>;
    const clarificationTask = hydratedTasks.find(
      (task) => task.id === "task-live-1",
    );
    expect(clarificationTask).toBeDefined();
    // Fallback chain: _threadId (undefined here) → liveThreadId (set from
    // the sendMessage threadId arg "thread-1", then refreshed to the
    // snapshot's thread_id on receipt).
    expect(clarificationTask?.threadId).toBeDefined();
    expect(clarificationTask?.threadId).not.toBe("");

    rendered.cleanup();
  });

  it("clears the live workflow slice at submit start so the previous run's stage does not leak into the new run", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
    });

    // First run: populate liveValuesPatch with workflow_stage via SSE.
    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-leak-1",
          resolved_orchestration_mode: "workflow",
          workflow_stage: "summarizing",
          workflow_stage_detail: "Finishing the first task",
          workflow_stage_updated_at: "2026-03-13T10:10:00.000Z",
        },
      },
    ]);

    expect(latestThread?.values.workflow_stage).toBe("summarizing");
    expect(latestThread?.values.run_id).toBe("run-leak-1");

    // Second submit: gate the generator so we can observe state BEFORE
    // the next state_snapshot arrives — this is the window where a stale
    // workflow_stage would otherwise still be visible.
    let resolveGate: (() => void) | undefined;
    const gate = new Promise<void>((resolve) => {
      resolveGate = resolve;
    });
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      (async function* () {
        await gate;
        yield {
          type: "state_snapshot",
          data: {
            thread_id: "thread-1",
            run_id: "run-leak-2",
            resolved_orchestration_mode: "workflow",
            workflow_stage: "queued",
            workflow_stage_detail: "Dispatching the next task",
            workflow_stage_updated_at: "2026-03-13T10:20:00.000Z",
          },
        } as RuntimeStreamEvent;
      })(),
    );

    let sendPromise: Promise<void> | undefined;
    await act(async () => {
      sendPromise = latestSendMessage?.("thread-1", {
        text: "Kick off the next task",
        files: [],
      });
      await Promise.resolve();
    });

    // Before the new state_snapshot arrives, the previous run's
    // workflow_stage should already be cleared from the live slice.
    expect(latestThread?.values.workflow_stage).not.toBe("summarizing");
    expect(latestThread?.values.workflow_stage_detail).not.toBe(
      "Finishing the first task",
    );

    resolveGate?.();
    await act(async () => {
      await sendPromise;
    });

    expect(latestThread?.values.run_id).toBe("run-leak-2");
    expect(latestThread?.values.workflow_stage).toBe("queued");

    rendered.cleanup();
  });

  it("marks the previous run_id stale at submit start so a late snapshot from it is dropped", async () => {
    const rendered = renderHook({
      assistantId: "entry_graph",
    });

    // First run establishes run_id = run-stale-1 in liveValuesPatch.
    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-stale-1",
          resolved_orchestration_mode: "workflow",
          workflow_stage: "executing",
          workflow_stage_detail: "First run executing",
          workflow_stage_updated_at: "2026-03-13T10:30:00.000Z",
        },
      },
    ]);

    // Second submit: yields a fresh run-stale-2 state_snapshot, then a
    // late snapshot from the superseded run-stale-1. The late snapshot
    // should be dropped by the staleRunIdsRef guard seeded at submit.
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      streamEvents([
        {
          type: "state_snapshot",
          data: {
            thread_id: "thread-1",
            run_id: "run-stale-2",
            resolved_orchestration_mode: "workflow",
            workflow_stage: "acknowledged",
            workflow_stage_detail: "Second run acknowledged",
            workflow_stage_updated_at: "2026-03-13T10:40:00.000Z",
          },
        },
        {
          type: "state_snapshot",
          data: {
            thread_id: "thread-1",
            run_id: "run-stale-1",
            resolved_orchestration_mode: "workflow",
            workflow_stage: "summarizing",
            workflow_stage_detail: "Late event from the first run",
            workflow_stage_updated_at: "2026-03-13T10:41:00.000Z",
          },
        },
      ]),
    );

    await act(async () => {
      await latestSendMessage?.("thread-1", {
        text: "Second run",
        files: [],
      });
    });

    expect(latestThread?.values.run_id).toBe("run-stale-2");
    expect(latestThread?.values.workflow_stage).toBe("acknowledged");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Second run acknowledged",
    );

    rendered.cleanup();
  });

  it("does not flag the live run_id stale on clarification resume so same-run snapshots still apply", async () => {
    // Backend reuses the existing workflow run_id on clarification resume
    // (selector_node / orchestration_selector — see
    // test_orchestration_selector_reuses_run_id_for_workflow_clarification_resume
    // and test_selector_node_reuses_existing_workflow_run_when_resume_is_explicitly_requested).
    // If the submit-start reset flagged that run_id as stale, the next
    // state_snapshot (same run_id) would be dropped at reception and the
    // resumed stage would never propagate to the UI.
    const rendered = renderHook({
      assistantId: "entry_graph",
      requestedOrchestrationMode: "workflow",
      threadValues: {
        resolved_orchestration_mode: "workflow",
        run_id: "run-clarify-resume-1",
        workflow_stage: "executing",
        workflow_stage_detail: "Booking the meeting room",
        execution_state: "INTERRUPTED",
        task_pool: [
          {
            task_id: "task-clarify-resume-1",
            description: "Book the meeting room",
            run_id: "run-clarify-resume-1",
            assigned_agent: "meeting-agent",
            status: "RUNNING",
            status_detail: "@waiting_clarification",
            clarification_prompt: "Which city should I use?",
          },
        ],
      },
    });

    // Seed liveValuesPatch.run_id via a first state_snapshot so the
    // submit-reset path would normally flag it stale.
    await driveGatewayStream([
      {
        type: "state_snapshot",
        data: {
          thread_id: "thread-1",
          run_id: "run-clarify-resume-1",
          resolved_orchestration_mode: "workflow",
          workflow_stage: "executing",
          workflow_stage_detail: "Awaiting clarification",
          workflow_stage_updated_at: "2026-03-13T11:00:00.000Z",
        },
      },
    ]);

    // Clarification resume submit: the backend reuses run-clarify-resume-1
    // and emits a new state_snapshot for the same run advancing the stage.
    streamRuntimeMessageMock.mockImplementationOnce(() =>
      streamEvents([
        {
          type: "state_snapshot",
          data: {
            thread_id: "thread-1",
            run_id: "run-clarify-resume-1",
            resolved_orchestration_mode: "workflow",
            workflow_stage: "routing",
            workflow_stage_detail: "Dispatching the resumed task",
            workflow_stage_updated_at: "2026-03-13T11:05:00.000Z",
          },
        },
      ]),
    );

    await act(async () => {
      await latestSendMessage?.("thread-1", {
        text: "Shenzhen",
        files: [],
      });
    });

    expect(latestThread?.values.run_id).toBe("run-clarify-resume-1");
    expect(latestThread?.values.workflow_stage).toBe("routing");
    expect(latestThread?.values.workflow_stage_detail).toBe(
      "Dispatching the resumed task",
    );

    rendered.cleanup();
  });
});

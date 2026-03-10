import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useThreadStream } from "./hooks";

const useStreamMock = vi.fn();
const useQueryClientMock = vi.fn(() => ({
  invalidateQueries: vi.fn(),
}));
const hydrateTasksMock = vi.fn();
const resetTasksBySourceMock = vi.fn();
const upsertTaskMock = vi.fn();

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
  useTaskActions: () => ({
    hydrateTasks: hydrateTasksMock,
    resetTasksBySource: resetTasksBySourceMock,
    upsertTask: upsertTaskMock,
  }),
}));

type HarnessProps = {
  assistantId: "entry_graph" | "lead_agent" | "multi_agent";
  threadValues?: Record<string, unknown>;
};

let lastStreamOptions: Record<string, unknown> = {};
let latestThread: { values: Record<string, unknown> } | null = null;

function renderHook(initialProps: HarnessProps) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  let currentProps = initialProps;

  function Harness() {
    const [thread] = useThreadStream({
      assistantId: currentProps.assistantId,
      threadId: "thread-1",
      context: { mode: "flash", requested_orchestration_mode: "auto" },
    });
    latestThread = thread;
    return null;
  }

  useStreamMock.mockImplementation((options: unknown) => {
    lastStreamOptions = options as Record<string, unknown>;
    return {
      messages: [],
      values: currentProps.threadValues ?? {},
      isLoading: false,
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

describe("useThreadStream orchestration hydration", () => {
  beforeEach(() => {
    useStreamMock.mockReset();
    hydrateTasksMock.mockReset();
    resetTasksBySourceMock.mockReset();
    upsertTaskMock.mockReset();
    lastStreamOptions = {};
    latestThread = null;
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
    expect(resetTasksBySourceMock).toHaveBeenCalledWith(
      "multi_agent",
      "run-1",
    );

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
    });

    expect(latestThread?.values.resolved_orchestration_mode).toBeNull();
    expect(latestThread?.values.orchestration_reason).toBeNull();
    expect(latestThread?.values.run_id).toBeNull();

    rendered.cleanup();
  });
});

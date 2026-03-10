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

let lastStreamOptions: Record<string, unknown> = {};

function renderHook(props: {
  assistantId: "lead_agent" | "multi_agent";
  threadValues?: Record<string, unknown>;
}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  function Harness() {
    useThreadStream({
      assistantId: props.assistantId,
      threadId: "thread-1",
      context: { mode: "flash" },
    });
    return null;
  }

  useStreamMock.mockImplementation((options: unknown) => {
    const callbacks = options as Record<string, unknown>;
    lastStreamOptions = callbacks;
    return {
      messages: [],
      values: props.threadValues ?? {},
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
    cleanup() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

describe("useThreadStream multi-agent compatibility", () => {
  beforeEach(() => {
    useStreamMock.mockReset();
    hydrateTasksMock.mockReset();
    resetTasksBySourceMock.mockReset();
    upsertTaskMock.mockReset();
    lastStreamOptions = {};
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("keeps the default chat path on lead_agent without multi_agent hydration", () => {
    const rendered = renderHook({
      assistantId: "lead_agent",
      threadValues: {
        resolved_orchestration_mode: "leader",
        run_id: "run-1",
      },
    });

    expect(lastStreamOptions.assistantId).toBe("lead_agent");
    expect(hydrateTasksMock).not.toHaveBeenCalled();
    expect(resetTasksBySourceMock).not.toHaveBeenCalled();

    rendered.cleanup();
  });

  it("hydrates task_pool only for multi_agent threads", () => {
    const rendered = renderHook({
      assistantId: "multi_agent",
      threadValues: {
        run_id: "run-1",
        task_pool: [{ task_id: "task-1", description: "Task", status: "PENDING" }],
      },
    });

    expect(lastStreamOptions.assistantId).toBe("multi_agent");
    expect(hydrateTasksMock).toHaveBeenCalledTimes(1);
    expect(hydrateTasksMock.mock.calls[0]?.[1]).toEqual({
      source: "multi_agent",
      runId: "run-1",
    });

    rendered.cleanup();
  });

  it("routes source-less task events through the legacy adapter", () => {
    const rendered = renderHook({
      assistantId: "multi_agent",
    });

    act(() => {
      const onCustomEvent = lastStreamOptions.onCustomEvent as
        | ((event: unknown) => void)
        | undefined;
      onCustomEvent?.({
        type: "task_running",
        task_id: "legacy-1",
        message: "Still working",
      });
    });

    expect(upsertTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "legacy-1",
        source: "legacy_subagent",
        status: "in_progress",
      }),
    );

    rendered.cleanup();
  });
});

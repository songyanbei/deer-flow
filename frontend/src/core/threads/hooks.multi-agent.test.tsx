import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
const hydrateTasksMock = vi.fn();
const resetTasksBySourceMock = vi.fn();
const upsertTaskMock = vi.fn();

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
    tasksById: {},
    orderedTaskIds: [],
  }),
  useTaskActions: () => ({
    hydrateTasks: hydrateTasksMock,
    resetTasksBySource: resetTasksBySourceMock,
    upsertTask: upsertTaskMock,
  }),
}));

let lastStreamOptions: Record<string, unknown> = {};
let latestSendMessage:
  | ((threadId: string, message: { text: string; files: [] }) => Promise<void>)
  | null = null;

function renderHook(props: {
  assistantId: "lead_agent" | "multi_agent";
  threadValues?: Record<string, unknown>;
}) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  function Harness() {
    const [, sendMessage] = useThreadStream({
      assistantId: props.assistantId,
      threadId: "thread-1",
      context: { mode: "flash" },
    });
    latestSendMessage = sendMessage as typeof latestSendMessage;
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
    streamRuntimeMessageMock.mockReset();
    streamRuntimeMessageMock.mockImplementation(() => emptyGatewayStream());
    lastStreamOptions = {};
    latestSendMessage = null;
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

  it("routes source-less task events through the legacy adapter", async () => {
    const rendered = renderHook({
      assistantId: "multi_agent",
    });

    streamRuntimeMessageMock.mockImplementation(() =>
      streamEvents([
        {
          type: "task_running",
          data: {
            thread_id: "thread-1",
            run_id: null,
            task_id: "legacy-1",
            message: "Still working",
          },
        },
      ]),
    );

    await act(async () => {
      await latestSendMessage?.("thread-1", { text: "noop", files: [] });
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

  it("maps multi-agent dependency events into workflow task state", async () => {
    const rendered = renderHook({
      assistantId: "multi_agent",
    });

    streamRuntimeMessageMock.mockImplementation(() =>
      streamEvents([
        {
          type: "task_waiting_dependency",
          data: {
            thread_id: "thread-1",
            run_id: "run-42",
            source: "multi_agent",
            task_id: "task-42",
            description: "Book the meeting room",
            requested_by_agent: "meeting-agent",
            blocked_reason: "Need organizer openId before booking",
            request_help: {
              problem: "Missing organizer openId",
              required_capability: "contact lookup",
              reason: "Meeting API requires an openId",
              expected_output: "Organizer openId and city",
              candidate_agents: ["contacts-agent"],
            },
          },
        },
      ]),
    );

    await act(async () => {
      await latestSendMessage?.("thread-1", { text: "noop", files: [] });
    });

    expect(upsertTaskMock).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "task-42",
        source: "multi_agent",
        runId: "run-42",
        status: "waiting_dependency",
        requestedByAgent: "meeting-agent",
        blockedReason: "Need organizer openId before booking",
        requestHelp: expect.objectContaining({
          requiredCapability: "contact lookup",
        }),
      }),
    );

    rendered.cleanup();
  });
});

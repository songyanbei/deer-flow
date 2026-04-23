import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  streamRuntimeMessage,
  streamRuntimeResume,
  type RuntimeStreamEvent,
  type RuntimeStreamHttpError,
} from "./runtime-stream";

function encodeBody(...chunks: string[]) {
  const encoder = new TextEncoder();
  let i = 0;
  return new ReadableStream({
    pull(controller) {
      if (i >= chunks.length) {
        controller.close();
        return;
      }
      const chunk = chunks[i] ?? "";
      controller.enqueue(encoder.encode(chunk));
      i += 1;
    },
  });
}

describe("streamRuntimeMessage", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  async function collect(
    iter: AsyncGenerator<RuntimeStreamEvent, void, void>,
  ) {
    const events: RuntimeStreamEvent[] = [];
    for await (const event of iter) events.push(event);
    return events;
  }

  it("posts to the Gateway messages:stream endpoint with app_context", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      body: encodeBody(
        `event: ack\ndata: {"thread_id":"t1","run_id":null}\n\n`,
        `event: run_completed\ndata: {"thread_id":"t1","run_id":"r1"}\n\n`,
      ),
    } as unknown as Response);

    const events = await collect(
      streamRuntimeMessage("t1", {
        message: "hello",
        app_context: { thinking_enabled: true, is_plan_mode: false },
      }),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toContain("/api/runtime/threads/t1/messages:stream");
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).credentials).toBe("include");
    expect(
      ((init as RequestInit).headers as Record<string, string>)["Content-Type"],
    ).toBe("application/json");
    const parsed = JSON.parse((init as RequestInit).body as string);
    expect(parsed.message).toBe("hello");
    expect(parsed.app_context).toEqual({
      thinking_enabled: true,
      is_plan_mode: false,
    });

    expect(events.map((e) => e.type)).toEqual(["ack", "run_completed"]);
  });

  it("throws RuntimeStreamHttpError with detail when server rejects", async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 422,
      text: async () => "app_context.unknown: extra fields not permitted",
    } as unknown as Response);

    await expect(
      collect(streamRuntimeMessage("t1", { message: "x" })),
    ).rejects.toMatchObject({
      name: "RuntimeStreamHttpError",
      status: 422,
      detail: "app_context.unknown: extra fields not permitted",
    } satisfies Partial<RuntimeStreamHttpError>);
  });

  it("parses multi-chunk SSE payloads and ignores unknown event names", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      body: encodeBody(
        `event: ack\ndata: {"thread_id":"t","run_id":null}\n\nevent: state_snapshot\n`,
        `data: {"thread_id":"t","run_id":"r1","title":"hi"}\n\n`,
        `event: debug_unknown\ndata: {"noop":1}\n\n`,
        `event: message_delta\ndata: {"thread_id":"t","run_id":"r1","content":"he"}\n\n`,
        `event: message_delta\ndata: {"thread_id":"t","run_id":"r1","content":"hello"}\n\n`,
        `event: run_completed\ndata: {"thread_id":"t","run_id":"r1","last_ai_content":"hello"}\n\n`,
      ),
    } as unknown as Response);

    const events = await collect(
      streamRuntimeMessage("t", { message: "hi" }),
    );

    expect(events.map((e) => e.type)).toEqual([
      "ack",
      "state_snapshot",
      "message_delta",
      "message_delta",
      "run_completed",
    ]);
    const snapshot = events[1] as Extract<
      RuntimeStreamEvent,
      { type: "state_snapshot" }
    >;
    expect(snapshot.data.title).toBe("hi");
    const last = events[4] as Extract<
      RuntimeStreamEvent,
      { type: "run_completed" }
    >;
    expect(last.data.last_ai_content).toBe("hello");
  });

  it("silently skips frames whose data JSON is malformed", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      body: encodeBody(
        `event: ack\ndata: not-json\n\n`,
        `event: run_completed\ndata: {"thread_id":"t","run_id":"r"}\n\n`,
      ),
    } as unknown as Response);

    const events = await collect(
      streamRuntimeMessage("t", { message: "x" }),
    );
    expect(events.map((e) => e.type)).toEqual(["run_completed"]);
  });

  it("forwards AbortSignal to fetch", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      body: encodeBody(
        `event: run_completed\ndata: {"thread_id":"t","run_id":"r"}\n\n`,
      ),
    } as unknown as Response);

    const controller = new AbortController();
    await collect(
      streamRuntimeMessage(
        "t",
        { message: "x" },
        { signal: controller.signal },
      ),
    );
    const [, init] = fetchMock.mock.calls[0]!;
    expect((init as RequestInit).signal).toBe(controller.signal);
  });

  it("posts to the Gateway resume endpoint with resume_payload and checkpoint", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      body: encodeBody(
        `event: ack\ndata: {"thread_id":"t1","run_id":null}\n\n`,
        `event: state_snapshot\ndata: {"thread_id":"t1","run_id":"r1","workflow_stage":"executing"}\n\n`,
        `event: run_completed\ndata: {"thread_id":"t1","run_id":"r1"}\n\n`,
      ),
    } as unknown as Response);

    const events = await collect(
      streamRuntimeResume("t1", {
        resume_payload: { message: "[intervention_resolved] request_id=req-1" },
        checkpoint: { checkpoint_id: "cp-1" },
        workflow_clarification_resume: true,
        workflow_resume_run_id: "r1",
        workflow_resume_task_id: "task-1",
        app_context: { thinking_enabled: true },
      }),
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toContain("/api/runtime/threads/t1/resume");
    expect((init as RequestInit).method).toBe("POST");
    expect((init as RequestInit).credentials).toBe("include");
    const parsed = JSON.parse((init as RequestInit).body as string);
    expect(parsed.resume_payload).toEqual({
      message: "[intervention_resolved] request_id=req-1",
    });
    expect(parsed.checkpoint).toEqual({ checkpoint_id: "cp-1" });
    expect(parsed.workflow_clarification_resume).toBe(true);
    expect(parsed.workflow_resume_run_id).toBe("r1");
    expect(parsed.workflow_resume_task_id).toBe("task-1");
    expect(parsed.app_context).toEqual({ thinking_enabled: true });

    expect(events.map((e) => e.type)).toEqual([
      "ack",
      "state_snapshot",
      "run_completed",
    ]);
  });
});

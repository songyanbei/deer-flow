import { getBackendBaseURL } from "../config";

import type { RequestedOrchestrationMode } from "./types";

/**
 * App-level runtime flags forwarded into the LangGraph `context` via the
 * Gateway `app_context` channel. Mirrors `AppRuntimeContext` in
 * `backend/src/gateway/routers/runtime.py`. Identity fields
 * (`tenant_id` / `user_id` / `thread_id` / `thread_context` / `auth_user`)
 * are intentionally absent — identity is server-sourced and will be rejected
 * (HTTP 422) if the client sets it here.
 */
export type RuntimeAppContext = {
  thinking_enabled?: boolean;
  is_plan_mode?: boolean;
  subagent_enabled?: boolean;
  is_bootstrap?: boolean;
  workflow_clarification_resume?: boolean;
  workflow_resume_run_id?: string;
  workflow_resume_task_id?: string;
  workflow_clarification_response?: {
    answers: Record<string, { text: string }>;
  };
};

export type RuntimeStreamRequest = {
  message: string;
  app_context?: RuntimeAppContext;
  requested_orchestration_mode?: RequestedOrchestrationMode;
  entry_agent?: string;
  group_key?: string;
  allowed_agents?: string[];
  metadata?: Record<string, string | number | boolean | null>;
};

// ── Event contract (mirrors backend/src/gateway/runtime_service.py) ────

export type RuntimeStreamBaseEvent = {
  thread_id: string;
  run_id: string | null;
};

export type RuntimeStreamEventMap = {
  ack: RuntimeStreamBaseEvent;
  state_snapshot: RuntimeStreamBaseEvent & {
    title?: string | null;
    todos?: unknown[];
    task_pool?: unknown[];
    workflow_stage?: string | null;
    workflow_stage_detail?: string | null;
    workflow_stage_updated_at?: string | null;
    resolved_orchestration_mode?: string | null;
    orchestration_reason?: string | null;
    messages_count?: number;
    last_human_message_id?: string;
    artifacts_count?: number;
  };
  message_delta: RuntimeStreamBaseEvent & { content: string };
  message_completed: RuntimeStreamBaseEvent & { content: string };
  artifact_created: RuntimeStreamBaseEvent & {
    artifact: Record<string, unknown>;
    artifact_url?: string;
  };
  intervention_requested: RuntimeStreamBaseEvent & Record<string, unknown>;
  governance_created: RuntimeStreamBaseEvent & { governance_id?: string };
  task_started: RuntimeStreamBaseEvent & Record<string, unknown>;
  task_running: RuntimeStreamBaseEvent & Record<string, unknown>;
  task_waiting_intervention: RuntimeStreamBaseEvent & Record<string, unknown>;
  task_waiting_dependency: RuntimeStreamBaseEvent & Record<string, unknown>;
  task_help_requested: RuntimeStreamBaseEvent & Record<string, unknown>;
  task_resumed: RuntimeStreamBaseEvent & Record<string, unknown>;
  task_completed: RuntimeStreamBaseEvent & Record<string, unknown>;
  task_failed: RuntimeStreamBaseEvent & Record<string, unknown>;
  task_timed_out: RuntimeStreamBaseEvent & Record<string, unknown>;
  workflow_stage_changed: RuntimeStreamBaseEvent & Record<string, unknown>;
  run_completed: RuntimeStreamBaseEvent & {
    final_state?: Record<string, unknown>;
    last_ai_content?: string;
  };
  run_failed: RuntimeStreamBaseEvent & { error: string };
};

export type RuntimeStreamEventName = keyof RuntimeStreamEventMap;

export type RuntimeStreamEvent = {
  [K in RuntimeStreamEventName]: {
    type: K;
    data: RuntimeStreamEventMap[K];
  };
}[RuntimeStreamEventName];

/** Thrown when the HTTP response is rejected before the SSE stream begins. */
export class RuntimeStreamHttpError extends Error {
  public readonly status: number;
  public readonly detail: string;

  constructor(status: number, detail: string) {
    super(`Runtime stream failed (${status})${detail ? `: ${detail}` : ""}`);
    this.name = "RuntimeStreamHttpError";
    this.status = status;
    this.detail = detail;
  }
}

// ── SSE frame parser ──────────────────────────────────────────────────

type RawFrame = { event: string; data: string };

function* parseSseFrames(buffer: { value: string }): Generator<RawFrame> {
  let text = buffer.value;
  // SSE spec: frames are separated by blank lines. Accept both "\n\n" and
  // "\r\n\r\n" (nginx may rewrite line endings in some setups).
  while (true) {
    const match = /\r?\n\r?\n/.exec(text);
    if (!match) break;
    const boundaryIndex = match.index;
    const rawFrame = text.slice(0, boundaryIndex);
    text = text.slice(boundaryIndex + match[0].length);

    let event = "message";
    const dataLines: string[] = [];
    for (const line of rawFrame.split(/\r?\n/)) {
      if (!line) continue;
      // Ignore comment lines that start with ":".
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      if (colon === -1) continue;
      const field = line.slice(0, colon);
      // Per SSE spec, a single leading space is stripped from the value.
      let value = line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") dataLines.push(value);
    }
    if (dataLines.length === 0) continue;
    yield { event, data: dataLines.join("\n") };
  }
  buffer.value = text;
}

function isKnownEventName(name: string): name is RuntimeStreamEventName {
  return KNOWN_EVENT_NAMES.has(name as RuntimeStreamEventName);
}

const KNOWN_EVENT_NAMES: ReadonlySet<RuntimeStreamEventName> = new Set<
  RuntimeStreamEventName
>([
  "ack",
  "state_snapshot",
  "message_delta",
  "message_completed",
  "artifact_created",
  "intervention_requested",
  "governance_created",
  "task_started",
  "task_running",
  "task_waiting_intervention",
  "task_waiting_dependency",
  "task_help_requested",
  "task_resumed",
  "task_completed",
  "task_failed",
  "task_timed_out",
  "workflow_stage_changed",
  "run_completed",
  "run_failed",
]);

// ── Streaming entry point ─────────────────────────────────────────────

export type StreamRuntimeMessageOptions = {
  signal?: AbortSignal;
};

/**
 * POST a message to the Gateway runtime and iterate over normalized SSE
 * events as they arrive. Consumers should call this as an async generator:
 *
 *     for await (const event of streamRuntimeMessage(threadId, payload, { signal })) {
 *       switch (event.type) { ... }
 *     }
 *
 * HTTP-level errors (422 schema validation, 404 thread not found, 409
 * already running, 503 upstream unavailable) are thrown as
 * `RuntimeStreamHttpError` before the stream yields any event. SSE
 * in-stream errors are surfaced as a `run_failed` event in the iterator.
 */
export async function* streamRuntimeMessage(
  threadId: string,
  body: RuntimeStreamRequest,
  options: StreamRuntimeMessageOptions = {},
): AsyncGenerator<RuntimeStreamEvent, void, void> {
  const url = `${getBackendBaseURL()}/api/runtime/threads/${encodeURIComponent(
    threadId,
  )}/messages:stream`;

  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    credentials: "include",
    body: JSON.stringify(body),
    signal: options.signal,
  });

  if (!response.ok) {
    const detail = await response.text().catch(() => "");
    throw new RuntimeStreamHttpError(response.status, detail);
  }

  if (!response.body) {
    throw new RuntimeStreamHttpError(
      response.status,
      "Response body is empty",
    );
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  const buffer = { value: "" };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer.value += decoder.decode(value, { stream: true });
      for (const frame of parseSseFrames(buffer)) {
        if (!isKnownEventName(frame.event)) continue;
        let parsed: unknown;
        try {
          parsed = JSON.parse(frame.data);
        } catch {
          continue;
        }
        if (typeof parsed !== "object" || parsed === null) continue;
        yield {
          type: frame.event,
          data: parsed as RuntimeStreamEventMap[typeof frame.event],
        } as RuntimeStreamEvent;
      }
    }
    // Flush any trailing partial frame that happens to be complete.
    buffer.value += decoder.decode();
    for (const frame of parseSseFrames(buffer)) {
      if (!isKnownEventName(frame.event)) continue;
      let parsed: unknown;
      try {
        parsed = JSON.parse(frame.data);
      } catch {
        continue;
      }
      if (typeof parsed !== "object" || parsed === null) continue;
      yield {
        type: frame.event,
        data: parsed as RuntimeStreamEventMap[typeof frame.event],
      } as RuntimeStreamEvent;
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      // swallow — AbortController.abort() already cancels the underlying stream
    }
  }
}

import type { AIMessage } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "vitest";

import {
  fromLegacyTaskEvent,
  fromMultiAgentTaskEvent,
  type MultiAgentTaskEvent,
} from "./adapters";

describe("task adapters", () => {
  it("maps legacy task events to legacy_subagent tasks", () => {
    const message = {
      id: "msg-1",
      type: "ai",
      content: "working",
    } as unknown as AIMessage;

    const task = fromLegacyTaskEvent({
      type: "task_running",
      task_id: "legacy-1",
      message,
    });

    expect(task.source).toBe("legacy_subagent");
    expect(task.status).toBe("in_progress");
    expect(task.latestMessage).toBe(message);
  });

  it("preserves multi_agent protocol fields from custom events", () => {
    const event: MultiAgentTaskEvent = {
      type: "task_running",
      source: "multi_agent",
      task_id: "task-1",
      run_id: "run-1",
      agent_name: "researcher",
      description: "Collect references",
      status: "waiting_clarification",
      clarification_prompt: "Need the target market.",
      status_detail: "Waiting on input",
      message: "Waiting on input",
    };

    const task = fromMultiAgentTaskEvent(event);

    expect(task.source).toBe("multi_agent");
    expect(task.runId).toBe("run-1");
    expect(task.agentName).toBe("researcher");
    expect(task.status).toBe("waiting_clarification");
    expect(task.clarificationPrompt).toBe("Need the target market.");
  });

  it("keeps task_help_requested in waiting_dependency status", () => {
    const task = fromMultiAgentTaskEvent({
      type: "task_help_requested",
      source: "multi_agent",
      task_id: "task-1",
      run_id: "run-1",
      description: "Book the meeting room",
      status: "waiting_dependency",
      blocked_reason: "Need organizer openId",
      request_help: {
        problem: "Missing organizer openId",
        required_capability: "contact lookup",
        reason: "Meeting API requires an openId",
        expected_output: "Organizer openId and city",
      },
    });

    expect(task.status).toBe("waiting_dependency");
    expect(task.blockedReason).toBe("Need organizer openId");
  });

  it("maps task_resumed metadata for resumed workflow tasks", () => {
    const task = fromMultiAgentTaskEvent({
      type: "task_resumed",
      source: "multi_agent",
      task_id: "task-1",
      run_id: "run-1",
      description: "Book the meeting room",
      status: "in_progress",
      status_detail: "Dependency resolved; task resumed",
      resume_count: 1,
      resolved_inputs: {
        "helper-1": {
          openId: "ou_123",
        },
      },
    });

    expect(task.status).toBe("in_progress");
    expect(task.resumeCount).toBe(1);
    expect(task.resolvedInputs).toEqual({
      "helper-1": {
        openId: "ou_123",
      },
    });
  });

  it("maps task_timed_out to a failed legacy task", () => {
    const task = fromLegacyTaskEvent({
      type: "task_timed_out",
      task_id: "legacy-timeout",
      error: "Timed out after 5 minutes",
    });

    expect(task.source).toBe("legacy_subagent");
    expect(task.status).toBe("failed");
    expect(task.error).toBe("Timed out after 5 minutes");
  });
});

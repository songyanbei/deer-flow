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

  it("maps structured clarification requests from custom events", () => {
    const event: MultiAgentTaskEvent = {
      type: "task_running",
      source: "multi_agent",
      task_id: "task-clar-1",
      run_id: "run-1",
      description: "Collect booking details",
      status: "waiting_clarification",
      clarification_request: {
        title: "Need a few more details",
        description: "Please answer the following questions.",
        questions: [
          {
            key: "meeting_time",
            label: "What time should I book?",
            kind: "input",
            placeholder: "e.g. Today 14:00-15:00",
          },
        ],
      },
    };

    const task = fromMultiAgentTaskEvent(event);

    expect(task.status).toBe("waiting_clarification");
    expect(task.clarificationRequest?.title).toBe("Need a few more details");
    expect(task.clarificationRequest?.questions[0]?.key).toBe("meeting_time");
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

  it("maps intervention events into waiting_intervention tasks", () => {
    const task = fromMultiAgentTaskEvent({
      type: "task_waiting_intervention",
      source: "multi_agent",
      task_id: "task-int-1",
      run_id: "run-1",
      description: "Approve sending the email",
      status: "waiting_intervention",
      intervention_fingerprint: "fp-1",
      intervention_status: "pending",
      intervention_request: {
        request_id: "req-1",
        fingerprint: "fp-1",
        intervention_type: "approval",
        title: "Need approval",
        reason: "This action sends an external email.",
        source_agent: "ops-agent",
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
    }, "thread-1");

    expect(task.status).toBe("waiting_intervention");
    expect(task.threadId).toBe("thread-1");
    expect(task.interventionFingerprint).toBe("fp-1");
    expect(task.interventionStatus).toBe("pending");
    expect(task.interventionRequest?.title).toBe("Need approval");
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

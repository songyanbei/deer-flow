import type { Message } from "@langchain/langgraph-sdk";
import { describe, expect, it } from "vitest";

import { filterWorkflowMessages } from "./workflow-message-filter";

function humanMessage(content: string, id: string): Message {
  return {
    id,
    type: "human",
    content,
  };
}

function aiMessage(content: string, id: string): Message {
  return {
    id,
    type: "ai",
    content,
  };
}

function aiToolCallMessage(
  id: string,
  toolName: string,
  content = "",
): Message {
  return {
    id,
    type: "ai",
    content,
    tool_calls: [
      {
        id: `${id}-tool`,
        name: toolName,
        args: {},
      },
    ],
  };
}

function toolMessage(name: string, id: string): Message {
  return {
    id,
    type: "tool",
    name,
    content: "tool result",
    tool_call_id: `${id}-call`,
  };
}

describe("filterWorkflowMessages", () => {
  it("keeps the real user prompt but removes workflow task prompts echoed from subagents", () => {
    const filtered = filterWorkflowMessages(
      [
        humanMessage("Book me a room tomorrow at 9:00.", "user-1"),
        humanMessage(
          "Check room availability and reserve 9:00-10:00.",
          "internal-1",
        ),
        aiMessage("Working on it.", "ai-1"),
      ],
      [{ description: "Check room availability and reserve 9:00-10:00." }],
    );

    expect(filtered.map((message) => message.id)).toEqual(["user-1", "ai-1"]);
  });

  it("keeps the first real user prompt even when a workflow task description matches it exactly", () => {
    const filtered = filterWorkflowMessages(
      [
        humanMessage("Prepare the launch brief.", "user-1"),
        aiMessage("I will break this into subtasks.", "ai-1"),
        humanMessage("Prepare the launch brief.", "internal-1"),
      ],
      [{ description: "Prepare the launch brief." }],
    );

    expect(filtered.map((message) => message.id)).toEqual(["user-1", "ai-1"]);
  });

  it("removes workflow executor context messages with known facts or clarification markers", () => {
    const filtered = filterWorkflowMessages(
      [
        humanMessage(
          [
            "Check room availability and reserve 9:00-10:00.",
            "",
            "Known facts (do not re-check):",
            "1. Room 42-1 is available.",
          ].join("\n"),
          "internal-1",
        ),
        humanMessage(
          [
            "Check room availability and reserve 9:00-10:00.",
            "",
            "User clarification answer:",
            "Use room 42-1.",
          ].join("\n"),
          "internal-2",
        ),
        humanMessage("Use room 42-1.", "user-2"),
      ],
      [{ description: "Check room availability and reserve 9:00-10:00." }],
    );

    expect(filtered.map((message) => message.id)).toEqual(["user-2"]);
  });

  it("keeps normal user follow-up messages that do not match workflow executor patterns", () => {
    const filtered = filterWorkflowMessages(
      [
        humanMessage("Use room 42-1 and invite the design team.", "user-1"),
        aiMessage("Will do.", "ai-1"),
      ],
      [{ description: "Check room availability and reserve 9:00-10:00." }],
    );

    expect(filtered.map((message) => message.id)).toEqual(["user-1", "ai-1"]);
  });

  it("removes workflow-internal ai and tool messages but keeps the top-level summary", () => {
    const filtered = filterWorkflowMessages(
      [
        humanMessage("Plan the launch and execute the subtasks.", "user-1"),
        aiToolCallMessage("ai-subtask-1", "web_search", "Searching for launch docs"),
        toolMessage("web_search", "tool-subtask-1"),
        aiMessage("Research is complete.", "ai-subtask-2"),
        aiMessage("All launch tasks are complete. Here is the final summary.", "ai-final"),
      ],
      [
        {
          description: "Research launch requirements",
          latestUpdate: "Searching for launch docs",
          result: "Research is complete.",
        },
      ],
    );

    expect(filtered.map((message) => message.id)).toEqual(["user-1", "ai-final"]);
  });

  it("keeps clarification messages visible in workflow mode", () => {
    const filtered = filterWorkflowMessages(
      [
        aiMessage("Need your input before I continue.", "ai-1"),
        {
          id: "ai-clarification",
          type: "ai",
          name: "ask_clarification",
          content: "Which room should I reserve?",
        },
        toolMessage("ask_clarification", "tool-clarification"),
      ],
      [{ description: "Reserve the room" }],
    );

    expect(filtered.map((message) => message.id)).toEqual([
      "ai-1",
      "ai-clarification",
      "tool-clarification",
    ]);
  });

  it("keeps present_files messages so artifact outputs still render in the transcript", () => {
    const filtered = filterWorkflowMessages(
      [
        aiToolCallMessage(
          "ai-files",
          "present_files",
          "Prepared the deliverables for you.",
        ),
      ],
      [{ description: "Prepare deliverables" }],
    );

    expect(filtered.map((message) => message.id)).toEqual(["ai-files"]);
  });
});

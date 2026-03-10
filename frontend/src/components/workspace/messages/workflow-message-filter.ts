import type { Message } from "@langchain/langgraph-sdk";

import {
  extractContentFromMessage,
  hasPresentFiles,
} from "@/core/messages/utils";

type WorkflowMessageTask = {
  description: string;
  latestUpdate?: string;
  statusDetail?: string;
  clarificationPrompt?: string;
  result?: string;
  error?: string;
};

function normalizeContent(value: string | null | undefined) {
  return (value ?? "").replace(/\r\n/g, "\n").trim();
}

export function filterWorkflowMessages(
  messages: Message[],
  workflowTasks: WorkflowMessageTask[],
) {
  const taskDescriptions = new Set(
    workflowTasks
      .map((task) => normalizeContent(task.description))
      .filter(Boolean),
  );
  const workflowTaskTexts = new Set(
    workflowTasks.flatMap((task) =>
      [
        task.description,
        task.latestUpdate,
        task.statusDetail,
        task.clarificationPrompt,
        task.result,
        task.error,
      ]
        .map((value) => normalizeContent(value))
        .filter(Boolean),
    ),
  );
  const firstHumanMessageId = messages.find((message) => message.type === "human")
    ?.id;

  return messages.filter((message) => {
    if (message.type === "human") {
      const content = normalizeContent(extractContentFromMessage(message));
      if (!content) {
        return true;
      }

      if (
        content.includes("Known facts (do not re-check):") ||
        content.includes("User clarification answer:")
      ) {
        return false;
      }

      if (message.id === firstHumanMessageId) {
        return true;
      }

      return !taskDescriptions.has(content);
    }

    if (message.type === "tool") {
      return message.name === "ask_clarification";
    }

    if (message.type !== "ai") {
      return true;
    }

    if (message.name === "ask_clarification") {
      return true;
    }

    if (message.tool_calls?.length && !hasPresentFiles(message)) {
      return false;
    }

    const content = normalizeContent(extractContentFromMessage(message));
    if (!content) {
      return true;
    }

    return !workflowTaskTexts.has(content);
  });
}

import type { Message } from "@langchain/langgraph-sdk";

import type { AgentThread } from "./types";

export function pathOfThread(threadId: string) {
  return `/workspace/chats/${threadId}`;
}

export function textOfMessage(message: Message) {
  if (typeof message.content === "string") {
    return message.content;
  } else if (Array.isArray(message.content)) {
    for (const part of message.content) {
      if (part.type === "text") {
        return part.text;
      }
    }
  }
  return null;
}

function normalizeTitleCandidate(value: unknown): string | null {
  if (typeof value !== "string") {
    return null;
  }
  const text = value
    .replaceAll("\r\n", "\n")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
  return text ? text : null;
}

function compactThreadTitle(title: string, maxChars = 60): string {
  if (title.length <= maxChars) {
    return title;
  }
  return title.slice(0, maxChars).trimEnd() + "…";
}

export function titleOfThread(thread: AgentThread) {
  const values = thread.values;
  const explicitTitle = normalizeTitleCandidate(values?.title);
  if (explicitTitle && explicitTitle !== "Untitled") {
    return compactThreadTitle(explicitTitle);
  }

  const derivedTitle =
    normalizeTitleCandidate(values?.planner_goal) ??
    normalizeTitleCandidate(values?.original_input) ??
    normalizeTitleCandidate(values?.workflow_stage_detail);
  if (derivedTitle) {
    return compactThreadTitle(derivedTitle);
  }

  return "Untitled";
}

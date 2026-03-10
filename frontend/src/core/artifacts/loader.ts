import type { BaseStream } from "@langchain/langgraph-sdk/react";

import type { AgentThreadState } from "../threads";

import { urlOfArtifact } from "./utils";
import { parseWriteFileUrl } from "./write-file-url";

export async function loadArtifactContent({
  filepath,
  threadId,
  isMock,
}: {
  filepath: string;
  threadId: string;
  isMock?: boolean;
}) {
  let enhancedFilepath = filepath;
  if (filepath.endsWith(".skill")) {
    enhancedFilepath = filepath + "/SKILL.md";
  }
  const url = urlOfArtifact({ filepath: enhancedFilepath, threadId, isMock });
  const response = await fetch(url);
  const text = await response.text();
  return text;
}

export function loadArtifactContentFromToolCall({
  url: urlString,
  thread,
}: {
  url: string;
  thread: BaseStream<AgentThreadState>;
}) {
  const { toolCallId, messageId } = parseWriteFileUrl(urlString);
  if (messageId && toolCallId) {
    const message = thread.messages.find((message) => message.id === messageId);
    if (message?.type === "ai" && message.tool_calls) {
      const toolCall = message.tool_calls.find(
        (toolCall) => toolCall.id === toolCallId,
      );
      if (toolCall) {
        return toolCall.args.content;
      }
    }
  }
}

import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { createContext, useContext } from "react";

import type { AgentThreadState } from "@/core/threads";
import type { RuntimeResumeRequest } from "@/core/threads/runtime-stream";

export interface ThreadContextType {
  thread: BaseStream<AgentThreadState>;
  resumeRuntime?: (
    threadId: string,
    body: RuntimeResumeRequest,
  ) => Promise<void>;
  isMock?: boolean;
}

export const ThreadContext = createContext<ThreadContextType | undefined>(
  undefined,
);

export function useThread() {
  const context = useContext(ThreadContext);
  if (context === undefined) {
    throw new Error("useThread must be used within a ThreadContext");
  }
  return context;
}

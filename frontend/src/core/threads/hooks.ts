import type { AIMessage, Message } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";

import { getAPIClient } from "../api";
import { useI18n } from "../i18n/hooks";
import type { FileInMessage } from "../messages/utils";
import type { LocalSettings } from "../settings";
import {
  fromLegacyTaskEvent,
  fromMultiAgentTaskEvent,
  fromMultiAgentTaskState,
} from "../tasks/adapters";
import { useTaskActions } from "../tasks/context";
import type { UploadedFileInfo } from "../uploads";
import { uploadFiles } from "../uploads";

import type { AgentThread, AgentThreadState } from "./types";

export type ToolEndEvent = {
  name: string;
  data: unknown;
};

export type ThreadAssistantId = "lead_agent" | "multi_agent";

type BaseTaskEvent = {
  type:
    | "task_started"
    | "task_running"
    | "task_completed"
    | "task_failed"
    | "task_timed_out";
  task_id: string;
  message?: AIMessage | string;
  result?: string;
  error?: string;
};

type MultiAgentTaskEvent = BaseTaskEvent & {
  source?: "multi_agent";
  run_id?: string;
  agent_name?: string;
  description?: string;
  status?: string;
  status_detail?: string;
  clarification_prompt?: string;
};

export type ThreadStreamOptions = {
  assistantId: ThreadAssistantId;
  threadId?: string | null | undefined;
  context: LocalSettings["context"];
  isMock?: boolean;
  onStart?: (threadId: string) => void;
  onFinish?: (state: AgentThreadState) => void;
  onToolEnd?: (event: ToolEndEvent) => void;
};

export function useThreadStream({
  assistantId,
  threadId,
  context,
  isMock,
  onStart,
  onFinish,
  onToolEnd,
}: ThreadStreamOptions) {
  const { t } = useI18n();
  const [_threadId, setThreadId] = useState<string | null>(threadId ?? null);
  const startedRef = useRef(false);
  const hydratedRunIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (_threadId && _threadId !== threadId) {
      setThreadId(threadId ?? null);
      startedRef.current = false;
    }
  }, [_threadId, threadId]);

  const queryClient = useQueryClient();
  const { hydrateTasks, resetTasksBySource, upsertTask } = useTaskActions();
  const thread = useStream<AgentThreadState>({
    client: getAPIClient(isMock),
    assistantId,
    threadId: _threadId,
    reconnectOnMount: true,
    fetchStateHistory: { limit: 1 },
    onCreated(meta) {
      setThreadId(meta.thread_id);
      if (!startedRef.current) {
        onStart?.(meta.thread_id);
        startedRef.current = true;
      }
    },
    onLangChainEvent(event) {
      if (event.event === "on_tool_end") {
        onToolEnd?.({
          name: event.name,
          data: event.data,
        });
      }
    },
    onCustomEvent(event: unknown) {
      if (typeof event !== "object" || event === null || !("type" in event)) {
        return;
      }

      const taskEvent = classifyTaskEvent(event);
      if (!taskEvent) {
        return;
      }

      if (taskEvent.kind === "multi_agent") {
        upsertTask(fromMultiAgentTaskEvent(taskEvent.event));
        return;
      }

      upsertTask(fromLegacyTaskEvent(taskEvent.event));
    },
    onFinish(state) {
      onFinish?.(state.values);
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    },
  });

  useEffect(() => {
    if (assistantId !== "multi_agent") {
      hydratedRunIdRef.current = null;
      return;
    }

    const runId = thread.values.run_id ?? null;
    const taskPool = thread.values.task_pool ?? [];
    if (runId === null && taskPool.length === 0) {
      hydratedRunIdRef.current = null;
      resetTasksBySource("multi_agent");
      return;
    }

    if (hydratedRunIdRef.current !== null && hydratedRunIdRef.current !== runId) {
      resetTasksBySource("multi_agent");
    }

    hydrateTasks(
      taskPool.map((task) => fromMultiAgentTaskState(task, _threadId ?? undefined)),
      {
        source: "multi_agent",
        runId,
      },
    );
    hydratedRunIdRef.current = runId;
  }, [
    assistantId,
    _threadId,
    hydrateTasks,
    resetTasksBySource,
    thread.values.run_id,
    thread.values.task_pool,
  ]);

  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const prevMsgCountRef = useRef(thread.messages.length);

  useEffect(() => {
    if (
      optimisticMessages.length > 0 &&
      thread.messages.length > prevMsgCountRef.current
    ) {
      setOptimisticMessages([]);
    }
  }, [thread.messages.length, optimisticMessages.length]);

  const sendMessage = useCallback(
    async (
      threadId: string,
      message: PromptInputMessage,
      extraContext?: Record<string, unknown>,
    ) => {
      const text = message.text.trim();

      prevMsgCountRef.current = thread.messages.length;

      const optimisticFiles: FileInMessage[] = (message.files ?? []).map(
        (f) => ({
          filename: f.filename ?? "",
          size: 0,
          status: "uploading" as const,
        }),
      );

      const optimisticHumanMsg: Message = {
        type: "human",
        id: `opt-human-${Date.now()}`,
        content: text ? [{ type: "text", text }] : "",
        additional_kwargs:
          optimisticFiles.length > 0 ? { files: optimisticFiles } : {},
      };

      const newOptimistic: Message[] = [optimisticHumanMsg];
      if (optimisticFiles.length > 0) {
        newOptimistic.push({
          type: "ai",
          id: `opt-ai-${Date.now()}`,
          content: t.uploads.uploadingFiles,
          additional_kwargs: { element: "task" },
        });
      }
      setOptimisticMessages(newOptimistic);

      if (!startedRef.current) {
        onStart?.(threadId);
        startedRef.current = true;
      }

      let uploadedFileInfo: UploadedFileInfo[] = [];

      try {
        if (message.files && message.files.length > 0) {
          try {
            const filePromises = message.files.map(async (fileUIPart) => {
              if (fileUIPart.url && fileUIPart.filename) {
                try {
                  const response = await fetch(fileUIPart.url);
                  const blob = await response.blob();

                  return new File([blob], fileUIPart.filename, {
                    type: fileUIPart.mediaType || blob.type,
                  });
                } catch (error) {
                  console.error(
                    `Failed to fetch file ${fileUIPart.filename}:`,
                    error,
                  );
                  return null;
                }
              }
              return null;
            });

            const conversionResults = await Promise.all(filePromises);
            const files = conversionResults.filter(
              (file): file is File => file !== null,
            );
            const failedConversions = conversionResults.length - files.length;

            if (failedConversions > 0) {
              throw new Error(
                `Failed to prepare ${failedConversions} attachment(s) for upload. Please retry.`,
              );
            }

            if (!threadId) {
              throw new Error("Thread is not ready for file upload.");
            }

            if (files.length > 0) {
              const uploadResponse = await uploadFiles(threadId, files);
              uploadedFileInfo = uploadResponse.files;

              const uploadedFiles: FileInMessage[] = uploadedFileInfo.map(
                (info) => ({
                  filename: info.filename,
                  size: info.size,
                  path: info.virtual_path,
                  status: "uploaded" as const,
                }),
              );
              setOptimisticMessages((messages) => {
                if (messages.length > 1 && messages[0]) {
                  const humanMessage: Message = messages[0];
                  return [
                    {
                      ...humanMessage,
                      additional_kwargs: { files: uploadedFiles },
                    },
                    ...messages.slice(1),
                  ];
                }
                return messages;
              });
            }
          } catch (error) {
            console.error("Failed to upload files:", error);
            const errorMessage =
              error instanceof Error
                ? error.message
                : "Failed to upload files.";
            toast.error(errorMessage);
            setOptimisticMessages([]);
            throw error;
          }
        }

        const filesForSubmit: FileInMessage[] = uploadedFileInfo.map(
          (info) => ({
            filename: info.filename,
            size: info.size,
            path: info.virtual_path,
            status: "uploaded" as const,
          }),
        );

        await thread.submit(
          {
            messages: [
              {
                type: "human",
                content: [
                  {
                    type: "text",
                    text,
                  },
                ],
                additional_kwargs:
                  filesForSubmit.length > 0 ? { files: filesForSubmit } : {},
              },
            ],
          },
          {
            threadId: threadId,
            streamSubgraphs: true,
            streamResumable: true,
            streamMode: ["values", "messages-tuple", "custom"],
            config: {
              recursion_limit: 1000,
            },
            context: {
              ...extraContext,
              ...context,
              thinking_enabled: context.mode !== "flash",
              is_plan_mode: context.mode === "pro" || context.mode === "ultra",
              subagent_enabled: context.mode === "ultra",
              thread_id: threadId,
            },
          },
        );
        void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
      } catch (error) {
        setOptimisticMessages([]);
        throw error;
      }
    },
    [thread, t.uploads.uploadingFiles, onStart, context, queryClient],
  );

  const mergedThread =
    optimisticMessages.length > 0
      ? ({
          ...thread,
          messages: [...thread.messages, ...optimisticMessages],
        } as typeof thread)
      : thread;

  return [mergedThread, sendMessage] as const;
}

export type ClassifiedTaskEvent =
  | {
      kind: "legacy";
      event: BaseTaskEvent;
    }
  | {
      kind: "multi_agent";
      event: MultiAgentTaskEvent;
    };

const TASK_EVENT_TYPES = new Set<BaseTaskEvent["type"]>([
  "task_started",
  "task_running",
  "task_completed",
  "task_failed",
  "task_timed_out",
]);

export function classifyTaskEvent(event: unknown): ClassifiedTaskEvent | null {
  if (typeof event !== "object" || event === null) {
    return null;
  }

  if (!("type" in event) || !("task_id" in event)) {
    return null;
  }

  const candidate = event as Partial<MultiAgentTaskEvent>;
  if (
    typeof candidate.type !== "string" ||
    !TASK_EVENT_TYPES.has(candidate.type) ||
    typeof candidate.task_id !== "string"
  ) {
    return null;
  }

  if (candidate.source === "multi_agent") {
    return {
      kind: "multi_agent",
      event: candidate as MultiAgentTaskEvent,
    };
  }

  if (candidate.source === undefined) {
    return {
      kind: "legacy",
      event: candidate as BaseTaskEvent,
    };
  }

  return null;
}

export function useThreads(
  params: Parameters<ThreadsClient["search"]>[0] = {
    limit: 50,
    sortBy: "updated_at",
    sortOrder: "desc",
    select: ["thread_id", "updated_at", "values"],
  },
) {
  const apiClient = getAPIClient();
  return useQuery<AgentThread[]>({
    queryKey: ["threads", "search", params],
    queryFn: async () => {
      const response = await apiClient.threads.search<AgentThreadState>(params);
      return response as AgentThread[];
    },
    refetchOnWindowFocus: false,
  });
}

export function useDeleteThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({ threadId }: { threadId: string }) => {
      await apiClient.threads.delete(threadId);
    },
    onSuccess(_, { threadId }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread>) => {
          return oldData.filter((t) => t.thread_id !== threadId);
        },
      );
    },
  });
}

export function useRenameThread() {
  const queryClient = useQueryClient();
  const apiClient = getAPIClient();
  return useMutation({
    mutationFn: async ({
      threadId,
      title,
    }: {
      threadId: string;
      title: string;
    }) => {
      await apiClient.threads.updateState(threadId, {
        values: { title },
      });
    },
    onSuccess(_, { threadId, title }) {
      queryClient.setQueriesData(
        {
          queryKey: ["threads", "search"],
          exact: false,
        },
        (oldData: Array<AgentThread>) => {
          return oldData.map((t) => {
            if (t.thread_id === threadId) {
              return {
                ...t,
                values: {
                  ...t.values,
                  title,
                },
              };
            }
            return t;
          });
        },
      );
    },
  });
}

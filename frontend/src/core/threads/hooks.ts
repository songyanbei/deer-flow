import type { AIMessage, Message } from "@langchain/langgraph-sdk";
import type { ThreadsClient } from "@langchain/langgraph-sdk/client";
import { useStream } from "@langchain/langgraph-sdk/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

export type ThreadAssistantId = "entry_graph" | "lead_agent" | "multi_agent";

type BaseTaskEvent = {
  type:
    | "task_started"
    | "task_running"
    | "task_waiting_dependency"
    | "task_help_requested"
    | "task_resumed"
    | "task_completed"
    | "task_failed"
    | "task_timed_out";
  source?: "legacy_subagent";
  task_id: string;
  message?: AIMessage | string;
  result?: string;
  error?: string;
};

type MultiAgentTaskEvent = Omit<BaseTaskEvent, "source"> & {
  source: "multi_agent";
  run_id?: string;
  agent_name?: string;
  description?: string;
  parent_task_id?: string;
  requested_by_agent?: string;
  request_help?: {
    problem: string;
    required_capability: string;
    reason: string;
    expected_output: string;
    context_payload?: Record<string, unknown> | null;
    candidate_agents?: string[] | null;
  };
  resolved_inputs?: Record<string, unknown>;
  blocked_reason?: string;
  resume_count?: number;
  status?: string;
  status_detail?: string;
  clarification_prompt?: string;
  resolved_orchestration_mode?: AgentThreadState["resolved_orchestration_mode"];
  orchestration_reason?: AgentThreadState["orchestration_reason"];
  workflow_stage?: AgentThreadState["workflow_stage"];
  workflow_stage_detail?: AgentThreadState["workflow_stage_detail"];
  workflow_stage_updated_at?: AgentThreadState["workflow_stage_updated_at"];
};

type ThreadEventPatch = Pick<
  AgentThreadState,
  | "resolved_orchestration_mode"
  | "orchestration_reason"
  | "workflow_stage"
  | "workflow_stage_detail"
  | "workflow_stage_updated_at"
  | "run_id"
>;

type LocalWorkflowShell = {
  stage: NonNullable<AgentThreadState["workflow_stage"]>;
  detail?: string;
  updatedAt: string;
};

const WORKFLOW_STAGES = new Set<NonNullable<AgentThreadState["workflow_stage"]>>([
  "queued",
  "acknowledged",
  "planning",
  "routing",
  "executing",
  "summarizing",
]);

function isWorkflowStage(
  value: unknown,
): value is NonNullable<AgentThreadState["workflow_stage"]> {
  return (
    typeof value === "string" &&
    WORKFLOW_STAGES.has(
      value as NonNullable<AgentThreadState["workflow_stage"]>,
    )
  );
}

function createLocalWorkflowShell(detail?: string): LocalWorkflowShell {
  return {
    stage: "queued",
    detail: detail?.trim() || undefined,
    updatedAt: new Date().toISOString(),
  };
}

function mergePatchedThreadValue<T>(
  value: T | null | undefined,
  patch: T | undefined,
  isLoading: boolean,
) {
  if (value !== undefined && value !== null) {
    return value;
  }

  if (isLoading && patch !== undefined) {
    return patch;
  }

  return value;
}

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
  const [eventPatch, setEventPatch] = useState<ThreadEventPatch>({});
  const [localWorkflowShell, setLocalWorkflowShell] =
    useState<LocalWorkflowShell | null>(null);
  const hadWorkflowHydrationRef = useRef(false);
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
      const patch = extractThreadEventPatch(event);
      if (patch) {
        setEventPatch((current) => ({
          resolved_orchestration_mode:
            patch.resolved_orchestration_mode ??
            current.resolved_orchestration_mode,
          orchestration_reason:
            patch.orchestration_reason ?? current.orchestration_reason,
          workflow_stage: patch.workflow_stage ?? current.workflow_stage,
          workflow_stage_detail:
            patch.workflow_stage_detail ?? current.workflow_stage_detail,
          workflow_stage_updated_at:
            patch.workflow_stage_updated_at ??
            current.workflow_stage_updated_at,
          run_id: patch.run_id ?? current.run_id,
        }));
      }

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
      setLocalWorkflowShell(null);
      onFinish?.(state.values);
      void queryClient.invalidateQueries({ queryKey: ["threads", "search"] });
    },
  });

  useEffect(() => {
    setEventPatch({});
    setLocalWorkflowShell(null);
    hadWorkflowHydrationRef.current = false;
    hydratedRunIdRef.current = null;
  }, [_threadId]);

  useEffect(() => {
    if (!localWorkflowShell) {
      return;
    }

    const hasAuthoritativeWorkflow =
      thread.values.resolved_orchestration_mode === "workflow" ||
      eventPatch.resolved_orchestration_mode === "workflow" ||
      thread.values.workflow_stage != null ||
      eventPatch.workflow_stage != null;

    if (hasAuthoritativeWorkflow) {
      setLocalWorkflowShell(null);
    }
  }, [
    eventPatch.resolved_orchestration_mode,
    eventPatch.workflow_stage,
    localWorkflowShell,
    thread.values.resolved_orchestration_mode,
    thread.values.workflow_stage,
  ]);

  const mergedValues = useMemo<AgentThreadState>(
    () => ({
      ...thread.values,
      resolved_orchestration_mode:
        mergePatchedThreadValue(
          thread.values.resolved_orchestration_mode,
          eventPatch.resolved_orchestration_mode,
          thread.isLoading,
        ) ??
        (localWorkflowShell ? "workflow" : null),
      orchestration_reason:
        mergePatchedThreadValue(
          thread.values.orchestration_reason,
          eventPatch.orchestration_reason,
          thread.isLoading,
        ) ?? null,
      workflow_stage:
        mergePatchedThreadValue(
          thread.values.workflow_stage,
          eventPatch.workflow_stage,
          thread.isLoading,
        ) ??
        localWorkflowShell?.stage ??
        null,
      workflow_stage_detail:
        mergePatchedThreadValue(
          thread.values.workflow_stage_detail,
          eventPatch.workflow_stage_detail,
          thread.isLoading,
        ) ??
        localWorkflowShell?.detail ??
        null,
      workflow_stage_updated_at:
        mergePatchedThreadValue(
          thread.values.workflow_stage_updated_at,
          eventPatch.workflow_stage_updated_at,
          thread.isLoading,
        ) ??
        localWorkflowShell?.updatedAt ??
        null,
      run_id:
        mergePatchedThreadValue(
          thread.values.run_id,
          eventPatch.run_id,
          thread.isLoading,
        ) ?? null,
    }),
    [eventPatch, localWorkflowShell, thread.isLoading, thread.values],
  );

  useEffect(() => {
    const runId = mergedValues.run_id ?? null;
    const taskPool = mergedValues.task_pool ?? [];
    const shouldHydrateWorkflow =
      mergedValues.resolved_orchestration_mode === "workflow" ||
      mergedValues.workflow_stage != null ||
      taskPool.length > 0;

    if (!shouldHydrateWorkflow) {
      if (hadWorkflowHydrationRef.current) {
        if (hydratedRunIdRef.current === null) {
          resetTasksBySource("multi_agent");
        } else {
          resetTasksBySource("multi_agent", hydratedRunIdRef.current);
        }
      }
      hadWorkflowHydrationRef.current = false;
      hydratedRunIdRef.current = null;
      return;
    }

    if (hadWorkflowHydrationRef.current && hydratedRunIdRef.current !== runId) {
      if (hydratedRunIdRef.current === null) {
        resetTasksBySource("multi_agent");
      } else {
        resetTasksBySource("multi_agent", hydratedRunIdRef.current);
      }
    }

    hydrateTasks(
      taskPool.map((task) => fromMultiAgentTaskState(task, _threadId ?? undefined)),
      {
        source: "multi_agent",
        runId,
      },
    );
    hadWorkflowHydrationRef.current = true;
    hydratedRunIdRef.current = runId;
  }, [
    _threadId,
    hydrateTasks,
    mergedValues.resolved_orchestration_mode,
    mergedValues.workflow_stage,
    mergedValues.run_id,
    mergedValues.task_pool,
    resetTasksBySource,
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
      const explicitWorkflowRequest =
        context.requested_orchestration_mode === "workflow";
      const shouldStreamSubgraphs = !explicitWorkflowRequest;

      prevMsgCountRef.current = thread.messages.length;

      if (explicitWorkflowRequest) {
        setLocalWorkflowShell(createLocalWorkflowShell(text));
      }

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
            streamSubgraphs: shouldStreamSubgraphs,
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
        setLocalWorkflowShell(null);
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
          values: mergedValues,
          messages: [...thread.messages, ...optimisticMessages],
        } as typeof thread)
      : ({
          ...thread,
          values: mergedValues,
        } as typeof thread);

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
  "task_waiting_dependency",
  "task_help_requested",
  "task_resumed",
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

  if (
    candidate.source === undefined ||
    candidate.source === "legacy_subagent"
  ) {
    return {
      kind: "legacy",
      event: candidate as BaseTaskEvent,
    };
  }

  return null;
}

function extractThreadEventPatch(event: unknown): ThreadEventPatch | null {
  if (typeof event !== "object" || event === null) {
    return null;
  }

  const candidate = event as Partial<
    Pick<
      MultiAgentTaskEvent,
      | "resolved_orchestration_mode"
      | "orchestration_reason"
      | "workflow_stage"
      | "workflow_stage_detail"
      | "workflow_stage_updated_at"
      | "run_id"
    >
  >;
  const patch: ThreadEventPatch = {};

  if (
    candidate.resolved_orchestration_mode === "leader" ||
    candidate.resolved_orchestration_mode === "workflow"
  ) {
    patch.resolved_orchestration_mode = candidate.resolved_orchestration_mode;
  }

  if (typeof candidate.orchestration_reason === "string") {
    patch.orchestration_reason = candidate.orchestration_reason;
  }

  if (isWorkflowStage(candidate.workflow_stage)) {
    patch.workflow_stage = candidate.workflow_stage;
  }

  if (typeof candidate.workflow_stage_detail === "string") {
    patch.workflow_stage_detail = candidate.workflow_stage_detail;
  }

  if (
    typeof candidate.workflow_stage_updated_at === "string" &&
    candidate.workflow_stage_updated_at
  ) {
    patch.workflow_stage_updated_at = candidate.workflow_stage_updated_at;
  }

  if (typeof candidate.run_id === "string" && candidate.run_id) {
    patch.run_id = candidate.run_id;
  }

  return Object.keys(patch).length > 0 ? patch : null;
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

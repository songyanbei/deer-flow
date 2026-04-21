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
import { useSubtaskContext, useTaskActions } from "../tasks/context";
import type { TaskViewModel } from "../tasks/types";
import type { UploadedFileInfo } from "../uploads";
import { uploadFiles } from "../uploads";

import {
  RuntimeStreamHttpError,
  streamRuntimeMessage,
  type RuntimeAppContext,
  type RuntimeStreamEvent,
  type RuntimeStreamRequest,
} from "./runtime-stream";
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
    | "task_waiting_intervention"
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
  pending_interrupt?: AgentThreadState["task_pool"] extends Array<infer Task>
    ? Task extends { pending_interrupt?: infer PendingInterrupt }
      ? PendingInterrupt
      : never
    : never;
  intervention_request?: AgentThreadState["task_pool"] extends Array<infer Task>
    ? Task extends { intervention_request?: infer Request }
      ? Request
      : never
    : never;
  intervention_status?: AgentThreadState["task_pool"] extends Array<infer Task>
    ? Task extends { intervention_status?: infer Status }
      ? Status
      : never
    : never;
  intervention_fingerprint?: string;
  intervention_resolution?: AgentThreadState["task_pool"] extends Array<
    infer Task
  >
    ? Task extends { intervention_resolution?: infer Resolution }
      ? Resolution
      : never
    : never;
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

function seedRecentThreadPreview(
  queryClient: ReturnType<typeof useQueryClient>,
  threadId: string,
  text: string,
) {
  const normalizedText = text.trim();
  if (!normalizedText) {
    return;
  }

  queryClient.setQueriesData(
    {
      queryKey: ["threads", "search"],
      exact: false,
    },
    (oldData: AgentThread[] | undefined) => {
      const threads = Array.isArray(oldData) ? [...oldData] : [];
      const now = new Date().toISOString();
      const index = threads.findIndex((thread) => thread.thread_id === threadId);

      if (index >= 0) {
        const existing = threads[index]!;
        const updated: AgentThread = {
          ...existing,
          updated_at: now,
          values: {
            ...existing.values,
            original_input: existing.values?.original_input ?? normalizedText,
            planner_goal: existing.values?.planner_goal ?? normalizedText,
          },
        };
        threads.splice(index, 1);
        threads.unshift(updated);
        return threads;
      }

      return [
        ({
          thread_id: threadId,
          updated_at: now,
          values: {
            title: "Untitled",
            messages: [],
            artifacts: [],
            original_input: normalizedText,
            planner_goal: normalizedText,
          },
        } as unknown) as AgentThread,
        ...threads,
      ];
    },
  );
}

type LocalWorkflowShell = {
  stage: NonNullable<AgentThreadState["workflow_stage"]>;
  detail?: string;
  updatedAt: string;
  previousRunId: string | null;
};

type PendingClarificationTask =
  NonNullable<AgentThreadState["task_pool"]>[number];

const WORKFLOW_STAGES = new Set<
  NonNullable<AgentThreadState["workflow_stage"]>
>([
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

function createLocalWorkflowShell(
  detail?: string,
  previousRunId?: string | null,
): LocalWorkflowShell {
  return {
    stage: "acknowledged",
    detail: detail?.trim() ?? undefined,
    updatedAt: new Date().toISOString(),
    previousRunId: previousRunId ?? null,
  };
}

function findPendingClarificationTask(
  values: AgentThreadState,
): PendingClarificationTask | null {
  const taskPool = values.task_pool ?? [];
  for (let idx = taskPool.length - 1; idx >= 0; idx -= 1) {
    const task = taskPool[idx];
    if (!task) {
      continue;
    }
    if (
      task.status === "RUNNING" &&
      typeof task.clarification_prompt === "string" &&
      task.clarification_prompt.trim()
    ) {
      return task;
    }
  }
  return null;
}

function findPendingClarificationTaskFromStore(
  tasksById: Record<string, TaskViewModel>,
  orderedTaskIds: string[],
  runId: string | null,
): TaskViewModel | null {
  for (let idx = orderedTaskIds.length - 1; idx >= 0; idx -= 1) {
    const taskId = orderedTaskIds[idx];
    const task = taskId ? tasksById[taskId] : undefined;
    if (task?.source !== "multi_agent") {
      continue;
    }
    if (task.status !== "waiting_clarification") {
      continue;
    }
    if (runId && task.runId?.trim() && task.runId !== runId) {
      continue;
    }
    return task;
  }
  return null;
}

function getClarificationTaskRunId(
  task: PendingClarificationTask | TaskViewModel,
) {
  return "source" in task ? task.runId : task.run_id;
}

function getClarificationTaskAgentName(
  task: PendingClarificationTask | TaskViewModel,
) {
  return "source" in task ? task.agentName : task.assigned_agent;
}

function getClarificationTaskParentId(
  task: PendingClarificationTask | TaskViewModel,
) {
  return "source" in task ? task.parentTaskId : task.parent_task_id;
}

function getClarificationTaskRequester(
  task: PendingClarificationTask | TaskViewModel,
) {
  return "source" in task ? task.requestedByAgent : task.requested_by_agent;
}

function parseWorkflowStageUpdatedAt(value: string | null | undefined) {
  if (!value) {
    return null;
  }

  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? null : timestamp;
}

function shouldPreferIncomingStage(
  current:
    | ThreadEventPatch
    | Pick<
        AgentThreadState,
        | "workflow_stage"
        | "workflow_stage_detail"
        | "workflow_stage_updated_at"
        | "run_id"
      >,
  incoming: ThreadEventPatch,
) {
  const currentTime = parseWorkflowStageUpdatedAt(
    current.workflow_stage_updated_at,
  );
  const incomingTime = parseWorkflowStageUpdatedAt(
    incoming.workflow_stage_updated_at,
  );

  if (incomingTime !== null) {
    if (currentTime === null) {
      return true;
    }
    return incomingTime >= currentTime;
  }

  if (incoming.workflow_stage && current.workflow_stage == null) {
    return true;
  }

  if (incoming.workflow_stage_detail && current.workflow_stage_detail == null) {
    return true;
  }

  return false;
}

function mergeThreadEventPatch(
  current: ThreadEventPatch,
  incoming: ThreadEventPatch,
  staleRunIds?: ReadonlySet<string>,
): ThreadEventPatch {
  if (Object.keys(current).length === 0) {
    return incoming;
  }

  const currentRunId = current.run_id ?? null;
  const incomingRunId = incoming.run_id ?? null;

  if (
    incomingRunId !== null &&
    (currentRunId === null || incomingRunId !== currentRunId)
  ) {
    if (staleRunIds?.has(incomingRunId)) {
      return current;
    }
    return incoming;
  }

  const preferIncomingStage = shouldPreferIncomingStage(current, incoming);

  return {
    resolved_orchestration_mode:
      incoming.resolved_orchestration_mode ??
      current.resolved_orchestration_mode,
    orchestration_reason:
      incoming.orchestration_reason ?? current.orchestration_reason,
    workflow_stage: preferIncomingStage
      ? (incoming.workflow_stage ?? current.workflow_stage)
      : current.workflow_stage,
    workflow_stage_detail: preferIncomingStage
      ? (incoming.workflow_stage_detail ?? current.workflow_stage_detail)
      : current.workflow_stage_detail,
    workflow_stage_updated_at: preferIncomingStage
      ? (incoming.workflow_stage_updated_at ??
        current.workflow_stage_updated_at)
      : current.workflow_stage_updated_at,
    run_id: incoming.run_id ?? current.run_id,
  };
}

function mergeThreadValuesWithPatch(
  values: AgentThreadState,
  patch: ThreadEventPatch,
  isLoading: boolean,
  hasLocalWorkflowShell: boolean,
) {
  if (Object.keys(patch).length === 0) {
    return values;
  }

  const valueRunId = values.run_id ?? null;
  const patchRunId = patch.run_id ?? null;
  const replacesRun =
    patchRunId !== null && valueRunId !== null && patchRunId !== valueRunId;
  const fillsLoadingGap =
    isLoading && patchRunId !== null && valueRunId === null;
  const preferPatchStage = shouldPreferIncomingStage(values, patch);
  const canApplyPatchStage =
    preferPatchStage &&
    (isLoading || valueRunId !== null || hasLocalWorkflowShell);
  const mergedValues: AgentThreadState = { ...values };

  if (replacesRun || fillsLoadingGap) {
    mergedValues.run_id = patch.run_id ?? mergedValues.run_id ?? null;
    mergedValues.resolved_orchestration_mode =
      patch.resolved_orchestration_mode ??
      mergedValues.resolved_orchestration_mode ??
      null;
    mergedValues.orchestration_reason =
      patch.orchestration_reason ?? mergedValues.orchestration_reason ?? null;
    // A newly identified run must not reuse the previous run's stage shell.
    mergedValues.workflow_stage = patch.workflow_stage ?? null;
    mergedValues.workflow_stage_detail = patch.workflow_stage_detail ?? null;
    mergedValues.workflow_stage_updated_at =
      patch.workflow_stage_updated_at ?? null;
    return mergedValues;
  }

  if (canApplyPatchStage) {
    mergedValues.workflow_stage =
      patch.workflow_stage ?? mergedValues.workflow_stage ?? null;
    mergedValues.workflow_stage_detail =
      patch.workflow_stage_detail ?? mergedValues.workflow_stage_detail ?? null;
    mergedValues.workflow_stage_updated_at =
      patch.workflow_stage_updated_at ??
      mergedValues.workflow_stage_updated_at ??
      null;
    mergedValues.run_id = patch.run_id ?? mergedValues.run_id ?? null;
  }

  if (isLoading || canApplyPatchStage) {
    if (patch.resolved_orchestration_mode != null) {
      mergedValues.resolved_orchestration_mode =
        patch.resolved_orchestration_mode;
    }
    if (patch.orchestration_reason != null) {
      mergedValues.orchestration_reason = patch.orchestration_reason;
    }
  }

  if (isLoading && mergedValues.run_id == null) {
    mergedValues.run_id = patch.run_id ?? null;
  }

  return mergedValues;
}

function shouldClearLocalWorkflowShell(
  localWorkflowShell: LocalWorkflowShell,
  values: AgentThreadState,
  patch: ThreadEventPatch,
) {
  if (patch.workflow_stage != null) {
    return true;
  }

  if (values.workflow_stage == null) {
    return false;
  }

  const authoritativeRunId = values.run_id ?? null;
  if (authoritativeRunId == null) {
    return false;
  }

  return authoritativeRunId !== localWorkflowShell.previousRunId;
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
  const { orderedTaskIds, tasksById } = useSubtaskContext();
  const { hydrateTasks, resetTasksBySource, upsertTask } = useTaskActions();
  const [eventPatch, setEventPatch] = useState<ThreadEventPatch>({});
  const [localWorkflowShell, setLocalWorkflowShell] =
    useState<LocalWorkflowShell | null>(null);
  const hadWorkflowHydrationRef = useRef(false);
  const staleRunIdsRef = useRef<Set<string>>(new Set());
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
        setEventPatch((current) => {
          const currentRunId = current.run_id ?? thread.values.run_id ?? null;
          const incomingRunId = patch.run_id ?? null;
          if (
            currentRunId !== null &&
            incomingRunId !== null &&
            currentRunId !== incomingRunId
          ) {
            staleRunIdsRef.current.add(currentRunId);
          }
          return mergeThreadEventPatch(
            current,
            patch,
            staleRunIdsRef.current,
          );
        });
      }

      if (typeof event !== "object" || event === null || !("type" in event)) {
        return;
      }

      const taskEvent = classifyTaskEvent(event);
      if (!taskEvent) {
        return;
      }

      if (taskEvent.kind === "multi_agent") {
        upsertTask(fromMultiAgentTaskEvent(taskEvent.event, _threadId ?? undefined));
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
    staleRunIdsRef.current = new Set();
  }, [_threadId]);

  useEffect(() => {
    if (!localWorkflowShell) {
      return;
    }

    if (
      shouldClearLocalWorkflowShell(
        localWorkflowShell,
        thread.values,
        eventPatch,
      )
    ) {
      setLocalWorkflowShell(null);
    }
  }, [
    eventPatch,
    eventPatch.workflow_stage,
    localWorkflowShell,
    thread.values,
    thread.values.run_id,
    thread.values.workflow_stage,
  ]);

  const mergedPatchedValues = useMemo(
    () =>
      mergeThreadValuesWithPatch(
        thread.values,
        eventPatch,
        thread.isLoading,
        localWorkflowShell != null,
      ),
    [eventPatch, localWorkflowShell, thread.isLoading, thread.values],
  );

  const mergedValues = useMemo<AgentThreadState>(
    () => {
      const localShellSupersedesPatchedStage =
        localWorkflowShell != null &&
        mergedPatchedValues.workflow_stage != null &&
        (mergedPatchedValues.run_id ?? null) === localWorkflowShell.previousRunId;

      return {
        ...mergedPatchedValues,
        resolved_orchestration_mode:
          mergedPatchedValues.resolved_orchestration_mode ??
          (localWorkflowShell ? "workflow" : null),
        orchestration_reason: mergedPatchedValues.orchestration_reason ?? null,
        workflow_stage: localShellSupersedesPatchedStage
          ? localWorkflowShell.stage
          : (mergedPatchedValues.workflow_stage ?? localWorkflowShell?.stage ?? null),
        workflow_stage_detail: localShellSupersedesPatchedStage
          ? (localWorkflowShell.detail ?? null)
          : (mergedPatchedValues.workflow_stage_detail ??
            localWorkflowShell?.detail ??
            null),
        workflow_stage_updated_at: localShellSupersedesPatchedStage
          ? localWorkflowShell.updatedAt
          : (mergedPatchedValues.workflow_stage_updated_at ??
            localWorkflowShell?.updatedAt ??
            null),
        run_id:
          mergedPatchedValues.run_id ??
          (localWorkflowShell ? (eventPatch.run_id ?? null) : null),
      };
    },
    [eventPatch.run_id, localWorkflowShell, mergedPatchedValues],
  );

  useEffect(() => {
    const runId = mergedValues.run_id ?? null;
    const taskPool = mergedValues.task_pool ?? [];
    const shouldPreserveExistingTasksDuringLoading =
      thread.isLoading &&
      localWorkflowShell == null &&
      taskPool.length === 0 &&
      hadWorkflowHydrationRef.current &&
      (runId === null || hydratedRunIdRef.current === runId);
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

    if (shouldPreserveExistingTasksDuringLoading) {
      return;
    }

    hydrateTasks(
      taskPool.map((task) =>
        fromMultiAgentTaskState(task, _threadId ?? undefined),
      ),
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
    localWorkflowShell,
    resetTasksBySource,
    thread.isLoading,
  ]);

  const [optimisticMessages, setOptimisticMessages] = useState<Message[]>([]);
  const prevMsgCountRef = useRef(thread.messages.length);

  // ── Gateway SSE live-run state ────────────────────────────────────
  // Phase 1 D1.2: main chat submits go through the Gateway
  // ``POST /api/runtime/threads/{id}/messages:stream`` endpoint. The SSE
  // consumer drives this local state; ``useStream`` is kept only for
  // initial thread hydration (``fetchStateHistory``) and never submits.
  const [liveValuesPatch, setLiveValuesPatch] = useState<
    Partial<AgentThreadState>
  >({});
  const [streamingAi, setStreamingAi] = useState<{
    id: string;
    content: string;
  } | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const abortControllerRef = useRef<AbortController | null>(null);

  const refetchThreadState = useCallback(async () => {
    if (!_threadId) return;
    try {
      const client = getAPIClient(isMock);
      const state = await client.threads.getState<AgentThreadState>(_threadId);
      if (state?.values) {
        // Re-hydrate authoritative messages/artifacts/etc. after the Gateway
        // run completes. We keep the shape aligned with useStream's values.
        setLiveValuesPatch((prev) => ({
          ...prev,
          messages: state.values.messages ?? prev.messages,
          artifacts: state.values.artifacts ?? prev.artifacts,
          title: state.values.title ?? prev.title,
        }));
      }
    } catch {
      // Hydration failures are non-fatal; state_snapshot events already
      // carried the primary fields needed by the UI surfaces.
    }
  }, [_threadId, isMock]);

  useEffect(() => {
    // New thread id → discard any in-flight Gateway run and reset live state.
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setLiveValuesPatch({});
    setStreamingAi(null);
    setIsRunning(false);
  }, [_threadId]);

  useEffect(() => {
    return () => {
      // Unmount guard: abort any in-flight Gateway stream.
      abortControllerRef.current?.abort();
    };
  }, []);

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
      const currentRunId = mergedValues.run_id ?? eventPatch.run_id ?? null;
      const clarificationTaskFromStore = findPendingClarificationTaskFromStore(
        tasksById,
        orderedTaskIds,
        currentRunId,
      );
      const clarificationTask =
        clarificationTaskFromStore ??
        findPendingClarificationTask(mergedValues);
      const isClarificationResume =
        explicitWorkflowRequest &&
        (clarificationTask !== null ||
          (mergedValues.resolved_orchestration_mode === "workflow" &&
            mergedValues.execution_state === "INTERRUPTED"));
      const shouldStreamSubgraphs = !explicitWorkflowRequest;
      const clarificationResumeTaskId =
        clarificationTask == null
          ? undefined
          : ("task_id" in clarificationTask
            ? clarificationTask.task_id
            : clarificationTask.id);

      prevMsgCountRef.current = thread.messages.length;

      seedRecentThreadPreview(queryClient, threadId, text);

      if (explicitWorkflowRequest && !isClarificationResume) {
        setLocalWorkflowShell(
          createLocalWorkflowShell(
            text,
            currentRunId,
          ),
        );
      }

      if (isClarificationResume && clarificationTask) {
        upsertTask({
          id:
            "task_id" in clarificationTask
              ? clarificationTask.task_id
              : clarificationTask.id,
          source: "multi_agent",
          runId: getClarificationTaskRunId(clarificationTask) ?? currentRunId ?? undefined,
          description: clarificationTask.description,
          agentName: getClarificationTaskAgentName(clarificationTask) ?? undefined,
          parentTaskId: getClarificationTaskParentId(clarificationTask) ?? undefined,
          requestedByAgent: getClarificationTaskRequester(clarificationTask) ?? undefined,
          status: "in_progress",
          statusDetail: undefined,
          clarificationPrompt: undefined,
          latestUpdate: undefined,
          updatedAt: new Date().toISOString(),
        });
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

        // ── Build Gateway submit payload ────────────────────────────
        // App-level runtime flags travel through ``app_context`` (validated
        // server-side with ``extra="forbid"``). Identity fields are never
        // sent from the browser — Gateway injects them from the session.
        const isBootstrapAgent =
          typeof extraContext?.is_bootstrap === "boolean"
            ? extraContext.is_bootstrap
            : undefined;
        const workflowClarificationAnswers = extractClarificationAnswers(
          extraContext?.workflow_clarification_response,
        );

        const appContext: RuntimeAppContext = {};
        if (context.mode === "flash") {
          appContext.thinking_enabled = false;
        } else if (
          context.mode === "thinking" ||
          context.mode === "pro" ||
          context.mode === "ultra"
        ) {
          appContext.thinking_enabled = true;
        }
        if (context.mode === "pro" || context.mode === "ultra") {
          appContext.is_plan_mode = true;
        }
        if (context.mode === "ultra") {
          appContext.subagent_enabled = true;
        }
        if (isBootstrapAgent !== undefined) {
          appContext.is_bootstrap = isBootstrapAgent;
        }
        if (isClarificationResume) {
          appContext.workflow_clarification_resume = true;
          if (currentRunId) appContext.workflow_resume_run_id = currentRunId;
          if (clarificationResumeTaskId) {
            appContext.workflow_resume_task_id = clarificationResumeTaskId;
          }
          if (workflowClarificationAnswers) {
            appContext.workflow_clarification_response = {
              answers: workflowClarificationAnswers,
            };
          }
        }

        const humanContent = text ? [{ type: "text" as const, text }] : "";
        const humanAdditionalKwargs: Record<string, unknown> =
          uploadedFileInfo.length > 0
            ? {
                files: uploadedFileInfo.map((info) => ({
                  filename: info.filename,
                  size: info.size,
                  path: info.virtual_path,
                  status: "uploaded" as const,
                })),
              }
            : {};
        // Upload-annotated messages go via the message text — file metadata
        // reaches the agent through ``uploads`` directory on the sandbox.
        // Gateway request body only carries the text.
        const requestedMode = context.requested_orchestration_mode;
        const agentName = context.agent_name;
        const body: RuntimeStreamRequest = {
          message: text,
          app_context:
            Object.keys(appContext).length > 0 ? appContext : undefined,
          requested_orchestration_mode:
            requestedMode === "auto" ||
            requestedMode === "leader" ||
            requestedMode === "workflow"
              ? requestedMode
              : undefined,
          entry_agent:
            typeof agentName === "string" && agentName ? agentName : undefined,
        };
        void humanContent;
        void humanAdditionalKwargs;
        void shouldStreamSubgraphs;

        // Start the Gateway SSE stream with a fresh AbortController.
        abortControllerRef.current?.abort();
        const abortController = new AbortController();
        abortControllerRef.current = abortController;
        setIsRunning(true);
        setStreamingAi(null);

        let runFailed = false;
        let lastRunId: string | null = null;

        try {
          const iter = streamRuntimeMessage(threadId, body, {
            signal: abortController.signal,
          });

          for await (const event of iter) {
            if (abortController.signal.aborted) break;
            lastRunId = event.data.run_id ?? lastRunId;
            applyGatewayEvent({
              event,
              onStateSnapshot: (patch, customPatch) => {
                setLiveValuesPatch((prev) => ({ ...prev, ...patch }));
                if (customPatch) {
                  setEventPatch((current) => {
                    const currentRunIdLocal =
                      current.run_id ?? thread.values.run_id ?? null;
                    const incomingRunIdLocal = customPatch.run_id ?? null;
                    if (
                      currentRunIdLocal !== null &&
                      incomingRunIdLocal !== null &&
                      currentRunIdLocal !== incomingRunIdLocal
                    ) {
                      staleRunIdsRef.current.add(currentRunIdLocal);
                    }
                    return mergeThreadEventPatch(
                      current,
                      customPatch,
                      staleRunIdsRef.current,
                    );
                  });
                }
              },
              onMessageDelta: (deltaContent) => {
                setStreamingAi((prev) => ({
                  id: prev?.id ?? `live-ai-${Date.now()}`,
                  content: (prev?.content ?? "") + deltaContent,
                }));
              },
              onMessageCompleted: () => {
                setStreamingAi(null);
              },
              onArtifactCreated: (artifact) => {
                setLiveValuesPatch((prev) => {
                  const existing =
                    prev.artifacts ?? thread.values.artifacts ?? [];
                  const candidateUrl = artifact.artifact_url;
                  const artifactKey =
                    typeof candidateUrl === "string" && candidateUrl
                      ? candidateUrl
                      : JSON.stringify(artifact);
                  if (existing.includes(artifactKey)) return prev;
                  return {
                    ...prev,
                    artifacts: [...existing, artifactKey],
                  };
                });
              },
              onCustomTaskEvent: (taskEvent) => {
                const classified = classifyTaskEvent(taskEvent);
                if (!classified) return;
                if (classified.kind === "multi_agent") {
                  upsertTask(
                    fromMultiAgentTaskEvent(
                      classified.event,
                      _threadId ?? undefined,
                    ),
                  );
                } else {
                  upsertTask(fromLegacyTaskEvent(classified.event));
                }
              },
              onRunCompleted: () => {
                setIsRunning(false);
                setStreamingAi(null);
                setLocalWorkflowShell(null);
                void refetchThreadState();
                void queryClient.invalidateQueries({
                  queryKey: ["threads", "search"],
                });
              },
              onRunFailed: (errorText) => {
                runFailed = true;
                setIsRunning(false);
                setStreamingAi(null);
                setLocalWorkflowShell(null);
                toast.error(errorText || "Runtime execution failed");
              },
            });
          }

          // Fire the onFinish callback with the best-effort merged state so
          // downstream (notifications, query invalidation) matches the
          // legacy useStream.onFinish behavior.
          if (!runFailed && !abortController.signal.aborted) {
            const finishedValues: AgentThreadState = {
              ...thread.values,
              ...liveValuesPatch,
              run_id: lastRunId ?? thread.values.run_id ?? null,
            };
            onFinish?.(finishedValues);
          }
        } catch (error) {
          if (
            error instanceof DOMException &&
            error.name === "AbortError"
          ) {
            // user-initiated stop — silent.
            setIsRunning(false);
            setStreamingAi(null);
          } else if (error instanceof RuntimeStreamHttpError) {
            setIsRunning(false);
            setStreamingAi(null);
            setLocalWorkflowShell(null);
            setOptimisticMessages([]);
            toast.error(
              `Gateway rejected the submission (${error.status})${error.detail ? `: ${error.detail}` : ""}`,
            );
            throw error;
          } else {
            setIsRunning(false);
            setStreamingAi(null);
            setLocalWorkflowShell(null);
            setOptimisticMessages([]);
            throw error;
          }
        } finally {
          if (abortControllerRef.current === abortController) {
            abortControllerRef.current = null;
          }
        }
      } catch (error) {
        setLocalWorkflowShell(null);
        setOptimisticMessages([]);
        throw error;
      }
    },
    [
      thread,
      t.uploads.uploadingFiles,
      onStart,
      context,
      tasksById,
      orderedTaskIds,
      queryClient,
      eventPatch.run_id,
      mergedValues,
      upsertTask,
      _threadId,
      liveValuesPatch,
      refetchThreadState,
      onFinish,
    ],
  );

  const stop = useCallback(async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    setIsRunning(false);
    setStreamingAi(null);
  }, []);

  const liveStreamingMessages = useMemo<Message[]>(() => {
    if (!streamingAi) return [];
    return [
      {
        type: "ai",
        id: streamingAi.id,
        content: streamingAi.content,
        additional_kwargs: {},
      } as Message,
    ];
  }, [streamingAi]);

  const mergedThread = useMemo(() => {
    const baseMessages = liveValuesPatch.messages ?? thread.messages;
    const extraMessages = [...optimisticMessages, ...liveStreamingMessages];
    return {
      ...thread,
      values: {
        ...mergedValues,
        ...liveValuesPatch,
        // mergedValues wins for workflow-stage-sensitive fields because it
        // already includes the local shell + event-patch logic.
        resolved_orchestration_mode: mergedValues.resolved_orchestration_mode,
        orchestration_reason: mergedValues.orchestration_reason,
        workflow_stage: mergedValues.workflow_stage,
        workflow_stage_detail: mergedValues.workflow_stage_detail,
        workflow_stage_updated_at: mergedValues.workflow_stage_updated_at,
        run_id: mergedValues.run_id ?? liveValuesPatch.run_id ?? null,
      },
      messages:
        extraMessages.length > 0
          ? [...baseMessages, ...extraMessages]
          : baseMessages,
      isLoading: isRunning || thread.isLoading,
      stop,
    } as typeof thread;
  }, [
    isRunning,
    liveStreamingMessages,
    liveValuesPatch,
    mergedValues,
    optimisticMessages,
    stop,
    thread,
  ]);

  return [mergedThread, sendMessage] as const;
}

function extractClarificationAnswers(
  value: unknown,
): Record<string, { text: string }> | null {
  if (typeof value !== "object" || value === null) return null;
  const container = value as { answers?: unknown };
  if (typeof container.answers !== "object" || container.answers === null) {
    return null;
  }
  const result: Record<string, { text: string }> = {};
  for (const [key, raw] of Object.entries(
    container.answers as Record<string, unknown>,
  )) {
    if (typeof raw === "object" && raw !== null) {
      const candidate = raw as { text?: unknown };
      if (typeof candidate.text === "string") {
        result[key] = { text: candidate.text };
      }
    }
  }
  return Object.keys(result).length > 0 ? result : null;
}

type GatewayEventApplyOptions = {
  event: RuntimeStreamEvent;
  onStateSnapshot: (
    patch: Partial<AgentThreadState>,
    customPatch: ThreadEventPatch | null,
  ) => void;
  onMessageDelta: (delta: string) => void;
  onMessageCompleted: () => void;
  onArtifactCreated: (artifact: Record<string, unknown>) => void;
  onCustomTaskEvent: (payload: Record<string, unknown>) => void;
  onRunCompleted: () => void;
  onRunFailed: (error: string) => void;
};

const CUSTOM_TASK_EVENT_NAMES = new Set<RuntimeStreamEvent["type"]>([
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
]);

function applyGatewayEvent(options: GatewayEventApplyOptions) {
  const { event } = options;
  switch (event.type) {
    case "ack":
      return;
    case "state_snapshot": {
      const snapshot = event.data;
      const patch: Partial<AgentThreadState> = {};
      if (snapshot.title !== undefined) {
        patch.title = snapshot.title ?? "";
      }
      if (snapshot.todos !== undefined) {
        patch.todos = snapshot.todos as AgentThreadState["todos"];
      }
      if (snapshot.task_pool !== undefined) {
        patch.task_pool = snapshot.task_pool as AgentThreadState["task_pool"];
      }
      if (snapshot.workflow_stage !== undefined) {
        patch.workflow_stage =
          snapshot.workflow_stage as AgentThreadState["workflow_stage"];
      }
      if (snapshot.workflow_stage_detail !== undefined) {
        patch.workflow_stage_detail = snapshot.workflow_stage_detail ?? null;
      }
      if (snapshot.workflow_stage_updated_at !== undefined) {
        patch.workflow_stage_updated_at =
          snapshot.workflow_stage_updated_at ?? null;
      }
      if (snapshot.resolved_orchestration_mode !== undefined) {
        patch.resolved_orchestration_mode =
          snapshot.resolved_orchestration_mode as AgentThreadState["resolved_orchestration_mode"];
      }
      if (snapshot.orchestration_reason !== undefined) {
        patch.orchestration_reason = snapshot.orchestration_reason ?? null;
      }
      if (snapshot.run_id) {
        patch.run_id = snapshot.run_id;
      }

      const customPatch = extractThreadEventPatch({
        ...snapshot,
        type: "state_snapshot",
      });
      options.onStateSnapshot(patch, customPatch);
      return;
    }
    case "message_delta":
      options.onMessageDelta(event.data.content ?? "");
      return;
    case "message_completed":
      options.onMessageCompleted();
      return;
    case "artifact_created":
      options.onArtifactCreated(event.data.artifact ?? {});
      return;
    case "run_completed":
      options.onRunCompleted();
      return;
    case "run_failed":
      options.onRunFailed(event.data.error || "");
      return;
    case "intervention_requested":
    case "governance_created":
      // Phase 2 targets — the state_snapshot task_pool/governance_queue
      // projection still carries the authoritative fields consumed by
      // intervention/governance cards, so Phase 1 UI remains correct.
      return;
    default:
      if (CUSTOM_TASK_EVENT_NAMES.has(event.type)) {
        options.onCustomTaskEvent({
          ...(event.data as Record<string, unknown>),
          type: event.type,
        });
      }
      return;
  }
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
  "task_waiting_intervention",
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

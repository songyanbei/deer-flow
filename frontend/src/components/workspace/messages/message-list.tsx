import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import type { StickToBottomContext } from "use-stick-to-bottom";

import {
  Conversation,
  ConversationContent,
} from "@/components/ai-elements/conversation";
import { useI18n } from "@/core/i18n/hooks";
import {
  extractContentFromMessage,
  extractPresentFilesFromMessage,
  groupMessages,
  hasContent,
  hasPresentFiles,
  hasReasoning,
} from "@/core/messages/utils";
import { useRehypeSplitWordsIntoSpans } from "@/core/rehype";
import {
  fromLegacyTaskToolCall,
  fromLegacyToolMessage,
} from "@/core/tasks";
import { useSubtaskContext, useUpdateSubtask } from "@/core/tasks/context";
import type { TaskUpsert } from "@/core/tasks/types";
import type { AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

import { ArtifactFileList } from "../artifacts/artifact-file-list";
import { StreamingIndicator } from "../streaming-indicator";
import {
  filterWorkflowTasks,
  getWorkflowProgressSummary,
} from "../workflow-progress";

import { MarkdownContent } from "./markdown-content";
import { MessageGroup } from "./message-group";
import { MessageListItem } from "./message-list-item";
import { MessageListSkeleton } from "./skeleton";
import { SubtaskCard } from "./subtask-card";
import { filterWorkflowMessages } from "./workflow-message-filter";
import { InterventionCard } from "./intervention-card";
import {
  ClarificationCard,
  type ClarificationSubmitPayload,
  splitClarificationQuestions,
} from "./clarification-card";

const ANIMATABLE_ASSISTANT_GROUP_TYPES = new Set([
  "assistant",
  "assistant:processing",
  "assistant:clarification",
  "assistant:present-files",
  "assistant:subagent",
]);
const MESSAGE_LIST_GAP = 32;
const VIRTUALIZATION_OVERSCAN = 960;
const VIRTUALIZATION_THRESHOLD = 24;

type VirtualizedRenderItem = {
  key: string;
  estimateHeight: number;
  content: ReactNode;
};

export function MessageList({
  className,
  threadId,
  thread,
  stoppedByUser = false,
  paddingBottom = 160,
  onSubmitClarification,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  stoppedByUser?: boolean;
  paddingBottom?: number;
  onSubmitClarification?: (payload: ClarificationSubmitPayload) => void;
}) {
  const { t } = useI18n();
  const animatedRehypePlugins = useRehypeSplitWordsIntoSpans(true);
  const staticRehypePlugins = useRehypeSplitWordsIntoSpans(false);
  const updateSubtask = useUpdateSubtask();
  const { orderedTaskIds, tasksById } = useSubtaskContext();
  const messages = thread.messages;
  const workflowTasks = filterWorkflowTasks(
    tasksById,
    orderedTaskIds,
    thread.values.run_id ?? null,
  );
  const workflowProgress = getWorkflowProgressSummary({
    isLoading: thread.isLoading,
    threadValues: thread.values,
    tasks: workflowTasks,
    t,
  });
  const isWorkflowMode =
    thread.values.resolved_orchestration_mode === "workflow";
  const visibleMessages =
    isWorkflowMode
      ? filterWorkflowMessages(messages, workflowTasks)
      : messages;
  const groupedMessages = useMemo(
    () => groupMessages(visibleMessages, (group) => group),
    [visibleMessages],
  );
  const lastAnimatedGroupIndex = useMemo(() => {
    if (!thread.isLoading) {
      return -1;
    }
    for (let index = groupedMessages.length - 1; index >= 0; index--) {
      const group = groupedMessages[index];
      if (!group) {
        continue;
      }
      if (ANIMATABLE_ASSISTANT_GROUP_TYPES.has(group.type)) {
        return index;
      }
    }
    return -1;
  }, [groupedMessages, thread.isLoading]);
  const hasVisibleAssistantContent = groupedMessages.some(
    (group) =>
      group.type === "assistant" ||
      group.type === "assistant:processing" ||
      group.type === "assistant:clarification" ||
      group.type === "assistant:present-files",
  );
  const shouldShowWorkflowInlineProgress =
    isWorkflowMode &&
    thread.isLoading &&
    !hasVisibleAssistantContent;
  const shouldShowStoppedNotice =
    stoppedByUser && !thread.isLoading && !hasVisibleAssistantContent;
  const inlineInterventionTask = workflowTasks.find(
    (task) =>
      task.status === "waiting_intervention" &&
      task.interventionRequest != null &&
      Boolean(task.threadId),
  );
  const inlineClarificationTask = workflowTasks.find(
    (task) =>
      task.status === "waiting_clarification" &&
      ((task.clarificationRequest?.questions?.length ?? 0) > 0 ||
        splitClarificationQuestions(task.clarificationPrompt?.trim() ?? "")
          .length > 0),
  );
  const legacySubagentGroups = useMemo(
    () =>
      groupedMessages.map((group) => {
        if (group.type !== "assistant:subagent") {
          return null;
        }

        const taskIds = new Set<string>();
        const taskUpdates: TaskUpsert[] = [];
        for (const message of group.messages) {
          if (message.type === "ai") {
            for (const toolCall of message.tool_calls ?? []) {
              const task = fromLegacyTaskToolCall(toolCall, threadId);
              if (task) {
                taskUpdates.push(task);
                taskIds.add(task.id);
              }
            }
          } else {
            const taskUpdate = fromLegacyToolMessage(message);
            if (taskUpdate) {
              taskUpdates.push(taskUpdate);
              taskIds.add(taskUpdate.id);
            }
          }
        }

        return {
          taskIds: Array.from(taskIds),
          taskUpdates,
        };
      }),
    [groupedMessages, threadId],
  );

  useEffect(() => {
    for (const group of legacySubagentGroups) {
      if (!group) {
        continue;
      }
      for (const taskUpdate of group.taskUpdates) {
        updateSubtask(taskUpdate);
      }
    }
  }, [legacySubagentGroups, updateSubtask]);

  if (thread.isThreadLoading && messages.length === 0) {
    return <MessageListSkeleton />;
  }
  const renderItems = useMemo(() => {
    const items: VirtualizedRenderItem[] = [];

    groupedMessages.forEach((group, groupIndex) => {
      const shouldAnimateGroup =
        thread.isLoading && groupIndex === lastAnimatedGroupIndex;
      const groupRehypePlugins = shouldAnimateGroup
        ? animatedRehypePlugins
        : staticRehypePlugins;

      if (group.type === "human" || group.type === "assistant") {
        items.push({
          key: group.id ?? `message-${groupIndex}`,
          estimateHeight: group.type === "human" ? 120 : 180,
          content: (
            <MessageListItem
              key={group.id}
              message={group.messages[0]!}
              isLoading={group.type === "assistant" && shouldAnimateGroup}
            />
          ),
        });
        return;
      }

      if (group.type === "assistant:clarification") {
        const message = group.messages[0];
        if (message && hasContent(message)) {
          items.push({
            key: group.id ?? `clarification-${groupIndex}`,
            estimateHeight: 160,
            content: (
              <MarkdownContent
                key={group.id}
                content={extractContentFromMessage(message)}
                isLoading={shouldAnimateGroup}
                rehypePlugins={groupRehypePlugins}
              />
            ),
          });
        }
        return;
      }

      if (group.type === "assistant:present-files") {
        const files: string[] = [];
        for (const message of group.messages) {
          if (hasPresentFiles(message)) {
            const presentFiles = extractPresentFilesFromMessage(message);
            files.push(...presentFiles);
          }
        }
        items.push({
          key: group.id ?? `present-files-${groupIndex}`,
          estimateHeight: files.length > 0 ? 260 : 180,
          content: (
            <div className="w-full" key={group.id}>
              {group.messages[0] && hasContent(group.messages[0]) && (
                <MarkdownContent
                  content={extractContentFromMessage(group.messages[0])}
                  isLoading={shouldAnimateGroup}
                  rehypePlugins={groupRehypePlugins}
                  className="mb-4"
                />
              )}
              <ArtifactFileList files={files} threadId={threadId} />
            </div>
          ),
        });
        return;
      }

      if (group.type === "assistant:subagent") {
        if (isWorkflowMode) {
          return;
        }
        const taskIds = legacySubagentGroups[groupIndex]?.taskIds ?? [];
        const results: ReactNode[] = [];
        for (const message of group.messages.filter(
          (message) => message.type === "ai",
        )) {
          if (hasReasoning(message)) {
            results.push(
              <MessageGroup
                key={"thinking-group-" + message.id}
                messages={[message]}
                isLoading={shouldAnimateGroup}
              />,
            );
          }
          results.push(
            <div
              key={"subtask-count-" + message.id}
              className="text-muted-foreground font-norma pt-2 text-sm"
            >
              {t.subtasks.executing(taskIds.length)}
            </div>,
          );
          const messageTaskIds = message.tool_calls
            ?.map((toolCall) => toolCall.id)
            .filter((taskId): taskId is string => Boolean(taskId));
          for (const taskId of messageTaskIds ?? []) {
            results.push(
              <SubtaskCard
                key={"task-group-" + taskId}
                taskId={taskId}
                isLoading={false}
              />,
            );
          }
        }
        items.push({
          key: group.id ?? `subagent-${groupIndex}`,
          estimateHeight: Math.max(220, 120 + taskIds.length * 120),
          content: (
            <div
              key={"subtask-group-" + group.id}
              className="relative z-1 flex flex-col gap-2"
            >
              {results}
            </div>
          ),
        });
        return;
      }

      items.push({
        key: group.id ?? `processing-${groupIndex}`,
        estimateHeight: 240,
        content: (
          <MessageGroup
            key={"group-" + group.id}
            messages={group.messages}
            isLoading={shouldAnimateGroup}
          />
        ),
      });
    });

    if (thread.isLoading) {
      if (isWorkflowMode) {
        if (shouldShowWorkflowInlineProgress) {
          items.push({
            key: "workflow-inline-progress",
            estimateHeight: workflowProgress ? 112 : 56,
            content: workflowProgress ? (
              <div className="bg-background/85 border-border/60 my-4 flex max-w-xl flex-col gap-2 rounded-2xl border px-4 py-3 shadow-sm backdrop-blur-sm">
                <div className="min-w-0">
                  <div className="text-sm font-medium">
                    {workflowProgress.title}
                  </div>
                  {workflowProgress.detail && (
                    <div className="text-muted-foreground mt-1 truncate text-sm">
                      {workflowProgress.detail}
                    </div>
                  )}
                </div>
                <StreamingIndicator className="shrink-0" size="sm" />
              </div>
            ) : (
              <StreamingIndicator className="my-4" />
            ),
          });
        }
      } else {
        items.push({
          key: "streaming-progress",
          estimateHeight: workflowProgress ? 112 : 56,
          content: workflowProgress ? (
            <div className="bg-background/85 border-border/60 my-4 flex max-w-xl flex-col gap-2 rounded-2xl border px-4 py-3 shadow-sm backdrop-blur-sm">
              <div className="min-w-0">
                <div className="text-sm font-medium">
                  {workflowProgress.title}
                </div>
                {workflowProgress.detail && (
                  <div className="text-muted-foreground mt-1 truncate text-sm">
                    {workflowProgress.detail}
                  </div>
                )}
              </div>
              <StreamingIndicator className="shrink-0" size="sm" />
            </div>
          ) : (
            <StreamingIndicator className="my-4" />
          ),
        });
      }
    }

    if (isWorkflowMode && !stoppedByUser && inlineInterventionTask) {
      items.push({
        key: "inline-intervention",
        estimateHeight: 240,
        content: (
          <div className="my-4 w-full">
            <InterventionCard task={inlineInterventionTask} />
          </div>
        ),
      });
    }

    if (
      isWorkflowMode &&
      !stoppedByUser &&
      inlineClarificationTask &&
      onSubmitClarification
    ) {
      items.push({
        key: "inline-clarification",
        estimateHeight: 240,
        content: (
          <div className="my-4 w-full">
            <ClarificationCard
              task={inlineClarificationTask}
              disabled={thread.isLoading}
              onSubmit={onSubmitClarification}
            />
          </div>
        ),
      });
    }

    if (shouldShowStoppedNotice) {
      items.push({
        key: "stopped-notice",
        estimateHeight: 112,
        content: (
          <div className="bg-background/85 border-border/60 my-4 flex max-w-xl flex-col gap-2 rounded-2xl border px-4 py-3 shadow-sm backdrop-blur-sm">
            <div className="min-w-0">
              <div className="text-sm font-medium">
                {t.workflowStatus.stopped}
              </div>
              <div className="text-muted-foreground mt-1 text-sm">
                {t.workflowStatus.stoppedDescription}
              </div>
            </div>
          </div>
        ),
      });
    }

    items.push({
      key: "bottom-padding",
      estimateHeight: paddingBottom,
      content: <div style={{ height: `${paddingBottom}px` }} />,
    });

    return items;
  }, [
    animatedRehypePlugins,
    groupedMessages,
    inlineClarificationTask,
    inlineInterventionTask,
    isWorkflowMode,
    lastAnimatedGroupIndex,
    legacySubagentGroups,
    onSubmitClarification,
    paddingBottom,
    shouldShowStoppedNotice,
    shouldShowWorkflowInlineProgress,
    staticRehypePlugins,
    stoppedByUser,
    t,
    thread.isLoading,
    threadId,
    workflowProgress,
  ]);

  return (
    <Conversation
      className={cn("flex size-full flex-col justify-center", className)}
      data-message-count={renderItems.length}
    >
      {(stickToBottom) => (
        <VirtualizedMessageListContent
          items={renderItems}
          stickToBottom={stickToBottom}
        />
      )}
    </Conversation>
  );
}

function VirtualizedMessageListContent({
  items,
  stickToBottom,
}: {
  items: VirtualizedRenderItem[];
  stickToBottom: StickToBottomContext;
}) {
  const [viewportHeight, setViewportHeight] = useState(0);
  const [measuredHeights, setMeasuredHeights] = useState<Record<string, number>>(
    {},
  );

  useEffect(() => {
    const scrollElement = stickToBottom.scrollRef.current;
    if (!scrollElement) {
      return;
    }

    const updateViewportHeight = () => {
      setViewportHeight(scrollElement.clientHeight);
    };

    updateViewportHeight();

    if (typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver(() => {
      updateViewportHeight();
    });
    observer.observe(scrollElement);

    return () => {
      observer.disconnect();
    };
  }, [stickToBottom.scrollRef]);

  const handleItemHeightChange = useCallback((key: string, height: number) => {
    setMeasuredHeights((current) => {
      if (current[key] === height) {
        return current;
      }
      return {
        ...current,
        [key]: height,
      };
    });
  }, []);

  const virtualizationEnabled =
    viewportHeight > 0 && items.length > VIRTUALIZATION_THRESHOLD;
  const itemHeights = useMemo(
    () => items.map((item) => measuredHeights[item.key] ?? item.estimateHeight),
    [items, measuredHeights],
  );
  const layouts = useMemo(() => {
    const nextLayouts: Array<{ start: number; end: number }> = [];
    let offset = 0;

    itemHeights.forEach((height, index) => {
      const start = offset;
      const end = start + height;
      nextLayouts.push({ start, end });
      offset = end + (index < itemHeights.length - 1 ? MESSAGE_LIST_GAP : 0);
    });

    return {
      items: nextLayouts,
      totalHeight: offset,
    };
  }, [itemHeights]);
  const visibleRange = useMemo(() => {
    if (!virtualizationEnabled) {
      return {
        startIndex: 0,
        endIndex: items.length - 1,
      };
    }

    const startOffset = Math.max(
      stickToBottom.state.scrollTop - VIRTUALIZATION_OVERSCAN,
      0,
    );
    const endOffset =
      stickToBottom.state.scrollTop +
      viewportHeight +
      VIRTUALIZATION_OVERSCAN;

    let startIndex = 0;
    while (
      startIndex < layouts.items.length - 1 &&
      layouts.items[startIndex]!.end < startOffset
    ) {
      startIndex += 1;
    }

    let endIndex = startIndex;
    while (
      endIndex < layouts.items.length - 1 &&
      layouts.items[endIndex]!.start <= endOffset
    ) {
      endIndex += 1;
    }

    return {
      startIndex,
      endIndex,
    };
  }, [
    items.length,
    layouts.items,
    stickToBottom.state.scrollTop,
    viewportHeight,
    virtualizationEnabled,
  ]);

  const topSpacerHeight = virtualizationEnabled
    ? layouts.items[visibleRange.startIndex]?.start ?? 0
    : 0;
  const bottomSpacerHeight = virtualizationEnabled
    ? Math.max(
        layouts.totalHeight -
          (layouts.items[visibleRange.endIndex]?.end ?? 0),
        0,
      )
    : 0;
  const visibleItems = virtualizationEnabled
    ? items.slice(visibleRange.startIndex, visibleRange.endIndex + 1)
    : items;

  return (
    <ConversationContent className="mx-auto w-full max-w-(--container-width-md) pt-12">
      <div className="w-full">
        {topSpacerHeight > 0 && (
          <div aria-hidden style={{ height: `${topSpacerHeight}px` }} />
        )}
        <div className="flex w-full flex-col gap-8">
          {visibleItems.map((item) => (
            <MeasuredMessageListItem
              key={item.key}
              itemKey={item.key}
              onHeightChange={handleItemHeightChange}
            >
              {item.content}
            </MeasuredMessageListItem>
          ))}
        </div>
        {bottomSpacerHeight > 0 && (
          <div aria-hidden style={{ height: `${bottomSpacerHeight}px` }} />
        )}
      </div>
    </ConversationContent>
  );
}

function MeasuredMessageListItem({
  itemKey,
  onHeightChange,
  children,
}: {
  itemKey: string;
  onHeightChange: (key: string, height: number) => void;
  children: ReactNode;
}) {
  const [element, setElement] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!element) {
      return;
    }

    const updateHeight = () => {
      onHeightChange(itemKey, element.offsetHeight);
    };

    updateHeight();

    if (typeof ResizeObserver === "undefined") {
      return;
    }

    const observer = new ResizeObserver(() => {
      updateHeight();
    });
    observer.observe(element);

    return () => {
      observer.disconnect();
    };
  }, [element, itemKey, onHeightChange]);

  return (
    <div ref={setElement} className="flow-root w-full">
      {children}
    </div>
  );
}

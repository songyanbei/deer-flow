import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { useEffect, useMemo } from "react";

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
import type { TaskUpsert } from "@/core/tasks/types";
import { useSubtaskContext, useUpdateSubtask } from "@/core/tasks/context";
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

export function MessageList({
  className,
  threadId,
  thread,
  paddingBottom = 160,
}: {
  className?: string;
  threadId: string;
  thread: BaseStream<AgentThreadState>;
  paddingBottom?: number;
}) {
  const { t } = useI18n();
  const rehypePlugins = useRehypeSplitWordsIntoSpans(thread.isLoading);
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
  return (
    <Conversation
      className={cn("flex size-full flex-col justify-center", className)}
    >
      <ConversationContent className="mx-auto w-full max-w-(--container-width-md) gap-8 pt-12">
        {groupedMessages.map((group, groupIndex) => {
          if (group.type === "human" || group.type === "assistant") {
            return (
              <MessageListItem
                key={group.id}
                message={group.messages[0]!}
                isLoading={thread.isLoading}
              />
            );
          } else if (group.type === "assistant:clarification") {
            const message = group.messages[0];
            if (message && hasContent(message)) {
              return (
                <MarkdownContent
                  key={group.id}
                  content={extractContentFromMessage(message)}
                  isLoading={thread.isLoading}
                  rehypePlugins={rehypePlugins}
                />
              );
            }
            return null;
          } else if (group.type === "assistant:present-files") {
            const files: string[] = [];
            for (const message of group.messages) {
              if (hasPresentFiles(message)) {
                const presentFiles = extractPresentFilesFromMessage(message);
                files.push(...presentFiles);
              }
            }
            return (
              <div className="w-full" key={group.id}>
                {group.messages[0] && hasContent(group.messages[0]) && (
                  <MarkdownContent
                    content={extractContentFromMessage(group.messages[0])}
                    isLoading={thread.isLoading}
                    rehypePlugins={rehypePlugins}
                    className="mb-4"
                  />
                )}
                <ArtifactFileList files={files} threadId={threadId} />
              </div>
            );
          } else if (group.type === "assistant:subagent") {
            if (isWorkflowMode) {
              return null;
            }
            const taskIds = legacySubagentGroups[groupIndex]?.taskIds ?? [];
            const results: React.ReactNode[] = [];
            for (const message of group.messages.filter(
              (message) => message.type === "ai",
            )) {
              if (hasReasoning(message)) {
                results.push(
                  <MessageGroup
                    key={"thinking-group-" + message.id}
                    messages={[message]}
                    isLoading={thread.isLoading}
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
                    taskId={taskId!}
                    isLoading={thread.isLoading}
                  />,
                );
              }
            }
            return (
              <div
                key={"subtask-group-" + group.id}
                className="relative z-1 flex flex-col gap-2"
              >
                {results}
              </div>
            );
          }
          return (
            <MessageGroup
              key={"group-" + group.id}
              messages={group.messages}
              isLoading={thread.isLoading}
            />
          );
        })}
        {thread.isLoading &&
          (isWorkflowMode ? null : workflowProgress ? (
            <div className="bg-background/85 border-border/60 my-4 flex max-w-xl items-start gap-3 rounded-2xl border px-4 py-3 shadow-sm backdrop-blur-sm">
              <StreamingIndicator className="mt-1 shrink-0" size="sm" />
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
            </div>
          ) : (
            <StreamingIndicator className="my-4" />
          ))}
        <div style={{ height: `${paddingBottom}px` }} />
      </ConversationContent>
    </Conversation>
  );
}

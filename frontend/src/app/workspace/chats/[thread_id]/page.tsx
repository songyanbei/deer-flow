"use client";

import { useCallback, useEffect, useState } from "react";

import { type PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import {
  ChatBox,
  useSpecificChatMode,
  useThreadChat,
} from "@/components/workspace/chats";
import { InputBox } from "@/components/workspace/input-box";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { OrchestrationSummary } from "@/components/workspace/orchestration-summary";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { useFooterPadding } from "@/components/workspace/use-footer-padding";
import { Welcome } from "@/components/workspace/welcome";
import { WorkflowFooterBar } from "@/components/workspace/workflow-footer-bar";
import { useI18n } from "@/core/i18n/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings } from "@/core/settings";
import { useThreadStream } from "@/core/threads/hooks";
import { textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export default function ChatPage() {
  const { t } = useI18n();
  const [settings, setSettings] = useLocalSettings();
  const [hydrated, setHydrated] = useState(false);

  const { threadId, isNewThread, setIsNewThread, isMock } = useThreadChat();
  useSpecificChatMode();

  useEffect(() => {
    setHydrated(true);
  }, []);

  const { showNotification } = useNotification();
  const {
    footerContainerRef,
    footerOverlayRef,
    inputShellRef,
    paddingBottom,
  } = useFooterPadding();

  const [thread, sendMessage] = useThreadStream({
    assistantId: "entry_graph",
    threadId: isNewThread ? undefined : threadId,
    context: settings.context,
    isMock,
    onStart: () => {
      setIsNewThread(false);
      history.replaceState(null, "", `/workspace/chats/${threadId}`);
    },
    onFinish: (state) => {
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages.at(-1);
        if (lastMessage) {
          const textContent = textOfMessage(lastMessage);
          if (textContent) {
            body =
              textContent.length > 200
                ? textContent.substring(0, 200) + "..."
                : textContent;
          }
        }
        showNotification(state.title, { body });
      }
    },
  });
  const [stoppedByUser, setStoppedByUser] = useState(false);

  useEffect(() => {
    setStoppedByUser(false);
  }, [threadId]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      setStoppedByUser(false);
      void sendMessage(threadId, message);
    },
    [sendMessage, threadId],
  );
  const handleSubmitClarification = useCallback(
    ({
      text,
      answers,
    }: {
      text: string;
      answers: Record<string, { text: string }>;
    }) => {
      setStoppedByUser(false);
      void sendMessage(
        threadId,
        { text, files: [] },
        {
          workflow_clarification_response: {
            answers,
          },
        },
      );
    },
    [sendMessage, threadId],
  );
  const handleStop = useCallback(async () => {
    setStoppedByUser(true);
    await thread.stop();
  }, [thread]);
  const shouldDockWorkflowFooter =
    !stoppedByUser &&
    (thread.values.resolved_orchestration_mode === "workflow" ||
      thread.values.workflow_stage != null) &&
    (thread.isLoading ||
      thread.values.workflow_stage != null ||
      (thread.values.task_pool?.length ?? 0) > 0 ||
      (thread.values.todos?.length ?? 0) > 0);

  if (!hydrated) {
    return (
      <div className="bg-background flex size-full min-h-0 flex-col">
        <div className="border-border/60 bg-background/80 h-12 shrink-0 border-b" />
        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex-1" />
          <div className="p-4">
            <div className="border-border/60 bg-background/70 h-32 w-full rounded-2xl border" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <ChatBox threadId={threadId}>
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center px-4",
              isNewThread
                ? "bg-background/0 backdrop-blur-none"
                : "bg-background/80 shadow-xs backdrop-blur",
            )}
          >
            <div className="flex min-w-0 w-full items-center gap-2 text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
              <OrchestrationSummary thread={thread} />
            </div>
            <div>
              <ArtifactTrigger />
            </div>
          </header>
          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex size-full justify-center">
              <MessageList
                className={cn("size-full", !isNewThread && "pt-10")}
                threadId={threadId}
                thread={thread}
                stoppedByUser={stoppedByUser}
                paddingBottom={paddingBottom}
                onSubmitClarification={handleSubmitClarification}
              />
            </div>
            <div
              ref={footerContainerRef}
              className="absolute right-0 bottom-0 left-0 z-30 flex justify-center px-4"
            >
              <div
                className={cn(
                  "relative w-full",
                  isNewThread && "-translate-y-[calc(50vh-96px)]",
                  isNewThread
                    ? "max-w-(--container-width-sm)"
                    : "max-w-(--container-width-md)",
                )}
              >
                <div
                  ref={footerOverlayRef}
                  className="absolute right-0 bottom-full left-0 z-0 pb-0.5"
                >
                  <div className="flex flex-col gap-0.5">
                    <WorkflowFooterBar
                      thread={thread}
                      hidden={false}
                      stopped={stoppedByUser}
                    />
                    <TodoList
                      className="bg-background/5"
                      todos={thread.values.todos ?? []}
                      hidden={
                        !thread.values.todos || thread.values.todos.length === 0
                      }
                    />
                  </div>
                </div>
                <div ref={inputShellRef}>
                  <InputBox
                    className={cn(
                      "bg-background/5 w-full",
                      shouldDockWorkflowFooter &&
                        "rounded-t-none border-t-0 *:data-[slot='input-group']:rounded-t-none",
                    )}
                    isNewThread={isNewThread}
                    autoFocus={isNewThread}
                    status={thread.isLoading ? "streaming" : "ready"}
                    context={settings.context}
                    extraHeader={
                      isNewThread && <Welcome mode={settings.context.mode} />
                    }
                    disabled={env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true"}
                    onContextChange={(context) => setSettings("context", context)}
                    onSubmit={handleSubmit}
                    onStop={handleStop}
                  />
                </div>
                {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                  <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                    {t.common.notAvailableInDemoMode}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      </ChatBox>
    </ThreadContext.Provider>
  );
}

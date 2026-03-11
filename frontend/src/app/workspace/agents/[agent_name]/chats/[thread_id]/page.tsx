"use client";

import { BotIcon, PlusSquare } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback } from "react";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { AgentWelcome } from "@/components/workspace/agent-welcome";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import { ChatBox, useThreadChat } from "@/components/workspace/chats";
import { InputBox } from "@/components/workspace/input-box";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { OrchestrationSummary } from "@/components/workspace/orchestration-summary";
import { TaskPanel } from "@/components/workspace/task-panel";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { Tooltip } from "@/components/workspace/tooltip";
import { useFooterPadding } from "@/components/workspace/use-footer-padding";
import { useAgent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { useNotification } from "@/core/notification/hooks";
import { useLocalSettings } from "@/core/settings";
import { useThreadStream } from "@/core/threads/hooks";
import { textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export default function AgentChatPage() {
  const { t } = useI18n();
  const [settings, setSettings] = useLocalSettings();
  const router = useRouter();

  const { agent_name } = useParams<{
    agent_name: string;
  }>();

  const { agent } = useAgent(agent_name);

  const { threadId, isNewThread, setIsNewThread } = useThreadChat();

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
    context: { ...settings.context, agent_name: agent_name },
    onStart: () => {
      setIsNewThread(false);
      history.replaceState(
        null,
        "",
        `/workspace/agents/${agent_name}/chats/${threadId}`,
      );
    },
    onFinish: (state) => {
      if (document.hidden || !document.hasFocus()) {
        let body = "Conversation finished";
        const lastMessage = state.messages[state.messages.length - 1];
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

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      void sendMessage(threadId, message, { agent_name });
    },
    [sendMessage, threadId, agent_name],
  );

  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);
  const shouldDockWorkflowFooter =
    thread.values.resolved_orchestration_mode === "workflow" &&
    (thread.isLoading ||
      (thread.values.task_pool?.length ?? 0) > 0 ||
      (thread.values.todos?.length ?? 0) > 0);

  return (
    <ThreadContext.Provider value={{ thread }}>
      <ChatBox threadId={threadId}>
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-4",
              isNewThread
                ? "bg-background/0 backdrop-blur-none"
                : "bg-background/80 shadow-xs backdrop-blur",
            )}
          >
            {/* Agent badge */}
            <div className="flex shrink-0 items-center gap-1.5 rounded-md border px-2 py-1">
              <BotIcon className="text-primary h-3.5 w-3.5" />
              <span className="text-xs font-medium">
                {agent?.name ?? agent_name}
              </span>
            </div>

            <div className="flex min-w-0 w-full items-center gap-2 text-sm font-medium">
              <ThreadTitle threadId={threadId} thread={thread} />
              <OrchestrationSummary thread={thread} />
            </div>
            <div className="mr-4 flex items-center">
              <Tooltip content={t.agents.newChat}>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    router.push(`/workspace/agents/${agent_name}/chats/new`);
                  }}
                >
                  <PlusSquare /> {t.agents.newChat}
                </Button>
              </Tooltip>
              <ArtifactTrigger />
            </div>
          </header>

          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex size-full justify-center">
              <MessageList
                className={cn("size-full", !isNewThread && "pt-10")}
                threadId={threadId}
                thread={thread}
                paddingBottom={paddingBottom}
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
                    <TaskPanel thread={thread} />
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
                      isNewThread && (
                        <AgentWelcome agent={agent} agentName={agent_name} />
                      )
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

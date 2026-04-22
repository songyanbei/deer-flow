"use client";

import { ArrowLeftIcon, BotIcon, CheckCircleIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { useUpdateAgent, type Agent } from "@/core/agents";
import { checkAgentName, getAgent } from "@/core/agents/api";
import { useI18n } from "@/core/i18n/hooks";
import { useThreadStream } from "@/core/threads/hooks";
import { createRuntimeThread } from "@/core/threads/runtime-api";
import { cn } from "@/lib/utils";

type Step = "name" | "chat";
type RequestedOrchestrationMode = "auto" | "leader" | "workflow";

const NAME_RE = /^[A-Za-z0-9-]+$/;

export default function NewAgentPage() {
  const { t } = useI18n();
  const router = useRouter();
  const updateAgent = useUpdateAgent();

  const [step, setStep] = useState<Step>("name");
  const [nameInput, setNameInput] = useState("");
  const [nameError, setNameError] = useState("");
  const [isCheckingName, setIsCheckingName] = useState(false);
  const [agentName, setAgentName] = useState("");
  const [agent, setAgent] = useState<Agent | null>(null);
  const [requestedOrchestrationMode, setRequestedOrchestrationMode] =
    useState<RequestedOrchestrationMode>("auto");

  // Thread id is issued by the Gateway (`POST /api/runtime/threads`) so
  // `ThreadRegistry` has a binding before the first `messages:stream` submit —
  // a locally-generated uuid would be unknown to `resolve_thread_context`
  // and get a 403. Stays null until the user confirms the agent name.
  const [threadId, setThreadId] = useState<string | null>(null);

  const syncAgentDefaultMode = useCallback(
    async (currentAgent: Agent) => {
      if (
        requestedOrchestrationMode === "auto" ||
        currentAgent.requested_orchestration_mode === requestedOrchestrationMode
      ) {
        setAgent(currentAgent);
        return;
      }

      const updated = await updateAgent.mutateAsync({
        name: currentAgent.name,
        request: {
          requested_orchestration_mode: requestedOrchestrationMode,
        },
      });
      setAgent(updated);
    },
    [requestedOrchestrationMode, updateAgent],
  );

  const [thread, sendMessage] = useThreadStream({
    assistantId: "lead_agent",
    threadId: step === "chat" && threadId ? threadId : undefined,
    context: {
      mode: "flash",
      is_bootstrap: true,
    },
    onToolEnd({ name }) {
      if (name !== "setup_agent" || !agentName) {
        return;
      }

      getAgent(agentName)
        .then((fetched) => void syncAgentDefaultMode(fetched))
        .catch((error) => {
          toast.error(error instanceof Error ? error.message : String(error));
        });
    },
  });

  const handleConfirmName = useCallback(async () => {
    const trimmed = nameInput.trim();
    if (!trimmed) {
      return;
    }
    if (!NAME_RE.test(trimmed)) {
      setNameError(t.agents.nameStepInvalidError);
      return;
    }

    setNameError("");
    setIsCheckingName(true);
    try {
      const result = await checkAgentName(trimmed);
      if (!result.available) {
        setNameError(t.agents.nameStepAlreadyExistsError);
        return;
      }
    } catch {
      setNameError(t.agents.nameStepCheckError);
      return;
    } finally {
      setIsCheckingName(false);
    }

    // Create the Gateway-registered thread BEFORE the first bootstrap submit.
    // `messages:stream` rejects unknown thread ids with 403, so a
    // locally-generated uuid would fail the ownership check.
    let registeredThreadId = threadId;
    if (!registeredThreadId) {
      try {
        const created = await createRuntimeThread();
        registeredThreadId = created.thread_id;
        setThreadId(registeredThreadId);
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : String(error),
        );
        return;
      }
    }

    setAgentName(trimmed);
    setStep("chat");
    await sendMessage(registeredThreadId, {
      text: t.agents.nameStepBootstrapMessage.replace("{name}", trimmed),
      files: [],
    });
  }, [
    nameInput,
    sendMessage,
    t.agents.nameStepAlreadyExistsError,
    t.agents.nameStepBootstrapMessage,
    t.agents.nameStepCheckError,
    t.agents.nameStepInvalidError,
    threadId,
  ]);

  const handleNameKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      void handleConfirmName();
    }
  };

  const handleChatSubmit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || thread.isLoading || !threadId) {
        return;
      }
      await sendMessage(
        threadId,
        { text: trimmed, files: [] },
        { agent_name: agentName },
      );
    },
    [agentName, sendMessage, thread.isLoading, threadId],
  );

  const header = (
    <header className="flex shrink-0 items-center gap-3 border-b px-4 py-3">
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={() => router.push("/workspace/agents")}
      >
        <ArrowLeftIcon className="h-4 w-4" />
      </Button>
      <h1 className="text-sm font-semibold">{t.agents.createPageTitle}</h1>
    </header>
  );

  if (step === "name") {
    return (
      <div className="flex size-full flex-col">
        {header}
        <main className="flex flex-1 flex-col items-center justify-center px-4">
          <div className="w-full max-w-sm space-y-8">
            <div className="space-y-3 text-center">
              <div className="bg-primary/10 mx-auto flex h-14 w-14 items-center justify-center rounded-full">
                <BotIcon className="text-primary h-7 w-7" />
              </div>
              <div className="space-y-1">
                <h2 className="text-xl font-semibold">
                  {t.agents.nameStepTitle}
                </h2>
                <p className="text-muted-foreground text-sm">
                  {t.agents.nameStepHint}
                </p>
              </div>
            </div>

            <div className="space-y-3">
              <Input
                autoFocus
                placeholder={t.agents.nameStepPlaceholder}
                value={nameInput}
                onChange={(event) => {
                  setNameInput(event.target.value);
                  setNameError("");
                }}
                onKeyDown={handleNameKeyDown}
                className={cn(nameError && "border-destructive")}
              />
              {nameError && (
                <p className="text-destructive text-sm">{nameError}</p>
              )}
              <div className="space-y-2 text-left">
                <p className="text-muted-foreground text-sm">
                  {t.inputBox.orchestrationMode}
                </p>
                <Select
                  value={requestedOrchestrationMode}
                  onValueChange={(value) =>
                    setRequestedOrchestrationMode(
                      value as RequestedOrchestrationMode,
                    )
                  }
                >
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="auto">
                      {t.inputBox.autoOrchestrationMode}
                    </SelectItem>
                    <SelectItem value="leader">
                      {t.inputBox.leaderOrchestrationMode}
                    </SelectItem>
                    <SelectItem value="workflow">
                      {t.inputBox.workflowOrchestrationMode}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button
                className="w-full"
                onClick={() => void handleConfirmName()}
                disabled={!nameInput.trim() || isCheckingName}
              >
                {t.agents.nameStepContinue}
              </Button>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <ThreadContext.Provider value={{ thread }}>
      <ArtifactsProvider>
        <div className="flex size-full flex-col">
          {header}

          <main className="flex min-h-0 flex-1 flex-col">
            <div className="flex min-h-0 flex-1 justify-center">
              <MessageList
                className="size-full pt-10"
                threadId={threadId ?? ""}
                thread={thread}
              />
            </div>

            <div className="bg-background flex shrink-0 justify-center border-t px-4 py-4">
              <div className="w-full max-w-(--container-width-md)">
                {agent ? (
                  <div className="flex flex-col items-center gap-4 rounded-2xl border py-8 text-center">
                    <CheckCircleIcon className="text-primary h-10 w-10" />
                    <p className="font-semibold">{t.agents.agentCreated}</p>
                    <div className="flex gap-2">
                      <Button
                        onClick={() =>
                          router.push(`/workspace/agents/${agentName}/chats/new`)
                        }
                      >
                        {t.agents.startChatting}
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => router.push("/workspace/agents")}
                      >
                        {t.agents.backToGallery}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <PromptInput
                    onSubmit={({ text }) => void handleChatSubmit(text)}
                  >
                    <PromptInputTextarea
                      autoFocus
                      placeholder={t.agents.createPageSubtitle}
                      disabled={thread.isLoading}
                    />
                    <PromptInputFooter className="justify-end">
                      <PromptInputSubmit disabled={thread.isLoading} />
                    </PromptInputFooter>
                  </PromptInput>
                )}
              </div>
            </div>
          </main>
        </div>
      </ArtifactsProvider>
    </ThreadContext.Provider>
  );
}

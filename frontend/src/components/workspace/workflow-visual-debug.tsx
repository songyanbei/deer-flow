"use client";

import type { BaseStream } from "@langchain/langgraph-sdk/react";
import { PauseIcon, PlayIcon, SkipBackIcon, SkipForwardIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { InputBox } from "@/components/workspace/input-box";
import { MessageList } from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { TodoList } from "@/components/workspace/todo-list";
import { useFooterPadding } from "@/components/workspace/use-footer-padding";
import { WorkflowFooterBar } from "@/components/workspace/workflow-footer-bar";
import { fromMultiAgentTaskState } from "@/core/tasks/adapters";
import { useTaskActions } from "@/core/tasks/context";
import type { AgentThreadContext, AgentThreadState } from "@/core/threads";
import { cn } from "@/lib/utils";

type DebugStage = Pick<
  AgentThreadState,
  | "execution_state"
  | "orchestration_reason"
  | "planner_goal"
  | "run_id"
  | "task_pool"
  | "todos"
> & {
  label: string;
};

const BASE_MESSAGES = [
  {
    type: "human" as const,
    id: "debug-user-1",
    content: [
      {
        type: "text" as const,
        text: "Please create a product launch workflow and break it into executable subtasks.",
      },
    ],
  },
  {
    type: "ai" as const,
    id: "debug-ai-1",
    content:
      "I will confirm the launch scope, parallelize the preparation tasks, and summarize the rollout plan at the end.",
    tool_calls: [],
    invalid_tool_calls: [],
  },
  {
    type: "human" as const,
    id: "debug-internal-1",
    content: "Confirm the launch window and target audience",
  },
  {
    type: "human" as const,
    id: "debug-internal-2",
    content:
      "Draft the homepage announcement and launch email\n\nKnown facts (do not re-check):\n1. The release is tentatively scheduled for April 8.\n2. The primary audience is current paid team admins.",
  },
  {
    type: "ai" as const,
    id: "debug-ai-2",
    content:
      "This debug page intentionally simulates workflow progress updates so we can inspect footer overlap, task glow visibility, and scroll stability while the panel changes height.\n\nTo make footer clearance easier to validate, this answer is deliberately longer than a normal assistant message. It repeats the same rollout summary with slightly different phrasing so the final block of content sits much closer to the footer. The launch work starts with audience confirmation, then moves into announcement copy, onboarding notes, support FAQ updates, and social teaser preparation.\n\nThe important behavior here is that the dedicated workflow panel should own the live subtask rendering while the main transcript stays clean. When the task panel expands, collapses, or changes status text, the bottom clearance for the conversation should grow with it so the final assistant message never gets clipped behind the footer controls.\n\nIf the footer padding is too small, the panel will visually cover the end of this paragraph. If the panel glow is clipped, the active task cards will look like they only have a thin border instead of the wider ambient sweep from the original DeerFlow UI.",
    tool_calls: [],
    invalid_tool_calls: [],
  },
  {
    type: "ai" as const,
    id: "debug-ai-3",
    content:
      "One more long block keeps the conversation anchored near the bottom of the viewport during the local browser run. That makes it much easier to spot layout jumps when the workflow stage advances from planning to running, then to clarification, and finally to summarizing. The intended result is stable scrolling, no user-bubble replacement, and a restored ambient glow around active subtask cards.",
    tool_calls: [],
    invalid_tool_calls: [],
  },
];

const DEBUG_STAGES: DebugStage[] = [
  {
    label: "Planning",
    execution_state: "PLANNING_DONE",
    orchestration_reason:
      "This task breaks into parallel workstreams, so workflow orchestration is appropriate.",
    planner_goal:
      "Prepare a coordinated product launch plan with parallel subtasks and a final summary.",
    run_id: "debug-workflow-run",
    task_pool: [
      {
        task_id: "debug-task-1",
        description: "Confirm the launch window and target audience",
        run_id: "debug-workflow-run",
        assigned_agent: "strategy-agent",
        status: "PENDING",
        status_detail: "Waiting for planning to finish.",
        updated_at: "2026-03-10T08:00:00Z",
      },
      {
        task_id: "debug-task-2",
        description: "Draft the homepage announcement and launch email",
        run_id: "debug-workflow-run",
        assigned_agent: "copy-agent",
        status: "PENDING",
        status_detail: "Queued after planning.",
        updated_at: "2026-03-10T08:00:01Z",
      },
      {
        task_id: "debug-task-3",
        description: "Prepare the support FAQ and social teaser copy",
        run_id: "debug-workflow-run",
        assigned_agent: "ops-agent",
        status: "PENDING",
        status_detail: "Queued after planning.",
        updated_at: "2026-03-10T08:00:02Z",
      },
    ],
    todos: [
      { content: "Confirm launch audience and release date", status: "pending" },
      { content: "Prepare launch announcement and email copy", status: "pending" },
      { content: "Prepare support FAQ", status: "pending" },
    ],
  },
  {
    label: "Running",
    execution_state: "RUNNING",
    orchestration_reason:
      "The workflow is now executing in parallel across strategy, copy, and support tracks.",
    planner_goal:
      "Prepare a coordinated product launch plan with parallel subtasks and a final summary.",
    run_id: "debug-workflow-run",
    task_pool: [
      {
        task_id: "debug-task-1",
        description: "Confirm the launch window and target audience",
        run_id: "debug-workflow-run",
        assigned_agent: "strategy-agent",
        status: "DONE",
        status_detail: "Launch date and audience confirmed.",
        updated_at: "2026-03-10T08:01:00Z",
        result:
          "The release is tentatively scheduled for April 8 and targets current paid team admins.",
      },
      {
        task_id: "debug-task-2",
        description: "Draft the homepage announcement and launch email",
        run_id: "debug-workflow-run",
        assigned_agent: "copy-agent",
        status: "RUNNING",
        status_detail:
          "Drafting the launch copy, tightening the homepage headline, and preparing a second bilingual pass for the email sequence.",
        updated_at: "2026-03-10T08:01:10Z",
      },
      {
        task_id: "debug-task-3",
        description: "Prepare the support FAQ and social teaser copy",
        run_id: "debug-workflow-run",
        assigned_agent: "ops-agent",
        status: "RUNNING",
        status_detail:
          "Collecting support edge cases and assembling a concise teaser thread for launch day.",
        updated_at: "2026-03-10T08:01:15Z",
      },
    ],
    todos: [
      { content: "Confirm launch audience and release date", status: "completed" },
      {
        content: "Prepare launch announcement and email copy",
        status: "in_progress",
      },
      { content: "Prepare support FAQ", status: "in_progress" },
    ],
  },
  {
    label: "Clarification",
    execution_state: "RUNNING",
    orchestration_reason:
      "One branch is waiting for clarification while the others continue.",
    planner_goal:
      "Prepare a coordinated product launch plan with parallel subtasks and a final summary.",
    run_id: "debug-workflow-run",
    task_pool: [
      {
        task_id: "debug-task-1",
        description: "Confirm the launch window and target audience",
        run_id: "debug-workflow-run",
        assigned_agent: "strategy-agent",
        status: "DONE",
        status_detail: "Launch date and audience confirmed.",
        updated_at: "2026-03-10T08:02:00Z",
        result:
          "The release is tentatively scheduled for April 8 and targets current paid team admins.",
      },
      {
        task_id: "debug-task-2",
        description: "Draft the homepage announcement and launch email",
        run_id: "debug-workflow-run",
        assigned_agent: "copy-agent",
        status: "RUNNING",
        status_detail: "The launch announcement draft is in editorial review.",
        updated_at: "2026-03-10T08:02:10Z",
      },
      {
        task_id: "debug-task-3",
        description: "Prepare the support FAQ and social teaser copy",
        run_id: "debug-workflow-run",
        assigned_agent: "ops-agent",
        status: "RUNNING",
        status_detail:
          "Waiting to confirm whether the FAQ should ship in English on launch day.",
        clarification_prompt:
          "Should the support FAQ ship with an English version on launch day?",
        updated_at: "2026-03-10T08:02:15Z",
      },
    ],
    todos: [
      { content: "Confirm launch audience and release date", status: "completed" },
      {
        content: "Prepare launch announcement and email copy",
        status: "in_progress",
      },
      { content: "Prepare support FAQ", status: "pending" },
    ],
  },
  {
    label: "Summarizing",
    execution_state: "EXECUTING_DONE",
    orchestration_reason:
      "All branches are complete and the assistant is composing the final summary.",
    planner_goal:
      "Prepare a coordinated product launch plan with parallel subtasks and a final summary.",
    run_id: "debug-workflow-run",
    task_pool: [
      {
        task_id: "debug-task-1",
        description: "Confirm the launch window and target audience",
        run_id: "debug-workflow-run",
        assigned_agent: "strategy-agent",
        status: "DONE",
        status_detail: "Launch date and audience confirmed.",
        updated_at: "2026-03-10T08:03:00Z",
        result:
          "The release is tentatively scheduled for April 8 and targets current paid team admins.",
      },
      {
        task_id: "debug-task-2",
        description: "Draft the homepage announcement and launch email",
        run_id: "debug-workflow-run",
        assigned_agent: "copy-agent",
        status: "DONE",
        status_detail: "Launch copy approved.",
        updated_at: "2026-03-10T08:03:10Z",
        result:
          "The homepage announcement and launch email draft are complete and approved for publication.",
      },
      {
        task_id: "debug-task-3",
        description: "Prepare the support FAQ and social teaser copy",
        run_id: "debug-workflow-run",
        assigned_agent: "ops-agent",
        status: "DONE",
        status_detail: "FAQ and teaser copy approved.",
        updated_at: "2026-03-10T08:03:15Z",
        result:
          "The support FAQ ships in English on launch day and the teaser thread is ready.",
      },
    ],
    todos: [
      { content: "Confirm launch audience and release date", status: "completed" },
      { content: "Prepare launch announcement and email copy", status: "completed" },
      { content: "Prepare support FAQ", status: "completed" },
    ],
  },
];

export function WorkflowVisualDebug({ threadId }: { threadId: string }) {
  const [stageIndex, setStageIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(true);
  const [context, setContext] = useState<
    Omit<
      AgentThreadContext,
      "thread_id" | "thinking_enabled" | "is_plan_mode" | "subagent_enabled"
    > & {
      mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
      reasoning_effort?: "minimal" | "low" | "medium" | "high";
      requested_orchestration_mode?: "auto" | "leader" | "workflow";
    }
  >({
    model_name: "gpt-5",
    mode: "pro",
    reasoning_effort: "medium",
    requested_orchestration_mode: "workflow",
  });
  const stage = DEBUG_STAGES[stageIndex]!;
  const { clearAllTasks, hydrateTasks } = useTaskActions();
  const {
    footerContainerRef,
    footerOverlayRef,
    inputShellRef,
    paddingBottom,
  } = useFooterPadding();

  useEffect(() => {
    if (!isPlaying) {
      return;
    }
    const timer = window.setInterval(() => {
      setStageIndex((current) => (current + 1) % DEBUG_STAGES.length);
    }, 1600);
    return () => window.clearInterval(timer);
  }, [isPlaying]);

  useEffect(() => {
    hydrateTasks(
      (stage.task_pool ?? []).map((task) =>
        fromMultiAgentTaskState(task, threadId),
      ),
      {
        source: "multi_agent",
        runId: stage.run_id ?? null,
      },
    );
  }, [hydrateTasks, stage.run_id, stage.task_pool, threadId]);

  useEffect(() => clearAllTasks, [clearAllTasks]);

  const thread = useMemo(
    () =>
      ({
        messages: BASE_MESSAGES,
        values: {
          title: "Workflow visual debug",
          messages: BASE_MESSAGES,
          artifacts: [],
          requested_orchestration_mode: "workflow",
          resolved_orchestration_mode: "workflow",
          ...stage,
        } satisfies AgentThreadState,
        isLoading: true,
        isThreadLoading: false,
        submit: async () => undefined,
        stop: async () => undefined,
      }) as unknown as BaseStream<AgentThreadState>,
    [stage],
  );

  return (
    <ThreadContext.Provider value={{ thread }}>
      <div className="relative flex size-full min-h-0 justify-between">
        <header className="bg-background/80 absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center justify-between px-4 shadow-xs backdrop-blur">
          <div className="flex min-w-0 items-center gap-3 text-sm font-medium">
            <span>Workflow Visual Debug</span>
            <span className="text-muted-foreground text-xs">{stage.label}</span>
            <span
              className="text-muted-foreground text-xs"
              data-testid="workflow-debug-padding"
            >
              footer-padding: {paddingBottom}px
            </span>
          </div>
          <div className="flex items-center gap-1">
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={() =>
                setStageIndex((current) =>
                  current === 0 ? DEBUG_STAGES.length - 1 : current - 1,
                )
              }
            >
              <SkipBackIcon />
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={() => setIsPlaying((value) => !value)}
            >
              {isPlaying ? <PauseIcon /> : <PlayIcon />}
            </Button>
            <Button
              size="icon-sm"
              variant="ghost"
              onClick={() =>
                setStageIndex((current) => (current + 1) % DEBUG_STAGES.length)
              }
            >
              <SkipForwardIcon />
            </Button>
          </div>
        </header>
        <main className="flex min-h-0 max-w-full grow flex-col">
          <div className="flex size-full justify-center">
            <MessageList
              className="size-full pt-10"
              threadId={threadId}
              thread={thread}
              paddingBottom={paddingBottom}
            />
          </div>
          <div
            ref={footerContainerRef}
            data-testid="workflow-debug-footer-shell"
            className="absolute right-0 bottom-0 left-0 z-30 flex justify-center px-4"
          >
            <div className="relative w-full max-w-(--container-width-md)">
              <div
                ref={footerOverlayRef}
                data-testid="workflow-debug-overlay"
                className="absolute right-0 bottom-full left-0 z-0 pb-0.5"
              >
                <div className="flex flex-col gap-0.5">
                  <WorkflowFooterBar thread={thread} />
                  <TodoList
                    className="bg-background/5"
                    todos={thread.values.todos ?? []}
                    hidden={
                      !thread.values.todos || thread.values.todos.length === 0
                    }
                  />
                </div>
              </div>
              <div ref={inputShellRef} data-testid="workflow-debug-input-shell">
                <InputBox
                  className={cn(
                    "bg-background/5 w-full rounded-t-none border-t-0 *:data-[slot='input-group']:rounded-t-none",
                  )}
                  status="streaming"
                  context={context}
                  onContextChange={setContext}
                  onSubmit={() => undefined}
                  onStop={() => setIsPlaying(false)}
                />
              </div>
            </div>
          </div>
        </main>
      </div>
    </ThreadContext.Provider>
  );
}

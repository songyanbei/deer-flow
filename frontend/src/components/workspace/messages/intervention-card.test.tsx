import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/core/i18n/context";
import type { TaskViewModel } from "@/core/tasks/types";

import { ThreadContext, type ThreadContextType } from "./context";
import { InterventionCard } from "./intervention-card";

const mutateAsyncMock = vi.fn();

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/core/interventions/hooks", () => ({
  useResolveIntervention: () => ({
    mutateAsync: mutateAsyncMock,
    isPending: false,
  }),
}));

vi.mock("@/core/settings", () => ({
  useLocalSettings: () => [
    {
      context: {
        mode: "ultra",
        requested_orchestration_mode: "workflow",
      },
    },
    vi.fn(),
  ],
}));

const mockThread = {
  submit: vi.fn(),
  values: { run_id: "run-1" },
  messages: [],
  isLoading: false,
} as unknown;

function createTask(overrides: Partial<TaskViewModel> = {}): TaskViewModel {
  return {
    id: overrides.id ?? "task-1",
    source: overrides.source ?? "multi_agent",
    threadId: overrides.threadId ?? "thread-1",
    description: overrides.description ?? "Intervention task",
    status: overrides.status ?? "waiting_intervention",
    interventionRequest: {
      request_id: "req-1",
      fingerprint: "fp-1",
      intervention_type: "approval",
      title: "Need approval",
      reason: "Approve the operation",
      source_agent: "meeting-agent",
      source_task_id: "task-1",
      action_schema: {
        actions: [
          {
            key: "approve",
            label: "Approve",
            kind: "button",
            resolution_behavior: "resume_current_task",
          },
        ],
      },
      created_at: "2026-03-17T10:00:00.000Z",
    },
    ...overrides,
  };
}

function renderCard(task: TaskViewModel) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(
      <I18nProvider initialLocale="en-US">
        <ThreadContext.Provider
          value={{ thread: mockThread as ThreadContextType["thread"] }}
        >
          <InterventionCard task={task} />
        </ThreadContext.Provider>
      </I18nProvider>,
    );
  });

  return {
    container,
    cleanup() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

describe("InterventionCard", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    mutateAsyncMock.mockReset();
    (mockThread as { submit: ReturnType<typeof vi.fn> }).submit.mockReset();
  });

  it("renders display-first content when display is present", () => {
    const rendered = renderCard(
      createTask({
        interventionRequest: {
          request_id: "req-1",
          fingerprint: "fp-1",
          intervention_type: "approval",
          title: "Need approval",
          reason: "Approve the operation",
          source_agent: "meeting-agent",
          source_task_id: "task-1",
          display: {
            title: "Confirm meeting booking",
            summary: "Please confirm the meeting room booking details.",
            sections: [
              {
                title: "Meeting details",
                items: [
                  { label: "Topic", value: "Product intro" },
                  { label: "Time", value: "Tomorrow 3:00 PM - 5:00 PM" },
                ],
              },
            ],
            risk_tip: "A meeting room reservation will be created after approval.",
            primary_action_label: "Confirm booking",
            secondary_action_label: "Do not book",
          },
          action_schema: {
            actions: [
              {
                key: "approve",
                label: "Approve",
                kind: "button",
                resolution_behavior: "resume_current_task",
              },
              {
                key: "reject",
                label: "Reject",
                kind: "button",
                resolution_behavior: "fail_current_task",
              },
            ],
          },
          created_at: "2026-03-17T10:00:00.000Z",
        },
      }),
    );

    expect(rendered.container.textContent).toContain("Confirm meeting booking");
    expect(rendered.container.textContent).toContain(
      "Please confirm the meeting room booking details.",
    );
    expect(rendered.container.textContent).toContain("Product intro");
    expect(rendered.container.textContent).toContain("Confirm booking");
    expect(rendered.container.textContent).toContain("Do not book");
    expect(rendered.container.textContent).not.toContain("meeting-agent");

    rendered.cleanup();
  });

  it("falls back to protocol fields when display is absent", () => {
    const rendered = renderCard(
      createTask({
        interventionRequest: {
          request_id: "req-1",
          fingerprint: "fp-1",
          intervention_type: "approval",
          title: "Need approval",
          reason: "Approve the operation",
          source_agent: "meeting-agent",
          source_task_id: "task-1",
          context: {
            room_name: "Room A",
          },
          action_schema: {
            actions: [
              {
                key: "approve",
                label: "Approve",
                kind: "button",
                resolution_behavior: "resume_current_task",
              },
            ],
          },
          created_at: "2026-03-17T10:00:00.000Z",
        },
      }),
    );

    expect(rendered.container.textContent).toContain("Need approval");
    expect(rendered.container.textContent).toContain("Approve the operation");
    expect(rendered.container.textContent).toContain("Room A");

    rendered.cleanup();
  });

  it("does not render debug details in the default user-facing card", () => {
    const rendered = renderCard(
      createTask({
        interventionRequest: {
          request_id: "req-1",
          fingerprint: "fp-1",
          intervention_type: "approval",
          title: "Need approval",
          reason: "Approve the operation",
          source_agent: "meeting-agent",
          source_task_id: "task-1",
          display: {
            title: "Confirm operation",
            debug: {
              source_agent: "meeting-agent",
              tool_name: "meeting_createMeeting",
              raw_args: {
                roomId: "room_123",
              },
            },
          },
          action_schema: {
            actions: [
              {
                key: "approve",
                label: "Approve",
                kind: "button",
                resolution_behavior: "resume_current_task",
              },
            ],
          },
          created_at: "2026-03-17T10:00:00.000Z",
        },
      }),
    );

    expect(rendered.container.textContent).not.toContain("meeting_createMeeting");
    expect(rendered.container.textContent).not.toContain("room_123");

    rendered.cleanup();
  });

  it("uses display action copy and respond placeholder", () => {
    const rendered = renderCard(
      createTask({
        interventionRequest: {
          request_id: "req-1",
          fingerprint: "fp-1",
          intervention_type: "override",
          title: "Need input",
          reason: "Provide a revised comment",
          source_agent: "meeting-agent",
          source_task_id: "task-1",
          display: {
            title: "Adjust meeting note",
            respond_action_label: "Submit response",
            respond_placeholder: "Add the note you want to send",
          },
          action_schema: {
            actions: [
              {
                key: "provide_input",
                label: "Provide input",
                kind: "input",
                resolution_behavior: "resume_current_task",
              },
            ],
          },
          created_at: "2026-03-17T10:00:00.000Z",
        },
      }),
    );

    expect(rendered.container.textContent).toContain("Submit response");
    const textarea = rendered.container.querySelector("textarea");
    expect(textarea?.getAttribute("placeholder")).toBe(
      "Add the note you want to send",
    );

    rendered.cleanup();
  });

  it("calls thread.submit with correct resume params after approve resolves", async () => {
    const submitMock = (mockThread as { submit: ReturnType<typeof vi.fn> }).submit;
    submitMock.mockResolvedValue(undefined);
    mutateAsyncMock.mockResolvedValue({
      ok: true,
      thread_id: "thread-1",
      request_id: "req-1",
      fingerprint: "fp-1",
      accepted: true,
      checkpoint: {
        checkpoint_id: "cp-1",
        checkpoint_ns: "",
      },
      resume_action: "submit_resume",
      resume_payload: {
        message: "[intervention_resolved] request_id=req-1 action_key=approve",
      },
    });

    const rendered = renderCard(createTask());

    const approveButton = Array.from(
      rendered.container.querySelectorAll("button"),
    ).find((btn) => btn.textContent?.includes("Approve"));
    expect(approveButton).toBeTruthy();

    await act(async () => {
      approveButton!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    // mutateAsync should have been called with the resolve params
    expect(mutateAsyncMock).toHaveBeenCalledWith({
      threadId: "thread-1",
      requestId: "req-1",
      fingerprint: "fp-1",
      actionKey: "approve",
      payload: {},
    });

    // thread.submit must be called to create the observable resume run
    expect(submitMock).toHaveBeenCalledTimes(1);
    const [submitValues, submitOptions] = submitMock.mock.calls[0]!;

    // Verify the human message carries the intervention_resolved prefix
    expect(submitValues.messages[0].type).toBe("human");
    expect(submitValues.messages[0].content[0].text).toBe(
      "[intervention_resolved] request_id=req-1 action_key=approve",
    );

    // Verify critical context params for the backend resume path
    expect(submitOptions.threadId).toBe("thread-1");
    expect(submitOptions.checkpoint).toEqual({
      checkpoint_id: "cp-1",
      checkpoint_ns: "",
    });
    expect(submitOptions.context.workflow_clarification_resume).toBe(true);
    expect(submitOptions.context.workflow_resume_run_id).toBe("run-1");
    expect(submitOptions.context.workflow_resume_task_id).toBe("task-1");
    expect(submitOptions.streamResumable).toBe(true);
    expect(submitOptions.streamMode).toEqual(["values", "messages-tuple", "custom"]);

    // Verify mode-derived context (mock settings has mode: "ultra")
    expect(submitOptions.context.thinking_enabled).toBe(true);
    expect(submitOptions.context.is_plan_mode).toBe(true);
    expect(submitOptions.context.subagent_enabled).toBe(true);

    rendered.cleanup();
  });

  it("does not call thread.submit when reject resolves with null resume_action", async () => {
    const submitMock = (mockThread as { submit: ReturnType<typeof vi.fn> }).submit;
    mutateAsyncMock.mockResolvedValue({
      ok: true,
      thread_id: "thread-1",
      request_id: "req-1",
      fingerprint: "fp-1",
      accepted: true,
      checkpoint: null,
      resume_action: null,
      resume_payload: null,
    });

    const rendered = renderCard(
      createTask({
        interventionRequest: {
          request_id: "req-1",
          fingerprint: "fp-1",
          intervention_type: "approval",
          title: "Need approval",
          reason: "Approve the operation",
          source_agent: "meeting-agent",
          source_task_id: "task-1",
          action_schema: {
            actions: [
              {
                key: "approve",
                label: "Approve",
                kind: "button",
                resolution_behavior: "resume_current_task",
              },
              {
                key: "reject",
                label: "Reject",
                kind: "button",
                resolution_behavior: "fail_current_task",
              },
            ],
          },
          created_at: "2026-03-17T10:00:00.000Z",
        },
      }),
    );

    const rejectButton = Array.from(
      rendered.container.querySelectorAll("button"),
    ).find((btn) => btn.textContent?.includes("Reject"));
    expect(rejectButton).toBeTruthy();

    await act(async () => {
      rejectButton!.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });

    expect(mutateAsyncMock).toHaveBeenCalledWith(
      expect.objectContaining({ actionKey: "reject" }),
    );
    expect(submitMock).not.toHaveBeenCalled();

    rendered.cleanup();
  });

  it("submits a resume run after resolve succeeds with submit_resume", async () => {
    mutateAsyncMock.mockResolvedValue({
      ok: true,
      thread_id: "thread-1",
      request_id: "req-1",
      fingerprint: "fp-1",
      accepted: true,
      checkpoint: {
        checkpoint_id: "cp-1",
        checkpoint_ns: "",
      },
      resume_action: "submit_resume",
      resume_payload: {
        message: "[intervention_resolved] request_id=req-1 action_key=approve",
      },
    });

    const rendered = renderCard(createTask());

    const approveButton = Array.from(
      rendered.container.querySelectorAll("button"),
    ).find((button) => button.textContent?.includes("Approve"));

    if (!approveButton) {
      throw new Error("Expected approve button");
    }

    await act(async () => {
      approveButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(mutateAsyncMock).toHaveBeenCalledWith({
      threadId: "thread-1",
      requestId: "req-1",
      fingerprint: "fp-1",
      actionKey: "approve",
      payload: {},
    });
    expect((mockThread as { submit: ReturnType<typeof vi.fn> }).submit).toHaveBeenCalledTimes(1);
    expect((mockThread as { submit: ReturnType<typeof vi.fn> }).submit).toHaveBeenCalledWith(
      {
        messages: [
          {
            type: "human",
            content: [
              {
                type: "text",
                text: "[intervention_resolved] request_id=req-1 action_key=approve",
              },
            ],
          },
        ],
      },
      expect.objectContaining({
        threadId: "thread-1",
        streamResumable: true,
        streamMode: ["values", "messages-tuple", "custom"],
        checkpoint: {
          checkpoint_id: "cp-1",
          checkpoint_ns: "",
        },
        context: expect.objectContaining({
          requested_orchestration_mode: "workflow",
          workflow_clarification_resume: true,
          workflow_resume_run_id: "run-1",
          workflow_resume_task_id: "task-1",
          thread_id: "thread-1",
        }),
      }),
    );

    rendered.cleanup();
  });
});

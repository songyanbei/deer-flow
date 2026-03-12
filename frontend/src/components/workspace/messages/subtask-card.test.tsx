import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";

import { I18nProvider } from "@/core/i18n/context";
import {
  SubtasksProvider,
  useSubtaskContext,
} from "@/core/tasks/context";
import type { TaskViewModel } from "@/core/tasks/types";

import { SubtaskCard } from "./subtask-card";

vi.mock("streamdown", () => ({
  Streamdown: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

vi.mock("./markdown-content", () => ({
  MarkdownContent: ({ content }: { content: string }) => <div>{content}</div>,
}));

function createTask(overrides: Partial<TaskViewModel>): TaskViewModel {
  return {
    id: overrides.id ?? "task-1",
    source: overrides.source ?? "multi_agent",
    description: overrides.description ?? "Task",
    status: overrides.status ?? "in_progress",
    ...overrides,
  };
}

function renderCard(task: TaskViewModel) {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  let context!: ReturnType<typeof useSubtaskContext>;

  function Harness() {
    context = useSubtaskContext();
    return <SubtaskCard taskId={task.id} isLoading={false} />;
  }

  act(() => {
    root.render(
      <I18nProvider initialLocale="en-US">
        <SubtasksProvider>
          <Harness />
        </SubtasksProvider>
      </I18nProvider>,
    );
  });

  act(() => {
    context.upsertTask(task);
  });

  const trigger = container.querySelector("button");
  if (!trigger) {
    throw new Error("Subtask card toggle button not found.");
  }

  act(() => {
    trigger.dispatchEvent(new MouseEvent("click", { bubbles: true }));
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

describe("SubtaskCard", () => {
  it("shows resumed execution details after dependency resolution", () => {
    const rendered = renderCard(
      createTask({
        id: "task-1",
        status: "in_progress",
        description: "Book the meeting room",
        statusDetail: "Dispatching task to domain agent",
        resumeCount: 1,
        resolvedInputs: {
          "helper-1": {
            openId: "ou_123",
            city: "Shanghai",
          },
        },
      }),
    );

    expect(rendered.container.textContent).toContain(
      "Dependency resolved; resumed execution",
    );
    expect(rendered.container.textContent).toContain("Resolved inputs");
    expect(rendered.container.textContent).toContain("ou_123");
    expect(rendered.container.textContent).toContain("Shanghai");

    rendered.cleanup();
  });
});

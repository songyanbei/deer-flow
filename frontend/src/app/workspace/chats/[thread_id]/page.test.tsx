import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const useThreadChatMock = vi.fn();
const useSpecificChatModeMock = vi.fn();
const useThreadStreamMock = vi.fn();
const useLocalSettingsMock = vi.fn();
const useFooterPaddingMock = vi.fn();
const showNotificationMock = vi.fn();

vi.mock("@/components/workspace/chats", () => ({
  ChatBox: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  useSpecificChatMode: () => useSpecificChatModeMock(),
  useThreadChat: () => useThreadChatMock(),
}));

vi.mock("@/components/workspace/input-box", () => ({
  InputBox: ({
    className,
    status,
  }: {
    className?: string;
    status?: string;
  }) => (
    <div
      data-testid="input-box"
      data-class-name={className ?? ""}
      data-status={status ?? ""}
    />
  ),
}));

vi.mock("@/components/workspace/messages", () => ({
  MessageList: ({ paddingBottom }: { paddingBottom: number }) => (
    <div
      data-testid="message-list"
      data-padding-bottom={String(paddingBottom)}
    />
  ),
}));

vi.mock("@/components/workspace/workflow-footer-bar", () => ({
  WorkflowFooterBar: () => (
    <div data-testid="workflow-footer">workflow footer</div>
  ),
}));

vi.mock("@/components/workspace/todo-list", () => ({
  TodoList: () => <div data-testid="todo-list">todo list</div>,
}));

vi.mock("@/components/workspace/thread-title", () => ({
  ThreadTitle: () => <div data-testid="thread-title">thread title</div>,
}));

vi.mock("@/components/workspace/orchestration-summary", () => ({
  OrchestrationSummary: () => (
    <div data-testid="orchestration-summary">workflow summary</div>
  ),
}));

vi.mock("@/components/workspace/artifacts", () => ({
  ArtifactTrigger: () => <div data-testid="artifact-trigger">artifact</div>,
}));

vi.mock("@/components/workspace/welcome", () => ({
  Welcome: () => <div data-testid="welcome">welcome</div>,
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      common: {
        notAvailableInDemoMode: "Not available in demo mode",
      },
    },
  }),
}));

vi.mock("@/core/notification/hooks", () => ({
  useNotification: () => ({
    showNotification: showNotificationMock,
  }),
}));

vi.mock("@/core/settings", () => ({
  useLocalSettings: () => useLocalSettingsMock(),
}));

vi.mock("@/core/threads/hooks", () => ({
  useThreadStream: () => useThreadStreamMock(),
}));

vi.mock("@/components/workspace/use-footer-padding", () => ({
  useFooterPadding: () => useFooterPaddingMock(),
}));

vi.mock("@/env", () => ({
  env: {
    NEXT_PUBLIC_STATIC_WEBSITE_ONLY: "false",
  },
}));

import ChatPage from "./page";

type ThreadValues = {
  resolved_orchestration_mode?: "leader" | "workflow" | null;
  workflow_stage?: string | null;
  task_pool?: unknown[];
  todos?: unknown[];
  messages?: unknown[];
  title?: string;
};

let currentThread: {
  values: ThreadValues;
  isLoading: boolean;
  stop: ReturnType<typeof vi.fn>;
} = {
  values: {
    resolved_orchestration_mode: null,
    workflow_stage: null,
    task_pool: [],
    todos: [],
    messages: [],
    title: "Thread title",
  },
  isLoading: false,
  stop: vi.fn(),
};

function renderPage() {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);

  act(() => {
    root.render(<ChatPage />);
  });

  return {
    container,
    rerender() {
      act(() => {
        root.render(<ChatPage />);
      });
    },
    cleanup() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

describe("ChatPage workflow integration", () => {
  beforeEach(() => {
    useThreadChatMock.mockReset();
    useSpecificChatModeMock.mockReset();
    useThreadStreamMock.mockReset();
    useLocalSettingsMock.mockReset();
    useFooterPaddingMock.mockReset();
    showNotificationMock.mockReset();

    currentThread = {
      values: {
        resolved_orchestration_mode: null,
        workflow_stage: null,
        task_pool: [],
        todos: [],
        messages: [],
        title: "Thread title",
      },
      isLoading: false,
      stop: vi.fn(),
    };

    useThreadChatMock.mockReturnValue({
      threadId: "thread-1",
      isNewThread: false,
      setIsNewThread: vi.fn(),
      isMock: false,
    });
    useThreadStreamMock.mockImplementation(() => [currentThread, vi.fn()]);
    useLocalSettingsMock.mockReturnValue([
      {
        context: {
          mode: "flash",
          requested_orchestration_mode: "auto",
        },
      },
      vi.fn(),
    ]);
    useFooterPaddingMock.mockReturnValue({
      footerContainerRef: { current: null },
      footerOverlayRef: { current: null },
      inputShellRef: { current: null },
      paddingBottom: 240,
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("docks the input shell and preserves message padding when workflow shell is active", () => {
    currentThread = {
      values: {
        resolved_orchestration_mode: "workflow",
        workflow_stage: "planning",
        task_pool: [],
        todos: [],
        messages: [],
        title: "Workflow thread",
      },
      isLoading: true,
      stop: vi.fn(),
    };

    const rendered = renderPage();
    const inputBox = rendered.container.querySelector(
      '[data-testid="input-box"]',
    );
    const messageList = rendered.container.querySelector(
      '[data-testid="message-list"]',
    );

    expect(inputBox?.getAttribute("data-class-name")).toContain(
      "rounded-t-none",
    );
    expect(messageList?.getAttribute("data-padding-bottom")).toBe("240");
    expect(rendered.container.textContent).toContain("workflow footer");

    rendered.cleanup();
  });

  it("keeps the default input shell styling outside workflow mode", () => {
    currentThread = {
      values: {
        resolved_orchestration_mode: "leader",
        workflow_stage: null,
        task_pool: [],
        todos: [],
        messages: [],
        title: "Leader thread",
      },
      isLoading: false,
      stop: vi.fn(),
    };

    const rendered = renderPage();
    const inputBox = rendered.container.querySelector(
      '[data-testid="input-box"]',
    );

    expect(inputBox?.getAttribute("data-class-name")).not.toContain(
      "rounded-t-none",
    );

    rendered.cleanup();
  });
});

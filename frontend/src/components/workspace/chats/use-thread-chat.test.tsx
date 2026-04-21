import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useThreadChat } from "./use-thread-chat";

const useParamsMock = vi.fn();
const usePathnameMock = vi.fn();
const useSearchParamsMock = vi.fn();
const createRuntimeThreadMock = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => useParamsMock(),
  usePathname: () => usePathnameMock(),
  useSearchParams: () => useSearchParamsMock(),
}));

vi.mock("@/core/threads/runtime-api", () => ({
  createRuntimeThread: () => createRuntimeThreadMock(),
}));

function Harness() {
  const state = useThreadChat();
  // Drop non-serializable fields for snapshot purposes.
  const { setIsNewThread, retryThreadCreation, threadCreationError, ...rest } =
    state;
  void setIsNewThread;
  void retryThreadCreation;
  return (
    <pre>
      {JSON.stringify({
        ...rest,
        threadCreationError: threadCreationError?.message ?? null,
      })}
    </pre>
  );
}

describe("useThreadChat thread lifecycle", () => {
  beforeEach(() => {
    useParamsMock.mockReset();
    usePathnameMock.mockReset();
    useSearchParamsMock.mockReset();
    createRuntimeThreadMock.mockReset();

    useSearchParamsMock.mockReturnValue({
      get: () => null,
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("does not call Gateway during SSR for the new-thread route", () => {
    useParamsMock.mockReturnValue({ thread_id: "new" });
    usePathnameMock.mockReturnValue("/workspace/chats/new");

    const html = renderToString(<Harness />);

    expect(html).toContain("&quot;threadId&quot;:&quot;new&quot;");
    expect(html).toContain("&quot;isNewThread&quot;:true");
    expect(html).toContain("&quot;threadReady&quot;:false");
    expect(createRuntimeThreadMock).not.toHaveBeenCalled();
  });

  it("creates a Gateway-registered thread on mount for the new-thread route", async () => {
    useParamsMock.mockReturnValue({ thread_id: "new" });
    usePathnameMock.mockReturnValue("/workspace/chats/new");
    createRuntimeThreadMock.mockResolvedValue({
      thread_id: "gateway-thread-id",
      portal_session_id: "deerflow-web:gateway-thread-id",
      tenant_id: "tenant-a",
      user_id: "user-1",
      created_at: "2026-04-21T00:00:00Z",
    });

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<Harness />);
    });

    // Gateway resolution flushes on the microtask queue; allow React to apply state.
    await act(async () => {
      await Promise.resolve();
    });

    expect(container.textContent).toContain('"threadId":"gateway-thread-id"');
    expect(container.textContent).toContain('"isNewThread":true');
    expect(container.textContent).toContain('"threadReady":true');
    expect(createRuntimeThreadMock).toHaveBeenCalledTimes(1);

    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("surfaces the error state when the Gateway thread creation fails", async () => {
    useParamsMock.mockReturnValue({ thread_id: "new" });
    usePathnameMock.mockReturnValue("/workspace/chats/new");
    createRuntimeThreadMock.mockRejectedValue(new Error("network down"));

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<Harness />);
    });

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain('"threadCreationState":"error"');
    expect(container.textContent).toContain('"threadCreationError":"network down"');
    expect(container.textContent).toContain('"threadReady":false');

    act(() => {
      root.unmount();
    });
    container.remove();
  });

  it("uses the path thread id as-is for existing threads", async () => {
    useParamsMock.mockReturnValue({ thread_id: "existing-abc" });
    usePathnameMock.mockReturnValue("/workspace/chats/existing-abc");

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    await act(async () => {
      root.render(<Harness />);
    });

    expect(container.textContent).toContain('"threadId":"existing-abc"');
    expect(container.textContent).toContain('"isNewThread":false');
    expect(container.textContent).toContain('"threadReady":true');
    expect(createRuntimeThreadMock).not.toHaveBeenCalled();

    act(() => {
      root.unmount();
    });
    container.remove();
  });
});

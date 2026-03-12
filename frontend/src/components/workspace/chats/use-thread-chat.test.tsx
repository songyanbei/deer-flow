import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useThreadChat } from "./use-thread-chat";

const useParamsMock = vi.fn();
const usePathnameMock = vi.fn();
const useSearchParamsMock = vi.fn();
const uuidMock = vi.fn();

vi.mock("next/navigation", () => ({
  useParams: () => useParamsMock(),
  usePathname: () => usePathnameMock(),
  useSearchParams: () => useSearchParamsMock(),
}));

vi.mock("@/core/utils/uuid", () => ({
  uuid: () => uuidMock(),
}));

function Harness() {
  const state = useThreadChat();
  return <pre>{JSON.stringify(state)}</pre>;
}

describe("useThreadChat hydration safety", () => {
  beforeEach(() => {
    useParamsMock.mockReset();
    usePathnameMock.mockReset();
    useSearchParamsMock.mockReset();
    uuidMock.mockReset();

    useSearchParamsMock.mockReturnValue({
      get: () => null,
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("does not generate a uuid during SSR for the new-thread route", () => {
    useParamsMock.mockReturnValue({ thread_id: "new" });
    usePathnameMock.mockReturnValue("/workspace/chats/new");

    const html = renderToString(<Harness />);

    expect(html).toContain("&quot;threadId&quot;:&quot;new&quot;");
    expect(html).toContain("&quot;isNewThread&quot;:true");
    expect(uuidMock).not.toHaveBeenCalled();
  });

  it("generates a uuid after mount for the new-thread route", () => {
    useParamsMock.mockReturnValue({ thread_id: "new" });
    usePathnameMock.mockReturnValue("/workspace/chats/new");
    uuidMock.mockReturnValue("generated-thread-id");

    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<Harness />);
    });

    expect(container.textContent).toContain('"threadId":"generated-thread-id"');
    expect(container.textContent).toContain('"isNewThread":true');
    expect(uuidMock).toHaveBeenCalledTimes(1);

    act(() => {
      root.unmount();
    });
    container.remove();
  });
});

import { StrictMode, act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
  type Mock,
} from "vitest";

const replaceMock = vi.fn();
const searchParamsStore = new Map<string, string>();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock }),
  useSearchParams: () => ({
    get: (key: string) => searchParamsStore.get(key) ?? null,
  }),
}));

vi.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({
    t: {
      sso: {
        callback: {
          pageTitle: "SSO",
          pending: "PENDING_TEXT",
          pendingHint: "PENDING_HINT",
          invalidEntryTitle: "INVALID_ENTRY",
          invalidEntryDescription: "INVALID_ENTRY_DESC",
          expiredTitle: "EXPIRED_TITLE",
          expiredDescription: "EXPIRED_DESC",
          unavailableTitle: "UNAVAILABLE_TITLE",
          unavailableDescription: "UNAVAILABLE_DESC",
          networkTitle: "NETWORK_TITLE",
          networkDescription: "NETWORK_DESC",
          backToMossHubHint: "BACK_HINT",
        },
      },
    },
  }),
}));

import SsoCallbackPage from "./page";

function setQuery(params: Record<string, string | undefined>) {
  searchParamsStore.clear();
  for (const [k, v] of Object.entries(params)) {
    if (typeof v === "string") searchParamsStore.set(k, v);
  }
}

function mountPage(options?: { strict?: boolean }): {
  container: HTMLDivElement;
  root: Root;
  unmount: () => void;
} {
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => {
    root.render(
      options?.strict ? (
        <StrictMode>
          <SsoCallbackPage />
        </StrictMode>
      ) : (
        <SsoCallbackPage />
      ),
    );
  });
  return {
    container,
    root,
    unmount() {
      act(() => {
        root.unmount();
      });
      container.remove();
    },
  };
}

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

function makeResponse(init: {
  ok?: boolean;
  status?: number;
  json?: () => unknown;
}) {
  const status = init.status ?? (init.ok ? 200 : 500);
  const ok = init.ok ?? (status >= 200 && status < 300);
  return {
    ok,
    status,
    json:
      init.json ??
      (() => Promise.resolve({})),
  } as unknown as Response;
}

describe("SsoCallbackPage", () => {
  let fetchMock: Mock;

  beforeEach(() => {
    replaceMock.mockReset();
    searchParamsStore.clear();
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("POSTs immediately when ticket is present", async () => {
    setQuery({ ticket: "abc" });
    fetchMock.mockResolvedValue(
      makeResponse({ ok: true, json: () => Promise.resolve({ redirect: "/chat" }) }),
    );

    const { unmount } = mountPage();
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/sso/callback");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe(
      "application/json",
    );

    const parsed = JSON.parse(init.body as string) as Record<string, unknown>;
    expect(parsed).toEqual({ ticket: "abc" });
    expect("targetSystem" in parsed).toBe(false);

    unmount();
  });

  it("includes targetSystem when present in query", async () => {
    setQuery({ ticket: "abc", targetSystem: "luliu" });
    fetchMock.mockResolvedValue(
      makeResponse({ ok: true, json: () => Promise.resolve({ redirect: "/chat" }) }),
    );

    const { unmount } = mountPage();
    await flush();

    const call = fetchMock.mock.calls[0] as [string, RequestInit];
    const parsed = JSON.parse(call[1].body as string) as Record<string, unknown>;
    expect(parsed).toEqual({ ticket: "abc", targetSystem: "luliu" });

    unmount();
  });

  it("redirects to response.redirect on success", async () => {
    setQuery({ ticket: "abc" });
    fetchMock.mockResolvedValue(
      makeResponse({
        ok: true,
        json: () => Promise.resolve({ redirect: "/custom-path" }),
      }),
    );

    const { unmount } = mountPage();
    await flush();

    expect(replaceMock).toHaveBeenCalledWith("/custom-path");
    unmount();
  });

  it("falls back to /chat when redirect missing", async () => {
    setQuery({ ticket: "abc" });
    fetchMock.mockResolvedValue(
      makeResponse({ ok: true, json: () => Promise.resolve({}) }),
    );

    const { unmount } = mountPage();
    await flush();

    expect(replaceMock).toHaveBeenCalledWith("/chat");
    unmount();
  });

  it("renders expired state on 401", async () => {
    setQuery({ ticket: "abc" });
    fetchMock.mockResolvedValue(makeResponse({ ok: false, status: 401 }));

    const { container, unmount } = mountPage();
    await flush();

    expect(container.textContent).toContain("EXPIRED_TITLE");
    expect(container.textContent).toContain("EXPIRED_DESC");
    expect(replaceMock).not.toHaveBeenCalled();
    unmount();
  });

  it("renders unavailable state on 500", async () => {
    setQuery({ ticket: "abc" });
    fetchMock.mockResolvedValue(makeResponse({ ok: false, status: 500 }));

    const { container, unmount } = mountPage();
    await flush();

    expect(container.textContent).toContain("UNAVAILABLE_TITLE");
    expect(replaceMock).not.toHaveBeenCalled();
    unmount();
  });

  it("renders network state on fetch rejection", async () => {
    setQuery({ ticket: "abc" });
    fetchMock.mockRejectedValue(new TypeError("network down"));

    const { container, unmount } = mountPage();
    await flush();

    expect(container.textContent).toContain("NETWORK_TITLE");
    expect(replaceMock).not.toHaveBeenCalled();
    unmount();
  });

  it("does not POST when ticket is missing", async () => {
    setQuery({});
    const { container, unmount } = mountPage();
    await flush();

    expect(fetchMock).not.toHaveBeenCalled();
    expect(container.textContent).toContain("INVALID_ENTRY");
    expect(replaceMock).not.toHaveBeenCalled();
    unmount();
  });

  it("posts only once under StrictMode double-invoke", async () => {
    setQuery({ ticket: "abc" });
    fetchMock.mockResolvedValue(
      makeResponse({ ok: true, json: () => Promise.resolve({ redirect: "/chat" }) }),
    );

    const { unmount } = mountPage({ strict: true });
    await flush();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    unmount();
  });

  it("sso callback is the first outbound request", async () => {
    setQuery({ ticket: "abc" });
    fetchMock.mockResolvedValue(
      makeResponse({ ok: true, json: () => Promise.resolve({ redirect: "/chat" }) }),
    );

    const { unmount } = mountPage();
    await flush();

    const call = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(call[0]).toBe("/api/sso/callback");
    unmount();
  });
});

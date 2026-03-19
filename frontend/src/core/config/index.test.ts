import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mockedEnv = vi.hoisted(() => ({
  NEXT_PUBLIC_BACKEND_BASE_URL: undefined as string | undefined,
  NEXT_PUBLIC_LANGGRAPH_BASE_URL: undefined as string | undefined,
  NEXT_PUBLIC_STATIC_WEBSITE_ONLY: undefined as string | undefined,
}));

vi.mock("@/env", () => ({
  env: mockedEnv,
}));

import { getLangGraphBaseURL } from "./index";
import { getBackendBaseURL } from "./index";

describe("getBackendBaseURL", () => {
  beforeEach(() => {
    mockedEnv.NEXT_PUBLIC_BACKEND_BASE_URL = undefined;
    mockedEnv.NEXT_PUBLIC_LANGGRAPH_BASE_URL = undefined;
    mockedEnv.NEXT_PUBLIC_STATIC_WEBSITE_ONLY = undefined;
    window.history.replaceState({}, "", "http://localhost:3000/workspace/chats/new");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the explicit env value when configured", () => {
    mockedEnv.NEXT_PUBLIC_BACKEND_BASE_URL = "http://localhost:8001";

    expect(getBackendBaseURL()).toBe("http://localhost:8001");
  });

  it("keeps same-origin mode when the app is served via the unified proxy port", () => {
    window.history.replaceState({}, "", "http://localhost:2026/workspace/chats/new");

    expect(getBackendBaseURL()).toBe("");
  });

  it("falls back to the backend port when the app is served directly on 3000", () => {
    expect(getBackendBaseURL()).toBe("http://localhost:8001");
  });
});

describe("getLangGraphBaseURL", () => {
  beforeEach(() => {
    mockedEnv.NEXT_PUBLIC_BACKEND_BASE_URL = undefined;
    mockedEnv.NEXT_PUBLIC_LANGGRAPH_BASE_URL = undefined;
    mockedEnv.NEXT_PUBLIC_STATIC_WEBSITE_ONLY = undefined;
    window.history.replaceState({}, "", "/workspace/chats/new");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns the absolute env URL unchanged", () => {
    mockedEnv.NEXT_PUBLIC_LANGGRAPH_BASE_URL = "http://localhost:2024/";

    expect(getLangGraphBaseURL()).toBe("http://localhost:2024");
  });

  it("resolves a relative env URL against the current origin", () => {
    mockedEnv.NEXT_PUBLIC_LANGGRAPH_BASE_URL = "/api/langgraph";

    expect(getLangGraphBaseURL()).toBe(
      new URL("/api/langgraph", window.location.origin).toString(),
    );
  });

  it("normalizes localhost env values without a protocol", () => {
    mockedEnv.NEXT_PUBLIC_LANGGRAPH_BASE_URL = "localhost:2024";

    expect(getLangGraphBaseURL()).toBe("http://localhost:2024");
  });

  it("falls back to the direct langgraph port when the app is served directly on 3000", () => {
    window.history.replaceState({}, "", "http://localhost:3000/workspace/chats/new");

    expect(getLangGraphBaseURL()).toBe("http://localhost:2024");
  });
});

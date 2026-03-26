import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchGovernanceHistory } from "./api";

describe("governance api", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("passes server-side resolved time filters to history requests", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      json: async () => ({
        items: [],
        total: 0,
        limit: 50,
        offset: 0,
      }),
    } as Response);

    await fetchGovernanceHistory({
      status: "resolved",
      resolvedFrom: "2026-03-21T00:00:00.000Z",
      resolvedTo: "2026-03-21T23:59:59.999Z",
      threadId: "thread-1",
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url] = fetchMock.mock.calls[0] ?? [];
    const requestUrl =
      typeof url === "string"
        ? url
        : url instanceof URL
          ? url.toString()
          : "";

    expect(requestUrl).toContain("/api/governance/history?");
    expect(requestUrl).toContain("status=resolved");
    expect(requestUrl).toContain("thread_id=thread-1");
    expect(requestUrl).toContain(
      "resolved_from=2026-03-21T00%3A00%3A00.000Z",
    );
    expect(requestUrl).toContain(
      "resolved_to=2026-03-21T23%3A59%3A59.999Z",
    );
  });
});

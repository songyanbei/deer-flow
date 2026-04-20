import { describe, expect, it } from "vitest";

import { metadata } from "./layout";

describe("SsoCallbackLayout metadata", () => {
  it("sets no-referrer referrer policy", () => {
    expect(metadata.referrer).toBe("no-referrer");
  });

  it("prevents search indexing", () => {
    expect(metadata.robots).toMatchObject({ index: false, follow: false });
  });
});

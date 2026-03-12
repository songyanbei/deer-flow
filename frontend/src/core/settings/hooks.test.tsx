import { act } from "react";
import { createRoot } from "react-dom/client";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_LOCAL_SETTINGS } from "./local";
import { useLocalSettings } from "./hooks";

const getLocalSettingsMock = vi.fn();
const saveLocalSettingsMock = vi.fn();

vi.mock("./local", async () => {
  const actual = await vi.importActual<typeof import("./local")>("./local");
  return {
    ...actual,
    getLocalSettings: () => getLocalSettingsMock(),
    saveLocalSettings: (...args: Parameters<typeof saveLocalSettingsMock>) =>
      saveLocalSettingsMock(...args),
  };
});

function Harness() {
  const [settings] = useLocalSettings();
  return <pre>{JSON.stringify(settings)}</pre>;
}

describe("useLocalSettings hydration safety", () => {
  beforeEach(() => {
    getLocalSettingsMock.mockReset();
    saveLocalSettingsMock.mockReset();
    getLocalSettingsMock.mockReturnValue({
      ...DEFAULT_LOCAL_SETTINGS,
      context: {
        ...DEFAULT_LOCAL_SETTINGS.context,
        model_name: "gpt-4.1",
        mode: "pro",
        reasoning_effort: "high",
      },
    });
  });

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("keeps SSR output on default settings", () => {
    const html = renderToString(<Harness />);

    expect(html).toContain(
      "&quot;requested_orchestration_mode&quot;:&quot;auto&quot;",
    );
    expect(html).toContain(
      "&quot;layout&quot;:{&quot;sidebar_collapsed&quot;:false}",
    );
    expect(getLocalSettingsMock).not.toHaveBeenCalled();
  });

  it("hydrates local settings after mount", () => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    act(() => {
      root.render(<Harness />);
    });

    expect(getLocalSettingsMock).toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain('"model_name":"gpt-4.1"');
    expect(container.textContent).toContain('"mode":"pro"');
    expect(container.textContent).toContain('"reasoning_effort":"high"');

    act(() => {
      root.unmount();
    });
    container.remove();
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";

import type { GovernanceItem } from "./types";
import {
  buildGovernanceResumeRequest,
  getGovernanceActionTarget,
  getGovernanceDisplaySummary,
  getGovernanceItemKind,
  pathOfGovernanceThread,
  resumeGovernanceThread,
  toGovernanceFilterEndISO,
  toGovernanceFilterStartISO,
} from "./utils";

vi.mock("@/core/config", () => ({
  getBackendBaseURL: () => "http://gateway.test",
}));

function createGovernanceItem(
  overrides: Partial<GovernanceItem> = {},
): GovernanceItem {
  return {
    governance_id: overrides.governance_id ?? "gov-1",
    thread_id: overrides.thread_id ?? "thread-1",
    run_id: overrides.run_id ?? "run-1",
    task_id: overrides.task_id ?? "task-1",
    source_agent: overrides.source_agent ?? "planner",
    hook_name: overrides.hook_name ?? "before_interrupt_emit",
    source_path: overrides.source_path ?? "workflow.router",
    risk_level: overrides.risk_level ?? "high",
    category: overrides.category ?? "approval",
    decision: overrides.decision ?? "require_intervention",
    status: overrides.status ?? "pending_intervention",
    created_at: overrides.created_at ?? "2026-03-26T09:00:00.000Z",
    action_summary: overrides.action_summary ?? "Review the next step",
    reason: overrides.reason ?? "Need approval before continuing",
    metadata: overrides.metadata ?? null,
    intervention_display: overrides.intervention_display ?? null,
    intervention_action_schema: overrides.intervention_action_schema ?? null,
    intervention_fingerprint: overrides.intervention_fingerprint ?? "fp-1",
    intervention_title: overrides.intervention_title ?? "Governance approval",
    intervention_tool_name: overrides.intervention_tool_name ?? "browser_click",
    request_id: overrides.request_id ?? "req-1",
    resolved_at: overrides.resolved_at ?? null,
    resolved_by: overrides.resolved_by ?? null,
    rule_id: overrides.rule_id ?? null,
  };
}

describe("governance utils", () => {
  it("prefers intervention display summary when available", () => {
    const item = createGovernanceItem({
      reason: "fallback reason",
      intervention_display: {
        title: "Title",
        summary: "Display summary",
      },
    });

    expect(getGovernanceDisplaySummary(item)).toBe("Display summary");
  });

  it("classifies clarification items and routes them back to the thread when no console action exists", () => {
    const item = createGovernanceItem({
      category: "interrupt_emit",
      action_summary: "Interrupt emit: clarification from planner",
      reason: null,
      intervention_action_schema: null,
    });

    expect(getGovernanceItemKind(item)).toBe("clarification");
    expect(getGovernanceActionTarget(item)).toBe("thread");
  });

  it("classifies dependency items separately from approvals", () => {
    const item = createGovernanceItem({
      category: "interrupt_emit",
      action_summary: "Interrupt emit: dependency from planner",
      reason: "Waiting for dependency",
      intervention_action_schema: null,
    });

    expect(getGovernanceItemKind(item)).toBe("dependency");
  });

  it("routes actionable pending items to the governance console", () => {
    const item = createGovernanceItem({
      intervention_action_schema: {
        actions: [
          {
            key: "approve",
            label: "Approve",
            kind: "button",
            resolution_behavior: "resume_current_task",
          },
        ],
      },
    });

    expect(getGovernanceItemKind(item)).toBe("approval");
    expect(getGovernanceActionTarget(item)).toBe("console");
  });

  it("treats resolved items as audit history", () => {
    const item = createGovernanceItem({
      status: "resolved",
      decision: "continue_after_resolution",
    });

    expect(getGovernanceActionTarget(item)).toBe("history");
  });

  it("builds agent-aware thread paths for custom agent governance items", () => {
    const item = createGovernanceItem({
      thread_id: "thread-9",
      metadata: {
        hook_metadata: {
          agent_name: "researcher",
        },
      },
    });

    expect(pathOfGovernanceThread(item)).toBe(
      "/workspace/agents/researcher/chats/thread-9",
    );
  });

  it("converts date filters to full ISO start and end timestamps", () => {
    const start = new Date(2026, 2, 21, 0, 0, 0, 0).toISOString();
    const end = new Date(2026, 2, 21, 23, 59, 59, 999).toISOString();

    expect(toGovernanceFilterStartISO("2026-03-21")).toBe(
      start,
    );
    expect(toGovernanceFilterEndISO("2026-03-21")).toBe(
      end,
    );
  });

  it("builds the Gateway governance resume body with safe app_context", () => {
    const item = createGovernanceItem({
      governance_id: "gov-7",
      thread_id: "thread-7",
      run_id: "run-7",
      task_id: "task-7",
    });

    const request = buildGovernanceResumeRequest(
      item,
      {
        mode: "pro",
        requested_orchestration_mode: "workflow",
        model_name: "gpt-5",
        reasoning_effort: "high",
      },
      "  [intervention_resolved] request_id=req-1  ",
    );

    expect(request.threadId).toBe("thread-7");
    expect(request.body).toEqual({
      message: "[intervention_resolved] request_id=req-1",
      governance_id: "gov-7",
      workflow_resume_run_id: "run-7",
      workflow_resume_task_id: "task-7",
      app_context: {
        thinking_enabled: true,
        is_plan_mode: true,
        subagent_enabled: false,
      },
    });
    // Critical: the Gateway pins ``AppRuntimeContext.extra="forbid"`` —
    // wholesale ``...settings`` smuggling of identity / routing keys would
    // 422. Only the three whitelisted flags must be present.
    const appContextKeys = Object.keys(
      request.body.app_context ?? {},
    ).sort();
    expect(appContextKeys).toEqual([
      "is_plan_mode",
      "subagent_enabled",
      "thinking_enabled",
    ]);
    // No direct streamMode / streamSubgraphs / streamResumable / config —
    // those are server-owned now.
    expect(request.body).not.toHaveProperty("streamMode");
    expect(request.body).not.toHaveProperty("streamSubgraphs");
    expect(request.body).not.toHaveProperty("streamResumable");
    expect(request.body).not.toHaveProperty("context");
  });

  it("omits workflow_resume_* hints when the governance item lacks them", () => {
    const item = createGovernanceItem({
      governance_id: "gov-8",
      thread_id: "thread-8",
      run_id: "",
      task_id: "",
    });

    const request = buildGovernanceResumeRequest(
      item,
      {
        mode: undefined,
        requested_orchestration_mode: "auto",
        model_name: undefined,
      },
      "resume",
    );

    expect(request.body).not.toHaveProperty("workflow_resume_run_id");
    expect(request.body).not.toHaveProperty("workflow_resume_task_id");
  });
});

describe("resumeGovernanceThread", () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.restoreAllMocks();
  });

  it("POSTs to the Gateway governance:resume endpoint with the trusted body", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        cancel: vi.fn().mockResolvedValue(undefined),
      },
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    const item = createGovernanceItem({
      governance_id: "gov-42",
      thread_id: "thread-9",
      run_id: "run-9",
      task_id: "task-3",
    });

    await resumeGovernanceThread(
      item,
      {
        mode: "flash",
        requested_orchestration_mode: "auto",
        model_name: undefined,
      },
      "approved",
    );

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe(
      "http://gateway.test/api/runtime/threads/thread-9/governance:resume",
    );
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(init.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      message: "approved",
      governance_id: "gov-42",
      workflow_resume_run_id: "run-9",
      workflow_resume_task_id: "task-3",
      app_context: {
        thinking_enabled: false,
        is_plan_mode: false,
        subagent_enabled: false,
      },
    });
  });

  it("throws with Gateway detail text when the endpoint returns non-2xx", async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      text: vi.fn().mockResolvedValue("already running"),
    }) as unknown as typeof fetch;

    const item = createGovernanceItem();
    await expect(
      resumeGovernanceThread(
        item,
        {
          mode: "pro",
          requested_orchestration_mode: "auto",
          model_name: undefined,
        },
        "approved",
      ),
    ).rejects.toThrow(/409.*already running/);
  });
});

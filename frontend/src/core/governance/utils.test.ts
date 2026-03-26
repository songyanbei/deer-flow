import { describe, expect, it } from "vitest";

import type { GovernanceItem } from "./types";
import {
  buildGovernanceResumeRequest,
  getGovernanceDisplaySummary,
  pathOfGovernanceThread,
  toGovernanceFilterEndISO,
  toGovernanceFilterStartISO,
} from "./utils";

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

  it("builds the resume request with workflow continuation context", () => {
    const item = createGovernanceItem({
      thread_id: "thread-7",
      run_id: "run-7",
      task_id: "task-7",
      metadata: {
        hook_metadata: {
          agent_name: "analyst",
        },
      },
    });

    const request = buildGovernanceResumeRequest(
      item,
      {
        mode: "pro",
        requested_orchestration_mode: "workflow",
        model_name: "gpt-5",
        reasoning_effort: "high",
      },
      "[intervention_resolved] request_id=req-1",
    );

    expect(request.threadId).toBe("thread-7");
    expect(request.assistantId).toBe("entry_graph");
    expect(request.payload.streamSubgraphs).toBe(false);
    expect(request.payload.context).toMatchObject({
      agent_name: "analyst",
      thread_id: "thread-7",
      workflow_clarification_resume: true,
      workflow_resume_run_id: "run-7",
      workflow_resume_task_id: "task-7",
      is_plan_mode: true,
      subagent_enabled: false,
      thinking_enabled: true,
    });
  });
});

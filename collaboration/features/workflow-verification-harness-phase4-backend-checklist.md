# Workflow Verification Harness Phase 4 Backend Checklist

- Status: `draft`
- Owner: `backend`
- Related feature:
  - [workflow-verification-harness-phase4.md](./workflow-verification-harness-phase4.md)
- Frontend impact target: `none required in this phase`

## 0. Current Architecture Analysis

### 0.1 Current Workflow Runtime Path

当前 workflow 主链路是：

1. graph 进入 `planner`
2. `planner` 负责任务分解 / 收尾 summary
3. `router` 负责领域 agent 分配
4. `executor` 负责调用 domain agent，并写回：
   - `task_pool`
   - `verified_facts`
   - `final_result`（部分特殊分支）
5. graph 根据 `execution_state` 和 `task_pool` 决定下一跳

当前 verification 的问题不在 graph 本身，而在于：

- 结果提交前没有正式 verifier gate
- `DONE` / `verified_facts` 主要依赖模型输出

### 0.2 Current Evals Baseline

当前 `backend/src/evals/` 已提供：

- case schema
- assertions
- collector
- runner
- report

并且 runner 已经：

- 跑真实 workflow graph
- 用 fixtures/stubs 替代 LLM / MCP / domain agent 外部依赖

这意味着：

- Phase 4 不需要重做评测基座
- 需要做的是把 verifier 结果纳入 runtime 和 report

### 0.3 Current Relevant Files

- `backend/src/agents/executor/executor.py`
  - task result / verified_facts / task DONE 的主要提交点
- `backend/src/agents/planner/node.py`
  - workflow-final summary / DONE 的主要提交点
- `backend/src/agents/thread_state.py`
  - `TaskStatus` / `ThreadState` 需要新增 verification 字段
- `backend/src/agents/graph.py`
  - graph 路由语义不应被本阶段重写
- `backend/src/evals/schema.py`
  - case result / suite result schema 需要感知 verifier
- `backend/src/evals/runner.py`
  - 需要把 runtime verification 结果带入 case result
- `backend/src/evals/report.py`
  - 需要把 verifier verdict / report 输出到 report

### 0.4 Current Gap Summary

当前缺口可以归纳为：

1. 缺少 `verifier registry`
2. 缺少统一 `VerificationContext / VerificationResult / VerificationReport`
3. 缺少 task-level runtime verification gate
4. 缺少 workflow-final runtime verification gate
5. 缺少 verification feedback / retry budget 的 state 表达
6. 缺少内建 meeting / contacts / hr / workflow verifier
7. 缺少 verification 结果到 eval/report 的贯通

## 1. Implementation Guardrails

- [ ] 只修改 workflow 模式相关后端代码
- [ ] 不修改 `leader` 模式
- [ ] 不修改前端文件
- [ ] 不重写 workflow graph 调度语义
- [ ] 不把真实 MCP / 真实 LLM 作为主测试门禁
- [ ] 不把 verifier 做成仅离线使用的工具
- [ ] 不在本阶段引入 Agent CRUD 的 verifier 配置化

Done when:

- verifier 改动稳定落在 verification package、planner/executor 接入点、state、eval/report

### Boundary Clarification

后端实现时，最容易误读的点有三个：

1. “接入 workflow runtime” 不等于“重做 workflow scheduler”
   - 本阶段只在 task commit 和 workflow-final commit 前增加 verification gate
   - 不是改 planner / router / executor 的任务调度结构

2. “Verification Harness” 不等于“只补 phase0 断言”
   - 本阶段必须进入 runtime
   - verifier 必须真正影响结果提交和失败处理

3. “与 Hook Harness 一致” 不等于“必须先做全域 hook framework”
   - 本阶段可先在明确接入点落 verifier
   - 但 contract 设计要为后续 hook 化留接口

## 2. Target Backend Structure

建议在现有目录下形成如下结构：

```text
backend/src/verification/
  __init__.py
  base.py
  registry.py
  runtime.py
  domains/
    __init__.py
    meeting.py
    contacts.py
    hr.py
  workflows/
    __init__.py
    default.py
  artifacts/
    __init__.py
    generic.py
```

说明：

- `base.py` 定义 verifier contract / verdict / context / report
- `registry.py` 负责注册和解析
- `runtime.py` 负责 planner / executor 侧接入辅助逻辑
- `domains/` 放 task-level verifier
- `workflows/` 放 workflow-final verifier
- `artifacts/` 放 generic artifact validator

## 3. Required Backend Deliverables

### 3.1 Verification Contract

- [ ] 新建 `backend/src/verification/base.py`
- [ ] 定义统一类型，至少包括：
  - `VerificationScope`
  - `VerificationVerdict`
  - `VerificationContext`
  - `VerificationFinding`
  - `VerificationReport`
  - `VerificationResult`

建议约束：

- `VerificationScope = task_result | workflow_result | artifact`
- `VerificationVerdict = passed | needs_replan | hard_fail`

### 3.2 Verifier Registry / Resolver

- [ ] 新建 `backend/src/verification/registry.py`
- [ ] 支持按 scope + domain / target 解析 verifier
- [ ] 支持列出当前已注册 verifier
- [ ] 支持 generic artifact validator
- [ ] workflow-final verifier 统一按 `workflow_kind` 解析，缺失时回退 `default`

registry 至少提供：

- `get_task_verifier(domain_or_agent: str | None)`
- `get_workflow_verifier(workflow_kind: str | None)`
- `get_artifact_validators(...)`
- `list_registered_verifiers()`

### 3.3 Built-In Verifiers

- [ ] 实现 `meeting` task result verifier
- [ ] 实现 `contacts` task result verifier
- [ ] 实现 `hr` task result verifier
- [ ] 实现 workflow-final verifier（用于当前 cross-domain workflow summary）
- [ ] 实现至少一个 generic artifact validator

要求：

- meeting / contacts / hr verifier 的判断依据要来自当前 task result、resolved_inputs、verified_facts、artifacts
- workflow verifier 的判断依据要来自 `task_pool`、`verified_facts`、`final_result`
- artifact validator 首版固定只做 generic（如文件存在、可读、非空）

### 3.4 Task-Level Runtime Integration

- [ ] 在 `executor.py` 的 task 成功提交流程中接入 verifier
- [ ] verifier 执行时机：
  - 在 task 标记 `DONE` 前
  - 在写入 `verified_facts` 前
- [ ] `passed` 时：
  - 正常提交 task result
  - 正常写入 `verified_facts`
- [ ] `needs_replan` 时：
  - 当前 task 不提交 `DONE`
  - 不写入 `verified_facts`
  - task 标记为 `FAILED`
  - 写入 `verification_status / verification_report`
  - 写入 planner 可消费的 `verification_feedback`
- [ ] `hard_fail` 时：
  - 当前 run 进入 `ERROR`
  - 写入 workflow-level verification failure summary

### 3.5 Workflow-Final Runtime Integration

- [ ] 在 `planner/node.py` 的 final summary / DONE 提交流程中接入 workflow verifier
- [ ] verifier 执行时机：
  - 在设置 `execution_state = DONE` 前
  - 在最终 `final_result` 成为 authoritative output 前
- [ ] `passed` 时：
  - 维持当前 DONE 流程
- [ ] `needs_replan` 时：
  - 不进入 `DONE`
  - 写入 `workflow_verification_status / workflow_verification_report`
  - 写入 `verification_feedback`
  - 增加 `verification_retry_count`
  - `execution_state` 调整为可重新进入 planner 的状态
  - 只要未超过预算，就让 planner 基于 `verification_feedback` 继续重规划
  - 不生成 synthetic remediation task
- [ ] `hard_fail` 时：
  - `execution_state = ERROR`
  - `final_result` 写为 verification failure summary

### 3.6 Retry Budget / Feedback State

- [ ] 在 `ThreadState` 中增加：
  - `verification_feedback`
  - `verification_retry_count`
  - `workflow_verification_status`
  - `workflow_verification_report`
- [ ] 在 `TaskStatus` 中增加：
  - `verification_status`
  - `verification_report`
- [ ] 明确 planner 如何消费 `verification_feedback`
- [ ] planner 直接消费 `verification_feedback`，不生成 synthetic remediation task
- [ ] 必须限制最大 verification retry 次数，避免死循环
- [ ] `verification_feedback` 采用统一 remediation contract，至少包含：
  - `source_scope`
  - `source_target`
  - `verifier_name`
  - `verdict`
  - `summary`
  - `findings`
  - `recommended_action`
  - `created_at`

建议：

- `max_verification_retries` 先放常量或 runtime config，不要求本阶段前端配置化

### 3.7 Evals / Replay / Report Integration

- [ ] 升级 `backend/src/evals/schema.py`
- [ ] `CaseRunResult` 至少新增：
  - `verification_status`
  - `verification_reports`
  - `verification_retry_count`
- [ ] 升级 `runner.py`，把 runtime verification 结果带入 case result
- [ ] 升级 `report.py`，在 markdown/json report 中输出 verifier 结果

### 3.8 Observability Integration

- [ ] 至少补齐 verification 相关日志
- [ ] 如实现成本可控，可补 verification metrics：
  - verifier pass/fail count
  - needs_replan count
  - hard_fail count

说明：

- 这不是前端 operator console
- 只要求后端可观测和测试可观察

## 4. Runtime Contract

### 4.1 Supported Scopes

Phase 4 正式支持：

- `task_result`
- `workflow_result`
- `artifact`

### 4.2 Supported Domains

当前必须覆盖：

- `meeting`
- `contacts`
- `hr`
- `workflows`（cross-domain workflow）

### 4.3 Verdict Strategy

统一采用：

- `passed`
- `needs_replan`
- `hard_fail`

### 4.4 Retry Strategy

必须有：

- 有界重试
- 明确上限
- 超限后转 `hard_fail`

### 4.5 Persistence / State Rule

约定：

- 未通过 verifier 的 task result 不得进入 `verified_facts`
- 未通过 workflow verifier 的 final summary 不得进入最终 `DONE`
- task-level `needs_replan` 时，当前 task 必须以 `FAILED` 终态结束，并交回 planner 决策下一步

### 4.6 Workflow Kind Resolution Rule

约定：

- workflow-final verifier 优先按显式 `workflow_kind` 解析
- 若当前运行态没有显式 `workflow_kind`，Phase 4 首版回退到 `default`
- 后续新增 workflow family 时，通过 registry 扩展，不在 planner 中硬编码分支

## 5. Files To Add

- [ ] `backend/src/verification/__init__.py`
- [ ] `backend/src/verification/base.py`
- [ ] `backend/src/verification/registry.py`
- [ ] `backend/src/verification/runtime.py`
- [ ] `backend/src/verification/domains/__init__.py`
- [ ] `backend/src/verification/domains/meeting.py`
- [ ] `backend/src/verification/domains/contacts.py`
- [ ] `backend/src/verification/domains/hr.py`
- [ ] `backend/src/verification/workflows/__init__.py`
- [ ] `backend/src/verification/workflows/default.py`
- [ ] `backend/src/verification/artifacts/__init__.py`
- [ ] `backend/src/verification/artifacts/generic.py`

## 6. Files To Modify

- [ ] `backend/src/agents/executor/executor.py`
- [ ] `backend/src/agents/planner/node.py`
- [ ] `backend/src/agents/thread_state.py`
- [ ] `backend/src/evals/schema.py`
- [ ] `backend/src/evals/runner.py`
- [ ] `backend/src/evals/report.py`

如需最小辅助改动，可涉及：

- [ ] `backend/src/evals/assertions.py`
- [ ] `backend/src/evals/collector.py`
- [ ] `backend/src/observability/metrics.py`

## 7. Files That Must Not Be Modified In Phase 4

- [ ] `frontend/` 全部文件
- [ ] `backend/src/agents/router/semantic_router.py` 的调度语义
- [ ] `backend/src/agents/graph.py` 的主图结构
- [ ] `leader` 模式主链路
- [ ] Agent CRUD / 前端 Agent 管理页面

## 8. Required Backend Acceptance Cases

### 8.1 Registry Layer

- [ ] task verifier 可按 domain 解析
- [ ] workflow verifier 可按 target 解析
- [ ] artifact validator 可按 artifact 情况解析
- [ ] 未配置 verifier 时有明确 fallback / no-op 行为

### 8.2 Task Verification Layer

- [ ] `meeting` task result 通过时正常写回 `verified_facts`
- [ ] `contacts` task result 通过时正常写回 `verified_facts`
- [ ] `hr` task result 通过时正常写回 `verified_facts`
- [ ] task verifier 返回 `needs_replan` 时，不写 `verified_facts`
- [ ] task verifier 返回 `needs_replan` 时，task 带 `verification_report`
- [ ] task verifier 返回 `needs_replan` 时，task 以 `FAILED` 终态结束
- [ ] task verifier 返回 `hard_fail` 时，run 进入 `ERROR`

### 8.3 Workflow-Final Verification Layer

- [ ] workflow summary verifier 通过时，run 正常进入 `DONE`
- [ ] workflow summary verifier 返回 `needs_replan` 时，不直接 `DONE`
- [ ] workflow summary verifier 返回 `needs_replan` 时，会增加 retry 计数
- [ ] workflow summary verifier 优先按 `workflow_kind` 解析，缺失时回退 `default`
- [ ] workflow summary verifier 超出预算后转为 `hard_fail`

### 8.4 Evals / Report Layer

- [ ] case result 中可看到 verification status
- [ ] report 中可看到 verifier findings / verdict
- [ ] phase0 benchmark 接入 verifier 后仍可运行

### 8.5 Workflow Integration

- [ ] 不改变 planner / router / executor 的调度结构
- [ ] 不影响 clarification 主链路
- [ ] 不影响 intervention 主链路
- [ ] 既有 happy path 在 verifier 通过时不回归

## 9. Recommended Implementation Order

1. 先定义 `verification/base.py` contract
2. 再实现 `registry.py` 和内建 verifiers
3. 再补 `ThreadState / TaskStatus` 字段
4. 再接入 `executor.py` task-level gate
5. 再接入 `planner/node.py` workflow-final gate
6. 最后升级 evals/report 和测试

原因：

- 先定 contract，避免 runtime 和 report 各写一套 verification 结构
- 先定 verdict 语义，再接 runtime，避免失败处理分歧

## 9.1 Recommended Backend Task Breakdown

建议后端按下面 6 个任务包拆分实施。

### Task Pack 1：Verification Contract / Registry

目标：

- 先把 verifier 的类型系统、verdict 语义、注册与解析入口定住

建议覆盖：

- `verification/base.py`
- `verification/registry.py`
- verifier resolver / fallback / no-op verifier

建议落点：

- `backend/src/verification/base.py`
- `backend/src/verification/registry.py`
- `backend/src/verification/__init__.py`

完成标准：

- runtime 和 eval/report 只依赖统一 contract，不再各自定义 verification 结构

### Task Pack 2：Built-In Verifiers

目标：

- 先把当前范围内必须支持的 verifier 落地

建议覆盖：

- `meeting` task verifier
- `contacts` task verifier
- `hr` task verifier
- workflow-final verifier
- generic artifact validator

建议落点：

- `backend/src/verification/domains/meeting.py`
- `backend/src/verification/domains/contacts.py`
- `backend/src/verification/domains/hr.py`
- `backend/src/verification/workflows/default.py`
- `backend/src/verification/artifacts/generic.py`

完成标准：

- 当前 `meeting / contacts / hr / workflows` 范围内已有可解析、可运行 verifier

### Task Pack 3：State / Feedback / Retry Budget

目标：

- 先把 runtime verification 需要的 state 表达补齐

建议覆盖：

- `TaskStatus` verification 字段
- `ThreadState` verification 字段
- `verification_feedback`
- `verification_retry_count`
- `max_verification_retries`

建议落点：

- `backend/src/agents/thread_state.py`
- 如需常量或轻量配置，可补：
  - `backend/src/verification/runtime.py`

完成标准：

- planner / executor / evals 都能从统一 state 字段读取 verification 信息

### Task Pack 4：Executor Task-Level Verification Gate

目标：

- 把 verifier 真正接进 task 结果提交流程

建议覆盖：

- task result 进入 verifier
- `passed` 时正常提交
- `needs_replan` 时不写 `verified_facts`
- `hard_fail` 时进入 `ERROR`

建议落点：

- `backend/src/agents/executor/executor.py`
- 辅助逻辑可放：
  - `backend/src/verification/runtime.py`

完成标准：

- 能明确证明 task result 在写入 `verified_facts` 前已过 verifier gate

### Task Pack 5：Planner Workflow-Final Verification Gate

目标：

- 把 workflow-final verifier 接进最终 summary / DONE 提交流程

建议覆盖：

- `passed` 时正常 DONE
- `needs_replan` 时写 `verification_feedback`
- planner 直接消费 `verification_feedback`
- 不生成 synthetic remediation task
- 超预算后 `hard_fail`

建议落点：

- `backend/src/agents/planner/node.py`
- 辅助逻辑可放：
  - `backend/src/verification/runtime.py`

完成标准：

- 能明确证明 workflow-final result 在进入 `DONE` 前已过 verifier gate

### Task Pack 6：Evals / Report / Observability Integration

目标：

- 把 verification 结果贯通到 replay、report 和可观测性

建议覆盖：

- `CaseRunResult` verification 字段
- runner 汇总 verifier 结果
- markdown/json report 输出 verifier 结果
- verification logs / metrics

建议落点：

- `backend/src/evals/schema.py`
- `backend/src/evals/runner.py`
- `backend/src/evals/report.py`
- `backend/src/evals/assertions.py`
- `backend/src/evals/collector.py`
- `backend/src/observability/metrics.py`

完成标准：

- verification 不只是 runtime 内部细节，而是能进入回放、报告和定位链路

## 9.2 Recommended Parallelization

如果后端有多人并行，建议这样拆：

1. 一人负责 Task Pack 1 + Task Pack 2
2. 一人负责 Task Pack 3 + Task Pack 4
3. 一人负责 Task Pack 5 + Task Pack 6

并行前提：

- Task Pack 1 要先尽快给出稳定 contract
- Task Pack 3 需要和 Task Pack 4 / 5 提前对齐 state 字段
- Task Pack 6 可以在 Task Pack 4 / 5 开始后并行补集成

## 10. Done Definition

Phase 4 后端完成，不是指“写了几个 verifier 文件”，而是指：

- verifier registry 已成为正式入口
- task-level 和 workflow-final verification 已真正进入 runtime 主链路
- verification verdict / report 已进入 state 与 report
- verification 失败有明确的 replan / hard-fail 处理边界
- meeting / contacts / hr / workflows 范围内可运行、可回归、可定位

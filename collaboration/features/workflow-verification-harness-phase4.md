# Feature: Workflow Verification Harness Phase 4

- Status: `draft`
- Owner suggestion: `backend` + `test`
- Related area: workflow runtime, planner/executor, thread state, evals, observability
- Frontend impact: `none required in this phase`

## Goal

基于当前已经完成的 Phase 0 baseline/evals 和 workflow 多智能体运行时，实现 Phase 4 的第一版 `Verification Harness`。

本阶段的目标不是继续扩 domain agent，也不是改 workflow 调度器，而是把当前“任务完成 / workflow 完成主要依赖模型自述”的状态，升级为：

1. 有正式的 `verifier registry`
2. 有清晰的 `verifier contract`
3. 有运行时接入点，能在 workflow 主链路上真正执行 verifier
4. 有结构化验证结果，能进入 state / report / replay
5. 有明确的失败处理策略，而不是验证失败后静默继续

Phase 4 完成后，需要达成以下结果：

1. domain agent 的任务结果不再直接写回 `verified_facts`，而是先经过 verifier
2. workflow 的最终 `final_result` 不再直接进入 `DONE`，而是先经过 workflow verifier
3. verification 结果有结构化状态和报告，可用于回放、定位和回归
4. 当前范围内的 meeting / contacts / hr / cross-domain workflow，都有明确 verifier 覆盖
5. phase0 的 deterministic baseline 能升级为“带 verifier 结果”的基线，而不是只看字符串断言

## Why This Needs Backend/Test Collaboration

本需求没有前端主改造，但它会直接影响：

- workflow executor 的结果提交流程
- planner 的最终收尾流程
- `ThreadState / TaskStatus` 的结构
- evals / replay / benchmark 报告
- 测试对“任务完成”的定义方式

因此必须由后端和测试共同收口。

### Backend 负责

- 建立 verifier contract / registry / resolver
- 在 workflow runtime 中接入 task-level 和 workflow-level verifier
- 设计 verification 失败后的状态推进和重规划边界
- 将 verification 结果写入 state、report 和日志

### Test 负责

- 校验 verifier registry / resolver 行为
- 校验 meeting / contacts / hr / workflow verifier 的判断结果
- 校验 verification 接入后 workflow 主链路不回归
- 校验 verification 失败时的状态、报告、回放结果是否符合约定

## Current Behavior

### Current Runtime Status

结合当前代码，现状是：

1. `backend/src/evals/` 已经存在 Phase 0 的 benchmark/eval 基础设施：
   - case schema
   - loader
   - runner
   - assertions
   - report
2. eval runner 已经跑在真实 workflow graph 上，但当前 assertions 仍主要是“离线预期断言”
3. `executor` 当前在 task 成功后会直接：
   - 把 task 标成 `DONE`
   - 把结果写进 `verified_facts`
4. `planner` 当前在 workflow 收尾时会直接：
   - 产出 `final_result`
   - 把 `execution_state` 置为 `DONE`
5. 当前还没有正式的 runtime verifier registry，也没有统一的 verification report/state 字段

这说明：

- DeerFlow 已经有 Phase 0 的 eval 基座
- 但还没有真正的 Phase 4 Verification Harness

### Current Gaps

当前缺口主要有六类：

1. **没有正式的 verifier registry**
   - 缺少统一注册、解析、分发入口

2. **没有 verifier contract**
   - 缺少统一 verdict
   - 缺少统一 report 结构
   - 缺少统一 context 输入

3. **运行时没有验证关口**
   - task result 现在直接进入 `verified_facts`
   - workflow summary 现在直接进入 `DONE`

4. **verification 失败没有正式处理语义**
   - 缺少 `needs_replan`
   - 缺少 `hard_fail`
   - 缺少 verification retry budget

5. **phase0 evals 与 runtime verification 还没有打通**
   - 现在 evals 主要做外部断言
   - verifier 本身还不是一等公民

6. **没有针对当前 domain 的内建 verifier**
   - meeting
   - contacts
   - hr
   - cross-domain workflow

## Current Architecture Analysis

本需求相关的核心后端文件如下：

- `backend/src/evals/schema.py`
  - 当前 case/result schema 基于 Phase 0 断言
- `backend/src/evals/assertions.py`
  - 当前主要是离线断言引擎
- `backend/src/evals/runner.py`
  - 当前会跑真实 workflow graph
- `backend/src/agents/executor/executor.py`
  - 当前 task result / verified_facts 的主要写回点
- `backend/src/agents/planner/node.py`
  - 当前 workflow 最终 summary / final_result / DONE 的主要写回点
- `backend/src/agents/thread_state.py`
  - 当前 `TaskStatus` / `ThreadState` 尚未包含 verification 字段
- `backend/src/agents/graph.py`
  - 当前 workflow graph 路由语义不应因本次改造被重写

现状可以概括为：

- Phase 0 的评测基础设施已经建立
- workflow 主链路已经成立
- 当前待补的是“验证成为运行时 gate”这一层能力

## In Scope

1. 建立正式的 verifier contract / registry / resolver
2. 为当前范围内的目标提供 verifier：
   - `meeting-agent`
   - `contacts-agent`
   - `hr-agent`
   - 这三者之间的 cross-domain workflow
3. 建立 workflow runtime 的两个验证关口：
   - task-level verification
   - workflow-final verification
4. 为 verification 新增结构化 state / report 字段
5. 将 Phase 0 evals/report 升级为可感知 verifier 结果
6. 补齐 unit / integration / benchmark / regression 测试

## Out Of Scope

1. `leader` 模式 verification
2. workflow planner / router / executor 的调度语义重写
3. 并行 scheduler
4. 前端 verification UI、operator console 或审批视图
5. 真实 MCP / 真实外部系统作为主门禁路径
6. 全域 Hook Harness 落地
7. Agent CRUD / 前端配置面暴露 verifier binding
8. 大规模 domain 扩容（当前只覆盖 meeting / contacts / hr / workflows）

## Frozen Decisions For Phase 4

### 1. Phase 4 只覆盖 workflow 模式

本阶段的 runtime verification 只接 workflow 主链路：

- planner
- router
- executor
- domain agent task result
- workflow final summary

不要求 `leader` 模式同步接入。

### 2. Phase 4 基于当前真实 workflow graph + deterministic fixtures

本阶段主测试路径继续沿用 Phase 0 思路：

- 跑真实 workflow graph
- 用 stub / fixture / mock 替代外部依赖
- 不把真实 MCP / 真实 LLM 作为主门禁

### 3. Phase 4 第一版采用代码侧 verifier registry，不要求 CRUD/UI 配置化

本阶段 verifier 的选择策略明确为：

- task-level verifier 按 domain / assigned_agent 解析
- workflow-final verifier 按 `workflow_kind` 解析
- artifact validator 固定先走内建 generic validator

也就是说：

- 本阶段不要求新增 `verifier_type` 配置项
- 不要求 Agent CRUD / 前端先暴露 verifier 绑定能力

补充约定：

- 若当前运行态没有显式 `workflow_kind`，Phase 4 首版统一回退到 `default`
- 后续新增不同 workflow family 时，应优先扩展 `workflow_kind -> verifier` 的映射，而不是把分支继续写死在 planner 中

### 4. Phase 4 的验证对象固定为三类

- `task_result`
- `workflow_result`
- `artifact`

本阶段不额外扩成任意 node-level verifier framework。

### 5. Phase 4 的统一 verdict 固定为三类

- `passed`
- `needs_replan`
- `hard_fail`

语义约定：

- `passed`
  - 允许继续提交结果
- `needs_replan`
  - 当前结果不提交，交回 workflow 继续修复/重规划
- `hard_fail`
  - 当前 run 进入 `ERROR`

### 6. Phase 4 的 retry / optimizer 是有边界的

本阶段只做**有预算的 verification retry / replan**，不做开放式 optimizer。

需要有：

- `verification_retry_count`
- `max_verification_retries`

当超过预算时：

- workflow 不再无限重试
- 必须转为 `hard_fail`

补充约定：

- `verification_feedback` 必须采用统一结构化 schema，而不是自由文本
- `verification_retry_count` 是 workflow 级预算计数，不绑定单一 agent

### 7. Phase 4 虽然与 Hook Harness 方向一致，但不依赖全量 Hook Framework

本阶段允许在现有明确接入点先落地 verifier：

- executor task commit 前
- planner final summary commit 前

要求：

- 接口设计要 hook-compatible
- 但不要求先把全域 hook framework 一起实现完

### 8. Workflow-final `needs_replan` 直接通过 `verification_feedback` 回到 planner

本阶段 workflow-final verifier 如果返回 `needs_replan`，固定采用以下策略：

- 不生成 synthetic remediation task
- 直接把结构化失败原因写入 `verification_feedback`
- 增加 `verification_retry_count`
- 将 `execution_state` 调整为可重新进入 planner 的状态

也就是说，本阶段 workflow-final optimizer 的输入来源就是 `verification_feedback`，不是额外的人造 task。

### 9. `verification_feedback` 使用通用 remediation contract

为保证后续多智能体框架可扩展，`verification_feedback` 不应设计成只服务当前三个 domain 的专用字段，而应采用通用 remediation contract。

建议至少包含：

- `source_scope`
  - `task_result | workflow_result | artifact`
- `source_target`
  - 例如 task_id / workflow_kind / artifact_id
- `verifier_name`
- `verdict`
- `summary`
- `findings`
  - 结构化问题列表
- `recommended_action`
  - 例如 `replan | retry_task | revise_summary | fail`
- `created_at`

说明：

- planner / executor 只依赖这份通用 contract，不依赖某个单域专用字段
- 后续如果引入更多 domain、更多 workflow family 或 verifier family，不需要改 feedback 的顶层结构

### 10. Task-Level `needs_replan` 采用统一状态推进语义

当 task-level verifier 返回 `needs_replan` 时，本阶段固定采用以下状态推进：

- 当前 task 不写入 `verified_facts`
- 当前 task 写入 `verification_status / verification_report`
- 当前 task 终态标记为 `FAILED`
- 将结构化 remediation 信息写入 `verification_feedback`
- `execution_state` 调整为可重新进入 planner 的状态

也就是说：

- task-level verifier 不会把未通过结果留在“伪完成”状态
- 也不会直接让 router/executor继续沿原路径误跑
- 下一步统一由 planner 基于 `verification_feedback` 决定是否重规划

## Contract To Confirm First

- Runtime mode:
  - `workflow only`
- Verification scopes:
  - `task_result | workflow_result | artifact`
- Supported domains:
  - `meeting | contacts | hr | workflows`
- Verdicts:
  - `passed | needs_replan | hard_fail`
- Task-level behavior:
  - verifier 通过前，不写 `verified_facts`
- Workflow-final behavior:
  - verifier 通过前，不写最终 `DONE`
- Retry policy:
  - bounded retry / replan only
- Workflow kind resolution:
  - 显式 `workflow_kind` 优先
  - 缺失时回退 `default`
- Verification feedback contract:
  - 使用统一结构化 remediation schema
- Test baseline:
  - 继续复用真实 workflow graph + fixture runtime

## Backend Changes

- 新增正式的 verification package
- 定义 verifier contract / registry / runtime context
- 在 executor 接入 task-level verification gate
- 在 planner 接入 workflow-final verification gate
- 为 `TaskStatus / ThreadState` 增加 verification 字段
- 升级 evals schema / report，让 verifier 结果进入报告

## Test Changes

- 新增 verifier registry / resolver 单元测试
- 新增 meeting / contacts / hr / workflow verifier 单元测试
- 新增 runtime verification 集成测试
- 新增 eval/report regression 测试
- 复用 phase0 benchmark 证明 verifier 接入后主链路不回归

## Risks

- 如果 verdict 语义不清楚，runtime 容易陷入“验证失败后到底重跑还是报错”的歧义
- 如果把 verifier 做成离线工具而不是 runtime gate，就达不到 Phase 4 目标
- 如果 runtime verification 改动越界到 workflow 调度，会把需求复杂度放大
- 如果没有 retry budget，很容易出现 verification replan 死循环
- 如果 report/state 没有标准化，测试和回放很难定位问题

## Acceptance Criteria

- verifier registry 已成为唯一可信的 verifier 分发入口
- task-level result 在写入 `verified_facts` 之前必须经过 verifier
- workflow-final result 在进入 `DONE` 之前必须经过 verifier
- verification verdict / report 有结构化 state 表达
- `meeting / contacts / hr / workflows` 范围内有可运行 verifier
- verification 失败有明确的 `needs_replan / hard_fail` 语义
- eval/report 能显示 verification 结果，而不是只显示传统断言结果
- 测试覆盖 verifier、runtime integration、workflow regression、eval/report regression

## Related Detailed Docs

- [workflow-verification-harness-phase4-backend-checklist.md](./workflow-verification-harness-phase4-backend-checklist.md)
- [workflow-verification-harness-phase4-test-checklist.md](./workflow-verification-harness-phase4-test-checklist.md)
- [workflow-phase0-baseline-and-metrics.md](./workflow-phase0-baseline-and-metrics.md)

## Feature Freeze Notes

为避免 Phase 4 在开发时继续发散，当前冻结结论为：

- artifact validator 首版只做 generic validator
- workflow-final `needs_replan` 直接复用 `verification_feedback`，不生成 synthetic remediation task

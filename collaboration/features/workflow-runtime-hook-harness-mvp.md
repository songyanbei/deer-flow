# Feature: Workflow Runtime Hook Harness MVP

- Status: `implemented`
- Owner suggestion: `backend` + `test`
- Related area: workflow runtime, hook harness, verification harness, governance foundation
- Frontend impact: `none required in this phase`

## Implementation Status

- Last verified: `2026-03-24`
- Delivery status: `accepted for MVP`

### Completed

1. Runtime hook contract / registry / runner have landed under `backend/src/agents/hooks/`.
2. `after_planner / after_router / after_executor / after_task_complete / before_final_result_commit` are implemented and executable.
3. Task-level verification now runs through `after_task_complete`.
4. Workflow-final verification now runs through `before_final_result_commit`.
5. Planner / router / executor now share a unified after-node hook entry via `backend/src/observability/node_wrapper.py`.
6. Default verification hooks are installed lazily for direct node calls and during graph compilation, preserving expected runtime behavior.
7. Hook execution is synchronous, ordered by `priority` then registration order, supports `short_circuit`, and fails closed on handler exceptions.
8. Hook handlers receive a state snapshot instead of mutating live graph state directly.
9. Targeted regression suites passed during verification:
   - `backend/tests/test_runtime_hooks.py`
   - `backend/tests/test_verification_runtime.py`
   - `backend/tests/test_multi_agent_core.py`
   - `backend/tests/test_executor_outcome.py`
   - `backend/tests/test_thread_state_continuation.py`

### Final Implementation Notes

- Final runner module landed as `backend/src/agents/hooks/runner.py` instead of the earlier suggested `runtime.py`.
- Default verifier hook installation is implemented in `backend/src/agents/hooks/verification_hooks.py` and invoked from:
  - `backend/src/agents/hooks/runner.py` for direct node execution
  - `backend/src/agents/graph.py` during compiled graph setup
- After-node metadata contract is implemented in `backend/src/observability/node_wrapper.py`.

## Goal

在当前 Milestone 1 已完成 `Phase 0 + Phase 1 + Phase 4` 的基础上，先落一版最小可用的 **Runtime Hook Harness MVP**，优先把 Phase 4 verifier 的运行时接入点从“散落在 planner / executor 主体逻辑中”升级为“通过统一 runtime hook 接入”。

本阶段的目标不是一次性做完整 Governance Harness，也不是直接进入并行调度，而是先完成下面三件事：

1. 建立统一、显式、可注册的 runtime hook contract
2. 让 verifier 通过 hook 接入，而不是继续直接硬编码在节点主体里
3. 在不改变现有 workflow 语义的前提下，为后续 `Phase 2` 并行调度与 `Phase 5` 治理能力打基础

本阶段完成后，系统应达到以下结果：

1. workflow runtime 具备正式的 runtime hook registry / runner / contract
2. task-level verification 通过 `after_task_complete` hook 执行
3. workflow-final verification 通过 `before_final_result_commit` hook 执行
4. planner / router / executor 具备统一的 after-node hook 接入点
5. 空 registry 下行为与当前线上行为完全一致

## Why This Needs Backend/Test Collaboration

这个需求没有前端改动，但它会同时影响：

- workflow 主链路的节点返回逻辑
- verifier 的运行时接入方式
- 中断 / 完成 / replan / hard_fail 的状态推进
- 测试对“节点行为”和“hook 行为”的断言方式

因此必须由后端和测试共同收口：

### Backend 负责

- 定义 runtime hook contract / registry / runner
- 在 planner / router / executor 上接入统一 hook 运行入口
- 把 Phase 4 verifier 迁移成 hook handler
- 确保空 hook、异常 hook、短路 hook 都有明确行为

### Test 负责

- 校验空 registry 时行为不变
- 校验 hook 调用顺序、注册优先级、短路行为
- 校验 verifier hook 化后 `passed / needs_replan / hard_fail` 语义与当前保持一致
- 校验现有 clarification / intervention / structured-output guardrail 不回归

## Current Behavior

### Current Runtime Status

结合当前代码，现状是：

1. `Phase 1` 的 build-time hooks 已完成，范围仅限：
   - `before_agent_build`
   - `after_agent_build`
   - `before_skill_resolve`
   - `before_mcp_bind`
2. workflow runtime 还没有统一的 runtime hook framework
3. `Phase 4` verifier 已经进入真实 runtime 主链路，但接入方式仍然是直接嵌入：
   - executor 内直接调用 task-level verification gate
   - planner 内直接调用 workflow-final verification gate
4. graph / router / executor / thread_state 中已经存在很多“隐式控制点”，但还没有抽成统一 hook 层

### Current Architecture Gaps

当前缺口主要有四个：

1. runtime hook 没有统一 contract，后续 verification / governance 只能继续散落接入
2. verifier 与节点主体逻辑耦合过深，后续扩展治理 hook 时容易继续复制判断分支
3. planner / router / executor 没有统一的 after-node 插桩点
4. 后续 `before_interrupt_emit / after_interrupt_resolve / before_task_pool_commit / before_verified_facts_commit` 没有承载层

## In Scope

本次 MVP 范围只做最小但完整的一层 runtime hook 基础设施，明确包括：

1. 新增正式的 runtime hook contract
2. 新增 runtime hook registry 与执行器
3. 支持以下 hook 点：
   - `after_planner`
   - `after_router`
   - `after_executor`
   - `after_task_complete`
   - `before_final_result_commit`
4. 将 Phase 4 verifier 优先迁移到：
   - `after_task_complete`
   - `before_final_result_commit`
5. 让 planner / router / executor 拥有统一的 after-node hook 接入方式
6. 增加 hook 运行日志 / span / 调试信息，便于后续治理与验收
7. 补齐与 hook 相关的单元测试、集成测试、回归测试

## Out Of Scope

本阶段明确不包含：

1. `Phase 2` 并行 task scheduler
2. persistent domain agent / domain memory
3. policy DSL / operator console / 审批队列 / 审计报表
4. 新 verifier 规则家族设计
5. 前端 UI 或协议变更
6. 全量治理 hook 一次性落地
7. `before_interrupt_emit / after_interrupt_resolve / before_task_pool_commit / before_verified_facts_commit` 的正式实现

说明：

- 这四个 hook 是下一小阶段的优先候选，但不属于本次 MVP 必须交付
- 本次只先把“统一 hook 层 + verifier hook 化”收口

## Frozen Decisions For This MVP

### 1. 先做 Runtime Hook Harness MVP，不等待完整 Governance Harness

本阶段只补“统一 runtime hook 承载层”，不要求把政策、风险、审计平台一次做完。

也就是说：

- 先解决“怎么统一挂”
- 暂不解决“治理产品形态怎么做全”

### 2. 本阶段只引入五个 hook 点

正式实现并可注册的 hook 点只有：

- `after_planner`
- `after_router`
- `after_executor`
- `after_task_complete`
- `before_final_result_commit`

其中：

- `after_planner / after_router / after_executor` 以“统一 after-node 承载层”为主
- `after_task_complete / before_final_result_commit` 为本阶段真正承载业务决策的 hook

### 3. Hook 运行模型采用“进程内、同步、按顺序、确定性执行”

本阶段不做异步 hook 队列，不做跨进程插件，不做远程扩展。

执行规则固定为：

1. 同一 hook 点可注册多个 handler
2. 按 `priority` 从小到大执行
3. 同优先级按注册顺序执行
4. 后一个 handler 能看到前一个 handler 合并后的 `proposed_update`

### 4. Hook 不直接改写全局状态，只操作节点的 `proposed_update`

hook handler 不允许直接 mutate graph checkpointer 中的全局状态。

它只能基于 `state snapshot + proposed_update` 输出：

- `continue`：继续后续 hook
- `short_circuit`：提前结束当前 hook 链，并返回更新后的结果

这样可以保证：

- 节点主体仍是状态写入的唯一所有者
- hook 行为对测试是可观察、可断言的
- reducer 语义不被隐式破坏

### 5. Phase 4 verifier 不重写，只迁移接入方式

本阶段不重写 `src.verification.runtime`、不重写 registry / verifier family。

要做的是：

- 保留现有 verifier contract
- 新增 verifier hook adapter
- 由 hook adapter 调用现有 `run_task_verification(...)` / `run_workflow_verification(...)`

### 6. 空 registry 必须保持零行为变化

如果没有注册任何 runtime hooks，planner / router / executor 的行为必须与当前版本一致。

这是一条强约束，用来保证：

- hook 基础设施可以先并入主干
- 后续逐个迁移 handler 时风险可控

### 7. Hook 异常默认 fail-closed

对于已注册的 runtime hook：

- 如果 handler 抛异常，不允许静默跳过
- 当前节点应返回明确错误态，并记录 `hook_error`

原因：

- verifier / governance 属于守门能力
- 静默 fail-open 会掩盖安全与正确性问题

空 registry 不受影响，因为没有 handler 就不会进入这条路径。

## Contract To Confirm First

### Runtime Hook Names

- `after_planner`
- `after_router`
- `after_executor`
- `after_task_complete`
- `before_final_result_commit`

### Runtime Hook Context

所有 hook handler 收到统一的 `RuntimeHookContext`，至少包含以下字段：

- `hook_name`
- `node_name`
- `run_id`
- `thread_id`
- `state`
- `proposed_update`
- `metadata`

其中：

- `state` 是只读快照，表示当前节点收到的输入 state
- `proposed_update` 是当前节点准备返回给 graph 的 update
- `metadata` 用于携带 hook 点特有的结构化上下文

### Hook-Specific Metadata

#### `after_planner`

- `planner_goal`
- `done`
- `summary`
- `task_pool_changed`

#### `after_router`

- `selected_task_id`
- `route_count`
- `execution_state`

#### `after_executor`

- `task_id`
- `assigned_agent`
- `outcome_kind`
- `used_fallback`

#### `after_task_complete`

- `task`
- `assigned_agent`
- `task_result`
- `resolved_inputs`
- `artifacts`
- `used_fallback`

#### `before_final_result_commit`

- `final_result`
- `task_pool`
- `verified_facts`
- `workflow_kind`
- `verification_retry_count`

### Hook Result Contract

每个 handler 返回统一的 `RuntimeHookResult`：

- `decision`: `continue | short_circuit`
- `update_patch`: `dict`
- `reason`: `str | None`

语义固定为：

1. `continue`
   - 将 `update_patch` 顶层浅合并到 `proposed_update`
   - 继续执行后续 hook
2. `short_circuit`
   - 将 `update_patch` 顶层浅合并到 `proposed_update`
   - 停止后续 hook
   - 立刻把合并后的结果作为节点最终返回值

### Merge Semantics

`update_patch` 的合并规则必须固定：

1. 顶层 key 浅合并
2. 相同 key 后写覆盖先写
3. `task_pool / verified_facts / intervention_cache` 仍然交给现有 reducer 处理
4. hook 不做深层 dict merge，避免隐藏语义

### Error Behavior

hook 执行报错时：

1. 记录结构化日志
2. 记录 span 属性
3. 当前节点返回 `execution_state=ERROR`
4. `final_result` 或任务错误信息中包含 `hook_error`

## Implementation Split

本次实施按两个切片拆分，但当前 feature 只要求切片 A 完成并验收。

### Slice A: Runtime Hook Core + Verifier Hook 化

这是本次必须完成的交付。

#### A1. 新增 runtime hook 基础设施

建议新增模块：

- `backend/src/agents/hooks/base.py`
- `backend/src/agents/hooks/registry.py`
- `backend/src/agents/hooks/runtime.py`
- `backend/src/agents/hooks/__init__.py`

建议职责：

- `base.py`
  - 定义 `RuntimeHookName`
  - 定义 `RuntimeHookContext`
  - 定义 `RuntimeHookResult`
  - 定义 `RuntimeHookHandler` 抽象基类
- `registry.py`
  - 提供 runtime hook registry
  - 提供 register / clear / list API
  - 负责优先级排序
- `runtime.py`
  - 提供 `run_runtime_hooks(...)`
  - 统一执行 hook、合并 patch、处理 short-circuit、处理异常

#### A2. 给 planner / router / executor 增加统一 after-node hook 入口

目标不是让 after-node hook 立刻承载复杂业务，而是先形成稳定入口。

建议接入方式：

- 通过统一 wrapper 或 node 级 helper，在节点返回 update 后执行：
  - planner -> `after_planner`
  - router -> `after_router`
  - executor -> `after_executor`

要求：

- 默认无 hook 时零行为变化
- 节点原有返回结构不变
- trace 中能看到 hook 是否执行、执行了哪些 handler、是否短路

#### A3. 将 task-level verification 迁移到 `after_task_complete`

当前 executor 的成功完成路径里，task-level verifier 是直接调用。

迁移后要求变为：

1. executor 先完成 outcome normalization / guardrail
2. 仅当 outcome 为真实 `complete` 时，构造一个“待提交”的成功 update
3. 把这个 `proposed_update` 交给 `after_task_complete`
4. verifier hook adapter 在这个 hook 中调用现有 `run_task_verification(...)`
5. 根据 verdict 返回：
   - `passed` -> 保持 `DONE` 更新
   - `needs_replan` -> 改写为当前 task `FAILED` + 写入 `verification_feedback` + `execution_state=EXECUTING_DONE`
   - `hard_fail` -> 改写为 `ERROR`

关键要求：

- `verified_facts` 只有在 verifier `passed` 时才允许进入返回 update
- `verification_status / verification_report` 仍按当前 contract 写入 task
- 行为必须与现有 Phase 4 语义等价

#### A4. 将 workflow-final verification 迁移到 `before_final_result_commit`

当前 planner 在判定 workflow 可完成后，直接调用 workflow verifier。

迁移后要求变为：

1. planner 先完成 done/summary 判定与 final result candidate 构造
2. 在真正返回 `execution_state=DONE` 之前，调用 `before_final_result_commit`
3. verifier hook adapter 在该 hook 中调用现有 `run_workflow_verification(...)`
4. 根据 verdict 返回：
   - `passed` -> 允许提交最终 `DONE`
   - `needs_replan` -> 清空 `task_pool`，写入 `verification_feedback`，回到 `QUEUED`
   - `hard_fail` -> 返回 `ERROR`

关键要求：

- `workflow_verification_status / workflow_verification_report` 语义保持不变
- `verification_retry_count` 的预算逻辑保持不变
- 不允许在 hook 化后丢失 warning findings 或 remediation feedback

#### A5. 增加默认 verifier hook adapter

建议新增模块：

- `backend/src/agents/hooks/verification_hooks.py`

建议内容：

- `TaskVerificationHook`
- `WorkflowVerificationHook`
- `install_default_runtime_hooks()` 或等价的显式注册入口

注意：

- 本阶段不要求做通用插件发现机制
- 允许先在 runtime 初始化阶段注册默认 hook handler

### Slice B: Interrupt / State Commit Hook 扩展

这部分不是本次 MVP 的必须交付，但建议在 Slice A 稳定后立即继续。

预留 hook 点：

- `before_interrupt_emit`
- `after_interrupt_resolve`
- `before_task_pool_commit`
- `before_verified_facts_commit`

这一小阶段主要服务于后续 Governance Harness，不应阻塞当前 verifier hook 化验收。

## Backend Changes

### New Modules

建议新增：

- `backend/src/agents/hooks/base.py`
- `backend/src/agents/hooks/registry.py`
- `backend/src/agents/hooks/runtime.py`
- `backend/src/agents/hooks/verification_hooks.py`
- `backend/src/agents/hooks/__init__.py`

### Files To Modify

建议修改：

- `backend/src/agents/planner/node.py`
  - 去掉直接内嵌的 workflow verifier 调用
  - 改为构造 candidate update 后交给 `before_final_result_commit`
- `backend/src/agents/router/semantic_router.py`
  - 增加 `after_router` 运行入口
- `backend/src/agents/executor/executor.py`
  - 去掉直接内嵌的 task verifier 调用
  - 在 complete 路径改为 `after_task_complete`
  - 在节点最终返回前接入 `after_executor`
- `backend/src/agents/graph.py` 或 `backend/src/observability/node_wrapper.py`
  - 统一承载 `after_planner / after_router / after_executor` 的接入

### Non-Goals During Backend Implementation

后端实现时，不要顺手扩大范围去改：

- planner / router / executor 的调度语义
- `ThreadState` reducer 设计
- intervention policy 结构
- verifier registry 规则家族

## Test Changes

### New Test Layers

至少补齐以下五层测试：

1. runtime hook contract unit tests
2. runtime hook registry / priority / short-circuit tests
3. verifier hook adapter unit tests
4. planner / executor hook integration tests
5. workflow regression tests

### Required Assertions

必须覆盖：

1. 空 registry 下行为与当前一致
2. 多 handler 顺序稳定
3. `continue` patch 会被后续 handler 看见
4. `short_circuit` 会停止后续 handler
5. hook 抛异常会进入明确错误态
6. `after_task_complete` 下：
   - `passed`
   - `needs_replan`
   - `hard_fail`
7. `before_final_result_commit` 下：
   - `passed`
   - `needs_replan`
   - `hard_fail`
8. structured-output guardrail 与 verifier hook 共存时不回归
9. clarification / intervention / resume 路径不回归

## Risks

1. 如果 hook contract 设计过宽，MVP 会变成半套插件系统，拖慢落地
2. 如果 hook result 允许深层任意 merge，会让状态语义变得不可预测
3. 如果 verifier 迁移时改动了 verdict 语义，容易把当前已通过验收的 runtime 行为打坏
4. 如果 after-node hook 和业务决策 hook 混在一起做，测试边界会模糊
5. 如果 hook 异常被静默吞掉，会让 verifier / governance 失去守门意义

## Acceptance Criteria

### Must Have

1. runtime hook contract / registry / runner 正式落地
2. `after_planner / after_router / after_executor / after_task_complete / before_final_result_commit` 可注册、可执行
3. task-level verification 已通过 `after_task_complete` 接入
4. workflow-final verification 已通过 `before_final_result_commit` 接入
5. 空 registry 下 planner / router / executor 行为与当前一致
6. 当前 Phase 4 行为语义保持不变：
   - `passed`
   - `needs_replan`
   - `hard_fail`
7. 现有 milestone 1 关键回归不过度受影响：
   - structured-output guardrail 相关测试继续通过
   - workflow core / intervention / clarification 关键回归继续通过

### Nice To Have

1. hook 运行有统一 span / log 字段
2. hook registry 支持测试内临时注册与清理
3. Slice B 的四个治理预备 hook 名称先在 contract 中预留文档位置

## Related Detailed Docs

- [workflow-runtime-hook-harness-mvp-backend-checklist.md](./workflow-runtime-hook-harness-mvp-backend-checklist.md)
- [workflow-runtime-hook-harness-mvp-test-checklist.md](./workflow-runtime-hook-harness-mvp-test-checklist.md)
- [workflow-verification-harness-phase4.md](./workflow-verification-harness-phase4.md)
- [workflow-engine-registry-phase1.md](./workflow-engine-registry-phase1.md)

## Open Questions

- runtime hook registry 的默认安装入口放在 graph build 还是 agent runtime bootstrap，更适合当前代码结构？
- `after_planner / after_router / after_executor` 是统一放到 node wrapper，还是分别在节点内调用 helper，更利于调试与测试？
- Slice B 是否紧接本 feature 作为 addendum 文档，还是单独开新 feature 文档？

# Feature: Workflow Runtime Hook Harness Slice B Interrupt & State Commit

- Status: `completed`
- Owner suggestion: `backend` + `test`
- Related area: workflow runtime, intervention lifecycle, state reducers, hook harness, governance foundation
- Frontend impact: `none required in this phase`

## Goal

在 `workflow-runtime-hook-harness-mvp` 和 Phase 4 verifier hook 稳定后，把 runtime hook harness 的下一层关键治理点补齐：

1. interrupt lifecycle hooks
   - `before_interrupt_emit`
   - `after_interrupt_resolve`
2. state commit hooks
   - `before_task_pool_commit`
   - `before_verified_facts_commit`

本阶段的目标不是继续堆 scattered 分支逻辑，而是把当前散落在 `executor / router / gateway / node_wrapper` 里的中断与状态提交落点，统一收敛到同一套 runtime hook contract 之下，为后续治理能力提供稳定接入层。

## Current Status

本 slice 已完成，当前状态如下：

1. 四个 Slice B hooks 已正式激活并可注册执行
   - `before_interrupt_emit`
   - `after_interrupt_resolve`
   - `before_task_pool_commit`
   - `before_verified_facts_commit`
2. interrupt emit 已统一接入 runtime hook contract
   - executor 四条 authoritative emit 路径统一先过 `before_interrupt_emit`
   - router 两条 interrupt 路径统一先过 `before_interrupt_emit`
   - event 发射使用 hook 之后的最终 task/update，避免 event 与 state 脱节
3. interrupt resolve 已同时覆盖 graph 与 gateway
   - router in-graph resume 统一经过 `after_interrupt_resolve`
   - gateway resolve + `update_state()` 直写路径统一经过 `after_interrupt_resolve`
   - router / executor hook 上下文均可拿到当前 state snapshot
4. state commit 已形成统一 pre-commit gate
   - `node_wrapper.py` 在 after-node hooks 之后接入 `apply_state_commit_hooks(...)`
   - gateway 在 `update_state()` 之前接入 `apply_state_commit_hooks(...)`
   - `task_pool -> verified_facts` 顺序固定，reducer 仍是唯一合并权威
5. destructive clear-all guard 已到位
   - `verified_facts={}` 默认 fail-closed
   - 仅显式传入 `allow_verified_facts_clear_all=True` 时允许通过
6. 文档、测试和关键回归已补齐
   - helper 单测、真实接入点集成测试、gateway/router/executor/node_wrapper 回归均已覆盖
   - verifier coexistence 与 multi-agent graph 关键回归已通过

## Scope

### In Scope

1. 正式启用四个 Slice B hook 名称
2. 为 interrupt emit 增加统一 hook 接入
3. 为 intervention resolve 增加统一 hook 接入
4. 为 `task_pool` 和 `verified_facts` 增加统一 state-commit hook 接入
5. 同时覆盖 graph 路径和 gateway 直写路径
6. 补齐文档、测试与关键回归

### Out Of Scope

1. policy DSL
2. operator console / approval queue / audit UI
3. frontend 协议改造
4. 并行 scheduler 或 Phase 2 调度改造
5. reducer 语义重写
6. intervention / clarification 协议重写
7. verifier family 扩展

## Design Requirements

### 1. 继续沿用现有 runtime hook 执行模型

新 hook 继承 MVP 的基础规则：

- 同步执行
- 按 `priority + insertion_order` 执行
- 支持 `continue / short_circuit`
- handler 异常时 fail-closed
- 空 registry 保持零行为变化

### 2. interrupt emit 必须在 event 发射之前经过 hook

`before_interrupt_emit` 必须早于 `_emit_task_event(...)`。

### 3. interrupt resolve 必须同时覆盖 graph 和 gateway

`after_interrupt_resolve` 既要覆盖 router in-graph resume，也要覆盖 gateway resolve 后的持久化路径。

### 4. state commit hooks 必须是最终提交前的统一 gate

`before_task_pool_commit` 和 `before_verified_facts_commit` 的职责是拦截“最终候选 update”，不是中途业务分支。

### 5. reducer 仍是唯一状态合并权威

state-commit hooks 只能 patch `proposed_update`，不能绕开 reducer 接管 `task_pool` / `verified_facts` 的合并语义。

### 6. 需要显式保护 `verified_facts={}` 的清空语义

当前 reducer 中，`verified_facts={}` 代表“清空全部 facts”，因此必须增加 destructive guard。

## Functional Result

完成后，系统具备以下能力：

1. 所有 authoritative interrupt emit 都有统一 hook 接入点
2. 所有 intervention resolve 都有统一 hook 接入点
3. `task_pool` 和 `verified_facts` 在最终提交前都有统一 pre-commit hook 接入点
4. graph 与 gateway 两条写入路径在治理语义上保持一致
5. 无注册 Slice B hooks 时，现有行为不发生变化

## Change Surface

本阶段改动面控制在以下区域：

- `backend/src/agents/hooks/`
- `backend/src/agents/executor/executor.py`
- `backend/src/agents/router/semantic_router.py`
- `backend/src/observability/node_wrapper.py`
- `backend/src/gateway/routers/interventions.py`
- `backend/docs/RUNTIME_HOOKS.md`
- 对应测试文件

不应外溢到：

- frontend
- reducer 规则本身
- Phase 2 调度逻辑
- intervention 协议定义

## Acceptance Criteria

### Must Have

1. 四个 Slice B hooks 正式启用并可注册执行
2. executor / router 的 interrupt emit 统一经过 `before_interrupt_emit`
3. gateway resolve / graph 内 resume 统一经过 `after_interrupt_resolve`
4. `task_pool` / `verified_facts` 最终提交前统一经过 state-commit hooks
5. graph 路径和 gateway 路径都被覆盖
6. 空 registry 下现有行为保持一致
7. clarification / intervention / resume / verifier 关键回归不被破坏
8. `verified_facts={}` 的清空语义有显式保护

### Nice To Have

1. hook logs / spans 补齐 `source_path` / `commit_reason`
2. `backend/docs/RUNTIME_HOOKS.md` 更新调用顺序说明
3. 为后续治理 hook 留出统一 helper，而不是继续复制接入逻辑

### Acceptance Status

- [x] 四个 Slice B hooks 已正式启用并可注册执行
- [x] executor / router 的 interrupt emit 已统一经过 `before_interrupt_emit`
- [x] gateway resolve / graph 内 resume 已统一经过 `after_interrupt_resolve`
- [x] `task_pool` / `verified_facts` 最终提交前已统一经过 state-commit hooks
- [x] graph 路径和 gateway 路径都已覆盖
- [x] 空 registry 下现有行为保持一致
- [x] clarification / intervention / resume / verifier 关键回归未破坏
- [x] `verified_facts={}` clear-all 语义已有显式保护

## Validation Summary

- 新增真实接入点集成测试：`backend/tests/test_runtime_hooks_slice_b_integration.py`
- 当前验收回归结果：
  - Slice B 定向回归：`99 passed`
  - 全量相关回归：`202 passed`

## Delivery Order

建议交付顺序：

1. `before_interrupt_emit`
2. `after_interrupt_resolve`
3. `before_task_pool_commit`
4. `before_verified_facts_commit`

## Related Docs

- [workflow-runtime-hook-harness-mvp.md](./workflow-runtime-hook-harness-mvp.md)
- [workflow-runtime-hook-harness-slice-b-interrupt-state-commit-backend-checklist.md](./workflow-runtime-hook-harness-slice-b-interrupt-state-commit-backend-checklist.md)
- [workflow-runtime-hook-harness-slice-b-interrupt-state-commit-test-checklist.md](./workflow-runtime-hook-harness-slice-b-interrupt-state-commit-test-checklist.md)
- [workflow-verification-harness-phase4.md](./workflow-verification-harness-phase4.md)
- [workflow-intervention-runtime-steady-state.md](./workflow-intervention-runtime-steady-state.md)

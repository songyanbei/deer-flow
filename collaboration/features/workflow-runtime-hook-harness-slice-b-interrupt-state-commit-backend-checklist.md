# Backend Checklist: Workflow Runtime Hook Harness Slice B Interrupt & State Commit

- Status: `completed`
- Depends on: [workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md](./workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md)
- Last updated: `2026-03-25`

## 1. Contract Activation

- [x] 激活 `RuntimeHookName.BEFORE_INTERRUPT_EMIT`
- [x] 激活 `RuntimeHookName.AFTER_INTERRUPT_RESOLVE`
- [x] 激活 `RuntimeHookName.BEFORE_TASK_POOL_COMMIT`
- [x] 激活 `RuntimeHookName.BEFORE_VERIFIED_FACTS_COMMIT`
- [x] 更新 `backend/src/agents/hooks/__init__.py` 导出
- [x] 更新 `backend/docs/RUNTIME_HOOKS.md` 的 hook 总览

## 2. Shared Lifecycle Helpers

- [x] 新增 `backend/src/agents/hooks/lifecycle.py`
- [x] 实现 `apply_before_interrupt_emit(...)`
- [x] 实现 `apply_after_interrupt_resolve(...)`
- [x] 实现 `apply_state_commit_hooks(...)`
- [x] 保持 `runner.py` 只负责通用 hook 执行，不混入 workflow-specific metadata 组装

## 3. Executor Interrupt Emit Integration

- [x] `request_intervention` 分支接入 `before_interrupt_emit`
- [x] user-owned `request_help` 分支接入 `before_interrupt_emit`
- [x] system dependency `request_help` 分支接入 `before_interrupt_emit`
- [x] `request_clarification` 分支接入 `before_interrupt_emit`
- [x] hook 执行顺序调整为“先 hook，后 event，最后 return”
- [x] 四条分支统一复用 helper，避免继续各自拼装 metadata
- [x] event 发射使用 hook 后的最终 task，避免 event / state 脱节

## 4. Router Interrupt Integration

- [x] `_interrupt_for_clarification()` 接入 `before_interrupt_emit`
- [x] `_interrupt_for_intervention()` 接入 `before_interrupt_emit`
- [x] router in-graph intervention resume 接入 `after_interrupt_resolve`
- [x] router 侧 metadata 使用稳定 `source_path`
- [x] router interrupt hooks 传入当前 state snapshot
- [x] router event 发射使用 hook 后的最终 task

## 5. Gateway Resolve Integration

- [x] intervention resolve endpoint 在 `apply_intervention_resolution()` 之后接入 `after_interrupt_resolve`
- [x] gateway resolve 在 `update_state()` 之前接入 `apply_state_commit_hooks(...)`
- [x] gateway 路径与 graph 路径复用同一套 helper
- [x] hook fail-closed 时返回明确 5xx，不做部分持久化

## 6. State Commit Integration

- [x] `node_wrapper.py` 在 after-node hooks 之后接入 `apply_state_commit_hooks(...)`
- [x] `before_task_pool_commit` 只在 update 含 `task_pool` 时触发
- [x] `before_verified_facts_commit` 只在 update 含 `verified_facts` 时触发
- [x] 固定执行顺序：先 `task_pool`，后 `verified_facts`
- [x] 保持 reducer 作为唯一合并权威

## 7. Safety Guards

- [x] 对 `verified_facts={}` 增加 clear-all guard
- [x] guard 需要显式 `allow_verified_facts_clear_all=True` 才放行
- [x] 不引入文本解析型逻辑
- [x] hook 异常进入明确错误态，不静默放过
- [x] 空 registry 保持零行为变化

## 8. Regression Guard

- [x] 不改调度语义
- [x] 不改 intervention payload schema
- [x] 不改 resume 协议
- [x] 不改 reducer contract
- [x] 不重做已完成的 Phase 4 verifier hook 集成
- [x] 不重新打开已修过的 clarification resume 逻辑

## 9. Documentation

- [x] 更新主 feature 文档状态
- [x] 更新 `backend/docs/RUNTIME_HOOKS.md`
- [x] 在文档中补充 graph 与 gateway 的接入差异
- [x] 在文档中记录 destructive commit guard 语义

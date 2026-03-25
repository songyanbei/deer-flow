# Test Checklist: Workflow Runtime Hook Harness Slice B Interrupt & State Commit

- Status: `completed`
- Depends on: [workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md](./workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md)
- Last updated: `2026-03-25`

## 1. Hook Contract Unit Tests

- [x] 四个 Slice B hook 名称可注册、可查询
- [x] 空 registry 返回原始 update
- [x] 多 handler 仍按 `priority + insertion_order` 执行
- [x] `continue` patch 会被后续 handler 看见
- [x] `short_circuit` 会停止后续 handler
- [x] hook 异常会转成明确错误

## 2. Interrupt Emit Helper Tests

- [x] `before_interrupt_emit` 覆盖 intervention emit
- [x] `before_interrupt_emit` 覆盖 clarification emit
- [x] `before_interrupt_emit` 覆盖 user-owned request_help emit
- [x] `before_interrupt_emit` 覆盖 system dependency emit
- [x] event 发射发生在 hook 之后
- [x] hook patch 能反映到最终 task/update

## 3. Interrupt Resolve Helper Tests

- [x] `after_interrupt_resolve` 覆盖 gateway resolve 路径
- [x] `after_interrupt_resolve` 覆盖 router in-graph resume 路径
- [x] 两条路径的 metadata 结构保持一致，只允许 `source_path` 等少数字段不同
- [x] payload/action_key/resolution_behavior 来自结构化 resolution，而不是消息文本

## 4. State Commit Helper Tests

- [x] update 不含 `task_pool` 时不触发 `before_task_pool_commit`
- [x] update 不含 `verified_facts` 时不触发 `before_verified_facts_commit`
- [x] 同时包含两者时，顺序固定为 `task_pool -> verified_facts`
- [x] graph node path 会进入 state-commit helper
- [x] gateway direct-write path 也会进入 state-commit helper

## 5. Destructive Guard Tests

- [x] `verified_facts={}` 且未显式允许时 fail-closed
- [x] `verified_facts={}` 且显式允许时可通过
- [x] 不会误伤正常的新增 / 覆盖 facts 提交

## 6. Regression Tests

- [x] clarification 路径不回归
- [x] intervention / resume 路径不回归
- [x] decision cache 路径不回归
- [x] verifier hooks 与 Slice B hooks 共存时不回归
- [x] task supersession / final convergence 不回归

## 7. Test Files

- [x] 更新 `backend/tests/test_runtime_hooks.py`
- [x] 新增 `backend/tests/test_runtime_hooks_slice_b.py`
- [x] 新增 `backend/tests/test_runtime_hooks_slice_b_integration.py`
- [x] 更新 `backend/tests/test_interventions_router.py`
- [x] 更新 `backend/tests/test_thread_state_continuation.py`
- [x] 更新 `backend/tests/test_intervention_clarification_resume.py`
- [x] 更新 `backend/tests/test_executor_outcome.py`
- [x] 更新 `backend/tests/test_multi_agent_core.py`
- [x] 覆盖 `backend/tests/test_multi_agent_graph.py`

## 8. Acceptance Execution

- [x] 定向 runtime hook pytest 通过
- [x] intervention router 相关 pytest 通过
- [x] clarification / resume 相关 pytest 通过
- [x] verifier coexistence 回归 pytest 通过
- [x] 关键 workflow regression pytest 通过
- [x] 当前验收回归结果已记录到主文档

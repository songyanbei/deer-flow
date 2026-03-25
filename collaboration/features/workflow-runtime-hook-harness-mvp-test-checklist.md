# Test Checklist: Workflow Runtime Hook Harness MVP

- Status: `completed with targeted regression coverage`
- Depends on: [workflow-runtime-hook-harness-mvp.md](./workflow-runtime-hook-harness-mvp.md)
- Last verified: `2026-03-24`

## 1. Hook Contract Unit Tests

- [x] 空 registry 返回原始 update
- [x] 单 handler `continue` patch 生效
- [x] 多 handler 按 `priority` 顺序执行
- [x] 同优先级按注册顺序执行
- [x] 后一个 handler 能看到前一个 handler 合并后的 `proposed_update`
- [x] `short_circuit` 后不再执行后续 handler
- [x] hook 异常返回明确错误态

## 2. After-Node Hook Integration Tests

- [x] `after_planner` 被调用一次
- [x] `after_router` 被调用一次
- [x] `after_executor` 被调用一次
- [x] after-node hook metadata 覆盖主文档 contract
- [x] 空 registry 下 planner / router / executor 返回值与改造前等价

## 3. Task Verification Hook Tests

- [x] `after_task_complete + passed`
  - task 变为 `DONE`
  - `verification_status=passed`
  - 写入 `verified_facts`
- [x] `after_task_complete + needs_replan`
  - task 变为 `FAILED`
  - `verification_status=needs_replan`
  - 写入 `verification_feedback`
  - 不写新的 `verified_facts`
- [x] `after_task_complete + hard_fail`
  - `execution_state=ERROR`
  - `workflow_verification_status=hard_fail`

## 4. Workflow Verification Hook Tests

- [x] `before_final_result_commit + passed`
  - `execution_state=DONE`
  - `workflow_verification_status=passed`
- [x] `before_final_result_commit + needs_replan`
  - `execution_state=QUEUED`
  - `task_pool=[]`
  - `workflow_verification_status=needs_replan`
  - 写入 `verification_feedback`
- [x] `before_final_result_commit + hard_fail`
  - `execution_state=ERROR`
  - `workflow_verification_status=hard_fail`

## 5. Compatibility / Regression Tests

- [x] structured-output guardrail 相关测试继续通过
- [x] workflow core 关键回归继续通过
- [x] clarification 相关路径继续通过
- [x] intervention / resume 相关路径继续通过
- [x] task supersession / final convergence 相关回归继续通过

## 6. Test Files

- [x] 新增 `backend/tests/test_runtime_hooks.py`
- [ ] 新增 `backend/tests/test_runtime_hook_verifier_integration.py`
- [x] 更新 `backend/tests/test_verification_runtime.py`
- [x] 更新 `backend/tests/test_multi_agent_core.py`
- [x] 更新 `backend/tests/test_executor_outcome.py`
- [x] 更新 `backend/tests/test_thread_state_continuation.py`

## 7. Acceptance Execution

- [x] 定向 pytest 套件通过
  - `backend/tests/test_runtime_hooks.py`
  - `backend/tests/test_verification_runtime.py`
- [x] milestone 1 关键回归套件通过
  - `backend/tests/test_multi_agent_core.py`
  - `backend/tests/test_executor_outcome.py`
  - `backend/tests/test_thread_state_continuation.py`
- [ ] 真实服务 smoke 已执行
  - 当前文档更新仅覆盖代码级与回归级验证，未补录真实服务 smoke 结果

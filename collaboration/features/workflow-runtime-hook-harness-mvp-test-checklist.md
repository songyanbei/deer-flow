# Test Checklist: Workflow Runtime Hook Harness MVP

- Status: `draft`
- Depends on: [workflow-runtime-hook-harness-mvp.md](./workflow-runtime-hook-harness-mvp.md)

## 1. Hook Contract Unit Tests

- [ ] 空 registry 返回原始 update
- [ ] 单 handler `continue` patch 生效
- [ ] 多 handler 按 `priority` 顺序执行
- [ ] 同优先级按注册顺序执行
- [ ] 后一个 handler 能看到前一个 handler 合并后的 `proposed_update`
- [ ] `short_circuit` 后不再执行后续 handler
- [ ] hook 异常返回明确错误态

## 2. After-Node Hook Integration Tests

- [ ] `after_planner` 被调用一次
- [ ] `after_router` 被调用一次
- [ ] `after_executor` 被调用一次
- [ ] 空 registry 下 planner / router / executor 返回值与改造前等价

## 3. Task Verification Hook Tests

- [ ] `after_task_complete + passed`
  - task 变为 `DONE`
  - `verification_status=passed`
  - 写入 `verified_facts`
- [ ] `after_task_complete + needs_replan`
  - task 变为 `FAILED`
  - `verification_status=needs_replan`
  - 写入 `verification_feedback`
  - 不写 `verified_facts`
- [ ] `after_task_complete + hard_fail`
  - `execution_state=ERROR`
  - `workflow_verification_status=hard_fail`

## 4. Workflow Verification Hook Tests

- [ ] `before_final_result_commit + passed`
  - `execution_state=DONE`
  - `workflow_verification_status=passed`
- [ ] `before_final_result_commit + needs_replan`
  - `execution_state=QUEUED`
  - `task_pool=[]`
  - `workflow_verification_status=needs_replan`
  - 写入 `verification_feedback`
- [ ] `before_final_result_commit + hard_fail`
  - `execution_state=ERROR`
  - `workflow_verification_status=hard_fail`

## 5. Compatibility / Regression Tests

- [ ] structured-output guardrail 相关测试继续通过
- [ ] workflow core 关键回归继续通过
- [ ] clarification 相关路径继续通过
- [ ] intervention / resume 相关路径继续通过
- [ ] task supersession / final convergence 相关回归继续通过

## 6. Suggested Test Files

- [ ] 新增 `backend/tests/test_runtime_hooks.py`
- [ ] 新增 `backend/tests/test_runtime_hook_verifier_integration.py`
- [ ] 更新 `backend/tests/test_verification_runtime.py`
- [ ] 更新 `backend/tests/test_multi_agent_core.py`
- [ ] 更新 `backend/tests/test_executor_outcome.py`
- [ ] 更新 `backend/tests/test_thread_state_continuation.py`

## 7. Acceptance Test Suggestions

- [ ] 定向 pytest 套件通过
- [ ] milestone 1 关键回归套件通过
- [ ] 至少补一轮真实服务 smoke：
  - 单域 contacts
  - 单域 hr
  - cross-domain contacts + hr
  - meeting intervention

# Backend Checklist: Workflow Runtime Hook Harness MVP

- Status: `draft`
- Depends on: [workflow-runtime-hook-harness-mvp.md](./workflow-runtime-hook-harness-mvp.md)

## 1. Hook Core

- [ ] 新增 `backend/src/agents/hooks/base.py`
- [ ] 定义 `RuntimeHookName`
- [ ] 定义 `RuntimeHookContext`
- [ ] 定义 `RuntimeHookResult`
- [ ] 定义 `RuntimeHookHandler` 抽象基类

- [ ] 新增 `backend/src/agents/hooks/registry.py`
- [ ] 支持按 hook name 注册多个 handler
- [ ] 支持按 `priority` 排序
- [ ] 支持清空 / 重置，便于测试

- [ ] 新增 `backend/src/agents/hooks/runtime.py`
- [ ] 实现 `run_runtime_hooks(...)`
- [ ] 支持 `continue` patch merge
- [ ] 支持 `short_circuit`
- [ ] 支持 hook 异常转明确错误

## 2. After-Node Hook 接入

- [ ] 选定统一接入方式：
  - node wrapper
  - 或 node 内 helper
- [ ] planner 接入 `after_planner`
- [ ] router 接入 `after_router`
- [ ] executor 接入 `after_executor`
- [ ] 空 registry 下零行为变化

## 3. Verifier Hook 化

- [ ] 新增 `backend/src/agents/hooks/verification_hooks.py`
- [ ] 实现 `TaskVerificationHook`
- [ ] 实现 `WorkflowVerificationHook`
- [ ] 保持调用现有 `src.verification.runtime` API

- [ ] 从 `executor.py` 移除直接内嵌的 task verifier 调用
- [ ] 在 complete 路径构造 candidate update
- [ ] 将 candidate update 交给 `after_task_complete`
- [ ] verifier `passed` 时保留 `DONE + verified_facts`
- [ ] verifier `needs_replan` 时返回 `FAILED + verification_feedback + EXECUTING_DONE`
- [ ] verifier `hard_fail` 时返回 `ERROR`

- [ ] 从 `planner/node.py` 移除直接内嵌的 workflow verifier 调用
- [ ] 在 final result commit 前调用 `before_final_result_commit`
- [ ] verifier `passed` 时保留 `DONE`
- [ ] verifier `needs_replan` 时返回 `QUEUED + task_pool=[] + verification_feedback`
- [ ] verifier `hard_fail` 时返回 `ERROR`

## 4. Runtime Safety

- [ ] hook 运行日志包含：
  - hook name
  - handler name
  - decision
  - 是否 short_circuit
- [ ] span 中补齐 hook 执行属性
- [ ] `update_patch` 合并规则固定为顶层浅合并
- [ ] 不允许 hook 直接写全局状态

## 5. Regression Guard

- [ ] 不改 planner / router / executor 的调度语义
- [ ] 不改 `ThreadState` reducer contract
- [ ] 不改 verifier registry / domain verifier family
- [ ] 不改 intervention / clarification 协议

## 6. Documentation

- [ ] 若实现中有 contract 微调，回填主文档
- [ ] 在文档中补最终安装入口
- [ ] 在文档中补最终文件落点

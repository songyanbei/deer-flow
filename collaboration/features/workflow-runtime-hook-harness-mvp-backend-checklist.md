# Backend Checklist: Workflow Runtime Hook Harness MVP

- Status: `completed`
- Depends on: [workflow-runtime-hook-harness-mvp.md](./workflow-runtime-hook-harness-mvp.md)
- Last verified: `2026-03-24`

## Execution Summary

- Result: `MVP backend scope completed`
- Final runner module: `backend/src/agents/hooks/runner.py`
- Default verifier install module: `backend/src/agents/hooks/verification_hooks.py`
- After-node integration module: `backend/src/observability/node_wrapper.py`

## 1. Hook Core

- [x] 新增 `backend/src/agents/hooks/base.py`
- [x] 定义 `RuntimeHookName`
- [x] 定义 `RuntimeHookContext`
- [x] 定义 `RuntimeHookResult`
- [x] 定义 `RuntimeHookHandler` 抽象基类

- [x] 新增 `backend/src/agents/hooks/registry.py`
- [x] 支持按 hook name 注册多个 handler
- [x] 支持按 `priority` 排序
- [x] 支持清空 / 重置，便于测试

- [x] 新增 runtime hook runner 模块
- [x] 实现 `run_runtime_hooks(...)`
- [x] 支持 `continue` patch merge
- [x] 支持 `short_circuit`
- [x] 支持 hook 异常转明确错误

## 2. After-Node Hook 接入

- [x] 选定统一接入方式
- [x] 使用 `node wrapper` 作为统一 after-node 入口
- [x] planner 接入 `after_planner`
- [x] router 接入 `after_router`
- [x] executor 接入 `after_executor`
- [x] 空 registry 下保持零行为变化

## 3. Verifier Hook 化

- [x] 新增 `backend/src/agents/hooks/verification_hooks.py`
- [x] 实现 `TaskVerificationHook`
- [x] 实现 `WorkflowVerificationHook`
- [x] 保持调用现有 `src.verification.runtime` API

- [x] 从 `executor.py` 移除直接内嵌的 task verifier 调用
- [x] 在 complete 路径构造 candidate update
- [x] 将 candidate update 交给 `after_task_complete`
- [x] verifier `passed` 时保留 `DONE + verified_facts`
- [x] verifier `needs_replan` 时返回 `FAILED + verification_feedback + EXECUTING_DONE`
- [x] verifier `hard_fail` 时返回 `ERROR`

- [x] 从 `planner/node.py` 移除直接内嵌的 workflow verifier 调用
- [x] 在 final result commit 前调用 `before_final_result_commit`
- [x] verifier `passed` 时保留 `DONE`
- [x] verifier `needs_replan` 时返回 `QUEUED + task_pool=[] + verification_feedback`
- [x] verifier `hard_fail` 时返回 `ERROR`

## 4. Runtime Safety

- [x] hook 运行日志包含 hook name / handler name / decision / short_circuit 信息
- [x] span 中补齐 hook 执行属性
- [x] `update_patch` 合并规则固定为顶层浅合并
- [x] 通过 state snapshot 防止 hook 直接写全局状态
- [x] 默认 verifier hook 支持 direct node 调用与 graph compile 路径

## 5. Regression Guard

- [x] 不改 planner / router / executor 的调度语义
- [x] 不改 `ThreadState` reducer contract
- [x] 不改 verifier registry / domain verifier family
- [x] 不改 intervention / clarification 协议

## 6. Documentation

- [x] 回填主文档状态与实现结果
- [x] 在文档中补最终安装入口
- [x] 在文档中补最终文件落点

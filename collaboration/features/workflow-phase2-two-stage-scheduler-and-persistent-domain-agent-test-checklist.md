# Test Checklist: Workflow Phase 2 Two-Stage Scheduler And Persistent Domain Agent

- Status: `in_progress` (`Stage 1` test acceptance closed on `2026-03-25`; `Stage 2` test plan in progress)
- Depends on: [workflow-phase2-two-stage-scheduler-and-persistent-domain-agent.md](./workflow-phase2-two-stage-scheduler-and-persistent-domain-agent.md)
- Last updated: `2026-03-25`

## Document Intent

这份文档只服务测试设计与验收，目标是让测试只看文档也能准确理解：

- 两个阶段分别要验证什么
- 关键风险点在哪里
- 回归范围是什么
- 哪些现象算通过，哪些现象算设计偏离

默认原则：

- 先完成 `Stage 1` 测试闭环
- `Stage 1` 稳定后再进入 `Stage 2`
- 不把两个阶段的测试目标混成一套模糊回归

## Stage 1: Dependency-aware Parallel Scheduler MVP

## 1. Stage Goal

验证 workflow 已从“串行执行单个任务”升级为“支持依赖感知与有限并发窗口”的调度器，并且不破坏现有 runtime 关键链路。

## 2. Test Focus

Stage 1 测试重点不是“有没有更多任务同时开始”，而是：

- 调度语义是否正确
- 依赖是否被正确尊重
- 并发下现有 runtime 关键语义是否仍成立

## 3. Required Test Layers

### 3.1 Scheduler Core Tests

- [ ] runnable 任务识别正确
- [ ] 有依赖的任务不会提前执行
- [ ] 无依赖的独立任务可以并发进入执行窗口
- [ ] 并发窗口上限正确生效
- [ ] 任务完成后，下游依赖任务可被释放

### 3.2 Router / Executor Integration Tests

- [ ] router 能识别多个可执行任务
- [ ] executor 能处理并发窗口内多个任务
- [ ] 并发下 task_pool 状态推进正确
- [ ] 并发下 graph 条件边不回归

### 3.3 Runtime Compatibility Tests

- [ ] clarification 在并发下不回归
- [ ] intervention 在并发下不回归
- [ ] resume 在并发下不回归
- [ ] helper / dependency 路径在并发下不回归
- [ ] verifier hooks 在并发下不回归
- [ ] slice-b interrupt/state-commit hooks 在并发下不回归

### 3.4 Convergence Tests

- [ ] task_pool 最终能稳定收敛
- [ ] verified_facts 最终提交正确
- [ ] 并发下不会产生不可恢复的悬挂任务
- [ ] 并发下不会出现错误的终态提前结束

### 3.5 Observability Tests

- [ ] 并发路径下事件流仍可解释
- [ ] 关键 task 事件数量、顺序和状态变化可验证
- [ ] 调度窗口行为能通过日志或事件观测到

## 4. Suggested Test Coverage

- [ ] 更新 graph 级集成测试
- [ ] 更新 router / executor 相关测试
- [ ] 更新 thread_state / reducer 相关测试
- [ ] 更新 clarification / intervention / resume 相关回归
- [ ] 补一个并发 workflow pilot 用例

## 5. Explicit Regression Scope

Stage 1 至少要覆盖以下已有高风险链路：

- [ ] clarification resume
- [ ] intervention approval / rejection / resume
- [ ] helper round-trip
- [ ] verified_facts 写入
- [ ] task supersession / final convergence
- [ ] workflow final verification

## 6. Acceptance Criteria

- [ ] 至少一条典型多任务链路能验证依赖感知并发
- [ ] clarification / intervention / resume 全部不回归
- [ ] verifier / state commit / interrupt hooks 全部不回归
- [ ] graph 与 task 终态收敛正确
- [ ] baseline / regression 可以稳定区分串行与并发行为

## Stage 2: Persistent Domain Agent Pilot

## 7. Stage Goal

验证一个 pilot domain agent 在引入有限 persistent 能力后，是否在真实 workflow 中带来可观察收益，同时不破坏现有 runtime truth source。

## 8. Test Focus

Stage 2 测试重点不是“memory 被写进去了”，而是：

- 是否真的减少重复澄清或重复 helper 往返
- 是否和 verified_facts / verifier 职责边界保持清晰
- 是否可以关闭和回退

## 9. Required Test Layers

### 9.1 Pilot Domain Behavior Tests

- [ ] pilot domain 在开启 persistent 能力后行为符合预期
- [ ] 至少一条真实 workflow 有明显改善
- [ ] 改善点可被结构化断言，而不是只凭主观观察

### 9.2 Boundary Tests

- [ ] domain memory 不会替代 verified_facts
- [ ] persistent 信息不会越权影响其他 domain
- [ ] verifier 仍然保有最终守门能力
- [ ] 关闭 persistent 开关后行为退回 Stage 1 稳定状态

### 9.3 Rollback / Isolation Tests

- [ ] pilot domain 关闭后不残留污染
- [ ] 非 pilot domain 行为不受影响
- [ ] memory 缺失或异常时系统仍能按当前稳定路径执行

## 10. Suggested Test Coverage

- [ ] 为 pilot domain 新增针对性集成测试
- [ ] 更新相关 workflow regression
- [ ] 增加开关 on/off 对照测试
- [ ] 增加 verifier coexistence 回归

## 11. Explicit Regression Scope

Stage 2 至少要确认以下不被破坏：

- [ ] current thread verified_facts 仍是结构化事实真相源
- [ ] clarification / intervention / resume 主链路不回归
- [ ] verifier 契约不回归
- [ ] 非 pilot domain 行为不回归

## 12. Acceptance Criteria

- [ ] 一个 pilot domain 的 persistent 能力被验证有效
- [ ] 至少一条真实 workflow 体现出可量化改善
- [ ] domain memory / verified_facts / verifier 职责边界清晰
- [ ] 关闭该能力后系统可稳定回退

## 13. Stage Boundary Guard

- [ ] Stage 1 未稳定，不提前验收 Stage 2
- [ ] Stage 2 测试不扩成全域 knowledge / memory 测试
- [ ] 如果 pilot domain 变化，测试计划同步回填主文档

## 14. Final Acceptance Execution

正式验收建议按阶段执行：

- [ ] Stage 1 独立验收
- [ ] Stage 1 回归稳定后，Stage 2 再独立验收
- [ ] 不将两个阶段一次性并包验收

## 14.1 Stage 1 Closure Record

## 14.2 Stage 2 Test Progress Update

- [x] Added pilot-domain prompt/runbook coverage for `meeting-agent`
- [x] Added non-pilot isolation coverage for `contacts-agent`
- [x] Added executor-context coverage for persistent domain memory injection
- [x] Added verified-success write-back coverage for persistent domain memory queueing
- [x] Added executor integration coverage for post-success Stage 2 queueing
- [x] Added queue-isolation coverage so same-thread memory updates from different logical sources do not overwrite each other
- [x] Added malformed-memory fallback coverage so invalid domain memory schema degrades safely to empty context
- [x] Added boundary coverage proving persistent write-back filters transactional meeting fields and keeps only safe reusable hints
- [x] Added regression coverage proving non-pilot domain agents keep prompt-level memory behavior
- [x] Stage 2 regression set is ready for formal code review
- [ ] Real workflow benefit comparison is still pending
- [ ] Stage 2 formal test acceptance is still pending

- [x] Stage 1 独立验收已完成
- [x] baseline / regression 已能稳定区分调度核心、并发调度、hooks 共存和 clarification resume 语义
- [x] 并发 clarification 场景已补充“每次只恢复第一个 clarification task”的测试固定项，见 `backend/tests/test_workflow_resume_concurrency.py`
- [x] 正式验收记录已沉淀到 [workflow-phase2-stage1-scheduler-acceptance-execution.md](./workflow-phase2-stage1-scheduler-acceptance-execution.md)

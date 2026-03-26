# Test Checklist: Workflow Phase 5 Two-Stage Governance Core And Operator Console

- Status: `Stage 5A validated; Stage 5B pending`
- Depends on: [workflow-phase5-two-stage-governance-core-and-operator-console.md](E:\work\deer-flow\collaboration\features\workflow-phase5-two-stage-governance-core-and-operator-console.md)
- Last updated: `2026-03-26`

## Document Intent

这份文档只服务测试设计与验收，目标是让测试明确：

- Stage 5A 和 Stage 5B 各自应该测什么
- 关键风险点在哪里
- 哪些现象算通过，哪些算设计偏离

默认原则：

- 先完成 `Stage 5A` 测试闭环
- `Stage 5A` 稳定后再做 `Stage 5B`
- 不把 governance core 和 operator console 的验收混成一套模糊回归

## Stage 5A: Governance Core

## 1. Stage Goal

验证治理内核已经成立，且不会破坏现有 intervention / clarification / resume / verification 主链路。

## 2. Test Focus

Stage 5A 测试重点不是“有没有更多日志”，而是：

- policy 决策是否一致
- allow / require_intervention / deny 是否稳定
- ledger 是否成为真实记录源
- 兼容链路是否不回归

## 3. Required Test Layers

### 3.1 Policy / Risk Evaluation Tests

- [x] risk taxonomy 判定正确
- [x] policy schema 加载与匹配正确
- [x] `allow / require_intervention / deny` 三类决策都可稳定触发

### 3.2 Hook / Middleware Integration Tests

- [x] before-tool 治理判定与 intervention middleware 正确联动
- [x] Slice B interrupt/state-commit hooks 与 governance 审计兼容
- [x] 空 policy registry 下兼容行为可验证

### 3.3 Ledger / Persistence Tests

- [x] 每次治理决策都会生成结构化记录
- [x] ledger 可关联 thread/run/task/request
- [x] resolve 后 resolved_at / resolution_action 等字段更新正确

### 3.4 Regression Tests

- [x] inline intervention card 主链路不回归
- [x] clarification / intervention / resume 不回归
- [x] workflow verifier / task verifier 不回归
- [x] scheduler / helper round-trip 不被治理逻辑意外破坏

## 4. Suggested Test Coverage

- [x] 扩展 intervention middleware / router / resolve API 测试
- [x] 扩展 runtime hooks slice-b integration 测试
- [x] 新增 governance decision / ledger 单测与集成测试

## 5. Acceptance Criteria

- [x] 至少一条真实高风险路径覆盖 `allow / require_intervention / deny`
- [x] ledger 记录字段完整且可查询
- [x] 现有 intervention / resume / verification 主链路全绿

## 5.1 Stage 5A Execution Snapshot (`2026-03-26`)

本轮实际执行结果：

- backend suite: `193 passed in 6.70s`
- frontend regression suite: `10 passed in 14.29s`

本轮新增补强点：

- [x] 覆盖 governance audit hooks 在 `runtime_hook_registry.clear()` 后的重新安装行为
- [x] 覆盖 state-commit 审计真正写入 governance ledger，而不是只存在 hook 壳子

## Stage 5B: Operator Console / Approval Ops

## 6. Stage Goal

验证 operator queue/detail/history 与 thread 内审批链路共用同一真相源，且处理结果一致。

## 7. Test Focus

Stage 5B 测试重点是：

- queue 是否真实反映 pending items
- detail 是否与 thread 内 intervention 一致
- console 操作后 thread 是否同步变化

## 8. Required Test Layers

### 8.1 Backend API Tests

- [ ] queue/list API 返回 pending items 正确
- [ ] detail API 返回字段完整
- [ ] history API 返回 resolved/rejected/failed/expired 正确

### 8.2 Frontend UI Tests

- [ ] queue 渲染正确
- [ ] detail 渲染 display / action / risk 信息正确
- [ ] approve / reject / provide_input 交互正确

### 8.3 End-to-End Consistency Tests

- [ ] thread 内产生 pending intervention
- [ ] console 能看到该 item
- [ ] console 处理后 thread 内状态同步变化
- [ ] thread 内处理后 queue/history 同步变化

### 8.4 Regression Scope

- [ ] intervention-card 不回归
- [ ] workflow task-panel / workflow-progress 不回归
- [ ] resolve + resume 行为不分叉

## 9. Suggested Test Coverage

- [ ] 新增 queue/detail/history API tests
- [ ] 新增 operator console 组件测试
- [ ] 新增一个 thread-card 与 console 双入口一致性的 e2e / integration case

## 10. Acceptance Criteria

- [ ] console 能稳定列出 pending items
- [ ] console 处理操作与 thread card 结果一致
- [ ] history 能追踪已处理项
- [ ] queue/history 不靠 message 文本拼装

## 11. Final Acceptance Execution Notes

- [x] Stage 5A 验收先于 Stage 5B
- [x] Stage 5B 通过前，必须先确认 Stage 5A governance ledger 已稳定
- [ ] 若前后端字段或状态语义不一致，应先走 handoff，再做联调验收

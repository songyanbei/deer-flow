# Frontend Checklist: Workflow Phase 5 Two-Stage Governance Core And Operator Console

- Status: `Stage 5A regression verified; Stage 5B pending`
- Depends on: [workflow-phase5-two-stage-governance-core-and-operator-console.md](E:\work\deer-flow\collaboration\features\workflow-phase5-two-stage-governance-core-and-operator-console.md)
- Last updated: `2026-03-26`

## Document Intent

这份文档只服务前端开发落地，目标是让前端明确：

- Stage 5A 前端是否需要动作
- Stage 5B operator console 应该做什么
- 哪些现有 UI 必须保留
- 哪些能力不能靠前端自行猜测

## Stage 5A: Governance Core

## 1. Frontend Role

Stage 5A 的主要交付在后端，前端的职责是：

- 不破坏现有 intervention-card 主链路
- 在必要时兼容后端新增但向后兼容的显示字段
- 不自行发明新的治理协议

## 2. In Scope

- [x] 兼容 Stage 5A 后端返回的新增治理显示字段（如有）
- [x] 保证 thread 内 intervention card 行为不回归

## 3. Out Of Scope

- [ ] 不做 operator console
- [ ] 不做审批队列页面
- [ ] 不做新的独立治理交互协议

## 4. Existing UI Boundary

当前前端已经有：

- [ ] [intervention-card.tsx](E:\work\deer-flow\frontend\src\components\workspace\messages\intervention-card.tsx)
- [ ] [task-panel.tsx](E:\work\deer-flow\frontend\src\components\workspace\task-panel.tsx)
- [ ] [threads/types.ts](E:\work\deer-flow\frontend\src\core\threads\types.ts)
- [ ] [interventions/hooks.ts](E:\work\deer-flow\frontend\src\core\interventions\hooks.ts)

Stage 5A 期间必须坚持：

- [x] 继续以 thread 内 card 为唯一前端审批入口
- [x] 不自行把 thread/task 状态再拼成第二份治理真相源

## 5. Acceptance Criteria

- [x] Stage 5A 后端接入完成后，现有 intervention card 不回归
- [x] clarification / intervention / resume 交互不回归

## 5.1 Validation Notes (`2026-03-26`)

- [x] 前端聚焦回归测试已执行通过：`10 passed`
- [x] 已验证 display-first 渲染、协议字段回退、approve/reject 后 resume 行为
- [x] Stage 5A 未新增 operator console 入口，前端边界保持不变

## Stage 5B: Operator Console / Approval Ops

## 6. Stage Goal

为 operator 新增一个 thread 外部的治理操作台，但不替代现有 thread 内审批卡片。

## 7. In Scope

- [ ] pending governance queue 页面
- [ ] governance detail 面板
- [ ] history / audit 列表
- [ ] queue -> detail -> resolve 的处理链路
- [ ] thread / run 跳转线索

## 8. Out Of Scope

- [ ] 不重写 thread 内 intervention card
- [ ] 不做 RBAC 权限体系
- [ ] 不做复杂 BI dashboard
- [ ] 不做组织级审批流编排

## 9. Target Frontend Change Surface

Stage 5B 的改动面应控制在：

- [ ] `frontend/src/components/workspace/` 现有工作台扩展位
- [ ] `frontend/src/core/interventions/`
- [ ] `frontend/src/core/threads/`
- [ ] 新增 governance queue/detail/history 相关组件与数据 hooks

不应外溢到：

- [ ] thread 提交流程协议
- [ ] message 基础协议
- [ ] 无关页面

## 10. UI Requirements

### 10.1 Approval Queue

- [ ] 列表必须能展示 pending governance items
- [ ] 至少展示：
- [ ] 风险等级
- [ ] 来源 agent
- [ ] 摘要
- [ ] 创建时间
- [ ] 当前状态

### 10.2 Detail View

- [ ] detail 必须能展示 display sections / risk tip / action summary
- [ ] detail 中必须能触发 approve / reject / provide_input
- [ ] detail 中必须保留 thread/run 跳转线索

### 10.3 History View

- [ ] history 必须能看 resolved / rejected / failed / expired
- [ ] 必须支持基础筛选：
- [ ] 状态
- [ ] 风险等级
- [ ] agent
- [ ] 时间范围

### 10.4 Consistency Rules

- [ ] console 的处理结果必须与 thread 内 card 完全一致
- [ ] 不允许 queue/history 靠解析 message 文本构造
- [ ] 所有展示都必须来自 backend governance source of truth

## 11. Acceptance Criteria

- [ ] operator 可在 thread 外看到 pending items
- [ ] operator 可完成 approve / reject / provide_input
- [ ] operator 处理后，thread 内状态同步一致
- [ ] inline card 与 console 不出现双写分叉

## 12. Handoff Rule

如果 Stage 5B 开发时发现后端缺：

- queue/detail/history 字段
- 稳定的状态枚举
- thread/run 跳转标识
- action availability 判断

不要在前端自行猜测，应记录到：

- [backend-to-frontend.md](E:\work\deer-flow\collaboration\handoffs\backend-to-frontend.md)
- [frontend-to-backend.md](E:\work\deer-flow\collaboration\handoffs\frontend-to-backend.md)

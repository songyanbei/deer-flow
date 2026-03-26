# Backend Checklist: Workflow Phase 5 Two-Stage Governance Core And Operator Console

- Status: `Stage 5A complete and regression-verified; Stage 5B pending`
- Depends on: [workflow-phase5-two-stage-governance-core-and-operator-console.md](E:\work\deer-flow\collaboration\features\workflow-phase5-two-stage-governance-core-and-operator-console.md)
- Last updated: `2026-03-26`

## Document Intent

这份文档只服务后端开发落地，目标是让后端只看文档也能明确：

- Stage 5A 和 Stage 5B 各自要交付什么
- 哪些模块允许改
- 哪些模块不应该动
- 每一阶段做到什么算完成

默认原则：

- 先完成 `Stage 5A`
- `Stage 5A` 稳定后再启动 `Stage 5B`
- 不把治理内核、operator console、权限系统混做成一个大需求

## Stage 5A: Governance Core

## 1. Stage Goal

把当前 intervention + runtime hooks 升级成统一治理内核，使治理决策不再分散在 middleware / executor / router / gateway 的局部分支里。

核心交付是：

- risk taxonomy
- policy schema / registry
- governance decision engine
- governance ledger

## 2. Current Backend Baseline

当前后端已具备的基础：

- runtime hook runner 已成立
- Slice B interrupt / state-commit hooks 已就位
- intervention middleware 能做 before-tool 拦截
- gateway resolve 能持久化 resolution 并恢复 thread
- thread/task state 已能承载 intervention request / resolution

当前后端还没有：

- 统一 policy schema
- 统一治理决策结果模型
- 统一 governance ledger
- queue / history query API

## 3. In Scope

- [x] 建立最小 risk taxonomy
- [x] 建立最小 policy schema / registry
- [x] 统一治理决策执行层
- [x] 在现有 hook / intervention 接入层上接治理内核
- [x] 建立 governance ledger
- [x] 保持现有 inline intervention 流兼容

## 4. Out Of Scope

- [ ] 不做 operator console UI
- [ ] 不做 RBAC / 组织权限系统
- [ ] 不重写 intervention 协议
- [ ] 不重写 verifier family
- [ ] 不扩 Phase 3 / Phase 6

## 5. Target Backend Change Surface

Stage 5A 的主要改动面应控制在：

- [ ] [hooks](E:\work\deer-flow\backend\src\agents\hooks)
- [ ] [intervention](E:\work\deer-flow\backend\src\agents\intervention)
- [ ] [intervention_middleware.py](E:\work\deer-flow\backend\src\agents\middlewares\intervention_middleware.py)
- [ ] [executor](E:\work\deer-flow\backend\src\agents\executor)
- [ ] [router](E:\work\deer-flow\backend\src\agents\router)
- [ ] [interventions.py](E:\work\deer-flow\backend\src\gateway\routers\interventions.py)
- [ ] [thread_state.py](E:\work\deer-flow\backend\src\agents\thread_state.py)
- [ ] [RUNTIME_HOOKS.md](E:\work\deer-flow\backend\docs\RUNTIME_HOOKS.md)

不应外溢到：

- [ ] frontend 页面实现
- [ ] agent prompt 大改
- [ ] workflow scheduler
- [ ] intervention payload 重构

## 6. Implementation Requirements

### 6.1 Risk Taxonomy

- [x] 风险等级必须结构化定义，不能散落在文案或 prompt 中
- [x] 统一支持 `medium / high / critical`
- [x] 风险等级必须可被日志、ledger、查询 API 复用

### 6.2 Policy Schema / Registry

- [x] policy schema 至少能表达：
- [x] 适用范围：tool / agent / category / source_path
- [x] 风险等级
- [x] 决策模式：`allow / require_intervention / deny`
- [x] 人类可读 reason 或 display override
- [x] registry 为空时尽量保持兼容行为

### 6.3 Governance Decision Engine

- [x] 治理决策必须从统一入口执行
- [x] 至少覆盖 `before_tool` 治理判定
- [x] Slice B interrupt / resolve / state-commit 路径必须能注入治理审计上下文
- [x] 不允许多个调用点各自拼装不同风格的 allow/deny 语义

### 6.4 Governance Ledger

- [x] 每次治理决策都必须生成结构化记录
- [x] ledger 必须可关联：
- [x] `thread_id`
- [x] `run_id`
- [x] `task_id`
- [x] `request_id`
- [x] `source_agent`
- [x] `decision`
- [x] `risk_level`
- [x] `created_at / resolved_at`
- [x] ledger 必须能成为 Stage 5B queue/history 的真相源

### 6.5 Backward Compatibility

- [x] 现有 thread 内 intervention card 不回归
- [x] 现有 resolve / resume 主链路不回归
- [x] clarification / intervention / workflow verification 主链路不回归

## 7. Acceptance Criteria

- [x] 至少一类高风险动作能被统一 policy 控制
- [x] `allow / require_intervention / deny` 都能被稳定观测与记录
- [x] governance ledger 可按 request/thread/run 追踪
- [x] inline intervention 体验不回归
- [x] 空 policy registry 下兼容行为可解释

## 7.1 Validation Notes (`2026-03-26`)

- [x] governance audit hooks 已支持在 `runtime_hook_registry.clear()` 后重新安装
- [x] `before_task_pool_commit / before_verified_facts_commit` 已接入 governance audit ledger
- [x] 后端聚焦测试已执行通过：`193 passed`

## Stage 5B: Operator Console / Approval Ops

## 8. Stage Goal

在 Stage 5A governance core 基础上，提供 queue / detail / history 查询与处理 API，支撑 operator console。

核心交付是：

- pending queue query
- governance detail query
- history query
- operator action entry

## 9. In Scope

- [ ] queue/list API
- [ ] detail API
- [ ] history API
- [ ] operator resolution backend entry
- [ ] 与现有 intervention resolution 统一 source of truth

## 10. Out Of Scope

- [ ] 不做前端具体页面布局
- [ ] 不做 RBAC 系统
- [ ] 不做复杂 BI dashboard
- [ ] 不做审批 SLA 自动流转

## 11. Target Backend Change Surface

Stage 5B 的主要改动面应控制在：

- [ ] [gateway/routers](E:\work\deer-flow\backend\src\gateway\routers)
- [ ] governance ledger 对应 persistence / query 层
- [ ] [thread_state.py](E:\work\deer-flow\backend\src\agents\thread_state.py)（若需要新增稳定索引字段）
- [ ] 对应 API / integration tests

不应外溢到：

- [ ] intervention resolution contract 重写
- [ ] workflow 调度逻辑
- [ ] policy schema 大改

## 12. Implementation Requirements

### 12.1 Queue API

- [ ] 能列出所有 pending governance items
- [ ] 支持按状态 / 风险等级 / agent / 时间筛选
- [ ] 返回字段必须足够驱动前端列表，不要求前端再拼 message 文本

### 12.2 Detail API

- [ ] 能返回单条 governance item 的完整详情
- [ ] 必须包含 display/action 信息、来源上下文和处理历史

### 12.3 History API

- [ ] 能查询 resolved / rejected / failed / expired items
- [ ] 历史记录和 pending queue 必须来自同一 ledger

### 12.4 Operator Action API

- [ ] operator action 必须复用现有 resolution contract 或其薄封装
- [ ] 不得另造平行审批协议
- [ ] operator action 后 thread 状态必须和 inline card 行为一致

## 13. Acceptance Criteria

- [ ] backend 已能支撑 queue/detail/history 三类视图
- [ ] operator action 能稳定驱动 workflow 恢复或失败
- [ ] console 与 thread 卡片无状态分叉
- [ ] queue/history 不依赖解析 thread message 文本

## 14. Stage Boundary Guard

- [x] Stage 5A 完成前，不启动 Stage 5B UI
- [ ] Stage 5B 不反向推翻 Stage 5A 的 policy / ledger contract

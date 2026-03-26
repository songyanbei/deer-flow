# Feature: Workflow Phase 5 Two-Stage Governance Core And Operator Console

- Status: `Stage 5A backend + regression complete; Stage 5B pending`
- Owner suggestion: `backend` + `frontend` + `test`
- Related area: workflow runtime, intervention lifecycle, runtime hooks, policy enforcement, audit, approval ops
- Frontend impact: `none required in Stage 5A`, `required in Stage 5B`

## Goal

在以下能力已经稳定之后，正式启动 `Phase 5 Governance Harness`：

- `Phase 1` engine / build-time hooks
- `Phase 4` verifier runtime hooks
- `workflow-runtime-hook-harness-slice-b-interrupt-state-commit`
- `Phase 2A` dependency-aware parallel scheduler
- `Phase 2B` persistent domain agent pilot

本阶段不再把治理逻辑继续散落在 middleware、executor、router 和 UI 分支里，而是拆成两个连续阶段：

1. `Stage 5A: Governance Core`
2. `Stage 5B: Operator Console / Approval Ops`

这样拆分的目标是：

- 先把治理内核做成统一、可审计、可复用的 runtime capability
- 再把已有 thread 内审批能力升级成 operator 可见、可处理、可追踪的操作台

## Why This Needs To Be The Next Work

结合当前代码现状，系统已经具备明显的治理基础，但还没有真正形成统一治理层：

- runtime hook contract 已存在，且 Slice B 已覆盖 interrupt / state commit 生命周期。[RUNTIME_HOOKS.md](E:\work\deer-flow\backend\docs\RUNTIME_HOOKS.md)
- intervention middleware 已能在工具调用前拦截高风险操作。[intervention_middleware.py](E:\work\deer-flow\backend\src\agents\middlewares\intervention_middleware.py)
- intervention resolve API 已能持久化 resolution 并恢复 workflow。[interventions.py](E:\work\deer-flow\backend\src\gateway\routers\interventions.py)
- thread/task state 已有 `intervention_request` / `intervention_resolution` / `pending_interrupt` 等结构化字段。[types.ts](E:\work\deer-flow\frontend\src\core\threads\types.ts)
- 前端 thread 内已有 `intervention-card`，说明基础审批交互链路已成立。[intervention-card.tsx](E:\work\deer-flow\frontend\src\components\workspace\messages\intervention-card.tsx)

但当前仍有 4 个明显缺口：

1. 缺统一风险分级与策略模型
2. 缺统一治理决策记录，无法把“为什么放行 / 拒绝 / 需要审批”沉淀成稳定审计事实
3. 缺跨入口一致的治理 source of truth
4. 缺 operator 视角的审批队列、审计查询和处理台

因此，`Phase 5` 的下一步不应该是继续补 scattered if/else，也不应该直接跳去做大而全的知识系统，而是先补齐治理内核，再做操作台。

## Current Status

## Implementation Snapshot (`2026-03-26`)

当前真实进展已经不是纯 `draft`：

- `Stage 5A backend` 已落地
- `Stage 5A regression / test` 已完成一轮闭环验证
- `Stage 5B operator console` 仍未开始正式实现与验收

本轮已经确认落地的 `Stage 5A` 能力包括：

1. **已实现 governance core 模块**
   - `backend/src/agents/governance/types.py`
   - `backend/src/agents/governance/policy.py`
   - `backend/src/agents/governance/engine.py`
   - `backend/src/agents/governance/ledger.py`
   - `backend/src/agents/governance/audit_hooks.py`

2. **已把治理决策接入现有运行链路**
   - `before_tool` 通过 `intervention_middleware` 接入统一治理入口
   - `before_interrupt_emit / after_interrupt_resolve` 已接入治理审计
   - `before_task_pool_commit / before_verified_facts_commit` 已接入治理审计

3. **已验证兼容链路未回归**
   - inline intervention card
   - resolve / resume
   - clarification
   - runtime hooks / verifier

4. **本轮测试中补齐的关键边界**
   - governance audit hooks 在 `runtime_hook_registry.clear()` 后可重新安装
   - state-commit hooks 不只是存在，而且会真实写入 governance ledger

因此，当前阶段判断应更新为：

- `Stage 5A`: implementation-complete + regression-verified
- `Stage 5B`: not started
当前系统已经具备以下治理基础：

1. **已有 runtime hook harness**
   - `before_interrupt_emit`
   - `after_interrupt_resolve`
   - `before_task_pool_commit`
   - `before_verified_facts_commit`

2. **已有 intervention protocol**
   - 结构化 `action_schema`
   - 结构化 `display`
   - fingerprint / request_id / resolution payload

3. **已有 thread 内审批入口**
   - thread message 中可直接 approve / reject / provide_input
   - resolve 后可触发 resume

4. **已有 task / workflow observability**
   - run_id / thread_id / task_id / route_count / workflow_stage
   - after-task / before-final-result verification hooks

当前系统仍然缺少的部分应更新为：

1. **Stage 5A 已补齐**
   - policy schema / risk taxonomy
   - governance decision engine
   - governance decision ledger
   - hook / middleware / gateway 接入
2. **当前仍缺**
   - queue/history query API
   - operator-facing approval UI
   - “inline card 与 operator console 共用同一真相源”的 Stage 5B 交付

## Two-Stage Plan

## Stage 5A: Governance Core

### Stage Goal

把当前 intervention + hook 基础设施升级成统一治理内核，使系统能稳定回答以下问题：

`这次动作为什么被允许、被拒绝，或者被要求人工确认？`

这一阶段的核心交付是：

- 风险分级模型
- 最小可用 policy schema
- 统一治理决策执行层
- 审计/决策记录真相源

不是：

- operator console
- 审批后台 UI
- 权限系统重构

### In Scope

1. 建立最小可用 risk taxonomy
2. 建立最小可用 policy schema / policy registry
3. 把治理决策统一挂到现有 runtime hook / intervention 接入层
4. 统一治理决策结果模型
5. 建立 governance audit / decision ledger
6. 保证 inline intervention card 继续可用，且不改坏现有 thread 内审批流

### Out Of Scope

1. operator console 页面
2. 审批队列前端
3. 组织级 RBAC / 用户权限系统
4. 全量 policy DSL 语言化设计
5. intervention 协议重写
6. verifier family 重写
7. domain agent prompt 大改

### Functional Requirements

1. **Risk Taxonomy**
   - 系统必须为治理相关动作定义稳定风险等级，至少覆盖：
   - `medium`
   - `high`
   - `critical`
   - 风险等级必须来自结构化治理规则，不得依赖 UI 文案或 prompt 文本猜测

2. **Policy Schema**
   - 系统必须提供最小可用的结构化 policy schema，至少能表达：
   - 适用范围：tool / agent / category / source_path
   - 风险等级
   - 决策模式：`allow` / `require_intervention` / `deny`
   - 可选的人类可读 reason / display overrides
   - schema 必须可在后端稳定加载与测试，不要求一开始做成复杂 DSL 解释器

3. **Governance Decision Engine**
   - 系统必须在统一治理入口上做决策，而不是在多个调用点复制判断逻辑
   - 至少覆盖以下治理入口：
   - `before_tool`
   - `before_interrupt_emit`
   - `after_interrupt_resolve`
   - 以及一条统一的 state-commit 侧审计接入

4. **Decision Outcomes**
   - 治理决策结果必须统一成有限集合：
   - `allow`
   - `require_intervention`
   - `deny`
   - `continue_after_resolution`
   - 不允许不同调用点返回风格各异的 ad-hoc 布尔语义

5. **Governance Ledger**
   - 每一次治理决策都必须沉淀为结构化记录，至少包含：
   - `governance_id`
   - `thread_id`
   - `run_id`
   - `task_id`
   - `source_agent`
   - `hook_name` / `source_path`
   - `risk_level`
   - `category`
   - `decision`
   - `request_id`（如进入 intervention）
   - `created_at`
   - `resolved_at`（如已解决）
   - 这份 ledger 必须成为 Stage 5B operator queue/history 的后端真相源

6. **Backward Compatibility**
   - 现有 thread 内 intervention card 必须继续工作
   - 现有 resolve / resume 主链路必须继续工作
   - 若 policy registry 为空，现有行为应尽量保持兼容

### Change Surface

Stage 5A 的改动面应主要控制在：

- `backend/src/agents/hooks/`
- `backend/src/agents/intervention/`
- `backend/src/agents/middlewares/intervention_middleware.py`
- `backend/src/agents/executor/`
- `backend/src/agents/router/`
- `backend/src/gateway/routers/interventions.py`
- `backend/src/agents/thread_state.py`
- `backend/docs/RUNTIME_HOOKS.md`
- 对应后端测试

Stage 5A 不应外溢到：

- frontend 新页面
- 权限系统
- domain 配置大改
- intervention payload 协议重写

### Acceptance Criteria

1. [x] 至少一类高风险 tool action 能通过统一 policy schema 决定 `allow / require_intervention / deny`
2. [x] 治理决策可在日志与持久化记录中被稳定追踪
3. [x] 现有 inline intervention flow 不回归
4. [x] clarification / intervention / resume / verification 主链路不回归
5. [x] governance ledger 足够支撑后续 queue/history 查询，而不需要重复解析 thread message

### Validation Snapshot (`2026-03-26`)

本轮 `Stage 5A` 验收已实际执行：

- backend focused suite: `193 passed`
- frontend intervention regression suite: `10 passed`

本轮验证覆盖了：

- policy / risk evaluation
- middleware integration
- interrupt emit / resolve audit
- state-commit audit
- ledger persistence / query / resolve transition
- inline card / resolve / resume / clarification regression

## Stage 5B: Operator Console / Approval Ops

### Stage Goal

在 Stage 5A governance core 成立之后，把治理能力从“thread 内可审批”升级成“operator 可查看、可处理、可追踪”的操作台。

这一阶段要回答的问题是：

`系统外的操作人员，能否不进入具体 thread 也看见待处理审批，并完成处理与审计追踪？`

### In Scope

1. pending governance items 的列表 / 队列视图
2. governance item 详情视图
3. operator 处理 pending intervention 的能力
4. resolved / rejected / expired item 的历史记录视图
5. 基础筛选 / 搜索 / 状态过滤
6. 与 thread 内 intervention card 使用同一后端真相源

### Out Of Scope

1. 复杂运营 BI 大盘
2. 组织权限系统 / 多角色审批流
3. SLA 自动升级 / 自动派单
4. 复杂统计报表中心
5. 全局治理配置后台

### Functional Requirements

1. **Approval Queue**
   - operator 必须能看到当前所有 pending governance items
   - 列表至少展示：
   - `request_id`
   - `thread_id`
   - `run_id`
   - `task_id`
   - `source_agent`
   - `risk_level`
   - `category`
   - `summary`
   - `created_at`

2. **Decision Detail**
   - operator 必须能查看单条 governance item 详情
   - 详情至少包含：
   - 原始 action summary
   - display sections / risk tip
   - tool name / source agent / source task
   - resolution history
   - 对应 thread/run 跳转线索

3. **Act On Pending Items**
   - operator 必须能 approve / reject / provide_input
   - operator 操作必须复用同一套后端 resolution contract，不得再做一条平行审批协议
   - operator 在 console 中完成处理后，thread 内状态必须同步反映

4. **History / Audit View**
   - operator 必须能查看已完成的治理记录
   - 历史记录至少支持按：
   - 状态
   - 风险等级
   - agent
   - 时间范围
   进行筛选

5. **UI Boundary**
   - thread 内 intervention card 继续保留
   - operator console 是新增入口，不替换现有 thread 内处理入口
   - 两个入口必须共用同一份 backend governance ledger

### Change Surface

Stage 5B 的改动面应主要控制在：

- `frontend/src/components/workspace/`
- `frontend/src/core/interventions/`
- `frontend/src/core/threads/`
- 新增或扩展的 governance queue/history 前端模块
- 配套的 backend query/list/detail API
- 对应前后端测试

Stage 5B 不应外溢到：

- intervention resolution 协议重写
- workflow 调度逻辑
- RBAC 系统重构
- Knowledge Harness

### Acceptance Criteria

1. operator 可在 thread 之外看到 pending governance items
2. operator 可在 queue/detail 中完成 approve / reject / provide_input
3. operator 操作后，thread 状态与 workflow resume 行为保持一致
4. queue 与 history 基于同一 governance ledger，不靠 message 文本拼装
5. thread 内 card 与 operator console 不出现状态分叉

## Stage Boundary Rules

为避免范围失控，这两个阶段必须严格拆开：

### Stage 5A 只回答一个问题

`治理内核是否已经成立，并且所有治理决策都能统一记录、统一解释？`

不要在这一步顺手把 operator UI、审批后台和运营报表一起做掉。

### Stage 5B 只回答一个问题

`在治理内核成立之后，operator 是否能真正看见、处理、追踪治理事件？`

不要在这一步顺手把 RBAC、SLA 自动升级和复杂 BI 一起做掉。

## Recommended Order

建议按下面顺序推进：

1. Stage 5A backend 核心能力
2. Stage 5A 回归与审计记录闭环
3. Stage 5B backend queue/history query API
4. Stage 5B frontend operator console
5. Stage 5B 联调与 operator flow 验收

## Risks

1. 如果 Stage 5A 直接做成复杂 DSL，会让治理内核在第一步就过度设计
2. 如果 Stage 5A 只加日志不做 ledger，Stage 5B 会被迫重新解析 thread 状态或 message 文本
3. 如果 Stage 5B 另造一条审批协议，inline card 与 console 很容易分叉
4. 如果 Stage 5B 过早引入 RBAC / SLA / BI，会让 operator console 范围失控

## Acceptance Summary

完成这两个阶段后，平台应从当前的：

`有 intervention 能力，但治理逻辑仍然分散`

升级为：

`有统一 governance core + 有 operator 可用的 approval ops console`

这会为后续继续演进：

- 更完整的 policy DSL
- 更强的 operator metrics / audit
- `Phase 3 Knowledge Harness`
- `Phase 6 Improvement Harness`

打下更稳的基础。

## Related Docs

- [workflow-runtime-hook-harness-mvp.md](E:\work\deer-flow\collaboration\features\workflow-runtime-hook-harness-mvp.md)
- [workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md](E:\work\deer-flow\collaboration\features\workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md)
- [workflow-phase2-two-stage-scheduler-and-persistent-domain-agent.md](E:\work\deer-flow\collaboration\features\workflow-phase2-two-stage-scheduler-and-persistent-domain-agent.md)
- [workflow-phase5-two-stage-governance-core-and-operator-console-backend-checklist.md](E:\work\deer-flow\collaboration\features\workflow-phase5-two-stage-governance-core-and-operator-console-backend-checklist.md)
- [workflow-phase5-two-stage-governance-core-and-operator-console-frontend-checklist.md](E:\work\deer-flow\collaboration\features\workflow-phase5-two-stage-governance-core-and-operator-console-frontend-checklist.md)
- [workflow-phase5-two-stage-governance-core-and-operator-console-test-checklist.md](E:\work\deer-flow\collaboration\features\workflow-phase5-two-stage-governance-core-and-operator-console-test-checklist.md)

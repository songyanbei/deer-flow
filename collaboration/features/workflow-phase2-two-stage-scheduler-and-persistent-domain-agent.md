# Feature: Workflow Phase 2 Two-Stage Scheduler And Persistent Domain Agent

- Status: `completed` (`Stage 1` accepted on `2026-03-25`; `Stage 2` not started)
- Owner suggestion: `backend` + `test`
- Related area: workflow runtime, router, executor, task scheduling, domain agents, thread state
- Frontend impact: `none required in stage 1`, `none required by default in stage 2`

## Goal

在 `Phase 1`、`Phase 4 verifier hook 化`、以及 `workflow-runtime-hook-harness-slice-b-interrupt-state-commit` 基本稳定后，下一步启动 `Phase 2`，但不整包一起做，而是拆成两个连续阶段：

1. `Stage 1: Dependency-aware Parallel Scheduler MVP`
2. `Stage 2: Persistent Domain Agent Pilot`

这样拆的目标是：

- 先把 workflow 从“串行推进图”升级成“有明确调度能力的 runtime”
- 再在稳定的调度器之上，试点一个可持续积累经验的 domain agent 形态

本需求文档只定义这两个阶段的目标、边界、功能划分、改动范围和验收标准，不直接展开成实现细节 checklist。

## Why This Needs To Be The Next Work

结合当前代码，workflow 现在最明显的结构性瓶颈已经不是 verifier，也不是 interrupt / state commit hooks，而是**调度层仍然本质串行**：

- [graph.py](E:\work\deer-flow\backend\src\agents\graph.py) 里 `route_after_workflow_router()` 仍然只看是否存在 `RUNNING` task，再决定是否进入 executor。
- [executor.py](E:\work\deer-flow\backend\src\agents\executor\executor.py) 一进入就从 `task_pool` 里拿第一个 `RUNNING` task 执行。
- [semantic_router.py](E:\work\deer-flow\backend\src\agents\router\semantic_router.py) 虽然已经有 helper / dependency / intervention / resume 等能力，但整体仍然是在“挑一个任务继续推”。

这意味着现在的 `task_pool` 更像共享黑板，还不是一个真正的调度器。

因此如果 slice-b 已完成，下一步最合理的顺序不是继续往上堆治理产品层，也不是先扩更多 domain agent，而是先把 **调度器成立**。

## Current Status

当前系统的基础条件已经具备：

1. `Phase 1` 已完成
   - engine registry 与 build-time hooks 已经站住

2. `Phase 4` 已完成主接入
   - verifier 已经迁到统一 runtime hooks

3. Hook Harness 已覆盖到关键 runtime 控制点
   - after-node
   - task complete
   - final result commit
   - interrupt lifecycle
   - state commit

4. clarification / intervention / resume 主链路已经有统一控制点

也就是说，**现在缺的不是更多守门点，而是更强的执行与调度能力。**

## Two-Stage Plan

## Stage 1: Dependency-aware Parallel Scheduler MVP

### Stage Goal

把当前 workflow 从“串行执行一个 `RUNNING` task”的模型，升级成“支持依赖感知和有限并发窗口”的调度器。

这一步的目标不是做复杂调度平台，而是先做一个最小可用、可验证、可回归的并发调度内核。

### In Scope

1. 为 `task_pool` 增加最小必要的调度字段
   - 依赖关系
   - 优先级或等价调度提示
   - runnable 判定所需状态
   - 最小资源预算表达

2. 升级 router
   - 从“选一个待执行任务”升级成“识别一组可执行任务”
   - 但不要求一开始引入复杂策略系统

3. 升级 executor
   - 从“每次执行一个 `RUNNING` task”升级成“支持有限并发窗口”
   - 建议先固定并发窗口，不做动态资源调度

4. 保证并发下的 runtime 语义仍然稳定
   - clarification
   - intervention
   - resume
   - verifier hooks
   - state commit hooks
   - task 事件与 observability

5. 先以 1 到 2 条典型 workflow 做 pilot
   - 例如 `contacts + hr`
   - 或 `meeting + contacts`

### Out Of Scope

1. 完整的资源调度框架
2. 任意复杂的优先级算法
3. 多租户或队列级调度
4. UI 级调度控制台
5. domain memory / persistent domain agent 正式落地

### Functional Requirements

1. 独立任务在依赖满足时可以并行执行
2. 有依赖的任务不会提前执行
3. clarification / intervention 不因并发而丢失上下文
4. verifier 与 hooks 在并发下仍保持确定语义
5. task_pool 最终能够收敛到稳定终态

### Change Surface

Stage 1 的改动范围应主要控制在：

- `backend/src/agents/router/semantic_router.py`
- `backend/src/agents/executor/executor.py`
- `backend/src/agents/graph.py`
- `backend/src/agents/thread_state.py`
- 相关 hooks / observability 接入点
- 对应测试文件

Stage 1 不应外溢到：

- frontend
- intervention protocol 定义
- verifier family 定义
- agent CRUD / engine registry

### Acceptance Criteria

1. workflow 运行时支持有限并发窗口
2. 至少一条典型多任务链路能从串行升级为依赖感知并发
3. clarification / intervention / resume 回归不破坏
4. verifier / interrupt / state-commit hooks 在并发下不回归
5. baseline / regression 可以稳定验证串行与并发的差异

### Stage 1 Completion Checklist (Current Assessment)

本节用于记录 `Stage 1` 的**完成判定**，只反映当前实现与验收状态，不改变上面的目标、范围或验收标准。

#### 已满足项

- [x] 对应 `Stage Goal`：workflow 已从“串行执行单个 `RUNNING` task”升级为“依赖感知 + 有限并发窗口”的调度模型
- [x] 对应 `In Scope / 1-3`：`task_pool` 已具备最小调度字段，router 已能识别一组可执行任务，executor 已支持固定并发窗口且执行层有兜底限制
- [x] 对应 `Functional Requirements / 1-2`：独立任务可并发执行；有依赖任务不会提前执行
- [x] 对应 `Functional Requirements / 3`：clarification / intervention / resume 主链路已回归通过，clarification answer 已在 router 绑定到目标 task 的 `resolved_inputs`
- [x] 对应 `Functional Requirements / 4`：verifier hooks、interrupt hooks、state commit hooks 在并发路径下回归通过
- [x] 对应 `Functional Requirements / 5`：`task_pool` 能收敛到稳定终态，`verified_facts` 提交与最终汇总链路可正常完成
- [x] 对应 `In Scope / 5` 与 `Acceptance Criteria / 2`：至少一条典型多任务 workflow 已验证依赖感知并发调度收益
- [x] 对应 `Acceptance Criteria / 1`：workflow 运行时已支持有限并发窗口，且 executor 对超窗 `RUNNING` 任务具备硬性保护
- [x] 对应 `Acceptance Criteria / 3-4`：clarification / intervention / resume 以及 verifier / interrupt / state-commit hooks 当前均无阻塞级回归

#### 剩余收尾项

- [ ] 对应 `Functional Requirements / 3` 与 `Acceptance Criteria / 3`：将“多个 clarification 同时等待时，每次 resume 仅绑定并恢复第一个 clarification task，剩余 task 等待后续轮次”补充到文档与测试中，避免该行为被误判为缺陷
- [ ] 对应 `Acceptance Criteria / 5`：补一份更明确的 baseline / regression 结果沉淀或验收记录，用于正式说明串行与并发路径的可区分性

#### 完成判定

- `Stage 1` 当前可判定为：**功能实现完成，进入验收收尾阶段**
- 在上述两项收尾完成后，可进一步判定为：**`Stage 1` 正式验收完成，可关闭并进入 `Stage 2`**

## Stage 2: Persistent Domain Agent Pilot

### Stage Goal

在 Stage 1 调度器稳定之后，选择**一个** domain agent 试点升级为 Persistent Domain Agent，使其具备有限的领域记忆、runbook 与 verifier 协同能力。

这一步的目标不是“一次性把所有 domain agent 持久化”，而是验证这种 agent 形态在当前平台上的可行性。

### In Scope

1. 只选择一个 pilot domain
   - 建议优先 `contacts-agent` 或 `meeting-agent`

2. 为该 pilot domain 增加最小持久能力
   - domain memory 开关
   - domain runbook / playbook 入口
   - domain-specific verifier 协同

3. 明确 persistent agent 的使用边界
   - 哪些信息允许积累
   - 哪些信息仍然必须来自当前 thread / verified_facts
   - 哪些行为只能由 verifier 或 policy 判断

4. 用真实 workflow 验证收益
   - 减少重复澄清
   - 提高跨回合收敛能力
   - 减少 helper 往返次数

### Out Of Scope

1. 所有 domain agent 一起持久化
2. 通用长期记忆平台重构
3. 知识系统全量建设
4. operator-facing 配置面板

### Functional Requirements

1. pilot domain 能在不破坏当前 workflow 语义的前提下复用有限领域知识
2. domain memory 不替代 verified_facts，而是与之协同
3. 持久能力必须可关闭、可回退、可验证
4. pilot domain 的收益必须可通过 baseline / regression 量化观察

### Change Surface

Stage 2 的改动范围应主要控制在：

- pilot domain agent 配置
- domain memory / runbook 接入层
- executor 构建上下文时的 domain-specific 注入点
- verifier / tests / docs

Stage 2 不应外溢到：

- 全量 domain agent
- 全局记忆系统重构
- frontend 新能力
- 完整 Knowledge Harness

### Acceptance Criteria

1. 一个 pilot domain 完成 Persistent Domain Agent 试点
2. 至少一条真实 workflow 在重复澄清、帮助往返、或跨回合收敛上有可验证改善
3. domain memory 与 verified_facts / verifier 的职责边界清晰
4. 关闭该能力后，系统可退回当前稳定行为

## Stage Boundary Rules

为避免范围失控，这两个阶段必须严格分开：

### Stage 1 只回答一个问题

`workflow 是否已经是一个真正的、依赖感知的调度器？`

不要在这一步顺手把 domain persistence 一起做掉。

### Stage 2 只回答一个问题

`在新的调度器之上，Persistent Domain Agent 是否真的带来业务价值？`

不要在这一步顺手把整个 Knowledge Harness 一起展开。

## Recommended Order

建议按下面顺序推进：

1. Stage 1 完成并发调度 MVP
2. Stage 1 回归稳定并接入 baseline / regression
3. Stage 2 只选一个 pilot domain 做持久化试点
4. Stage 2 验证收益后，再决定是否扩到更多 domain

## Why Not Other Work First

### 为什么不是先做更完整的 Governance Harness

因为 governance 的 runtime 接入点已经基本具备，当前更大的平台收益瓶颈是调度能力不足。先补调度器，后续治理层才有更高价值。

### 为什么不是先做完整 Knowledge Harness

知识系统很重要，但当前最直接限制 workflow 能力上限的，是执行模型仍然串行。先做 knowledge，收益会先被串行调度模型压住。

### 为什么不是先做更多 domain agents

如果不先增强调度层，只会继续把更多 agent 堆到当前串行 runtime 上，复杂度会上升，但平台能力增益有限。

## Risks

1. 如果 Stage 1 范围失控，容易把“并发调度 MVP”做成“通用资源调度平台”，导致周期和风险都失控
2. 如果 Stage 1 没有先保护 clarification / intervention / resume 回归，并发很容易放大已有边界问题
3. 如果 Stage 2 一开始覆盖多个 domain，会让“持久能力到底有没有价值”变得难以归因
4. 如果 Stage 2 让 domain memory 侵入 verified_facts 的职责边界，后续会很难治理

## Acceptance Summary

完成这两阶段后，平台应从当前的：

`串行 workflow + 可治理 runtime`

升级为：

`依赖感知并发 workflow + 可验证的 persistent domain pilot`

这会为后续真正进入更完整的 `Phase 5 Governance Harness` 和 `Phase 3/6` 打下更稳的基础。

## Stage 1 Acceptance Close-out Update

- [x] “多个 clarification 同时等待时，每次 resume 仅绑定并恢复第一个 clarification task，剩余 task 等待后续轮次”已补充到实现与测试语义中；回归入口见 `backend/tests/test_workflow_resume_concurrency.py`
- [x] Stage 1 baseline / regression / observability 验收沉淀已补齐；正式记录见 [workflow-phase2-stage1-scheduler-acceptance-execution.md](./workflow-phase2-stage1-scheduler-acceptance-execution.md)
- [x] `Stage 1` 现已可判定为正式验收完成，可关闭并进入 `Stage 2`

## Related Docs

- [workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md](./workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md)
- [workflow-runtime-hook-harness-mvp.md](./workflow-runtime-hook-harness-mvp.md)
- [workflow-verification-harness-phase4.md](./workflow-verification-harness-phase4.md)
- [workflow-engine-registry-phase1.md](./workflow-engine-registry-phase1.md)
- [workflow-phase0-baseline-and-metrics.md](./workflow-phase0-baseline-and-metrics.md)
- [workflow-phase2-stage1-scheduler-acceptance-execution.md](./workflow-phase2-stage1-scheduler-acceptance-execution.md)
- [workflow-phase2-two-stage-scheduler-and-persistent-domain-agent-backend-checklist.md](./workflow-phase2-two-stage-scheduler-and-persistent-domain-agent-backend-checklist.md)
- [workflow-phase2-two-stage-scheduler-and-persistent-domain-agent-test-checklist.md](./workflow-phase2-two-stage-scheduler-and-persistent-domain-agent-test-checklist.md)

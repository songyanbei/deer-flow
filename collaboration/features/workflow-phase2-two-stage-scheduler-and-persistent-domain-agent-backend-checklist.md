# Backend Checklist: Workflow Phase 2 Two-Stage Scheduler And Persistent Domain Agent

- Status: `completed` (`Stage 1` backend accepted on `2026-03-25`)
- Depends on: [workflow-phase2-two-stage-scheduler-and-persistent-domain-agent.md](./workflow-phase2-two-stage-scheduler-and-persistent-domain-agent.md)
- Last updated: `2026-03-25`

## Document Intent

这份文档只服务后端开发落地，目标是让开发只看文档也能准确理解：

- 两个阶段分别要交付什么
- 哪些模块允许改
- 哪些模块不应该动
- 每一阶段什么算完成

默认原则：

- 先完成 `Stage 1`
- `Stage 1` 稳定后再启动 `Stage 2`
- 不允许把两个阶段混做成一个大改造

## Stage 1: Dependency-aware Parallel Scheduler MVP

## 1. Stage Goal

把当前 workflow 从“串行选择一个 `RUNNING` task 执行”的模型，升级为“支持依赖感知与有限并发窗口”的调度器。

这一步的核心交付是 **调度器成立**，不是完整 orchestration 平台。

## 2. Current Backend Baseline

当前代码里，调度层的真实形态是：

- `graph.py`
  - router 后只判断是否存在 `RUNNING` task，再进入 executor
- `executor.py`
  - 每次只拿第一个 `RUNNING` task 执行
- `semantic_router.py`
  - 已具备 helper / dependency / intervention / resume 能力
  - 但仍然围绕“推进单个任务”设计

所以 Stage 1 不是优化局部逻辑，而是要明确把 `task_pool` 升级为可调度结构。

## 3. In Scope

- [ ] 为 `task_pool` 增加最小必要的调度字段
- [ ] 明确任务依赖与 runnable 判定
- [ ] router 从“挑一个任务”升级成“识别可执行任务集合”
- [ ] executor 从“单任务执行”升级成“有限并发窗口”
- [ ] 并发下保持 clarification / intervention / resume 正确
- [ ] 并发下保持 verifier / interrupt / state-commit hooks 正确
- [ ] 并发下保持事件流和可观测性稳定

## 4. Out Of Scope

- [ ] 不做通用资源调度平台
- [ ] 不做复杂优先级策略系统
- [ ] 不做 frontend 调度界面
- [ ] 不做 domain memory
- [ ] 不重写 verifier family
- [ ] 不重写 intervention / clarification 协议
- [ ] 不扩 agent CRUD / engine registry

## 5. Target Backend Change Surface

Stage 1 的主要改动范围应控制在：

- [ ] [graph.py](E:\work\deer-flow\backend\src\agents\graph.py)
- [ ] [semantic_router.py](E:\work\deer-flow\backend\src\agents\router\semantic_router.py)
- [ ] [executor.py](E:\work\deer-flow\backend\src\agents\executor\executor.py)
- [ ] [thread_state.py](E:\work\deer-flow\backend\src\agents\thread_state.py)
- [ ] 相关 hooks / observability 接入点

不应外溢到：

- [ ] frontend
- [ ] domain agent YAML/CRUD 设计
- [ ] verifier contract
- [ ] intervention protocol contract

## 6. Implementation Requirements

### 6.1 Task Scheduling Model

- [ ] 为 task 增加明确的依赖表达
- [ ] 为 task 增加最小必要的调度状态或派生字段
- [ ] runnable 判定必须基于结构化状态，而不是 prompt / 文案判断
- [ ] task 状态迁移仍需与现有 reducer 语义兼容

### 6.2 Router Upgrade

- [ ] router 能识别一组当前可执行的任务
- [ ] router 不再隐含“每轮只会挑一个唯一任务”的假设
- [ ] router 的新逻辑不能破坏 helper / dependency / interruption 分支
- [ ] router 输出必须仍然可被 graph 条件边稳定消费

### 6.3 Executor Upgrade

- [ ] executor 支持有限并发窗口
- [ ] 并发窗口先做固定值，不做动态资源管理
- [ ] executor 在并发下仍能正确处理：
  - [ ] task_complete
  - [ ] request_help
  - [ ] request_clarification
  - [ ] request_intervention
  - [ ] resume_tool_call
- [ ] executor 不应引入新的非确定性状态写入方式

### 6.4 State And Hook Compatibility

- [ ] 与 `before_interrupt_emit / after_interrupt_resolve` 兼容
- [ ] 与 `before_task_pool_commit / before_verified_facts_commit` 兼容
- [ ] 与 `after_task_complete / before_final_result_commit` verifier hooks 兼容
- [ ] 并发提交不能绕开 reducer
- [ ] `verified_facts` 提交语义不能被并发路径破坏

### 6.5 Pilot Scope

- [ ] 只挑 1 到 2 条典型 workflow 做并发 pilot
- [ ] 不要求全量 domain 同时适配
- [ ] pilot 选择应优先覆盖“可独立并行”的真实任务组合

## 7. Acceptance Criteria

- [ ] workflow 运行时支持有限并发窗口
- [ ] 至少一条典型多任务链路支持依赖感知并发
- [ ] clarification / intervention / resume 回归不破坏
- [ ] verifier 与 slice-b hooks 在并发下不回归
- [ ] task_pool 能稳定收敛
- [ ] observability 能看出并发调度结果

## 7.1 Stage 1 Completion Checklist (Current Assessment)

本节用于记录 `Stage 1` 的**后端完成判定**，只反映当前开发与验收收尾状态，不改变上面的目标、范围与交付边界。

### 已满足项

- [x] 对应 `1. Stage Goal`：workflow 已从“串行选择一个 `RUNNING` task 执行”升级为“依赖感知 + 有限并发窗口”的调度模型
- [x] 对应 `3. In Scope`：`task_pool` 最小调度字段、router 批量识别 runnable tasks、executor 固定并发窗口三项已落地
- [x] 对应 `6.1 Task Scheduling Model`：任务依赖表达、runnable 判定、状态迁移与 reducer 兼容性已成立
- [x] 对应 `6.2 Router Upgrade`：router 已不再依赖“每轮只挑一个任务”的前提，且 helper / dependency / interruption 分支保持可用
- [x] 对应 `6.3 Executor Upgrade`：executor 已支持有限并发窗口，并对超窗 `RUNNING` 任务增加执行层硬性保护
- [x] 对应 `6.4 State And Hook Compatibility`：`before_interrupt_emit / after_interrupt_resolve`、`before_task_pool_commit / before_verified_facts_commit`、`after_task_complete / before_final_result_commit` 在并发路径下回归通过
- [x] 对应 `6.5 Pilot Scope`：已有 1 到 2 条典型 workflow 覆盖独立并发与依赖恢复的 pilot 验证
- [x] 对应 `7. Acceptance Criteria / 1-5`：当前后端实现已满足有限并发窗口、依赖感知并发、clarification / intervention / resume 回归、hooks 回归、task_pool 收敛等核心要求

### 剩余收尾项

- [ ] 对应 `6.3 Executor Upgrade` 与 `7. Acceptance Criteria / 3`：将“多个 clarification 同时等待时，每次 resume 只绑定并恢复第一个 clarification task，剩余 task 保持等待下一轮”的运行时语义补充到后端文档与测试中
- [ ] 对应 `7. Acceptance Criteria / 6`：补一份更明确的 observability / baseline / regression 结果沉淀，作为 Stage 1 正式验收记录

### 完成判定

- `Stage 1` 当前可判定为：**后端实现完成，进入验收收尾阶段**
- 上述两项收尾完成后，可进一步判定为：**`Stage 1` 后端正式验收完成，可关闭并进入 `Stage 2`**

## 7.2 Stage 1 Close-out Update

- [x] “多个 clarification 同时等待时，每次 resume 只绑定并恢复第一个 clarification task，剩余 task 保持等待下一轮”的运行时语义已通过 `workflow_resume.py` 与 `backend/tests/test_workflow_resume_concurrency.py` 固化
- [x] Stage 1 observability / baseline / regression 验收沉淀已补齐，见 [workflow-phase2-stage1-scheduler-acceptance-execution.md](./workflow-phase2-stage1-scheduler-acceptance-execution.md)
- [x] `Stage 1` 后端正式验收完成，可关闭并进入 `Stage 2`

## Stage 2: Persistent Domain Agent Pilot

## 8. Stage Goal

在 Stage 1 调度器稳定后，只选择一个 domain agent 试点升级为 Persistent Domain Agent，验证“有限领域记忆 + runbook + verifier 协同”是否真的带来收益。

这一步的核心交付是 **pilot 价值被验证**，不是全量 domain 持久化。

## 9. In Scope

- [ ] 只选一个 pilot domain
- [ ] 为该 domain 增加最小持久能力
- [ ] 增加 runbook / playbook 入口
- [ ] 增加与 verifier 的协同
- [ ] 定义 domain memory 与 verified_facts 的边界
- [ ] 提供可关闭、可回退的实现方式

## 10. Out Of Scope

- [ ] 不同时改多个 domain
- [ ] 不重构全局 memory 系统
- [ ] 不做完整 Knowledge Harness
- [ ] 不做新的 frontend 能力
- [ ] 不做 operator-facing 管理界面

## 11. Target Backend Change Surface

Stage 2 的主要改动范围应控制在：

- [ ] 一个 pilot domain 的配置与运行时接入
- [ ] domain memory / runbook 注入点
- [ ] executor 构建上下文时的 domain-specific 注入
- [ ] 该 domain 对应 verifier / tests / docs

不应外溢到：

- [ ] 全量 domain agents
- [ ] 全局长期记忆平台
- [ ] 全量知识目录重构

## 12. Implementation Requirements

### 12.1 Pilot Selection

- [ ] 只允许选择一个 pilot domain
- [ ] 建议优先 `contacts-agent` 或 `meeting-agent`
- [ ] 选择标准应是“真实 workflow 收益清晰”，不是“实现最容易”

### 12.2 Domain Memory Boundary

- [ ] domain memory 不能替代 verified_facts
- [ ] verified_facts 仍是当前 thread 的结构化事实真相源
- [ ] domain memory 只承载允许沉淀的领域经验或辅助知识
- [ ] 所有跨回合复用能力必须可验证、可关闭

### 12.3 Runbook / Verifier Coordination

- [ ] runbook 必须有稳定入口，不得把大量领域知识继续堆进 prompt
- [ ] domain-specific verifier 仍然保留守门职责
- [ ] persistent 能力不能绕开 verifier 直接决定业务结果

### 12.4 Rollback Safety

- [ ] 关闭 persistent 开关后，系统能退回 Stage 1 稳定行为
- [ ] pilot 失败时不得污染其他 domain runtime 语义

## 13. Acceptance Criteria

- [ ] 一个 pilot domain 完成 Persistent Domain Agent 试点
- [ ] 至少一条真实 workflow 体现出可验证收益
- [ ] domain memory / verified_facts / verifier 三者边界清晰
- [ ] 关闭能力后可回退到现有稳定行为

## 14. Stage Boundary Guard

- [ ] Stage 1 未稳定前，不启动 Stage 2 正式开发
- [ ] Stage 1 不顺手实现 domain persistence
- [ ] Stage 2 不顺手扩成全量 knowledge / memory 平台

## 15. Documentation

- [ ] 主 feature 文档状态及时更新
- [ ] 如阶段边界变化，先回填主文档再扩代码
- [ ] 若 pilot domain 选型变化，必须回填主文档

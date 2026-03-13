# workflow 状态反馈与最终汇总优化方案

## 1. 背景

结合当前代码现状，`workflow` 模式已经完成核心编排闭环，但在用户体感上仍存在三类明显问题：

1. 用户发送请求后，要等一段时间 `WorkflowFooterBar` 才出现。
2. 状态栏出现后，首个初始状态停留时间过长，且中间几乎没有变化，随后直接跳出子任务。
3. 像“会议室预定”这类子任务已经成功时，到最终 assistant 输出“预定成功”的总结信息之间仍有明显空窗，用户不知道系统正在做什么。

这三个问题会让当前串行 workflow 虽然“能跑通”，但呈现出“前面不动、后面跳变、结尾又悬空”的观感。

## 2. 与现有设计文档的关系

本方案不是另起炉灶，而是对两份既有设计文档的收口延伸：

1. 对齐 [多智能体架构技术路线说明书.md](E:/work/deer-flow/多智能体架构技术路线说明书.md)
- 保持“DeerFlow 基础设施 + 轻量 Planner / Router + 共享黑板”的主路线不变。
- 不把业务规则塞回框架层。
- 不破坏当前串行 workflow 的职责边界。

2. 直接承接 [多智能体兼容改造分阶段实施方案.md](E:/work/deer-flow/多智能体兼容改造分阶段实施方案.md)
- 承接 10.4 中已经提出但尚未完成的“入口预处理与即时确认反馈链路”。
- 承接 12.8 中已经提出但尚未落地的 `workflow_ack_ready` 即时确认气泡。
- 补强 3.4、12.7、20.10 中已经明确指出的“queued workflow 早期阶段反馈不足”和“入口即时确认反馈链路缺失”。

因此，这份方案的定位是：

- 不改变核心编排架构
- 只补齐“状态连续性”和“执行可解释性”
- 优先解决用户最直观的等待焦虑

## 3. 当前根因分析

### 3.1 问题一：状态栏要等一段时间才出现

当前前端页面中，`WorkflowFooterBar` 的挂载条件依赖：

- `thread.values.resolved_orchestration_mode === "workflow"`
- 或者已经有 `task_pool / todos`

相关位置：

- [frontend/src/app/workspace/chats/[thread_id]/page.tsx](E:/work/deer-flow/frontend/src/app/workspace/chats/[thread_id]/page.tsx)
- [frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx](E:/work/deer-flow/frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx)

而 `resolved_orchestration_mode` 当前要等后端真正执行到 `orchestration_selector` 后，才会通过 custom event 或 hydration 回到前端：

- [backend/src/agents/orchestration/selector.py](E:/work/deer-flow/backend/src/agents/orchestration/selector.py)
- [frontend/src/core/threads/hooks.ts](E:/work/deer-flow/frontend/src/core/threads/hooks.ts)

这意味着：

1. 请求刚提交但 worker 还未真正开始执行时，前端无法知道本轮是否进入 workflow。
2. 在单 worker 排队场景下，这段空窗会被明显放大。

### 3.2 问题二：状态栏出来后，初始状态太久不变，随后直接跳出子任务

当前 `workflow-progress` 的摘要主要依赖：

- `resolved_orchestration_mode`
- `execution_state`
- `task_pool`

相关位置：

- [frontend/src/components/workspace/workflow-progress.ts](E:/work/deer-flow/frontend/src/components/workspace/workflow-progress.ts)
- [frontend/src/components/workspace/workflow-footer-bar.tsx](E:/work/deer-flow/frontend/src/components/workspace/workflow-footer-bar.tsx)

而后端在“首个子任务出现前”能提供的显式增量信息很少：

1. selector 只会很快给出一次 mode decision。
2. planner 在真正产出 `task_pool` 之前，没有持续发 custom event。
3. router / executor 的细粒度事件，要等首个 task 真正被创建并执行后才出现。

于是 UI 实际呈现为：

`workflow 已选中 -> 一个泛化 planning 状态停留较久 -> 直接出现第一个子任务`

中间缺少这些关键阶段：

- 已进入 workflow
- 正在理解请求
- 正在拆解步骤
- 已生成计划，准备分发

### 3.3 问题三：子任务成功后到最终总结之间空窗明显

从当前图结构看，`executor` 完成后并不会直接结束，而是会回到 `planner` 做 validate / summarize：

- [backend/src/agents/graph.py](E:/work/deer-flow/backend/src/agents/graph.py)
- [backend/src/agents/planner/node.py](E:/work/deer-flow/backend/src/agents/planner/node.py)

这是符合当前架构设计的，原因是：

1. 顶层 Planner 是唯一“目标是否达成”的裁决者。
2. 子任务成功不等于用户目标已经被最终确认达成。
3. framework 当前明确保持串行 workflow，不走业务特判式快速结束。

但前端对这个“总结阶段”的表达不够清楚：

1. `execution_state === "EXECUTING_DONE"` 时虽然有 `summarizing` 文案。
2. 但 detail 主要还是 `planner_goal / orchestration_reason`。
3. 它没有告诉用户“最后一个子任务已经完成，现在正在整理最终答复”。

于是体感上会变成：

- 子任务卡片已经显示成功
- 主状态栏仍是泛化处理态
- 最终回答迟迟未出
- 用户不知道系统此刻是在总结、校验，还是卡住了

## 4. 目标与非目标

### 4.1 目标

本方案希望实现以下结果：

1. 用户提交 workflow 请求后，界面立即进入“已启动协作流程”的连续反馈状态。
2. 首个子任务出现前，状态栏至少经历 2 到 4 个可感知的阶段变化，而不是长时间静止。
3. 最后一个子任务完成后，界面明确进入“正在整理最终结果”的状态。
4. 整个链路仍由后端 authoritative event 驱动，不把业务理解逻辑转移到前端。
5. 不破坏当前 `planner -> router -> executor` 的职责边界。

### 4.2 非目标

本方案暂不解决以下问题：

1. 不在本轮引入并行调度器。
2. 不在本轮引入完整 HITL 审批流。
3. 不在 framework 层添加任何“会议室预定成功就直接结束”的业务特判。
4. 不要求本轮就把最终总结的模型耗时显著缩短，第一优先级是“让用户知道系统在干什么”。

## 5. 总体方案

核心思路是把当前断裂的状态链路改造成“两段式反馈”：

1. 前端本地乐观壳层
- 解决“提交后到 selector 真正开始前”的空窗。

2. 后端 authoritative stage event
- 解决“selector 之后到首个 task 出现前”和“最后一个 task 成功后到最终回答前”的空窗。

整体上形成如下状态连续体：

```text
用户点击发送
  -> 本地乐观 workflow 壳层（仅限可确定为 workflow 的情况）
  -> selector 决策
  -> workflow_ack_ready
  -> planning_started
  -> planning_ready
  -> first_task_dispatched
  -> task events...
  -> summarizing_started
  -> final assistant summary
```

## 6. 方案细化

### 6.1 方案 A：补齐“状态栏出现前”的空窗

#### 6.1.1 引入前端本地临时阶段

为解决“worker 尚未起跑时前端无任何 workflow UI”的问题，增加一个**非持久化、本地临时状态**：

```ts
type LocalWorkflowShellStage =
  | "idle"
  | "submitting"
  | "queued";
```

该状态只存在于前端 hook 中，不写入线程状态，不参与历史恢复。

建议挂载位置：

- [frontend/src/core/threads/hooks.ts](E:/work/deer-flow/frontend/src/core/threads/hooks.ts)

#### 6.1.2 触发条件

只有在前端能够高置信判断“本轮必定会进入 workflow”时才启用本地壳层：

1. `context.requested_orchestration_mode === "workflow"`
2. 或 agent 创建页 / agent 元数据已知其默认模式就是 `workflow`

`auto` 模式下不做前端猜测，不在该层臆断“本轮一定是 workflow”。

#### 6.1.3 呈现策略

本地壳层只负责让状态栏尽早出现，不负责输出主消息区 ack 气泡。

建议文案：

1. `submitting`
- 标题：`正在启动协作流程`
- 详情：`请求已提交，正在等待工作流接管`

2. `queued`
- 标题：`协作流程排队中`
- 详情：`前方仍有任务执行中，当前请求已进入等待队列`

注意：

- 这是前端局部状态，不是正式执行承诺。
- 一旦后端返回 `resolved_orchestration_mode=workflow` 或任何 workflow stage event，立即被 authoritative 状态替换。

#### 6.1.4 为什么必须要有前端壳层

单靠现有文档中规划的 `workflow_ack_ready` 还不够，因为：

1. `workflow_ack_ready` 必须等 worker 真的开始跑到 selector 后才能发出。
2. 如果当前 runtime 是单 worker 排队，ack event 本身也会被延后。

因此，要解决“状态栏迟迟不弹出”，必须引入“前端可立即响应”的非持久化壳层。

这不违背现有设计文档，因为：

- 它只影响状态栏显示，不生成 assistant 语义内容
- 不冒充真实 workflow 执行结果
- 也不替代 12.8 里要求由后端生成的即时确认气泡

### 6.2 方案 B：把“初始状态静止很久”拆成可感知阶段

#### 6.2.1 新增统一 stage 字段

建议在线程状态中新增以下字段：

```python
WorkflowStage = Literal[
    "queued",
    "acknowledged",
    "planning",
    "routing",
    "executing",
    "summarizing",
]

workflow_stage: NotRequired[WorkflowStage | None]
workflow_stage_detail: NotRequired[str | None]
workflow_stage_updated_at: NotRequired[str | None]
```

建议修改位置：

- [backend/src/agents/thread_state.py](E:/work/deer-flow/backend/src/agents/thread_state.py)
- [frontend/src/core/threads/types.ts](E:/work/deer-flow/frontend/src/core/threads/types.ts)

#### 6.2.2 新增统一事件

建议新增一个统一 custom event，而不是散落多个互不兼容事件：

```json
{
  "type": "workflow_stage_changed",
  "run_id": "run_xxx",
  "resolved_orchestration_mode": "workflow",
  "workflow_stage": "planning",
  "workflow_stage_detail": "正在拆解执行步骤",
  "workflow_stage_updated_at": "2026-03-12T15:00:00Z"
}
```

统一事件优于为每个动作单独造事件名，因为：

1. 前端只需要维护一套状态合并逻辑。
2. 后续扩展新的中间阶段不会继续膨胀事件种类。
3. 更符合“状态机”而非“事件点状堆积”的设计。

#### 6.2.3 推荐阶段切换点

建议后端至少发出以下阶段：

1. `acknowledged`
- 触发点：selector 确认进入 workflow 后
- detail：`已进入协作流程，开始理解请求`

2. `planning`
- 触发点：planner 首轮 decompose 前
- detail：`正在拆解执行步骤`

3. `routing`
- 触发点：planner 已产生 `task_pool`，router 准备分发第一个任务时
- detail：`已生成执行计划，正在分发首个任务`

4. `executing`
- 触发点：第一个 task 真正进入 `RUNNING`
- detail：优先使用首个任务标题或状态详情

5. `summarizing`
- 触发点：所有 task 已完成，planner 开始 validate / summarize
- detail：`已完成执行，正在整理最终结果`

#### 6.2.4 后端文件改动建议

1. selector
- [backend/src/agents/orchestration/selector.py](E:/work/deer-flow/backend/src/agents/orchestration/selector.py)
- 在 resolved mode 为 workflow 时，同时发：
  - `orchestration_mode_resolved`
  - `workflow_stage_changed(stage=acknowledged)`

2. planner
- [backend/src/agents/planner/node.py](E:/work/deer-flow/backend/src/agents/planner/node.py)
- 首轮 decompose 前发 `planning`
- 生成新任务后发 `routing`
- validate 分支开始前发 `summarizing`

3. router
- [backend/src/agents/router/semantic_router.py](E:/work/deer-flow/backend/src/agents/router/semantic_router.py)
- 当第一个 pending task 被置为 `RUNNING` 时，可补发一次 `executing`

注意：

- 这些事件只表达“流程阶段”，不替代 task event。
- task event 继续服务任务级可视化。
- stage event 服务流程级反馈。

### 6.3 方案 C：让“子任务成功后到最终回答前”变得可解释

#### 6.3.1 问题本质

这里的真正空窗并不是系统停住，而是：

```text
executor 完成最后一个任务
  -> graph 回 planner
  -> planner validate
  -> 生成最终 assistant 总结
```

也就是说，系统正在做“最终裁决与总结”，但 UI 没把这层语义表达出来。

#### 6.3.2 第一阶段改法：先增强解释性，不急着优化耗时

建议先不做 framework 层“快速结束”优化，而是先把这段显示清楚：

1. planner validate 开始时发 `workflow_stage_changed(stage=summarizing)`
2. `workflow_stage_detail` 优先参考最后一个完成任务的结果摘要
3. 状态栏显示例如：
- `正在整理最终结果`
- `会议室预定已完成，正在生成最终答复`

这里的 detail 不能写业务规则，但可以通用地从以下来源降级获取：

1. 最后一个 `DONE` task 的 `status_detail`
2. 最后一个 `DONE` task 的 `description`
3. `planner_goal`

也就是：

```text
优先说“刚完成了什么”
其次说“正在整理什么”
```

#### 6.3.3 第二阶段可选优化：受约束的 summary fast-path

如果第一阶段做完后，仍然觉得尾部耗时不可接受，可以考虑第二阶段再评估一个**协议驱动的快速收尾机制**。

注意，这里不能做“会议室预定成功就直接结束”的业务硬编码。只能做通用协议：

```python
class VerifiedFactEntry(TypedDict):
    ...
    completion_hint: NotRequired[Literal["goal_likely_satisfied"] | None]
```

或在 task result payload 中引入通用 completion signal。

Planner validate 可在满足以下条件时走轻量 fast-path：

1. 所有 top-level task 都已 `DONE`
2. 没有 `WAITING_DEPENDENCY / FAILED / PENDING`
3. 存在明确 completion hint
4. 当前 run 未进入追加任务分支

即便如此，也应保留：

- 灰度开关
- 日志埋点
- 回退到原 validate LLM 的能力

建议把它定义为第二阶段优化项，不作为第一阶段必须项。

## 7. 前端实施方案

### 7.1 线程状态与事件合并

当前 [frontend/src/core/threads/hooks.ts](E:/work/deer-flow/frontend/src/core/threads/hooks.ts) 的 `extractThreadEventPatch()` 只接 `resolved_orchestration_mode / orchestration_reason / run_id`。

需要扩展为同时接：

```ts
type ThreadEventPatch = Pick<
  AgentThreadState,
  | "resolved_orchestration_mode"
  | "orchestration_reason"
  | "run_id"
  | "workflow_stage"
  | "workflow_stage_detail"
  | "initial_ack_text"
>;
```

这样前端在 task 尚未出现前，也能依靠 stage patch 维持状态栏。

### 7.2 `WorkflowFooterBar` 的展示优先级

建议状态栏标题/详情的优先级改为：

1. `waiting_clarification`
2. `waiting_dependency`
3. 本地临时壳层 `queued/submitting`
4. authoritative `workflow_stage`
5. task level active status
6. fallback 到当前 `execution_state`

这样可以保证：

1. 首屏早期优先显示“流程状态”
2. 子任务出现后自然切到任务状态
3. 尾部总结时再切回“流程总结状态”

### 7.3 文案策略

建议新增或重构 `workflowStatus` 文案，不再只靠当前几条泛化词条：

建议补充：

- `startingWorkflow`
- `queuedWorkflow`
- `acknowledgedWorkflow`
- `planningSteps`
- `routingFirstTask`
- `summarizingFinal`

其中中文建议：

1. `startingWorkflow`
- `正在启动协作流程`

2. `queuedWorkflow`
- `协作流程排队中`

3. `acknowledgedWorkflow`
- `已理解请求，准备开始处理`

4. `planningSteps`
- `正在拆解执行步骤`

5. `routingFirstTask`
- `已生成执行计划，正在分发首个任务`

6. `summarizingFinal`
- `已完成执行，正在整理最终结果`

### 7.4 主消息区即时确认气泡

这部分建议复用现有分阶段方案中的 `workflow_ack_ready` 设计，但本轮优先级低于状态栏连续性。

原因：

1. 用户当前最强烈的不适感来自“状态栏不出现”和“状态栏不动”。
2. ack 气泡有帮助，但如果状态栏本身仍断裂，效果仍然有限。

建议排序：

1. 先补状态栏连续状态
2. 再落 `workflow_ack_ready`

## 8. 后端实施方案

### 8.1 ThreadState 扩展

建议扩展：

- [backend/src/agents/thread_state.py](E:/work/deer-flow/backend/src/agents/thread_state.py)

新增字段：

```python
workflow_stage: NotRequired[str | None]
workflow_stage_detail: NotRequired[str | None]
workflow_stage_updated_at: NotRequired[str | None]
ack_plan: NotRequired[dict[str, Any] | None]
initial_ack_text: NotRequired[str | None]
```

其中：

1. `workflow_stage*` 用于状态栏和重连恢复
2. `ack_plan / initial_ack_text` 承接既有设计文档中的 ack 规划

### 8.2 统一 stage emitter

建议新增一个公共工具函数，例如：

```python
def emit_workflow_stage(
    writer,
    *,
    run_id: str | None,
    stage: str,
    detail: str | None,
    resolved_mode: str = "workflow",
) -> dict:
    ...
```

它负责：

1. 发 custom event
2. 返回对应的线程状态 patch

避免 selector / planner / router 各自手写一套结构。

### 8.3 planner 的阶段发射时机

在 [backend/src/agents/planner/node.py](E:/work/deer-flow/backend/src/agents/planner/node.py) 中建议加入：

1. decompose 前：
- `stage=planning`

2. 任务生成成功后：
- `stage=routing`

3. validate 前：
- `stage=summarizing`

其中 validate 前的 detail 建议从最后一个 `DONE` task 摘要中提取，而不是重复 `planner_goal`。

### 8.4 不建议在第一阶段引入的改法

以下做法第一阶段不建议采用：

1. framework 层识别“会议预定成功”后直接短路 `DONE`
2. 根据 task 文案做业务关键字硬编码
3. 把最终总结职责从 Planner 转移给 Executor

这些都与当前技术路线强调的“顶层轻量裁决 + 不把业务逻辑写回框架”冲突。

## 9. 分阶段实施建议

### 阶段 A：状态栏尽快出现

范围：

1. 前端本地 workflow 壳层
2. `WorkflowFooterBar` 挂载条件放宽到“明确 workflow 提交中”

预期收益：

- 解决“发送后长时间不弹状态栏”

风险：

- 只适用于前端已高置信知道本轮是 workflow 的情况
- `auto` 模式下仍需要后端 authoritative 决策

### 阶段 B：补齐 planning / routing / summarizing 阶段

范围：

1. 新增 `workflow_stage_changed`
2. 扩展线程状态
3. 改造 `workflow-progress` 与 `WorkflowFooterBar`

预期收益：

- 解决“状态栏出来后不动”
- 解决“子任务完成后不知道系统在干嘛”

风险：

- 需要统一前后端 patch 合并逻辑
- 需要避免 stage 与 task 状态互相打架

### 阶段 C：评估 summary fast-path

范围：

1. completion hint 协议
2. planner validate 的灰度优化

预期收益：

- 缩短“最后一个 task 成功到最终回答”的纯耗时

风险：

- 如果协议定义不稳，容易造成误判完成
- 必须严格灰度

## 10. 验收标准

### 10.1 针对问题一

1. 显式 `workflow` 请求发送后，状态栏应立即出现，不再依赖首个 task。
2. 若 worker 被占用，用户仍能看到“协作流程排队中”或等价状态。

### 10.2 针对问题二

1. 状态栏出现后，在首个 task 出现前至少发生一次到两次明确阶段变化。
2. 用户能够区分“已进入 workflow”“正在拆解步骤”“准备分发任务”。

### 10.3 针对问题三

1. 最后一个 task 完成后，界面明确切换到“正在整理最终结果”。
2. detail 优先表达“刚完成了什么”，而不是再次回退成泛化 loading。
3. 即使最终总结仍需数秒，用户也能理解当前系统正在做收尾，而不是卡住。

## 11. 建议修改文件清单

### 后端

- [backend/src/agents/thread_state.py](E:/work/deer-flow/backend/src/agents/thread_state.py)
- [backend/src/agents/orchestration/selector.py](E:/work/deer-flow/backend/src/agents/orchestration/selector.py)
- [backend/src/agents/planner/node.py](E:/work/deer-flow/backend/src/agents/planner/node.py)
- [backend/src/agents/router/semantic_router.py](E:/work/deer-flow/backend/src/agents/router/semantic_router.py)
- [backend/tests/test_multi_agent_core.py](E:/work/deer-flow/backend/tests/test_multi_agent_core.py)

### 前端

- [frontend/src/core/threads/types.ts](E:/work/deer-flow/frontend/src/core/threads/types.ts)
- [frontend/src/core/threads/hooks.ts](E:/work/deer-flow/frontend/src/core/threads/hooks.ts)
- [frontend/src/components/workspace/workflow-progress.ts](E:/work/deer-flow/frontend/src/components/workspace/workflow-progress.ts)
- [frontend/src/components/workspace/workflow-footer-bar.tsx](E:/work/deer-flow/frontend/src/components/workspace/workflow-footer-bar.tsx)
- [frontend/src/app/workspace/chats/[thread_id]/page.tsx](E:/work/deer-flow/frontend/src/app/workspace/chats/[thread_id]/page.tsx)
- [frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx](E:/work/deer-flow/frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx)
- [frontend/src/core/i18n/locales/zh-CN.ts](E:/work/deer-flow/frontend/src/core/i18n/locales/zh-CN.ts)
- [frontend/src/core/i18n/locales/en-US.ts](E:/work/deer-flow/frontend/src/core/i18n/locales/en-US.ts)

## 12. 最终建议

建议先按以下顺序推进，而不是一次性大改：

1. 先做“前端本地 workflow 壳层 + 状态栏立即出现”
2. 再做“后端统一 stage event + planning/routing/summarizing 三段式状态”
3. 最后再评估“summary fast-path”是否真的有必要

原因很直接：

1. 第一阶段就能明显改善“发出去没反应”的体感。
2. 第二阶段就能解决“状态栏不动”和“结尾不知道在干嘛”的核心抱怨。
3. 第三阶段才涉及真正的执行耗时优化，风险最高，应在体验问题先被解释清楚之后再考虑。

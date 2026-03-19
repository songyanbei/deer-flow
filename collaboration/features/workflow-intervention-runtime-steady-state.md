# Feature: Workflow Intervention Runtime Steady State

- Status: `draft`
- Owner suggestion: `backend` for protocol normalization, runtime state machine, and resume safety; `frontend` for authoritative intervention surface priority and duplicate suppression; `test` for cross-run, retry, reconnect, and idempotency validation
- Related area: workflow mode, intervention protocol, executor normalization, tool confirmation, user-owned blocking, resume semantics, decision cache

## Goal

基于当前已经落地的 intervention 基础能力，形成一套可以长期稳定演进的运行时方案，让 `workflow` 中所有“需要用户解除阻塞”的场景都收敛到统一、可恢复、可去重、可观察的主链路。

这份方案不是从零重做，而是要在当前代码已经具备的能力之上，把“局部可用”提升为“长期稳态”。

核心目标：

1. 同一个用户阻塞动作，在任意一次 workflow 生命周期中只能有一个 authoritative interrupt 实例
2. `before_tool` 风险确认与 `clarification / select / confirm` 用户交互共享统一顶层状态语义，但保留不同的恢复路径
3. 用户一旦确认，恢复逻辑必须稳定消费原始待执行动作，而不是依赖模型再次生成相同工具调用
4. 重试、刷新、断线重连、重复点击、跨 run 恢复都不能产生重复确认或重复执行
5. Decision Cache 继续保留，但只作为复用优化，不再承担主正确性

## Why This Needs Frontend/Backend Collaboration

Backend 当前已经掌握：

- `WAITING_INTERVENTION` 顶层状态
- `intervention_request / intervention_resolution`
- `pending_tool_call / continuation_mode`
- tool-level `before_tool` interception
- gateway resolve endpoint
- decision cache

Frontend 当前已经掌握：

- intervention card 渲染
- display projection 呈现
- `WAITING_INTERVENTION` 展示和提交 resolve

但长期稳态不是单侧能完成的：

1. backend 需要给出唯一 authoritative interrupt
2. frontend 需要保证同一时刻只呈现一张 authoritative intervention card
3. backend 和 frontend 需要对“同一个 interrupt 的生命周期”有一致认识
4. test 需要覆盖跨 run、恢复、重试、重复点击这些非页面级问题

## Related Documents

这份文档是“长期稳态总方案”，与现有文档的关系如下：

1. `collaboration/features/workflow-intervention-flow.md`
   - 定义了 intervention 的基础协议与第一阶段运行时能力
2. `collaboration/features/workflow-intervention-decision-cache.md`
   - 解决“用户已回答过的问题在重试时重复询问”
3. `collaboration/features/workflow-intervention-card-refactor.md`
   - 解决前端渲染层的结构分层与复用
4. `collaboration/features/workflow-user-intervention-state-refactor.md`
   - 解决 `request_help(user_*)` 直接落 `WAITING_INTERVENTION` 的主链路归一

本方案不替代上述文档，而是回答一个更高层的问题：

- 在这些能力都已部分落地之后，如何把 interrupt / resume / dedup / cache / display 收敛成稳定的一致协议

## Current Code Snapshot

当前代码不是“没有 intervention”，而是已经进入“功能具备但语义尚未完全收口”的状态。

### Backend Already Exists

截至当前代码状态，后端已经具备以下能力：

1. `TaskStatus` 已支持：
   - `WAITING_INTERVENTION`
   - `intervention_request`
   - `intervention_status`
   - `intervention_fingerprint`
   - `intervention_resolution`
   - `continuation_mode`
   - `pending_tool_call`
   - 文件：`backend/src/agents/thread_state.py`
2. executor 已支持从 `intervention_required` 进入：
   - `WAITING_INTERVENTION`
   - `continuation_mode = "resume_tool_call"`
   - `pending_tool_call`
   - 文件：`backend/src/agents/executor/executor.py`
3. executor 已支持把用户型 `request_help` 直接归一为：
   - `WAITING_INTERVENTION`
   - 文件：`backend/src/agents/executor/executor.py`
4. gateway 已提供：
   - `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
   - 文件：`backend/src/gateway/routers/interventions.py`
5. intervention decision cache 已落地：
   - `intervention_cache`
   - tool semantic fingerprint
   - clarification semantic fingerprint
   - 文件：`backend/src/agents/intervention/decision_cache.py`

### Frontend Already Exists

截至当前代码状态，前端已经具备以下能力：

1. `WAITING_INTERVENTION` 已可渲染
2. intervention card 已支持多种交互形式
3. display projection 已支持 `meeting_createMeeting` 等场景型确认卡
4. resolve endpoint 已接入

### Current Runtime Fault Lines

虽然上述能力都已存在，但当前运行时仍有几处关键语义断裂。

#### 1. Framework interrupt 和 agent follow-up 未形成严格优先级

executor 当前会处理：

- `intervention_required`
- `request_help`
- `ask_clarification`

但当前归一化仍然容易受到“最后一个 terminal signal”影响，而不是“哪个 signal 更 authoritative”。

这意味着：

1. middleware 已经发出 `intervention_required`
2. agent 后续又输出 `request_help`
3. executor 可能最终吃进 `request_help`
4. 同一轮执行内出现两套确认语义

#### 2. `before_tool` 的主正确性仍部分依赖新 run 中的再次生成

当前已经有 `resume_tool_call` 分支，这是非常好的基础。

但实际链路中仍然存在以下问题：

1. 不是所有用户确认路径都会稳定回到 `resume_tool_call`
2. 某些路径仍会回到“继续让 agent 跑”
3. 一旦继续让 agent 跑，就会重新碰到 middleware 拦截

#### 3. dedup 指纹仍混有执行上下文语义

当前 `InterventionMiddleware` 的 `_generate_fingerprint` 仍包含：

- `run_id`
- `task_id`
- `agent_name`
- `tool_name`
- `tool_args`

这让它更像“本轮执行实例指纹”，而不是“长期稳态 interrupt 标识”。

结果是：

1. 同语义动作跨 run 恢复时，很难直接命中同一个 interrupt
2. 已解决动作的 dedup 更像局部优化，而不是稳定协议

#### 4. gateway / workflow_resume 写回的数据不足以表达“这是哪个 interrupt 被解决了”

当前 resolve 时会写入：

- `intervention_status = resolved`
- `intervention_resolution`
- `resolved_inputs["intervention_resolution"] = { action_key, payload, resolution_behavior }`

但缺少更稳定的实例级绑定信息，例如：

- interrupt instance identity
- source signal type
- pending tool snapshot binding

#### 5. DanglingToolCallMiddleware 可能改变语义而不仅仅是补格式

当前 `DanglingToolCallMiddleware` 会为悬空 tool call 注入 synthetic `ToolMessage(error)`。

这对“修复消息格式”有帮助，但对于副作用工具和待确认工具来说，风险在于：

1. 它可能让模型继续在补洞后的历史上推理
2. 模型可能再次发起同一个 risky tool call
3. 于是再次触发确认

这说明它当前更像“格式修复器”，还不是“有 interrupt 语义意识的安全补洞器”。

### Observed Production-Like Failure In Latest Log

最新日志已经证明上述断裂会在真实链路中叠加成重复确认。

已观察到的实际现象：

1. 同一个 `meeting_createMeeting` 在同一线程中连续触发多次 `intervention_required`
2. 随后又生成一轮 `request_help(user_confirmation for meeting booking)`
3. 用户确认后，恢复执行中再次触发多轮 `intervention_required`

这说明当前问题不是单点 bug，而是以下多个模块共同作用的结果：

- middleware 优先级
- executor outcome normalization
- resolve 写回语义
- continuation mode 选择
- dangling tool call patch

## Long-Term Design Principle

长期稳态需要遵守以下原则。

### 1. 顶层阻塞状态按“谁来解除阻塞”建模

- 系统解除阻塞：`WAITING_DEPENDENCY`
- 用户解除阻塞：`WAITING_INTERVENTION`

这条原则已经部分落地，必须继续保持，不回退到“看工具名猜状态”。

### 2. interrupt instance 必须是第一公民

系统不能只知道“有一个 intervention_request”，还必须稳定表达：

1. 这是哪一个 interrupt 实例
2. 它来自哪个 source signal
3. 它绑定了哪个待执行动作
4. 它当前处于 pending / resolved / consuming / consumed 哪个阶段

### 3. framework interrupt 优先于 agent paraphrase

当同一轮执行里同时出现：

- `intervention_required`
- `request_help(user_confirmation / user_clarification)`
- `ask_clarification`

framework 直接发出的结构化 interrupt 必须具有更高优先级。

### 4. side-effect confirmation 必须恢复原动作，而不是重问模型

对于 `before_tool`：

1. 用户确认的对象是一个原始待执行工具调用
2. 恢复时应直接消费这个 `pending_tool_call`
3. 不应让模型重新“决定要不要再调一次”

### 5. Decision Cache 只做优化，不做主正确性

cache 可以：

- 复用相同语义决策
- 减少重复提问

但 cache 不能承担：

- interrupt 主实例身份
- 原始待执行动作绑定
- consume / idempotency 主语义

### 6. 格式修复中间件不能破坏运行时语义

像 `DanglingToolCallMiddleware` 这样的组件只能做：

- 格式补全
- 安全降级

不能隐式改变：

- interrupt 生命周期
- side-effect 工具是否应继续执行

## Proposed Steady-State Model

## Top-Level Task State

保留当前顶层状态集合：

- `PENDING`
- `RUNNING`
- `WAITING_DEPENDENCY`
- `WAITING_INTERVENTION`
- `DONE`
- `FAILED`

不新增新的顶层 task status。

## Interrupt Taxonomy

在 `WAITING_INTERVENTION` 下，明确区分 interrupt 来源：

1. `before_tool`
   - middleware 在 risky tool 调用前拦截
2. `clarification`
   - 用户补信息
3. `selection`
   - 用户从候选项中选择
4. `confirmation`
   - 用户确认继续某个非工具型决策

其中：

- `clarification / selection / confirmation` 可以继续沿用现有 `intervention_type = "clarification"` 的兼容表示
- 但运行时内部需要再区分一个更清晰的 `interrupt_kind`

## Required Runtime Identity Layers

长期稳态建议区分三层 identity：

1. `request_id`
   - 面向前端 resolve API 的当前请求实例 ID
2. `interrupt_fingerprint`
   - 当前 interrupt 的实例指纹
3. `semantic_key`
   - 跨 run / 重试复用的稳定语义键

建议职责：

- `request_id`：短生命周期，用于一次提交
- `interrupt_fingerprint`：实例级幂等与 stale check
- `semantic_key`：cache reuse 与“是否同语义动作”的判断

当前代码里的 `fingerprint` 既承担实例去重，又承担复用语义，职责过重，建议拆分。

## Proposed State Extensions

在现有 `TaskStatus` 基础上，建议增强以下字段语义。

### `pending_interrupt`

当前已有 `pending_interrupt`，建议扩展为：

```ts
{
  interrupt_type: "dependency" | "clarification" | "intervention";
  interrupt_kind?: "before_tool" | "clarification" | "selection" | "confirmation";
  request_id?: string;
  fingerprint?: string;
  semantic_key?: string;
  source_signal?: "intervention_required" | "request_help" | "ask_clarification";
  source_agent?: string;
  created_at?: string;
}
```

### `pending_tool_call`

当前已有 `pending_tool_call`，建议增强为：

```ts
{
  tool_name: string;
  tool_args: Record<string, unknown>;
  tool_call_id?: string;
  idempotency_key?: string;
  source_agent: string;
  source_task_id: string;
  snapshot_hash?: string;
  interrupt_fingerprint?: string;
}
```

### `intervention_resolution`

当前已存在，建议在写回时确保至少可追溯：

```ts
{
  request_id: string;
  fingerprint: string;
  action_key: string;
  payload: Record<string, unknown>;
  resolution_behavior: string;
}
```

同时 `resolved_inputs["intervention_resolution"]` 不应再只保留简化版本。

## Runtime Precedence Rules

executor 对本轮 agent 新消息的归一化优先级应固定为：

1. `intervention_required`
2. 用户型 `request_help`
3. `ask_clarification`
4. 系统型 `request_help`
5. `task_complete / final_output / fail`

更具体地说：

1. 如果同一消息切片里既有 `intervention_required` 又有 `request_help`
   - 优先采用 `intervention_required`
   - 后续 `request_help` 视为噪音或 agent follow-up，不得再生成第二张卡
2. 如果有多个 `intervention_required`
   - 按 `fingerprint` 或 `tool_call_id` 去重
   - 只保留一个 authoritative interrupt
3. 只有在没有 framework interrupt 的情况下，才允许 `request_help(user_*)` 变成主 interrupt

## Resume Lanes

长期稳态需要把恢复路径明确分成两条。

### Lane A: `resume_tool_call`

适用于：

- `before_tool`
- 已存在 `pending_tool_call`

要求：

1. 用户确认后直接执行原始 `pending_tool_call`
2. 工具执行前先校验：
   - interrupt 仍为 `resolved`
   - `pending_tool_call` 未漂移
   - snapshot_hash 一致
3. 一旦开始消费，状态进入 `consuming`
4. 执行完成后标记 `consumed`

### Lane B: `continue_after_intervention`

适用于：

- clarification
- selection
- 非工具型 confirmation

要求：

1. 用户答案作为结构化输入注入恢复上下文
2. 恢复时允许继续调用 agent
3. 但不能继承原始 `pending_tool_call`

当前代码里的 `continue_after_dependency` 在用户型 intervention 上仍有复用，这不够清晰。长期建议引入：

- `continue_after_intervention`

短期兼容可保留老值，但新主链路应逐步迁移。

## Decision Cache Role In Steady State

Decision Cache 保留，但定位需要明确：

### Cache Should Do

1. 当语义完全相同的问题再次出现时，自动复用已有答案
2. 减少重复提问
3. 提供 `[Cache HIT] / [Cache EXPIRED]` 观测能力

### Cache Should Not Do

1. 代替 interrupt 实例身份
2. 代替 `pending_tool_call` 消费
3. 代替 resolve 幂等语义
4. 掩盖参数漂移

### Tool Confirmation Cache Policy

对 `before_tool` 建议采用更保守策略：

1. 默认 `max_reuse = 1`
2. 只有显式策略声明允许时再放宽

理由：

- 工具确认有副作用
- 多次自动复用虽然减少询问，但容易弱化用户对真实执行次数的感知

## Dangling Tool Call Safety Rule

当前 `DanglingToolCallMiddleware` 不能直接删，但要增加 interrupt-aware 安全边界。

建议规则：

1. 对无副作用工具：
   - 允许继续补洞
2. 对有副作用、且已存在 `pending_interrupt` 的工具调用：
   - 不再简单注入 error placeholder 后继续喂给模型
   - 应直接短路回：
     - `WAITING_INTERVENTION`
     - 或 `blocked_by_pending_interrupt`

原则是：

- 格式修复不能隐式触发同语义副作用工具的再次生成

## Backend Changes

### Phase 1: Correctness Barrier

目标：先让当前重复确认问题彻底消失。

1. executor 在 outcome normalization 阶段增加严格优先级：
   - `intervention_required > request_help(user_*) > ask_clarification > request_help(system_*)`
2. `intervention_required` 一旦命中：
   - 必须直接落 `WAITING_INTERVENTION`
   - 必须写入 `pending_tool_call`
   - 必须使用 `resume_tool_call`
3. 同一批消息里出现后续 `request_help` 时：
   - 不再覆盖 framework interrupt
4. 增加日志：
   - `authoritative_interrupt_selected`
   - `suppressed_followup_request_help`

涉及文件：

- `backend/src/agents/executor/executor.py`

### Phase 2: Protocol Hardening

目标：让 interrupt 生命周期跨 run 稳定。

1. 拆分实例指纹与语义键
2. 增强 `pending_interrupt` 与 `pending_tool_call`
3. gateway resolve 和 `workflow_resume.resolve_intervention` 统一写回：
   - `request_id`
   - `fingerprint`
   - `resolution_behavior`
   - 结构化 resolution context
4. 新主链路逐步引入：
   - `continue_after_intervention`

涉及文件：

- `backend/src/agents/thread_state.py`
- `backend/src/gateway/routers/interventions.py`
- `backend/src/agents/workflow_resume.py`
- `backend/src/agents/intervention/decision_cache.py`
- `backend/src/agents/intervention/fingerprint.py`

### Phase 3: Middleware Safety

目标：减少“补洞后再重复发起风险动作”。

1. `DanglingToolCallMiddleware` 增加 risky tool + pending interrupt 感知
2. `InterventionMiddleware` 去重逻辑改为：
   - 实例级检查
   - 语义级缓存复用
3. 明确 tool confirmation 的默认 reuse policy

涉及文件：

- `backend/src/agents/middlewares/dangling_tool_call_middleware.py`
- `backend/src/agents/middlewares/intervention_middleware.py`
- `backend/src/agents/lead_agent/agent.py`

## Frontend Changes

前端不需要等待协议大改完才行动，但需要对“authoritative interrupt only”有明确配合。

### Phase 1

1. 同一 task 同一时刻只展示一个 authoritative intervention card
2. 当线程已进入 `WAITING_INTERVENTION` 时，不再用其他补充提示覆盖主卡
3. resolve 后等待 authoritative 回写，而不是本地生成第二套“继续中”语义

### Phase 2

1. 若后端补充 `interrupt_kind / semantic_key / source_signal`
   - 前端只做展示增强，不做业务判断
2. intervention card 继续复用当前 schema-driven 架构
3. display projection 仍由 backend authoritative 下发

涉及文件：

- `frontend/src/core/threads/types.ts`
- `frontend/src/core/threads/hooks.ts`
- `frontend/src/components/workspace/messages/intervention/*`

## Testing Strategy

长期稳态必须把测试从“是否能弹卡”提升到“生命周期是否稳定”。

### Backend Unit Tests

1. 同一消息切片里同时出现 `intervention_required` 和 `request_help`
   - 断言 executor 只选前者
2. `before_tool` resolve 后走 `resume_tool_call`
   - 断言直接执行 `pending_tool_call`
3. 重复 resolve 同一 interrupt
   - 断言工具只执行一次
4. 新 run 恢复后
   - 不会再弹第二张同语义确认卡
5. `DanglingToolCallMiddleware`
   - 对 risky tool + pending interrupt 不再放任模型继续重试

### Integration Tests

1. 会议预定前确认
2. 用户选择会议室后继续执行
3. 用户确认后失败重试
4. 页面刷新后恢复
5. 断线重连后恢复

### Log Assertions

新增结构化观测点：

1. `interrupt_created`
2. `interrupt_selected_as_authoritative`
3. `interrupt_resolution_persisted`
4. `interrupt_consuming`
5. `interrupt_consumed`
6. `interrupt_followup_suppressed`
7. `cache_hit`
8. `cache_expired`

## Risks

1. 如果 Phase 1 只做 executor 优先级，不同步校正 resume lane，仍可能在边角路径上重复确认
2. 如果实例指纹与语义键拆分不清，可能引入新的 stale / dedup 混乱
3. 如果 `DanglingToolCallMiddleware` 改得过于保守，可能让部分无害场景恢复能力下降
4. 如果工具确认默认无限复用，可能降低用户对副作用执行次数的控制感

## Acceptance Criteria

### Runtime Correctness

1. 同一副作用工具调用在一次 workflow 生命周期里最多只生成一个 authoritative interrupt
2. 用户确认后，恢复路径直接消费原始 `pending_tool_call`
3. 用户重复点击确认，工具只执行一次
4. 同语义动作跨 run 恢复时不会再次弹出重复确认卡

### State Semantics

1. `WAITING_INTERVENTION` 始终只表示“等待用户解除阻塞”
2. `before_tool` 与 `clarification` 共享顶层状态，但恢复路径清晰区分
3. cache miss 不影响 correctness，cache hit 只带来体验优化

### UX

1. 前端同一时刻只出现一张 authoritative intervention card
2. 用户不会看到“先确认一次，恢复后又确认一次”的重复体验
3. 刷新与重连后仍能恢复到同一个 interrupt 实例

## Recommended Implementation Order

1. 先做 Phase 1
   - executor authoritative interrupt 归一
   - `resume_tool_call` 成为 `before_tool` 的唯一主恢复路径
2. 再做 Phase 2
   - interrupt identity 拆分
   - gateway / workflow_resume 写回增强
3. 最后做 Phase 3
   - dangling tool safety
   - cache policy 收紧
   - observability 收口

原因：

1. Phase 1 直接解决最新日志中已经出现的重复确认问题
2. Phase 2 才把它从“修 bug”提升为“长期稳态协议”
3. Phase 3 再处理中间件副作用和运行治理

## Open Questions

1. `interrupt_fingerprint` 与 `semantic_key` 是否要在对外 API 中同时暴露，还是先只在后端内部拆分
2. `continue_after_dependency` 是否先兼容保留，再逐步迁移到 `continue_after_intervention`
3. 前端是否需要一个极简的“已自动复用上次决策”提示，还是完全保持无感

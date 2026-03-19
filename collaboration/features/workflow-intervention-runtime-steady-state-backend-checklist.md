# Workflow Intervention Runtime Steady State Backend Checklist

- Status: `draft`
- Owner: `backend`
- Related feature: `workflow-intervention-runtime-steady-state.md`

## 0. 使用前提

本实施文档只面向后端。

后端实现时必须先以主文档
`collaboration/features/workflow-intervention-runtime-steady-state.md`
为准，再按本清单执行。

本清单的目的不是重复主文档，而是把后端该做的事情收敛成：

1. 明确的改动范围
2. 明确的阶段顺序
3. 明确的完成判定
4. 明确的不做事项

如果开发过程中发现前端需要额外配合，而主文档里没有明确写出：

- 不自行假设前端行为
- 记录到 `collaboration/handoffs/backend-to-frontend.md`

## 1. 当前代码基线确认

- [ ] 先确认当前代码已经具备以下能力，不重复造轮子：
  - `TaskStatus` 已有 `WAITING_INTERVENTION`
  - `TaskStatus` 已有 `intervention_request / intervention_status / intervention_fingerprint / intervention_resolution`
  - `TaskStatus` 已有 `continuation_mode / pending_interrupt / pending_tool_call`
  - executor 已有 `request_intervention` 分支
  - executor 已有 `resume_tool_call` 分支
  - gateway 已有 `POST /api/threads/{thread_id}/interventions/{request_id}:resolve`
  - `intervention_cache` 已存在
- [ ] 确认以下文件是本次主要实现落点：
  - `backend/src/agents/executor/executor.py`
  - `backend/src/agents/thread_state.py`
  - `backend/src/gateway/routers/interventions.py`
  - `backend/src/agents/workflow_resume.py`
  - `backend/src/agents/middlewares/intervention_middleware.py`
  - `backend/src/agents/middlewares/dangling_tool_call_middleware.py`
  - `backend/src/agents/intervention/fingerprint.py`
  - `backend/src/agents/intervention/decision_cache.py`
- Done when:
  - 开发者对“当前已有能力”和“本次只是在其上收口”没有歧义

## 2. 冻结本次后端范围

- [ ] 本次后端必须实现：
  - executor 对 interrupt 信号的 authoritative 归一
  - `before_tool` 恢复路径稳定收口到 `resume_tool_call`
  - interrupt 实例身份与语义键职责拆分
  - resolve 写回语义增强
  - `DanglingToolCallMiddleware` 对 risky tool + pending interrupt 的安全边界
  - 结构化日志补齐
- [ ] 本次后端明确不做：
  - 新增顶层 task status
  - 改前端 resolve API 路径
  - 改 display projection 业务文案
  - 改业务 agent prompt 规则
  - 引入跨 thread 全局缓存
  - 引入新的前端展示协议作为前置条件
- [ ] 本次后端不得依赖：
  - “模型恢复后会再次生成同一个 tool call”
  - “前端只会点一次确认”
  - “缓存命中就一定正确”
- Done when:
  - 范围边界清晰，后端实现不会扩散到主文档未授权的方向

## 3. Phase 1：Executor Authoritative Interrupt 归一

### 3.1 固定归一化优先级

- [ ] 修改 `backend/src/agents/executor/executor.py`
- [ ] 将 executor 对本轮新消息的 interrupt 归一优先级固定为：
  - `intervention_required`
  - 用户型 `request_help`
  - `ask_clarification`
  - 系统型 `request_help`
  - 其他结果
- [ ] 不能再使用“最后一个 terminal signal 赢”的弱规则作为主逻辑
- [ ] 如果同一批新消息同时包含：
  - `intervention_required`
  - `request_help(user_confirmation / user_clarification / user_multi_select)`
  则必须以 `intervention_required` 为 authoritative interrupt
- [ ] 同一批新消息中的后续 `request_help` 必须被视为 follow-up 噪音，不得再生成第二张 intervention card
- Done when:
  - executor 对 framework interrupt 与 agent follow-up 的优先级完全确定

### 3.2 收紧 `request_intervention` 落库行为

- [ ] 当 authoritative signal 为 `intervention_required` 时，executor 必须直接写入：
  - `status = WAITING_INTERVENTION`
  - `status_detail = @waiting_intervention`
  - `intervention_request`
  - `intervention_status = pending`
  - `intervention_fingerprint`
  - `pending_interrupt`
  - `pending_tool_call`
  - `continuation_mode = "resume_tool_call"`
- [ ] `pending_tool_call` 必须优先来自 authoritative tool interrupt 对应的原始 tool call
- [ ] 如果消息切片中存在多个同语义 `intervention_required`：
  - 按 `fingerprint` 或 `tool_call_id` 去重
  - 只保留一个 authoritative interrupt
- Done when:
  - 同一轮工具确认只会产生一个 authoritative interrupt 实例

### 3.3 `before_tool` 恢复主路径唯一化

- [ ] 确认 `before_tool` 的恢复主路径只有一条：
  - resolve
  - `status = RUNNING`
  - `continuation_mode = resume_tool_call`
  - 直接执行 `pending_tool_call`
- [ ] 不允许 `before_tool` 在主链路上回退到：
  - `continue_after_dependency`
  - 或“恢复后重新让 agent 续跑并再次决定是否调用工具”
- [ ] 如果当前存在仍可能落到“继续让 agent 跑”的边角路径：
  - 要在 executor 中显式兜底矫正
- Done when:
  - `before_tool` 确认后一定直接消费原始待执行动作

### 3.4 新增 Phase 1 日志

- [ ] 在 executor 增加结构化日志：
  - `authoritative_interrupt_selected`
  - `suppressed_followup_request_help`
- [ ] 日志至少包含：
  - `run_id`
  - `task_id`
  - `agent_name`
  - `source_signal`
  - `intervention_request_id`
  - `fingerprint`
- Done when:
  - 仅看日志就能判断本轮 interrupt 是谁胜出、谁被抑制

## 4. Phase 2：Protocol Hardening

### 4.1 拆分 interrupt identity 层

- [ ] 当前 `fingerprint` 同时承担：
  - 当前实例校验
  - 语义复用
  职责过重，本次要拆清
- [ ] 在不破坏现有 API 的前提下，后端内部至少要明确区分：
  - `request_id`
  - `interrupt_fingerprint`
  - `semantic_key`
- [ ] 可以先保持对外字段仍叫 `fingerprint`，但在后端内部必须明确：
  - 什么是实例身份
  - 什么是 cache 语义键
- [ ] 若需要新增内部字段，优先挂到：
  - `pending_interrupt`
  - `pending_tool_call`
- Done when:
  - 实例校验与语义复用不再共享同一份模糊职责

### 4.2 增强 `pending_interrupt`

- [ ] 修改 `backend/src/agents/thread_state.py`
- [ ] 在当前 `pending_interrupt` 语义基础上，补充或约定以下字段：
  - `interrupt_kind`
  - `semantic_key`
  - `source_signal`
  - `source_agent`
- [ ] `interrupt_kind` 至少能区分：
  - `before_tool`
  - `clarification`
  - `selection`
  - `confirmation`
- [ ] 这里可以先以 Optional 字段方式落地，避免大面积破坏旧 checkpoint
- Done when:
  - 任一活跃 interrupt 的来源与类型都能从 task state 中读清

### 4.3 增强 `pending_tool_call`

- [ ] 修改 `backend/src/agents/thread_state.py`
- [ ] 为 `pending_tool_call` 增强内部语义字段：
  - `snapshot_hash`
  - `interrupt_fingerprint`
- [ ] `snapshot_hash` 用于恢复前校验“用户确认的还是不是这一个动作”
- [ ] `interrupt_fingerprint` 用于将待执行工具调用与 interrupt 实例绑定
- Done when:
  - `pending_tool_call` 足以支持恢复前一致性校验

### 4.4 resolve 写回语义增强

- [ ] 修改 `backend/src/gateway/routers/interventions.py`
- [ ] 修改 `backend/src/agents/workflow_resume.py`
- [ ] resolve 后写回时，不能只保留：
  - `action_key`
  - `payload`
  - `resolution_behavior`
- [ ] 必须确保以下语义可追溯：
  - `request_id`
  - `fingerprint`
  - `resolution_behavior`
  - 该 resolution 对应的是哪个 pending interrupt
- [ ] `resolved_inputs["intervention_resolution"]` 的写回内容必须与 `task.intervention_resolution` 保持一致语义，不能一份全量一份缩水到只剩动作值
- Done when:
  - 任意一次 resolve 的来源、动作与恢复语义都可稳定追溯

### 4.5 引入更清晰的用户型恢复语义

- [ ] 对非 `before_tool` 的用户型 intervention，逐步从：
  - `continue_after_dependency`
  迁移到：
  - `continue_after_intervention`
- [ ] Phase 2 可先兼容保留旧值，但新主链路不再继续扩大 `continue_after_dependency` 的语义
- [ ] 如果暂时无法一次性改完：
  - 至少在代码注释和日志里明确它是兼容值，不是推荐长期值
- Done when:
  - “等系统依赖恢复”和“等用户输入恢复”不再共享同一 continuation 语义

## 5. Phase 3：Middleware Safety

### 5.1 收紧 `InterventionMiddleware` 去重职责

- [ ] 修改 `backend/src/agents/middlewares/intervention_middleware.py`
- [ ] 去重逻辑拆成两层：
  - 实例级：当前 interrupt 是否已 resolved / consumed
  - 语义级：是否存在可复用 cache
- [ ] `resolved_fingerprints` 不再被当作长期稳态主正确性，只作为当前执行上下文的辅助去重
- [ ] `intervention_cache` 继续保留，但只负责复用优化
- Done when:
  - middleware 的去重与 cache 语义不再混淆

### 5.2 收紧 `DanglingToolCallMiddleware`

- [ ] 修改 `backend/src/agents/middlewares/dangling_tool_call_middleware.py`
- [ ] 当前补洞逻辑只按消息格式工作，不区分副作用工具与普通工具
- [ ] 本次要增加规则：
  - 对无副作用工具，允许现有 placeholder patch
  - 对 risky tool，且线程中已存在 active pending interrupt 时，不得继续用 placeholder 方式让模型在同一历史上重试
- [ ] 推荐结果：
  - 返回安全降级
  - 或短路回既有 interrupt
  - 但不能继续放任模型生成第二轮同语义风险动作
- [ ] 如果需要从 middleware 读取更多上下文：
  - 只能通过现有 config/state 机制补齐
  - 不引入新的跨层黑盒依赖
- Done when:
  - 补洞逻辑不会再成为重复确认的放大器

### 5.3 收紧工具确认 cache 策略

- [ ] 修改 `backend/src/agents/intervention/decision_cache.py`
- [ ] 对 `before_tool` 默认采用更保守复用策略：
  - 默认 `max_reuse = 1`
  - 仅当显式策略允许时才更高
- [ ] 不调整 `clarification` 的主策略，除非有明确主文档结论
- Done when:
  - tool confirmation cache 保留体验收益，但不会无限削弱用户对副作用执行次数的控制

## 6. 明确本次后端不修改的内容

- [ ] 不修改前端 intervention card 文案结构
- [ ] 不新增新的 resolve endpoint
- [ ] 不引入新的顶层线程状态机分支
- [ ] 不把业务场景特判硬编码进 framework：
  - 不加“会议预定成功就直接结束”之类业务捷径
- [ ] 不依赖 agent prompt 调整作为主修复手段
- Done when:
  - 后端修复仍然是 framework-level 方案，而不是业务 patch

## 7. Backend Delivery Order

- [ ] 按以下顺序开发，不要打乱：
  1. Phase 1：executor authoritative interrupt 归一
  2. Phase 1：`before_tool` 唯一恢复主路径
  3. Phase 2：interrupt identity 与 resolve 写回增强
  4. Phase 3：middleware safety 与 cache policy 收口
- [ ] 每完成一个 Phase 后先补对应测试，再继续下一阶段
- Done when:
  - 实施过程可回滚、可验证，不是一次性大爆改

## 8. Backend Self-Check

- [ ] 同一轮消息里 `intervention_required` 与 `request_help` 同时出现时，只会落一张 authoritative card
- [ ] `before_tool` resolve 后一定直接执行原始 `pending_tool_call`
- [ ] 用户重复点击确认，工具只执行一次
- [ ] 同语义动作跨 run 恢复时不会再次生成新的 authoritative interrupt
- [ ] `DanglingToolCallMiddleware` 不会继续放大 risky tool 重试
- [ ] 结构化日志已能完整追踪 interrupt 生命周期

## 9. 最终后端交付判定

- [ ] 主文档中定义的 Phase 1/2/3 后端范围均已实现或明确记录未完成项
- [ ] 所有后端代码改动均在本清单声明的文件范围内，未出现未说明的额外扩散
- [ ] 如有前端依赖变更，已写入 `collaboration/handoffs/backend-to-frontend.md`
- [ ] 相关测试已由测试文档要求覆盖，并全部通过

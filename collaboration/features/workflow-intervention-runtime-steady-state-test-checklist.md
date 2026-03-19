# Workflow Intervention Runtime Steady State Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature: `workflow-intervention-runtime-steady-state.md`

## 0. 测试定位

本实施文档只面向测试。

目标不是“补几个回归用例”，而是验证 intervention 运行时是否已经达到长期稳态要求：

1. authoritative interrupt 选择稳定
2. `before_tool` 恢复路径稳定
3. resolve 幂等
4. 跨 run / 重试 / 刷新 / 重连稳定
5. cache 只做优化，不影响 correctness

测试实现时必须先阅读：

- `collaboration/features/workflow-intervention-runtime-steady-state.md`
- `collaboration/features/workflow-intervention-runtime-steady-state-backend-checklist.md`

如果测试中发现主文档未覆盖但会影响前端呈现或接口契约：

- 不自行扩大理解
- 记录到 `collaboration/handoffs/backend-to-frontend.md`

## 1. 当前代码基线确认

- [ ] 确认当前已有测试覆盖基础 intervention 能力，但还不足以证明“长期稳态”
- [ ] 重点回看现有相关测试：
  - `backend/tests/test_interventions_router.py`
  - `backend/tests/test_executor_intervention_normalization.py`
  - `backend/tests/test_help_request_builder.py`
  - `backend/tests/test_multi_agent_core.py`
  - `backend/tests/test_multi_agent_graph.py`
- [ ] 本次新增与扩展测试要覆盖的是：
  - authoritative interrupt 选择
  - `resume_tool_call` 正确性
  - interrupt lifecycle
  - risky tool dangling patch 安全边界
  - 重复确认不再出现
- Done when:
  - 测试目标明确，不会误把旧有“能跑通”当成“已经稳态”

## 2. 测试范围冻结

- [ ] 本次测试必须覆盖：
  - Phase 1 correctness barrier
  - Phase 2 protocol hardening 的核心行为
  - Phase 3 middleware safety 的关键边界
- [ ] 本次测试不强制覆盖：
  - 前端视觉细节
  - display projection 文案本身
  - 业务 prompt 优化
- [ ] 但必须覆盖以下用户感知结果：
  - 不会确认两次
  - 确认后不会重复执行
  - 刷新/重连后不会出现新的冲突 interrupt
- Done when:
  - 测试目标直接对应主文档验收标准

## 3. Phase 1：Authoritative Interrupt 归一测试

### 3.1 同批消息内的优先级测试

- [ ] 新增或扩展 executor 单测，构造同一批 agent 新消息同时包含：
  - `ToolMessage(name="intervention_required")`
  - `ToolMessage(name="request_help")`
- [ ] 断言：
  - executor 选择 `intervention_required`
  - task 落为 `WAITING_INTERVENTION`
  - `continuation_mode == "resume_tool_call"`
  - `pending_tool_call` 被写入
  - `request_help` 不会再生成第二张 intervention
- [ ] 再补一组用例：
  - `intervention_required + ask_clarification`
  - 断言仍以 `intervention_required` 为 authoritative
- Done when:
  - framework interrupt 优先级在测试中是硬约束，不依赖人工读日志

### 3.2 多个 `intervention_required` 去重测试

- [ ] 构造同一批消息中多个 `intervention_required`
- [ ] 覆盖：
  - 同 `fingerprint`
  - 同 `tool_call_id`
  - 不同 `request_id`
- [ ] 断言：
  - 最终只写入一个 active interrupt
  - 不会产生多张并发确认卡语义
- Done when:
  - executor 的 authoritative interrupt 去重行为确定

### 3.3 follow-up `request_help` 抑制测试

- [ ] 构造：
  1. 先出现 `intervention_required`
  2. 后出现 `request_help(user_confirmation)`
- [ ] 断言：
  - follow-up `request_help` 被抑制
  - 不改变主 interrupt
  - 不覆盖 `pending_tool_call`
- Done when:
  - “两套确认机制打架”在测试中被明确禁止

## 4. Phase 1：`resume_tool_call` 主正确性测试

### 4.1 resolve 后直接执行原始待执行动作

- [ ] 覆盖 `before_tool` 典型链路：
  1. 触发 `intervention_required`
  2. task 落为 `WAITING_INTERVENTION`
  3. resolve 写回
  4. executor 恢复执行
- [ ] 断言：
  - 进入 `resume_tool_call`
  - 直接执行 `pending_tool_call`
  - 不再重新让 agent 生成一次 tool call
- Done when:
  - “确认后消费原始动作”成为测试中的刚性要求

### 4.2 重复 resolve 幂等测试

- [ ] 对同一 interrupt 连续提交两次 resolve
- [ ] 断言：
  - 第一次 resolve 正常
  - 第二次不会导致工具重复执行
  - interrupt 最终状态正确停留在 `consumed` 或等效终态
- Done when:
  - 用户重复点击确认不会导致重复副作用

### 4.3 `before_tool` 不回退到 continue-after-agent 流程

- [ ] 增加测试覆盖：
  - resolve 后不会走 `continue_after_dependency`
  - 不会落入“恢复后再让 agent 再想一遍”
- Done when:
  - `before_tool` 主链路恢复语义唯一明确

## 5. Phase 2：Interrupt Identity 与 Resolve 语义测试

### 5.1 interrupt identity 字段一致性测试

- [ ] 若实现中新增或增强以下字段：
  - `interrupt_kind`
  - `semantic_key`
  - `source_signal`
  - `snapshot_hash`
  则必须为每个字段补测试
- [ ] 断言：
  - `pending_interrupt` 与 `pending_tool_call` 的 identity 字段一致
  - resolve 后这些字段仍可追溯
- Done when:
  - interrupt 实例身份链条在测试中可验证

### 5.2 gateway resolve 与 workflow_resume 语义一致性测试

- [ ] 覆盖 gateway resolve 路径：
  - `backend/src/gateway/routers/interventions.py`
- [ ] 覆盖 `workflow_resume.resolve_intervention`
- [ ] 断言两者对以下字段的写回语义一致：
  - `request_id`
  - `fingerprint`
  - `action_key`
  - `payload`
  - `resolution_behavior`
  - `intervention_status`
- [ ] 断言 `resolved_inputs["intervention_resolution"]` 不再丢失关键身份信息
- Done when:
  - resolve 结果在不同入口下具有同一语义

### 5.3 `continue_after_intervention` 兼容迁移测试

- [ ] 如果实现新增了 `continue_after_intervention`
  - 验证新值主链路可用
- [ ] 如果仍兼容 `continue_after_dependency`
  - 验证兼容值仅作为旧路径保留，不影响新路径判断
- Done when:
  - 用户型 intervention 恢复语义在测试里不再和 dependency 混淆

## 6. Phase 3：Dangling Tool Safety 测试

### 6.1 无副作用工具补洞不回归

- [ ] 为 `DanglingToolCallMiddleware` 增加测试：
  - 普通 read-only tool call 缺失 `ToolMessage` 时，仍可正常补洞
- Done when:
  - 格式修复能力保留

### 6.2 risky tool + pending interrupt 不得继续放大

- [ ] 构造场景：
  - 历史消息中存在 risky tool 的悬空调用
  - 当前 task 已有 active pending interrupt
- [ ] 断言：
  - middleware 不会继续让模型在补洞后再生成一轮同语义 risky tool
  - 不会再次触发新的重复确认
- Done when:
  - dangling patch 不再是重复确认放大器

## 7. Decision Cache 定位测试

### 7.1 cache hit 只优化体验，不影响 correctness

- [ ] 对 `clarification / selection / confirmation`：
  - cache hit 时自动复用
  - cache miss 时仍能正确进入 intervention
- [ ] 对 `before_tool`：
  - cache hit 只在允许范围内生效
  - cache miss 不影响主正确性
- Done when:
  - cache 的角色在测试里被清晰约束为“优化层”

### 7.2 tool confirmation 默认复用策略测试

- [ ] 若后端将 `before_tool` 默认 `max_reuse` 调整为更保守值：
  - 验证达到上限后会重新确认
- [ ] 验证 cache policy 不会导致同一工具副作用被无限静默复用
- Done when:
  - tool confirmation cache 与长期稳态设计一致

## 8. 日志级断言

- [ ] 为新增结构化日志补断言或 smoke 检查：
  - `authoritative_interrupt_selected`
  - `suppressed_followup_request_help`
  - `interrupt_created`
  - `interrupt_resolution_persisted`
  - `interrupt_consuming`
  - `interrupt_consumed`
  - `cache_hit`
  - `cache_expired`
- [ ] 至少保证关键主链路有可搜索日志：
  - “谁被选为 authoritative interrupt”
  - “谁被 suppress 了”
  - “原始待执行工具什么时候被 consume”
- Done when:
  - 线上或本地复现时，仅靠日志即可复盘 interrupt 生命周期

## 9. 集成场景测试

### 9.1 会议预定前确认

- [ ] 按真实主链路覆盖：
  1. 用户发起预定
  2. contacts helper 完成
  3. 选择会议室
  4. 工具前确认
  5. resolve
  6. 直接执行 `meeting_createMeeting`
- [ ] 断言：
  - 不出现第二张确认卡
  - 不出现重复 `meeting_createMeeting` 执行
- Done when:
  - 已知问题场景不再回归

### 9.2 用户选择后失败重试

- [ ] 覆盖：
  1. 用户完成选择
  2. 后续执行失败
  3. 系统重试
- [ ] 断言：
  - 如果语义相同且允许复用，则自动复用，不重新询问
  - 如果语义已变化，则产生新的正确 interrupt
- Done when:
  - “重试后重复询问”问题被稳定控制

### 9.3 刷新与重连恢复

- [ ] 在 pending intervention 时刷新
- [ ] 在 resolve 之后、consume 之前模拟重连
- [ ] 断言：
  - 恢复到同一个 interrupt 实例
  - 不会生成新的冲突 interrupt
- Done when:
  - 长期稳态的 refresh / reconnect 语义可验证

## 10. 回归范围

- [ ] 重新运行并扩展以下回归：
  - `backend/tests/test_interventions_router.py`
  - `backend/tests/test_executor_intervention_normalization.py`
  - `backend/tests/test_help_request_builder.py`
  - `backend/tests/test_multi_agent_core.py`
  - `backend/tests/test_multi_agent_graph.py`
- [ ] 如果新增测试文件，建议命名清晰对应本特性，例如：
  - `backend/tests/test_intervention_runtime_steady_state.py`
  - `backend/tests/test_dangling_tool_interrupt_safety.py`
- Done when:
  - 新测试与旧测试共同证明：不是只修一条链路，而是 runtime 语义整体更稳

## 11. 测试结论输出要求

- [ ] 测试完成后，输出必须按以下结构汇总：
  1. 已覆盖的 Phase
  2. 通过的核心场景
  3. 未覆盖或存在风险的边界
  4. 是否发现新的前后端 handoff
- [ ] 如发现主文档与实现不一致：
  - 先标注为测试阻塞或实现偏差
  - 不自行改写需求定义
- Done when:
  - 测试结论可直接反哺主文档与联调，不需要二次口头解释

## 12. 最终测试交付判定

- [ ] authoritative interrupt 选择已被自动化测试锁死
- [ ] `before_tool -> resolve -> resume_tool_call -> consume` 主链路已被自动化测试锁死
- [ ] 重复点击确认不会重复执行已被自动化测试锁死
- [ ] 刷新、重连、重试场景至少有一条集成测试或高可信回归覆盖
- [ ] risky tool dangling patch 的安全边界已被验证
- [ ] 如有前端协作问题，已写入 `collaboration/handoffs/backend-to-frontend.md`

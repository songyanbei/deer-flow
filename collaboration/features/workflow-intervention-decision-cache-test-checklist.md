# Workflow Intervention Decision Cache Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature: `workflow-intervention-decision-cache.md`
- Test files:
  - 新增：`backend/tests/test_intervention_decision_cache.py`
  - 回归：`backend/tests/test_interventions_router.py`
  - 回归：`backend/tests/test_multi_agent_core.py`
  - 回归：`backend/tests/test_multi_agent_graph.py`
  - 回归：`backend/tests/test_executor_intervention_normalization.py`
  - 回归：`backend/tests/test_help_request_builder.py`

## 1. 语义指纹确定性验证

- [ ] 工具干预指纹：相同 `(agent_name, tool_name, tool_args)` 产生相同指纹
  ```python
  def test_tool_semantic_fingerprint_deterministic():
      fp1 = generate_tool_semantic_fingerprint("meeting-agent", "book_room", {"room": "A301"})
      fp2 = generate_tool_semantic_fingerprint("meeting-agent", "book_room", {"room": "A301"})
      assert fp1 == fp2
  ```
- [ ] 工具干预指纹：不同参数产生不同指纹
  ```python
  def test_tool_semantic_fingerprint_differs_on_args():
      fp1 = generate_tool_semantic_fingerprint("meeting-agent", "book_room", {"room": "A301"})
      fp2 = generate_tool_semantic_fingerprint("meeting-agent", "book_room", {"room": "B502"})
      assert fp1 != fp2
  ```
- [ ] 工具干预指纹：不同 agent 产生不同指纹
  ```python
  def test_tool_semantic_fingerprint_differs_on_agent():
      fp1 = generate_tool_semantic_fingerprint("meeting-agent", "book_room", {"room": "A301"})
      fp2 = generate_tool_semantic_fingerprint("calendar-agent", "book_room", {"room": "A301"})
      assert fp1 != fp2
  ```
- [ ] 工具干预指纹：参数 key 顺序不影响指纹
  ```python
  def test_tool_semantic_fingerprint_key_order_invariant():
      fp1 = generate_tool_semantic_fingerprint("agent", "tool", {"a": 1, "b": 2})
      fp2 = generate_tool_semantic_fingerprint("agent", "tool", {"b": 2, "a": 1})
      assert fp1 == fp2
  ```
- [ ] 用户澄清指纹：相同 `(agent_name, question, options)` 产生相同指纹
  ```python
  def test_clarification_semantic_fingerprint_deterministic():
      fp1 = generate_clarification_semantic_fingerprint(
          "meeting-agent", "请选择会议室", ["A301", "B502", "C103"]
      )
      fp2 = generate_clarification_semantic_fingerprint(
          "meeting-agent", "请选择会议室", ["A301", "B502", "C103"]
      )
      assert fp1 == fp2
  ```
- [ ] 用户澄清指纹：选项顺序不影响指纹
  ```python
  def test_clarification_semantic_fingerprint_option_order_invariant():
      fp1 = generate_clarification_semantic_fingerprint(
          "meeting-agent", "请选择会议室", ["A301", "B502"]
      )
      fp2 = generate_clarification_semantic_fingerprint(
          "meeting-agent", "请选择会议室", ["B502", "A301"]
      )
      assert fp1 == fp2
  ```
- [ ] 用户澄清指纹：问题文本前后空格不影响指纹
  ```python
  def test_clarification_semantic_fingerprint_strips_whitespace():
      fp1 = generate_clarification_semantic_fingerprint(
          "meeting-agent", "  请选择会议室  ", ["A301"]
      )
      fp2 = generate_clarification_semantic_fingerprint(
          "meeting-agent", "请选择会议室", ["A301"]
      )
      assert fp1 == fp2
  ```
- [ ] 用户澄清指纹：不同问题产生不同指纹
  ```python
  def test_clarification_semantic_fingerprint_differs_on_question():
      fp1 = generate_clarification_semantic_fingerprint(
          "meeting-agent", "请选择会议室", ["A301"]
      )
      fp2 = generate_clarification_semantic_fingerprint(
          "meeting-agent", "请选择参会人员", ["张三"]
      )
      assert fp1 != fp2
  ```
- [ ] `build_help_request_intervention` 生成的指纹是确定性的（不再随机）
  ```python
  def test_build_help_request_intervention_fingerprint_deterministic():
      task = {"task_id": "t1", ...}
      help_request = {
          "resolution_strategy": "user_clarification",
          "clarification_question": "请选择会议室",
          "clarification_options": ["A301", "B502"],
      }
      req1 = build_help_request_intervention(task, help_request, agent_name="meeting-agent")
      req2 = build_help_request_intervention(task, help_request, agent_name="meeting-agent")
      assert req1["fingerprint"] == req2["fingerprint"]
      assert not req1["fingerprint"].startswith("fp_")  # 不再是随机 UUID
  ```
- Done when:
  - 所有指纹确定性测试通过
  - 语义相同的干预总是产生相同指纹
  - 语义不同的干预产生不同指纹

## 2. 缓存写入验证

- [ ] 用户解决工具干预后，`intervention_cache` 中新增对应条目
  ```python
  def test_cache_write_on_tool_intervention_resolve():
      # 模拟：用户批准工具执行
      # 验证：intervention_cache 中存在该工具的语义指纹条目
      # 验证：条目 action_key == "approve"
      # 验证：条目 intervention_type == "before_tool"
      # 验证：条目 max_reuse == 3
      # 验证：条目 reuse_count == 0
  ```
- [ ] 用户解决澄清干预后，`intervention_cache` 中新增对应条目
  ```python
  def test_cache_write_on_clarification_resolve():
      # 模拟：用户选择会议室 A301
      # 验证：intervention_cache 中存在该问题的语义指纹条目
      # 验证：条目 action_key == "submit_response"
      # 验证：条目 payload 包含用户选择
      # 验证：条目 intervention_type == "clarification"
      # 验证：条目 max_reuse == -1
      # 验证：条目 reuse_count == 0
  ```
- [ ] 用户拒绝工具执行时，缓存也写入（`resolution_behavior == "fail_current_task"`）
  ```python
  def test_cache_write_on_tool_intervention_reject():
      # 验证：条目 resolution_behavior == "fail_current_task"
      # 验证：后续缓存命中时 _is_cache_valid 返回 False（因为 behavior 不是 resume）
  ```
- [ ] 重复解决不覆盖已有缓存条目的 `reuse_count`
  ```python
  def test_cache_write_does_not_reset_reuse_count():
      # 模拟：缓存条目已存在且 reuse_count=2
      # 用户再次解决同一干预
      # 验证：reuse_count 被重置为 0（因为这是一次全新的用户决策）
  ```
- Done when:
  - 干预解决后缓存条目正确写入
  - 条目字段完整且符合预期

## 3. 缓存命中跳过干预验证（工具干预）

- [ ] 缓存命中且有效时，工具干预被跳过
  ```python
  def test_tool_intervention_skipped_on_cache_hit():
      # 准备：intervention_cache 中存在 book_room 的缓存条目
      #       resolution_behavior="resume_current_task", reuse_count=0, max_reuse=3
      # 执行：InterventionMiddleware 处理 book_room 调用
      # 验证：返回 None（不触发干预）
      # 验证：工具被正常执行
  ```
- [ ] 缓存命中但 `resolution_behavior != "resume_current_task"` 时，不跳过
  ```python
  def test_tool_intervention_not_skipped_on_reject_cache():
      # 准备：缓存条目 resolution_behavior="fail_current_task"
      # 验证：干预正常触发
  ```
- [ ] 缓存命中但 `reuse_count >= max_reuse` 时，不跳过
  ```python
  def test_tool_intervention_not_skipped_on_exhausted_cache():
      # 准备：缓存条目 reuse_count=3, max_reuse=3
      # 验证：干预正常触发
  ```
- [ ] 缓存未命中时，干预正常触发
  ```python
  def test_tool_intervention_triggers_on_cache_miss():
      # 准备：intervention_cache 为空
      # 验证：干预正常触发（行为与当前版本一致）
  ```
- Done when:
  - 缓存命中的工具干预被正确跳过
  - 缓存无效或未命中时行为不变

## 4. 缓存命中跳过干预验证（用户澄清）

- [ ] 缓存命中时，用户澄清干预被跳过，任务直接恢复
  ```python
  def test_clarification_skipped_on_cache_hit():
      # 准备：intervention_cache 中存在"请选择会议室"的缓存条目
      # 执行：router _route_help_request 处理相同问题的 help_request
      # 验证：不中断（不返回 INTERRUPTED）
      # 验证：任务状态变为 RUNNING
      # 验证：status_detail == "@cache_auto_resolved"
      # 验证：resolved_inputs 中注入了缓存的用户选择
  ```
- [ ] 缓存命中时，`continuation_mode` 被正确设置为 `"continue_after_clarification"`
  ```python
  def test_clarification_cache_hit_sets_continuation_mode():
      # 验证：resumed_task 的 continuation_mode == "continue_after_clarification"
  ```
- [ ] 缓存命中时，`resume_count` 正确递增
  ```python
  def test_clarification_cache_hit_increments_resume_count():
      # 准备：parent_task.resume_count = 1
      # 验证：resumed_task.resume_count == 2
  ```
- [ ] 缓存未命中时，正常中断等待用户回答
  ```python
  def test_clarification_triggers_on_cache_miss():
      # 准备：intervention_cache 为空
      # 验证：行为与当前版本一致（INTERRUPTED）
  ```
- Done when:
  - 用户澄清缓存命中时任务自动恢复
  - resolved_inputs 中包含正确的缓存数据
  - 缓存未命中时行为不变

## 5. 缓存复用计数验证

- [ ] 每次缓存命中后 `reuse_count` 递增 1
  ```python
  def test_reuse_count_increments_on_hit():
      # 准备：缓存条目 reuse_count=0
      # 第 1 次命中后：reuse_count=1
      # 第 2 次命中后：reuse_count=2
  ```
- [ ] `max_reuse == -1` 时，无论 `reuse_count` 多大都有效
  ```python
  def test_unlimited_reuse_when_max_minus_one():
      # 准备：缓存条目 max_reuse=-1, reuse_count=100
      # 验证：缓存仍然有效
  ```
- [ ] `max_reuse == 3` 时，`reuse_count` 达到 3 后缓存失效
  ```python
  def test_cache_expires_at_max_reuse():
      # 准备：缓存条目 max_reuse=3, reuse_count=2
      # 第 3 次命中：仍然有效（reuse_count=2 < 3）
      # reuse_count 递增到 3
      # 第 4 次查询：缓存无效（reuse_count=3 >= 3）
  ```
- [ ] 递增后的 `reuse_count` 被持久化到 `ThreadState`
  ```python
  def test_reuse_count_persisted_to_state():
      # 验证：executor 返回的 state update 中 intervention_cache
      #       包含更新后的 reuse_count
  ```
- Done when:
  - 复用计数正确递增和持久化
  - 上限判断逻辑正确

## 6. ThreadState intervention_cache Reducer 验证

- [ ] reducer 正确合并两个 cache dict
  ```python
  def test_merge_intervention_cache_combines():
      existing = {"fp_a": {"action_key": "approve", "reuse_count": 1}}
      update = {"fp_b": {"action_key": "submit", "reuse_count": 0}}
      result = merge_intervention_cache(existing, update)
      assert "fp_a" in result
      assert "fp_b" in result
  ```
- [ ] reducer 新值覆盖旧值（同一 key）
  ```python
  def test_merge_intervention_cache_overwrites_same_key():
      existing = {"fp_a": {"reuse_count": 1}}
      update = {"fp_a": {"reuse_count": 2}}
      result = merge_intervention_cache(existing, update)
      assert result["fp_a"]["reuse_count"] == 2
  ```
- [ ] `intervention_cache` 默认值为空 dict
  ```python
  def test_intervention_cache_default_empty():
      state = ThreadState(messages=[])
      assert state.get("intervention_cache") == {} or state.get("intervention_cache") is None
  ```
- Done when:
  - reducer 合并逻辑正确
  - 不丢失已有条目
  - 默认值安全

## 7. 端到端场景验证：会议室选择重试不重复询问

- [ ] 模拟完整场景：
  ```
  1. agent 发起 request_help（user_clarification: "请选择会议室", options: ["A301", "B502"]）
  2. 系统中断，等待用户选择
  3. 用户选择 "A301"
  4. 干预解决，缓存写入
  5. 任务恢复执行，但执行失败（模拟 API 错误）
  6. 任务标记为 FAILED
  7. router 决定重试
  8. agent 重新执行，再次发起相同 request_help
  9. 验证：缓存命中，不中断用户
  10. 验证：resolved_inputs 中包含 "A301"
  11. 验证：任务直接恢复执行
  ```
- [ ] 场景变体：重试时问题文本相同但选项不同，应视为新问题
  ```
  # 第一次：options=["A301", "B502"]，用户选 A301
  # 重试时：options=["A301", "B502", "C103"]（新增选项）
  # 验证：指纹不同，缓存未命中，重新询问用户
  ```
- Done when:
  - 核心场景（选择后重试不重复询问）通过
  - 选项变化时正确识别为新问题

## 8. 端到端场景验证：工具确认重试不重复询问

- [ ] 模拟完整场景：
  ```
  1. agent 调用 book_room(room="A301")
  2. InterventionMiddleware 拦截，触发工具确认干预
  3. 用户批准执行
  4. 干预解决，缓存写入
  5. 工具执行成功，但后续步骤失败
  6. 任务重试，agent 再次调用 book_room(room="A301")
  7. 验证：缓存命中，不再要求确认
  8. 验证：工具直接执行
  ```
- [ ] 工具确认达到复用上限后重新询问：
  ```
  # max_reuse=3
  # 第 1、2、3 次重试：缓存命中，跳过确认
  # 第 4 次重试：缓存失效，重新要求确认
  ```
- Done when:
  - 工具确认缓存在复用上限内正确跳过
  - 达到上限后正确重新询问

## 9. 向后兼容验证

- [ ] 旧 checkpoint（无 `intervention_cache` 字段）恢复后不报错
  ```python
  def test_old_checkpoint_without_cache_works():
      # 模拟：state 中没有 intervention_cache 字段
      # 验证：所有缓存相关逻辑 fallback 到空 dict
      # 验证：干预流程正常工作
  ```
- [ ] 现有的 `_collect_resolved_fingerprints` 去重机制仍然有效
  ```python
  def test_existing_fingerprint_dedup_still_works():
      # 验证：同一 run 内的指纹去重不受缓存机制影响
  ```
- [ ] 现有的幂等性检查（`idempotency_key`）不受影响
  ```python
  def test_idempotency_key_unchanged():
      # 验证：intervention_request 中的 idempotency_key 生成逻辑不变
  ```
- Done when:
  - 旧数据兼容
  - 现有去重和幂等机制不受影响

## 10. 回归验证

- [ ] 运行 `backend/tests/test_interventions_router.py` — 全部通过
- [ ] 运行 `backend/tests/test_multi_agent_core.py` — 全部通过
- [ ] 运行 `backend/tests/test_multi_agent_graph.py` — 全部通过
- [ ] 运行 `backend/tests/test_executor_intervention_normalization.py` — 全部通过
- [ ] 运行 `backend/tests/test_help_request_builder.py` — 全部通过
- [ ] 运行 `make test` — 无新增失败
- Done when:
  - 缓存功能不破坏任何现有测试
  - 总测试数无减少

## 11. 日志验证

- [ ] 缓存命中时日志包含 `[Cache HIT]`
  ```python
  def test_cache_hit_logging(caplog):
      # 验证：caplog 中包含 "[Cache HIT]"
      # 验证：日志包含 semantic_fp 和 reuse_count
  ```
- [ ] 缓存写入时日志包含 `[Cache WRITE]`
  ```python
  def test_cache_write_logging(caplog):
      # 验证：caplog 中包含 "[Cache WRITE]"
  ```
- [ ] 缓存过期时日志包含 `[Cache EXPIRED]`
  ```python
  def test_cache_expired_logging(caplog):
      # 验证：达到 max_reuse 后日志包含 "[Cache EXPIRED]"
  ```
- Done when:
  - 缓存行为可通过日志完整追踪

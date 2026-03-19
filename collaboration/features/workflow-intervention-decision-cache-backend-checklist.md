# Workflow Intervention Decision Cache Backend Checklist

- Status: `draft`
- Owner: `backend`
- Related feature: `workflow-intervention-decision-cache.md`

## 1. ThreadState 新增 intervention_cache 字段

- [ ] 在 `backend/src/agents/thread_state.py` 的 `ThreadState` 中新增字段：
  ```python
  intervention_cache: Annotated[dict[str, dict], merge_intervention_cache]
  ```
- [ ] 实现 reducer `merge_intervention_cache`：
  - 合并策略：新值覆盖旧值（按 key 合并），不丢失已有条目
  - 如果新值中某个 key 的 `reuse_count` 更大，取更大值
  - 示例：
    ```python
    def merge_intervention_cache(
        existing: dict[str, dict],
        update: dict[str, dict],
    ) -> dict[str, dict]:
        merged = {**existing}
        for key, value in update.items():
            merged[key] = value
        return merged
    ```
- [ ] 确保 `intervention_cache` 默认值为空 dict `{}`
- Done when:
  - `ThreadState` 包含 `intervention_cache` 字段
  - reducer 正确合并，不丢失已有缓存条目
  - LangGraph checkpoint 可以正确持久化和恢复该字段

## 2. 语义指纹函数：工具干预

- [ ] 在 `backend/src/agents/middlewares/intervention_middleware.py` 中新增函数：
  ```python
  def _generate_semantic_fingerprint(
      agent_name: str,
      tool_name: str,
      tool_args: dict[str, Any],
  ) -> str:
      """基于语义内容生成确定性指纹，不依赖 run_id。"""
      normalized_args = json.dumps(tool_args, sort_keys=True, ensure_ascii=False, default=str)
      raw = f"{agent_name}:{tool_name}:{normalized_args}"
      return hashlib.sha256(raw.encode()).hexdigest()[:24]
  ```
- [ ] 关键约束：
  - 不包含 `run_id`
  - 不包含 `task_id`
  - 不包含任何随机成分
  - 相同 agent + 相同工具 + 相同参数 = 相同指纹
- [ ] 保留现有的 `_generate_fingerprint` 函数不变（仍用于其他用途如幂等性）
- Done when:
  - 相同的 `(agent_name, tool_name, tool_args)` 在任意执行轮次中
    产生完全相同的语义指纹
  - 不同的参数产生不同的指纹

## 3. 语义指纹函数：用户澄清

- [ ] 在 `backend/src/agents/intervention/help_request_builder.py` 中新增函数：
  ```python
  def _generate_clarification_semantic_fingerprint(
      agent_name: str,
      question: str,
      options: list[str],
  ) -> str:
      """基于问题内容生成确定性指纹。"""
      options_str = json.dumps(sorted(options), ensure_ascii=False) if options else ""
      raw = f"{agent_name}:{question.strip()}:{options_str}"
      return hashlib.sha256(raw.encode()).hexdigest()[:24]
  ```
- [ ] 修改 `build_help_request_intervention` 函数：
  - 将第 262 行的 `fingerprint = f"fp_{uuid.uuid4().hex[:12]}"` 替换为：
    ```python
    fingerprint = _generate_clarification_semantic_fingerprint(
        agent_name, question, options
    )
    ```
- [ ] 关键约束：
  - 相同 agent + 相同问题 + 相同选项 = 相同指纹
  - 选项顺序不影响指纹（通过 `sorted` 保证）
  - 问题文本前后空格不影响指纹（通过 `strip` 保证）
- Done when:
  - 用户澄清干预的指纹是确定性的
  - 重试时相同问题产生相同指纹

## 4. InterventionMiddleware 增加缓存查询

- [ ] 修改 `InterventionMiddleware.__init__`，新增参数：
  ```python
  def __init__(
      self,
      *,
      # ... 现有参数不变 ...
      intervention_cache: dict[str, dict] | None = None,  # 新增
  ):
      # ... 现有初始化不变 ...
      self._intervention_cache = intervention_cache or {}
  ```
- [ ] 修改 `_process_tool_call` 方法，在 `_check_already_resolved` 之后、
  `_handle_intervention` 之前增加缓存查询：
  ```python
  def _process_tool_call(self, request, handler):
      tool_name = request.tool_call.get("name", "")
      should_intervene, policy = self._should_intervene(tool_name, request.tool_call.get("args", {}))
      if not should_intervene:
          return None

      tool_args = request.tool_call.get("args", {})

      # 现有去重检查（保留）
      if self._check_already_resolved(tool_name, tool_args):
          logger.info("[InterventionMiddleware] Skipping - already resolved in run.")
          return None

      # 新增：缓存查询
      semantic_fp = _generate_semantic_fingerprint(self._agent_name, tool_name, tool_args)
      cached = self._intervention_cache.get(semantic_fp)
      if cached and self._is_cache_valid(cached):
          logger.info(
              "[InterventionMiddleware] [Cache HIT] tool='%s' semantic_fp=%s reuse_count=%d/%s",
              tool_name, semantic_fp,
              cached.get("reuse_count", 0),
              cached.get("max_reuse", -1),
          )
          return None  # 跳过干预，直接执行工具

      return self._handle_intervention(request, tool_name, tool_args, policy)
  ```
- [ ] 新增缓存有效性检查方法：
  ```python
  def _is_cache_valid(self, cached: dict) -> bool:
      """检查缓存条目是否仍然有效。"""
      if cached.get("resolution_behavior") != "resume_current_task":
          return False
      max_reuse = cached.get("max_reuse", -1)
      reuse_count = cached.get("reuse_count", 0)
      if max_reuse != -1 and reuse_count >= max_reuse:
          return False
      return True
  ```
- Done when:
  - 缓存命中时，工具干预被跳过，工具直接执行
  - 缓存未命中时，行为与当前完全一致
  - 日志中记录 `[Cache HIT]`

## 5. Executor 传递 intervention_cache 到 Middleware

- [ ] 修改 `backend/src/agents/executor/executor.py` 的 `executor_node` 函数
- [ ] 从 `state` 中读取 `intervention_cache`：
  ```python
  intervention_cache = state.get("intervention_cache") or {}
  ```
- [ ] 将 `intervention_cache` 加入 `agent_config_override` 的 configurable：
  ```python
  agent_config_override = RunnableConfig(
      configurable={
          **config.get("configurable", {}),
          # ... 现有字段不变 ...
          "resolved_fingerprints": resolved_fingerprints,
          "intervention_cache": intervention_cache,  # 新增
      }
  )
  ```
- [ ] 确认 `make_lead_agent` 或 domain agent 工厂能从 config 中读取
  `intervention_cache` 并传递给 `InterventionMiddleware`
- Done when:
  - `InterventionMiddleware` 在每次执行时都能获取到最新的
    `intervention_cache`

## 6. Router 增加用户澄清缓存查询

- [ ] 修改 `backend/src/agents/router/semantic_router.py` 的
  `_route_help_request` 函数
- [ ] 在 `should_interrupt_for_user_clarification` 判断为 True 之后、
  `_interrupt_for_clarification` 调用之前，增加缓存查询：
  ```python
  if should_interrupt_for_user_clarification(help_request):
      # 新增：缓存查询
      intervention_cache = state.get("intervention_cache") or {}
      question = str(help_request.get("clarification_question") or "").strip()
      options = normalize_clarification_options(
          help_request.get("clarification_options")
      )
      semantic_fp = _generate_clarification_semantic_fingerprint(
          agent_name, question, options
      )
      cached = intervention_cache.get(semantic_fp)
      if cached and _is_clarification_cache_valid(cached):
          logger.info(
              "[Router] [Cache HIT] clarification semantic_fp=%s "
              "reuse_count=%d/%s",
              semantic_fp,
              cached.get("reuse_count", 0),
              cached.get("max_reuse", -1),
          )
          return _auto_resolve_from_cache(
              parent_task, cached, semantic_fp,
              intervention_cache, route_count, writer,
              agent_name=agent_name,
          )

      # 缓存未命中：正常中断（现有逻辑不变）
      ...
  ```
- [ ] 实现 `_auto_resolve_from_cache` 函数：
  ```python
  def _auto_resolve_from_cache(
      parent_task: TaskStatus,
      cached: dict,
      semantic_fp: str,
      intervention_cache: dict,
      route_count: int,
      writer,
      *,
      agent_name: str,
  ) -> dict:
      """使用缓存的用户决策自动恢复任务，跳过用户中断。"""
      # 递增复用计数
      updated_entry = {**cached, "reuse_count": cached.get("reuse_count", 0) + 1}
      updated_cache = {**intervention_cache, semantic_fp: updated_entry}

      # 将缓存的决策注入 resolved_inputs
      resolved_inputs = dict(parent_task.get("resolved_inputs") or {})
      resolved_inputs["intervention_resolution"] = {
          "action_key": cached["action_key"],
          "payload": cached.get("payload", {}),
          "resolution_behavior": cached.get("resolution_behavior", "resume_current_task"),
      }

      resumed_task: TaskStatus = {
          **parent_task,
          "status": "RUNNING",
          "status_detail": "@cache_auto_resolved",
          "resolved_inputs": resolved_inputs,
          "continuation_mode": "continue_after_clarification",
          "resume_count": int(parent_task.get("resume_count") or 0) + 1,
          "updated_at": _utc_now_iso(),
      }

      _emit_task_event(
          writer, "task_running", resumed_task, agent_name,
          message="Auto-resolved from intervention cache",
          status_detail="@cache_auto_resolved",
      )

      return {
          "task_pool": [resumed_task],
          "execution_state": "ROUTING_DONE",
          "route_count": route_count,
          "intervention_cache": updated_cache,
      }
  ```
- [ ] 实现 `_is_clarification_cache_valid` 函数：
  ```python
  def _is_clarification_cache_valid(cached: dict) -> bool:
      max_reuse = cached.get("max_reuse", -1)
      reuse_count = cached.get("reuse_count", 0)
      if max_reuse != -1 and reuse_count >= max_reuse:
          return False
      return True
  ```
- Done when:
  - 用户澄清重试时，缓存命中则直接恢复任务，不中断用户
  - 缓存未命中时，行为与当前完全一致
  - `intervention_cache` 中的复用计数正确递增

## 7. Router 增加缓存写入

- [ ] 修改 `backend/src/agents/router/semantic_router.py` 中处理
  intervention resolution 的逻辑（即将 `WAITING_INTERVENTION` 任务
  恢复为 `RUNNING` 的路径）
- [ ] 在用户解决干预后，计算语义指纹并写入缓存：
  ```python
  # 在 intervention resolution 处理逻辑中
  intervention_cache = state.get("intervention_cache") or {}

  # 计算语义指纹
  intervention_request = task.get("intervention_request") or {}
  intervention_type = intervention_request.get("intervention_type")

  if intervention_type == "before_tool":
      # 工具干预：从 context 中提取工具信息
      ctx = intervention_request.get("context") or {}
      semantic_fp = _generate_semantic_fingerprint(
          intervention_request.get("source_agent", ""),
          intervention_request.get("tool_name", ""),
          ctx.get("tool_args", {}),
      )
      default_max_reuse = 3
  elif intervention_type == "clarification":
      # 用户澄清：从请求中提取问题信息
      semantic_fp = intervention_request.get("fingerprint", "")
      # fingerprint 已经是确定性的了（步骤 3 的修改）
      default_max_reuse = -1
  else:
      semantic_fp = None

  if semantic_fp:
      intervention_cache[semantic_fp] = {
          "action_key": action_key,
          "payload": user_payload,
          "resolution_behavior": resolution_behavior,
          "resolved_at": _utc_now_iso(),
          "intervention_type": intervention_type,
          "source_agent": intervention_request.get("source_agent", ""),
          "max_reuse": default_max_reuse,
          "reuse_count": 0,
      }
  ```
- [ ] 将更新后的 `intervention_cache` 包含在返回的 state update 中：
  ```python
  return {
      "task_pool": [resumed_task],
      # ... 其他字段 ...
      "intervention_cache": intervention_cache,  # 新增
  }
  ```
- Done when:
  - 用户每次解决干预后，决策被正确写入 `intervention_cache`
  - 工具干预的 `max_reuse` 默认为 3
  - 用户澄清的 `max_reuse` 默认为 -1（无限）

## 8. InterventionMiddleware 缓存命中时递增复用计数

- [ ] 当缓存命中跳过干预时，需要递增 `reuse_count`
- [ ] 由于 `InterventionMiddleware` 运行在 agent 内部（不直接返回
  state update），需要通过以下方式传递更新：
  - 方案 A：在 middleware 中修改 `self._intervention_cache` 的条目，
    executor 在 agent 执行结束后将更新后的 cache 写回 state
  - 方案 B：将缓存命中事件记录到一个列表中，executor 在结束时批量
    更新 cache
- [ ] 推荐方案 A（更简单）：
  ```python
  # 在缓存命中分支中
  cached["reuse_count"] = cached.get("reuse_count", 0) + 1
  self._intervention_cache[semantic_fp] = cached
  ```
- [ ] Executor 在 agent 执行结束后，将 `intervention_cache` 写回 state：
  ```python
  # executor_node 返回值中包含
  return {
      "task_pool": [...],
      "intervention_cache": intervention_cache,  # 来自 middleware
      ...
  }
  ```
- Done when:
  - 每次缓存命中后 `reuse_count` 正确递增
  - 递增后的值被持久化到 ThreadState

## 9. 导入语义指纹函数到 Router

- [ ] 在 `semantic_router.py` 顶部导入语义指纹函数：
  ```python
  from src.agents.middlewares.intervention_middleware import (
      _generate_semantic_fingerprint,
  )
  from src.agents.intervention.help_request_builder import (
      _generate_clarification_semantic_fingerprint,
      normalize_clarification_options,
  )
  ```
- [ ] 或者将两个语义指纹函数提取到一个共享模块
  `backend/src/agents/intervention/fingerprint.py`，避免跨层导入
- [ ] 推荐提取到共享模块：
  ```python
  # backend/src/agents/intervention/fingerprint.py
  import hashlib
  import json
  from typing import Any

  def generate_tool_semantic_fingerprint(
      agent_name: str,
      tool_name: str,
      tool_args: dict[str, Any],
  ) -> str:
      normalized_args = json.dumps(
          tool_args, sort_keys=True, ensure_ascii=False, default=str
      )
      raw = f"{agent_name}:{tool_name}:{normalized_args}"
      return hashlib.sha256(raw.encode()).hexdigest()[:24]

  def generate_clarification_semantic_fingerprint(
      agent_name: str,
      question: str,
      options: list[str],
  ) -> str:
      options_str = (
          json.dumps(sorted(options), ensure_ascii=False) if options else ""
      )
      raw = f"{agent_name}:{question.strip()}:{options_str}"
      return hashlib.sha256(raw.encode()).hexdigest()[:24]
  ```
- Done when:
  - 语义指纹函数可被 middleware、router、help_request_builder 共同使用
  - 无循环依赖

## 10. 日志规范

- [ ] 所有缓存命中日志使用统一格式：
  ```
  [InterventionMiddleware] [Cache HIT] tool='<tool_name>' semantic_fp=<fp> reuse_count=<n>/<max>
  [Router] [Cache HIT] clarification semantic_fp=<fp> reuse_count=<n>/<max>
  ```
- [ ] 所有缓存写入日志使用统一格式：
  ```
  [Router] [Cache WRITE] semantic_fp=<fp> type=<intervention_type> max_reuse=<n>
  ```
- [ ] 缓存失效（达到复用上限）日志：
  ```
  [InterventionMiddleware] [Cache EXPIRED] tool='<tool_name>' semantic_fp=<fp> reuse_count=<n> reached max_reuse=<max>
  [Router] [Cache EXPIRED] clarification semantic_fp=<fp> reuse_count=<n> reached max_reuse=<max>
  ```
- Done when:
  - 缓存行为可通过日志完整追踪
  - 日志格式统一，便于搜索和过滤

## 11. 向后兼容

- [ ] 现有的 `_collect_resolved_fingerprints` 和
  `_check_already_resolved` 逻辑保留不变
  - 它们仍然在同一 run 内提供即时去重
  - 缓存机制是额外的跨重试去重层
- [ ] 现有的 `_generate_fingerprint`（含 run_id）保留不变
  - 仍用于 `InterventionRequest` 的 `fingerprint` 字段
  - 仍用于幂等性检查
- [ ] 当 `ThreadState` 中不存在 `intervention_cache` 时（旧 checkpoint），
  所有缓存相关逻辑 fallback 到空 dict，行为等同于当前版本
- Done when:
  - 旧 checkpoint 恢复后不会因为缺少 `intervention_cache` 而报错
  - 现有的指纹去重和幂等性机制不受影响

## 12. Final Backend Sign-Off

- [ ] 语义指纹函数对相同输入总是产生相同输出
- [ ] 缓存写入在 intervention resolution 路径中正确执行
- [ ] 缓存查询在 InterventionMiddleware 和 Router 中正确执行
- [ ] 缓存命中时跳过干预，任务直接恢复
- [ ] 缓存复用计数正确递增并持久化
- [ ] 复用上限到达后重新询问用户
- [ ] 现有干预流程（创建、解决、恢复）所有测试通过
- [ ] 新 checkpoint 和旧 checkpoint 均可正常工作

# Feature: Workflow Intervention Decision Cache

- Status: `draft`
- Owner suggestion: `backend` for cache runtime and fingerprint redesign, `test` for regression and cache behavior validation
- Related area: workflow mode, intervention middleware, help request builder, executor resume, router retry

## Goal

解决子任务重试时重复询问用户的问题。

当前行为：模型让用户做出选择（如选择会议室），用户选择后子任务继续执行，
但如果执行失败触发重试，系统会再次向用户提出相同问题。这破坏了用户体验，
也浪费了用户已经提供的决策信息。

本特性引入 **Intervention Decision Cache（干预决策缓存）**，将用户在
工作流中做出的干预决策作为工作流级别的稳定事实缓存起来，重试时自动复用
已缓存的用户决策，避免重复询问。

核心目标：

1. 用户对同一问题只需回答一次，重试时自动复用
2. 缓存机制对所有干预类型通用（工具确认、用户澄清、用户选择、用户确认）
3. 缓存作用域为工作流级别，跨任务重试共享
4. 提供安全的缓存失效策略，防止过期决策被错误复用

## Root Cause Analysis

重试时重复询问的根因有三个：

### 根因 1: 用户澄清干预的指纹是随机的

文件：`backend/src/agents/intervention/help_request_builder.py` 第 262 行

```python
fingerprint = f"fp_{uuid.uuid4().hex[:12]}"
```

每次构建 `InterventionRequest` 都会生成全新的随机指纹。即使是完全相同的
问题（相同的 agent、相同的问题文本、相同的选项列表），重试时也会生成不同
的指纹。`_collect_resolved_fingerprints` 的去重机制无法命中。

### 根因 2: 工具干预指纹包含 run_id

文件：`backend/src/agents/middlewares/intervention_middleware.py` 第 60 行

```python
raw = f"{run_id}:{task_id}:{agent_name}:{tool_name}:{normalized_args}"
```

指纹依赖 `run_id`。重试时如果 `run_id` 发生变化（新的执行轮次），
即使工具名和参数完全相同，指纹也会不同，去重失效。

### 根因 3: 任务失败时清除了 continuation 状态

文件：`backend/src/agents/executor/executor.py` 第 1088-1098 行

任务失败后 `_clear_continuation_fields()` 清除了 `pending_tool_call` 等
状态。重试时 agent 从头执行，自然会重新触发相同的干预请求。而由于根因 1
和根因 2，去重机制无法识别这是已经回答过的问题。

## Why This Is Backend-Only

本特性是纯后端运行时语义变更：

- 不改变 `InterventionRequest` 的 payload 结构
- 不改变 intervention resolve endpoint 的 API 契约
- 不改变前端渲染逻辑或交互流程
- 前端无需感知缓存的存在——缓存命中时干预直接跳过，前端不会收到该干预

唯一的外部可观测变化是：重试时用户不再被重复询问相同问题。

## Current Behavior

### 干预触发流程（正常路径）

1. agent 执行子任务，调用工具或发起 `request_help`
2. `InterventionMiddleware` 或 router 检测到需要用户干预
3. 生成 `InterventionRequest`，写入任务状态，中断执行
4. 前端展示干预卡片，用户做出选择
5. 用户决策写入 `intervention_resolution`，任务恢复执行

### 重试时的问题路径

1. 用户选择完会议室后，任务恢复执行
2. 执行过程中发生错误（如 API 调用失败），任务标记为 `FAILED`
3. router 决定重试该任务
4. agent 重新执行，再次调用相同工具或发起相同 `request_help`
5. 干预机制生成新指纹，去重未命中
6. 用户被再次询问相同问题

### 去重机制的失效点

当前去重依赖 `_collect_resolved_fingerprints` 收集已解决干预的指纹，
然后在 `InterventionMiddleware._check_already_resolved` 中比对。
但由于指纹生成规则的问题（随机 UUID / 含 run_id），重试时新旧指纹
永远不匹配。

## Design Principle

1. **语义指纹**：指纹必须基于干预的语义内容（谁在问、问什么、有哪些选项），
   而非执行上下文（run_id、uuid）
2. **工作流级缓存**：缓存存储在 `ThreadState` 中，由 LangGraph checkpoint
   自动持久化，跨任务、跨重试共享
3. **透明跳过**：缓存命中时直接跳过干预，前端无感知，无需新的交互协议
4. **安全失效**：通过复用次数限制和作用域控制，防止过期或不适用的决策被错误复用

## Contract To Confirm First

- Event/API:
  - 无新增 API，无 payload 结构变更
  - intervention resolve endpoint 不变
- Payload shape:
  - `InterventionRequest` 结构不变
  - `intervention_resolution` 结构不变
  - `ThreadState` 新增 `intervention_cache` 字段（纯后端内部状态）
- Persistence:
  - `intervention_cache` 通过 LangGraph checkpoint 持久化
  - 作用域为单个 thread（工作流实例）
- Error behavior:
  - 缓存查询失败时 fallback 到正常干预流程（不影响现有行为）
  - 缓存不影响干预解决流程的正确性
- Dedup/replacement:
  - 替代现有的 `_collect_resolved_fingerprints` 指纹去重机制
  - 现有指纹字段保留用于其他用途（如幂等性），但去重改用语义指纹

## Frozen Decisions For This Feature

### 1. 语义指纹取代执行指纹用于去重

去重指纹不再依赖 `run_id` 或随机 UUID。

工具干预语义指纹：

```python
semantic_fp = sha256(f"{agent_name}:{tool_name}:{normalized_tool_args}")[:24]
```

用户澄清语义指纹：

```python
options_str = json.dumps(sorted(options), ensure_ascii=False) if options else ""
semantic_fp = sha256(f"{agent_name}:{question_text}:{options_str}")[:24]
```

关键点：相同 agent 对相同工具/问题/选项的干预，无论执行多少轮次，
语义指纹始终一致。

### 2. 缓存存储在 ThreadState

`intervention_cache` 是 `ThreadState` 的一个顶层字段：

```python
intervention_cache: dict[str, dict]  # semantic_fp -> cached_resolution
```

不在 `TaskStatus` 中存储，因为缓存需要跨任务共享。

### 3. 缓存条目结构

每个缓存条目包含完整的复用所需信息：

```python
CachedResolution = {
    "action_key": str,              # 用户选择的动作 key
    "payload": dict,                # 用户提交的数据
    "resolution_behavior": str,     # "resume_current_task" 等
    "resolved_at": str,             # ISO 时间戳
    "intervention_type": str,       # "before_tool" | "clarification"
    "source_agent": str,            # 产生干预的 agent
    "max_reuse": int,               # 最大复用次数，-1 表示无限
    "reuse_count": int,             # 已复用次数
}
```

### 4. 缓存命中时的行为

- **工具干预**（`InterventionMiddleware`）：缓存命中且
  `resolution_behavior == "resume_current_task"` 时，跳过干预，
  直接执行工具。日志记录缓存命中。
- **用户澄清**（router `_route_help_request`）：缓存命中时，
  将缓存的用户回答注入 `resolved_inputs`，跳过中断，
  直接恢复任务执行。

### 5. 缓存复用次数限制

不同干预类型有不同的默认复用上限：

| 干预类型 | 默认 max_reuse | 理由 |
|---------|---------------|------|
| 用户选择（single_select） | -1（无限） | 选会议室这类决策在工作流内不变 |
| 用户确认（confirm） | -1（无限） | 确认类决策在工作流内不变 |
| 用户输入（input） | -1（无限） | 自由文本输入在工作流内不变 |
| 工具执行确认（before_tool） | 3 | 有副作用的工具多次执行需要适度保护 |

当 `reuse_count >= max_reuse`（且 `max_reuse != -1`）时，缓存条目失效，
重新询问用户。

### 6. 缓存写入时机

用户解决干预时写入缓存。具体位置：

- router 中处理 intervention resolution 的逻辑（现有的
  `WAITING_INTERVENTION` 任务恢复路径）
- 写入前计算语义指纹，与 resolution 数据一起存入 `intervention_cache`

### 7. 不引入缓存失效 API

Phase 1 不提供前端主动清除缓存的 API。缓存随 thread 生命周期自然失效。
如果未来需要"重新选择"功能，作为 Phase 2 考虑。

## Phase 1 Scope

Phase 1 包含：

1. `ThreadState` 增加 `intervention_cache` 字段
2. 语义指纹函数（工具干预 + 用户澄清两种）
3. `InterventionMiddleware` 增加缓存查询逻辑
4. router `_route_help_request` 增加缓存查询逻辑
5. intervention resolution 路径增加缓存写入逻辑
6. 缓存复用计数和上限检查
7. 完整的单元测试覆盖

Phase 1 不包含：

1. 前端缓存状态展示（如"已自动复用上次选择"提示）
2. 前端主动清除缓存的交互
3. 跨 thread 的缓存持久化
4. 缓存条目的 TTL 过期机制

## Backend Changes

1. `thread_state.py` — 新增 `intervention_cache` 字段和 reducer
2. `intervention_middleware.py` — 语义指纹函数 + 缓存查询逻辑
3. `help_request_builder.py` — 指纹改为确定性（基于问题内容）
4. `semantic_router.py` — 缓存写入 + 用户澄清缓存查询
5. `executor.py` — 传递 `intervention_cache` 到 middleware

See:

- `workflow-intervention-decision-cache-backend-checklist.md`

## Frontend Changes

无。本特性不影响前端协议和交互。

## Test Changes

1. 语义指纹确定性验证
2. 缓存写入和读取验证
3. 缓存命中时跳过干预验证
4. 缓存复用计数和上限验证
5. 缓存未命中时正常干预流程验证
6. 现有干预流程回归验证

See:

- `workflow-intervention-decision-cache-test-checklist.md`

## Risks

1. 如果语义指纹的粒度过粗（如只用 `tool_name` 不含参数），可能导致
   不同场景的干预被错误缓存命中——必须包含完整的工具参数或问题内容
2. 如果工具参数在重试时发生合理变化（如时间戳参数更新），语义指纹
   可能不匹配——需要在指纹计算时排除易变字段
3. 如果缓存的用户决策在重试时已经不适用（如会议室已被占用），
   自动复用可能导致执行再次失败——但这和用户手动重新选择同一间
   会议室的效果一致，不会比现有行为更差
4. `intervention_cache` 作为 `ThreadState` 顶层字段，需要确保
   reducer 合并逻辑正确，避免并发写入丢失

## Acceptance Criteria

1. 用户选择会议室后，子任务失败重试时不再重复询问会议室选择
2. 用户确认工具执行后，子任务失败重试时不再重复要求确认（在复用上限内）
3. 缓存仅在同一工作流实例（thread）内生效，新工作流不受影响
4. 缓存命中时日志明确记录 `[Cache HIT]`，便于调试
5. 缓存未命中或失效时，正常触发干预流程，用户体验与当前一致
6. 现有的干预创建、解决、恢复流程不受影响，所有现有测试通过
7. 缓存复用达到上限后，重新询问用户

## Open Questions

1. 工具参数中是否存在"易变字段"（如时间戳、随机 ID）需要在指纹计算时
   排除？如果存在，是否需要一个可配置的排除字段列表？
2. 未来是否需要支持"用户主动重新选择"功能（前端提供"重新决策"按钮，
   清除特定缓存条目）？
3. 是否需要在缓存命中时向前端发送一个通知事件（如"已自动复用上次选择"），
   让用户知道系统没有重新询问？

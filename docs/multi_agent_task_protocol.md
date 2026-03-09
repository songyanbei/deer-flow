# Multi-Agent Task Protocol

> 适用范围：`multi_agent` 主流程  
> 不适用于旧 `task` 工具驱动的 `legacy_subagent` 消息协议

## 1. 目标

本协议用于约束 `multi_agent` 主流程中的：

1. 线程状态字段
2. `task_pool` 数据结构
3. `task_*` custom event 结构
4. `run_id` 生命周期

该协议的设计目标是：

1. 后端以线程状态作为任务恢复主源
2. 前端以 `task_pool + custom event` 完成 hydration 和增量更新
3. 刷新、重连、多轮对话切换时任务状态保持一致

## 2. 线程状态字段

`multi_agent` 线程状态至少包含以下字段：

```python
original_input: str | None
run_id: str | None
planner_goal: str | None
task_pool: list[TaskStatus]
verified_facts: dict[str, Any]
route_count: int
execution_state: str | None
final_result: str | None
```

其中：

- `run_id`：当前执行轮次标识
- `task_pool`：当前轮的任务快照
- `execution_state`：图级执行状态

## 3. TaskStatus 结构

`task_pool` 中每个任务对象应满足以下结构：

```python
class TaskStatus(TypedDict):
    task_id: str
    description: str
    run_id: str | None
    assigned_agent: str | None
    status: Literal["PENDING", "RUNNING", "DONE", "FAILED"]
    status_detail: str | None
    clarification_prompt: str | None
    updated_at: str | None
    result: str | None
    error: str | None
```

字段说明：

- `task_id`：任务唯一标识
- `run_id`：所属执行轮次
- `status_detail`：用于前端展示的状态说明
- `clarification_prompt`：等待用户澄清时的具体问题
- `updated_at`：最近一次状态更新时间，ISO 8601 UTC

## 4. 任务状态机

后端 `task_pool.status` 允许的转换如下：

- `PENDING -> RUNNING`
- `PENDING -> FAILED`
- `RUNNING -> DONE`
- `RUNNING -> FAILED`

不允许：

- `RUNNING -> PENDING`
- `DONE -> PENDING`
- `FAILED -> PENDING`

前端统一状态映射建议如下：

| 后端 | 前端 |
|------|------|
| `PENDING` | `pending` |
| `RUNNING` | `in_progress` |
| `RUNNING + clarification_prompt` | `waiting_clarification` |
| `DONE` | `completed` |
| `FAILED` | `failed` |

## 5. run_id 生命周期

`run_id` 用于区分同一线程中的不同执行轮次。

规则如下：

1. 新用户问题进入时生成新的 `run_id`
2. 澄清恢复时沿用原 `run_id`
3. 当前轮中的所有任务必须共享同一个 `run_id`
4. 历史旧线程恢复时，如果缺少 `run_id`，应在恢复阶段补齐

## 6. Custom Event Schema

`multi_agent` 事件必须带：

```json
{
  "type": "task_started | task_running | task_completed | task_failed",
  "source": "multi_agent",
  "run_id": "run_xxx",
  "task_id": "task_xxx",
  "agent_name": "contacts-agent",
  "description": "查询联系人信息",
  "status": "in_progress | waiting_clarification | completed | failed"
}
```

按事件类型扩展字段：

### 6.1 task_started

```json
{
  "type": "task_started",
  "message": "Task execution started",
  "status_detail": "Task execution started"
}
```

### 6.2 task_running

```json
{
  "type": "task_running",
  "message": "Dispatching task to domain agent",
  "status_detail": "Dispatching task to domain agent"
}
```

等待澄清时：

```json
{
  "type": "task_running",
  "status": "waiting_clarification",
  "message": "Waiting for user clarification",
  "status_detail": "Waiting for user clarification",
  "clarification_prompt": "请确认员工姓名或邮箱"
}
```

### 6.3 task_completed

```json
{
  "type": "task_completed",
  "status": "completed",
  "status_detail": "Task completed",
  "result": "已找到员工档案"
}
```

### 6.4 task_failed

```json
{
  "type": "task_failed",
  "status": "failed",
  "status_detail": "具体错误信息",
  "error": "具体错误信息"
}
```

## 7. 恢复策略

前端恢复 `multi_agent` 任务状态时，应遵循以下优先级：

1. `thread.values.task_pool`
2. 当前流中的 `task_*` custom event

`messages` 不作为 `multi_agent` 的任务恢复主源。

## 8. 与 legacy_subagent 的边界

旧 `legacy_subagent` 继续使用 deer-flow 原有协议：

1. AI `task` tool call 作为任务创建锚点
2. tool result 作为任务最终结果锚点
3. custom event 作为实时进度补充

`legacy_subagent` 不要求使用本协议中的 `task_pool`。


# Runtime Hook Harness

workflow runtime 的统一 hook 基础设施，为 planner / router / executor 提供可注册、可扩展的运行时插桩点。

## 架构

```
src/agents/hooks/
├── __init__.py              # 公共 API 入口
├── base.py                  # 契约: RuntimeHookName / Context / Result / Handler ABC
├── registry.py              # 线程安全的全局注册表 (singleton: runtime_hook_registry)
├── runner.py                # 执行器: run_runtime_hooks() + ensure_default_hooks()
├── lifecycle.py             # Slice B 统一接入层: interrupt emit / resolve / state commit helpers
└── verification_hooks.py    # 默认 handler: TaskVerificationHook / WorkflowVerificationHook

src/observability/node_wrapper.py   # traced_node() 承载 after-node hooks + state commit hooks
```

**导入入口**：

```python
from src.agents.hooks import (
    RuntimeHookName,
    RuntimeHookContext,
    RuntimeHookResult,
    RuntimeHookHandler,
    HookDecision,
    runtime_hook_registry,
    run_runtime_hooks,
    HookExecutionError,
    install_default_runtime_hooks,
)
```

## 已支持的 Hook 点

### 总览

| Hook 名称 | 触发时机 | 接入方式 | 默认 Handler | 状态 |
|---|---|---|---|---|
| `after_planner` | planner 节点返回后 | `traced_node` wrapper | 无 | Slice A，可注册 |
| `after_router` | router 节点返回后 | `traced_node` wrapper | 无 | Slice A，可注册 |
| `after_executor` | executor 节点返回后 | `traced_node` wrapper | 无 | Slice A，可注册 |
| `after_task_complete` | executor 内 task 成功完成 | executor 内直接调用 | `TaskVerificationHook` (priority=50) | Slice A，已承载 Phase 4 |
| `before_final_result_commit` | planner 判定 done、提交前 | planner 内直接调用 | `WorkflowVerificationHook` (priority=50) | Slice A，已承载 Phase 4 |
| `before_interrupt_emit` | 中断 event 发射之前 | executor / router lifecycle helper | 无 | **Slice B，已接入** |
| `after_interrupt_resolve` | intervention 解决之后 | router / gateway lifecycle helper | 无 | **Slice B，已接入** |
| `before_task_pool_commit` | `task_pool` 最终提交前 | node_wrapper / gateway lifecycle helper | 无 | **Slice B，已接入** |
| `before_verified_facts_commit` | `verified_facts` 最终提交前 | node_wrapper / gateway lifecycle helper | 无 | **Slice B，已接入** |

### 两类 Hook 的区别

- **after-node hooks** (`after_planner` / `after_router` / `after_executor`)：通过 `node_wrapper.py` 的 `traced_node` 装饰器统一接入，节点返回 update 后自动执行。metadata 由 node_wrapper 从 state + result 中构建。
- **业务决策 hooks** (`after_task_complete` / `before_final_result_commit`)：在节点内部的特定业务路径上直接调用 `run_runtime_hooks()`，由节点代码构建 metadata 并传入。

## Hook Metadata 契约

每个 hook 点的 `ctx.metadata` 包含以下字段：

### `after_planner`

| 字段 | 类型 | 说明 |
|---|---|---|
| `planner_goal` | `str` | 当前规划目标 |
| `done` | `bool` | execution_state 是否为 DONE/ERROR |
| `summary` | `str` | final_result 内容 |
| `task_pool_changed` | `bool` | 本次是否产出了新 task_pool |

### `after_router`

| 字段 | 类型 | 说明 |
|---|---|---|
| `selected_task_id` | `str` | 被选中执行的 RUNNING task id |
| `route_count` | `int` | 累计路由次数 |
| `execution_state` | `str` | 路由后的 execution_state |

### `after_executor`

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | `str` | 执行的任务 id |
| `assigned_agent` | `str` | 执行该任务的 agent |
| `outcome_kind` | `str` | 真实 outcome 类型（complete / task_fail / escalation 等） |
| `used_fallback` | `bool` | 是否走了 fallback 路径 |

### `after_task_complete`

| 字段 | 类型 | 说明 |
|---|---|---|
| `task` | `dict` | 完整的 TaskStatus dict |
| `assigned_agent` | `str` | 执行 agent |
| `task_result` | `str` | agent 最终输出文本 |
| `resolved_inputs` | `dict \| None` | 依赖任务的解析输入 |
| `artifacts` | `list` | 产出的 artifact 列表 |
| `verified_facts` | `dict` | 已累积的 verified_facts |
| `used_fallback` | `bool` | 是否走了 fallback 路径 |

### `before_final_result_commit`

| 字段 | 类型 | 说明 |
|---|---|---|
| `final_result` | `str` | 待提交的最终摘要 |
| `task_pool` | `list[dict]` | 当前 task pool 快照 |
| `verified_facts` | `dict` | 已累积的 verified_facts |
| `workflow_kind` | `str \| None` | workflow 类型 |
| `verification_retry_count` | `int` | 已重试次数 |
| `original_input` | `str` | 用户原始输入 |
| `run_id` | `str` | 当前 run id |
| `planner_goal` | `str` | 规划目标 |

## 执行规则

1. **同步、确定性执行** — 同一 hook 点的 handler 按 `(priority, insertion_order)` 排序，低优先级先执行
2. **浅合并** — `update_patch` 的顶层 key 覆盖 `proposed_update`，不做深层 dict merge
3. **`continue`** — 合并 patch，继续后续 handler
4. **`short_circuit`** — 合并 patch，立即停止 hook 链，将结果作为节点最终返回
5. **fail-closed** — handler 抛异常 → `HookExecutionError` → 节点返回 `ERROR` 状态
6. **空 registry 零行为变化** — 无 handler 时直接返回原始 `proposed_update`（同一引用）
7. **state 只读** — handler 收到的 `state` 是 deep copy 快照，不可反向影响 graph 状态
8. **自动恢复** — `registry.clear()` 后下次 hook 调用自动重装默认 handler

## 使用方式

### 1. 编写自定义 Hook Handler

所有 handler 继承 `RuntimeHookHandler`，实现 `handle(ctx)` 方法：

```python
from src.agents.hooks import (
    RuntimeHookName,
    RuntimeHookHandler,
    RuntimeHookResult,
    runtime_hook_registry,
)

class PlannerAuditHook(RuntimeHookHandler):
    name = "planner_audit"
    priority = 10  # 比 verification (50) 更早执行

    def handle(self, ctx):
        # ctx.metadata 包含 planner_goal, done, summary, task_pool_changed
        logger.info("Planner decided: done=%s, tasks_changed=%s",
                     ctx.metadata["done"], ctx.metadata["task_pool_changed"])
        # 不修改任何状态，继续链
        return RuntimeHookResult.ok()

# 注册到全局 registry
runtime_hook_registry.register(RuntimeHookName.AFTER_PLANNER, PlannerAuditHook())
```

### 2. 使用 short_circuit 拦截异常行为

```python
class TaskRiskGateHook(RuntimeHookHandler):
    name = "task_risk_gate"
    priority = 40  # 在 verification (50) 之前执行

    def handle(self, ctx):
        task_result = ctx.metadata.get("task_result", "")
        if "DELETE" in task_result.upper():
            return RuntimeHookResult.short_circuit(
                patch={
                    "execution_state": "ERROR",
                    "final_result": "Risk gate blocked: destructive operation detected",
                },
                reason="risk_gate_blocked",
            )
        return RuntimeHookResult.ok()

runtime_hook_registry.register(RuntimeHookName.AFTER_TASK_COMPLETE, TaskRiskGateHook())
```

### 3. 使用 update_patch 修改节点返回

```python
class InjectMetricsHook(RuntimeHookHandler):
    name = "inject_metrics"
    priority = 90  # 在 verification 之后执行

    def handle(self, ctx):
        # 给节点返回值追加指标字段
        return RuntimeHookResult.ok(
            patch={"_hook_metrics": {"handler": self.name, "hook": ctx.hook_name.value}},
            reason="metrics_injected",
        )
```

### 4. 测试中使用独立 registry

```python
from src.agents.hooks import RuntimeHookRegistry, RuntimeHookName, run_runtime_hooks

@pytest.fixture
def custom_registry():
    reg = RuntimeHookRegistry()
    reg.register(RuntimeHookName.AFTER_EXECUTOR, MyTestHook())
    yield reg
    reg.clear()

def test_my_hook(custom_registry):
    result = run_runtime_hooks(
        RuntimeHookName.AFTER_EXECUTOR,
        node_name="executor",
        state={"run_id": "test-run"},
        proposed_update={"execution_state": "EXECUTING_DONE"},
        registry=custom_registry,  # 不影响全局 registry
    )
    assert result["my_custom_field"] == "expected_value"
```

## 默认 Handler 说明

### TaskVerificationHook

- **Hook 点**: `after_task_complete`
- **优先级**: 50
- **行为**: 调用 `run_task_verification()`，根据 verdict 返回：
  - `PASSED` → `continue`，附带 `_verification_result` marker
  - `NEEDS_REPLAN` → `short_circuit`，任务标记 FAILED + 写入 `verification_feedback`
  - `HARD_FAIL` → `short_circuit`，返回 `ERROR` 状态

### WorkflowVerificationHook

- **Hook 点**: `before_final_result_commit`
- **优先级**: 50
- **行为**: 调用 `run_workflow_verification()`，根据 verdict 返回：
  - `PASSED` → `continue`，附带 `workflow_verification_status=passed`
  - `NEEDS_REPLAN` → `short_circuit`，回到 `QUEUED` 重新规划（含 retry budget 检查）
  - `HARD_FAIL` → `short_circuit`，返回 `ERROR` 状态

## Reducer 交互注意事项

hook 的 `update_patch` 最终由 LangGraph 的 reducer 处理。需要注意：

- `task_pool`: `merge_task_pool` reducer — 按 task_id 合并，有状态转换守卫
- `verified_facts`: `merge_verified_facts` reducer — **空 dict `{}` 表示"清空全部"**，如需保留已有 facts，应从 update 中 pop 该 key 而非设为 `{}`
- `messages`: LangGraph 默认 append reducer — 设为 `[]` 不会清空历史，只是不追加

## Slice B Hook 点（中断生命周期 & 状态提交）

### 总览

| Hook 名称 | 触发时机 | 接入方式 | 默认 Handler | 状态 |
|---|---|---|---|---|
| `before_interrupt_emit` | 中断 event 发射之前 | executor / router 内通过 `lifecycle.apply_before_interrupt_emit()` | 无 | 可注册 |
| `after_interrupt_resolve` | intervention 解决之后 | router(in-graph) / gateway 通过 `lifecycle.apply_after_interrupt_resolve()` | 无 | 可注册 |
| `before_task_pool_commit` | `task_pool` 最终提交前 | node_wrapper / gateway 通过 `lifecycle.apply_state_commit_hooks()` | 无 | 可注册 |
| `before_verified_facts_commit` | `verified_facts` 最终提交前 | node_wrapper / gateway 通过 `lifecycle.apply_state_commit_hooks()` | 无 | 可注册 |

### 调用路径

#### `before_interrupt_emit` — 先 hook，后 event，最后 return

- **executor**（4 个分支）：
  - `executor.request_intervention` — intervention_required 信号
  - `executor.request_help_user` — 用户所有的 help request
  - `executor.request_help_system` — 系统依赖 help request
  - `executor.request_clarification` — ask_clarification 信号
- **router**（2 个函数）：
  - `router._interrupt_for_clarification` — 路由侧 clarification
  - `router._interrupt_for_intervention` — 路由侧 intervention

#### `after_interrupt_resolve` — 两条路径统一覆盖

- **router (in-graph)**：`router.in_graph_resolve` — graph 恢复时路由器检测到 resolution message
- **gateway (direct-write)**：`gateway.resolve_intervention` — REST endpoint 直接 `update_state()` 前

#### State commit hooks — 固定执行顺序：先 `task_pool`，后 `verified_facts`

- **node_wrapper**：`node_wrapper.{planner|router|executor}` — 所有节点返回前，after-node hooks 之后
- **gateway**：`gateway.resolve_intervention` — `update_state()` 之前

### Hook Metadata 契约

#### `before_interrupt_emit`

| 字段 | 类型 | 说明 |
|---|---|---|
| `interrupt_type` | `str` | `"intervention"` / `"clarification"` / `"dependency"` |
| `task_id` | `str` | 触发中断的 task id |
| `agent_name` | `str` | 触发中断的 agent |
| `source_path` | `str` | 稳定的调用来源标识 |

#### `after_interrupt_resolve`

| 字段 | 类型 | 说明 |
|---|---|---|
| `task_id` | `str` | 被解决的 task id |
| `new_status` | `str` | 解决后的 task status |
| `source_path` | `str` | `"router.in_graph_resolve"` / `"gateway.resolve_intervention"` |
| `action_key` | `str` | 用户选择的 action key |
| `resolution_behavior` | `str` | `"resume_current_task"` / `"fail_current_task"` 等 |
| `request_id` | `str` | intervention request id |

#### `before_task_pool_commit`

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_path` | `str` | 调用来源 |
| `task_pool_size` | `int` | 候选提交的 task_pool 大小 |
| `commit_reason` | `str` | `"state_commit"` |

#### `before_verified_facts_commit`

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_path` | `str` | 调用来源 |
| `facts_count` | `int` | 候选提交的 facts 数量 |
| `is_clear_all` | `bool` | 是否为 `{}` 清空操作 |
| `commit_reason` | `str` | `"state_commit"` |

### Safety Guard: `verified_facts={}` 清空保护

当 `proposed_update` 中 `verified_facts` 为空 dict `{}` 时，`apply_state_commit_hooks()` 会抛出
`VerifiedFactsClearAllGuardError`，除非调用方显式传入 `allow_verified_facts_clear_all=True`。

这是一个 fail-closed 的安全措施，防止 hook 或业务逻辑误触发全量 facts 清空。

### Lifecycle Helpers

所有 Slice B hook 通过统一的 lifecycle helpers 接入，避免各调用方重复拼装 metadata：

```python
from src.agents.hooks.lifecycle import (
    apply_before_interrupt_emit,
    apply_after_interrupt_resolve,
    apply_state_commit_hooks,
    VerifiedFactsClearAllGuardError,
)
```

## 相关文件

- [Hook 基础设施](../src/agents/hooks/) — 完整源码
- [Lifecycle Helpers](../src/agents/hooks/lifecycle.py) — Slice B 统一接入层
- [Node Wrapper](../src/observability/node_wrapper.py) — after-node hook + state commit hook 接入点
- [Feature Spec (Slice A)](../../collaboration/features/workflow-runtime-hook-harness-mvp.md) — Slice A 需求文档
- [Feature Spec (Slice B)](../../collaboration/features/workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md) — Slice B 需求文档
- [Backend Checklist (Slice B)](../../collaboration/features/workflow-runtime-hook-harness-slice-b-interrupt-state-commit-backend-checklist.md) — Slice B 开发 checklist

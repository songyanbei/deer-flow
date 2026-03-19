# Feature: Workflow Structured Observability

- Status: `draft`
- Owner suggestion: `backend`
- Related area: workflow mode, executor tracing, decision audit, performance metrics
- Frontend impact: `none required`

## Goal

为 DeerFlow 多智能体 workflow 模式建立结构化可观测性体系，使得：

1. 每次 workflow 执行的完整调用链（Planner -> Router -> Executor -> Agent -> Tool）可追踪
2. 所有关键决策（编排模式选择、任务分解、Agent 路由、outcome 归类）有结构化审计日志
3. 核心性能指标（LLM 延迟、token 消耗、MCP 工具延迟、端到端耗时）可采集和查询
4. 生产问题可通过 `run_id` + `task_id` 在日志/traces/metrics 之间关联定位

## Why This Is Backend-Only

可观测性改造仅涉及后端内部埋点、日志和指标采集。不改变：

1. 现有 SSE 事件流的事件类型和 payload 结构
2. 前端消费的 `task_pool` 状态字段
3. 干预解决 API 的请求/响应格式
4. 任何前端组件的渲染逻辑

前端无需感知这些改动。后端可独立部署。

## Current Observability State

### What Exists

1. **事件流**: `get_stream_writer()` 发送 8 种 `task_*` 事件 + `workflow_stage_changed` + `orchestration_mode_resolved` 到前端 SSE
2. **模块日志**: 所有核心模块通过 `logging.getLogger(__name__)` 输出文本日志，带 `[Executor]`/`[Router]`/`[Planner]` 前缀
3. **决策日志**: `_log_executor_decision()` 在 executor.py 中输出 JSON 字符串到 INFO 日志
4. **LangSmith 追踪**: 可选集成，通过 `LANGSMITH_*` 环境变量启用，仅覆盖 LLM 调用层
5. **trace_id 传播**: task_tool.py 中生成 8 字符 trace_id，在 subagent 日志中使用
6. **队列指标**: `_get_inmem_worker_queue_snapshot()` 获取 worker 数量

### What Is Missing

1. **无 span 级追踪**: 无法看到 Planner -> Router -> Executor 的完整调用链和各环节耗时
2. **决策审计散落在日志流中**: Router 选了哪个 Agent、为什么选？Planner 如何分解？这些信息需要 grep 解析
3. **无性能指标**: 无 token 消耗、LLM 延迟、MCP 工具延迟、端到端耗时的结构化采集
4. **日志与 trace 割裂**: `run_id`/`task_id` 在日志和事件中都出现，但没有统一的 trace context 贯穿
5. **无告警基础**: 没有结构化 metrics 就无法设置告警

## Design Principles

1. **渐进式引入**: 不改变现有架构，通过中间件/装饰器/callback 模式注入
2. **零侵入核心逻辑**: Planner/Router/Executor 的业务代码改动最小化
3. **兼容现有 LangSmith**: 作为 LangSmith 的补充而非替代
4. **可选依赖**: OpenTelemetry 作为可选依赖，未安装时自动降级为 noop
5. **统一 context 传播**: 用 `run_id` + `task_id` 作为贯穿所有层面的关联键

## Architecture Overview

```
Layer 3: 消费层
  Grafana Dashboard / Jaeger UI / Log Query / Alert Rules
  ─────────────────────────────────────────────────────────
Layer 2: 采集导出层
  OTLP Exporter (Traces)  |  Prometheus Exporter (Metrics)
  Structured JSON Logger (Decision Logs)
  ─────────────────────────────────────────────────────────
Layer 1: 埋点层
  TracingFacade  |  MetricsCollector  |  DecisionRecorder
  (Graph 节点 + LLM Callback + MCP 中间件)
```

## New Backend Module

新建模块: `backend/src/observability/`

### Module Structure

```
backend/src/observability/
  __init__.py              # 公共 API 导出
  setup.py                 # 一次性初始化（startup 调用）
  tracer.py                # Span 管理 facade（OTel 或 noop）
  metrics.py               # 指标采集 facade（OTel 或内存 fallback）
  decision_log.py          # 结构化决策记录（独立 logger）
  node_wrapper.py          # LangGraph 节点装饰器
  llm_callback.py          # LangChain callback handler
```

## Span Tree Design

每次 workflow 执行产生一棵层次化 span 树：

```
workflow (root span)
  orchestration_selector
    llm_call (if scoring)
  planner [round=1]
    context_build
    llm_call (decompose/validate)
  router [round=1]
    dependency_check
    llm_route (semantic matching)
    mcp_init (if needed)
  executor [round=1, task=task-001]
    context_build
    mcp_ensure_ready
    agent_invoke
      llm_call [turn=1]
      tool_call [tool=search_employee]
      llm_call [turn=2]
      tool_call [tool=task_complete]
    outcome_normalize
  router [round=2]
    ...
  executor [round=2, task=task-002]
    ...
  planner [round=2] (validate)
    llm_call
```

### Span Attributes

所有 span 共享的属性：

| Attribute | Type | Description |
|-----------|------|-------------|
| `run_id` | string | workflow run 标识 |
| `task_id` | string | 当前任务标识（executor span） |
| `node` | string | graph 节点名 (planner/router/executor) |
| `route_count` | int | 当前调度轮次 |
| `task_pool_size` | int | task pool 中的任务数 |

Executor span 额外属性：

| Attribute | Type | Description |
|-----------|------|-------------|
| `agent_name` | string | 执行 agent 名称 |
| `continuation_mode` | string | 当前恢复模式 |
| `outcome_kind` | string | outcome 归类结果 |
| `execution_state` | string | 状态转换结果 |

## Decision Log Design

使用独立 logger `deer-flow.decisions`，输出 JSON Lines 格式，与模块日志分离，可独立路由到不同的日志后端。

### Decision Types

| Decision Type | 触发节点 | 记录内容 |
|--------------|---------|---------|
| `orchestration_mode` | Selector | 编排模式选择: leader vs workflow, 评分依据 |
| `task_decomposition` | Planner | 任务分解结果: goal, task_ids, assigned_agents |
| `workflow_completion` | Planner | workflow 完成判定: done flag, summary |
| `agent_route` | Router | Agent 路由: task_desc, candidates, selected, reason |
| `agent_route_fallback` | Router | 路由回退: task_desc, fallback reason |
| `helper_dispatch` | Router | Helper 派发: parent_task, help_request, helper_task_id |
| `helper_retry` | Router | Helper 重试: parent_task, failed_helper, retry_count |
| `budget_escalation` | Router | 预算耗尽: route_count, help_depth, resume_count |
| `dependency_resolution` | Router | 依赖恢复: parent_task, dependency_results |
| `outcome_classification` | Executor | Outcome 归类: message_count, outcome_kind, used_fallback |
| `intervention_trigger` | Executor | 干预触发: tool_name, risk_level |
| `intervention_resolution` | Router/Gateway | 干预决议: request_id, action_key, behavior |

### Decision Log Entry Shape

```json
{
  "ts": 1711036800.123,
  "decision_type": "agent_route",
  "run_id": "run_abc123",
  "task_id": "task-001",
  "agent_name": "contacts-agent",
  "inputs": {"task_desc": "查询张三的联系方式", "candidates": ["contacts-agent", "hr-agent"]},
  "output": {"selected": "contacts-agent"},
  "reason": "fast_path_pre_assigned",
  "alternatives": ["hr-agent"],
  "confidence": 0.95,
  "duration_ms": 12.3
}
```

## Metrics Design

### Metric Definitions

| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| `workflow.total` | Counter | run_id, mode | workflow 启动计数 |
| `workflow.duration_ms` | Histogram | mode, status | 端到端耗时 |
| `task.total` | Counter | agent, status | 任务创建计数 |
| `task.duration_ms` | Histogram | agent, status | 单任务耗时 |
| `task.failure.total` | Counter | agent | 任务失败计数 |
| `llm.call.total` | Counter | model, node | LLM 调用计数 |
| `llm.call.duration_ms` | Histogram | model, node | LLM 调用延迟 |
| `llm.tokens.total` | Counter | model, direction | token 消耗（input+output） |
| `mcp.call.total` | Counter | tool, agent, success | MCP 工具调用计数 |
| `mcp.call.duration_ms` | Histogram | tool, agent | MCP 工具延迟 |
| `intervention.total` | Counter | agent, tool, risk_level | 干预触发计数 |
| `helper.retry.total` | Counter | parent_task_id, agent | Helper 重试计数 |

### Fallback Mode

未安装 OpenTelemetry 时，所有指标在内存中维护，通过 `GET /debug/metrics` 端点暴露 JSON 快照（含 count/avg/max/p95）。

## Integration Points (Code Changes)

### New Files

| File | Description | Lines (est.) |
|------|-------------|-------------|
| `backend/src/observability/__init__.py` | 公共 API 导出 | ~15 |
| `backend/src/observability/setup.py` | 初始化：决策 logger 配置 + OTel 初始化 | ~80 |
| `backend/src/observability/tracer.py` | Span 管理 facade | ~100 |
| `backend/src/observability/metrics.py` | 指标采集 facade | ~180 |
| `backend/src/observability/decision_log.py` | 结构化决策记录 | ~70 |
| `backend/src/observability/node_wrapper.py` | Graph 节点装饰器 | ~50 |
| `backend/src/observability/llm_callback.py` | LangChain callback handler | ~60 |
| `backend/src/config/observability_config.py` | 配置定义 | ~30 |

### Existing Files To Modify

| File | Change | Impact |
|------|--------|--------|
| `backend/src/gateway/app.py` | startup 调用 `init_observability()` + 注册 `/debug/metrics` 端点 | +10 lines |
| `backend/src/models/factory.py` | 注入 `ObservabilityCallbackHandler` 到 LLM callback 链 | +5 lines |
| `backend/src/agents/graph.py` | 节点函数包装 `traced_node()` | +6 lines |
| `backend/src/agents/planner/node.py` | 插入 `record_decision()` 调用 | +20 lines |
| `backend/src/agents/router/semantic_router.py` | 插入 `record_decision()` 调用 | +40 lines |
| `backend/src/agents/executor/executor.py` | 插入 `record_decision()` 调用 | +20 lines |
| `backend/src/agents/orchestration/selector.py` | 插入 `record_decision()` 调用 | +10 lines |
| `pyproject.toml` | 添加 `observability` 可选依赖组 | +5 lines |

### Files NOT Changed

| File | Reason |
|------|--------|
| 所有 `frontend/` 文件 | 可观测性是后端内部改造 |
| `thread_state.py` | 不新增 state 字段 |
| `gateway/routers/interventions.py` | 不改变 API 契约 |
| 现有 SSE 事件发送逻辑 | 事件类型和 payload 不变 |

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OTEL_ENABLED` | `false` | 启用 OpenTelemetry 导出 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP collector 地址 |
| `OTEL_SERVICE_NAME` | `deer-flow` | 服务名 |
| `DEER_FLOW_DECISION_LOG_FILE` | (空=stdout) | 决策日志文件路径 |
| `DEER_FLOW_METRICS_ENABLED` | `true` | 启用内存指标 |

### Dependency Management

```toml
[project.optional-dependencies]
observability = [
    "opentelemetry-api>=1.20",
    "opentelemetry-sdk>=1.20",
    "opentelemetry-exporter-otlp-proto-grpc>=1.20",
]
```

不安装 observability 依赖时，所有 tracer/metrics 自动降级为 noop，决策日志仍正常工作（仅依赖标准库 `logging` + `json`）。

## Compatibility With Existing Systems

| Existing Component | Compatibility |
|-------------------|---------------|
| LangSmith 追踪 | 共存 — ObservabilityCallbackHandler 与 LangChainTracer 各自独立注册 |
| 现有事件流 (SSE) | 不变 — span/metrics 走独立通道 |
| 现有模块日志 | 不变 — decision_log 使用独立 logger，不混入模块日志 |
| 现有 `_log_executor_decision()` | 渐进替换 — 先并存，稳定后移除旧函数 |
| config.yaml | 追加 `observability` 节，不修改现有配置 |

## Delivery Phases

### Phase 1: 基础设施 + 决策日志

1. 创建 `src/observability/` 包（tracer, metrics, decision_log, setup）
2. 添加 ObservabilityConfig
3. Gateway startup 初始化
4. 决策日志埋点（Planner, Router, Executor, Selector）
5. `/debug/metrics` 端点

产出: 结构化 JSONL 决策日志，可通过 grep/jq 查询

### Phase 2: Span 追踪 + LLM/MCP 指标

1. 节点装饰器 `traced_node`
2. Graph 节点包装
3. LLM callback handler
4. 注入 LLM callback
5. MCP 工具调用指标采集

产出: Span 树 + LLM/MCP 延迟和 token 指标

### Phase 3: OTEL 导出 + 可视化

1. OTEL setup 逻辑
2. pyproject.toml 可选依赖
3. docker-compose 加 Jaeger/Grafana（可选）
4. Dashboard 模板

产出: 完整的可视化面板

## Alert Rules (Future)

| Alert | Condition | Severity |
|-------|-----------|----------|
| Workflow 超时 | `workflow.duration_ms P95 > 120s` | Warning |
| 任务失败率突增 | `task.failure.total rate > 0.2 (5min)` | Critical |
| LLM 延迟飙升 | `llm.call.duration_ms P95 > 30s` | Warning |
| MCP 服务不可用 | `mcp.call.total{success=false} rate > 0.5` | Critical |
| 干预堆积 | `intervention.total rate > 10/min` | Warning |

## Risks

### Risk 1: OTel 依赖引入

Mitigation: OTel 为可选依赖，不安装时所有功能降级为 noop，不影响运行。

### Risk 2: 埋点对性能的影响

Mitigation: span 和 metrics 的 record 操作是非阻塞的；decision_log 写入是同步但轻量的 JSON 序列化 + logger.info。

### Risk 3: 决策日志量大

Mitigation: 每次 workflow 约产生 10-30 条决策记录，日志量可控；file output 模式下可配置 rotation。

## Done Definition

此 feature 完成当：

1. 每次 workflow 执行的关键决策都有结构化 JSON 审计日志
2. 通过 `run_id` 可以查询到完整的决策链
3. LLM 和 MCP 调用延迟、token 消耗可通过 `/debug/metrics` 或 OTel 查看
4. 现有前端行为和 API 契约完全不受影响
5. 未安装 OTel 依赖时系统正常运行，仅缺少 trace 导出能力

# Workflow Phase 0 Baseline And Metrics Backend Checklist

- Status: `draft`
- Owner: `backend`
- Related feature:
  - [workflow-phase0-baseline-and-metrics.md](./workflow-phase0-baseline-and-metrics.md)
- Frontend impact target: `none required`

## 0. Current Architecture Analysis

### Backend Runtime Topology

当前后端分为两部分：

1. `LangGraph Server`
   - 负责 `entry_graph`、`lead_agent`、`multi_agent`
   - 负责 workflow 执行、状态推进、stream/custom event 输出
2. `Gateway API`
   - 负责 agents、uploads、artifacts、memory、skills、models、interventions 等 REST 接口

本次 Phase 0 最相关的现有代码文件：

- `backend/src/agents/entry_graph.py`
- `backend/src/agents/graph.py`
- `backend/src/agents/orchestration/selector.py`
- `backend/src/agents/planner/node.py`
- `backend/src/agents/router/semantic_router.py`
- `backend/src/agents/executor/executor.py`
- `backend/src/agents/thread_state.py`
- `backend/src/observability/node_wrapper.py`
- `backend/src/observability/metrics.py`
- `backend/src/observability/decision_log.py`
- `backend/tests/test_multi_agent_core.py`
- `backend/tests/test_multi_agent_graph.py`
- `backend/tests/test_observability.py`

### What Already Exists And Can Be Reused

- `workflow` 主链路已经可作为 benchmark 的真实执行目标
- `ThreadState` 已经提供 baseline 断言所需的核心状态
- `task_pool` 已经是 workflow 任务状态的后端真相源
- `verified_facts` 已经是任务级结构化事实容器
- `node_wrapper` 与 `WorkflowMetrics` 已经提供最基础的可观测挂点
- 现有图级测试证明可以通过 stub / mocking 做 deterministic workflow 执行

### What Does Not Exist Yet

- baseline case schema
- baseline case loader
- benchmark runner
- case assertion engine
- benchmark report generator
- baseline case 目录规范
- baseline 专属指标汇总口径

### Important Clarification

当前 `observability` 不等于 Phase 0 的 baseline 体系。

现有 observability 主要解决：

- tracing
- decision log
- LLM call metrics

但它还没有解决：

- benchmark case 的统一执行
- case pass / fail / error 的标准化表达
- suite 级报告
- baseline 回归比较

因此，Phase 0 本质上是在现有 runtime 和 observability 之上补一层“评测层”。

### Runtime Boundary Clarification

后端同学需要特别避免一个常见误解：

- Phase 0 不是只做一个“假 workflow runner”
- Phase 0 也不是直接把真实 MCP / 真实外部系统接进 CI

本阶段要求的是：

- 跑真实编译出的 workflow graph
- 走真实 planner / router / executor 主链路
- 走真实 `ThreadState`、条件边和 reducer
- 但把 LLM、MCP、外部系统结果替换成 deterministic fixture

所以 Phase 0 验证的是“真实 runtime 是否正确”，不是“真实外部世界是否可用”。

## 1. Implementation Guardrails

- [ ] 不改任何 `frontend/` 文件
- [ ] 不新增前端依赖的 API 契约
- [ ] baseline 主路径不依赖真实 MCP 或外部服务
- [ ] 不把 baseline runner 写成一次性脚本
- [ ] 不把 case schema 写死在测试代码里
- [ ] 不把某个具体业务场景硬编码到框架层
- [ ] Phase 0 必须优先支持 deterministic CI
- [ ] 不能影响现有 workflow 主执行路径的对外行为

Done when:

- baseline 可独立于前端运行
- baseline 可在无外部依赖的 CI 中执行
- 当前业务运行逻辑无需前端配合即可部署

## 2. New Backend Scope

建议新增如下结构：

```text
backend/
  benchmarks/
    README.md
    phase0/
      meeting/
      contacts/
      hr/
      workflows/

backend/src/evals/
  __init__.py
  schema.py
  loader.py
  fixtures.py
  assertions.py
  collector.py
  runner.py
  report.py
  cli.py
```

说明：

- `backend/benchmarks/` 用于存放 case 数据
- `backend/src/evals/` 用于存放执行逻辑
- 不单独建立 `regressions/` 顶层目录
- 回归样本放回所属目录，并打 `regression` tag

不建议把 benchmark case 塞进 `backend/tests/`，因为：

- `tests/` 主要验证代码正确性
- `benchmarks/` 主要验证系统能力与回归基线
- 两者生命周期和维护方式不同

## 3. Phase 0 Deliverables

### 3.1 Case Schema

- [ ] 新建 `backend/src/evals/schema.py`
- [ ] 定义 `BenchmarkCase` 主模型
- [ ] 定义以下子结构：
  - `CaseInput`
  - `CaseExpected`
  - `CaseLimits`
  - `CaseFixtureConfig`
  - `CaseTags`

### 3.2 Loader

- [ ] 新建 `backend/src/evals/loader.py`
- [ ] 支持从 `backend/benchmarks/phase0/**.yaml` 加载
- [ ] 支持按 `suite / domain / category / tags / case-id` 过滤
- [ ] 对非法 schema 直接报错，不允许静默跳过

### 3.3 Fixture Runtime

- [ ] 新建 `backend/src/evals/fixtures.py`
- [ ] 为 deterministic baseline 提供 stub runtime
- [ ] 至少支持：
  - planner / model stub
  - domain-agent stub
  - MCP / 外部系统 stub
  - custom event collector

### 3.4 Assertion Engine

- [ ] 新建 `backend/src/evals/assertions.py`
- [ ] 实现最小断言能力：
  - `resolved_orchestration_mode`
  - `assigned_agent` / `assigned_agents`
  - `task status`
  - `clarification happened`
  - `intervention happened`
  - `verified_facts exists`
  - `final_result contains / not_contains`
  - `task_count <= limit`
  - `route_count <= limit`

### 3.5 Collector

- [ ] 新建 `backend/src/evals/collector.py`
- [ ] 统一收集：
  - final state
  - captured events
  - duration
  - task / route / clarification / intervention 计数
  - available llm metrics snapshot

### 3.6 Runner

- [ ] 新建 `backend/src/evals/runner.py`
- [ ] runner 负责：
  - 加载 case
  - 准备 fixture runtime
  - 执行 graph / case
  - 收集 state / events / metrics
  - 执行断言
  - 输出 `CaseRunResult`

### 3.7 Report

- [ ] 新建 `backend/src/evals/report.py`
- [ ] 支持生成：
  - `json` 详细报告
  - `markdown` 汇总报告
- [ ] 报告至少包含：
  - case id
  - suite / domain / category
  - pass / fail / error
  - failed assertions
  - run duration
  - llm metrics
  - task / clarification / intervention 摘要

### 3.8 CLI Entry

- [ ] 新建 `backend/src/evals/cli.py`
- [ ] 提供统一入口，例如：

```bash
uv run python -m src.evals.cli run --suite phase0-core
```

- [ ] 支持参数：
  - `--suite`
  - `--domain`
  - `--tag`
  - `--case-id`
  - `--output-dir`

## 4. Case Schema Requirements

### 4.1 Required Fields

每条 case 至少应包含：

```yaml
id: workflow.contacts.to.meeting.basic
title: 先查联系人再预定会议室
suite: phase0-core
domain: workflows
category: happy_path
type: workflow

input:
  message: 帮我先查王明的员工编号，再帮他预定明天下午三点的会议室

fixtures:
  profile: contacts_to_meeting_basic

expected:
  resolved_orchestration_mode: workflow
  assigned_agents:
    - contacts-agent
    - meeting-agent
  clarification_expected: false
  intervention_expected: false
  final_result_contains:
    - 员工编号
    - 会议室
  verified_facts_min_count: 2

limits:
  max_route_count: 4
  max_task_count: 3
  max_duration_ms: 10000

tags:
  - workflow
  - cross_domain
```

### 4.2 Supported Domain Values

Phase 0 只允许以下 `domain`：

- `meeting`
- `contacts`
- `hr`
- `workflows`

### 4.3 Unsupported Assertions

以下能力不作为 Phase 0 必需能力：

- 精确 tool 调用序列断言
- token 级逐条对比
- 真实外部系统结果比对
- 跨运行历史趋势分析

如果 schema 中预留了这些字段，runner 必须明确：

- 要么显式报 unsupported
- 要么文档声明本阶段不校验

不能出现“字段存在但实际上不生效”的隐性设计。

## 5. Runner Output Contract

### 5.1 CaseRunResult

- [ ] 定义 `CaseRunResult`
- [ ] 建议字段：
  - `case_id`
  - `status`: `passed | failed | error | skipped`
  - `duration_ms`
  - `resolved_orchestration_mode`
  - `assigned_agents`
  - `task_count`
  - `route_count`
  - `clarification_count`
  - `intervention_count`
  - `verified_fact_count`
  - `llm_metrics`
  - `failed_assertions`
  - `error`

### 5.2 SuiteRunResult

- [ ] 定义 `SuiteRunResult`
- [ ] 建议字段：
  - `suite`
  - `started_at`
  - `finished_at`
  - `total`
  - `passed`
  - `failed`
  - `error`
  - `case_results`
  - `aggregate_metrics`

## 6. Metrics Collection Decision

### 6.1 Existing Gap

当前后端已有可观测能力，但对 baseline 还缺：

- suite 维度 duration
- task 数
- route 数
- clarification / intervention 次数
- case 级错误归类

### 6.2 Required Decision

实现时必须明确二选一：

#### Option A: 补 runtime 埋点

在 workflow 主链路中补齐 `WorkflowMetrics` 调用，让 runner 直接读增量 metrics。

优点：

- baseline 与线上运行更一致

缺点：

- 需要改动 runtime 文件更多

#### Option B: Runner 自采集

runner 通过：

- wall clock
- final state
- captured events
- decision log

自行汇总 Phase 0 指标。

优点：

- 对现有 runtime 更保守

缺点：

- baseline 指标与 runtime 指标口径可能并存

### 6.3 Recommended Decision

建议采用：

> `Option B` 为主，`Option A` 只补最小缺口

即：

- Phase 0 报告口径先以 runner 自采集为主
- 如果补 runtime 埋点成本很低，可以顺手补齐
- 不因为追求统一 metrics 而阻塞 baseline runner 落地

## 7. Files To Add

- [ ] `backend/src/evals/__init__.py`
- [ ] `backend/src/evals/schema.py`
- [ ] `backend/src/evals/loader.py`
- [ ] `backend/src/evals/fixtures.py`
- [ ] `backend/src/evals/assertions.py`
- [ ] `backend/src/evals/collector.py`
- [ ] `backend/src/evals/runner.py`
- [ ] `backend/src/evals/report.py`
- [ ] `backend/src/evals/cli.py`
- [ ] `backend/benchmarks/README.md`
- [ ] `backend/benchmarks/phase0/...`

## 8. Files That May Need Modification

- [ ] `backend/src/observability/metrics.py`
  - 如需新增 reset / snapshot / delta 能力
- [ ] `backend/src/agents/orchestration/selector.py`
  - 如需更稳定地暴露 run metrics 起点
- [ ] `backend/src/agents/router/semantic_router.py`
  - 如需补齐 clarification / intervention 计数采集
- [ ] `backend/src/agents/executor/executor.py`
  - 如需更直接地输出 task 结果摘要

## 9. Files That Must Not Be Modified In Phase 0

- [ ] 所有 `frontend/` 文件
- [ ] intervention resolve API request / response contract
- [ ] `task_pool` 基本语义定义
- [ ] 现有 workflow 事件名称

## 10. Required Baseline Suites

### 10.1 Meeting

- [ ] 预定会议室 happy path
- [ ] 缺时间 / 人数 / 主题时触发 clarification
- [ ] 房间冲突后的结果处理
- [ ] 依赖联系人信息后继续预定
- [ ] 修改 / 取消场景中的治理路径

### 10.2 Contacts

- [ ] 按姓名查员工
- [ ] 查 openId
- [ ] 同名歧义 clarification
- [ ] 查无此人
- [ ] 只读场景不应误触发 intervention

### 10.3 HR

- [ ] 查考勤
- [ ] 查请假 / 假期余额
- [ ] 缺身份信息 clarification
- [ ] 无法处理或权限不足时的合理输出

### 10.4 Cross-domain Workflows

- [ ] `contacts -> meeting`
- [ ] `contacts -> hr`
- [ ] clarification 后 resume
- [ ] dependency helper 回写后 resume

说明：

- 本阶段跨域 workflow 只围绕 `meeting`、`contacts`、`hr`
- 不再扩到其它 domain agent

### 10.5 Regression Tagging Rule

- [ ] meeting 域真实 bug 的回归 case 放入 `meeting/` 并打 `regression`
- [ ] contacts 域真实 bug 的回归 case 放入 `contacts/` 并打 `regression`
- [ ] hr 域真实 bug 的回归 case 放入 `hr/` 并打 `regression`
- [ ] 跨域真实 bug 的回归 case 放入 `workflows/` 并打 `regression`

## 11. Backend Acceptance Criteria

- [ ] 可以通过统一命令运行 Phase 0 baseline
- [ ] 所有 case 都通过 schema 校验
- [ ] 可以按 `suite / domain / tag / case-id` 过滤执行
- [ ] 每次运行都会产出 `json + markdown` 报告
- [ ] 报告包含 case 级错误归因
- [ ] deterministic baseline 不依赖真实 MCP
- [ ] 至少一组 `phase0-core` 可在 CI 稳定执行

## 12. Recommended Implementation Order

1. 先做 `schema + loader`
2. 再做 `fixtures + collector + runner`
3. 再做 `assertions + report`
4. 最后接 `cli + CI`

原因：

- 先把 case 规范和执行骨架定住
- 避免后端先写 runner，测试后面才发现 case 表达能力不够

## 13. Done Definition

本文件完成后，后端交付的不是“几条测试”，而是：

- 一套 baseline case 规范
- 一套 benchmark 执行器
- 一套稳定输出报告的评测能力

只有做到这三点，才算完成 Phase 0 的后端部分。

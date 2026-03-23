# Workflow Phase 0 Baseline And Metrics Backend Checklist

- Status: `done`
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

- ~~baseline case schema~~ ✅ done
- ~~baseline case loader~~ ✅ done
- ~~benchmark runner~~ ✅ done
- ~~case assertion engine~~ ✅ done
- ~~benchmark report generator~~ ✅ done
- ~~baseline case 目录规范~~ ✅ done
- ~~baseline 专属指标汇总口径~~ ✅ done

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

因此，Phase 0 本质上是在现有 runtime 和 observability 之上补一层"评测层"。

### Runtime Boundary Clarification

后端同学需要特别避免一个常见误解：

- Phase 0 不是只做一个"假 workflow runner"
- Phase 0 也不是直接把真实 MCP / 真实外部系统接进 CI

本阶段要求的是：

- 跑真实编译出的 workflow graph
- 走真实 planner / router / executor 主链路
- 走真实 `ThreadState`、条件边和 reducer
- 但把 LLM、MCP、外部系统结果替换成 deterministic fixture

所以 Phase 0 验证的是"真实 runtime 是否正确"，不是"真实外部世界是否可用"。

## 1. Implementation Guardrails

- [x] 不改任何 `frontend/` 文件
- [x] 不新增前端依赖的 API 契约
- [x] baseline 主路径不依赖真实 MCP 或外部服务
- [x] 不把 baseline runner 写成一次性脚本
- [x] 不把 case schema 写死在测试代码里
- [x] 不把某个具体业务场景硬编码到框架层
- [x] Phase 0 必须优先支持 deterministic CI
- [x] 不能影响现有 workflow 主执行路径的对外行为

Done when:

- ✅ baseline 可独立于前端运行
- ✅ baseline 可在无外部依赖的 CI 中执行
- ✅ 当前业务运行逻辑无需前端配合即可部署

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

✅ 已按此结构实现。

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

- [x] 新建 `backend/src/evals/schema.py`
- [x] 定义 `BenchmarkCase` 主模型
- [x] 定义以下子结构：
  - `CaseInput`
  - `CaseExpected`
  - `CaseLimits`
  - `CaseFixtureConfig`
  - `CaseTags`（使用 `list[str]` 字段）

### 3.2 Loader

- [x] 新建 `backend/src/evals/loader.py`
- [x] 支持从 `backend/benchmarks/phase0/**.yaml` 加载
- [x] 支持按 `suite / domain / category / tags / case-id` 过滤
- [x] 对非法 schema 直接报错，不允许静默跳过

### 3.3 Fixture Runtime

- [x] 新建 `backend/src/evals/fixtures.py`
- [x] 为 deterministic baseline 提供 stub runtime
- [x] 至少支持：
  - planner / model stub（`_PlannerStubLLM`）
  - domain-agent stub（`_StubDomainAgent`）
  - MCP / 外部系统 stub（`AsyncMock` for `_ensure_mcp_ready`）
  - custom event collector（via patched `get_stream_writer`）
- [x] 通过 `build_fixture_patches()` 上下文管理器驱动真实编译图执行

### 3.4 Assertion Engine

- [x] 新建 `backend/src/evals/assertions.py`
- [x] 实现最小断言能力：
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

- [x] 新建 `backend/src/evals/collector.py`
- [x] 统一收集：
  - final state（从真实 ThreadState 提取）
  - captured events
  - duration
  - task / route / clarification / intervention 计数
  - available llm metrics snapshot

### 3.6 Runner

- [x] 新建 `backend/src/evals/runner.py`
- [x] runner 负责：
  - 加载 case
  - 准备 fixture runtime（`build_fixture_patches()`）
  - 执行 graph / case（`build_multi_agent_graph_for_test()` + `graph.ainvoke()`）
  - 收集 state / events / metrics
  - 执行断言
  - 输出 `CaseRunResult`

### 3.7 Report

- [x] 新建 `backend/src/evals/report.py`
- [x] 支持生成：
  - `json` 详细报告
  - `markdown` 汇总报告
- [x] 报告至少包含：
  - case id
  - suite / domain / category
  - pass / fail / error
  - failed assertions
  - run duration
  - llm metrics
  - task / clarification / intervention 摘要

### 3.8 CLI Entry

- [x] 新建 `backend/src/evals/cli.py`
- [x] 提供统一入口，例如：

```bash
uv run python -m src.evals.cli run --suite phase0-core
```

- [x] 支持参数：
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

✅ 已实现，所有模型使用 `extra="forbid"` 严格校验。

### 4.2 Supported Domain Values

Phase 0 只允许以下 `domain`：

- `meeting`
- `contacts`
- `hr`
- `workflows`

✅ 通过 `CaseDomain` 枚举实现。

### 4.3 Unsupported Assertions

以下能力不作为 Phase 0 必需能力：

- 精确 tool 调用序列断言
- token 级逐条对比
- 真实外部系统结果比对
- 跨运行历史趋势分析

如果 schema 中预留了这些字段，runner 必须明确：

- 要么显式报 unsupported
- 要么文档声明本阶段不校验

不能出现"字段存在但实际上不生效"的隐性设计。

✅ 未预留上述字段，schema 使用 `extra="forbid"` 确保不会有未声明字段。

## 5. Runner Output Contract

### 5.1 CaseRunResult

- [x] 定义 `CaseRunResult`
- [x] 建议字段：
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

- [x] 定义 `SuiteRunResult`
- [x] 建议字段：
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

- ~~suite 维度 duration~~ ✅ done
- ~~task 数~~ ✅ done
- ~~route 数~~ ✅ done
- ~~clarification / intervention 次数~~ ✅ done
- ~~case 级错误归类~~ ✅ done

### 6.2 Required Decision

✅ 采用 **Option B: Runner 自采集**。

collector 从真实 ThreadState 字段（`task_pool`、`verified_facts`、`route_count`、`messages`）提取指标，不依赖额外的 runtime 埋点。

### 6.3 Recommended Decision

建议采用：

> `Option B` 为主，`Option A` 只补最小缺口

✅ 已按此实施。

## 7. Files To Add

- [x] `backend/src/evals/__init__.py`
- [x] `backend/src/evals/schema.py`
- [x] `backend/src/evals/loader.py`
- [x] `backend/src/evals/fixtures.py`
- [x] `backend/src/evals/assertions.py`
- [x] `backend/src/evals/collector.py`
- [x] `backend/src/evals/runner.py`
- [x] `backend/src/evals/report.py`
- [x] `backend/src/evals/cli.py`
- [x] `backend/benchmarks/README.md`
- [x] `backend/benchmarks/phase0/...`（19 个 YAML 用例）

## 8. Files That May Need Modification

- [ ] `backend/src/observability/metrics.py`
  - 如需新增 reset / snapshot / delta 能力
  - ✅ Phase 0 未修改，collector 通过 ThreadState 自采集
- [ ] `backend/src/agents/orchestration/selector.py`
  - 如需更稳定地暴露 run metrics 起点
  - ✅ Phase 0 未修改
- [ ] `backend/src/agents/router/semantic_router.py`
  - 如需补齐 clarification / intervention 计数采集
  - ✅ Phase 0 未修改
- [ ] `backend/src/agents/executor/executor.py`
  - 如需更直接地输出 task 结果摘要
  - ✅ Phase 0 未修改

## 9. Files That Must Not Be Modified In Phase 0

- [x] 所有 `frontend/` 文件 — 未修改
- [x] intervention resolve API request / response contract — 未修改
- [x] `task_pool` 基本语义定义 — 未修改
- [x] 现有 workflow 事件名称 — 未修改

## 10. Required Baseline Suites

### 10.1 Meeting

- [x] 预定会议室 happy path — `meeting.happy_path.basic`
- [x] 缺时间 / 人数 / 主题时触发 clarification — `meeting.clarification.missing_time`
- [x] 房间冲突后的结果处理 — `meeting.conflict.room`
- [x] 依赖联系人信息后继续预定 — `meeting.dependency.contacts`
- [x] 修改 / 取消场景中的治理路径 — `meeting.governance.cancel` + `meeting.governance.cancel_rejected`

### 10.2 Contacts

- [x] 按姓名查员工 — `contacts.happy_path.by_name`
- [x] 查 openId — `contacts.happy_path.query_openid`
- [x] 同名歧义 clarification — `contacts.ambiguity.same_name`
- [x] 查无此人 — `contacts.not_found.unknown_person`
- [x] 只读场景不应误触发 intervention — `contacts.read_only.no_intervention`

### 10.3 HR

- [x] 查考勤 — `hr.happy_path.attendance`
- [x] 查请假 / 假期余额 — `hr.happy_path.leave_balance`
- [x] 缺身份信息 clarification — `hr.clarification.identity`
- [x] 无法处理或权限不足时的合理输出 — `hr.unsupported.permission_denied`

### 10.4 Cross-domain Workflows

- [x] `contacts -> meeting` — `workflow.contacts.to.meeting.basic`
- [x] `contacts -> hr` — `workflow.contacts.to.hr.basic`
- [x] clarification 后 resume — `workflow.clarification.resume`
- [x] dependency helper 回写后 resume — `workflow.dependency.helper_resume`

说明：

- 本阶段跨域 workflow 只围绕 `meeting`、`contacts`、`hr`
- 不再扩到其它 domain agent

### 10.5 Regression Tagging Rule

- [x] meeting 域真实 bug 的回归 case 放入 `meeting/` 并打 `regression`
- [x] contacts 域真实 bug 的回归 case 放入 `contacts/` 并打 `regression`
- [x] hr 域真实 bug 的回归 case 放入 `hr/` 并打 `regression`
- [x] 跨域真实 bug 的回归 case 放入 `workflows/` 并打 `regression`

✅ tagging 机制已支持，当前无已知 regression case。

## 11. Backend Acceptance Criteria

- [x] 可以通过统一命令运行 Phase 0 baseline — `uv run python -m src.evals.cli run --suite phase0-core`
- [x] 所有 case 都通过 schema 校验 — 19/19 通过
- [x] 可以按 `suite / domain / tag / case-id` 过滤执行
- [x] 每次运行都会产出 `json + markdown` 报告
- [x] 报告包含 case 级错误归因
- [x] deterministic baseline 不依赖真实 MCP
- [x] 至少一组 `phase0-core` 可在 CI 稳定执行 — 19/19 pass, 87 unit tests

## 12. Recommended Implementation Order

1. ~~先做 `schema + loader`~~ ✅
2. ~~再做 `fixtures + collector + runner`~~ ✅
3. ~~再做 `assertions + report`~~ ✅
4. ~~最后接 `cli + CI`~~ ✅

原因：

- 先把 case 规范和执行骨架定住
- 避免后端先写 runner，测试后面才发现 case 表达能力不够

## 13. Done Definition

本文件完成后，后端交付的不是"几条测试"，而是：

- ✅ 一套 baseline case 规范（Pydantic strict schema + 19 YAML cases）
- ✅ 一套 benchmark 执行器（真实图执行 + stub 外部依赖）
- ✅ 一套稳定输出报告的评测能力（JSON + Markdown + CLI）

只有做到这三点，才算完成 Phase 0 的后端部分。

**Status: DONE** — 19/19 cases passing, 87 unit tests passing, 0 impact on existing functionality.

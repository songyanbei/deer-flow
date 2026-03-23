# Feature: Workflow Phase 0 Baseline And Metrics

- Status: `draft`
- Owner suggestion: `backend` + `test`
- Related area: workflow mode, domain agents, baseline replay, evaluation metrics
- Frontend impact: `none required`

## Goal

为当前 DeerFlow 的 `workflow` 体系建立第一版“基线盘点与度量体系”，让团队可以稳定、重复、可比较地评估现有三类领域 agent 的能力表现。

本次 Phase 0 的目标不是扩新功能，而是补齐一套可落地的评测基础设施：

1. 一套统一的 baseline case 规范
2. 一套可重复执行 baseline case 的后端 runner
3. 一套可输出结果、评分和对比报告的 metrics/report 机制

## Scope

本次测试覆盖范围只包含以下两层：

1. 三个领域 agent 的单域测试
   - `meeting-agent`
   - `contacts-agent`
   - `hr-agent`
2. 这三个领域之间的跨域 workflow 测试
   - `contacts -> meeting`
   - `contacts -> hr`
   - 必要时包含 `meeting <-> contacts` 的依赖回写场景

说明：

- clarification / intervention / resume 仍然需要覆盖
- 但它们只作为上述单域或跨域场景里的断言维度出现
- 本阶段不额外拆一套“通用 interruption suite”

## Why This Needs Backend/Test Collaboration

本阶段不要求前端改动，但非常依赖后端与测试共同定义边界。

### Backend 负责

- baseline case 的加载与校验
- runner、fixture runtime、断言执行器、报告生成器
- workflow 执行结果与指标采集
- 与现有 `ThreadState`、`task_pool`、`verified_facts`、`observability` 对接

### Test 负责

- baseline case 设计与分类
- 三个领域与跨域链路的覆盖策略
- 验收断言是否足够表达真实业务需求
- CI 接入、失败归类、回归样本补充规则

如果没有统一文档，容易出现的问题是：

- 后端把 runner 做成一次性脚本，后续难复用
- 测试把用例写成零散数据，后续难回归
- 团队混淆“运行时可观测性”和“结果基线评测”

## Current Behavior

### Backend

结合当前代码，后端已经具备以下基础：

1. `entry_graph` 支持 `leader` 与 `workflow` 双模式分流
2. `workflow` 已经形成 `planner -> router -> executor` 主链路
3. `ThreadState` 已包含 `task_pool`、`verified_facts`、`workflow_stage`、`route_count`
4. `observability` 已提供 tracing、decision log、LLM metrics、`/debug/metrics`
5. 仓库中已有多智能体图级测试和 executor/intervention 相关测试，可作为 stub / fixture 模式的基础

但当前仍缺少：

1. baseline case 的统一目录和 schema
2. case loader
3. 可回放的 benchmark runner
4. 面向业务验收的结构化断言引擎
5. 统一的 JSON / Markdown 报告
6. 面向 baseline 的指标汇总口径

### Frontend

当前前端已经能展示 workflow 进度、clarification、intervention。
但 Phase 0 本身不要求前端增加入口或页面。

## In Scope

1. 为 `meeting`、`contacts`、`hr`、`workflows` 建立 baseline suite
2. 定义 Phase 0 case schema
3. 实现后端 baseline runner
4. 生成结构化执行报告
5. 输出第一版对比指标
6. 建立 CI 可跑的 deterministic baseline
7. 补齐配套的后端开发文档与测试文档

## Out Of Scope

1. 前端工作台改造
2. 线上可视化 dashboard
3. 独立的通用 interruption suite
4. 独立的 regressions 顶层 suite
5. 面向 `leader` 模式的全量 benchmark
6. 真实 MCP / 外部系统联调作为主路径

## Frozen Decisions For Phase 0

### 1. Baseline 默认使用 Deterministic Fixtures

Phase 0 的 baseline 必须能在本地和 CI 中稳定运行，因此默认规则如下：

- baseline 主路径不依赖真实 MCP
- baseline 主路径不依赖外部在线系统
- baseline 通过 stub / fixture / mock runtime 执行
- 真实环境 smoke test 不是本阶段主门槛

### 2. Baseline 是 Backend/Test 内部能力，不做前端入口

Phase 0 统一通过后端命令入口运行，不新增 UI。

推荐入口：

- `pytest`
- `python -m src.evals.cli`

### 3. 本阶段优先做 Agent / Workflow 级断言

本阶段的最小闭环断言聚焦以下对象：

- `resolved_orchestration_mode`
- `assigned_agent` 或 `assigned_agents`
- `task_pool` 里的状态流转
- clarification / intervention 是否发生
- `verified_facts`
- `final_result`
- duration / route_count / task_count 等指标

精细的 tool-level 断言可以预留字段，但不是 Phase 0 主验收项。

### 4. Phase 0 Tests The Real Workflow Runtime, Not The Real External World

这条边界需要明确写死，避免后续误解：

- Phase 0 跑的是当前真实的 workflow graph
- 真实进入 `planner -> router -> executor` 主链路
- 真实使用当前 `ThreadState`、条件边、reducer、clarification / intervention / resume 路径
- 但会用 stub / fixture / mock 替代外部依赖

也就是说，Phase 0 替换的是：

- LLM 输出
- MCP 初始化与外部工具调用
- domain agent 的外部世界返回值

而不是替换：

- graph 编译
- 节点调用顺序
- 条件边跳转
- 状态合并逻辑
- workflow 中断与恢复语义

因此，Phase 0 能验证的是：

- 当前 workflow runtime 和状态机是否正确
- graph 流转、条件边、reducer、resume 是否回归
- 在“给定可控输入”的前提下，系统是否会把流程走对

Phase 0 不能直接代表的是：

- 真实 LLM 的稳定性与输出质量
- 真实 MCP / 真实业务系统的超时、权限、脏数据、认证、限流问题
- 真实外部依赖下的最终业务效果

一句话总结：

> Phase 0 是“真实 workflow runtime 的 deterministic baseline”，不是“真实外部世界的验收测试”。

### 5. 回归样本不单独建顶层 Suite

真实 bug 产生的回归 case 仍然归入：

- `meeting/`
- `contacts/`
- `hr/`
- `workflows/`

通过 `regression` tag 管理，而不是新建 `regressions/` 根目录。

## Contract To Confirm First

- Case storage:
  - 统一放在 `backend/benchmarks/phase0/`
- Case format:
  - YAML
- Runner entry:
  - 统一从后端命令入口运行
- Determinism:
  - 默认 fixture-driven
- Output:
  - 每次执行输出 `json + markdown` 报告
- Metrics source:
  - 以 runner 自采集为主，必要时补 runtime metrics
- CI behavior:
  - 至少一组 `phase0-core` 可稳定接入 CI

## Backend Changes

- 新增 baseline case schema
- 新增 loader
- 新增 runner
- 新增 assertion engine
- 新增 report generator
- 新增 benchmark 目录规范
- 视需要补齐 `WorkflowMetrics` 的 reset / snapshot / delta 能力

## Test Changes

- 建立三个领域 agent 的 baseline 用例
- 建立三者之间的跨域 workflow baseline 用例
- 在这些 case 中覆盖 clarification / intervention / resume
- 建立 regression tag 的补充规则
- 建立 deterministic baseline 的 CI 接入方式

## Risks

- 如果主路径依赖真实 MCP，baseline 会不稳定
- 如果 schema 设计过重，后端和测试落地成本会过高
- 如果只有 runner 没有报告与统一断言，后续仍难比较改动收益
- 如果把现有 observability 误当成 baseline 体系，会导致边界混乱

## Acceptance Criteria

- 可以通过统一入口运行 `phase0-core` baseline suite
- baseline 用例至少覆盖：
  - `meeting-agent`
  - `contacts-agent`
  - `hr-agent`
  - 这三者之间的跨域 workflow
- 单域或跨域 case 中需要包含若干 clarification / intervention / resume 场景
- 每次运行都能产出结构化报告
- 报告至少包含：
  - case 结果
  - fail / error 分类
  - wall clock
  - LLM metrics
  - task 数量与中断次数
- CI 中至少有一组 deterministic baseline 可以稳定通过

## Related Detailed Docs

- [workflow-phase0-baseline-and-metrics-backend-checklist.md](./workflow-phase0-baseline-and-metrics-backend-checklist.md)
- [workflow-phase0-baseline-and-metrics-test-checklist.md](./workflow-phase0-baseline-and-metrics-test-checklist.md)

## Open Questions

- 是否需要在 Phase 0 同时提供一个连接真实 MCP 的可选 smoke suite
- baseline 报告是否需要入库，还是只保留在 CI artifact
- `phase0-core` 与 `phase0-full` 的切分阈值是否由 case 数量还是 case tag 决定

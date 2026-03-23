# Workflow Phase 0 Baseline And Metrics Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature:
  - [workflow-phase0-baseline-and-metrics.md](./workflow-phase0-baseline-and-metrics.md)
  - [workflow-phase0-baseline-and-metrics-backend-checklist.md](./workflow-phase0-baseline-and-metrics-backend-checklist.md)
- Frontend impact target: `none required`

## 0. Test Objective

测试团队在 Phase 0 的目标不是“再补几条单测”，而是帮助团队建立一套长期可维护的 baseline 体系。

本阶段测试文档要保证三件事：

1. baseline case 明确覆盖 `meeting-agent`、`contacts-agent`、`hr-agent` 三个领域，以及它们之间的跨域 workflow
2. benchmark runner 的结果可信、可重复、可回归
3. 后续新增场景或修复真实 bug 时，团队知道该如何补 baseline

## 1. Test Strategy Overview

Phase 0 测试分成 4 层：

### A. Schema / Loader 层

验证：

- case YAML 是否可解析
- 非法字段是否会被拒绝
- 缺少必要字段是否会报错
- `suite / domain / tag / case-id` 过滤是否准确

### B. Assertion Engine 层

验证：

- 每类断言都能正确通过 / 失败
- 失败时能返回明确错误原因
- 不支持的断言类型会显式报错，而不是静默忽略

### C. Runner 层

验证：

- runner 能装载 fixture runtime
- runner 能正确执行 case
- runner 能收集 `state / events / metrics`
- runner 能输出 `passed / failed / error / skipped`

### D. Baseline Suite 层

验证：

- 三个领域 case 是否覆盖当前能力边界
- 跨域 workflow case 是否覆盖依赖、resume 和路由链路
- clarification / intervention / resume 是否在这些场景里被正确断言

## 2. Current Codebase Testing Situation

当前仓库已经有较好的测试基础，可直接复用：

- `backend/tests/test_multi_agent_core.py`
  - 适合复用 stub graph 测试方式
- `backend/tests/test_multi_agent_graph.py`
  - 适合复用 end-to-end 图级测试结构
- `backend/tests/test_observability.py`
  - 可复用 metrics / report 断言风格
- `backend/tests/test_observability_integration.py`
  - 可复用 callback / tracing 集成思路
- `backend/tests/test_executor_*`
  - 可复用 clarification / intervention / resume 场景样式

但当前测试仍缺：

- 面向 baseline 的 case 目录管理
- schema / loader 自动化验证
- benchmark runner 验证
- suite 报告与聚合指标验证

## 3. Determinism Rules

Phase 0 baseline 的首要要求是稳定。

测试必须遵守：

- [ ] baseline 主路径不依赖真实 MCP
- [ ] baseline 主路径不依赖真实网络
- [ ] baseline 主路径不依赖外部在线数据
- [ ] baseline 主路径不依赖非确定性的时间窗口
- [ ] 同一套 fixture 在本地与 CI 结果一致

Done when:

- 同一条 case 连续运行多次结果稳定
- CI 不会因为外部依赖波动而随机失败

### Boundary Clarification

测试侧需要明确区分两类验证：

1. Phase 0 baseline
   - 跑真实 workflow graph
   - stub 外部依赖
   - 验证 runtime / 状态机 / graph 流转是否正确
2. live smoke / 真实环境验证
   - 连接真实 MCP / 真实系统
   - 验证真实外部依赖下的业务效果

因此，Phase 0 的测试目标不是证明“真实外部世界完全可用”，而是证明“当前系统在真实代码路径下不会把流程走错”。

## 4. Required Test Coverage By Module

### 4.1 Schema Tests

- [ ] 最小合法 case 可通过
- [ ] 缺少 `id` 报错
- [ ] 缺少 `input.message` 报错
- [ ] 缺少 `expected` 报错
- [ ] 非法 `domain` 报错
- [ ] 非法 `category` 报错
- [ ] 非法断言字段报错
- [ ] 非法 YAML 结构报错

Done when:

- schema 相关错误都能明确定位到 case 文件与字段

### 4.2 Loader Tests

- [ ] 加载单个 case
- [ ] 加载整个 suite
- [ ] 按 `domain` 过滤
- [ ] 按 `tag` 过滤
- [ ] 按 `case-id` 过滤
- [ ] 非法文件阻断整个执行并给出错误

Done when:

- loader 不会把错误 case 静默跳过

### 4.3 Assertion Engine Tests

- [ ] `resolved_orchestration_mode` 断言通过 / 失败
- [ ] `assigned_agent` / `assigned_agents` 断言通过 / 失败
- [ ] `clarification_expected` 断言通过 / 失败
- [ ] `intervention_expected` 断言通过 / 失败
- [ ] `verified_facts_min_count` 断言通过 / 失败
- [ ] `final_result_contains` 断言通过 / 失败
- [ ] `final_result_not_contains` 断言通过 / 失败
- [ ] `max_route_count` 限制通过 / 失败
- [ ] `max_task_count` 限制通过 / 失败
- [ ] unsupported 断言类型必须显式报错

Done when:

- 每个失败断言都返回清晰、可读、可定位的错误原因

### 4.4 Runner Tests

- [ ] 单个 case 执行成功并生成 `CaseRunResult`
- [ ] case fixture 缺失时返回 `error`
- [ ] case assertion 不通过时返回 `failed`
- [ ] runner 能收集 workflow 终态
- [ ] runner 能收集 custom events
- [ ] runner 能收集 metrics 快照
- [ ] runner 支持多 case 批量执行

Done when:

- runner 的状态区分清晰：
  - `passed`
  - `failed`
  - `error`
  - `skipped`

### 4.5 Report Tests

- [ ] JSON 报告结构正确
- [ ] Markdown 报告包含汇总信息
- [ ] fail / error case 在报告中有明确归因
- [ ] aggregate metrics 计算正确

Done when:

- 测试同学可以只看报告快速判断失败原因

## 5. Required Baseline Case Coverage

本节是本阶段最重要的测试需求，必须保证覆盖范围与业务边界一致。

### 5.1 Meeting Agent Cases

至少覆盖以下场景：

- [ ] happy path：直接预定会议室
- [ ] clarification：缺少时间 / 人数 / 主题时触发澄清
- [ ] dependency：依赖联系人信息后继续预定
- [ ] conflict：资源冲突时返回合理结果
- [ ] governance：修改 / 取消等高风险路径触发正确处理

### 5.2 Contacts Agent Cases

至少覆盖以下场景：

- [ ] happy path：按姓名查询员工信息
- [ ] happy path：查询 openId
- [ ] ambiguity：同名人员触发澄清
- [ ] not_found：查无此人
- [ ] read_only：只读场景不应误触发 intervention

### 5.3 HR Agent Cases

至少覆盖以下场景：

- [ ] happy path：查询考勤
- [ ] happy path：查询请假 / 假期余额
- [ ] clarification：缺少身份信息时澄清
- [ ] unsupported / denied：无法处理或权限不足

### 5.4 Cross-domain Workflow Cases

至少覆盖以下场景：

- [ ] `contacts -> meeting`
- [ ] `contacts -> hr`
- [ ] dependency helper 完成后 parent task resume
- [ ] clarification 之后仍在同一链路继续执行

说明：

- 本阶段跨域场景只围绕 `meeting`、`contacts`、`hr`
- 不扩展到其它 domain agent

### 5.5 Clarification / Intervention / Resume Coverage Rule

本阶段不单独做一套通用 interruption suite，而是在上述 case 中覆盖：

- [ ] clarification 进入等待态
- [ ] clarification 回答后正确 resume
- [ ] intervention 触发后进入等待态
- [ ] intervention resolve / reject 后结果正确
- [ ] clarification 与 intervention 不混淆

### 5.6 Regression Coverage Rule

真实 bug 的回归 case 仍归入现有四类目录：

- [ ] meeting 相关 bug 放入 `meeting/` 并打 `regression`
- [ ] contacts 相关 bug 放入 `contacts/` 并打 `regression`
- [ ] hr 相关 bug 放入 `hr/` 并打 `regression`
- [ ] 跨域 bug 放入 `workflows/` 并打 `regression`

## 6. Minimum Suite Size Recommendation

考虑到本阶段范围已经收敛，建议首批规模：

- `meeting-agent`：8-10 条
- `contacts-agent`：6-8 条
- `hr-agent`：6-8 条
- `workflows`：6-8 条

总规模建议：

- [ ] 至少 `26-34` 条 deterministic baseline case

说明：

- 少于 20 条，覆盖很容易不足
- 超过 40 条，第一阶段维护成本会明显上升

## 7. CI Strategy

### 7.1 Must-Have CI Suite

- [ ] `phase0-core`

包含：

- 少量关键 meeting case
- 少量关键 contacts case
- 少量关键 hr case
- 至少 2 条跨域 workflow
- 至少 2 条带 clarification / intervention / resume 的 case

要求：

- 运行稳定
- 耗时可控
- 可作为 PR 门禁

### 7.2 Optional Suites

- [ ] `phase0-full`
  - 全量 deterministic suite
- [ ] `phase0-live-smoke`
  - 如果未来需要，可连接真实 MCP / 真实依赖

但：

- `phase0-live-smoke` 不得作为当前主门禁

## 8. Regression Update Rule

后续每出现以下情况，测试同学都需要判断是否补 baseline：

- [ ] 新增 meeting / contacts / hr 相关能力边界
- [ ] 新增三者之间新的跨域链路
- [ ] 新增一种新的 clarification / intervention 处理模式
- [ ] 线上出现一次真实 bug
- [ ] 某一类路由错误重复出现

原则：

> 不是“每个需求都加一大套 case”，而是“每种新的能力边界、失败模式、风险模式，都要进入 baseline”。

## 9. Test Exit Criteria

- [ ] schema / loader / assertion / runner / report 都有自动化测试
- [ ] `phase0-core` 在 CI 中稳定运行
- [ ] 当前三个领域与它们之间的关键 workflow 有 baseline 覆盖
- [ ] 报告可以直接帮助研发定位失败
- [ ] 回归样本有明确增补规则

## 10. Done Definition

Phase 0 测试部分完成，不是指“写完几条 pytest”，而是指：

- 团队拥有一套稳定的 baseline suite
- 新增三类领域能力或修复真实 bug 时，知道如何补 case
- 基线结果可以持续比较

只有做到这三点，测试侧才算真正完成 Phase 0。

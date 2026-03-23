# Workflow Engine Registry Phase 1 Test Checklist

- Status: `in_progress (phase1 core coverage done, build-time hook addendum pending)`
- Owner: `test`
- Related feature:
  - [workflow-engine-registry-phase1.md](./workflow-engine-registry-phase1.md)
  - [workflow-engine-registry-phase1-backend-checklist.md](./workflow-engine-registry-phase1-backend-checklist.md)
- Frontend impact target: `none required in this phase`

## 0. Test Objective

Phase 1 测试的目标不是验证“有没有 `engine_type` 字段”，而是验证：

1. registry 是否真的成为唯一可信的引擎解析入口
2. config / CRUD / runtime 是否对 `engine_type` 有一致理解
3. workflow domain agent 是否能在不改变调度语义的前提下，正确使用对应 engine builder

补充说明：

- 上述 Phase 1 核心测试目标，当前已经基本覆盖。
- 本次新增需求主要针对 build-time Hook Harness addendum。
- 也就是在不改 workflow 调度语义的前提下，补测 build-time hooks 的 contract、顺序和默认 no-op 行为。

## 1. Test Strategy Overview

Phase 1 测试分成 5 层：

### A. Registry Unit Tests

验证：

- canonical name 解析
- alias 解析
- unknown fallback
- supported engine list

### B. Builder Unit Tests

验证：

- 各 engine builder 的 prompt mode
- 各 engine builder 的 tool policy
- `read_only_explorer` 的工具过滤行为

### C. Config / CRUD Contract Tests

验证：

- config loader 是否正确读取 `engine_type`
- create / get / update API 是否正确读写 `engine_type`
- alias 输入是否被正确归一化

### D. Runtime / Workflow Integration Tests

验证：

- `make_lead_agent()` 是否通过 registry 构建
- workflow domain agent 是否按 config 选择 engine
- clarification / intervention 既有链路是否未被改坏

### E. Build-Time Hook Contract Tests

验证：

- `before_agent_build`
- `after_agent_build`
- `before_skill_resolve`
- `before_mcp_bind`
- 默认 no-op hook 是否保持现有行为
- hook 调用顺序和触发时机是否稳定

## 2. Current Testing Baseline

当前可直接复用的测试基础：

- `backend/tests/test_engine_registry.py`
  - 已有 registry / builder / CRUD / config / fallback 测试基础
- `backend/tests/test_lead_agent_model_resolution.py`
  - 已有 runtime build / `engine_mode` 测试基础
- `backend/tests/test_multi_agent_graph.py`
  - 已有 workflow domain agent 图级集成测试基础

但当前缺少：

- build-time hook 的独立测试
- hook 默认 no-op 行为测试
- hook 顺序与触发时机测试
- hook 接入后不改既有 runtime build 结果的回归断言

### 2.1 Current Completion Snapshot

结合当前代码和测试，可以确认 Phase 1 核心覆盖已经存在：

- registry 行为已可独立断言
- builder 行为已可独立断言
- config / CRUD round-trip 已有测试
- runtime build 和 workflow 主链路已有回归基础

因此本 checklist 当前需要新增关注的是：

- build-time hook contract
- build-time hook 的默认 no-op 行为
- hook 接入后的 runtime build 不回归

## 3. Test Guardrails

- [ ] 不把测试目标扩展成 workflow 调度改造验证
- [ ] 不要求前端 UI 配合
- [ ] 不要求真实 MCP 联调
- [ ] 不要求新增业务 engine 类型
- [ ] 不把 Phase 1 测试写成只验证 prompt 文案

Done when:

- 测试焦点稳定落在 registry / builder / CRUD / runtime build

### Boundary Clarification

测试侧需要特别避免两种误读：

1. “workflow integration tests” 的含义
   - 是验证 registry 接入后，当前 workflow 主链路不回归
   - 不是要求新增一套新的 workflow 图、调度策略或并行执行测试体系
2. “API contract tests” 的含义
   - 是验证后端 Agent CRUD 对 `engine_type` 的读写行为
   - 不是要求前端 Agent 管理页面一起联调

## 4. Required Test Coverage By Module

### 4.1 Registry Tests

- [ ] canonical `default` -> default builder
- [ ] canonical `react` -> react builder
- [ ] canonical `read_only_explorer` -> read_only_explorer builder
- [ ] canonical `sop` -> sop builder
- [ ] alias `ReAct` -> `react`
- [ ] alias `ReadOnly_Explorer` -> `read_only_explorer`
- [ ] alias `readonly` -> `read_only_explorer`
- [ ] alias `SOP` / `sop_engine` -> `sop`
- [ ] unknown engine -> warning + default fallback
- [ ] supported engine list 返回完整 canonical 集合

Done when:

- registry 行为不再依赖 `agent.py` 的隐式判断

### 4.2 Builder Tests

- [ ] default builder 保持默认行为
- [ ] react builder 输出正确的 prompt mode
- [ ] sop builder 输出正确的 prompt mode
- [ ] read_only_explorer builder 会过滤非只读工具
- [ ] builder 不会错误修改与 engine 无关的 runtime 选项

Done when:

- 每个 builder 的差异都是独立可断言的

### 4.3 Config Loader Tests

- [ ] `config.yaml` 包含 canonical `engine_type` 时可正确加载
- [ ] `config.yaml` 包含 alias 时可正确归一化或正确解析
- [ ] `config.yaml` 缺失 `engine_type` 时保持默认
- [ ] `config.yaml` 包含未知值时触发 fallback 行为

Done when:

- loader 与 runtime 对 `engine_type` 的理解一致

### 4.4 Agent CRUD Tests

- [ ] create agent 时可写入 `engine_type`
- [ ] get agent 时返回 `engine_type`
- [ ] update agent 时可修改 `engine_type`
- [ ] alias 作为输入时，返回值是 canonical
- [ ] 持久化到 `config.yaml` 的值是 canonical

Done when:

- API 行为、文件持久化、返回结果三者一致

### 4.5 Runtime Build Tests

- [ ] `make_lead_agent()` 使用 registry，而不是本地 if/else
- [ ] `react` engine 会把正确的 `engine_mode` 传给 prompt
- [ ] `sop` engine 会把正确的 `engine_mode` 传给 prompt
- [ ] `read_only_explorer` engine 会过滤不合规 MCP tool
- [ ] `default` engine 保持当前行为不回归

Done when:

- runtime build 测试能证明“registry 已接管构建分发”

### 4.6 Workflow Integration Tests

- [ ] workflow domain agent 使用配置的 `engine_type`
- [ ] engine registry 接入后，既有 workflow happy path 不回归
- [ ] engine registry 接入后，clarification 流程不回归
- [ ] engine registry 接入后，request_help / helper resume 流程不回归
- [ ] engine registry 接入后，intervention 流程不回归

Done when:

- Phase 1 的改动没有破坏现有 workflow 运行主链路

### 4.7 Build-Time Hook Tests

- [ ] `before_agent_build` 在 agent build 入口被触发
- [ ] `after_agent_build` 在 agent 完成构建后被触发
- [ ] `before_skill_resolve` 在 skill allowlist / skill 解析前被触发
- [ ] `before_mcp_bind` 在 per-agent MCP tools 装配前被触发
- [ ] 默认 no-op hooks 不改变现有 engine registry 构建结果
- [ ] hooks 的触发顺序稳定且可断言
- [ ] hooks 接入后，既有 workflow happy path 不回归

Done when:

- build-time hooks 已成为可验证 contract，而不是仅存在于实现假设中

## 5. Required Regression Matrix

### 5.1 Compatibility Regressions

- [ ] 历史 alias 仍可被接受
- [ ] 未设置 `engine_type` 的 agent 仍按 default 运行
- [ ] 手工写入未知 engine_type 的旧配置不会直接炸运行时

### 5.2 Behavior Regressions

- [ ] default engine 的 prompt 行为不回归
- [ ] read-only 过滤只影响 `read_only_explorer`
- [ ] `react` 和 `sop` 不应错误过滤工具
- [ ] 默认 no-op hooks 不应改变既有 builder 行为

### 5.3 API Regressions

- [ ] 旧 agent 在 GET / list 中不会因缺少 `engine_type` 失败
- [ ] update 未传 `engine_type` 时不应误清空既有值

## 6. Minimum Test Suite Recommendation

建议至少形成以下测试规模：

- registry / builder 单元测试：8-12 条
- config / CRUD 契约测试：6-8 条
- runtime build 测试：4-6 条
- workflow 集成 / 回归测试：4-6 条
- build-time hook 测试：6-8 条

总量建议：

- [ ] 至少 `28-40` 条测试覆盖

## 7. CI Strategy

### 7.1 Must-Have Suites

- [ ] `engine-registry-unit`
  - registry + builder
- [ ] `engine-registry-api`
  - config + CRUD
- [ ] `engine-registry-workflow`
  - runtime build + workflow regression
- [ ] `engine-registry-hooks`
  - build-time hook contract + no-op regression

### 7.2 Merge Gate Requirement

至少满足：

- registry / builder 单元测试通过
- CRUD 契约测试通过
- 既有 workflow 图级关键测试通过

## 8. Test Exit Criteria

- [ ] registry、builder、CRUD、runtime、workflow、build-time hooks 六层都有自动化覆盖
- [ ] alias、canonical、unknown fallback 行为有明确断言
- [ ] workflow 关键主链路回归通过
- [ ] 报错和 fallback 行为对测试同学可观察、可验证

## 9. Done Definition

Phase 1 测试完成，不是指“字段测到了”，而是指：

- registry 的行为被独立验证
- builder 差异被独立验证
- config / CRUD / runtime 三层契约被统一验证
- workflow 集成未回归

本轮新增 addendum 完成后，还需要额外满足：

- build-time hooks 的 contract 被独立验证
- 默认 no-op hooks 不改变现有行为
- hooks 接入后 workflow 主链路不回归

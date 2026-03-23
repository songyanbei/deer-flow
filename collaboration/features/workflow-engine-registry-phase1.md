# Feature: Workflow Engine Registry Phase 1

- Status: `done`
- Owner suggestion: `backend` + `test`
- Related area: custom agents, workflow domain agents, lead-agent runtime, agent CRUD
- Frontend impact: `none required in this phase`

## Goal

基于当前已经存在的轻量 `engine_type` 能力，完成第一阶段的 Engine Registry 化改造。

本阶段的目标不是引入更多 engine 类型，也不是改 workflow 编排策略，而是把当前“engine_type 只是 prompt / tool 行为开关”的状态，升级为“有明确注册表、明确 builder、明确配置入口、明确测试契约”的后端能力。

Phase 1 完成后，需要达成以下结果：

1. `engine_type` 不再只是散落在 `make_lead_agent()` 里的条件判断
2. 引擎选择逻辑有统一 registry 和 builder 抽象
3. Agent 配置加载、Gateway CRUD、运行时构建、测试用例，对 `engine_type` 的理解一致
4. 新增一个同类 engine 时，不需要继续把逻辑硬塞进 `agent.py`

补充说明：

- 上述 Phase 1 核心目标，后端已经基本完成。
- 当前重新打开这份文档，不是推翻已完成实现，而是在原 Phase 1 基础上补一批 `build-time Hook Harness` 要求。
- 这批新增要求的目标是：把 engine registry 已经建立起来的“构建期分层”，继续向统一 build-time hook 扩一小步，为后续 Hook Harness 打地基。

## Why This Needs Backend/Test Collaboration

本需求没有前端改造，但它会直接影响：

- custom agent 的配置模型
- Agent CRUD API 的读写契约
- workflow domain agent 的运行时构建
- 测试对 engine 行为的断言方式

因此必须由后端和测试共同收口：

### Backend 负责

- 抽离 engine registry / builder
- 打通配置模型与 Agent CRUD
- 确保运行时使用 registry 进行 engine 解析与构建
- 补齐引擎相关的兼容策略和日志策略

### Test 负责

- 校验 registry 行为、别名兼容、fallback / validation 行为
- 校验 Agent CRUD 对 `engine_type` 的读写契约
- 校验 workflow domain agent 在不同 engine 下的构建行为
- 补齐回归用例，避免后续 engine 扩展时破坏现有 agent

## Current Behavior

### Current Runtime Status

结合当前代码，现状是：

1. `AgentConfig` 已经有 `engine_type` 字段
2. `engine_registry.py` 已升级为正式 registry，支持：
   - canonical / alias 归一化
   - builder 分发
   - unknown fallback
   - supported engine list
3. `make_lead_agent()` 已经通过 `get_engine_builder(...)` 构建 agent
4. `backend/src/agents/lead_agent/engines/` 已存在独立 builder：
   - `default`
   - `react`
   - `read_only_explorer`
   - `sop`
5. Agent CRUD 已经暴露 `engine_type`，并支持 canonical 化持久化
6. 后端测试中已经有独立的 registry / CRUD / runtime build 覆盖

这说明 Phase 1 的 Engine Registry 核心目标已经基本落地。

### Current Gaps

Phase 1 全部缺口已关闭，包括：

1. **build-time hook contract 已建立** — `BuildContext` + `BuildTimeHooks` 定义在 `engines/base.py`
   - `before_agent_build` / `after_agent_build` / `before_skill_resolve` / `before_mcp_bind` 四个 hook 已有显式 contract
   - 默认 no-op 实现不改变现有行为
   - `get_build_time_hooks()` / `set_build_time_hooks()` 提供模块级单例管理

2. **build-time hook 已接入 `make_lead_agent()`**
   - 正常构建路径：4 个 hook 按文档顺序依次触发
   - Bootstrap 路径：触发 `before_agent_build` + `after_agent_build`
   - `BuildContext.extra_tools` 可写字段已正确回写到最终 agent 工具列表
   - `BuildContext.available_skills` 可写字段已正确回写到 prompt 模板

3. **build-time hook 独立测试已覆盖**
   - `test_build_time_hooks.py` 覆盖 20+ 用例
   - 包括：no-op 行为、调用顺序、mutation 能力、lifecycle 管理、make_lead_agent 集成

## Current Architecture Analysis

本需求相关的核心后端文件如下：

- `backend/src/config/agents_config.py`
  - 当前 `AgentConfig` 已有 `engine_type`
- `backend/src/gateway/routers/agents.py`
  - 当前 Agent CRUD 已暴露 `engine_type`，并支持 canonical 化持久化
- `backend/src/agents/lead_agent/engine_registry.py`
  - 当前已是正式 registry，负责 canonical/alias 解析与 builder 分发
- `backend/src/agents/lead_agent/agent.py`
  - 当前已接入 `get_engine_builder(...)`，但 build-time hooks 还未显式抽象
- `backend/src/agents/executor/executor.py`
  - workflow domain agent 统一从这里进入 `make_lead_agent()`
- `backend/src/agents/graph.py`
  - workflow 主链路不应因为本次改造改变语义

现状可以概括为：

- workflow domain agent 的”调度”已经成立
- domain agent 的”执行引擎分层”核心已经成立
- build-time hooks 已完成，4 个 hook 已接入 `make_lead_agent()` 并通过独立测试验证

## In Scope

1. 将当前轻量 `engine_type` 解析升级为正式 registry
2. 为当前已支持的 engine 建立独立 builder 抽象
3. 打通 `AgentConfig`、config loader、Agent CRUD、运行时构建链路
4. 保留兼容别名与安全 fallback 策略
5. 补齐引擎相关单元测试、API 测试、workflow 集成测试
6. 在 Phase 1 范围内补齐第一批 build-time hooks：
   - `before_agent_build`
   - `after_agent_build`
   - `before_skill_resolve`
   - `before_mcp_bind`

本阶段 registry 至少覆盖以下 engine：

- `default`
- `react`
- `read_only_explorer`
- `sop`

说明：

- 这些 engine 是“Phase 1 正式注册支持的 engine”
- 不要求本阶段新增 `planner / worker / verifier` 等新 engine

## Out Of Scope

1. workflow planner / router / executor 的调度语义改造
2. 并行 task scheduler
3. 前端 Agent 管理 UI 改造
4. 新增更多 engine 家族
5. 真实业务 SOP 规则大改
6. MCP 分类与装配体系的单独重构
7. node / task / tool / interrupt / state commit 的广义 Hook Harness

## Frozen Decisions For Phase 1

### 1. Phase 1 只做 Runtime / Config / API 的 Engine Registry 化

这次不改 workflow 的编排主链路，只改 agent 构建层。

也就是说：

- planner 不变
- router 不变
- executor 的任务分配语义不变
- engine 选择发生在“agent 构建”阶段，而不是“workflow 任务调度”阶段

### 2. `engine_type` 需要有 Canonical 值

Phase 1 统一以以下 canonical 值作为正式 engine 类型：

- `default`
- `react`
- `read_only_explorer`
- `sop`

允许保留历史别名作为兼容输入，但：

- 写入配置时应尽量持久化为 canonical 值
- 测试和文档以 canonical 值为准

### 3. Runtime 继续保留 Safe Fallback

对于手工编辑 YAML 或历史配置中的未知 `engine_type`，本阶段运行时继续允许安全 fallback 到 `default`，并记录 warning。

原因：

- 避免 Phase 1 因配置清洗问题直接打断现有环境
- 但 API 层与测试层应尽量把无效值提前暴露出来

### 4. Engine Builder 是本阶段的最小落地点

本阶段 registry 不是只有一个 alias map，也不是完整多生命周期框架。

必须至少形成：

- registry
- builder interface
- 每个 engine 的独立 builder 实现
- 统一的运行时解析入口

### 5. Phase 1 Addendum 只补 Build-Time Hooks，不扩成全域 Hook Framework

本次新增需求只要求把 engine registry 已经形成的 build-time 分层，继续往前推进一小步。

明确包含：

- `before_agent_build`
- `after_agent_build`
- `before_skill_resolve`
- `before_mcp_bind`

明确不包含：

- `before_tool_call`
- `after_tool_call`
- `before_interrupt_emit`
- `before_task_pool_commit`
- 任何 workflow scheduling hook

### 6. Phase 1 Does Not Change Workflow Scheduling

这一条需要明确写死，避免和后续调度改造混淆：

- Phase 1 会继续复用当前真实的 workflow runtime path
- 但 Phase 1 不修改 planner / router / executor 的任务流转语义
- workflow integration 的含义是“验证 registry 接入后，现有 workflow 主链路不回归”
- 不是“在 Phase 1 重做 workflow 编排、并行调度或新增执行图”

也就是说，本阶段改的是：

- agent config
- agent CRUD
- engine registry
- engine builder
- make_lead_agent 构建路径

不是改：

- workflow graph 结构
- task_pool 状态机
- executor 分发策略
- clarification / intervention 协议

## Contract To Confirm First

- Storage field:
  - `engine_type`
- Canonical values:
  - `default | react | read_only_explorer | sop`
- Runtime behavior:
  - 未知值 warning + fallback default
- API behavior:
  - 对外暴露 `engine_type`
- Registry boundary:
  - engine 负责构建期差异，不负责 workflow 调度
- Workflow integration boundary:
  - 只验证“当前 workflow 主链路继续可用”，不新增新的调度能力
- Test baseline:
  - 以 registry / CRUD / runtime build 三层为主

## Backend Changes

- 新增正式的 engine registry / builder 抽象
- 将 `make_lead_agent()` 中的 engine 差异抽离出去
- 暴露 `engine_type` 到 Agent CRUD API
- 对 config loader / API 输入做 canonicalization 和兼容处理
- 补齐 engine 相关日志与错误策略
- 补齐第一批 build-time hook contract 与 no-op 默认实现
- 在 agent build / skill resolve / MCP bind 三处提供显式扩展点

## Test Changes

- 新增 registry / builder 单元测试
- 新增 Agent CRUD 的 `engine_type` 契约测试
- 新增 runtime build 与 workflow domain agent 的集成测试
- 新增兼容别名与 fallback 回归测试
- 新增 build-time hook contract 测试：
  - 默认 no-op 不改变现有行为
  - hook 调用顺序稳定
  - hook 可观测、可断言

## Risks

- 如果 registry 抽象过重，会拖慢 Phase 1 落地
- 如果只做 alias map，不做 builder 分层，后续仍会继续污染 `agent.py`
- 如果 API、config、runtime 三层对 `engine_type` 的 canonical 值理解不一致，会导致测试和线上行为不一致
- 如果 Phase 1 越界去改 workflow 调度，会放大风险

## Acceptance Criteria

- `engine_type` 已成为正式的 Agent 配置项，并贯通：
  - config loader
  - Agent CRUD API
  - runtime builder
- runtime 构建不再把 engine 差异硬编码在 `agent.py` 中
- 当前四类 engine 都能通过 registry 正确解析
- 历史 alias 输入有明确兼容策略
- 未知值有明确 fallback 与 warning 行为
- workflow domain agent 在不改变调度语义的前提下，能按配置使用对应 engine builder
- 第一批 build-time hooks 已有明确 contract，并且不改变现有 workflow 调度语义
- 测试覆盖 registry、CRUD、runtime build、workflow 集成、build-time hooks 五层

## Related Detailed Docs

- [workflow-engine-registry-phase1-backend-checklist.md](./workflow-engine-registry-phase1-backend-checklist.md)
- [workflow-engine-registry-phase1-test-checklist.md](./workflow-engine-registry-phase1-test-checklist.md)

## Open Questions

- ~~API 层是否要只接受 canonical 值，还是同时接受 alias 并在写入时归一化~~ **已决定：API 接受 alias 输入，写入时归一化为 canonical 值**
- 是否需要在 Agent 列表接口中同步返回 `supported_engine_types`（Phase 1 未实现，可作为后续增强）

## Completion Summary

Phase 1 全部工作已完成，包括核心 Engine Registry 和 Build-Time Hook Addendum：

- **Engine Registry**: `engine_registry.py` 提供 canonical/alias 解析、builder 分发、unknown fallback
- **Engine Builders**: 4 个独立 builder (`default`, `react`, `read_only_explorer`, `sop`) 在 `engines/` 下
- **Runtime Integration**: `make_lead_agent()` 通过 registry 获取 builder，engine 差异不再硬编码在 `agent.py`
- **CRUD Integration**: Agent API 支持 `engine_type` 读写，alias 输入归一化为 canonical 值持久化
- **Build-Time Hooks**: `BuildContext` + `BuildTimeHooks` contract 已建立，4 个 hook 在 `make_lead_agent()` 中按序触发
- **测试覆盖**: 55+ engine registry 测试 + 20+ build-time hook 测试 + 14 lead agent model resolution 测试

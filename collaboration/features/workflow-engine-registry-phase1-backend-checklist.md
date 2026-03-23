# Workflow Engine Registry Phase 1 Backend Checklist

- Status: `done`
- Owner: `backend`
- Related feature:
  - [workflow-engine-registry-phase1.md](./workflow-engine-registry-phase1.md)
- Frontend impact target: `none required in this phase`

## 0. Current Architecture Analysis

### 0.1 Current Runtime Path

当前 workflow domain agent 的核心执行路径是：

1. workflow graph 进入 `executor`
2. `executor` 根据 `assigned_agent` 选择领域 agent
3. 领域 agent 仍然通过 `make_lead_agent()` 构建
4. `make_lead_agent()` 读取 `AgentConfig`
5. `get_engine_builder(...)` 把 `engine_type` 解析为正式 builder
6. `make_lead_agent()` 根据 builder 调整：
   - prompt 的 `engine_mode`
   - MCP tools 的 read-only 过滤

这说明：

- 当前已经有正式 engine 解析入口
- 且已经有独立的 engine builder 分层

### 0.2 Current Relevant Files

- `backend/src/config/agents_config.py`
  - `AgentConfig.engine_type`
- `backend/src/gateway/routers/agents.py`
  - Agent CRUD 已暴露 `engine_type`，并支持 canonical 化持久化
- `backend/src/agents/lead_agent/engine_registry.py`
  - 当前正式 registry
- `backend/src/agents/lead_agent/agent.py`
  - 已通过 builder 接入 engine 差异，并已接入 4 个 build-time hooks
- `backend/src/agents/executor/executor.py`
  - workflow domain agent 的运行入口
- `backend/tests/test_engine_registry.py`
  - 已有 registry / builder / CRUD / config 测试基础
- `backend/tests/test_build_time_hooks.py`
  - build-time hook contract / integration 的 20+ 独立测试
- `backend/tests/test_lead_agent_model_resolution.py`
  - 已有 runtime build / engine_mode 相关测试基础

### 0.3 Current Gap Summary

Phase 1 全部缺口已关闭：

1. ~~build-time hook 的显式 contract~~ → 已在 `engines/base.py` 定义 `BuildContext` + `BuildTimeHooks`
2. ~~agent build / skill resolve / MCP bind 的统一扩展点~~ → 已在 `make_lead_agent()` 中接入 4 个 hook
3. ~~hook 默认 no-op 行为与顺序约束~~ → 默认 no-op 不改行为，调用顺序已测试验证
4. ~~针对 build-time hook 的独立测试与验收标准~~ → `test_build_time_hooks.py` 覆盖 20+ 用例

### 0.4 Current Completion Snapshot

Phase 1 全部完成，包括核心 Engine Registry 和 Build-Time Hook Addendum：

- `backend/src/agents/lead_agent/engines/` 已存在 `base.py / default.py / react.py / read_only_explorer.py / sop.py`
- `engine_registry.py` 已提供 `normalize_engine_type(...)`、`get_engine_builder(...)`、`list_supported_engine_types(...)`
- `agent.py` 已通过 `get_engine_builder(...)` 构建领域 agent，并接入 4 个 build-time hooks
- `agents.py` 已对 create / get / update / list 打通 `engine_type`
- `engines/base.py` 已定义 `BuildContext` + `BuildTimeHooks` contract，含 `get/set_build_time_hooks()` 单例管理
- `backend/tests/test_engine_registry.py` 已覆盖 registry / CRUD / config / runtime 的 55+ 测试
- `backend/tests/test_build_time_hooks.py` 已覆盖 build-time hook contract / integration 的 20+ 测试

## 1. Implementation Guardrails

- [x] 不修改 workflow planner / router / executor 的调度语义
- [x] 不修改前端文件
- [x] 不在本阶段新增 engine 家族
- [x] 不在本阶段重构 MCP 整体架构
- [x] 不把 engine registry 做成与 workflow 调度耦合的机制
- [x] 不在 `agent.py` 里继续追加新的 engine if/else

Done when:

- engine 变化只影响 agent 构建层
- workflow 任务流转语义保持不变
- Phase 1 改动能被清晰限制在 registry / builder / CRUD / config 范围

### Boundary Clarification

后端同学在实现时，容易误读的点有两个，这里明确写死：

1. “workflow integration” 不等于“修改 workflow 图”
   - 本阶段只是让 workflow domain agent 的构建过程改走 registry
   - 不是改 planner / router / executor 的调度或状态机
2. “打通 Agent CRUD” 不等于“要做前端管理面改造”
   - 本阶段只要求后端 API 和 config 契约打通
   - 不要求前端 UI 或 Agent 管理页面一起落地

## 2. Target Backend Structure

建议在现有目录下形成如下结构：

```text
backend/src/agents/lead_agent/
  engine_registry.py
  engines/
    __init__.py
    base.py
    default.py
    react.py
    read_only_explorer.py
    sop.py
```

说明：

- `engine_registry.py` 负责注册、解析、alias 归一化、builder 分发
- `engines/base.py` 定义 builder interface / context
- 各 engine 文件只负责各自构建差异

## 3. Required Backend Deliverables

### 3.1 Engine Builder Interface

- [x] 新建 `backend/src/agents/lead_agent/engines/base.py`
- [x] 定义统一的 builder 接口，至少包括：
  - `canonical_name`
  - `aliases`
  - `build_prompt_kwargs(...)`
  - `prepare_extra_tools(...)`
  - `prepare_runtime_options(...)`

说明：

- 本阶段 builder 只要求覆盖“构建期差异”
- 不要求实现完整生命周期 hook 框架

### 3.2 Engine Builders

- [x] 新建 `default` builder
- [x] 新建 `react` builder
- [x] 新建 `read_only_explorer` builder
- [x] 新建 `sop` builder

各 builder 至少要明确：

- canonical 名称
- alias 列表
- prompt mode
- 是否过滤 read-only tools
- 是否保留默认工具装配策略

### 3.3 Formal Registry

- [x] 将 `engine_registry.py` 升级为正式 registry
- [x] 支持：
  - 通过 canonical name 获取 builder
  - 通过 alias 解析 canonical name
  - 未知 engine 的安全 fallback
  - 对外查询当前支持的 engine 集

registry 至少提供以下能力：

- `normalize_engine_type(raw: str | None) -> str | None`
- `get_engine_builder(raw: str | None) -> BaseEngineBuilder`
- `list_supported_engine_types() -> list[str]`

### 3.4 Runtime Integration

- [x] `make_lead_agent()` 改为通过 registry 获取 builder
- [x] `agent.py` 中不再直接判断 `engine_behavior.filter_read_only_tools`
- [x] prompt 的 `engine_mode` 由 builder 提供
- [x] tool 过滤由 builder 提供

说明：

- `make_lead_agent()` 仍是统一入口
- 但 engine 相关差异必须收敛到 builder 层

### 3.5 Config Layer Integration

- [x] `AgentConfig` 保留 `engine_type`
- [x] 明确 loader 对 `engine_type` 的处理策略：
  - 支持 alias 输入
  - 建议归一化为 canonical
  - 未知值保留 warning + fallback

建议：

- `AgentConfig` 仍允许 `str | None`
- 不在 Phase 1 直接把 loader 改成强校验失败
- 以兼容历史 YAML 为先

### 3.6 Agent CRUD Integration

- [x] `backend/src/gateway/routers/agents.py` 的以下模型需要补上 `engine_type`：
  - `AgentResponse`
  - `AgentCreateRequest`
  - `AgentUpdateRequest`
- [x] `_build_config_data(...)` 需要支持写入 `engine_type`
- [x] GET / POST / PUT 对 `engine_type` 的行为必须一致

推荐策略：

- API 输入允许 alias
- 写入配置时统一持久化为 canonical 值
- API 输出返回 canonical 值

### 3.7 Optional Capability

- [ ] 如实现成本低，可在 agents API 中增加 `supported_engine_types` 辅助信息

但说明：

- 这不是 Phase 1 主门槛
- 不能为了这个把 API 改成另一个需求

### 3.8 Phase 1 Addendum: Build-Time Hook Harness

- [x] 为 build-time hooks 建立最小 contract，至少覆盖：
  - `before_agent_build`
  - `after_agent_build`
  - `before_skill_resolve`
  - `before_mcp_bind`
- [x] 为上述 hooks 提供默认 no-op 实现，确保不接入扩展时行为与当前一致
- [x] 明确 hook 的输入上下文与允许修改的字段边界
- [x] 确保 hook 只作用于 agent 构建期，不侵入 workflow 调度语义
- [x] 在 `make_lead_agent()` 周边接入这些 hooks，而不是把逻辑继续写回散落分支

实现说明：

- `BuildContext` 定义在 `engines/base.py`，包含只读字段（`agent_name`, `engine_type`, `model_name`, `is_domain_agent`, `is_bootstrap`）和可写字段（`available_skills`, `extra_tools`, `metadata`）
- `BuildTimeHooks` 提供 4 个默认 no-op 方法，通过 `get/set_build_time_hooks()` 管理模块级单例
- `make_lead_agent()` 在正常路径按 `before_agent_build → before_skill_resolve → before_mcp_bind → after_agent_build` 顺序调用
- Bootstrap 路径触发 `before_agent_build` + `after_agent_build`
- Hook 通过 `BuildContext.extra_tools` 注入的工具会正确合并到最终 agent 工具列表
- Hook 通过 `BuildContext.available_skills` 修改的技能集会正确回写到 prompt 模板

## 4. Runtime Contract

### 4.1 Supported Canonical Engine Types

Phase 1 正式支持：

- `default`
- `react`
- `read_only_explorer`
- `sop`

### 4.2 Alias Compatibility

建议至少兼容当前历史输入：

- `ReAct` -> `react`
- `react` -> `react`
- `ReadOnly_Explorer` / `readonly` / `readonly_explorer` -> `read_only_explorer`
- `SOP` / `sop_engine` -> `sop`

### 4.3 Unknown Engine Strategy

未知 `engine_type` 时：

- 记录 warning
- builder 回退到 `default`
- 输出中保留原始值用于日志 / 排障

本阶段不建议：

- 运行时直接抛异常阻塞 agent 启动

## 5. API Contract Changes

### 5.1 Create / Update Request

请求体应支持：

```yaml
engine_type: react
```

### 5.2 Response

返回体应包含：

```yaml
engine_type: react
```

### 5.3 Persistence Rule

`config.yaml` 中最终持久化建议使用 canonical 值，例如：

```yaml
name: contacts-agent
domain: contacts
engine_type: read_only_explorer
requested_orchestration_mode: workflow
```

## 6. Files To Add

- [x] `backend/src/agents/lead_agent/engines/__init__.py`
- [x] `backend/src/agents/lead_agent/engines/base.py`
- [x] `backend/src/agents/lead_agent/engines/default.py`
- [x] `backend/src/agents/lead_agent/engines/react.py`
- [x] `backend/src/agents/lead_agent/engines/read_only_explorer.py`
- [x] `backend/src/agents/lead_agent/engines/sop.py`

## 7. Files To Modify

- [x] `backend/src/agents/lead_agent/engine_registry.py`
- [x] `backend/src/agents/lead_agent/agent.py`
- [x] `backend/src/config/agents_config.py`
- [x] `backend/src/gateway/routers/agents.py`

如需最小辅助改动，可涉及：

- [ ] `backend/src/agents/lead_agent/prompt.py`
  - 仅在 engine_mode 参数整理时改动

## 8. Files That Must Not Be Modified In Phase 1

- [ ] `backend/src/agents/planner/node.py`
- [ ] `backend/src/agents/router/semantic_router.py`
- [ ] `backend/src/agents/thread_state.py`
- [ ] workflow SSE / custom event 契约
- [ ] `frontend/` 全部文件

## 9. Required Backend Acceptance Cases

### 9.1 Registry Layer

- [x] canonical 值可直接解析到 builder
- [x] alias 值可解析到正确 builder
- [x] 未知值回退到 default builder
- [x] 可列出全部支持的 engine 类型

### 9.2 Runtime Build Layer

- [x] `default` engine 走默认装配
- [x] `react` engine 正确传递 prompt mode
- [x] `read_only_explorer` engine 正确过滤非只读 MCP tools
- [x] `sop` engine 正确传递 SOP prompt mode

### 9.3 CRUD Layer

- [x] create agent 支持写入 `engine_type`
- [x] get agent 可返回 `engine_type`
- [x] update agent 可更新 `engine_type`
- [x] alias 输入能被归一化

### 9.4 Workflow Integration

- [x] workflow domain agent 按配置选择 engine builder
- [x] 不改变 executor 的任务分发语义
- [x] 不影响现有 clarification / intervention 主链路

### 9.5 Build-Time Hook Addendum

- [x] `before_agent_build` 可被稳定触发
- [x] `after_agent_build` 可被稳定触发
- [x] `before_skill_resolve` 可被稳定触发
- [x] `before_mcp_bind` 可被稳定触发
- [x] 默认 no-op hook 不改变现有 engine registry 行为
- [x] hook 接入后，不改变 workflow domain agent 的既有调度语义

## 10. Recommended Implementation Order

1. 先抽 `engines/base.py` 和各 engine builder
2. 再升级 `engine_registry.py`
3. 再接入 `make_lead_agent()`
4. 再打通 `agents.py` 的 CRUD 契约
5. 最后补测试和回归

原因：

- 先把抽象层定住，避免 API 和 runtime 各写一套逻辑
- 避免继续把 registry 逻辑塞回 `agent.py`

## 11. Done Definition

Phase 1 全部完成，包括核心 Engine Registry 和 Build-Time Hook Addendum：

- engine_type 有正式 registry ✅
- engine 差异有独立 builder ✅
- CRUD / config / runtime 三层口径一致 ✅
- workflow domain agent 已通过 registry 构建 ✅
- agent build / skill resolve / MCP bind 有显式 hook 落点 ✅
- 默认 no-op 不改当前行为 ✅
- hook contract 与边界清晰，不侵入 workflow 调度 ✅
- 测试覆盖：55+ engine registry 测试 + 20+ build-time hook 测试 ✅

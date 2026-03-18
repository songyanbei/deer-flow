# MCP Light Management Loading And Isolation

- Status: `draft`
- Owner suggestion: backend + test
- Related area: MCP / multi-agent / workflow domain agents

## Goal

在 DeerFlow 中落地一套“轻管理、重装配、按需加载、严格隔离”的 MCP 使用机制。

本次功能目标：

1. DeerFlow 只负责配置和使用 MCP，不负责注册管理 MCP
2. 支持 `global / domain / shared / ephemeral` 四类 MCP 语义
3. 支持主 Agent 与 Domain Agent 的不同装配规则
4. 支持 `meeting-agent`、`contacts-agent` 这类 Domain Agent 的 MCP 相互隔离
5. 支持 Domain MCP 仅在真正执行时才加载
6. 支持最新业务型 MCP 工程的 `stdio / sse / http` 连接方式，尤其是：
   - `GET /sse`
   - `POST /message`
   - `GET /health`

## Why This Needs Frontend/Backend Collaboration

本次主要是后端和测试改造，前端不是首要改动方。

当前阶段不要求新增前端 MCP 管理台，但需要保证：

- 现有 workflow/domain-agent 行为不回归
- 现有 `/api/mcp/config` 可继续被消费
- 后续若前端需要展示配置字段，返回结构应具备扩展性

## Current Behavior

### Backend

当前 DeerFlow 存在两套 MCP 使用逻辑：

1. 全局 MCP 工具池
- 通过 [backend/src/mcp/cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py) 与 [backend/src/mcp/tools.py](/E:/work/deer-flow/backend/src/mcp/tools.py) 提供
- 由 [backend/src/tools/tools.py](/E:/work/deer-flow/backend/src/tools/tools.py) 在主 Agent 工具装配中直接注入

2. Domain Agent 专属 MCP
- 通过 [backend/src/execution/mcp_pool.py](/E:/work/deer-flow/backend/src/execution/mcp_pool.py) 提供
- 当前 `mcp_pool` 主要按 agent 维度缓存连接，且实现上偏向 `stdio`

当前问题：

- 配置模型不统一
- 运行时实现分裂
- 无明确 `global / domain / shared` 分类语义
- `meeting-agent`、`contacts-agent` 的隔离原则没有统一落到配置和运行时
- 与最新 MCP 服务形态的对齐不完整

### Frontend

当前前端不是此次功能的直接开发主体。

但必须保证：

- 现有 workflow 页面和聊天链路不因后端 MCP 改造而回归
- `/api/mcp/config` 不发生破坏性变化

## Contract To Confirm First

- Event/API:
  - 保持 `GET /api/mcp/config` 和 `PUT /api/mcp/config` 继续可用
  - 允许在配置响应中新增字段，但不能破坏原有字段
- Payload shape:
  - `mcpServers` 继续是顶层 map
  - 每个 server 增加运行时提示字段，如 `category/domain/readonly/healthcheck_path`
- Persistence:
  - 继续持久化到 `extensions_config.json`
  - agent 级装配配置继续落在各自 `config.yaml`
- Error behavior:
  - MCP 连接失败必须返回明确错误，不允许静默失败
  - Domain Agent MCP 不可用时，仍允许 workflow 走既有 `request_help` 或澄清路径
- Dedup/replacement:
  - 同一作用域下重复装配同名 server 时去重
  - 不同作用域缓存不能串工具集

## Backend Changes

### 一、配置模型升级

需要改造：

- [backend/src/config/extensions_config.py](/E:/work/deer-flow/backend/src/config/extensions_config.py)
- [backend/src/config/agents_config.py](/E:/work/deer-flow/backend/src/config/agents_config.py)

要求：

1. 平台 MCP 配置支持：
   - `type=stdio/sse/http`
   - `command/args/env`
   - `url/headers/oauth`
   - `healthcheck_path`
   - `connect_timeout_seconds`
   - `call_timeout_seconds`
   - `retry_count`
   - `circuit_breaker_enabled`
   - `category`
   - `domain`
   - `readonly`

2. agent 配置新增：
   - `mcp_binding`
   - `use_global`
   - `domain`
   - `shared`
   - `ephemeral`

3. 旧配置兼容：
   - 旧 `mcp_servers` 继续支持
   - 在加载阶段转换为 `mcp_binding.domain`

### 二、统一运行时

需要收敛：

- [backend/src/mcp/cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py)
- [backend/src/execution/mcp_pool.py](/E:/work/deer-flow/backend/src/execution/mcp_pool.py)

建议新增：

- `backend/src/mcp/runtime_manager.py`
- `backend/src/mcp/binding_resolver.py`
- `backend/src/mcp/health.py`
- `backend/src/mcp/tool_filter.py`

要求：

1. 支持 `global` 作用域
2. 支持 `domain:<agent_name>` 作用域
3. 支持 `run:<run_id>` 预留作用域
4. 支持 `stdio / sse / http`
5. 对 `sse/http` 支持基于 `/health` 的探活

### 三、主 Agent 与 Domain Agent 装配规则落地

要求：

1. 主 Agent 默认只装配 `global`
2. Domain Agent 默认不继承 `global`
3. Domain Agent 仅装配：
   - `mcp_binding.domain`
   - `mcp_binding.shared`
4. `meeting-agent` 与 `contacts-agent` 的 MCP 必须相互隔离
5. Domain MCP 必须按需加载，不允许系统启动时全量加载所有 domain MCP

### 四、只读过滤

要求：

对 `ReadOnly_Explorer` 增加最轻量的 MCP 写操作过滤。

最小过滤关键字：

- `write`
- `create`
- `update`
- `delete`
- `cancel`
- `insert`
- `modify`
- `submit`

## Frontend Changes

本次不要求主动开发前端功能。

但如果后续需要补字段展示，前端可读取新增字段，不应阻塞本次后端交付。

## Risks

1. 旧配置兼容不完整，导致现有 agent 无法启动
2. 全局 MCP 与 Domain MCP 运行时并行存在时可能出现重复逻辑或串缓存
3. `meeting-agent`、`contacts-agent` 的工具隔离做得不彻底，导致跨域误用
4. `sse` 健康检查与建连失败处理不清晰，导致排障困难
5. 只读过滤规则过于粗糙，可能误杀或漏掉工具

## Acceptance Criteria

1. DeerFlow 可读取并运行最新业务型 MCP 配置
2. 支持至少以下三种 transport：
   - `stdio`
   - `sse`
   - `http`
3. 主 Agent 默认只获得 `global` MCP
4. `contacts-agent` 仅获得自己的 `domain` MCP
5. `meeting-agent` 仅获得自己的 `domain` MCP 和显式声明的 `shared`
6. `meeting-agent` 与 `contacts-agent` 的 MCP 工具集互不串台
7. Domain MCP 在未执行前不加载
8. 旧配置仍可运行
9. `/api/mcp/config` 不发生破坏性回归

## Open Questions

- `shared` MCP 是否允许主 Agent 显式装配，还是仅限 Domain Agent 使用
- `ephemeral` 是否只做字段预留，还是需要最小运行时支持

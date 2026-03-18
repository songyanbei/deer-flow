# MCP Light Management Loading And Isolation Backend Checklist

- Related feature: [mcp-light-management-loading-and-isolation.md](/E:/work/deer-flow/collaboration/features/mcp-light-management-loading-and-isolation.md)
- Status: `draft`

## Scope

本清单只面向后端开发同学，覆盖：

- 配置模型
- 运行时实现
- 装配逻辑
- API 兼容
- 文档同步

---

## A. 配置模型

### A1. 平台 MCP 配置

- [x] 扩展 [backend/src/config/extensions_config.py](/E:/work/deer-flow/backend/src/config/extensions_config.py) 中的 `McpServerConfig`
- [x] 新增字段：
  - [x] `healthcheck_path`
  - [x] `connect_timeout_seconds`
  - [x] `call_timeout_seconds`
  - [x] `retry_count`
  - [x] `circuit_breaker_enabled`
  - [x] `category`
  - [x] `domain`
  - [x] `readonly`
- [x] 保证 `type=stdio/sse/http` 都能被解析
- [x] 保证 `command + args + env` 配置可用
- [x] 保证 `url + headers + oauth` 配置可用
- [x] 保证 `$ENV_VAR` 解析不回归

### A2. Agent MCP 装配配置

- [x] 在 [backend/src/config/agents_config.py](/E:/work/deer-flow/backend/src/config/agents_config.py) 新增 `McpBindingConfig`
- [x] 在 `AgentConfig` 中新增 `mcp_binding`
- [x] `mcp_binding` 至少支持：
  - [x] `use_global`
  - [x] `domain`
  - [x] `shared`
  - [x] `ephemeral`
- [x] 对旧 `mcp_servers` 做兼容迁移
- [x] 旧 `mcp_servers[].name` 自动映射到 `mcp_binding.domain`
- [x] 若旧 `mcp_servers` 里仍有 `command/args/env`，给出清晰 warning

---

## B. 运行时统一

### B1. 新增统一运行时

- [x] 新增 `backend/src/mcp/runtime_manager.py`
- [x] 新增 `backend/src/mcp/binding_resolver.py`
- [x] 新增 `backend/src/mcp/health.py`
- [x] 新增 `backend/src/mcp/tool_filter.py`

### B2. 统一原有两套运行时

- [x] 收敛 [backend/src/mcp/cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py)
- [x] 收敛 [backend/src/execution/mcp_pool.py](/E:/work/deer-flow/backend/src/execution/mcp_pool.py)
- [x] 避免继续维护两套独立 MCP 装配逻辑
- [x] Domain MCP 不再只支持 `stdio`

### B3. 作用域缓存

- [x] 支持 `scope_key="global"`
- [x] 支持 `scope_key="domain:<agent_name>"`
- [x] 预留 `scope_key="run:<run_id>"`
- [x] 同一 scope 下重复请求复用缓存
- [x] 不同 scope 不串工具集

---

## C. transport 与健康检查

### C1. stdio

- [x] 支持 `node src/index.js` 形式启动
- [x] 支持 `command + args + env`
- [x] `stdio` 错误有明确日志

### C2. SSE

- [x] 支持 `GET /sse`
- [x] 支持 `POST /message`
- [x] 支持 `GET /health`
- [ ] 对齐最新 MCP 工程格式：
  - [ ] [contacts/src/index.js](/E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/contacts/src/index.js)
  - [ ] [meeting-assistant/src/index.js](/E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/meeting-assistant/src/index.js)
  - [ ] [hcm/src/index.js](/E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/hcm/src/index.js)
  - [ ] [time-server/src/index.js](/E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/time-server/src/index.js)

### C3. 健康检查

- [x] `sse/http` 默认优先访问 `/health`
- [x] 健康检查失败时记录清晰错误
- [x] 健康检查不应阻塞整个系统启动

---

## D. 装配规则

### D1. 主 Agent

- [x] [backend/src/tools/tools.py](/E:/work/deer-flow/backend/src/tools/tools.py) 中主 Agent 默认只装配 `global`
- [x] 主 Agent 不默认装配所有 `domain`
- [x] 主 Agent 不默认装配所有 `shared`

### D2. Domain Agent

- [x] [backend/src/agents/executor/executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py) 中接入 `McpBindingResolver`
- [x] Domain Agent 默认不继承全局 MCP
- [x] Domain Agent 仅装配：
  - [x] `mcp_binding.domain`
  - [x] `mcp_binding.shared`
- [x] `meeting-agent` 与 `contacts-agent` 相互隔离
- [x] Domain MCP 在执行前才预热

### D3. Helper Task

- [ ] helper task 默认只拿最小必要 MCP 集
- [ ] helper 不继承父任务全部 MCP

---

## E. 只读过滤

- [x] 对 `ReadOnly_Explorer` 接入 `McpToolFilter`
- [x] 至少过滤以下关键字：
  - [x] `write`
  - [x] `create`
  - [x] `update`
  - [x] `delete`
  - [x] `cancel`
  - [x] `insert`
  - [x] `modify`
  - [x] `submit`
- [x] 非 `ReadOnly_Explorer` 不受该过滤影响

---

## F. API 与兼容性

### F1. Gateway API

- [x] 更新 [backend/src/gateway/routers/mcp.py](/E:/work/deer-flow/backend/src/gateway/routers/mcp.py)
- [x] `/api/mcp/config` 支持新字段读写
- [x] 保持原有字段兼容
- [x] 返回结构不做破坏性修改

### F2. 兼容旧行为

- [x] 旧 `extensions_config.json` 可继续运行
- [x] 旧 agent 配置可继续运行
- [x] workflow 主链路不回归
- [x] `request_help` 主链路不回归

---

## G. 文档同步

- [ ] 更新 [backend/docs/MCP_SERVER.md](/E:/work/deer-flow/backend/docs/MCP_SERVER.md)
- [ ] 更新 [backend/README.md](/E:/work/deer-flow/backend/README.md)
- [ ] 更新 [README.md](/E:/work/deer-flow/README.md)
- [ ] 文档补充：
  - [ ] `global/domain/shared/ephemeral` 分类说明
  - [ ] `stdio/sse/http` 示例
  - [ ] agent 仅引用 server name 的推荐写法

---

## H. 完成判定

- [ ] 可使用最新业务型 MCP 工程完成至少一个 `global` MCP 接入
- [ ] 可完成至少一个 `domain` MCP 接入
- [ ] `meeting-agent`、`contacts-agent` 工具隔离正确
- [ ] Domain MCP 在未执行前不加载
- [ ] 旧配置与现有 workflow 行为不回归

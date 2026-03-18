# MCP Light Management Loading And Isolation Test Checklist

- Related feature: [mcp-light-management-loading-and-isolation.md](/E:/work/deer-flow/collaboration/features/mcp-light-management-loading-and-isolation.md)
- Status: `draft`

## Scope

本清单只面向测试同学，覆盖：
- 配置兼容验证
- MCP 分类与装配验证
- Domain Agent 隔离验证
- 按需加载验证
- `stdio / sse / http` 连接验证
- 健康检查、超时、重试、失败降级验证
- 回归验证

---

## A. 测试准备

### A1. 测试环境

- [ ] 准备 DeerFlow 后端分支环境
- [ ] 准备最新 MCP mock 工程：[E:/work/laifu-agent-MCP-Server-Mock](/E:/work/laifu-agent-MCP-Server-Mock)
- [ ] 至少可启动以下 MCP 服务：
  - [ ] `contacts`
  - [ ] `meeting-assistant`
  - [ ] `hcm`
  - [ ] `time-server`
- [ ] 同时准备 `stdio` 与 `sse` 两类启动方式

### A2. 基础验证素材

- [ ] 准备一份只包含全局 MCP 的配置
- [ ] 准备一份包含 `global + domain + shared` 的配置
- [ ] 准备一份仍使用旧 `mcp_servers` 的 agent 配置
- [ ] 准备一份故意配置错误的 MCP 样例：
  - [ ] 不存在的 `command`
  - [ ] 不可访问的 `url`
  - [ ] `/health` 返回失败
  - [ ] 超时配置过小

---

## B. 配置兼容测试

### B1. 平台 MCP 配置

- [ ] 验证 [backend/src/config/extensions_config.py](/E:/work/deer-flow/backend/src/config/extensions_config.py) 新增字段可被正确读取
- [ ] 验证以下字段在配置中生效：
  - [ ] `type`
  - [ ] `command`
  - [ ] `args`
  - [ ] `env`
  - [ ] `url`
  - [ ] `headers`
  - [ ] `healthcheck_path`
  - [ ] `connect_timeout_seconds`
  - [ ] `call_timeout_seconds`
  - [ ] `retry_count`
  - [ ] `circuit_breaker_enabled`
  - [ ] `category`
  - [ ] `domain`
  - [ ] `readonly`

### B2. Agent 装配配置

- [ ] 验证 [backend/src/config/agents_config.py](/E:/work/deer-flow/backend/src/config/agents_config.py) 的 `mcp_binding` 可以正确解析
- [ ] 验证 `mcp_binding.use_global` 行为正确
- [ ] 验证 `mcp_binding.domain` 行为正确
- [ ] 验证 `mcp_binding.shared` 行为正确
- [ ] 验证 `mcp_binding.ephemeral` 预留字段不会导致异常

### B3. 旧配置兼容

- [ ] 当 agent 仍使用旧 `mcp_servers` 时，系统可以正常启动
- [ ] 旧 `mcp_servers[].name` 能正确映射到新的 domain 装配逻辑
- [ ] 旧配置中包含连接细节时，系统给出 warning 但不直接崩溃
- [ ] `GET /api/mcp/config` 与 `PUT /api/mcp/config` 不发生破坏性变化

---

## C. MCP 分类与装配测试

### C1. 全局 MCP

- [ ] 主 Agent 默认只拿到 `global` MCP
- [ ] 主 Agent 不应自动拿到任意 Domain MCP
- [ ] 主 Agent 不应自动拿到任意 `shared` MCP，除非明确声明

### C2. Domain MCP

- [ ] `meeting-agent` 只拿到自己声明的 `domain` MCP
- [ ] `contacts-agent` 只拿到自己声明的 `domain` MCP
- [ ] `meeting-agent` 看不到 `contacts-agent` 的专属 MCP
- [ ] `contacts-agent` 看不到 `meeting-agent` 的专属 MCP

### C3. Shared MCP

- [ ] 同一个 `shared` MCP 可被多个 Domain Agent 显式装配
- [ ] 未声明 `shared` 的 agent 不应看到对应 MCP
- [ ] `shared` 不应被误当作 `global`

### C4. Ephemeral MCP

- [ ] 当前阶段如仅做预留，配置存在时系统不崩溃
- [ ] 未实现的能力要有明确提示，不允许 silent ignore

---

## D. 按需加载测试

### D1. 启动阶段

- [ ] 系统启动时，不主动建立所有 Domain MCP 连接
- [ ] 系统启动时，只允许预热全局必需 MCP
- [ ] 启动日志中可以看出哪些 MCP 被初始化，哪些未加载

### D2. 执行阶段

- [ ] 当 workflow 未命中 `meeting-agent` 时，不应加载 `meeting-assistant`
- [ ] 当 workflow 首次命中 `meeting-agent` 时，才加载 `meeting-assistant`
- [ ] 当 workflow 未命中 `contacts-agent` 时，不应加载 `contacts`
- [ ] 当 workflow 首次命中 `contacts-agent` 时，才加载 `contacts`

### D3. 复用与释放

- [ ] 同一作用域下重复执行时可复用连接或工具缓存
- [ ] 不同作用域之间不串工具集
- [ ] 连接失败后重试逻辑符合配置
- [ ] 空闲后的释放或过期行为符合实现设计

---

## E. Transport 测试

### E1. stdio

- [ ] 验证 `node src/index.js` 方式可正常启动 MCP
- [ ] 验证 `command + args + env` 配置生效
- [ ] `stdio` 启动失败时有清晰报错
- [ ] `stdio` 调用失败时不会导致整个 workflow 无提示失败

### E2. SSE

- [ ] 验证 `GET /sse` 可连接
- [ ] 验证 `POST /message` 可调用
- [ ] 验证 `GET /health` 可探活
- [ ] 对齐以下最新 MCP 服务实现做联调验证：
  - [ ] [contacts/src/index.js](/E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/contacts/src/index.js)
  - [ ] [meeting-assistant/src/index.js](/E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/meeting-assistant/src/index.js)
  - [ ] [hcm/src/index.js](/E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/hcm/src/index.js)
  - [ ] [time-server/src/index.js](/E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/time-server/src/index.js)

### E3. HTTP

- [ ] 如本阶段支持 `http`，验证基础请求链路可用
- [ ] 如本阶段仅保留框架兼容，验证配置解析和错误提示正确

---

## F. 健康检查与容错测试

### F1. 健康检查

- [ ] `sse/http` 默认优先访问 `/health`
- [ ] `/health` 失败时返回明确错误信息
- [ ] 健康检查失败不会阻塞整个系统启动

### F2. 超时与重试

- [ ] 连接超时时按 `connect_timeout_seconds` 生效
- [ ] 调用超时时按 `call_timeout_seconds` 生效
- [ ] 重试次数按 `retry_count` 生效
- [ ] 超时和重试过程有清晰日志

### F3. 熔断与降级

- [ ] 当 MCP 连续失败时，若启用熔断应进入熔断状态
- [ ] 熔断期间重复请求不应无限重试
- [ ] Domain MCP 不可用时，workflow 应有明确失败或降级路径
- [ ] 不允许出现静默失效

---

## G. 只读过滤测试

### G1. ReadOnly Agent

- [ ] `ReadOnly_Explorer` 只能拿到只读 MCP 工具
- [ ] 写类工具名被正确过滤
- [ ] 建议至少覆盖以下关键字：
  - [ ] `write`
  - [ ] `create`
  - [ ] `update`
  - [ ] `delete`
  - [ ] `cancel`
  - [ ] `insert`

### G2. 误杀与漏放

- [ ] 不应误过滤明显只读工具
- [ ] 不应放过高风险写工具
- [ ] 对真实业务 MCP 至少验证一轮：
  - [ ] `contacts`
  - [ ] `meeting-assistant`
  - [ ] `hcm`

---

## H. 回归测试

### H1. 现有功能

- [ ] 现有主 Agent 工具装配不回归
- [ ] 现有 workflow router 行为不回归
- [ ] 现有 domain-agent 执行链路不回归
- [ ] 现有 `/api/mcp/config` 页面或调用方不回归

### H2. 错误处理

- [ ] MCP 服务不可用时错误提示清晰
- [ ] 单个 MCP 异常不会污染其他 agent 的工具集
- [ ] 单个 Domain MCP 失败不会让无关 Domain Agent 同步失败

---

## I. 验收结论

以下条件全部满足后，测试侧可判定通过：

- [ ] 配置兼容通过
- [ ] `global / domain / shared / ephemeral` 语义验证通过
- [ ] `meeting-agent` 与 `contacts-agent` 隔离验证通过
- [ ] Domain MCP 按需加载验证通过
- [ ] `stdio / sse / http` 的当前支持范围验证通过
- [ ] 健康检查、超时、重试、熔断行为验证通过
- [ ] 只读过滤验证通过
- [ ] 关键回归项验证通过

## Deliverables

- [ ] 输出测试用例清单
- [ ] 输出测试执行记录
- [ ] 输出缺陷列表
- [ ] 输出最终验收结论

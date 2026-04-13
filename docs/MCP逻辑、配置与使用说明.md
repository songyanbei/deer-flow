# DeerFlow MCP 逻辑、配置与使用说明

> **最后更新**: 2026-04-10 | 完整索引见 [MCP_INDEX.md](MCP_INDEX.md)

## 1. 文档目的

本文档基于 DeerFlow 当前已经完成的 MCP 改造代码，说明以下内容：

1. DeerFlow 当前如何接入和使用 MCP
2. MCP 在主 Agent 与 Domain Agent 中分别如何生效
3. 平台级 `extensions_config.json` 和 Agent 级 `config.yaml` 应该如何配置
4. 多租户模式下 MCP 的 scope 隔离
5. 常见使用方式、加载时机和排障方式

本文档描述的是”当前代码真实行为”，不是纯设计目标。

---

## 2. 当前实现结论

当前 DeerFlow 已经落地的是一套“轻管理、重装配”的 MCP 接入机制，边界如下：

- DeerFlow 只负责配置和使用 MCP，不负责 MCP 的注册管理
- MCP 服务地址、命令、鉴权等连接信息统一配置在 `extensions_config.json`
- Agent 不再重复配置完整连接细节，而是优先通过 `mcp_binding` 引用已注册的 MCP 名称
- MCP 已支持按用途分为四类：
  - `global`
  - `domain`
  - `shared`
  - `ephemeral`
- 主 Agent 默认只使用 `global` MCP
- `meeting-agent`、`contacts-agent`、`hr-agent` 这类 Domain Agent 使用各自专属的 `domain/shared` MCP
- Domain Agent 的 MCP 工具集相互隔离

当前实现里同时保留了新旧两套兼容能力：

- 新方式：`mcp_binding + runtime_manager + binding_resolver`
- 兼容方式：`mcp_servers + mcp_pool`

因此，当前代码已经可以优先按新方式使用，同时兼容旧配置。

---

## 3. 当前代码中的 MCP 总体架构

### 3.1 平台配置层

平台级 MCP 配置定义在：

- [extensions_config.py](/E:/work/deer-flow/backend/src/config/extensions_config.py)

这里定义了每个 MCP server 的统一配置模型 `McpServerConfig`，包括：

- 基础连接信息：`type`、`command`、`args`、`env`、`url`、`headers`
- OAuth 信息：`oauth`
- 运行时控制：`healthcheck_path`、`connect_timeout_seconds`、`call_timeout_seconds`、`retry_count`、`circuit_breaker_enabled`
- 分类信息：`category`、`domain`、`readonly`

DeerFlow 启动和运行时都会从 `extensions_config.json` 读取这些配置。

### 3.2 Agent 装配层

Agent 级 MCP 装配定义在：

- [agents_config.py](/E:/work/deer-flow/backend/src/config/agents_config.py)

这里定义了：

- 旧模型：`mcp_servers`
- 新模型：`mcp_binding`

推荐使用 `mcp_binding`，由 Agent 只声明自己需要哪些 MCP 名称，而不再重复写 `command/url/env`。

### 3.3 绑定解析层

MCP 绑定解析逻辑在：

- [binding_resolver.py](/E:/work/deer-flow/backend/src/mcp/binding_resolver.py)

它负责把 Agent 的 `mcp_binding` 解析成最终可连接的 MCP server 配置，规则如下：

1. `use_global=true` 时，引入所有 `category=global` 的 MCP
2. `domain` 中声明的名称，从平台配置中查找对应 server
3. `shared` 中声明的名称，从平台配置中查找对应 server
4. 若 Agent 仍在使用旧 `mcp_servers`，则自动迁移为 `domain` 语义并兜底兼容
5. `ephemeral` 当前仅预留，尚未真正创建运行时临时 MCP

### 3.4 运行时层

统一运行时在：

- [runtime_manager.py](/E:/work/deer-flow/backend/src/mcp/runtime_manager.py)

它按 scope 管理 MCP 连接和工具缓存。当前定义了六类 scope key（含多租户扩展）：

| Scope Key | 生成方法 | 说明 |
|-----------|---------|------|
| `global` | `scope_key_for_tenant()` | 平台级全局 MCP |
| `domain:<agent_name>` | `scope_key_for_agent(name)` | Domain Agent 专属 MCP |
| `tenant:<tid>:global` | `scope_key_for_tenant(tid)` | 租户级全局 MCP |
| `tenant:<tid>:domain:<agent>` | `scope_key_for_agent(name, tid)` | 租户+Agent 组合 |
| `tenant:<tid>:user:<uid>:global` | `scope_key_for_user(tid, uid)` | 用户个人 MCP |
| `tenant:<tid>:user:<uid>:domain:<agent>` | `scope_key_for_user_agent(name, tid, uid)` | 用户+Agent 组合 |
| `run:<run_id>` | `scope_key_for_run(rid)` | 运行时临时 MCP（预留） |

当 `tenant_id` 为 `"default"` 或 `user_id` 为 `"anonymous"` 时，自动降级到上一层 scope。

统一运行时会负责：

- 根据 transport 构造连接参数
- 建立 `MultiServerMCPClient`
- 获取该 scope 下的工具列表
- 记录当前 scope 的错误状态
- 支持 scope 级卸载和全局 shutdown

### 3.5 主 Agent MCP 路径

主 Agent 的工具装配入口在：

- [tools.py](/E:/work/deer-flow/backend/src/tools/tools.py)
- [cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py)
- [tools.py](/E:/work/deer-flow/backend/src/mcp/tools.py)

当前主 Agent 的行为是：

- 默认只加载 `category=global` 的 MCP
- 仍然通过全局缓存 `get_cached_mcp_tools()` 获取工具
- 旧兼容逻辑：如果 `extensions_config.json` 中没有任何一个 server 设置了非 `global` 的 `category`（即所有 server 的 category 都是默认值 `global`），则退回旧行为——加载所有启用的 MCP。一旦有任何一个 server 被显式设置了 `domain`/`shared`/`ephemeral` 分类，主 Agent 就只会加载 `category=global` 的子集
- 也就是说，只要 `extensions_config.json` 中开始使用 `category` 字段，主 Agent 就不会再拿到 `domain`/`shared` MCP

主 Agent 目前仍走”全局缓存路径”（`cache.py`），没有完全切到 `runtime_manager`。

### 3.6 Domain Agent MCP 路径

Domain Agent 的执行入口在：

- [executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py)
- [agent.py](/E:/work/deer-flow/backend/src/agents/lead_agent/agent.py)

当前行为如下：

1. 执行某个 Domain Agent 前，`executor` 会调用 `_ensure_mcp_ready(agent_name)`
2. `_ensure_mcp_ready` 会：
   - 读取该 agent 的 `config.yaml`
   - 如果 agent 有 `get_effective_mcp_binding()` 方法（即真正的 `AgentConfig` 实例），用 `resolve_binding()` 解析出最终 server 配置，然后用 `mcp_runtime.load_scope("domain:<agent_name>", resolved_servers)` 建立该 agent 的 MCP scope
   - 同时也会初始化 legacy `mcp_pool`（如果 `mcp_servers` 存在），以便旧路径调用方仍可工作
3. 创建 agent 时，优先从 `mcp_runtime.get_tools_sync(scope_key)` 获取 MCP tools
4. 如果 runtime manager 中没有拿到工具，则回退到旧 `mcp_pool.get_agent_tools_sync(agent_name)`

因此，Domain Agent 现在已经主要走新 runtime 路径，同时保留了旧路径的兜底。

### 3.7 只读过滤

只读工具过滤逻辑在：

- [tool_filter.py](/E:/work/deer-flow/backend/src/mcp/tool_filter.py)

当前规则：

- 若某个 engine 行为要求只读，例如 `ReadOnly_Explorer`
- 则会对注入到该 agent 的 MCP tools 做名称关键字过滤
- 默认过滤关键字包括：
  - `write`
  - `create`
  - `update`
  - `delete`
  - `cancel`
  - `insert`
  - `modify`
  - `submit`

`contacts-agent` 当前就是典型的只读 Domain Agent。

### 3.8 健康检查

健康检查逻辑在：

- [health.py](/E:/work/deer-flow/backend/src/mcp/health.py)

当前行为：

- `stdio` 不做 HTTP 健康检查，直接视为可连接
- `sse/http` 会优先探测 `healthcheck_path`，默认是 `/health`
- 健康检查失败只会记录 warning，不会阻断后续 MCP 连接尝试

### 3.9 配置 API

MCP 配置接口在：

- [mcp.py](/E:/work/deer-flow/backend/src/gateway/routers/mcp.py)

当前对外仍保留：

- `GET /api/mcp/config`
- `PUT /api/mcp/config`

也就是说，外部仍然可以通过原有 Gateway API 读写 MCP 配置。

---

## 4. MCP 分类说明

当前 DeerFlow 约定了四类 MCP。

### 4.1 `global`

全局通用 MCP，供主 Agent 默认使用。

适用场景：

- 通用时间服务
- 通用浏览器/自动化服务
- 通用低风险查询类能力

### 4.2 `domain`

某个 Domain Agent 专属的 MCP。

适用场景：

- `meeting-agent -> meeting-assistant`
- `contacts-agent -> contacts`
- `hr-agent -> hr-attendance`

要求：

- 默认不能被其他 Domain Agent 看见
- 不自动暴露给主 Agent

### 4.3 `shared`

多个 Domain Agent 可共享，但不应全局暴露的 MCP。

适用场景：

- `time-server`
- 多个业务域共用的身份解析、通知、审批等服务

### 4.4 `ephemeral`

运行时临时 MCP。

当前状态：

- 配置模型已预留
- 绑定解析已识别
- 真实运行时创建逻辑尚未实现

---

## 5. 当前加载时机说明

这是当前实现里最需要说明清楚的一点。

### 5.1 主 Agent

主 Agent 的 `global` MCP：

- 通过 [cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py) 懒加载
- 首次真正取工具时初始化
- 配置文件 `mtime` 变化时会触发缓存失效并重载

### 5.2 Domain Agent

设计目标原本是“仅在命中执行时按需加载”，但当前代码实际上是“两段式”：

1. 图启动时会做一次 Domain Agent MCP warmup
   - 入口在 [graph.py](/E:/work/deer-flow/backend/src/agents/graph.py)
   - `ensure_domain_agent_mcp_warmup()` 会预热所有声明了 MCP 的 Domain Agent
2. 真正执行 agent 前，`executor` 仍会再次用 `_ensure_mcp_ready()` 做兜底确认

所以，当前真实状态是：

- Domain Agent 具备按 agent scope 隔离的 MCP 装配
- 但并不是“完全纯懒加载”
- 系统启动阶段会尝试做预热
- 执行阶段会做二次确保

如果后续要进一步收敛到纯懒加载，需要单独再改 `graph.py` 中的 warmup 行为。

---

## 6. 当前真实配置方式

MCP 配置分成两层：

1. 平台级：`extensions_config.json`
2. Agent 级：`backend/.deer-flow/agents/<agent_name>/config.yaml`

### 6.1 平台级配置：`extensions_config.json`

文件位置示例：

- [extensions_config.json](/E:/work/deer-flow/extensions_config.json)

推荐把所有 MCP server 的连接信息统一维护在这个文件中。

> **注意**：当前实际的 `extensions_config.json` 文件中尚未配置 `category` 等新字段，所有 server 使用默认值 `category=global`，因此处于"旧兼容模式"——主 Agent 会加载所有启用的 MCP。一旦你开始在配置中显式设置 `category`，分类隔离规则就会自动生效。

推荐配置示例（含完整分类字段）：

```json
{
  "mcpServers": {
    "contacts": {
      "enabled": true,
      "type": "stdio",
      "command": "node",
      "args": [
        "E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/contacts/src/index.js"
      ],
      "category": "domain",
      "domain": "contacts",
      "readonly": true,
      "description": "Contacts MCP server"
    },
    "meeting-assistant": {
      "enabled": true,
      "type": "stdio",
      "command": "node",
      "args": [
        "E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/meeting-assistant/src/index.js"
      ],
      "category": "domain",
      "domain": "meeting",
      "description": "Meeting assistant MCP server"
    },
    "time-server": {
      "enabled": true,
      "type": "stdio",
      "command": "node",
      "args": [
        "E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/time-server/src/index.js"
      ],
      "category": "shared",
      "readonly": true,
      "description": "Shared time server"
    },
    "playwright": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@playwright/mcp@latest",
        "--headless",
        "--isolated"
      ],
      "category": "global",
      "description": "Global browser automation MCP"
    }
  }
}
```

字段说明：

- `type`
  - 可选：`stdio`、`sse`、`http`
- `command/args/env`
  - `stdio` 使用
- `url/headers/oauth`
  - `sse/http` 使用
- `category`
  - 指定该 MCP 属于哪一类
- `domain`
  - 当 `category=domain` 时建议填写
- `readonly`
  - 表示该服务主要用于只读场景，便于只读 agent 使用
- `healthcheck_path`
  - `sse/http` 健康检查路径，默认 `/health`

### 6.2 Agent 级配置：`config.yaml`

推荐配置在：

- [meeting-agent/config.yaml](/E:/work/deer-flow/backend/.deer-flow/agents/meeting-agent/config.yaml)
- [contacts-agent/config.yaml](/E:/work/deer-flow/backend/.deer-flow/agents/contacts-agent/config.yaml)

当前推荐使用 `mcp_binding`。

#### 示例 1：`meeting-agent`

```yaml
name: meeting-agent
domain: meeting
tool_groups: []
available_skills:
  - meeting

# Declarative MCP binding (preferred)
mcp_binding:
  use_global: false
  domain:
    - meeting-assistant
  shared:
    - time-server

# Legacy fallback (kept for backward compatibility)
mcp_servers:
  - name: meeting-assistant
    command: node
    args:
      - $MEETING_ASSISTANT_MCP_ENTRY
  - name: time-server
    command: node
    args:
      - $TIME_SERVER_MCP_ENTRY
```

含义：

- 不继承全局 MCP
- 专属使用 `meeting-assistant`
- 额外挂载共享 `time-server`
- 同时保留 `mcp_servers` 作为兜底：当 `extensions_config.json` 中找不到对应 server 时，`binding_resolver` 会从 `mcp_servers` 构造 stdio 配置

#### 示例 2：`contacts-agent`

```yaml
name: contacts-agent
engine_type: ReadOnly_Explorer
domain: contacts
tool_groups: []
available_skills:
  - contacts

# Declarative MCP binding (preferred)
mcp_binding:
  use_global: false
  domain:
    - contacts

# Legacy fallback (kept for backward compatibility)
mcp_servers:
  - name: contacts
    command: node
    args:
      - $CONTACTS_MCP_ENTRY
```

含义：

- 不继承全局 MCP
- 只挂自己的 `contacts`
- 因为是 `ReadOnly_Explorer`，注入工具时还会再做只读过滤
- 同样保留 `mcp_servers` 兜底

### 6.3 旧配置兼容方式

如果 Agent 还在使用旧式 `mcp_servers`，当前代码仍然兼容，例如：

```yaml
mcp_servers:
  - name: contacts
    command: node
    args:
      - $CONTACTS_MCP_ENTRY
```

当前兼容逻辑：

- `agents_config.py` 会自动把 `mcp_servers[].name` 迁移成 `mcp_binding.domain`
- 若 `binding_resolver` 在平台配置中找不到对应 server，也会尝试从旧 `mcp_servers` 兜底构造一个 `stdio` 类型配置

建议：

- 新增 Agent 一律只写 `mcp_binding`
- 旧 `mcp_servers` 仅用于过渡，不建议继续扩展

---

## 7. 如何使用最新 MCP Mock 工程

当前建议对齐的业务型 MCP 工程是：

- [laifu-agent-MCP-Server-Mock](/E:/work/laifu-agent-MCP-Server-Mock)

典型服务包括：

- `contacts`
- `meeting-assistant`
- `hcm`
- `time-server`

如果使用 `stdio` 方式，通常直接在 `extensions_config.json` 中配置：

```json
{
  "mcpServers": {
    "contacts": {
      "enabled": true,
      "type": "stdio",
      "command": "node",
      "args": [
        "E:/work/laifu-agent-MCP-Server-Mock/mcp-servers/contacts/src/index.js"
      ],
      "category": "domain",
      "domain": "contacts",
      "readonly": true
    }
  }
}
```

如果使用 `sse/http`，则配置方式改为：

```json
{
  "mcpServers": {
    "meeting-assistant": {
      "enabled": true,
      "type": "sse",
      "url": "http://127.0.0.1:18080",
      "healthcheck_path": "/health",
      "headers": {},
      "category": "domain",
      "domain": "meeting"
    }
  }
}
```

当前代码对 `sse/http` 的支持重点包括：

- 参数构造见 [client.py](/E:/work/deer-flow/backend/src/mcp/client.py)
- 健康检查见 [health.py](/E:/work/deer-flow/backend/src/mcp/health.py)
- 连接管理见 [runtime_manager.py](/E:/work/deer-flow/backend/src/mcp/runtime_manager.py)

---

## 8. 使用流程

### 8.1 给主 Agent 增加一个全局 MCP

步骤：

1. 在 `extensions_config.json` 新增一个 server
2. 将它的 `category` 设为 `global`
3. 重启服务或通过现有配置接口更新配置
4. 主 Agent 在下次初始化/缓存重载后即可使用

示例：

```json
{
  "mcpServers": {
    "playwright": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest"],
      "category": "global"
    }
  }
}
```

### 8.2 给某个 Domain Agent 挂专属 MCP

步骤：

1. 在 `extensions_config.json` 注册该 MCP
2. `category` 设为 `domain`
3. 在对应 agent 的 `config.yaml` 中加入 `mcp_binding.domain`
4. 确保 agent 的 `domain` 字段正确
5. 重启或重新加载服务

示例：

```yaml
mcp_binding:
  use_global: false
  domain:
    - meeting-assistant
```

### 8.3 给多个 Domain Agent 复用一个共享 MCP

步骤：

1. 在 `extensions_config.json` 中把该 MCP 设为 `shared`
2. 在需要它的 agent 中显式写入 `mcp_binding.shared`

示例：

```yaml
mcp_binding:
  use_global: false
  domain:
    - meeting-assistant
  shared:
    - time-server
```

注意：

- `shared` 不会自动给所有 agent
- 必须显式声明才会被装配

---

## 9. 当前已落地的真实 Agent 示例

### 9.1 `meeting-agent`

配置位置：

- [meeting-agent/config.yaml](/E:/work/deer-flow/backend/.deer-flow/agents/meeting-agent/config.yaml)

当前特点：

- Domain Agent
- 使用 `meeting-assistant` 作为专属 `domain` MCP
- 使用 `time-server` 作为 `shared` MCP
- 不继承全局 MCP

### 9.2 `contacts-agent`

配置位置：

- [contacts-agent/config.yaml](/E:/work/deer-flow/backend/.deer-flow/agents/contacts-agent/config.yaml)

当前特点：

- Domain Agent
- `engine_type=ReadOnly_Explorer`
- 使用 `contacts` 作为专属 `domain` MCP
- 不继承全局 MCP
- 工具注入时会额外做只读过滤

### 9.3 `hr-agent`

配置位置：

- [hr-agent/config.yaml](/E:/work/deer-flow/backend/.deer-flow/agents/hr-agent/config.yaml)

当前特点：

- Domain Agent
- 使用 `hcm` 作为专属 `domain` MCP
- 不继承全局 MCP
- 配置方式与 `meeting-agent`、`contacts-agent` 一致
- 对应的 skill 名为 `hcm`

---

## 10. 当前能力边界与注意事项

### 10.1 已完成能力

当前已经完成并可使用的能力包括：

- 平台级 MCP 配置模型统一
- Agent 级 `mcp_binding` 装配模型
- `global / domain / shared / ephemeral` 分类字段
- 主 Agent 与 Domain Agent 的装配分流
- Domain Agent scope 隔离
- 只读工具过滤
- `stdio / sse / http` 参数构造
- `sse/http` 健康检查
- 旧 `mcp_servers` 兼容迁移
- 配置 API 兼容

### 10.2 当前仍需注意的点

1. Domain Agent 还不是完全纯懒加载
- [graph.py](/E:/work/deer-flow/backend/src/agents/graph.py) 中仍会在图构建时预热 Domain MCP

2. 主 Agent 仍走旧的全局缓存路径
- [cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py) 仍在主链路中使用

3. `ephemeral` 目前仅是语义预留
- 还没有完整的 run 级临时 MCP 生命周期实现

4. `retry_count`、`circuit_breaker_enabled`、`call_timeout_seconds` 目前主要落在配置模型中
- 当前 `runtime_manager.py` 还没有完整实现高级重试/熔断治理
- `call_timeout_seconds` 也尚未在运行时传递给底层 `MultiServerMCPClient`，仅用于健康检查的 `connect_timeout_seconds` 已生效

5. 只读过滤当前主要依赖工具名关键字
- 对复杂业务工具仍建议通过命名规范配合使用

---

## 11. 常见排障方式

### 11.1 MCP 没有生效

优先检查：

1. `extensions_config.json` 中该 server 是否 `enabled=true`
2. `type` 与 `command/url` 是否匹配
3. agent 的 `mcp_binding` 是否正确引用了 server 名称
4. MCP 服务本身是否可启动
5. 若是 `sse/http`，`/health` 是否可访问

### 11.2 Domain Agent 看不到自己的 MCP

检查：

1. 对应 agent 是否真的被路由执行
2. `config.yaml` 是否配置了 `mcp_binding.domain/shared`
3. 平台配置里是否存在同名 server
4. `executor` 日志中 `_ensure_mcp_ready()` 是否报错
5. `agent.py` 中 runtime manager 是否成功注入 tools

### 11.3 主 Agent 拿到了不该拿的 MCP

检查：

1. `extensions_config.json` 是否错误地把某个 server 设成了 `global`
2. 是否仍处在“无 category 的旧兼容模式”

### 11.4 `ReadOnly_Explorer` 仍拿到了写工具

检查：

1. 工具名是否包含明确写操作关键字
2. 该 engine 是否走到了只读过滤逻辑
3. MCP 工具命名是否足够规范

---

## 12. 推荐配置规范

为了让后续配置更稳定，建议遵循以下规范：

1. 所有新 MCP 一律先配置到 `extensions_config.json`
2. 所有新 Agent 一律优先使用 `mcp_binding`
3. `domain` MCP 命名尽量与业务域一致，例如：
   - `contacts`
   - `meeting-assistant`
   - `hcm`
4. `shared` MCP 只在需要的 Agent 中显式声明
5. 只读 MCP 尽量设置 `readonly=true`
6. 写操作类 MCP 工具命名尽量包含动作语义，例如 `create_`、`update_`、`cancel_`

---

## 13. 总结

当前 DeerFlow 的 MCP 机制可以概括为：

- 平台统一配置 MCP 连接信息
- Agent 通过 `mcp_binding` 声明自己需要哪些 MCP
- 主 Agent 默认只使用 `global`
- Domain Agent 使用自己专属的 `domain/shared`
- Domain Agent 之间严格隔离
- 同时保留旧 `mcp_servers` 路径做兼容

如果后续没有额外架构调整，团队日常新增和使用 MCP 时，按本文档中的两层配置方式操作即可。

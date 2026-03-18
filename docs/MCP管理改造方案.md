# MCP 管理改造方案

## 1. 背景

当前 `deer-flow` 已具备两套 MCP 接入能力：

1. 平台级全局 MCP 工具池
- 入口位于 `backend/src/mcp/`
- 由 `backend/src/tools/tools.py` 在 `get_available_tools()` 中并入主 Agent 工具集
- 支持 `stdio` / `sse` / `http` 传输与 OAuth

2. workflow domain-agent 专属 MCP 连接池
- 入口位于 `backend/src/execution/mcp_pool.py`
- 由 `executor` 在领域 Agent 执行前按 agent 配置进行预热
- 当前仅支持 `stdio`

这说明项目已经验证了“全局能力 + 领域隔离能力”两条路径都成立，但也带来了明显的平台治理缺口：注册模型不统一、运行时不统一、作用域语义不统一、API 能力偏弱、缺少目录化能力治理。

本方案的目标不是继续增加 MCP 配置项，而是把 MCP 从“工具接入机制”提升为“多智能体平台的能力管理平面”。

---

## 2. 现状诊断

## 2.1 已有能力

### 2.1.1 平台级全局 MCP

- [backend/src/mcp/client.py](/E:/work/deer-flow/backend/src/mcp/client.py#L11) 负责把 `ExtensionsConfig` 转换为 `MultiServerMCPClient` 所需的 server params
- [backend/src/mcp/tools.py](/E:/work/deer-flow/backend/src/mcp/tools.py#L14) 负责从所有启用的 MCP server 拉取工具
- [backend/src/mcp/cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py#L56) 负责 lazy init、mtime 检测和缓存失效
- [backend/src/config/extensions_config.py](/E:/work/deer-flow/backend/src/config/extensions_config.py) 定义了平台级 MCP 配置模型，支持 `mcpServers`
- [backend/src/gateway/routers/mcp.py](/E:/work/deer-flow/backend/src/gateway/routers/mcp.py#L98) 提供整份 MCP 配置读写 API

### 2.1.2 领域 Agent 专属 MCP

- [backend/src/config/agents_config.py](/E:/work/deer-flow/backend/src/config/agents_config.py#L40) 定义 agent 级 `McpServerEntry`
- [backend/src/execution/mcp_pool.py](/E:/work/deer-flow/backend/src/execution/mcp_pool.py#L111) 管理按 agent 维度缓存的 MCP client
- [backend/src/agents/executor/executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py) 在执行领域 Agent 前做 MCP 预热
- [backend/src/tools/tools.py](/E:/work/deer-flow/backend/src/tools/tools.py#L71) 通过 `is_domain_agent` 将主 Agent 与领域 Agent 的工具暴露边界区分开

### 2.1.3 多智能体编排已对 MCP 隔离提出明确要求

- [当前多智能体实现说明.md](/E:/work/deer-flow/当前多智能体实现说明.md#L217) 已明确领域 Agent 需要更强约束和更少副作用
- [多智能体兼容改造分阶段实施方案.md](/E:/work/deer-flow/多智能体兼容改造分阶段实施方案.md#L123) 已明确 workflow domain agent 不再继承全局 cached MCP，而只使用各自声明的 `mcp_servers`
- [多智能体兼容改造分阶段实施方案.md](/E:/work/deer-flow/多智能体兼容改造分阶段实施方案.md#L340) 已将 agent registry / engine registry 作为长期方向

## 2.2 主要问题

### 2.2.1 配置模型分裂

当前存在两套配置模型：

- 平台级 `extensions_config.json -> mcpServers`
- agent 级 `config.yaml -> mcp_servers`

二者能力不对等：

- 平台级支持 `stdio/sse/http/oauth`
- agent 级目前只有 `stdio`
- 平台级通过名称作为 map key
- agent 级通过内嵌结构重复声明 command/args/env

结果是：

- 同一个 MCP server 无法被平台统一注册再被多个 agent 复用
- agent 迁移或复制时容易出现重复配置
- 无法建立稳定的 server ID、owner、标签、环境差异和权限策略

### 2.2.2 运行时分裂

当前存在两套运行时：

- `src.mcp.cache`：全局 lazy init + mtime 失效
- `src.execution.mcp_pool`：按 agent 连接池

二者在以下方面没有统一：

- 生命周期
- 会话复用策略
- 连接健康检查
- 错误恢复
- metrics
- transport 覆盖范围

### 2.2.3 管理 API 过于原始

当前 [backend/src/gateway/routers/mcp.py](/E:/work/deer-flow/backend/src/gateway/routers/mcp.py#L98) 只支持：

- 读取整份配置
- 覆盖写回整份配置

缺少：

- server 注册 / 删除 / 更新
- 连通性测试
- tools/resources/prompts 发现
- profile / binding 管理
- session 状态查询
- 审计与统计

### 2.2.4 能力对象仍停留在 tool 级

目前系统只消费 MCP tools，但主流 MCP 集成方向已经普遍包含：

- tools
- resources
- prompts

如果 DeerFlow 要演化成通用多智能体框架，MCP 管理对象必须从“工具源”升级为“能力源”。

### 2.2.5 权限治理不够稳

当前 `ReadOnly_Explorer` 更接近“按工具名关键字过滤”的轻量约束。对于生产级场景，这还不足以承担：

- 写操作隔离
- 敏感操作审批
- 参数约束
- 调用审计
- profile 级权限复用

---

## 3. 外部框架启发

结合主流框架的官方实现与文档，可以提炼出对 DeerFlow 有价值的共识：

1. LangChain / LangGraph 路线
- MCP 不只是工具接入，而是统一能力挂载入口
- `MultiServerMCPClient` 天然支持 multi-server、interceptor，以及 tools/resources/prompts 统一装配

2. AutoGen 路线
- 强调 workbench / session 作用域
- MCP server 挂载不应只是全局单例，而应支持 agent/team/run 级隔离与回收

3. CrewAI 路线
- 把 MCP server 看作 agent/crew 可绑定的外部能力源
- transport 需要统一抽象，不能让上层 orchestration 感知太多连接细节

4. OpenAI Agents SDK 路线
- MCP server 已被提升为 agent 的一等工具来源
- “注册”与“挂载”是两个不同层次：先有 provider，再决定 agent 怎么用

基于这些共识，可以推导出一个适合 DeerFlow 的方向：

> 统一注册、统一运行时、绑定决定作用域、profile 决定权限、session 决定生命周期。

---

## 4. 目标

本次改造的终极目标是建立一套可支撑通用多智能体框架的 MCP 管理体系，满足以下要求：

1. 同一套 MCP server 可以被平台、domain agent、run 级任务按需复用
2. 主 Agent 与 workflow domain-agent 使用同一套注册与运行时体系
3. 支持 tools/resources/prompts 三类能力统一管理
4. 支持 transport 无关化：`stdio` / `sse` / `http`
5. 支持 profile 化权限治理与审批接入
6. 支持目录化发现、健康检查、观测与审计
7. 支持平滑兼容现有 `extensions_config.json` 与 agent `config.yaml`

---

## 5. 目标架构

目标架构分为五层：

1. Registry Layer
- 负责 MCP server 的注册、元数据、能力目录与版本信息

2. Policy Layer
- 负责 capability profile、权限规则、审计等级、审批要求

3. Binding Layer
- 负责把 profile 绑定到 `global` / `domain` / `agent` / `run`

4. Runtime Layer
- 负责连接建立、session 复用、熔断、缓存、健康检查、回收

5. Orchestration Integration Layer
- 负责把绑定后的能力集装配到 `leader`、`workflow domain-agent`、helper task 和未来的其他 agent engine

可以抽象为：

`MCP Server` -> `Capability Profile` -> `Binding` -> `Session Policy` -> `Agent/Run`

---

## 6. 核心设计

## 6.1 统一数据模型

建议新增以下核心模型。

### 6.1.1 `McpServerDefinition`

表示平台已注册的 MCP server。

关键字段建议：

- `server_id`
- `name`
- `description`
- `transport`
- `command`
- `args`
- `env`
- `url`
- `headers`
- `oauth`
- `enabled`
- `tags`
- `owner`
- `environment`
- `healthcheck`
- `metadata`

说明：

- `server_id` 是平台稳定主键，agent 不再直接依赖临时名称
- `name` 用于展示
- 连接信息只在这里定义一次

### 6.1.2 `McpCapabilityCatalog`

表示从 server 发现出来的能力目录。

关键字段建议：

- `server_id`
- `tools[]`
- `resources[]`
- `prompts[]`
- `discovered_at`
- `schema_version`

其中单个 tool/resource/prompt 条目应包含：

- `original_name`
- `normalized_name`
- `description`
- `input_schema`
- `risk_level`
- `tags`
- `read_write_classification`

### 6.1.3 `McpCapabilityProfile`

表示一组可复用的能力暴露策略。

关键字段建议：

- `profile_id`
- `name`
- `description`
- `server_ids`
- `tool_filters`
- `resource_filters`
- `prompt_filters`
- `default_session_policy`
- `risk_level`
- `approval_policy`

例子：

- `platform-readonly-common`
- `contacts-readonly`
- `meeting-ops-write`
- `hr-sensitive-admin`

### 6.1.4 `McpBinding`

表示某个 profile 绑定到什么目标。

关键字段建议：

- `binding_id`
- `target_type`：`global | domain | agent | run`
- `target_id`
- `profile_id`
- `enabled`
- `priority`
- `session_policy_override`

### 6.1.5 `McpSessionRecord`

表示某次实际连接与会话状态。

关键字段建议：

- `session_id`
- `binding_id`
- `server_id`
- `scope_key`
- `lifecycle`
- `status`
- `started_at`
- `last_used_at`
- `expires_at`
- `error_count`
- `metrics`

---

## 6.2 统一作用域模型

建议明确四层作用域：

### 6.2.1 `global`

用于：

- 主 Agent 公共能力
- 全局辅助工具
- 平台公共只读资源

### 6.2.2 `domain`

用于：

- `contacts-agent`
- `meeting-agent`
- `hr-agent`

这将替代当前 agent `config.yaml` 里重复书写连接信息的做法。

### 6.2.3 `agent`

用于：

- 某个具体 Agent 定义的个性化能力
- 特定租户或环境下的 agent 定制绑定

### 6.2.4 `run`

用于：

- 临时任务能力扩展
- helper task 临时借用能力
- 审批通过后的短期写权限提升

---

## 6.3 统一运行时

建议新增：

- `backend/src/mcp/runtime/manager.py`
- `backend/src/mcp/runtime/session.py`
- `backend/src/mcp/runtime/catalog.py`

核心类建议命名为 `McpRuntimeManager`。

职责：

1. 根据 binding 解析当前应挂载哪些 server
2. 为当前 scope 建立或复用 session
3. 拉取并缓存 tools/resources/prompts catalog
4. 生成上层可消费的能力对象
5. 记录 health / metrics / last_error
6. 支持 session TTL、熔断与回收

### 6.3.1 统一替换现有两套运行时

建议演进路径：

- 用 `McpRuntimeManager` 替代 `src.mcp.cache`
- 用 `McpRuntimeManager(scope=agent/domain)` 替代 `src.execution.mcp_pool`

兼容阶段可保留旧 API，但内部都转发到新 manager。

### 6.3.2 会话策略

建议支持：

- `shared`
  - 平台级共享 session
- `scope_persistent`
  - domain / agent 级长生命周期 session
- `ephemeral`
  - run 级临时 session，任务完成后关闭

### 6.3.3 transport 无关化

当前 domain-agent MCP pool 只支持 `stdio`，这是短板。

目标状态：

- 所有作用域都统一支持 `stdio/sse/http`
- OAuth、headers、interceptor、healthcheck 均由 runtime 层接管

---

## 6.4 统一装配入口

建议新增一层显式能力解析器：

- `backend/src/mcp/bindings/resolver.py`

为上层暴露类似接口：

- `resolve_capabilities_for_leader(...)`
- `resolve_capabilities_for_domain_agent(...)`
- `resolve_capabilities_for_run(...)`

然后：

- [backend/src/tools/tools.py](/E:/work/deer-flow/backend/src/tools/tools.py#L22) 不再直接读取全局 cached MCP tools
- [backend/src/agents/executor/executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py) 不再直接操作 `mcp_pool`

而是统一改成：

- 先解 binding
- 再拿 session
- 再装配能力

这样 orchestration 只关心“我要什么能力”，而不关心“怎么连 MCP”。

---

## 6.5 权限治理

MCP 改造必须与 engine registry 对齐。

建议规则：

### 6.5.1 `ReadOnly_Explorer`

- 默认只允许绑定 `readonly` profile
- 工具过滤不再只靠名称关键字
- 需要有显式 `read_write_classification=read`

### 6.5.2 `ReAct`

- 可绑定通用 profile
- 允许工具调用，但高风险工具进入 approval policy

### 6.5.3 `SOP`

- profile 与 SOP 步骤模板联动
- 可限制允许调用的工具集合与顺序

### 6.5.4 高风险操作审批

后续与 `WAITING_APPROVAL` 对接时，MCP profile 可直接挂审批策略：

- `approval_policy = none`
- `approval_policy = user_confirmation`
- `approval_policy = admin_approval`

---

## 7. API 改造

## 7.1 兼容保留

保留当前接口：

- `GET /api/mcp/config`
- `PUT /api/mcp/config`

用途：

- 兼容现有前端和脚本
- 作为过渡桥接层

## 7.2 新增 API

建议新增以下 API：

### 7.2.1 Server Registry

- `GET /api/mcp/servers`
- `POST /api/mcp/servers`
- `GET /api/mcp/servers/{server_id}`
- `PUT /api/mcp/servers/{server_id}`
- `DELETE /api/mcp/servers/{server_id}`
- `POST /api/mcp/servers/{server_id}/test`
- `POST /api/mcp/servers/{server_id}/discover`

### 7.2.2 Capability Catalog

- `GET /api/mcp/catalog/tools`
- `GET /api/mcp/catalog/resources`
- `GET /api/mcp/catalog/prompts`

### 7.2.3 Profile

- `GET /api/mcp/profiles`
- `POST /api/mcp/profiles`
- `PUT /api/mcp/profiles/{profile_id}`
- `DELETE /api/mcp/profiles/{profile_id}`

### 7.2.4 Binding

- `GET /api/mcp/bindings`
- `POST /api/mcp/bindings`
- `PUT /api/mcp/bindings/{binding_id}`
- `DELETE /api/mcp/bindings/{binding_id}`

### 7.2.5 Runtime / Session / Metrics

- `GET /api/mcp/sessions`
- `POST /api/mcp/sessions/{session_id}/recycle`
- `GET /api/mcp/health`
- `GET /api/mcp/metrics`

---

## 8. 配置迁移策略

## 8.1 兼容输入

启动阶段同时接受：

1. `extensions_config.json -> mcpServers`
2. `agent config.yaml -> mcp_servers`

## 8.2 迁移规则

### 8.2.1 平台配置迁移

把 `extensions_config.json` 中的每个 server 转换为：

- 一个 `McpServerDefinition`
- 一个默认 `global` binding

### 8.2.2 Agent 配置迁移

把 agent 内嵌的 `mcp_servers` 自动转换为：

- 平台注册 server
- profile：`agent-{name}-default`
- binding：`target_type=agent`

兼容期内 agent 仍可继续写旧字段，但系统会在加载阶段归一化。

## 8.3 目标状态

长期目标：

- agent `config.yaml` 不再写连接细节
- 只引用 `mcp_profile_ids` 或 `mcp_binding_ids`

示例：

```yaml
name: contacts-agent
domain: contacts
engine_type: ReadOnly_Explorer
mcp_profiles:
  - contacts-readonly
```

---

## 9. 代码改造建议

## 9.1 第一阶段：统一模型与兼容层

建议新增：

- `backend/src/mcp/models.py`
- `backend/src/mcp/registry/store.py`
- `backend/src/mcp/bindings/models.py`

建议调整：

- [backend/src/config/extensions_config.py](/E:/work/deer-flow/backend/src/config/extensions_config.py)
- [backend/src/config/agents_config.py](/E:/work/deer-flow/backend/src/config/agents_config.py)

目标：

- 建立统一数据模型
- 先不动上层装配流程
- 先把“数据定义”统一

## 9.2 第二阶段：统一运行时

建议新增：

- `backend/src/mcp/runtime/manager.py`
- `backend/src/mcp/runtime/cache.py`
- `backend/src/mcp/runtime/discovery.py`

建议下沉兼容：

- `backend/src/mcp/cache.py`
- `backend/src/execution/mcp_pool.py`

目标：

- 让两套运行时都转向同一底层 manager

## 9.3 第三阶段：统一装配

建议调整：

- [backend/src/tools/tools.py](/E:/work/deer-flow/backend/src/tools/tools.py#L22)
- [backend/src/agents/executor/executor.py](/E:/work/deer-flow/backend/src/agents/executor/executor.py)
- `backend/src/agents/lead_agent/agent.py`

目标：

- leader / workflow domain-agent 都通过 binding resolver 获取 MCP 能力

## 9.4 第四阶段：治理与前端

建议调整：

- [backend/src/gateway/routers/mcp.py](/E:/work/deer-flow/backend/src/gateway/routers/mcp.py)
- 前端新增 MCP server / profile / binding 管理页

目标：

- 建立真正可运维的 MCP 管理台

---

## 10. 与多智能体框架目标的对齐方式

这次改造完成后，MCP 在框架中的角色会发生变化：

### 改造前

- MCP = Agent 直接加载的一组工具

### 改造后

- MCP = 平台注册的能力提供者
- Agent = 通过 profile/binding 获得能力集
- Workflow = 在编排过程中动态决定挂载哪些能力
- Engine = 决定能力如何被约束和使用

这会使 DeerFlow 更接近真正的通用多智能体平台，而不是“带一些外部工具接入能力的 Agent 项目”。

---

## 11. 实施阶段建议

## 阶段 A：治理底座

目标：

- 统一 server / profile / binding 数据模型
- 保持旧配置兼容

产出：

- 新模型
- 兼容加载器
- 基础测试

## 阶段 B：运行时统一

目标：

- 用单一 runtime manager 替代两套连接逻辑

产出：

- session 复用
- transport 无关化
- health / metrics

## 阶段 C：编排接入

目标：

- leader / workflow domain-agent 全部改走 binding resolver

产出：

- 统一装配链路
- engine_type 对齐 profile

## 阶段 D：治理闭环

目标：

- 审批、审计、目录发现、前端管理台

产出：

- server test/discover
- profile/binding 管理
- metrics / 审计 / 灰度能力

---

## 12. 风险与注意事项

1. 不要一步到位删除旧配置
- 建议至少保留一轮兼容期

2. 不要把权限治理继续放在 prompt 里
- prompt 只负责引导
- 权限必须由 profile / runtime 决定

3. 不要让 workflow domain-agent 长期停留在 `stdio only`
- 否则未来接远程企业服务 MCP 时会再次分叉

4. 不要只缓存 `BaseTool[]`
- 应缓存更高层的 catalog 与 session
- 否则后续 resources/prompts 很难纳入统一治理

5. 不要把 API 继续设计成“整份 JSON 文件读写”
- 这会阻碍目录化注册、审批、审计与前端管理

---

## 13. 建议的近期落地顺序

如果按当前仓库推进，建议优先级如下：

1. 先统一数据模型
2. 再统一 runtime manager
3. 然后改造 `tools.py` 与 `executor.py` 的装配链路
4. 最后补 API 与前端管理页

这个顺序的好处是：

- 对现有 workflow 主链路影响最小
- 可以逐步替换 [backend/src/mcp/cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py#L82) 与 [backend/src/execution/mcp_pool.py](/E:/work/deer-flow/backend/src/execution/mcp_pool.py#L121)
- 可以让现有 `engine_type` 和后续审批治理自然接入

---

## 14. 结论

对 DeerFlow 来说，MCP 改造的关键不是“再加几个 server 配置项”，而是把 MCP 提升为：

- 平台统一注册的能力源
- 可按 profile 复用的权限单元
- 可按 binding 挂载到不同作用域的资源
- 可被 runtime manager 统一治理的连接与会话

只有这样，现有的 `leader`、`workflow domain-agent`、`helper task`、未来更多 `engine_type` 才能共享同一套能力管理平面，支撑“通用多智能体框架”的最终目标。

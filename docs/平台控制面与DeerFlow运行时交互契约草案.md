# 平台控制面与 DeerFlow 运行时交互契约草案

更新时间：2026-04-01

## 1. 文档目的

本文档用于回答一个关键问题：

- AI 开发者平台已经具备智能体、MCP、Skill 的创建和管理能力
- DeerFlow 是一套多智能体执行框架
- DeerFlow 当前只提供后端，不提供前端

在这种前提下，平台和 DeerFlow 不应该重复建设同一套能力，而应该明确：

1. 哪些能力属于平台控制面
2. 哪些能力属于 DeerFlow 运行时
3. 平台在运行时应向 DeerFlow 传递哪些上下文
4. DeerFlow 当前还缺哪些接入点需要补齐

本文档是“接入前的模型与契约草案”，优先用于对齐设计，不直接替代详细实施文档。

---

## 2. 总体定位

建议采用以下职责边界：

- **AI 开发者平台 = 控制面**
  - 管理智能体、MCP、Skill、分组、发布状态、展示元数据
  - 提供平台前端页面与管理入口
  - 维护“某个业务入口允许调用哪些智能体”的组合关系

- **DeerFlow = 运行时**
  - 执行多智能体工作流
  - 负责 planner / router / executor
  - 负责 thread、memory、artifact、upload、intervention、governance
  - 负责校验平台透传过来的 Keycloak access token

一句话定义：

- **平台管资产**
- **DeerFlow 管执行**

---

## 3. 当前代码现状与关键结论

### 3.1 已具备的能力

DeerFlow 当前已经具备以下基础：

1. OIDC Resource Server 能力已可用
2. `/api/agents` 已具备智能体 CRUD
3. agent 存储已支持 tenant-aware 目录
4. upload / artifact / intervention / governance 已有后端接口
5. thread 与 tenant 的归属检查链路已经接上

### 3.2 当前还缺的关键运行时契约

虽然 DeerFlow 已经有 agent 管理面，但运行时还没有一个明确的“平台声明允许哪些 agent 参与本次执行”的接口契约。

也就是说，当前最需要先定义的是：

1. 平台中的“单个智能体”如何映射到 DeerFlow agent
2. 平台中的“组合智能体/分组”如何映射到运行时 `allowed_agents`
3. 会话发起时由谁生成 `thread_id`
4. 平台 chat session 与 DeerFlow thread 的映射关系如何保存

### 3.3 当前最重要的设计结论

**下一步不应该先写平台接入代码，而应该先把“平台管理模型 + DeerFlow 运行时入参契约”定下来。**

---

## 4. 推荐的对象模型

## 4.1 平台中的单个智能体

平台中的“单个智能体”建议映射为 DeerFlow 中一个可执行 agent 配置。

### 平台侧建议字段

```json
{
  "agent_key": "data-analyst",
  "name": "数据分析助手",
  "description": "负责数据分析与图表解释",
  "status": "published",
  "model": "gpt-5.4",
  "engine_type": "default",
  "system_prompt": "...",
  "tool_groups": ["python", "web_search"],
  "mcp_bindings": ["bi-server", "docs-server"],
  "skills": ["csv-analyze", "chart-read"],
  "requested_orchestration_mode": "auto"
}
```

### DeerFlow 侧映射

平台后端同步到 DeerFlow 时，对应一个 agent 目录：

```text
.deer-flow/agents/{agent_key}/
  - config.yaml
  - SOUL.md
```

对于多租户场景，则对应：

```text
.deer-flow/tenants/{tenant_id}/agents/{agent_key}/
```

### 设计原则

- 平台是单个智能体配置的 Source of Truth
- DeerFlow 不负责“平台级管理语义”
- DeerFlow 只消费可执行配置

---

## 4.2 平台中的组合智能体 / 分组

平台中的“组合智能体”不建议在 DeerFlow 中落成另一份持久化 agent 实体。

推荐将它定义为：

- 一个**运行时约束集合**
- 本质上是“这次会话允许 DeerFlow 使用哪些子智能体”

### 平台侧建议字段

```json
{
  "group_key": "market-analysis-team",
  "name": "市场分析组合",
  "description": "用于市场研究场景",
  "allowed_agents": [
    "research-agent",
    "data-analyst",
    "report-agent"
  ],
  "entry_agent": "research-agent",
  "requested_orchestration_mode": "workflow"
}
```

### 设计原则

- 组合关系只存平台，不存 DeerFlow
- 每次运行时由平台显式下发 `allowed_agents`
- DeerFlow 只在允许的 agent 范围内进行规划、路由和执行

---

## 4.3 平台会话与 DeerFlow thread

平台当前已有自己的会话概念，DeerFlow 内部则以 `thread_id` 作为运行时上下文与文件隔离单元。

建议增加一张平台侧映射关系：

```json
{
  "portal_session_id": "sess_xxx",
  "deerflow_thread_id": "thread_xxx",
  "user_id": "keycloak-sub",
  "tenant_id": "default",
  "group_key": "market-analysis-team"
}
```

### 设计原则

- 平台 session 与 DeerFlow thread 一对一
- 平台负责保存映射
- DeerFlow 不需要理解平台 session 语义

---

## 5. 平台与 DeerFlow 的职责边界

| 能力 | 平台 | DeerFlow |
|---|---|---|
| 用户登录 | 负责 | 不负责 |
| OIDC token 获取 | 负责 | 不负责 |
| OIDC token 校验 | 可做宽校验/转发 | **负责严格校验** |
| 智能体 CRUD | **负责** | 提供执行配置落地接口 |
| MCP 管理 | **负责** | 消费绑定结果 |
| Skill 管理 | **负责** | 执行时使用 |
| 智能体分组/组合 | **负责** | 仅消费 `allowed_agents` |
| 会话 UI | **负责** | 不负责 |
| 多智能体执行 | 不负责 | **负责** |
| 线程状态 | 可维护业务映射 | **负责运行时状态** |
| intervention / governance | 负责展示与运营入口 | **负责生成与处理逻辑** |
| uploads / artifacts | 负责页面交互 | **负责运行时文件处理** |

---

## 6. 推荐的运行时请求契约

## 6.1 鉴权方式

平台调用 DeerFlow 时，统一透传：

```http
Authorization: Bearer <access_token>
```

说明：

- 继续复用已完成的 OIDC 改造
- DeerFlow 作为 Resource Server 自己验 token
- 当前阶段 `tenant_id` 可回落为 `default`

---

## 6.2 运行时最小必填字段

建议平台在发起一次 DeerFlow 执行时，至少传递以下业务上下文：

```json
{
  "thread_id": "thread_xxx",
  "user_message": "请分析本月销售数据并给出结论",
  "allowed_agents": [
    "research-agent",
    "data-analyst",
    "report-agent"
  ],
  "entry_agent": "research-agent",
  "requested_orchestration_mode": "workflow",
  "metadata": {
    "portal_session_id": "sess_xxx",
    "group_key": "market-analysis-team",
    "source": "portal"
  }
}
```

### DeerFlow 内部应补充注入的字段

以下字段可以由 DeerFlow 自身从 OIDC 中间件得到，不必由平台重复信任传入：

- `user_id`
- `username`
- `tenant_id`

也就是说，运行时真正可信的身份应来自 DeerFlow 自己验签后的 request context，而不是平台 body 里的声明字段。

---

## 6.3 为什么建议传 `allowed_agents`

原因有三个：

1. 平台才知道“某个业务入口允许哪些智能体参与”
2. DeerFlow 不应该持有平台的组合管理语义
3. 这样可以避免 DeerFlow planner/router 默认看到全部 agent

因此建议：

- **运行时按 allowlist 执行**
- **控制面按分组管理**

---

## 7. 推荐的接入方式

## 7.1 推荐架构

推荐采用“平台后端代理 DeerFlow”的方式：

```text
平台前端
  -> 平台后端
    -> DeerFlow Gateway / LangGraph
```

### 原因

1. 平台前端当前已经习惯调平台自己的后端
2. 平台后端更适合保存 `portal_session_id -> deerflow_thread_id` 映射
3. 平台后端更适合做 DeerFlow 协议适配
4. 可以避免平台前端直接暴露 DeerFlow 细节

---

## 7.2 平台后端建议承担的适配职责

平台后端建议新增 DeerFlow Adapter 层，负责：

1. 透传 `Authorization: Bearer <access_token>`
2. 管理平台 session 与 DeerFlow thread 映射
3. 把平台的 group 配置转换成 DeerFlow 的 `allowed_agents`
4. 把 DeerFlow 的 intervention / governance / artifact 数据转换为平台前端更稳定的响应格式

---

## 8. 当前阶段建议的最小闭环

为了降低复杂度，建议按以下顺序接入。

### 阶段 A：只接运行时聊天

先不做全量资产同步，只先打通：

1. 平台登录
2. 平台后端透传 `access_token`
3. 平台创建或查找 `deerflow_thread_id`
4. 平台把 `allowed_agents` 发给 DeerFlow
5. DeerFlow 返回消息流、artifact、intervention

目标是先验证：

- OIDC 链路
- thread 链路
- 多智能体执行链路

### 阶段 B：再接智能体同步

等阶段 A 稳定后，再做：

1. 平台智能体创建/编辑 -> 同步到 DeerFlow `/api/agents`
2. 平台分组关系 -> 只保留在平台
3. 运行时调用时由平台把分组展开成 `allowed_agents`

### 阶段 C：再接治理与运营

最后接：

1. governance 队列展示
2. intervention 处理
3. artifact 下载与回看

---

## 9. 当前最需要先确认的设计决策

在开始平台接 DeerFlow 编码前，建议先确认以下 5 项。

### 9.1 单个智能体是否一一映射到 DeerFlow agent

建议答案：**是**

### 9.2 组合智能体是否在 DeerFlow 落库

建议答案：**否**

### 9.3 运行时是否由平台传 `allowed_agents`

建议答案：**是**

### 9.4 平台是否保存 `portal_session_id -> deerflow_thread_id`

建议答案：**是**

### 9.5 当前阶段是否按单租户上线

建议答案：**是**

原因：

- OIDC 已跑通
- tenant claim 尚未稳定
- 先按 `tenant_id=default` 跑通价值最大

---

## 10. 对 DeerFlow 的下一步改造建议

结合当前代码现状，DeerFlow 下一步最值得做的不是继续改 OIDC，而是补上运行时分组能力。

建议新增的能力包括：

1. 在运行时入口显式支持 `allowed_agents`
2. planner / router / executor 只在 allowlist 范围内选择 agent
3. 对 `entry_agent` 做合法性校验
4. 在 thread metadata 中记录本次运行的 `group_key / allowed_agents`

这是平台接入 DeerFlow 的真正核心增量。

---

## 11. 结论

当前最合理的总体方案是：

- **平台继续做控制面**
- **DeerFlow 只做后端运行时**
- **OIDC 继续保留并作为平台到 DeerFlow 的统一身份透传方案**
- **平台分组关系不落 DeerFlow**
- **运行时由平台传 `allowed_agents`**

因此，下一步最该推进的工作是：

**先确认”智能体管理模型 + 运行时分组契约”，再开始平台适配与 DeerFlow runtime 改造。**

---

## 12. 实施完成记录

> 本节记录基于本契约草案各阶段的实际实施情况。

### 阶段 A 完成：运行时聊天打通 ✅

- 完成时间：2026-04-01
- 目标：OIDC 链路 + thread 链路 + 多智能体执行链路
- 实际交付：
  1. **OIDC 认证**：`OIDCAuthMiddleware` 完成，JWKS 缓存 + RS256 验签，所有 Gateway 端点受保护
  2. **Thread 链路**：`ThreadRegistry` 支持 `thread_id → metadata` 对象存储，含 `tenant_id` / `user_id` / `portal_session_id` / `group_key` / `allowed_agents` 等字段
  3. **Runtime Adapter**（`/api/runtime/*`）：
     - `POST /api/runtime/threads` — 创建运行时线程
     - `GET /api/runtime/threads/{thread_id}` — 查询绑定 + 状态快照
     - `POST /api/runtime/threads/{thread_id}/messages:stream` — 消息提交 + 归一化 SSE
  4. **SSE 归一化**：上游 LangGraph 事件映射为稳定外部契约（`ack` / `message_delta` / `message_completed` / `artifact_created` / `intervention_requested` / `run_completed` / `run_failed`）
  5. **安全**：tenant + owner 双重访问控制，`allowed_agents` 校验（存在性 + 去重 + entry_agent 合法性）

### 阶段 B 完成：智能体同步 ✅

- 完成时间：2026-04-02
- 目标：平台智能体创建/编辑同步到 DeerFlow
- 实际交付：
  1. **批量同步接口** `POST /api/agents/sync`：
     - `upsert` 模式：存在则更新，不存在则创建
     - `replace` 模式：额外删除不在列表中的 agent
     - 支持全部 `AgentConfig` 字段（domain / engine_type / mcp_binding / available_skills / requested_orchestration_mode 等）
  2. **allowed_agents 运行时过滤贯通**：
     - `list_domain_agents()` 新增 `allowed_agents` 参数
     - `planner_node` → `router_node` → `_get_helper_candidates` 全链路消费 `configurable[“allowed_agents”]`
     - 组内 `request_help` 受白名单约束

### 阶段 C：治理与运营 — 待实施

- 后端基础接口已具备（governance / intervention / artifact API 均存在且受 OIDC + tenant 保护）
- 待实施内容：平台展示适配层（将 DeerFlow 内部数据格式转换为平台前端消费格式）

### §9 设计决策确认状态

| 编号 | 决策 | 建议答案 | 实施状态 |
|------|------|---------|---------|
| 9.1 | 单个智能体一一映射到 DeerFlow agent | 是 | ✅ 已实施（`/api/agents` CRUD + `/api/agents/sync`） |
| 9.2 | 组合智能体不在 DeerFlow 落库 | 否 | ✅ 已实施（`allowed_agents` 运行时透传，不落库） |
| 9.3 | 运行时由平台传 `allowed_agents` | 是 | ✅ 已实施（runtime adapter 校验 + planner/router 过滤） |
| 9.4 | 平台保存 `portal_session_id → deerflow_thread_id` | 是 | ✅ DeerFlow 侧已支持（`register_binding` 存储映射） |
| 9.5 | 当前阶段按单租户上线 | 是 | ✅ 已实施（`tenant_id=default` 回落 + 多租户预埋） |

### §10 改造建议落实状态

| 编号 | 建议改造 | 实施状态 |
|------|---------|---------|
| 10.1 | 运行时入口显式支持 `allowed_agents` | ✅ `POST /api/runtime/threads/{id}/messages:stream` 接受并校验 |
| 10.2 | planner / router / executor 只在 allowlist 范围内选择 agent | ✅ planner + router 已接入，executor 无需改动 |
| 10.3 | 对 `entry_agent` 做合法性校验 | ✅ 必须在 `allowed_agents` 内，否则 422 |
| 10.4 | thread metadata 记录 `group_key / allowed_agents` | ✅ `update_binding()` 在成功提交后持久化 |

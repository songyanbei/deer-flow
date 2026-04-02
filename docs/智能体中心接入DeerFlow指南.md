# 智能体中心接入 DeerFlow 指南

> 更新时间：2026-04-02
>
> 本文档面向智能体中心开发团队，目标是让平台同事快速理解 DeerFlow 的定位、工作流程，以及如何通过 HTTP API 完成接入。

---

## 目录

1. [职责边界：谁管什么](#1-职责边界谁管什么)
2. [DeerFlow 工作流简介](#2-deerflow-工作流简介)
3. [接入架构与鉴权](#3-接入架构与鉴权)
4. [接入流程与 API 详解](#4-接入流程与-api-详解)
   - 阶段 A：智能体同步
   - 阶段 B：会话与消息
   - 阶段 C：文件与产物
   - 阶段 D：人工干预（Intervention）
5. [SSE 事件协议](#5-sse-事件协议)
6. [错误码速查](#6-错误码速查)
7. [环境与部署](#7-环境与部署)
8. [FAQ](#8-faq)

---

## 1. 职责边界：谁管什么

**一句话：平台管资产，DeerFlow 管执行。**

| 能力 | 智能体中心（控制面） | DeerFlow（运行时） |
|---|---|---|
| 用户登录、token 获取 | **负责** | 不负责 |
| token 校验（OIDC Resource Server） | 可做宽校验/转发 | **负责严格验签** |
| 智能体 CRUD、发布状态、展示元数据 | **负责**（Source of Truth） | 提供同步落地接口 |
| MCP 服务注册与管理 | **负责** | 消费绑定结果 |
| Skill 管理 | **负责** | 执行时使用 |
| 智能体分组 / 组合 | **负责**（只存平台） | 仅消费 `allowed_agents` 白名单 |
| 会话 UI | **负责** | 不提供前端 |
| 多智能体工作流执行 | 不负责 | **负责**（planner → router → executor） |
| 线程状态、沙箱、memory | 可维护业务映射 | **负责运行时状态** |
| 文件上传 / 产物下载 | 负责页面交互 | **负责文件处理与存储** |
| 干预（intervention）/ 治理（governance） | 负责展示与运营入口 | **负责生成与处理逻辑** |

### 核心原则

- **智能体 CRUD 及用户/人员关系由智能体中心存储和管理**，DeerFlow 仅接收同步后的快照用于运行时执行
- **调用时传 `allowed_agents` 白名单**，DeerFlow 的 planner/router 只在此范围内选择 agent
- **SSE 实时状态解析为可选** — `message_delta`、`artifact_created` 等事件可以根据平台 UI 需要选择性渲染
- **⚠️ 人工干预（`intervention_requested`）渲染为必选** — 如果平台不渲染干预卡片，agent 将永久挂起无法继续执行

### 不需要在 DeerFlow 做的事

- 智能体分组关系不落 DeerFlow — 平台把分组展开成 `allowed_agents` 列表传入即可
- 平台 session 与 DeerFlow thread 的映射关系由平台保存 — DeerFlow 只认 `thread_id`
- 用户管理、权限管理、配额管理、人员与智能体的关联关系 — 都是平台的事，DeerFlow 不存储任何用户信息

---

## 2. DeerFlow 工作流简介

DeerFlow 是一个基于 LangGraph 的多智能体执行框架。平台发送一条用户消息后，DeerFlow 内部的处理流程如下：

```
用户消息
  │
  ▼
┌─────────────────────────────────────────────────┐
│  Lead Agent（总控智能体）                         │
│                                                  │
│  1. 接收消息，结合 memory + 上下文理解意图          │
│  2. Planner 规划：拆分子任务，分配给哪些 agent      │
│     └─ 仅从 allowed_agents 白名单中选择            │
│  3. Router 路由：将子任务分发给对应 domain agent    │
│  4. Executor 执行：每个 agent 调用工具完成任务      │
│     └─ 工具来源：sandbox / MCP / skill / built-in  │
│  5. 汇总各 agent 结果，生成最终回复                 │
│                                                  │
│  运行期间可能产生：                                │
│  · intervention — 需要人类审批/输入才能继续         │
│  · artifact — 生成的文件（代码、报告、图表等）       │
│  · governance — 治理审计记录                       │
└─────────────────────────────────────────────────┘
  │
  ▼
SSE 流式返回（message_delta → message_completed → artifact_created → ...）
```

### 关键概念

| 概念 | 说明 |
|---|---|
| **Thread** | 一次会话的运行时容器。包含消息历史、沙箱文件、memory。对应平台的一个 chat session |
| **Domain Agent** | 专注某个领域的子智能体（如 data-analyst、report-agent）。由平台创建并同步到 DeerFlow |
| **allowed_agents** | 平台每次请求传入的白名单，DeerFlow 的 planner/router 只在此范围内选择 agent |
| **entry_agent** | 可选的入口 agent。指定后直接跳过 planner，由该 agent 处理消息 |
| **Intervention** | 执行过程中需要人工决策的暂停点（如：确认删除、选择方案、补充信息） |
| **Governance** | 所有关键决策的审计记录，支持队列查看和事后回溯 |

---

## 3. 接入架构与鉴权

### 3.1 推荐架构

```
平台前端 → 平台后端（Adapter 层）→ DeerFlow Gateway (:8001)
                                  → DeerFlow LangGraph (:2024)  ← 通过 nginx(:2026) 代理
```

平台后端承担适配职责：
1. 透传 Keycloak `access_token`
2. 管理 `portal_session_id ↔ deerflow_thread_id` 映射
3. 把平台分组展开成 `allowed_agents`
4. 将 DeerFlow 的 SSE 事件转为平台前端的展示格式

### 3.2 鉴权方式

所有请求统一带上 Keycloak 签发的 access_token：

```http
Authorization: Bearer <access_token>
```

DeerFlow 作为 OIDC Resource Server 自行验签，从 token 中提取：
- `sub` → `user_id`
- `preferred_username` → `username`
- `organization` / `tenant_id` / `org_id`（按优先级取第一个非空值） → `tenant_id`

> **当前阶段**：Keycloak 尚无稳定的 tenant claim，`tenant_id` 回落为 `"default"`。先按单租户跑通。

### 3.3 DeerFlow OIDC 环境变量

在 DeerFlow 启动时配置：

| 变量 | 说明 | 示例值 |
|---|---|---|
| `OIDC_ENABLED` | 开启认证 | `true` |
| `OIDC_ISSUER` | Keycloak realm URL | `https://keycloak.example.com/realms/moss` |
| `OIDC_JWKS_URI` | JWKS 端点（留空则自动拼接） | 留空 |
| `OIDC_AUDIENCE` | 期望的 aud 值（留空跳过校验） | `moss-market` 或留空 |
| `OIDC_VERIFY_SSL` | 自签证书设为 false | `false` |

---

## 4. 接入流程与 API 详解

> **Base URL**：`http://<deerflow-host>:2026`（nginx 统一入口）  
> 或直达 Gateway：`http://<deerflow-host>:8001`

### 阶段 A：智能体同步

平台创建/编辑智能体后，同步到 DeerFlow。

#### A1. 批量同步（推荐）

```
POST /api/agents/sync
```

**适用场景**：平台发布智能体、批量更新、全量同步。

**Request Body**：

```json
{
  "agents": [
    {
      "name": "data-analyst",
      "description": "负责数据分析与图表解释",
      "model": "gpt-4o",
      "engine_type": "ReAct",
      "domain": "data-analysis",
      "tool_groups": ["python", "web_search"],
      "soul": "你是一个数据分析专家，擅长...",
      "mcp_binding": {
        "servers": ["bi-server"],
        "readonly": false
      },
      "available_skills": ["csv-analyze"],
      "requested_orchestration_mode": "auto"
    },
    {
      "name": "report-agent",
      "description": "负责生成研究报告",
      "soul": "你是一个报告撰写专家..."
    }
  ],
  "mode": "upsert"
}
```

**字段说明**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `agents` | array | 是 | 要同步的智能体列表 |
| `mode` | string | 否 | `upsert`（默认）= 有则更新无则创建；`replace` = 额外删除不在列表中的 agent |

**每个 agent 的字段**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 唯一标识，仅允许 `[A-Za-z0-9-]`，存储为小写 |
| `description` | string | 否 | 描述信息 |
| `model` | string | 否 | 使用的模型名，如 `gpt-4o`、`claude-3-opus`。为空则使用系统默认模型 |
| `engine_type` | string | 否 | 执行引擎：`ReAct`（默认推理循环）、`ReadOnly_Explorer`（只读、过滤写工具）、`SOP`（标准操作流程） |
| `domain` | string | 否 | 领域标签，用于 planner 路由时的语义匹配 |
| `tool_groups` | string[] | 否 | 工具组名称列表 |
| `soul` | string | 否 | 系统提示词（SOUL.md 内容） |
| `system_prompt_file` | string | 否 | 提示词文件名，默认 `SOUL.md` |
| `hitl_keywords` | string[] | 否 | 触发人工介入的关键词 |
| `max_tool_calls` | int | 否 | 单轮最大工具调用次数 |
| `mcp_binding` | object | 否 | MCP 服务绑定配置 |
| `available_skills` | string[] | 否 | 可用的 skill 名称列表 |
| `requested_orchestration_mode` | string | 否 | 编排模式提示：`auto` / `leader` / `workflow` |

**Response** `200`：

```json
{
  "created": ["data-analyst"],
  "updated": ["report-agent"],
  "deleted": [],
  "errors": []
}
```

#### A2. 单个智能体 CRUD（可选，按需使用）

##### 列表 — `GET /api/agents`

无参数。返回当前租户下所有 agent。

**Response** `200`：

```json
{
  "agents": [
    {
      "name": "data-analyst",
      "description": "负责数据分析",
      "model": "gpt-4o",
      "engine_type": "react",
      "domain": "data-analysis",
      "tool_groups": ["python"],
      "system_prompt_file": "SOUL.md",
      "hitl_keywords": null,
      "max_tool_calls": null,
      "mcp_binding": null,
      "available_skills": null,
      "requested_orchestration_mode": null,
      "soul": null
    }
  ]
}
```

> 注：列表接口不返回 `soul`（系统提示词内容），需要通过详情接口获取。

##### 查看 — `GET /api/agents/{name}`

| 参数 | 位置 | 说明 |
|---|---|---|
| `name` | path | agent 名称（大小写不敏感，内部转小写） |

**Response** `200`：同上结构，但 `soul` 字段会包含系统提示词内容。

##### 名称检查 — `GET /api/agents/check`

| 参数 | 位置 | 必填 | 说明 |
|---|---|---|---|
| `name` | query | 是 | 待检查的 agent 名称 |

**Response** `200`：

```json
{
  "available": true,
  "name": "data-analyst"
}
```

> `name` 返回的是标准化后的小写名称。`available=false` 表示该名称已被占用。

##### 创建 — `POST /api/agents`

**Request Body**（与 sync 中的 agent 字段一致）：

```json
{
  "name": "data-analyst",
  "description": "负责数据分析",
  "model": "gpt-4o",
  "soul": "你是一个数据分析专家..."
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | **是** | 唯一标识，仅允许 `[A-Za-z0-9-]`，存储为小写 |
| `description` | string | 否 | 默认空字符串 |
| `soul` | string | 否 | 系统提示词内容，默认空字符串 |
| 其他字段 | — | 否 | 与[阶段 A1 agent 字段表](#a1-批量同步推荐)完全一致 |

**Response** `201`：返回完整的 agent 对象（含 `soul`）。

**错误码**：`409` agent 已存在，`422` 名称不合法。

##### 更新 — `PUT /api/agents/{name}`

| 参数 | 位置 | 说明 |
|---|---|---|
| `name` | path | agent 名称 |

**Request Body**（部分更新，只传需要改的字段）：

```json
{
  "description": "更新后的描述",
  "soul": "更新后的系统提示词..."
}
```

> 与创建不同：更新请求中 **所有字段都是可选的**，未传的字段保持原值不变。`name` 不可修改。

**Response** `200`：返回更新后的完整 agent 对象。

##### 删除 — `DELETE /api/agents/{name}`

| 参数 | 位置 | 说明 |
|---|---|---|
| `name` | path | agent 名称 |

**Response** `204`：无 body。

**错误码**：`404` agent 不存在。

---

### 阶段 B：会话与消息（核心链路）

这是最关键的接入链路：创建线程 → 发送消息 → 接收流式回复。

#### B1. 创建线程

```
POST /api/runtime/threads
```

**Request Body**：

```json
{
  "portal_session_id": "sess_abc123"
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `portal_session_id` | string | 是 | 平台侧的会话 ID，最长 128 字符 |

**Response** `201`：

```json
{
  "thread_id": "d4f5a6b7-...",
  "portal_session_id": "sess_abc123",
  "tenant_id": "default",
  "user_id": "keycloak-sub-xxx",
  "created_at": "2026-04-02T10:30:00+00:00"
}
```

> **重要**：平台需保存 `portal_session_id → thread_id` 的映射关系。

#### B2. 发送消息并流式接收

```
POST /api/runtime/threads/{thread_id}/messages:stream
```

**Request Body**：

```json
{
  "message": "请分析本月销售数据并给出结论",
  "group_key": "market-analysis-team",
  "allowed_agents": ["research-agent", "data-analyst", "report-agent"],
  "entry_agent": null,
  "requested_orchestration_mode": "auto",
  "metadata": {
    "source": "portal",
    "portal_session_id": "sess_abc123"
  }
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `message` | string | 是 | 用户消息文本，最大 100KB |
| `group_key` | string | 是 | 智能体分组标识（平台侧的 group_key），最长 128 字符 |
| `allowed_agents` | string[] | 是 | **允许参与本次执行的 agent 名称列表**。DeerFlow 的 planner/router 只会从这个列表中选择 agent。不可为空，最多 100 个 |
| `entry_agent` | string | 否 | 入口 agent。指定后跳过 planner 直接由该 agent 处理。**必须在 `allowed_agents` 中** |
| `requested_orchestration_mode` | string | 否 | `auto`（默认，由 planner 决策）/ `leader`（单 agent 模式）/ `workflow`（严格多步工作流） |
| `metadata` | object | 否 | 附加元数据，值只允许原始类型（string/number/boolean/null） |

**Response**：`200 text/event-stream`（SSE 流）

```
event: ack
data: {"thread_id": "d4f5a6b7-..."}

event: message_delta
data: {"content": "根据分析，", "run_id": "run_xxx"}

event: message_delta
data: {"content": "本月销售额同比增长15%", "run_id": "run_xxx"}

event: message_completed
data: {"content": "根据分析，本月销售额同比增长15%...", "run_id": "run_xxx"}

event: artifact_created
data: {"artifact": {"type": "file", "path": "/mnt/user-data/outputs/report.md"}, "artifact_url": "/api/threads/d4f5a6b7-.../artifacts/mnt/user-data/outputs/report.md"}

event: run_completed
data: {"thread_id": "d4f5a6b7-...", "run_id": "run_xxx"}
```

> SSE 事件详解见[第 5 节](#5-sse-事件协议)。

#### B3. 查询线程状态

```
GET /api/runtime/threads/{thread_id}
```

**Response** `200`：

```json
{
  "thread_id": "d4f5a6b7-...",
  "portal_session_id": "sess_abc123",
  "tenant_id": "default",
  "user_id": "keycloak-sub-xxx",
  "group_key": "market-analysis-team",
  "allowed_agents": ["research-agent", "data-analyst", "report-agent"],
  "entry_agent": null,
  "requested_orchestration_mode": "auto",
  "created_at": "2026-04-02T10:30:00+00:00",
  "updated_at": "2026-04-02T10:31:05+00:00",
  "state": {
    "title": "本月销售数据分析",
    "run_id": "run_xxx",
    "artifacts_count": 1,
    "pending_intervention": false
  }
}
```

---

### 阶段 C：文件与产物

#### C1. 上传文件

```
POST /api/threads/{thread_id}/uploads
Content-Type: multipart/form-data
```

- 支持多文件上传
- PDF、PPT、Excel、Word 自动转换为 markdown
- 上传的文件会注入到 agent 的对话上下文中

**Response** `200`：

```json
{
  "success": true,
  "files": [
    {
      "filename": "sales.xlsx",
      "size": "15234",
      "virtual_path": "/mnt/user-data/uploads/sales.xlsx",
      "artifact_url": "/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/sales.xlsx",
      "markdown_file": "sales.xlsx.md",
      "markdown_virtual_path": "/mnt/user-data/uploads/sales.xlsx.md"
    }
  ],
  "message": "1 file(s) uploaded successfully"
}
```

#### C2. 列出已上传文件

```
GET /api/threads/{thread_id}/uploads/list
```

#### C3. 下载产物

```
GET /api/threads/{thread_id}/artifacts/{path}
```

- `path` 是虚拟路径，如 `mnt/user-data/outputs/report.md`
- 自动检测 MIME 类型，HTML 内联显示，其他类型按 attachment 下载
- 加 `?download=true` 强制下载

---

### 阶段 D：人工干预（Intervention）

当 agent 执行过程中遇到需要人工决策的操作（如：执行有副作用的工具、需要用户确认方案、需要补充信息），DeerFlow 会暂停执行并通过 SSE 推送 `intervention_requested` 事件。

> **⚠️ 必须实现**：人工干预是平台**必须**对接的能力。如果平台不渲染干预卡片、不调用 resolve API，agent 将永久停留在等待状态，用户看不到任何提示也无法继续对话。即使平台第一期不需要渲染 `message_delta`、`artifact_created` 等可选事件，也必须实现干预的完整流程（渲染 → 用户操作 → resolve → resume）。

#### D1. 干预的完整生命周期

```
1. Agent 执行中触发干预 → DeerFlow 暂停执行
2. SSE 推送 intervention_requested 事件（含渲染所需全部信息）
3. 平台渲染干预卡片，用户做出选择
4. 平台调用 resolve API 提交用户决策
5. 平台根据 resume_action 发送恢复消息，agent 继续执行
```

#### D2. SSE 中的 `intervention_requested` 事件

```json
{
  "thread_id": "d4f5a6b7-...",
  "run_id": "run_xxx",
  "request_id": "intv_a1b2c3d4e5f6g7h8",
  "fingerprint": "fp_xxx",
  "intervention_type": "before_tool",
  "title": "工具 bash 需要确认",
  "reason": "Agent data-analyst 尝试执行工具 bash，该操作可能产生副作用，需要您确认后才能继续。",
  "source_agent": "data-analyst",
  "tool_name": "bash",
  "risk_level": "medium",
  "category": "tool_execution",
  "action_summary": "执行 bash",
  "action_schema": {
    "actions": [
      {
        "key": "approve",
        "label": "批准执行",
        "kind": "button",
        "resolution_behavior": "resume_current_task"
      },
      {
        "key": "reject",
        "label": "拒绝执行",
        "kind": "button",
        "resolution_behavior": "fail_current_task"
      },
      {
        "key": "provide_input",
        "label": "修改后执行",
        "kind": "input",
        "resolution_behavior": "resume_current_task",
        "placeholder": "请输入修改意见..."
      }
    ]
  },
  "display": {
    "title": "执行命令确认",
    "summary": "即将执行以下命令",
    "sections": [
      {
        "title": "命令详情",
        "items": [
          {"label": "命令", "value": "python analyze.py --input sales.csv"}
        ]
      }
    ],
    "risk_tip": "该命令将在沙箱中执行，可能修改工作区文件",
    "primary_action_label": "批准执行",
    "secondary_action_label": "拒绝执行"
  }
}
```

**事件关键字段说明**：

| 字段 | 说明 |
|---|---|
| `request_id` | 干预请求唯一 ID，resolve 时需要 |
| `fingerprint` | 并发防护指纹，resolve 时需要原样传回 |
| `intervention_type` | 触发类型：`before_tool`（工具执行前确认）、`clarification`（需要用户补充信息或选择方案） |
| `title` / `reason` | 人类可读的标题和原因 |
| `action_schema.actions[]` | 可选操作列表，每个 action 有 `key`、`label`、`kind`、`resolution_behavior` |
| `display` | 渲染投影，包含 sections（详情面板）、risk_tip、按钮文案等 |
| `questions` | 多问题复合干预时的问题列表（仅 `clarification` 类型使用，`before_tool` 类型不含此字段） |

**action.kind 枚举**：

| kind | 含义 | 对应 payload |
|---|---|---|
| `confirm` | 确认按钮 | `{"confirmed": true}` |
| `button` | 普通按钮（如拒绝） | `{}` |
| `input` | 文本输入框 | `{"text": "用户输入的内容"}` |
| `single_select` | 单选 | `{"selected": "option_key"}` |
| `multi_select` | 多选 | `{"selected": ["opt1", "opt2"]}` |
| `composite` | 多问题组合 | `{"answers": {"question_1": {...}, "question_2": {...}}}` |

**action.resolution_behavior 枚举**：

| behavior | 含义 |
|---|---|
| `resume_current_task` | 恢复执行（approve / 用户输入后继续） |
| `fail_current_task` | 终止当前任务（reject） |

#### D3. 提交干预决策

```
POST /api/threads/{thread_id}/interventions/{request_id}:resolve
```

**Request Body**：

```json
{
  "fingerprint": "fp_xxx",
  "action_key": "approve",
  "payload": {}
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `fingerprint` | string | 是 | 从 `intervention_requested` 事件中获取，原样传回 |
| `action_key` | string | 是 | 用户选择的操作，必须是 `action_schema.actions[].key` 中的值 |
| `payload` | object | 是 | 操作载荷，结构取决于对应 action 的 `kind`（见下方 payload 对照表） |

**Response** `200`：

```json
{
  "ok": true,
  "thread_id": "d4f5a6b7-...",
  "request_id": "intv_a1b2c3d4e5f6g7h8",
  "fingerprint": "fp_xxx",
  "accepted": true,
  "resume_action": "submit_resume",
  "resume_payload": {
    "message": "[intervention_resolved] request_id=intv_xxx action_key=approve"
  },
  "checkpoint": null
}
```

**错误码**：

| 状态码 | 含义 |
|---|---|
| 404 | thread 或 intervention 不存在 |
| 409 | fingerprint 不匹配（干预已过期或被其他人处理） |
| 422 | action_key 无效或 payload 校验失败 |

#### D4. 恢复执行

resolve 成功后，如果 `resume_action` 为 `"submit_resume"`，平台需要再发一条消息来恢复 agent 执行：

```
POST /api/runtime/threads/{thread_id}/messages:stream
```

```json
{
  "message": "[intervention_resolved] request_id=intv_xxx action_key=approve",
  "group_key": "market-analysis-team",
  "allowed_agents": ["research-agent", "data-analyst", "report-agent"]
}
```

> **注意**：`message` 字段使用 resolve 响应中 `resume_payload.message` 的值。`group_key` 和 `allowed_agents` 需与原始请求保持一致。

恢复后 SSE 流将继续推送后续的 `message_delta` / `message_completed` / `run_completed` 事件。

---

## 5. SSE 事件协议

`POST /api/runtime/threads/{thread_id}/messages:stream` 返回的 SSE 事件类型：

| 事件名 | 必须处理 | 时机 | data 关键字段 | 说明 |
|---|---|---|---|---|
| `ack` | 可选 | 流开始 | `thread_id` | 确认连接建立，可用于前端展示"思考中" |
| `message_delta` | 可选 | 逐步生成 | `content`, `run_id` | 增量文本片段，拼接后得到完整回复。不渲染也不影响流程 |
| `message_completed` | 可选 | 一段回复完成 | `content`, `run_id` | 完整回复文本。如果已处理 delta，可忽略 |
| `artifact_created` | 可选 | 产出文件 | `artifact`, `artifact_url` | agent 生成了文件。不展示也不影响流程 |
| `intervention_requested` | **必须** | 需要人工介入 | `request_id`, `fingerprint`, `title`, `reason`, `action_schema`, `display`, ... | ⚠️ 暂停等待人类决策。**平台必须渲染干预卡片并调用 resolve API，否则 agent 永久挂起**（详见[阶段 D](#阶段-d人工干预intervention)） |
| `governance_created` | 可选 | 治理记录 | `governance_id` | 审计事件（可用于运营看板，当前阶段可忽略） |
| `run_completed` | 推荐 | 正常结束 | `thread_id`, `run_id` | 本次执行成功完成。建议处理以更新 UI 状态 |
| `run_failed` | 推荐 | 异常结束 | `thread_id`, `run_id`, `error` | 执行失败。建议处理以展示错误信息 |

> **⚠️ 重要**：大部分 SSE 事件的处理是可选的，平台可以根据自身 UI 需要选择渲染哪些内容。**唯一必须处理的事件是 `intervention_requested`** — 如果平台忽略此事件，agent 将暂停在等待人类决策的状态，永远无法继续执行。

### 事件顺序示意

**正常流程**：
```
ack → message_delta (N次) → message_completed → artifact_created (0~N次) → run_completed
```

**含干预的流程**：
```
ack → message_delta (N次) → intervention_requested → (流暂停)
     ↓ 平台调用 resolve API
     ↓ 平台发送 resume 消息
ack → message_delta (N次) → message_completed → run_completed
```

**失败流程**：
```
ack → message_delta (0~N次) → run_failed
```

---

## 6. 错误码速查

| HTTP 状态码 | 含义 | 常见场景 |
|---|---|---|
| 400 | 请求参数非法 | 非法 tenant_id（含路径遍历字符）、格式错误 |
| 401 | 未认证 | 缺少 / 过期 / 无效的 Bearer token |
| 403 | 无权限 | 访问其他租户或其他用户的资源 |
| 404 | 资源不存在 | thread / agent / governance item 未找到 |
| 409 | 冲突 | agent 已存在（创建时）、governance item 已处理 |
| 422 | 业务校验失败 | allowed_agents 为空、entry_agent 不在白名单、agent 名称不合法 |
| 500 | 服务内部错误 | DeerFlow 或 LangGraph 内部异常 |
| 503 | 上游不可用 | LangGraph Server 连接失败 |

---

## 7. 环境与部署

### 7.1 服务端口

| 服务 | 端口 | 说明 |
|---|---|---|
| Nginx 统一入口 | **2026** | **平台应该对接此端口** |
| Gateway API | 8001 | REST API 服务 |
| LangGraph Server | 2024 | Agent 运行时（不直接暴露） |

### 7.2 Nginx 路由规则

| 路径模式 | 路由目标 |
|---|---|
| `/api/runtime/*` | → Gateway (8001) |
| `/api/agents*` | → Gateway (8001) |
| `/api/governance/*` | → Gateway (8001) |
| `/api/threads/*/uploads` | → Gateway (8001) |
| `/api/threads/*/artifacts` | → Gateway (8001) |
| `/api/threads/*/interventions` | → Gateway (8001) |
| `/api/models` | → Gateway (8001) |
| `/api/mcp/*` | → Gateway (8001) |
| `/api/skills*` | → Gateway (8001) |
| `/api/memory*` | → Gateway (8001) |
| `/api/langgraph/*` | → LangGraph (2024) |
| `/health` | → Gateway (8001) |

### 7.3 健康检查

```
GET /health
```

返回：`{"status": "healthy", "service": "deer-flow-gateway"}`

免认证，可用于负载均衡器探活。

---

## 8. FAQ

### Q：平台的智能体分组如何映射到 DeerFlow？

不映射。分组关系只保留在平台。每次发消息时，平台把分组展开成 `allowed_agents` 列表传给 DeerFlow 即可。

### Q：`thread_id` 由谁生成？

由 DeerFlow 生成。平台调用 `POST /api/runtime/threads` 后获得 `thread_id`，平台自行保存 `portal_session_id ↔ thread_id` 映射。

### Q：身份信息从哪来？

`user_id`、`tenant_id` 从 Bearer token 中提取，**不从请求 body 中读取**。平台只需透传 access_token，不需要在 body 中重复传递身份字段。

### Q：当前是单租户还是多租户？

当前阶段按单租户运行（`tenant_id=default`）。多租户基础设施已全部就绪，Keycloak 稳定输出 tenant claim 后即可切换，**平台代码无需修改**。

### Q：如果不需要多智能体，只想让一个 agent 处理怎么办？

设置 `entry_agent` 为目标 agent 名称，`allowed_agents` 只放一个元素。这样会跳过 planner 直接执行。

### Q：SSE 连接断开怎么办？

DeerFlow 不支持 SSE 断点续传。如果连接断开，可通过 `GET /api/runtime/threads/{thread_id}` 查询最终状态，或重新发送消息触发新的执行。

### Q：系统级配置（模型、MCP、Skill）需要关注吗？

阶段 A/B 不需要。这些是 DeerFlow 运维级配置，平台通常不直接调用。接口列表供参考：

| 接口 | 说明 |
|---|---|
| `GET /api/models` | 查看可用模型列表 |
| `GET/PUT /api/mcp/config` | 查看/更新 MCP 服务配置 |
| `GET/PUT /api/skills` | 查看/更新 Skill 配置 |
| `GET /api/memory` | 查看 agent 记忆数据 |

---

## 附录：最小接入代码示例（Python）

```python
import httpx

BASE = "http://deerflow-host:2026"
TOKEN = "<keycloak_access_token>"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}

# 1. 同步智能体（一次性或定时）
httpx.post(f"{BASE}/api/agents/sync", headers=HEADERS, json={
    "agents": [
        {"name": "data-analyst", "description": "数据分析", "soul": "你是数据分析专家..."},
        {"name": "report-agent", "description": "报告生成", "soul": "你是报告撰写专家..."},
    ],
    "mode": "upsert",
})

# 2. 创建线程
resp = httpx.post(f"{BASE}/api/runtime/threads", headers=HEADERS, json={
    "portal_session_id": "sess_001",
})
thread_id = resp.json()["thread_id"]

# 3. 发消息 + 流式接收
with httpx.stream("POST", f"{BASE}/api/runtime/threads/{thread_id}/messages:stream",
                   headers=HEADERS, json={
    "message": "请分析本月销售数据",
    "group_key": "market-team",
    "allowed_agents": ["data-analyst", "report-agent"],
}) as stream:
    for line in stream.iter_lines():
        if line.startswith("data: "):
            print(line[6:])
```

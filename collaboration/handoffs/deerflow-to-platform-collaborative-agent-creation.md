# 协作智能体创建配置说明 — 平台对接指南

- 发出方: DeerFlow 后端
- 接收方: moss-dev-portal 后端 + 前端
- 日期: 2026-04-17
- 状态: 待平台侧确认
- 前置文档: [deerflow-to-platform-agent-composition.md](deerflow-to-platform-agent-composition.md)

---

## 1. 目标

平台侧需要在 UI 上支持用户创建"协作智能体"（workflow 模式下多个 domain agent 组合协作），并将完整配置同步到 DeerFlow 运行时。

本文档基于 DeerFlow 已有的 3 个生产级 domain agent（meeting-agent / hr-agent / contacts-agent）总结配置模板，并逐项标注当前同步接口的完成状态。

---

## 2. 一个 domain agent 需要哪些配置

以 meeting-agent 为完整参考，一个 domain agent 由以下 **4 层配置** 组成：

```
协作智能体
├── 1️⃣ Agent 定义（config.yaml）    ← 通过 POST /api/agents/sync 推送
├── 2️⃣ 行为指令（SOUL.md）          ← 通过 sync 的 soul 字段推送
├── 3️⃣ MCP 工具（extensions_config）← 通过 PUT /api/mcp/config/{name} 推送
└── 4️⃣ Skill 技能（.skill 包）      ← 通过 POST /api/skills/install_from_payload 推送
```

### 2.1 Agent 定义（config.yaml）

以下是每个字段的说明、是否必填、以及 3 个现有 agent 的实际取值：

| 字段 | 类型 | 必填 | 说明 | meeting-agent | hr-agent | contacts-agent |
|------|------|:---:|------|--------------|----------|---------------|
| `name` | string | **是** | 唯一标识，`^[A-Za-z0-9-]+$` | `meeting-agent` | `hr-agent` | `contacts-agent` |
| `domain` | string | **是** | 业务域标签，planner 按此识别能力 | `meeting` | `hr` | `contacts` |
| `description` | string | **是** | planner 路由决策的核心依据（见下文 §3） | 详细 MCP 能力描述 + 边界声明 | 同左 | 同左 |
| `mcp_binding` | object | 推荐 | 声明此 agent 使用哪些 MCP server | `{domain: ["meeting-assistant"], shared: ["time-server"]}` | `{domain: ["hcm"]}` | `{domain: ["contacts"]}` |
| `available_skills` | list | 可选 | 此 agent 可用的 Skill 名称列表 | `["meeting"]` | `["hcm"]` | `["contacts"]` |
| `soul` | string | **推荐** | 行为指令（SOUL.md 内容），定义 agent 的执行策略 | 6.3 KB 详细指令 | 未设置 | 3.3 KB 详细指令 |
| `engine_type` | string | 可选 | 引擎类型：`default` / `react` / `read_only_explorer` / `sop` | 未设置（default） | 未设置 | `ReadOnly_Explorer` |
| `model` | string | 可选 | 模型覆盖，不设置则用全局默认模型 | 未设置 | 未设置 | 未设置 |
| `tool_groups` | list | 可选 | 工具组白名单，`[]` 表示只用 MCP 工具 | `[]` | `[]` | `[]` |
| `max_tool_calls` | int | 可选 | 单次任务最大工具调用次数，默认 20 | 未设置（20） | 未设置（20） | 未设置（20） |
| `hitl_keywords` | list | 可选 | 触发人工审批的关键词 | 未设置 | 未设置 | 未设置 |
| `requested_orchestration_mode` | string | 可选 | agent 级默认编排模式偏好 | 未设置 | 未设置 | 未设置 |

### 2.2 行为指令（SOUL.md）

SOUL.md 是 agent 的"灵魂"，定义其执行哲学和边界规则。基于现有案例总结的模板结构：

```markdown
You are `{agent-name}`, the domain specialist for {domain description}.

## Core Principles
1. {最小信息策略 — 只要求下一步工具调用真正必需的数据}
2. {区分必填和可选字段}
3. {区分已确认事实和推测事实}

## {Domain}-Domain Execution Rules
1. {具体的域内执行规则}
2. {工具使用优先级}
3. {降级策略}

## Escalation Rules
Use `request_help` only when:
- {需要其他 domain 的数据（如人员 openId 需找 contacts-agent）}
- {需要用户确认/授权}

Never escalate when:
- {自己的工具能解决的问题}
```

**关键点**：SOUL.md 的质量直接决定 agent 的执行效果。建议平台 UI 提供模板引导。

### 2.3 MCP 工具配置

每个 domain agent 通过 `mcp_binding` 声明它需要哪些 MCP server。这些 server 必须先注册到 DeerFlow：

```
mcp_binding:
  use_global: false      # 不继承全局 MCP（domain agent 通常关闭）
  domain:                # 此 agent 专属的 MCP server
    - meeting-assistant  # ← 必须在 extensions_config.json 中存在
  shared:                # 跨 agent 共享的 MCP server
    - time-server        # ← 必须在 extensions_config.json 中存在
```

### 2.4 Skill 技能

Skill 是注入到 agent 系统提示的知识/能力包。通过 `available_skills` 引用：

```yaml
available_skills:
  - meeting     # ← 必须在 skills 目录中存在对应的 SKILL.md
```

---

## 3. description 编写规范

`description` 是 planner 做路由决策的**唯一信息来源**。从现有案例中提炼的编写规范：

### 3.1 模板

```
{一句话概括能力范围}。
核心能力：
1. {能力 1}
2. {能力 2}
3. {能力 3}
【重要边界】：{明确声明做不到的事情，指引 planner 找其他 agent}。
```

### 3.2 现有案例分析

| agent | description 结构 | 效果 |
|-------|-----------------|------|
| meeting-agent | "负责所有会议室预定..." + 6 条能力列表 + "除此之外必须向其他智能体发起求助" | planner 精确路由会议相关任务 |
| hr-agent | "拥有 HR 域数据查询..." + 3 条能力 + "**不具备查询 openId 等能力，请找通讯录智能体**" | planner 知道 HR 需要 contacts-agent 配合 |
| contacts-agent | "拥有完整企业员工和组织架构查询能力..." + 5 条能力 + "不具备任何写入操作" | planner 知道这是只读查询 agent |

### 3.3 反面案例

```
❌ "通用助手" — 太模糊，planner 无法判断什么该路由给它
❌ "处理所有业务" — 与其他 agent 能力边界重叠，planner 会困惑
❌ 不声明边界 — planner 可能错误地把不属于它的任务分给它
```

---

## 4. 完整同步流程

平台创建一个"协作智能体"（如"HR 综合助手" = hr-agent + meeting-agent + contacts-agent）的完整同步流程：

```
步骤 1: 推 MCP 工具
  PUT /api/mcp/config/hcm                {"type": "stdio", "command": "...", "source": "moss-portal"}
  PUT /api/mcp/config/meeting-assistant   {"type": "sse",   "url": "...",    "source": "moss-portal"}
  PUT /api/mcp/config/contacts            {"type": "sse",   "url": "...",    "source": "moss-portal"}
  PUT /api/mcp/config/time-server         {"type": "stdio", "command": "...", "source": "moss-portal"}

步骤 2: 推 Skill（如需要）
  POST /api/skills/install_from_payload   file=meeting.skill, source=moss-portal
  POST /api/skills/install_from_payload   file=hcm.skill,     source=moss-portal
  POST /api/skills/install_from_payload   file=contacts.skill, source=moss-portal

步骤 3: 推 Agent 定义（含 SOUL.md）
  POST /api/agents/sync
  {
    "agents": [
      {
        "name": "hr-agent",
        "domain": "hr",
        "description": "拥有 HR 域的数据查询和操作能力...",
        "tool_groups": [],
        "mcp_binding": {"use_global": false, "domain": ["hcm"]},
        "available_skills": ["hcm"],
        "soul": "You are hr-agent, the domain specialist for..."
      },
      {
        "name": "meeting-agent",
        "domain": "meeting",
        "description": "负责处理所有会议室预定...",
        "tool_groups": [],
        "mcp_binding": {"use_global": false, "domain": ["meeting-assistant"], "shared": ["time-server"]},
        "available_skills": ["meeting"],
        "soul": "You are meeting-agent, the domain specialist for..."
      },
      {
        "name": "contacts-agent",
        "domain": "contacts",
        "description": "拥有完整的企业员工和组织架构查询能力...",
        "engine_type": "ReadOnly_Explorer",
        "tool_groups": [],
        "mcp_binding": {"use_global": false, "domain": ["contacts"]},
        "available_skills": ["contacts"],
        "soul": "You are contacts-agent, the domain specialist for..."
      }
    ],
    "mode": "upsert",
    "validate_dependencies": true
  }

步骤 4: 运行时发消息
  POST /api/runtime/threads/{id}/messages:stream
  {
    "message": "帮我请下周一的假，然后预定周二上午的会议室",
    "group_key": "hr-composite-assistant",
    "allowed_agents": ["hr-agent", "meeting-agent", "contacts-agent"],
    "requested_orchestration_mode": "workflow"
  }
```

---

## 5. 接口完成状态

### 5.1 资源同步接口

| 接口 | 用途 | 状态 | 备注 |
|------|------|:---:|------|
| `PUT /api/mcp/config/{name}` | 推送单个 MCP server | ✅ 已完成 | 带 source 所有权保护 |
| `DELETE /api/mcp/config/{name}` | 删除单个 MCP server | ✅ 已完成 | 带 source 校验 |
| `POST /api/skills/install_from_payload` | 直传 Skill 包 | ✅ 已完成 | multipart + source |
| `POST /api/skills/install_from_url` | URL 下载 Skill | ✅ 已完成 | SSRF 防护 + checksum |
| `POST /api/agents/sync` | 批量同步 agent 定义 | ⚠️ 部分完成 | 见 §5.2 缺失字段 |
| `GET /api/mcp/config` | 查询 MCP 配置 | ✅ 已完成 | 含 source/mcp_kind |
| `GET /api/skills` | 查询 Skill 列表 | ✅ 已完成 | 含 install_source |
| `GET /api/agents` | 查询 agent 列表 | ✅ 已完成 | — |

### 5.2 Agent Sync 字段覆盖度

对比 `AgentConfig`（运行时完整模型） vs `AgentSyncItem`（sync API 支持的字段）：

| AgentConfig 字段 | AgentSyncItem 支持 | 现有 agent 是否使用 | 缺失影响 |
|-----------------|:-----------------:|:-----------------:|---------|
| `name` | ✅ | ✅ 全部 | — |
| `description` | ✅ | ✅ 全部 | — |
| `domain` | ✅ | ✅ 全部 | — |
| `model` | ✅ | 未使用 | — |
| `engine_type` | ✅ | contacts-agent | — |
| `tool_groups` | ✅ | ✅ 全部 | — |
| `mcp_binding` | ✅ | ✅ 全部 | — |
| `available_skills` | ✅ | ✅ 全部 | — |
| `soul`（SOUL.md） | ✅ | meeting + contacts | — |
| `system_prompt_file` | ✅ | 未使用 | — |
| `hitl_keywords` | ✅ | 未使用 | — |
| `max_tool_calls` | ✅ | 未使用 | — |
| `requested_orchestration_mode` | ✅ | 未使用 | — |
| `persistent_memory_enabled` | ❌ **缺失** | meeting-agent ✅ | 无法开启域持久记忆 |
| `persistent_runbook_file` | ❌ **缺失** | meeting-agent ✅ | 无法指定 RUNBOOK.md |
| `intervention_policies` | ❌ **缺失** | 未使用 | 暂不阻塞 |
| `guardrail_structured_completion` | ❌ **缺失** | 默认值即可 | 暂不阻塞 |
| `guardrail_max_retries` | ❌ **缺失** | 默认值即可 | 暂不阻塞 |
| `guardrail_safe_default` | ❌ **缺失** | 默认值即可 | 暂不阻塞 |

### 5.3 阻塞项与建议

| # | 缺失 | 阻塞级别 | 说明 | 建议 |
|---|------|:-------:|------|------|
| 1 | `persistent_memory_enabled` | **P1** | meeting-agent 依赖此字段开启域记忆。不传时默认 false，agent 不会积累跨 session 的经验 | DeerFlow 侧在 `AgentSyncItem` + `_build_config_data` 中补上 |
| 2 | `persistent_runbook_file` | **P1** | meeting-agent 的 RUNBOOK.md 定义了记忆使用规则。不传时无 runbook 约束 | 同上；RUNBOOK.md 内容可通过新增 `runbook` 字段传入（类似 `soul`） |
| 3 | `intervention_policies` | P3 | 当前无 agent 使用 | 远期补 |
| 4 | guardrail 三个字段 | P3 | 默认值覆盖绝大多数场景 | 远期补 |

### 5.4 运行时接口

| 接口 | 用途 | 状态 | 备注 |
|------|------|:---:|------|
| `POST /api/runtime/threads` | 创建 thread | ✅ 已完成 | — |
| `POST /api/runtime/threads/{id}/messages:stream` | 发消息 + SSE 流 | ⚠️ 有 bug | `agent_name` 和 `requested_orchestration_mode` 未注入 configurable（见附录 A） |
| `GET /api/runtime/threads/{id}` | 查询 thread 状态 | ✅ 已完成 | — |

---

## 6. 平台 UI 建议

基于 DeerFlow 的运行时模型，平台创建"协作智能体"的 UI 流程建议：

### 6.1 创建流程

```
第一步：创建 Agent Group（"协作智能体"）
  ├── 输入：分组名称 → 生成 group_key
  └── 选择编排模式 → requested_orchestration_mode（通常选 workflow）

第二步：添加 Domain Agent（逐个或批量）
  ├── 基本信息
  │   ├── name（必填，唯一标识）
  │   ├── domain（必填，业务域标签）
  │   └── description（必填，建议提供模板引导）
  ├── 行为指令
  │   └── soul / SOUL.md（推荐，提供模板）
  ├── 工具绑定
  │   ├── MCP Server 选择（从已注册的 MCP 列表中勾选）
  │   └── Skill 选择（从已安装的 Skill 列表中勾选）
  └── 高级配置（可折叠）
      ├── engine_type（默认 default，只读场景选 ReadOnly_Explorer）
      ├── model（默认不填，用全局模型）
      └── max_tool_calls（默认 20）

第三步：发布同步
  ├── 调用 PUT /api/mcp/config/{name}     推 MCP
  ├── 调用 POST /api/skills/install_*      推 Skill
  ├── 调用 POST /api/agents/sync           推 Agent 定义
  └── validate_dependencies: true 确保引用完整
```

### 6.2 运行时调用

```
创建 Thread → POST /api/runtime/threads

发消息 → POST /api/runtime/threads/{id}/messages:stream
{
    "message": "...",
    "group_key": "{第一步生成的 group_key}",
    "allowed_agents": ["{第二步添加的所有 agent name}"],
    "requested_orchestration_mode": "{第一步选择的模式}"
}
```

---

## 7. 配置模板（可直接复制）

### 7.1 最小可用 domain agent

```json
{
    "name": "my-domain-agent",
    "domain": "my-domain",
    "description": "负责处理 xxx 相关业务。核心能力：1. xxx 2. xxx。【重要边界】：不具备 yyy 能力。",
    "tool_groups": [],
    "mcp_binding": {
        "use_global": false,
        "domain": ["my-mcp-server"]
    },
    "soul": "You are my-domain-agent, the domain specialist for xxx.\n\n## Core Principles\n..."
}
```

### 7.2 完整配置 domain agent（以 meeting-agent 为参考）

```json
{
    "name": "meeting-agent",
    "domain": "meeting",
    "description": "负责处理所有会议室预定、查询、修改、取消的请求，以及多地协同会议的全流程。\n可以查询空闲会议室、创建/修改/取消会议、查看指定用户的会议列表、查看会议详情和参会人。\n除了以上功能之外的所有功能，必须向其他智能体发起求助（如人员 openId 需找通讯录智能体）。",
    "tool_groups": [],
    "mcp_binding": {
        "use_global": false,
        "domain": ["meeting-assistant"],
        "shared": ["time-server"]
    },
    "available_skills": ["meeting"],
    "soul": "You are `meeting-agent`, the domain specialist for meeting room booking...\n\n## Core Principles\n1. Follow a minimum-required-information strategy...\n\n## Meeting-Domain Reasoning Rules\n...\n\n## Escalation Rules\nUse `request_help` only for real external dependency gaps..."
}
```

### 7.3 只读查询 agent（以 contacts-agent 为参考）

```json
{
    "name": "contacts-agent",
    "domain": "contacts",
    "engine_type": "ReadOnly_Explorer",
    "description": "拥有完整的企业员工和组织架构查询能力...\n不具备任何写入、修改、创建或删除操作的能力。",
    "tool_groups": [],
    "mcp_binding": {
        "use_global": false,
        "domain": ["contacts"]
    },
    "available_skills": ["contacts"],
    "soul": "You are `contacts-agent`, the domain specialist for employee directory...\n\n## Core Principles\n1. Treat your own MCP tools as the default execution path..."
}
```

### 7.4 协作智能体组合同步（完整请求）

```json
POST /api/agents/sync
{
    "agents": [
        { /* meeting-agent 完整配置，如 7.2 */ },
        { /* hr-agent 完整配置 */ },
        { /* contacts-agent 完整配置，如 7.3 */ }
    ],
    "mode": "upsert",
    "validate_dependencies": true
}
```

---

## 附录 A：已知需 DeerFlow 侧修复的问题

| # | 问题 | 影响 | 状态 |
|---|------|------|------|
| 1 | `runtime_service.py` 未将 `agent_name`/`allowed_agents`/`requested_orchestration_mode` 注入 configurable | 运行时 planner 读不到 `allowed_agents`，orchestration selector 读不到 `requested_orchestration_mode` | 待修复 |
| 2 | `AgentSyncItem` 缺 `persistent_memory_enabled` + `persistent_runbook_file` | 无法通过 sync API 开启域记忆 | 待补字段 |
| 3 | `_orchestration_payload` 的 context fallback 逻辑被 configurable 短路 | 即使修了 #1，仍需确认 fallback 逻辑正确 | 待修复 |

## 附录 B：同步顺序依赖图

```
MCP Server ──┐
             ├──→ Agent Sync（validate_dependencies 校验引用）──→ 运行时可用
Skill ───────┘

必须先推 MCP + Skill，再推 Agent。
如果需要乱序推送，设 validate_dependencies: false（不推荐）。
```

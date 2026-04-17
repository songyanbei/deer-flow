# DeerFlow 多智能体组合模型 — 平台对接说明

- 发出方: DeerFlow 后端
- 接收方: moss-dev-portal 后端 + 前端
- 日期: 2026-04-17
- 状态: 待平台侧确认

---

## 1. 背景

平台侧在对接 `/api/runtime/threads/{id}/messages:stream` 时，涉及两个核心问题：

1. **`entry_agent` 字段是否生效？**
2. **平台 UI 上"智能体 A 引入子智能体 B"的父子关系，在 DeerFlow 运行时如何表达？**

本文档从 DeerFlow 运行时的实际代码行为出发，给出明确结论和对接方案。

---

## 2. 结论先行

| 结论 | 说明 |
|------|------|
| **`entry_agent` 当前不生效，建议废弃** | 字段在接口契约中保留但标记 deprecated；运行时忽略此值 |
| **DeerFlow 没有"父调子"的层级模型** | 运行时是 **planner 驱动的扁平协作模型** |
| **平台的"子智能体"概念应映射为 `allowed_agents` 列表** | planner 在此列表范围内自主路由 |
| **主次/优先级关系通过 agent 的 `description` 语义引导** | 不通过硬编码 |

---

## 3. DeerFlow 的多智能体运行时模型

### 3.1 架构图

```
用户消息
  │
  ▼
orchestration_selector ── 决定 leader / workflow 模式
  │
  ├── leader 模式 ──→ 单 agent 直接处理（不涉及多 agent）
  │
  └── workflow 模式 ─→ planner（LLM 自主规划）
                        │
                        ├── Task 1 → agent-A（HR 域）
                        ├── Task 2 → agent-B（会议域）  ← depends_on Task 1
                        └── Task 3 → agent-C（文档域）
                        │
                        ▼
                      router → executor → 结果回传 planner → 验收/追加任务
```

### 3.2 关键行为

| 组件 | 行为 |
|------|------|
| **planner** | 收到用户请求 + `allowed_agents` 列表 → 读每个 agent 的 `name: description` → LLM 决定拆分成哪些子任务、分派给哪个 agent、任务间有无依赖 |
| **router** | 按 planner 的分派结果，将任务交给对应 domain agent 执行 |
| **executor** | 加载目标 agent 的完整配置（model, MCP, skills, prompt）执行单个任务 |
| **depends_on** | planner 自动判断任务间的数据依赖关系，后置任务等前置任务完成后才执行 |

### 3.3 每个 agent 独立拥有的资源

```yaml
# 通过 POST /api/agents/sync 推送的 agent 配置
name: hr-agent
domain: "human-resources"                    # ← planner 按此识别能力域
description: "处理 HR 相关业务，包括考勤、请假、入离职"  # ← planner 按此做路由决策
mcp_binding:
  domain: ["hr-system"]                      # ← 此 agent 独占的 MCP 工具
available_skills: ["leave-calculator"]       # ← 此 agent 可用的 Skill
# SOUL.md                                   # ← 此 agent 独有的行为指令
```

---

## 4. `entry_agent` 为什么不生效

### 4.1 技术原因

```
runtime.py 构建 context dict:
  context["agent_name"] = entry_agent          ← 正确设置
                │
runtime_service.py 只将 thread_id/tenant_id/user_id 注入 configurable
  agent_name 留在 context 参数中，不在 configurable 中
                │
orchestration_selector 的 _orchestration_payload():
  读 configurable → 非空（有 thread_id 等）→ 直接返回
  永远不走 context fallback
                │
  结果: agent_name = None → 走自动推断逻辑
```

### 4.2 架构原因

即使修复了传递链路，`entry_agent` 在当前架构下也没有合理语义：

- **leader 模式**：leader 节点在图编译时已固定（`make_lead_agent` 在 `build_entry_graph` 中被调用），运行时不会根据 `entry_agent` 动态切换
- **workflow 模式**：planner 是唯一的入口编排器，它基于 LLM 推理自主路由——`entry_agent` 没有注入点

### 4.3 处置建议

```
MessageStreamRequest:
    entry_agent: str | None  # DEPRECATED — 运行时忽略此值，保留字段仅供向后兼容
```

平台侧不应依赖此字段做任何逻辑。

---

## 5. 平台 UI 概念 → DeerFlow 参数映射

### 5.1 映射总表

| 平台 UI 操作 | DeerFlow 运行时参数 | 模式 |
|---|---|---|
| 创建独立智能体 A | `allowed_agents: ["a"]`, `mode: "leader"` | 单 agent |
| 智能体 A 添加子智能体 B, C | `allowed_agents: ["a", "b", "c"]`, `mode: "workflow"` | 多 agent 协作 |
| 智能体 A 添加子智能体 B, C, D（4+个）| 同上，列表更长 | 同上 |
| 删除子智能体 C | 从 `allowed_agents` 中移除 "c" | — |
| 设置 A 为主力、B 为辅助 | 通过 A/B 各自的 `description` 文案区分 | 见 §5.3 |
| 给这组智能体命名 | `group_key: "team-xxx"` | 标识 + 排障用 |

### 5.2 完整请求示例

**场景：用户在平台上创建了"HR 综合助手"，包含 hr-agent + meeting-agent + doc-agent**

```
第一步：确保 3 个 agent 的 MCP/Skill/配置已同步到 DeerFlow
  PUT  /api/mcp/config/hr-system       → hr-agent 的 MCP
  PUT  /api/mcp/config/meeting-system   → meeting-agent 的 MCP
  POST /api/skills/install_from_payload → doc-agent 的 Skill
  POST /api/agents/sync                 → 3 个 agent 定义

第二步：创建 thread
  POST /api/runtime/threads
  {"portal_session_id": "sess_abc123"}

第三步：发消息
  POST /api/runtime/threads/{id}/messages:stream
  {
      "message": "帮我请下周一的假，然后预定周二上午的会议室",
      "group_key": "hr-composite-assistant",
      "allowed_agents": ["hr-agent", "meeting-agent", "doc-agent"],
      "requested_orchestration_mode": "workflow",
      "metadata": {
          "source": "moss-dev-portal",
          "portal_session_id": "sess_abc123"
      }
  }
```

**DeerFlow planner 的实际行为**：

```
收到消息 + 3 个可用 agent:
  - hr-agent: 处理 HR 相关业务，包括考勤、请假、入离职
  - meeting-agent: 处理会议预定、会议室管理
  - doc-agent: 处理文档生成、模板填充

LLM 规划输出:
[
  {"description": "为用户请下周一的假", "assigned_agent": "hr-agent", "depends_on": []},
  {"description": "预定周二上午的会议室", "assigned_agent": "meeting-agent", "depends_on": []}
]

→ 两个任务并行执行，doc-agent 本次未被使用（planner 判断不需要）
```

### 5.3 如何表达"主次关系"

DeerFlow 的 planner 是 LLM——它根据每个 agent 的 **description 文本** 做路由决策。平台可以通过精心编写 description 来引导路由优先级：

```yaml
# 主力 agent — description 涵盖面广
name: general-assistant
domain: general
description: >
  通用业务助手，能处理大部分日常业务请求，包括数据查询、报告生成、
  流程审批、日程安排。优先将任务分配给本 agent，除非涉及以下专项领域。

# 辅助 agent — description 限定在狭窄专项
name: financial-analyst
domain: financial-analysis
description: >
  专业财务分析师，仅处理复杂财务建模、DCF 估值、财务报表深度分析等专项工作。
  日常数据查询和简单报表不在此 agent 范围内。
```

planner 看到这两段 description 后，自然会把大部分任务路由给 general-assistant，只有涉及复杂财务分析时才路由给 financial-analyst。

**这比硬编码 `entry_agent` 更灵活**：平台只需在 agent 同步时写好 description，不用每次发消息时做复杂的路由决策。

---

## 6. 平台侧心智模型 vs DeerFlow 实际模型

```
平台当前的心智模型：              DeerFlow 的实际模型：

  Agent A（入口）                  planner（固定入口）
  ├── Agent B（被 A 调用）          ├── Agent A（peer）
  └── Agent C（被 A 调用）          ├── Agent B（peer）
                                    └── Agent C（peer）
  层级调用树                        扁平协作 + 任务依赖图
  静态路由                          LLM 动态路由
  A 控制 B/C 的调用时机             planner 控制所有 agent 的调用时机
```

**核心区别**：

| 维度 | 平台心智模型 | DeerFlow 实际 |
|------|------------|--------------|
| 入口 | 指定的 entry_agent | planner（固定，LLM 驱动） |
| 路由决策者 | 父 agent | planner LLM |
| agent 间关系 | 树形层级 | 扁平 peer，通过 depends_on 表达数据流 |
| 路由依据 | 静态配置 | LLM 基于 domain + description + 用户输入实时推理 |
| 优先级控制 | 硬编码 entry_agent | 通过 description 文案语义引导 |

**平台侧需要做的认知转换**：

> "添加子智能体 B" 的操作在 DeerFlow 侧不是建立"A → B"的调用关系，
> 而是把 B 加入 A 所在的**协作池**（`allowed_agents`）。
> planner 在池中按需调度，不存在固定的调用层级。

---

## 7. 需要平台侧确认的事项

| # | 事项 | DeerFlow 侧建议 | 待平台确认 |
|---|------|-----------------|-----------|
| 1 | `entry_agent` 废弃 | 标记 deprecated，runtime 忽略 | 平台侧是否有其他依赖此字段的逻辑？ |
| 2 | `allowed_agents` 作为"子智能体"的载体 | 平台侧"添加/移除子智能体"映射为修改此列表 | 是否符合平台的产品预期？ |
| 3 | 主次关系通过 description 引导 | 同步 agent 时精心编写 description | 平台的 agent 编辑 UI 是否支持编辑 description？ |
| 4 | `group_key` 命名规范 | 建议 `{agent-group-name}` 或 `{composite-agent-id}` | 平台侧的生成策略？ |
| 5 | `requested_orchestration_mode` 传递链路修复 | DeerFlow 侧将修复 context → configurable 注入 | 修复后平台可通过此字段控制 leader/workflow |

---

## 8. 接口参数速查

### `POST /api/runtime/threads/{id}/messages:stream`

```jsonc
{
    // 必填
    "message": "用户消息文本",
    "group_key": "composite-agent-xxx",          // ≤128 字符，标识 agent 分组
    "allowed_agents": ["agent-a", "agent-b"],    // 1-100 个，平台"子智能体"列表

    // 可选
    "entry_agent": null,                         // DEPRECATED — 不传或传 null
    "requested_orchestration_mode": "workflow",   // "auto" | "leader" | "workflow"
    "metadata": {                                 // 值只允许 primitive（string/number/boolean/null）
        "source": "moss-dev-portal",
        "portal_session_id": "sess_abc123"
    }
}
```

### 模式选择指南

| 平台场景 | `requested_orchestration_mode` | `allowed_agents` |
|----------|-------------------------------|-------------------|
| 单 agent 独立对话 | `"leader"` | `["agent-a"]`（仅 1 个） |
| 多 agent 协作 | `"workflow"` | `["agent-a", "agent-b", "agent-c"]` |
| 不确定，让 DeerFlow 决定 | `"auto"` 或不传 | 按需 |

---

## 附录 A：常见问题

**Q: 平台能否控制"先让 A 处理，A 处理不了再让 B 处理"？**

A: 不能通过接口参数控制。但可以通过 Agent A 的 `description` 写明"优先处理 xxx 类请求"，Agent B 的 `description` 写明"仅处理 A 无法覆盖的 yyy 专项"。planner LLM 会据此做路由决策。

**Q: planner 的路由决策靠谱吗？会不会乱分派？**

A: planner 是 LLM 驱动的，路由质量取决于：
1. 每个 agent 的 `description` 是否清晰、无歧义
2. agent 的 `domain` 标签是否区分度足够
3. 用户请求是否明确

建议平台侧在 agent 编辑 UI 中强调 description 的重要性，提供编写指引。

**Q: 如果以后 DeerFlow 支持了动态 entry_agent，需要改什么？**

A: 仅需：
1. DeerFlow 侧重新启用 `entry_agent` 字段
2. 平台侧在 `messages:stream` 请求中传入
3. 不影响 `allowed_agents` 的使用方式

当前标记 deprecated 而非移除，就是为了预留这个升级路径。

**Q: `allowed_agents` 里只传 1 个 agent + `mode: "workflow"` 会怎样？**

A: planner 会把所有任务都分配给这一个 agent。功能上等价于 leader 模式但多了 planner 的开销。建议单 agent 场景直接用 `mode: "leader"`。

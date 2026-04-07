# New Agent Onboarding Guide

- Status: `drafted from implemented backend behavior`
- Source of truth:
  - `backend/src/config/agents_config.py`
  - `backend/src/config/onboarding.py`
  - `backend/src/config/capability_profiles.py`
  - `backend/src/config/platform_capabilities.py`
- Audience: 新增 DeerFlow domain agent 的研发 / 配置同学

## 1. 目标

这份文档回答两个问题：

1. 新 agent 接入时，**应该配置什么**
2. 现有平台能力下，**哪些配置不应该由 agent 自己手配**

结论先说：

- 新 agent 默认只需要声明**业务身份**和**能力暴露面**
- 运行时骨架能力已经由平台托管，不需要为单个 agent 重新 wiring
- 高级能力通过 `Capability Profile` 接入，不通过堆内部参数接入

## 2. 平台已经默认提供什么

以下能力属于 `Platform Core`，新 agent 默认继承，不应再按单 agent 配置：

- engine registry
- workflow runtime
- intervention / clarification / help escalation protocol
- runtime hook harness
- parallel scheduler
- governance core
- observability base
- verifier runtime integration
- output guardrails
- MCP binding runtime manager
- subagent delegation
- middleware chain
- build-time extension hooks
- sandbox + workspace runtime

对新 agent 来说，这意味着：

- 不需要自己接 scheduler
- 不需要自己接 clarification / resume 协议
- 不需要自己拼 middleware
- 不需要自己管理 sandbox / workspace / uploads / outputs
- 不需要自己实现 MCP 连接生命周期
- 不需要自己实现 structured completion guardrails

## 3. 新 Agent 最小接入模型

根据 `onboarding.py`，新 agent 的字段分为三类。

### 3.1 必填

只有两个：

- `name`
- `domain`

要求：

- `name` 不能为空，且目录名 / agent 名建议保持一致
- `domain` 不能为空；router 发现 domain agent 依赖它

### 3.2 业务可选

这些字段可以按业务需要填写：

- `description`
- `system_prompt_file`
- `available_skills`
- `mcp_binding`
- `tool_groups`
- `engine_type`
- `requested_orchestration_mode`
- `model`

### 3.3 平台内部字段

这些字段**不应作为新 agent 的常规接入配置**：

- `persistent_memory_enabled`
- `persistent_runbook_file`
- `hitl_keywords`
- `intervention_policies`
- `max_tool_calls`
- `guardrail_structured_completion`
- `guardrail_max_retries`
- `guardrail_safe_default`

如果显式设置，上述字段在 `validate_onboarding()` 中会产生 warning，表示你在触碰平台内部能力。

## 4. 推荐目录结构

新 agent 推荐使用下面的目录结构：

```text
backend/.deer-flow/agents/<agent-name>/
├── config.yaml
├── SOUL.md
└── RUNBOOK.md              # 仅当申请 runbook / persistent memory profile 时需要
```

说明：

- `config.yaml` 是 agent 配置
- `SOUL.md` 是 system prompt 身份文件
- `RUNBOOK.md` 是 profile 级文档，不是所有 agent 都需要

## 5. 最小可用配置模板

这是新 agent 的推荐起步模板：

```yaml
name: meeting-agent
domain: meeting
description: 负责会议相关查询与执行

# 可选
system_prompt_file: SOUL.md
engine_type: react
requested_orchestration_mode: workflow

# 业务能力暴露面
tool_groups:
  - meeting

available_skills:
  - calendar
  - docs

mcp_binding:
  use_global: true
  domain:
    - meeting-room-server
  shared:
    - contacts-directory
```

对应的 `SOUL.md` 只需要写业务身份、职责边界、成功标准，不需要描述平台内部协议。

## 6. 每个配置项怎么选

### `system_prompt_file`

- 默认可不填，平台会读取 `SOUL.md`
- 只有在你需要自定义文件名时才配置

### `engine_type`

当前支持的 canonical engine type：

- `default`
- `react`
- `read_only_explorer`
- `sop`

建议：

- 普通执行型 domain agent：优先 `react`
- 只读探索 / 检索型 agent：可选 `read_only_explorer`
- 强 SOP 流程型 agent：可选 `sop`
- 不确定时：留空，走平台默认

### `requested_orchestration_mode`

可选值：

- `auto`
- `leader`
- `workflow`

建议：

- 标准 domain agent：优先 `workflow`
- 只有明确理由时才固定成 `leader`
- 大多数情况下也可以留空，交给平台自动选择

### `tool_groups`

适用于：

- 你希望明确限制 agent 只能看到哪些内建工具组

不适用于：

- 想控制 clarification / help / task_complete / task_fail 这些协议工具

这些协议工具由平台根据 agent 类型自动暴露：

- top-level agent：自动带 `ask_clarification`
- domain agent：自动带 `request_help`、`task_complete`、`task_fail`

### `available_skills`

适用于：

- 你希望将技能暴露面限制在一个 allowlist 内

留空含义：

- 暴露所有已启用技能

### `mcp_binding`

推荐只声明“引用哪些 MCP server”，不要自己处理连接逻辑。

字段含义：

- `use_global`: 是否继承 global MCP
- `domain`: 当前 domain 独享 server 名称
- `shared`: 多 agent 共享 server 名称
- `ephemeral`: 保留字段，当前 runtime 尚未正式支持

注意：

- server 名称不能为空
- `ephemeral` 当前只会触发 warning，不建议新 agent 使用

### `model`

仅在该 agent 必须使用特殊模型时才设置；否则建议走全局默认模型。

## 7. 不要这样接入

下面这些做法不推荐：

### 7.1 直接手配治理和 guardrail 内部参数

例如：

```yaml
intervention_policies:
  send_money: require_approval
guardrail_max_retries: 5
max_tool_calls: 80
```

问题：

- 会被视为平台内部字段
- onboarding 会报 warning
- 这类能力应通过 profile / 平台策略统一接入

### 7.2 为了 runbook 注入而顺手打开 persistent memory

如果你只是需要 runbook，不要默认把 `persistent_memory_enabled` 一起打开。

正确做法：

- 需要 runbook：走 `domain_runbook_support`
- 需要持久记忆：走 `persistent_domain_memory`

### 7.3 在 SOUL.md 里重复描述平台协议

例如不需要自己写：

- clarification 如何中断
- request_help 如何 resume
- sandbox 路径如何映射
- middleware 顺序如何拼装

这些都已经是平台行为。

## 8. 什么时候需要 Capability Profile

以下 4 类能力不属于“新 agent 默认接入面”，要按 profile 申请。

### 8.1 `domain_runbook_support`

适用：

- 该 agent 有明确 SOP / 流程文档需要注入 prompt

需要准备：

- `RUNBOOK.md`，或配置 `persistent_runbook_file`

### 8.2 `persistent_domain_memory`

适用：

- 该 domain 确实存在跨会话、稳定、可复用的用户偏好或事实

需要准备：

- `persistent_memory_enabled: true`
- `RUNBOOK.md`
- 允许持久化 / 禁止持久化 / truth priority / safety 边界说明

### 8.3 `domain_verifier_pack`

适用：

- 该 agent 的结果需要 domain-specific verifier 做校验

需要准备：

- verifier registry 中有该 domain 的 verifier

### 8.4 `governance_strict_mode`

适用：

- 该 domain 的治理要求高于平台默认治理路径

需要准备：

- domain 级策略和边界文档

## 9. Runbook 应该怎么写

如果 agent 申请 `domain_runbook_support` 或 `persistent_domain_memory`，`RUNBOOK.md` 至少应覆盖：

- `allowed`: 哪些信息允许复用
- `must stay`: 哪些信息必须以当前 thread 为准，不能持久化
- `conflict`: 冲突时谁优先
- `safety`: 风险和边界

否则 admission 会至少给出 warning。

## 10. 接入后如何做 readiness 校验

平台已经提供统一入口：

```python
from src.config.agents_config import load_agent_config, validate_agent_platform_readiness

cfg = load_agent_config("meeting-agent")
report = validate_agent_platform_readiness(cfg)

assert report["ok"], report["all_issues"]
```

这个校验会一次性覆盖：

- onboarding
- platform core wiring
- 当前激活的 capability profiles

### 10.1 onboarding 检查什么

- `name` / `domain` 是否为空
- 是否误设置了平台内部字段

### 10.2 platform core wiring 检查什么

- MCP binding 是否有空引用
- `ephemeral` MCP 是否被使用
- `max_tool_calls` 是否越界
- `guardrail_max_retries` 是否越界
- `guardrail_safe_default` 是否非法

### 10.3 profile admission 检查什么

根据 agent 当前激活的 profile 自动检查，例如：

- `persistent_memory_enabled=true` → 检查 `persistent_domain_memory`
- 存在 `RUNBOOK.md` → 检查 `domain_runbook_support`
- verifier 已注册 → 检查 `domain_verifier_pack`
- 有治理严格模式信号 → 检查 `governance_strict_mode`

## 11. 推荐接入流程

### 场景 A：普通新 domain agent

1. 新建 agent 目录
2. 写 `config.yaml`
3. 写 `SOUL.md`
4. 只配置 `name`、`domain` 和必要的业务可选项
5. 运行 `validate_agent_platform_readiness()`
6. 补最基础的 routing / execution / prompt 测试

### 场景 B：带 MCP 的新 domain agent

1. 按普通 agent 接入
2. 在 `mcp_binding` 中只声明 server 引用
3. 不处理连接、缓存、disconnect 逻辑
4. 跑 readiness，确认没有空引用和不支持项

### 场景 C：需要高级能力的 agent

1. 先按普通 agent 接入
2. 明确需要哪个 profile
3. 补 profile 所需文档 / artifact
4. 再打开对应 profile 配置
5. 跑 readiness + profile regression

## 12. 三个推荐模板

### 12.1 最小 agent

```yaml
name: hr-agent
domain: hr
description: 负责 HR 领域查询与执行
```

### 12.2 带能力暴露面的 agent

```yaml
name: contacts-agent
domain: contacts
description: 负责联系人与组织录查询
engine_type: read_only_explorer
tool_groups:
  - contacts
available_skills:
  - docs
  - search
mcp_binding:
  use_global: true
  domain:
    - contacts-directory
```

### 12.3 带 runbook 的 agent

```yaml
name: travel-agent
domain: travel
description: 负责出行预订与改签
engine_type: sop
requested_orchestration_mode: workflow

# 只有在 profile 场景下才建议加
persistent_runbook_file: RUNBOOK.md
```

上面这个第三个模板虽然能跑，但按平台标准，它应配合 `domain_runbook_support` 的准入流程使用，而不是当作普通 onboarding 模板。

## 13. 接入 Checklist

- agent 目录存在
- `config.yaml` 可被 `load_agent_config()` 正确加载
- `name` / `domain` 非空
- `SOUL.md` 已提供
- 只使用 Required + Business Optional 字段
- 如果声明 `mcp_binding`，没有空 server name
- 没有误用 `ephemeral`
- 没有直接手配治理 / guardrail / tool limit 内部参数
- 如果启用 runbook / persistent memory，相关 `RUNBOOK.md` 已准备
- `validate_agent_platform_readiness()` 返回 `ok=True`

## 14. 一句话原则

新 agent 只声明“我是谁、我负责什么、我能看见哪些外部能力”；至于 workflow runtime、middleware、sandbox、guardrails、clarification/help/resume 这些平台骨架，都应该默认继承，而不是由 agent 自己重新配置。

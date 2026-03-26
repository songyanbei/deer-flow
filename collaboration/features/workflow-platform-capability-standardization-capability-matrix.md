# Workflow Platform Capability Standardization Capability Matrix

- Status: `implemented` — 已代码化为 `backend/src/config/platform_capabilities.py`、`onboarding.py`、`capability_profiles.py`
- Purpose: 统一记录当前 DeerFlow 能力的层级归属、开放策略和 admission 口径
- Code Reference: 本矩阵中的分层、接入面、准入合约均有对应的代码实现和测试

## Platform Layer Definitions

### Platform Core

- 默认继承
- 平台负责 wiring
- 用户新增 agent 时无须理解内部实现

### Capability Profile

- 平台可选高级能力
- 通过 profile 启用
- 启用前有 admission check

### Pilot / Experimental

- 已有局部验证
- 尚未抽象成通用 contract
- 暂不直接对新 agent 开放

## Capability Matrix

> 代码化实现：`backend/src/config/platform_capabilities.py` — `CapabilityTier` 枚举 + `CapabilityDescriptor` 描述 + `get_capability_matrix()` 导出
> 程序化查询：`list_capabilities(CapabilityTier.PLATFORM_CORE)` / `get_capability("engine_registry")`

| Capability | Current Layer | Current Evidence | Open Strategy | Notes |
|---|---|---|---|---|
| Engine registry | Platform Core | `engine_type` + registry/builder 已全局存在 | Default | 新 agent 不应重复声明内部 builder 规则 |
| Workflow runtime | Platform Core | planner / router / executor / task_pool 已全局存在 | Default | 属于平台骨架 |
| Intervention / clarification / resume protocol | Platform Core | `thread_state` / `workflow_resume` / gateway resolve 已统一 | Default | 不应继续暴露成 agent 级协议配置 |
| Runtime hook harness | Platform Core | interrupt/state-commit hooks 已统一 | Default | 属于平台控制面 |
| Parallel scheduler | Platform Core | scheduler + graph 路径已作用于 workflow runtime | Default | 不是单一 agent pilot |
| Governance core | Platform Core | governance engine / ledger / middleware 已存在 | Default | 默认治理路径属于平台能力 |
| Observability base path | Platform Core | workflow 结构化可观测性已存在 | Default | agent 不应单独处理 trace wiring |
| Verifier runtime integration | Platform Core | verifier 已挂 runtime hooks | Default | profile 只定义 domain-specific pack |
| Persistent domain memory runtime entry | Capability Profile | 通用开关与注入入口已存在 | Admission required | 入口已平台化，domain 接入标准未完全产品化 |
| Domain runbook support | Capability Profile | runbook loader 已存在 | Admission required | 需要明确哪些 profile 必须带 runbook |
| Domain verifier pack | Capability Profile | runtime verifier integration 已存在 | Admission required | 具体 domain pack 应按准入标准开放 |
| Governance strict mode | Capability Profile | 治理能力已存在，可进一步按 domain 收紧 | Admission required | 属于 profile，而非默认对所有 agent 开启 |
| Meeting persistent memory hint extraction | Pilot / Experimental | `meeting-agent` 试点已成立 | Do not generalize directly | 当前仍是 domain-specific 逻辑 |
| Meeting-specific memory write-back boundary | Pilot / Experimental | meeting pilot 已验证 | Do not generalize directly | 需先抽象 admission contract |

## Agent Minimum Onboarding Matrix

> 代码化实现：`backend/src/config/onboarding.py` — `FieldCategory` 枚举 + `ONBOARDING_FIELDS` 分类 + `validate_onboarding()` 校验
> 程序化查询：`get_onboarding_matrix()` 导出 JSON 格式

| Category | Should User Provide? | Notes |
|---|---|---|
| `name` | Yes | 基础身份字段 |
| `domain` | Yes | 基础身份字段 |
| `SOUL.md` / prompt | Yes | 业务身份与行为边界 |
| `available_skills` | Yes | 业务能力暴露 |
| `mcp_binding` / `tool_groups` | Yes | 外部能力暴露 |
| `engine_type` | Optional | 运行模式选择，不是必须 |
| `requested_orchestration_mode` | Optional | 运行模式选择，不是必须 |
| hook registration details | No | 平台自动 wiring |
| scheduler internals | No | 平台自动 wiring |
| intervention protocol internals | No | 平台自动 wiring |
| governance ledger details | No | 平台自动 wiring |
| verifier runtime details | No | 平台自动 wiring |
| persistent memory injection internals | No | 通过 profile + validator 处理 |

## Capability Profile Admission Matrix

> 代码化实现：`backend/src/config/capability_profiles.py` — `ProfileDefinition` + per-profile admission validator + `validate_profile_admission()` 校验
> 程序化查询：`get_profile_admission_matrix()` 导出 JSON 格式
> 聚合入口：`backend/src/config/agents_config.py` — `validate_agent_platform_readiness(config)` 一次调用完成 onboarding + profile admission

| Profile | Required Config | Required Docs | Required Tests | Rollback Requirement |
|---|---|---|---|---|
| `persistent_domain_memory` | domain + enable switch | runbook + persistence boundary definition | profile regression + truth precedence regression | 关闭开关后退回默认 thread-truth behavior |
| `domain_runbook_support` | profile enablement | runbook | runbook injection regression | 关闭后不再注入 runbook context |
| `domain_verifier_pack` | domain verifier binding | verifier contract doc | verifier regression | 关闭后退回平台默认 verifier path |
| `governance_strict_mode` | profile enablement | policy / guard boundary doc | governance regression | 关闭后退回默认治理路径 |

## Standardization Rule

只有同时满足以下条件的能力，才允许从 `Pilot / Experimental` 升级为 `Capability Profile`：

1. 有稳定的平台入口
2. 有明确 admission requirement
3. 有明确 acceptance
4. 有明确 rollback
5. 不再依赖单一 domain 的特定实现细节

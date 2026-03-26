# Feature: Workflow Platform Capability Standardization

- Status: `implemented` — backend code + tests delivered, pending formal code review
- Owner suggestion: `backend` + `test`
- Related area: workflow runtime, agent onboarding, capability rollout, governance, persistent domain memory, scheduler, verifier
- Frontend impact: `none required in this phase`

## Goal

在当前已完成或已验证通过的多智能体能力基础上，新增一轮“平台能力标准化”工作，把已有能力从“某个需求做通”或“某个 pilot 成立”，升级成“平台可复制能力”。

这次需求不新增大的业务功能，不推进 Knowledge Harness，也不推进 Improvement Harness。核心目标只有三个：

1. 明确当前哪些能力已经是平台级能力，哪些仍然只是试点能力
2. 定义新 agent 的最小接入模型，避免新增 agent 时暴露过多平台内部细节
3. 定义高级能力的准入标准，让平台可以低负担、可校验、可回滚地向新 agent 开放能力

## Why This Needs To Be The Next Work

结合当前代码现状，DeerFlow 已经具备较完整的多智能体框架骨架，但“能力成立”和“能力平台化”仍然不是同一件事。

当前已经成立的平台底座包括：

- engine registry / build-time hooks
- workflow runtime + task_pool control plane
- intervention / clarification / resume structured protocol
- runtime hook harness
- dependency-aware parallel scheduler
- governance core
- structured observability / eval / verifier foundation

但当前仍存在两个明显问题：

1. 部分能力虽然已在代码中成立，但还没有抽象成平台可复制标准
2. 部分能力仍停留在 pilot 级别，尚不能面向新 agent 直接开放

最典型的例子就是 persistent domain memory：

- 平台已经有通用开关与注入入口
- 但真正启用并完成边界定义的只有 `meeting-agent`
- domain-specific 的 hint 提取仍写在 pilot 逻辑里

这说明下一步不该只是继续扩更多 agent，而应该先把“试点通过的能力”抽象成平台标准，再决定后续如何开放。

## Current State Summary

### 已经是平台级能力的

这些能力应视为 `Platform Core` 候选，不再按单 agent 特例理解：

1. engine registry / build-time capability loading
2. workflow runtime / planner / router / executor 主链路
3. task_pool / verified_facts / intervention state control plane
4. intervention / clarification / resume 协议
5. runtime hook harness
6. dependency-aware parallel scheduler
7. governance core / governance ledger
8. structured observability / regression / verifier runtime hooks

### 已有平台底座，但尚未标准化开放的

这些能力应视为 `Capability Profile` 候选：

1. persistent domain memory
2. domain runbook injection
3. domain-specific verifier pack
4. stricter governance profile（比默认治理更强的 domain 约束）

### 当前仍属于 pilot / experimental 的

这些能力当前不应对新 agent 直接开放：

1. `meeting-agent` 的 persistent memory 领域边界
2. `meeting-agent` 专属的 reusable hint 提取逻辑
3. 尚未抽象成通用 contract 的 domain-specific profile 行为

## Design Principles

### 1. 新 agent 接入必须保持轻量

新增 agent 时，用户只应配置业务身份相关内容，而不是配置平台内部 wiring。

用户最小只需要关心：

- `name`
- `domain`
- `SOUL.md` / system prompt
- `skills`
- `MCP binding / tool groups`
- 可选的 `engine_type`
- 可选的 `requested_orchestration_mode`

不应要求用户显式配置：

- runtime hooks
- scheduler 行为
- intervention / resume 协议
- governance ledger
- verifier wiring
- state-commit semantics
- persistent memory 注入链路

### 2. 高级能力通过 profile 接入，不通过底层配置拼装

平台应把高级能力抽象成 `Capability Profile`，而不是要求用户为单个 agent 手配大量内部参数。

启用高级能力时，应采用：

- profile 选择
- 平台校验前置条件
- 平台补齐默认 wiring
- 平台提供回滚开关

而不是：

- 让用户显式声明所有底层注入点和状态语义

### 3. 平台内部必须有显式准入标准

“可开放给新 agent”不能只靠经验判断，必须形成准入标准与验收口径。

每个高级能力都必须明确：

- 适用对象
- 依赖前提
- 必需文档 / 配置
- 必需测试
- 可观测性要求
- 回滚要求

## Scope

### In Scope

1. 定义平台能力分层模型
   - `Platform Core`
   - `Capability Profile`
   - `Pilot / Experimental`

2. 产出统一的平台能力清单
   - 梳理当前能力归属
   - 说明每项能力的成熟度、作用范围和开放策略

3. 定义新 agent 的最小接入模型
   - 最小必填项
   - 平台默认继承项
   - 不应暴露给用户的内部能力

4. 定义第一批高级能力的准入标准
   - `persistent_domain_memory`
   - `domain_runbook_support`
   - `domain_verifier_pack`
   - `governance_strict_mode`

5. 定义 profile 启用后的验收与回滚要求

6. 补齐文档、测试口径和面向后续接入的 checklist

### Out Of Scope

1. 扩新业务 agent
2. 全量推广 persistent memory 到所有 domain
3. Knowledge Harness
4. Improvement Harness
5. 重写 intervention / scheduler / governance 协议
6. 前端新增控制台或接入配置 UI

## Functional Requirements

### 1. 平台能力分层必须清晰且互斥

每项能力都必须被归类到以下三层之一：

- `Platform Core`
- `Capability Profile`
- `Pilot / Experimental`

不能再保留“默认感觉是平台能力，但实际上只有单个 agent 在用”的模糊状态。

### 2. 新 agent 最小接入模型必须稳定

平台必须定义并对齐一套最小接入模型，保证新增 agent 时只需声明业务身份与外部能力，不需要理解平台内部实现。

### 3. Capability Profile 必须具备显式准入标准

每个 profile 至少要定义：

- profile 目标
- 适用 domain 特征
- 必需配置
- 必需文档
- 必需测试
- 默认回滚方式

### 4. 准入标准必须由平台验证，不应转化为用户负担

对于高级能力，平台应尽量通过 validator / checklist 检查接入条件，而不是把所有要求暴露成用户必须手填的配置项。

### 5. 验收与回滚必须可操作

每个 profile 都必须能回答两个问题：

- 接入完成后如何判断“通过”
- 关闭该 profile 后系统会回退到什么行为

## Proposed Platform Model

### A. Platform Core

定义：所有 agent 默认继承、平台负责维护、用户无需感知的基础能力。

当前建议纳入：

1. engine registry
2. workflow runtime
3. scheduler
4. intervention / clarification / resume protocol
5. runtime hook harness
6. governance base path
7. observability base path
8. verifier runtime integration

### B. Capability Profile

定义：平台可选高级能力，通过 profile 接入，由平台做 admission check。

当前第一批候选：

1. `persistent_domain_memory`
2. `domain_runbook_support`
3. `domain_verifier_pack`
4. `governance_strict_mode`

### C. Pilot / Experimental

定义：已验证局部价值，但仍未抽象为通用 contract 的能力。

当前候选：

1. `meeting-agent` persistent memory 的具体 hint 提取规则
2. 仅适用于单 domain 的特定 write-back 逻辑

## Agent Minimum Onboarding Model

平台应正式定义：一个新 agent 的创建，只允许默认围绕以下信息展开：

1. agent identity
   - `name`
   - `domain`

2. prompt identity
   - `SOUL.md` 或等价 system prompt

3. capability exposure
   - `skills`
   - `MCP binding`
   - `tool_groups`

4. optional runtime selector
   - `engine_type`
   - `requested_orchestration_mode`

除上述内容外，其余运行时能力默认由平台提供，不作为用户新增 agent 的主配置负担。

## Admission Model For Capability Profiles

每个 profile 必须按统一模板定义：

1. `Profile Definition`
   - 这个 profile 做什么
   - 为什么不是 core
   - 面向哪些 domain

2. `Admission Requirements`
   - 开启 profile 前必须满足什么

3. `Acceptance`
   - 接入完成后需要通过哪些回归

4. `Rollback`
   - 关闭 profile 后如何退回默认行为

其中 `persistent_domain_memory` 的 admission 需至少覆盖：

- 有明确 `domain`
- 有 profile 对应 runbook
- 有允许持久化的信息边界
- 有禁止持久化的信息边界
- 有 truth priority 说明
- 有 rollback 开关
- 有 regression 覆盖

## Deliverables

本阶段交付物应包括：

1. 一份主需求文档 — ✅ 本文档
2. 一份 backend checklist — ✅ `workflow-platform-capability-standardization-backend-checklist.md`
3. 一份 test checklist — ✅ `workflow-platform-capability-standardization-test-checklist.md`
4. 一份 capability / admission matrix — ✅ `workflow-platform-capability-standardization-capability-matrix.md`

### Backend Code Deliverables

| 模块 | 路径 | 说明 |
|------|------|------|
| 平台能力清单 | `backend/src/config/platform_capabilities.py` | 14 项能力分三层，`CapabilityTier` 枚举 + `CapabilityDescriptor` 不可变描述 + 查询/导出 API |
| 最小接入合约 | `backend/src/config/onboarding.py` | 18 个 AgentConfig 字段分类为 Required / Optional / Internal，`validate_onboarding()` 校验 |
| Profile 准入合约 | `backend/src/config/capability_profiles.py` | 4 个 profile 定义 + 4 个 admission validator，每个含 goal / admission / acceptance / rollback |
| Persistent memory 平台化 | `backend/src/agents/persistent_domain_memory.py` | `DomainHintExtractor` ABC + 注册表 + `MeetingHintExtractor` pilot 标记 |
| 就绪度入口 | `backend/src/config/agents_config.py` | `validate_agent_platform_readiness()` 聚合 onboarding + profile admission |

### Test Deliverables

| 测试文件 | 用例数 | 覆盖 |
|----------|--------|------|
| `tests/test_platform_capabilities.py` | 12 | 分层完整性、查找、不可变性、矩阵导出 |
| `tests/test_onboarding.py` | 9 | 字段全覆盖、必填校验、内部字段识别 |
| `tests/test_capability_profiles.py` | 18 | 4 个 profile 的准入/拒绝/警告/自动检测 |
| `tests/test_hint_extractor_registry.py` | 8 | 注册表 CRUD、meeting 提取器行为 |
| `tests/test_platform_readiness.py` | 4 | 聚合校验 happy/sad path |
| `tests/test_persistent_domain_memory.py` | 11 | 原有测试全部通过（向后兼容） |

## Acceptance Criteria

1. ✅ 当前 DeerFlow 已有能力被统一梳理为 `Platform Core / Capability Profile / Pilot` — `platform_capabilities.py` 中 14 项能力分三层
2. ✅ 新 agent 最小接入模型被明确写成平台标准 — `onboarding.py` 中 18 字段分类 + `validate_onboarding()` 校验
3. ✅ 第一批 capability profile 的 admission 标准被明确写成平台标准 — `capability_profiles.py` 中 4 个 profile 定义 + validator
4. ✅ 文档足以支持后续新增 agent 时复用同一接入路径 — admission contract + onboarding matrix + 代码中有完整校验
5. ✅ 本阶段不引入新的业务功能负担，也不要求前端新增配置入口 — 无业务功能改动，无前端改动

## Recommended Next Step After This Work

完成本阶段后，后续平台扩展应优先按”能力标准化后的接入流程”推进，例如：

1. 选择第二个适合的 domain，按标准接入 `persistent_domain_memory`
   - 运行 `validate_profile_admission(“persistent_domain_memory”, config)` 确认准入
   - 实现该 domain 的 `DomainHintExtractor` 并注册
2. 把 capability validator / onboarding checklist 落到 CI 中（如在 `load_agent_config` 路径中自动执行）
3. 在平台能力标准稳定后，再进入 Knowledge Harness

## Related Docs

- [workflow-phase2-two-stage-scheduler-and-persistent-domain-agent.md](E:\work\deer-flow\collaboration\features\workflow-phase2-two-stage-scheduler-and-persistent-domain-agent.md)
- [workflow-phase5-two-stage-governance-core-and-operator-console.md](E:\work\deer-flow\collaboration\features\workflow-phase5-two-stage-governance-core-and-operator-console.md)
- [workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md](E:\work\deer-flow\collaboration\features\workflow-runtime-hook-harness-slice-b-interrupt-state-commit.md)

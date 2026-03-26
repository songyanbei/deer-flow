# Workflow Platform Capability Standardization Backend Checklist

- Audience: `backend`
- Status: `implemented` — 代码 + 测试已交付，pending formal code review
- Goal: 把已验证通过的能力抽象成平台标准，而不是继续在单个 agent 上复制特例

## Backend Role

后端在本阶段负责三件事：

1. 统一梳理当前能力归属
2. 定义新 agent 的最小接入模型
3. 定义并固化 capability profile 的 admission contract

本阶段不是扩新业务 agent，也不是继续做单个 pilot 的需求实现。

## Scope

### In Scope

1. 梳理当前 backend 已有能力，区分：
   - `Platform Core`
   - `Capability Profile`
   - `Pilot / Experimental`

2. 对齐 agent config 的最小接入面
   - 哪些字段属于用户需要理解的最小输入
   - 哪些字段属于平台内部 wiring，不应进一步暴露

3. 为第一批 profile 定义 admission contract
   - `persistent_domain_memory`
   - `domain_runbook_support`
   - `domain_verifier_pack`
   - `governance_strict_mode`

4. 明确 admission contract 对应的 backend 责任
   - config schema
   - validator
   - runtime default behavior
   - rollback semantics

### Out Of Scope

1. 新增业务 domain agent
2. 全量推广 persistent memory 到更多 domain
3. 重写 scheduler / governance / intervention 实现
4. 新增 frontend 配置页
5. Knowledge Harness / Improvement Harness

## Required Backend Outputs

### 1. Platform Capability Inventory — ✅ Delivered

> 代码：`backend/src/config/platform_capabilities.py`
> 测试：`backend/tests/test_platform_capabilities.py` (12 tests)

后端已输出能力清单，共 14 项能力分三层：

**Platform Core (8)**:
1. ✅ engine registry
2. ✅ workflow runtime
3. ✅ parallel scheduler
4. ✅ intervention / clarification / resume protocol
5. ✅ runtime hook harness
6. ✅ governance core
7. ✅ observability base path
8. ✅ verifier runtime integration

**Capability Profile (4)**:
1. ✅ persistent domain memory
2. ✅ domain runbook support
3. ✅ domain verifier pack
4. ✅ governance strict mode

**Pilot / Experimental (2)**:
1. ✅ meeting persistent memory hint extraction
2. ✅ meeting memory write-back boundary

### 2. Agent Minimum Onboarding Contract — ✅ Delivered

> 代码：`backend/src/config/onboarding.py`
> 测试：`backend/tests/test_onboarding.py` (9 tests)

后端已明确全部 18 个 `AgentConfig` 字段的接入分类：

**Required（必填）**:
1. ✅ `name`
2. ✅ `domain`

**Business Optional（业务可选）**:
3. ✅ `description`
4. ✅ `system_prompt_file` / `SOUL.md`
5. ✅ `available_skills`
6. ✅ `mcp_binding`
7. ✅ `tool_groups`
8. ✅ `engine_type`
9. ✅ `requested_orchestration_mode`
10. ✅ `model`

**Platform Internal（平台内部，不应暴露给用户）**:
11. ✅ `persistent_memory_enabled` — 通过 profile admission 管理
12. ✅ `persistent_runbook_file` — 通过 profile admission 管理
13. ✅ `hitl_keywords` — Phase 1 backward-compat
14. ✅ `intervention_policies` — governance wiring
15. ✅ `max_tool_calls` — platform safety default
16. ✅ `guardrail_structured_completion` — platform guardrail
17. ✅ `guardrail_max_retries` — platform guardrail
18. ✅ `guardrail_safe_default` — platform guardrail

`validate_onboarding()` 校验器在字段缺失或 internal 字段被显式设置时自动报告 error/warning。

测试 `test_onboarding_fields_cover_all_agent_config_fields` 确保每次 `AgentConfig` 新增字段时必须同步更新分类。

### 3. Capability Profile Admission Contract — ✅ Delivered

> 代码：`backend/src/config/capability_profiles.py`
> 测试：`backend/tests/test_capability_profiles.py` (18 tests)

后端已为每个 profile 输出统一 admission contract，每个 profile 包含：

1. ✅ profile name + display_name
2. ✅ profile goal
3. ✅ why_not_core
4. ✅ target_domains
5. ✅ required config
6. ✅ required docs / artifacts
7. ✅ required tests
8. ✅ rollback path

每个 profile 还有对应的 admission validator：

- `persistent_domain_memory` → 检查 domain / enable switch / RUNBOOK.md 存在性 / runbook 内容完整性 / hint extractor 注册
- `domain_runbook_support` → 检查 runbook 文件存在性
- `domain_verifier_pack` → 检查 domain / verifier registry 注册
- `governance_strict_mode` → 检查 domain

`validate_all_active_profiles()` 可自动检测 agent config 中哪些 profile 被隐式激活。

### 4. Persistent Domain Memory Admission Definition — ✅ Delivered

> 代码：`backend/src/agents/persistent_domain_memory.py`
> 测试：`backend/tests/test_hint_extractor_registry.py` (8 tests) + `backend/tests/test_persistent_domain_memory.py` (11 existing tests, all pass)

后端已把 pilot-only 的逻辑和平台级 profile 分开：

1. ✅ `persistent_memory_enabled` 只是入口开关 — admission validator 明确检查全部准入条件
2. ✅ 新 domain 接入前必须满足：
   - ✅ 明确 domain — admission check `domain_required`
   - ✅ runbook — admission check `runbook_exists` + `runbook_section_*`
   - ✅ allowlist / denylist boundary — runbook 内容检查 `allowed` + `must_stay` sections
   - ✅ truth priority — runbook 内容检查 `conflict` section
   - ✅ rollback switch — `ProfileDefinition.rollback_doc` 明确说明关闭开关即回退
   - ✅ regression — `ProfileDefinition.required_tests_doc` 明确回归要求
3. ✅ domain-specific hint extraction 通过 `DomainHintExtractor` ABC + 注册表实现
   - `MeetingHintExtractor` 明确标记为 Pilot / Experimental
   - 新 domain 必须注册自己的 extractor，不得复制 meeting 的实现
   - admission validator 在域缺少 extractor 时发出 warning

## Change Surface

本阶段实际改动面：

**修改的文件**:
- `backend/src/config/agents_config.py` — 新增 `validate_agent_platform_readiness()` 聚合入口
- `backend/src/agents/persistent_domain_memory.py` — 引入 `DomainHintExtractor` ABC + 注册表，将 meeting pilot 逻辑封装为 `MeetingHintExtractor`

**新增的文件**:
- `backend/src/config/platform_capabilities.py` — 平台能力清单
- `backend/src/config/onboarding.py` — 最小接入合约 + 校验
- `backend/src/config/capability_profiles.py` — Profile 准入合约 + 校验
- `backend/tests/test_platform_capabilities.py` — 12 tests
- `backend/tests/test_onboarding.py` — 9 tests
- `backend/tests/test_capability_profiles.py` — 18 tests
- `backend/tests/test_hint_extractor_registry.py` — 8 tests
- `backend/tests/test_platform_readiness.py` — 4 tests

**未改动的文件（符合设计预期）**:
- `backend/src/agents/governance/` — 无需修改，governance core 已是 Platform Core
- `backend/src/agents/hooks/` — 无需修改，hook harness 已是 Platform Core
- `backend/src/agents/thread_state.py` — 无需修改，本阶段不改运行时状态

所有 validator 和 admission helper 均落在 `backend/src/config/` 标准化模块内，未散落到 domain agent 目录。

## Backend Acceptance Criteria

1. ✅ 平台能力分层口径在 backend 侧清晰一致 — `platform_capabilities.py` 中 14 项能力互斥分三层
2. ✅ 新 agent 最小接入模型在 backend 侧清晰一致 — `onboarding.py` 中 18 字段分类 + `validate_onboarding()`
3. ✅ capability profile admission contract 在 backend 侧清晰一致 — `capability_profiles.py` 中 4 个 profile 的统一模板 + validator
4. ✅ persistent domain memory 不再被表述成”开了开关就等于可复制” — admission validator 检查 domain / runbook / hint extractor，`MeetingHintExtractor` 明确标记 Pilot
5. ✅ 后续新增 domain agent 时，backend 可以依据同一 admission 标准执行 — `validate_agent_platform_readiness()` 一次调用完成 onboarding + profile admission 检查

## Handoff Rule

如果 backend 在标准化过程中发现需要 frontend 补充展示、配置或运营入口，不在本阶段自行扩展范围，统一通过 `handoffs/backend-to-frontend.md` 提出。

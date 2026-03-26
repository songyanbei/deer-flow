# Workflow Platform Capability Standardization Test Checklist

- Audience: `test`
- Status: `implemented` — 51 个新测试已交付，覆盖下述全部 Required Test Coverage 维度
- Goal: 验证平台能力标准、准入口径和回滚标准清晰可执行，而不是只验证某个 agent case 是否跑通

## Test Role

测试在本阶段的重点不是新增业务回归，而是确认：

1. 能力分层是否清楚
2. 最小接入模型是否稳定
3. capability profile 的 admission / acceptance / rollback 是否可验证

## Scope

### In Scope

1. 验证平台能力清单与当前代码事实是否一致
2. 验证新 agent 最小接入模型是否合理、收敛
3. 验证 profile admission 标准是否具备可执行性
4. 验证 rollback 标准是否清晰
5. 验证文档是否足以支持后续接入复用

### Out Of Scope

1. 新业务 agent 功能测试
2. 新 UI 功能测试
3. Knowledge Harness / Improvement Harness 测试
4. 对现有 scheduler / governance / intervention 功能做额外重构验证

## Required Test Coverage

### 1. Capability Classification Consistency — ✅ Covered

> 测试文件：`tests/test_platform_capabilities.py` (12 tests)

测试已确认：

1. ✅ 哪些能力属于 `Platform Core` — `test_platform_core_count` 确认 ≥ 8 项
2. ✅ 哪些能力属于 `Capability Profile` — `test_capability_profile_count` 确认 ≥ 4 项
3. ✅ 哪些能力仍属于 `Pilot / Experimental` — `test_pilot_experimental_count` 确认 ≥ 2 项
4. ✅ 每项能力互斥归属一层 — `test_all_capabilities_assigned_to_exactly_one_tier`
5. ✅ 分层标记不可篡改 — `test_capability_descriptor_is_immutable`
6. ✅ meeting hint extraction 明确标记为 Pilot — `test_meeting_hints_is_pilot`
7. ✅ persistent domain memory 明确标记为 Capability Profile — `test_get_persistent_domain_memory_is_capability_profile`

### 2. Agent Minimum Onboarding Model Consistency — ✅ Covered

> 测试文件：`tests/test_onboarding.py` (9 tests)

测试已确认：

1. ✅ 最小接入项只有 `name` + `domain` — `test_required_fields_are_name_and_domain`
2. ✅ 全部 AgentConfig 字段均有分类 — `test_onboarding_fields_cover_all_agent_config_fields`（新增字段未分类时自动 fail）
3. ✅ platform internal 字段包含 hooks/intervention/guardrails — `test_platform_internal_fields_include_hook_wiring`
4. ✅ 缺 domain 时 error — `test_missing_domain_is_error`
5. ✅ 缺 name 时 error — `test_missing_name_is_error`
6. ✅ 内部字段被显式设置时 warning — `test_persistent_memory_without_admission_warns`

### 3. Capability Profile Admission Coverage — ✅ Covered

> 测试文件：`tests/test_capability_profiles.py` (18 tests)

测试已针对每个 profile 检查：

1. ✅ 4 个 profile 全部注册 — `test_all_four_profiles_registered`
2. ✅ 每个 profile 定义完整 — `test_profile_definitions_have_required_fields`
3. ✅ `persistent_domain_memory` 缺域/缺开关/缺 runbook 时 error — 3 个 test
4. ✅ `persistent_domain_memory` 有效 runbook 通过 — `test_pdm_admission_passes_with_valid_runbook`
5. ✅ `persistent_domain_memory` 不完整 runbook 时 warning — `test_pdm_admission_warns_on_incomplete_runbook`
6. ✅ `domain_runbook_support` 缺文件时 error，有文件通过 — 2 个 test
7. ✅ `domain_verifier_pack` 缺域 error，未注册 verifier 时 warning — 2 个 test
8. ✅ `governance_strict_mode` 缺域 error，有域通过 — 2 个 test
9. ✅ 未知 profile 抛 ValueError — `test_unknown_profile_raises_value_error`
10. ✅ 自动检测激活的 profile — `test_validate_all_active_profiles_detects_pdm`

### 4. Persistent Domain Memory Special Review — ✅ Covered

> 测试文件：`tests/test_hint_extractor_registry.py` (8 tests) + `tests/test_persistent_domain_memory.py` (11 existing)

测试已确认：

1. ✅ 平台能力：`DomainHintExtractor` ABC + 注册表 — `test_register_custom_extractor`
2. ✅ meeting-agent pilot 逻辑封装为 `MeetingHintExtractor` — `test_meeting_extractor_auto_registered`
3. ✅ 新 domain 必须注册自己的 extractor — `test_get_hint_extractor_returns_none_for_unknown`
4. ✅ admission validator 在域缺 extractor 时发出 warning（不是直接放行）
5. ✅ 原有 11 个 persistent domain memory 测试全部通过（向后兼容）

### 5. Documentation Acceptance — ✅ Covered

> 测试文件：`tests/test_platform_readiness.py` (4 tests)

测试已确认：

1. ✅ 新 agent 创建时调用 `validate_agent_platform_readiness()` 即可一次性获取全部问题 — `test_plain_domain_agent_is_ready`
2. ✅ profile 准入失败有清晰的 error 列表 — `test_agent_with_pdm_but_no_runbook_fails`
3. ✅ profile 通过后报告清晰 — `test_meeting_agent_with_valid_setup`
4. ✅ 矩阵导出 API 可供文档/UI 消费 — `test_admission_matrix_structure` + `test_onboarding_matrix_structure`

## Recommended Regression View

本阶段建议把测试视角分成两层：

1. `Platform Standard Regression`
   - 验证文档与平台事实一致
   - 验证标准能复用

2. `Profile Admission Regression`
   - 验证某个 profile 接入条件是否被定义清楚
   - 验证 acceptance / rollback 是否明确

## Test Acceptance Criteria

1. ✅ 测试可以仅依赖本阶段文档判断某项能力属于哪一层 — `platform_capabilities.py` + `get_capability()` API
2. ✅ 测试可以仅依赖本阶段文档判断新增 agent 的最小接入面 — `onboarding.py` + `validate_onboarding()` API
3. ✅ 测试可以仅依赖本阶段文档判断 profile 的 admission 是否完整 — `capability_profiles.py` + `validate_profile_admission()` API
4. ✅ 测试可以仅依赖本阶段文档判断 profile 的 rollback 是否明确 — `ProfileDefinition.rollback_doc` 字段
5. ✅ 没有继续把 pilot 成功误判为平台 ready 的模糊地带 — `MeetingHintExtractor` 标记 Pilot + admission validator 检查 extractor 注册

## Handoff Rule

如果测试在阅读标准化文档后，仍无法判断某项能力的层级、准入或回滚规则，应通过 `handoffs/test-to-backend` 等协作路径回提，不应自行假设。

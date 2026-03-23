# Workflow Verification Harness Phase 4 Test Checklist

- Status: `draft`
- Owner: `test`
- Related feature:
  - [workflow-verification-harness-phase4.md](./workflow-verification-harness-phase4.md)
  - [workflow-verification-harness-phase4-backend-checklist.md](./workflow-verification-harness-phase4-backend-checklist.md)
- Frontend impact target: `none required in this phase`

## 0. Test Objective

Phase 4 测试的目标不是验证“代码里有没有 verifier 类”，而是验证：

1. verifier registry 是否成为唯一可信的 verifier 解析入口
2. task result / workflow final result 是否真的被 verifier gate 拦住
3. verification 失败时 runtime 是否按约定进入 `needs_replan / hard_fail`
4. verification 结果是否真正进入 state / replay / report
5. 既有 workflow 主链路在 verifier 通过时是否不回归

## 1. Test Strategy Overview

Phase 4 测试分成 6 层：

### A. Verification Contract Unit Tests

验证：

- verdict 枚举
- result/report schema
- context schema
- 默认 no-op / fallback 行为

### B. Registry / Resolver Tests

验证：

- task verifier 解析
- workflow verifier 解析
- artifact validator 解析
- fallback / missing behavior

### C. Built-In Verifier Unit Tests

验证：

- `meeting` verifier
- `contacts` verifier
- `hr` verifier
- workflow verifier
- generic artifact validator

### D. Runtime Integration Tests

验证：

- executor task verification gate
- planner workflow-final verification gate
- retry / replan 边界
- hard fail 边界

### E. Workflow Regression Tests

验证：

- happy path 不回归
- clarification 不回归
- intervention 不回归
- cross-domain workflow 不回归

### F. Evals / Report Regression Tests

验证：

- `backend/src/evals/` 输出 verification 字段
- markdown/json report 正确展示 verifier 结果
- phase0 benchmark 接入 verifier 后可继续运行

## 2. Current Testing Baseline

当前可直接复用的测试基础：

- `backend/tests/test_evals.py`
  - 已有 phase0 evals schema / runner / report 测试基础
- `backend/tests/test_multi_agent_graph.py`
  - 已有 workflow graph happy path / clarification / intervention 测试基础
- `backend/tests/test_executor_outcome.py`
  - 已有 executor outcome normalization 测试基础
- `backend/tests/test_runtime_queue_stage.py`
  - 已有 workflow stage / queue / resume 相关测试基础

但当前缺少：

- verifier contract 的独立测试
- verifier registry / resolver 的独立测试
- meeting / contacts / hr / workflow verifier 的单元测试
- verification 进入 runtime gate 后的集成测试
- verification 结果写入 eval/report 的测试

## 3. Test Guardrails

- [ ] 只测 workflow 模式
- [ ] 不把测试范围扩成 leader mode
- [ ] 不要求前端联调
- [ ] 不要求真实 MCP / 真实外部系统作为主测试路径
- [ ] 不把 verification 测试写成单纯字符串比较
- [ ] 不把需求误解为“只补 phase0 case”

Done when:

- 测试焦点稳定落在 verifier、runtime gate、report、workflow regression

### Boundary Clarification

测试侧要特别避免三种误读：

1. “Verification Harness” 不等于“只跑 benchmark”
   - 必须验证 runtime gate
   - 必须验证 state 与失败语义

2. “workflow integration tests” 不等于“改调度测试”
   - 目标是验证 verification 接入后主链路不回归
   - 不是要求新增新的 scheduler 测试体系

3. “artifact validator” 不等于“一开始就做所有 artifact 类型”
   - 本阶段只要求 generic validator 有测试
   - 不强求先覆盖所有未来 artifact 类型

## 4. Required Test Coverage By Module

### 4.1 Verification Contract Tests

- [ ] `VerificationVerdict` 仅允许 `passed / needs_replan / hard_fail`
- [ ] `VerificationResult` 必须包含 verdict 和 report
- [ ] `VerificationContext` 能表达 task / workflow / artifact 三类 scope
- [ ] fallback verifier / no-op verifier 行为清晰可断言
- [ ] `verification_feedback` remediation contract 字段完整且稳定

Done when:

- contract 成为稳定测试对象，而不是靠实现猜测

### 4.2 Registry / Resolver Tests

- [ ] `meeting` task verifier 可被正确解析
- [ ] `contacts` task verifier 可被正确解析
- [ ] `hr` task verifier 可被正确解析
- [ ] workflow verifier 可被正确解析
- [ ] generic artifact validator 可被正确解析
- [ ] 未配置 verifier 时 fallback 行为明确
- [ ] workflow-final verifier 优先按 `workflow_kind` 解析
- [ ] 缺失 `workflow_kind` 时回退 `default` verifier

Done when:

- verifier 选择不依赖 planner/executor 内嵌分支

### 4.3 Built-In Verifier Unit Tests

- [ ] `meeting` verifier 对合法结果返回 `passed`
- [ ] `meeting` verifier 对缺关键字段结果返回 `needs_replan` 或 `hard_fail`
- [ ] `contacts` verifier 对合法结果返回 `passed`
- [ ] `hr` verifier 对合法结果返回 `passed`
- [ ] workflow verifier 对合格 final summary 返回 `passed`
- [ ] workflow verifier 对 summary 与 facts/task 不一致时返回非通过 verdict
- [ ] generic artifact validator 对存在且非空文件返回 `passed`

Done when:

- 内建 verifier 的判断逻辑独立可验证

### 4.4 Executor Task Verification Integration Tests

- [ ] task verifier 通过时，task 正常 `DONE`
- [ ] task verifier 通过时，`verified_facts` 正常写回
- [ ] task verifier 返回 `needs_replan` 时，不写 `verified_facts`
- [ ] task verifier 返回 `needs_replan` 时，task 带 `verification_report`
- [ ] task verifier 返回 `needs_replan` 时，planner 可见 `verification_feedback`
- [ ] task verifier 返回 `needs_replan` 时，task 以 `FAILED` 终态结束
- [ ] task verifier 返回 `hard_fail` 时，run 进入 `ERROR`

Done when:

- 能证明 verifier 已进入 executor 提交关口

### 4.5 Planner Workflow-Final Verification Tests

- [ ] workflow verifier 通过时，planner 正常进入 `DONE`
- [ ] workflow verifier 返回 `needs_replan` 时，不直接进入 `DONE`
- [ ] workflow verifier 返回 `needs_replan` 时，retry 计数增加
- [ ] workflow verifier 返回 `needs_replan` 时，planner 直接消费 `verification_feedback`
- [ ] workflow verifier 返回 `needs_replan` 时，不生成 synthetic remediation task
- [ ] workflow verifier 优先按 `workflow_kind` 解析
- [ ] 缺失 `workflow_kind` 时回退 `default`
- [ ] workflow verifier 超预算后转 `hard_fail`
- [ ] workflow verifier 返回 `hard_fail` 时，run 进入 `ERROR`

Done when:

- 能证明 verifier 已进入 workflow-final 提交关口

### 4.6 Workflow Regression Tests

- [ ] verification 接入后，meeting happy path 不回归
- [ ] verification 接入后，contacts happy path 不回归
- [ ] verification 接入后，hr happy path 不回归
- [ ] verification 接入后，cross-domain happy path 不回归
- [ ] clarification 主链路不回归
- [ ] intervention 主链路不回归

Done when:

- Phase 4 改动没有把 workflow 主链路改坏

### 4.7 Evals / Report Tests

- [ ] `CaseRunResult` 含 verification status
- [ ] `CaseRunResult` 含 verification report / retry count
- [ ] markdown report 中可见 verifier verdict / findings
- [ ] json report 中可见 verifier 结构化结果
- [ ] phase0 benchmark 接入 verifier 后仍可跑通

Done when:

- verification 不只是 runtime 内部细节，而是进入回放和报告体系

## 5. Required Regression Matrix

### 5.1 Pass Regressions

- [ ] verifier 全部通过时，既有 happy path 结果不回归
- [ ] verifier 全部通过时，clarification/intervention 流程不回归

### 5.2 Verification Failure Regressions

- [ ] task-level `needs_replan` 不会错误写入 `verified_facts`
- [ ] task-level `needs_replan` 不会停留在伪完成状态
- [ ] workflow-final `needs_replan` 不会错误进入 `DONE`
- [ ] `hard_fail` 不会继续静默完成 run
- [ ] verification retry 超限后不会死循环

### 5.3 Report Regressions

- [ ] verifier 结果能稳定写入 report
- [ ] 没有 verifier 结果时 report 不应崩溃

## 6. Minimum Test Suite Recommendation

建议至少形成以下测试规模：

- verifier contract / registry 单元测试：8-10 条
- built-in verifier 单元测试：10-14 条
- runtime integration 测试：8-12 条
- workflow regression 测试：6-8 条
- eval/report regression 测试：4-6 条

总量建议：

- [ ] 至少 `36-50` 条测试覆盖

## 6.1 Recommended Test Task Breakdown

建议测试同学按下面 5 个任务包拆分实施。

### Task Pack 1：Verification Contract / Registry 单元测试

目标：

- 先把 verifier contract 和 registry 固化成稳定测试对象

建议覆盖：

- `VerificationVerdict`
- `VerificationContext`
- `VerificationResult / VerificationReport`
- task/workflow/artifact verifier resolver
- fallback / no-op verifier

建议落点：

- 新增 `backend/tests/test_verification_registry.py`
- 或拆分为：
  - `backend/tests/test_verification_contract.py`
  - `backend/tests/test_verification_registry.py`

前置依赖：

- backend 先完成 `verification/base.py`
- backend 先完成 `verification/registry.py`

完成标准：

- 不依赖 runtime，也能独立验证 contract 和 resolver

### Task Pack 2：Built-In Verifier 单元测试

目标：

- 独立验证 meeting / contacts / hr / workflow / artifact verifier 的判断逻辑

建议覆盖：

- 合法结果 -> `passed`
- 缺关键字段 / facts 不一致 -> `needs_replan`
- 明确不可恢复错误 -> `hard_fail`

建议落点：

- 新增 `backend/tests/test_verifiers_domains.py`
- 新增 `backend/tests/test_verifiers_workflow.py`
- 新增 `backend/tests/test_verifiers_artifacts.py`

前置依赖：

- backend 先完成内建 verifiers

完成标准：

- verifier 逻辑不依赖 graph，就能直接测出 verdict 和 findings

### Task Pack 3：Runtime Gate 集成测试

目标：

- 证明 verifier 真正进入 executor / planner 的提交流程

建议覆盖：

- task-level `passed / needs_replan / hard_fail`
- workflow-final `passed / needs_replan / hard_fail`
- retry count 增长
- `verification_feedback` 写入和消费

建议落点：

- 新增 `backend/tests/test_verification_runtime.py`

可复用基础：

- `backend/tests/test_multi_agent_graph.py`
- `backend/tests/test_executor_outcome.py`

前置依赖：

- backend 先完成 executor / planner runtime integration
- backend 先完成 `ThreadState / TaskStatus` verification 字段

完成标准：

- 能直接证明 verifier gate 改变了 runtime 的提交行为

### Task Pack 4：Workflow Regression 回归测试

目标：

- 验证 verifier 接入后，既有 workflow 主链路不回归

建议覆盖：

- meeting happy path
- contacts happy path
- hr happy path
- cross-domain happy path
- clarification
- intervention

建议落点：

- 优先补在现有 `backend/tests/test_multi_agent_graph.py`
- 如用例过多，可拆新文件 `backend/tests/test_verification_workflow_regression.py`

前置依赖：

- Task Pack 3 至少完成基本 gate 集成

完成标准：

- verifier 全部通过时，现有 workflow 主链路行为与预期一致

### Task Pack 5：Evals / Report 回归测试

目标：

- 验证 verification 结果能进入 phase0 evals、json report、markdown report

建议覆盖：

- `CaseRunResult` 新字段
- markdown/json report 输出
- phase0 benchmark 接入 verifier 后仍能运行
- verifier 失败时报告内容完整

建议落点：

- 优先补在现有 `backend/tests/test_evals.py`

前置依赖：

- backend 先完成 `evals/schema.py`
- backend 先完成 `evals/runner.py`
- backend 先完成 `evals/report.py`

完成标准：

- verification 结果对测试、回放、报告都是可见的

## 6.2 Recommended Test Order

推荐顺序：

1. Task Pack 1
2. Task Pack 2
3. Task Pack 3
4. Task Pack 4
5. Task Pack 5

原因：

- 先锁 contract 和 verifier 逻辑，能减少 runtime 集成时的歧义
- runtime gate 稳定后，再做 workflow regression 和 report regression 更省成本

## 7. CI Strategy

### 7.1 Must-Have Suites

- [ ] `verification-unit`
  - contract + registry + built-in verifiers
- [ ] `verification-runtime`
  - executor/planner integration
- [ ] `verification-workflow`
  - workflow regression
- [ ] `verification-evals`
  - eval/report regression

### 7.2 Merge Gate Requirement

至少满足：

- verifier 单元测试通过
- runtime verification 集成测试通过
- 既有 workflow 关键图级测试通过
- eval/report regression 通过

## 8. Test Exit Criteria

- [ ] verifier、runtime gate、workflow regression、eval/report 四层都有自动化覆盖
- [ ] `passed / needs_replan / hard_fail` 三类 verdict 都有明确断言
- [ ] workflow 关键主链路回归通过
- [ ] verification retry budget 有明确断言
- [ ] report 中 verifier 结果可观察、可验证

## 9. Done Definition

Phase 4 测试完成，不是指“测到几个 verifier 文件”，而是指：

- verifier contract 被独立验证
- verifier registry / resolver 被独立验证
- task-level 和 workflow-final runtime gate 被独立验证
- workflow 主链路在 verifier 接入后未回归
- eval/report 能真实呈现 verification 结果

只有做到这五点，测试侧才算真正完成 Verification Harness Phase 4。

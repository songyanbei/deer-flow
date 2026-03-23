# Feature: Workflow Harness Core Milestone 1 Acceptance

- Status: `draft`
- Milestone scope: `Phase 0 + Phase 1 + Phase 4`
- Owner suggestion: `backend` + `test` + `product/owner`
- Frontend impact: `no new frontend delivery required, but existing UI/runtime projections must not regress`

## 1. Milestone Definition

当前里程碑对应 [harness_engineering改造实施方案.md](../../docs/harness_engineering改造实施方案.md) 中的第一阶段能力闭环：

- `Phase 0：基线盘点与度量体系`
- `Phase 1：Engine Registry 化`
- `Phase 4：Verification Harness`

如果这三个阶段都已经完成开发并分别通过单独测试，那么当前里程碑不应只停留在“各 Phase 自测通过”，而应该做一次**系统性验收**，确认：

1. 新增能力是否真的形成闭环
2. Phase 之间是否已经正确集成
3. 历史 workflow 功能是否被破坏
4. 当前里程碑是否已经达到“可进入下一阶段开发”的质量线

一句话定义：

> 当前里程碑的验收目标，是确认 DeerFlow 已从“能跑 workflow 的多智能体系统”，升级为“有 baseline、有 engine registry、有 runtime verification gate 的 Harness Core”。

## 2. Acceptance Objectives

本次验收需要同时回答四类问题：

### A. 新增功能是否真的完成

- Phase 0 的 baseline / report / deterministic replay 是否真的可用
- Phase 1 的 engine registry / builders / build-time hooks 是否真的接管构建链路
- Phase 4 的 verifier registry / runtime gate / eval/report integration 是否真的进入主链路

### B. Phase 之间是否真正集成

- baseline 是否能感知 verification 结果
- workflow runtime 是否真的使用 engine registry 和 verifier
- state / report / metrics 是否形成统一口径

### C. 历史功能是否回归

- workflow 主链路是否仍然稳定
- clarification / intervention / help_request / resume 是否仍然正确
- 共享 runtime / agent build 改造是否误伤 `leader` 或 custom agent 直聊能力

### D. 是否达到里程碑出口标准

- 是否具备继续进入 Phase 2 / 3 / 5 的前提
- 是否还有 blocker 级问题
- 是否存在必须在下一阶段前补齐的 debt

## 3. Acceptance Scope

### 3.1 In Scope

本次验收必须覆盖：

1. `workflow` 模式主链路
2. `meeting-agent`
3. `contacts-agent`
4. `hr-agent`
5. 这三者之间的 cross-domain workflow
6. `Phase 0 + Phase 1 + Phase 4` 的新增能力
7. 受这些改动影响的历史共享能力

### 3.2 Out Of Scope

本次验收不要求作为主门禁覆盖：

1. 真实 MCP / 真实外部系统联调
2. 前端新增页面或 operator console
3. 并行 scheduler
4. Knowledge Harness / Governance Harness / Improvement Harness 未落地部分
5. 全量 `leader` 模式深测

说明：

- `leader` 模式虽然不在本里程碑主目标内，但因为 Phase 1 修改了共享 agent build 路径，本次验收仍要求有**共享能力级 smoke review**

## 4. Acceptance Inputs

验收前需要准备以下输入材料：

1. Phase 0 文档与测试结果
   - [workflow-phase0-baseline-and-metrics.md](./workflow-phase0-baseline-and-metrics.md)
2. Phase 1 文档与测试结果
   - [workflow-engine-registry-phase1.md](./workflow-engine-registry-phase1.md)
3. Phase 4 文档与测试结果
   - [workflow-verification-harness-phase4.md](./workflow-verification-harness-phase4.md)
4. 当前 milestone 对应的测试报告 / CI 结果
5. 当前 benchmark 报告（json + markdown）
6. 已知问题列表（如果有）

## 5. Acceptance Method

本次验收采用四层方法：

1. **文档验收**
   - 确认实现结果与需求文档一致
2. **自动化验收**
   - 以已有 automated tests / benchmark / report 作为主证据
3. **集成验收**
   - 关注跨 Phase 的真实集成结果，而不是各模块孤立通过
4. **历史功能回归 review**
   - 针对受影响的历史能力逐项检查是否有破坏

## 6. Milestone Acceptance Checklist

## 6.1 Phase 0 Acceptance

### A. Baseline Infrastructure

- [ ] `phase0-core` 可通过统一入口稳定运行
- [ ] baseline case schema / loader / runner / assertion engine / report generator 已形成稳定链路
- [ ] benchmark runner 跑在真实 workflow graph 上，而不是假图
- [ ] baseline 主路径使用 deterministic fixtures，而不是依赖真实 MCP / 真实 LLM

### B. Coverage Scope

- [ ] baseline 覆盖 `meeting-agent`
- [ ] baseline 覆盖 `contacts-agent`
- [ ] baseline 覆盖 `hr-agent`
- [ ] baseline 覆盖三者之间的 cross-domain workflow
- [ ] baseline 中已包含 clarification / intervention / resume 场景

### C. Report / Metrics

- [ ] 每次运行都能生成 `json + markdown` 报告
- [ ] 报告至少包含 case 结果、失败分类、duration、task_count、route_count、LLM metrics
- [ ] report 结果可被 CI artifact 或本地留存

### D. Deterministic Baseline Quality

- [ ] 同一 suite 在本地重复运行时结果稳定
- [ ] CI 中 deterministic baseline 可以稳定通过
- [ ] benchmark 输出可用于后续版本对比

## 6.2 Phase 1 Acceptance

### A. Engine Registry Core

- [ ] `engine_type` 已成为正式配置项
- [ ] canonical / alias / fallback 策略与文档一致
- [ ] runtime 已通过 registry 解析 engine
- [ ] 四类 engine 已正式注册：
  - `default`
  - `react`
  - `read_only_explorer`
  - `sop`

### B. Builder / Runtime Integration

- [ ] `make_lead_agent()` 已通过 builder 构建 agent
- [ ] 不再依赖散落的 engine if/else
- [ ] `read_only_explorer` 的只读过滤正确生效
- [ ] prompt `engine_mode` 由 builder 统一提供

### C. Agent CRUD / Config

- [ ] Agent CRUD 已支持 `engine_type`
- [ ] create / get / update / list 行为一致
- [ ] alias 输入可 canonical 化持久化
- [ ] 未知值有 warning + safe fallback

### D. Build-Time Hook Addendum

- [ ] `before_agent_build`
- [ ] `after_agent_build`
- [ ] `before_skill_resolve`
- [ ] `before_mcp_bind`

以上 4 个 build-time hooks 已具备：

- [ ] 显式 contract
- [ ] 默认 no-op 行为
- [ ] 稳定调用顺序
- [ ] mutation 能力边界清晰

## 6.3 Phase 4 Acceptance

### A. Verification Contract / Registry

- [ ] verifier registry 已成为唯一可信入口
- [ ] verifier contract 已统一：
  - scope
  - verdict
  - report
  - feedback
- [ ] task verifier / workflow verifier / artifact validator 可被统一解析

### B. Built-In Verifiers

- [ ] `meeting` task verifier 已可运行
- [ ] `contacts` task verifier 已可运行
- [ ] `hr` task verifier 已可运行
- [ ] workflow-final verifier 已可运行
- [ ] generic artifact validator 已可运行

### C. Runtime Verification Gate

- [ ] task result 在写入 `verified_facts` 前必须经过 verifier
- [ ] workflow final summary 在进入 `DONE` 前必须经过 verifier
- [ ] `passed / needs_replan / hard_fail` 三类 verdict 语义与文档一致
- [ ] verification retry budget 已生效
- [ ] `verification_feedback` 已采用结构化 remediation contract

### D. Evals / Reports Integration

- [ ] verifier 结果已进入 `CaseRunResult`
- [ ] verifier 结果已进入 json / markdown report
- [ ] phase0 benchmark 接入 verifier 后仍能运行

## 6.4 Cross-Phase Integration Acceptance

这是本次里程碑最关键的一层，必须单独检查。

### A. Phase 0 × Phase 1

- [ ] baseline runner 能跑带 engine registry 的真实 workflow runtime
- [ ] benchmark / regression 不因 engine registry 接入而失真
- [ ] 不同 engine 配置的 agent 可被 baseline 正常评估

### B. Phase 0 × Phase 4

- [ ] baseline/report 已能显示 verifier 结果
- [ ] deterministic suite 在引入 verifier 后仍稳定
- [ ] benchmark 结果同时反映“传统断言 + verifier 结论”

### C. Phase 1 × Phase 4

- [ ] workflow domain agent 通过 engine registry 构建后，verification gate 仍能正确接入
- [ ] build-time hooks 与 verifier contract 不冲突
- [ ] shared runtime build path 不因 verifier 接入而被绕过

### D. End-to-End Milestone Goal

- [ ] 当前系统已形成：
  - baseline
  - engine registry
  - runtime verification gate
- [ ] 三者已能在同一条 workflow 主链路上共同工作

## 6.5 Historical Function Regression Review

这部分不是“新功能验收”，而是专门 review 是否误伤历史功能。

### A. Workflow Core Regression

- [ ] `planner -> router -> executor` 主链路仍正常
- [ ] `task_pool` reducer 没有被新字段破坏
- [ ] `verified_facts` reducer 没有被新 gate 破坏
- [ ] `workflow_stage` / `execution_state` 仍能稳定推进
- [ ] `SYSTEM_FINISH` / `SYSTEM_FALLBACK` 语义不回归

### B. Clarification Regression

- [ ] clarification 触发逻辑不回归
- [ ] clarification resume 不回归
- [ ] clarification 后 workflow run_id / task continuity 不回归

### C. Intervention Regression

- [ ] intervention before-tool 拦截不回归
- [ ] intervention resolve 后 resume 不回归
- [ ] intervention cache / decision reuse 不回归
- [ ] task 不会出现 WAITING_INTERVENTION 与 DONE 的冲突态

### D. Help Request / Cross-Domain Regression

- [ ] `request_help` helper task 机制不回归
- [ ] helper resume / dependency write-back 不回归
- [ ] cross-domain facts 复用不回归

### E. Shared Agent Build Regression

- [ ] workflow domain agent 的 agent build 不回归
- [ ] custom agent 构建不回归
- [ ] shared `make_lead_agent()` 路径不回归
- [ ] build-time hooks 默认 no-op 时不改变历史行为

### F. Leader Smoke Review

虽然不作为 Phase 4 主范围，但由于 Phase 1 修改了共享构建链路，本次仍需要最小 smoke：

- [ ] `leader` 模式仍可基本启动
- [ ] shared agent build path 不导致 `leader` 模式异常
- [ ] 未配置 `engine_type` 的老 agent / 默认路径不崩溃

### G. Existing Report / Metrics Regression

- [ ] 原有 eval/report 不因新增 verification 字段崩溃
- [ ] 原有 observability / metrics 不因新增字段失真

## 6.6 Non-Functional Acceptance

### A. Stability

- [ ] benchmark / test 在本地可重复通过
- [ ] milestone 主门禁在 CI 可稳定通过
- [ ] verification retry 不会出现死循环

### B. Diagnosability

- [ ] verification 失败可从 report / state / logs 定位
- [ ] engine fallback / verifier fallback 可被日志观察
- [ ] benchmark 失败可区分：
  - case assertion fail
  - verification fail
  - runtime error

### C. Maintainability

- [ ] 新增字段 / contract 已进入文档
- [ ] 没有把关键规则埋成散落分支
- [ ] 当前实现与后续 Hook Harness / Governance / Knowledge 方向不冲突

## 7. Milestone Exit Criteria

只有满足以下条件，当前里程碑才算验收通过：

### Must Have

- [ ] Phase 0 / Phase 1 / Phase 4 各自功能验收通过
- [ ] Cross-phase integration 验收通过
- [ ] 历史 workflow 核心功能无 blocker 级回归
- [ ] 所有 blocker / critical 问题已关闭或明确降级处理
- [ ] milestone 相关自动化测试与 benchmark 报告齐全

### Should Have

- [ ] `leader` shared-runtime smoke 通过
- [ ] report / metrics / logs 具备定位问题的最小可用性
- [ ] 对下一阶段已形成清晰 debt 列表

### Fail Conditions

任一项成立，则当前里程碑不应判定通过：

- [ ] verifier 仍只是离线工具，未真正进入 runtime gate
- [ ] baseline 不能稳定运行
- [ ] engine registry 实际未接管主构建链路
- [ ] clarification / intervention / help_request 出现明显回归
- [ ] verification retry 出现死循环或状态机紊乱

## 8. Acceptance Outputs

验收结束后，建议至少形成以下输出：

1. 一份 milestone 验收结论
   - `pass / pass with known issues / fail`
2. 一份 blocker / known issues 列表
3. 一份 benchmark / report 归档
4. 一份进入下一阶段前的技术 debt 清单

## 9. Recommended Review Roles

建议至少由以下角色共同 review：

1. `backend`
   - 关注 runtime、state、registry、verifier、report
2. `test`
   - 关注覆盖范围、回归、证据完整性、失败分类
3. `product/owner`
   - 关注里程碑是否达到“可继续进入下一阶段”的目标

## 10. Final Acceptance Conclusion Template

可直接用以下模板收口：

```md
结论：PASS / PASS WITH KNOWN ISSUES / FAIL

本次验收覆盖：
- Phase 0
- Phase 1
- Phase 4
- 历史 workflow regression review

通过项：
- ...

未通过项：
- ...

已知问题：
- ...

是否允许进入下一阶段：
- 是 / 否
```

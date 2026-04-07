# Multi-Tenant Absolute Isolation Backend Checklist

- Related feature:
  [multi-tenant-absolute-isolation-analysis.md](/E:/work/deer-flow/collaboration/features/multi-tenant-absolute-isolation-analysis.md)
- Status: `in_progress（A/B/C/D/E/F/G 基本完成，剩余少量收尾项）`
- Owner: `backend`
 
## 复核更新（2026-04-03）

本轮已确认以下实现项完成并通过代码复核或定向行为验证：

- [x] `ThreadRegistry.check_access()` 已执行 tenant + user 双校验。
- [x] artifacts / uploads / interventions / skills.install 已统一传入 `user_id` 做 thread ownership 校验。
- [x] `ThreadDataMiddleware` 已登记 `user_id`，不再只登记 tenant。
- [x] `MemoryMiddleware` 已在 OIDC 缺失 `user_id` 时跳过写入，且 fallback 不再因非 runnable 上下文提前抛异常。
- [x] Embedded Client 已透传 `tenant_id` / `user_id`，并将其纳入 agent cache key 与 tools / prompt 构建。
- [x] skill enable/disable 已写入 tenant overlay，不再落全局 `extensions_config.json`。
- [x] observability 已补 tenant / user 维度传递。

以下测试性收口已补齐（2026-04-07）：

- [x] 为 Embedded Client 增加”tenant/user 切换触发 rebuild”的显式回归测试（`TestTenantUserCacheRebuild`，3 tests）。
- [x] 为 MemoryMiddleware 增加”OIDC 开启且缺 `user_id`/`tenant_id` 时跳过写入且不抛异常”的显式回归测试（`TestMemoryMiddlewareOidcSkip`，4 tests）。

## 当前实现状态映射（2026-04-07）

以下状态以主需求文档当前目标模型和代码复核结果为准，用来覆盖本清单下方仍保留的原始 `draft` 任务明细。

- Task Pack A `身份传播链修复`：`完成`
  - `runtime_service.py` 已将 `thread_id` / `tenant_id` / `user_id` 注入 `configurable`，�� Agent 构建阶段可以读取正确身份��
  - `ThreadDataMiddleware` / `MemoryMiddleware` 的防御性补强已落地：异常类型收窄、OIDC tenant/user 缺失警告。
  - 主 Agent → 子智能体 → Embedded Client 的 tenant/user 传播链已打���。
  - Embedded Client 在 OIDC 启用时，缺 `tenant_id`/`user_id` 输出 warning 日志（防御性警告，不做 hard error 以保持向后兼容）。

- Task Pack B `Thread 归属与同租户跨用户越权修复`：`完成`
  - `ThreadRegistry.check_access()` 已支持 tenant + user 双校验。
  - artifacts / uploads / interventions / runtime / skills.install 等 thread 相关路径已统一走 owner 校验。
  - 未注册 thread 默认拒绝访问。

- Task Pack C `用户级运行时数据下沉`：`基本完成`
  - memory、agent memory、`USER.md`、governance ledger 的 user 维度能力已进入实现。
  - 路径工具、governance 数据模型和 query/record user 维度已补齐。
  - 新增 `migrate_tenant_memory_to_user_level()` 迁移辅助函数，支持一次性迁移。
  - 剩余差距：兼容窗口结束后旧路径禁写策略，仍应继续作为文档/实现待补项保留。

- Task Pack D `租户级共享资源隔离`：`基本完成`
  - skills 已支持 tenant 目录加载与 tenant 安装路径。
  - `extensions_config.json` 已形成“平台基线 + tenant overlay”模型。
  - MCP runtime scope、cache、tools 链路都已补 tenant 维度。
  - policy registry 已按 tenant 分桶。
  - 剩余差距：若后续要把 policy 持久化或进一步固化装载时机，仍可继续细化，但当前隔离主目标已基本达成。

- Task Pack E `RBAC 执行层`：`基本完成`
  - `oidc.py` / `dependencies.py` 已支持 `role` 提取、`get_role()`、`require_role()`。
  - skills、mcp、agents、governance 等关键管理接口已接入 `admin/owner` 写权限约束。
  - 缺失 `role` 时按最低权限 `member` 处理。
  - 剩余差距：若要把“所有共享资源管理接口”完全做成矩阵化核对，仍建议继续补专项回归。

- Task Pack F `fallback / 错误语义 / 可观测性`：`基本完成`
  - 关键身份链路上的 silent fallback 已明显收敛；OIDC 开启时缺 `tenant_id` / `user_id` 会走 `401`。
  - observability 的 tenant / user 维度已补到 decision log、metrics、ledger、audit hooks、intervention middleware。
  - 中间件异常类型已收窄（`except Exception` → `except (ImportError, RuntimeError)` / `except (ValueError, OSError)`）。
  - OIDC 启用时 MemoryMiddleware 会额外检查 tenant_id == “default” 并跳过写入。
  - 剩余差距：开发模式下保留的 `default` / `anonymous` fallback 仍是有意设计，不应和生产 OIDC 语义混淆。

- Task Pack G `生命周期支持`：`基本完成`
  - ThreadRegistry 扩展：`list_threads_by_user()`、`delete_threads_by_user()`、`delete_threads_by_tenant()`、`list_expired_threads()`。
  - MemoryQueue 扩展：`cancel_by_user()`、`cancel_by_tenant()`。
  - GovernanceLedger 扩展：`archive_by_user()`、`purge_by_tenant()`。
  - MCP 清理：`invalidate_tenant()`（cache）、`unload_tenant_scopes()`（runtime）。
  - LifecycleManager 编排模块：`delete_user()`、`decommission_tenant()`、`cleanup_expired_threads()`。
  - Admin API router（`/api/admin`）：`DELETE /users/{user_id}`、`DELETE /tenants/{tenant_id}`、`POST /cleanup/expired-threads`，均要求 `admin`/`owner` 角色。
  - 剩余差距：失败补偿（事务回滚）策略仍为 best-effort，后续可考虑补偿日志或分布式事务。

建议使用方式：

- 将本节作为“当前落地状态”读取。
- 将下方原始复选框继续作为“剩余开发明细和补强清单”使用。

## Scope

本清单只面向后端开发同学，覆盖：

- 身份传播链修复
- 线程与用户归属校验统一
- 用户级运行时数据下沉
- 租户级共享资源隔离
- RBAC 执行层
- fallback / 错误语义 / 可观测性收敛
- 生命周期支持与回归门禁

## 0. 实施前必须先对齐的约束

- [ ] 启用 OIDC 时，`tenant_id`、`user_id` 缺失直接拒绝，不允许降级为 `"default"` / `"anonymous"`
- [ ] DeerFlow 只消费上层下发的 `role` claim，不在本期内建设角色管理体系
- [ ] Agents / SOUL 继续保持 tenant 级共享，不下沉到 user 级
- [ ] Memory / Agent Memory / USER.md / Governance Ledger 必须下沉到 `tenants/{tid}/users/{uid}/`
- [ ] Skills / MCP / Extensions / Policy 必须从“系统级共享”收敛为“平台基线 + 租户覆盖”或“租户私有写入”
- [ ] 关键错误语义固定为：身份缺失 `401`，越权 `403`，资源不存在 `404`

## 1. 实现边界

- [ ] 只修改 `backend/` 下代码和本协作文档
- [ ] 不修改前端文件
- [ ] 不在本期把配置全面迁移到数据库
- [ ] 不在本期建设角色绑定 CRUD / 租户成员管理
- [ ] 不把 LocalSandbox 替换成全新执行基础设施
- [ ] 不把同租户共享资源误改成 user 私有资源

Done when:

- 后端改动完成后，测试同学可直接按测试清单复现跨租户、同租户跨用户、断流、RBAC、生命周期与性能回归。

## 2. Task Pack A: 身份传播链修复

### A1. Gateway -> RunnableConfig

- [ ] 更新 [backend/src/gateway/runtime_service.py](/E:/work/deer-flow/backend/src/gateway/runtime_service.py)
- [ ] 更新相关 runtime 入口路由，确保构造运行配置时同时写入：
  - [ ] `context.thread_id`
  - [ ] `context.tenant_id`
  - [ ] `context.user_id`
  - [ ] `configurable.thread_id`
  - [ ] `configurable.tenant_id`
  - [ ] `configurable.user_id`
- [ ] 确认主 Agent 构建阶段不再依赖 `"default"` tenant

### A2. Middleware 防御性补强

- [ ] 更新 [backend/src/agents/middlewares/thread_data_middleware.py](/E:/work/deer-flow/backend/src/agents/middlewares/thread_data_middleware.py)
- [ ] 更新 [backend/src/agents/middlewares/memory_middleware.py](/E:/work/deer-flow/backend/src/agents/middlewares/memory_middleware.py)
- [ ] middleware 仅做校验和补强，不再承担首次注入职责
- [ ] 缺失 `tenant_id` / `user_id` 时拒绝执行或拒绝写入，不再静默 fallback

### A3. 主 Agent -> 子智能体传播

- [ ] 更新 [backend/src/tools/builtins/task_tool.py](/E:/work/deer-flow/backend/src/tools/builtins/task_tool.py)
- [ ] 更新 [backend/src/subagents/executor.py](/E:/work/deer-flow/backend/src/subagents/executor.py)
- [ ] 子智能体 `RunnableConfig["configurable"]` 必须显式携带 `tenant_id` + `user_id`
- [ ] 子智能体内部不允许再次降级到 `"default"`

### A4. Embedded Client

- [ ] 更新 [backend/src/client.py](/E:/work/deer-flow/backend/src/client.py)
- [ ] `stream()` / `chat()` / `_get_runnable_config()` 接受并透传 `tenant_id`、`user_id`
- [ ] 调用方如果只传 `thread_id`，返回清晰错误而不是继续执行

Done when:

- 所有执行路径在 Agent 构建前即可拿到正确身份，且任何断流都会被显式拒绝。

## 3. Task Pack B: Thread 归属与同租户跨用户越权修复

### B1. 统一线程归属校验

- [ ] 更新 [backend/src/gateway/thread_registry.py](/E:/work/deer-flow/backend/src/gateway/thread_registry.py)
- [ ] 统一形成 tenant + user 双校验能力
- [ ] 未注册 thread 不再默认放行
- [ ] 兼容历史 registry 记录时，明确缺失 `user_id` 的处理策略

### B2. 路由层对齐

- [ ] 更新 [backend/src/gateway/routers/runtime.py](/E:/work/deer-flow/backend/src/gateway/routers/runtime.py)
- [ ] 更新 [backend/src/gateway/routers/artifacts.py](/E:/work/deer-flow/backend/src/gateway/routers/artifacts.py)
- [ ] 更新 [backend/src/gateway/routers/uploads.py](/E:/work/deer-flow/backend/src/gateway/routers/uploads.py)
- [ ] 更新 [backend/src/gateway/routers/interventions.py](/E:/work/deer-flow/backend/src/gateway/routers/interventions.py)
- [ ] 所有 thread 相关端点统一走同一套 owner 校验
- [ ] 确保“查看、上传、列出、删除、resolve”都不能跨用户操作

Done when:

- 同租户不同用户无法互访对方 thread 资源，也无法代对方执行治理或干预类操作。

## 4. Task Pack C: 用户级运行时数据下沉

### C1. 路径模型

- [ ] 更新 [backend/src/config/paths.py](/E:/work/deer-flow/backend/src/config/paths.py)
- [ ] 新增 user 级路径工具函数：
  - [ ] 用户全局 memory
  - [ ] 用户 × Agent memory
  - [ ] USER.md
  - [ ] governance ledger

### C2. Memory / Profile / Governance

- [ ] 更新 [backend/src/agents/memory/updater.py](/E:/work/deer-flow/backend/src/agents/memory/updater.py)
- [ ] 更新 [backend/src/agents/memory/queue.py](/E:/work/deer-flow/backend/src/agents/memory/queue.py)
- [ ] 更新 [backend/src/gateway/routers/agents.py](/E:/work/deer-flow/backend/src/gateway/routers/agents.py)
- [ ] 更新 [backend/src/agents/governance/types.py](/E:/work/deer-flow/backend/src/agents/governance/types.py)
- [ ] 更新 [backend/src/agents/governance/ledger.py](/E:/work/deer-flow/backend/src/agents/governance/ledger.py)
- [ ] 更新 [backend/src/gateway/routers/governance.py](/E:/work/deer-flow/backend/src/gateway/routers/governance.py)
- [ ] Governance 数据模型补 `user_id`
- [ ] Governance query / record / resolve 都支持 user 维度

### C3. 迁移策略

- [ ] 在主文档中明确历史 tenant 级 memory / USER / governance 的迁移方案
- [ ] 实现层明确：
  - [ ] 是否一次性迁移
  - [ ] 是否短期双读兼容
  - [ ] 兼容窗口结束后旧路径是否禁止写入

Done when:

- 所有用户私有运行时数据都只读写 user 级目录，且治理记录具备 user 维度。

## 5. Task Pack D: 租户级共享资源隔离

### D1. Skills

- [ ] 更新 [backend/src/skills/loader.py](/E:/work/deer-flow/backend/src/skills/loader.py)
- [ ] 更新 [backend/src/gateway/routers/skills.py](/E:/work/deer-flow/backend/src/gateway/routers/skills.py)
- [ ] 平台内置 skills 继续只读加载
- [ ] 租户自定义 skills 只从 `tenants/{tenant_id}/skills/` 加载和安装
- [ ] 技能安装端点必须绑定 tenant 写入路径

### D2. MCP / Extensions

- [ ] 更新 [backend/src/config/extensions_config.py](/E:/work/deer-flow/backend/src/config/extensions_config.py)
- [ ] 更新 [backend/src/gateway/routers/mcp.py](/E:/work/deer-flow/backend/src/gateway/routers/mcp.py)
- [ ] 更新 [backend/src/mcp/runtime_manager.py](/E:/work/deer-flow/backend/src/mcp/runtime_manager.py)
- [ ] 更新 [backend/src/mcp/tools.py](/E:/work/deer-flow/backend/src/mcp/tools.py)
- [ ] 更新 [backend/src/mcp/cache.py](/E:/work/deer-flow/backend/src/mcp/cache.py)
- [ ] 更新 [backend/src/tools/tools.py](/E:/work/deer-flow/backend/src/tools/tools.py)
- [ ] `extensions_config.json` 改为“全局基线 + 租户覆盖层”
- [ ] MCP scope key、runtime cache、tool cache 都必须带 tenant
- [ ] 主 Agent 和 Domain Agent 两条 MCP 链路都不能再使用全局共享 key

### D3. Policy

- [ ] 更新 [backend/src/agents/governance/policy.py](/E:/work/deer-flow/backend/src/agents/governance/policy.py)
- [ ] 明确 policy registry 的 tenant 分桶模型
- [ ] 明确 policy 的存储路径和装载时机

Done when:

- 租户自定义共享资源只影响本租户，平台内置共享资源保持只读全局基线。

## 6. Task Pack E: RBAC 执行层

### E1. 角色提取与依赖

- [ ] 更新 [backend/src/gateway/middleware/oidc.py](/E:/work/deer-flow/backend/src/gateway/middleware/oidc.py)
- [ ] 更新 [backend/src/gateway/dependencies.py](/E:/work/deer-flow/backend/src/gateway/dependencies.py)
- [ ] 从 claim 提取 `role`
- [ ] 新增 `get_role()` / `require_role()`
- [ ] `role` 缺失时按最低权限处理，并有清晰日志

### E2. 管理接口接入

- [ ] 为以下共享资源管理端点接入最小权限校验：
  - [ ] agents / SOUL 写操作
  - [ ] skills 安装 / 卸载 / 启停
  - [ ] mcp config 写操作
  - [ ] extensions config 写操作
  - [ ] policy 管理
  - [ ] governance 全租户查看 / 代审批
- [ ] 明确 `member` 与 `admin/owner` 的分界

Done when:

- DeerFlow 能稳定拒绝对 tenant 共享资源的越权写操作。

## 7. Task Pack F: fallback、错误语义与可观测性

### F1. fallback 收敛

- [ ] 清理所有关键路径中的 `"default"` / `"anonymous"` 降级
- [ ] 主文档第十章列出的 fallback 点逐项对照实现
- [ ] 缺失身份、缺失 owner、缺失路径、缺失 claim 都返回明确错误

### F2. 指标与日志

- [ ] 更新 [backend/src/observability/metrics.py](/E:/work/deer-flow/backend/src/observability/metrics.py)
- [ ] 更新 [backend/src/observability/decision_log.py](/E:/work/deer-flow/backend/src/observability/decision_log.py)
- [ ] 更新 [backend/src/observability/setup.py](/E:/work/deer-flow/backend/src/observability/setup.py)
- [ ] 指标与日志补 tenant / user 维度
- [ ] 错误日志能区分跨租户越权、同租户跨用户越权、身份断流、迁移失败

Done when:

- 所有关键失败路径均可观测、可区分、可定位。

## 8. Task Pack G: 生命周期支持

- [ ] 根据主文档第十一章补齐用户删除流程需要的底层能力
- [ ] 根据主文档第十一章补齐租户注销流程需要的底层能力
- [ ] 明确 thread TTL 清理时是否需要同步清理 user 级目录和 registry
- [ ] 明确治理记录、memory、USER.md 的删除顺序与失败补偿策略

Done when:

- 测试可按用户删除、租户注销、TTL 清理三个场景稳定验证数据生命周期行为。

## 9. 推荐实施顺序

- [ ] 先完成 Task Pack A，再做 B
- [ ] B 完成后再做 C
- [ ] D 与 E 可以并行，但都依赖 A 的身份链稳定
- [ ] F 在每个任务包落地时同步收口，不放到最后一次性修
- [ ] G 在 C、D、E 稳定后再补齐

## 10. 完成判定

- [ ] 主 Agent、子智能体、Embedded Client 三条主执行链均完成身份传播修复
- [ ] thread 相关接口全部完成 tenant + user 双校验
- [ ] user 私有运行时数据全部下沉到 user 级目录
- [ ] skills / mcp / extensions / policy 完成 tenant 作用域隔离
- [ ] 共享资源管理接口具备 RBAC 执行层
- [ ] fallback、日志、指标、错误响应全部对齐主文档约束
- [ ] 测试清单中的前置依赖项全部已满足

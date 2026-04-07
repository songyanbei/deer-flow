# Multi-Tenant Absolute Isolation Test Checklist

- Related feature:
  [multi-tenant-absolute-isolation-analysis.md](/E:/work/deer-flow/collaboration/features/multi-tenant-absolute-isolation-analysis.md)
- Related backend checklist:
  [multi-tenant-absolute-isolation-analysis-backend-checklist.md](/E:/work/deer-flow/collaboration/features/multi-tenant-absolute-isolation-analysis-backend-checklist.md)
- Status: `in_progress（关键回归已验证，剩余显式用例待补）`
- Owner: `test`

## 复核更新（2026-04-03）

本轮已确认以下测试事实：

- [x] 可观测性 tenant / user 维度覆盖已补到 `backend/tests/test_observability.py`，包括 decision log、metrics、governance ledger、engine、audit hooks、intervention middleware。
- [x] tenant 传递链路相关覆盖已补到 `backend/tests/test_tenant_propagation.py`。
- [x] Embedded Client 相关稳定子集已通过定向测试，且通过脚本验证确认 tenant/user 切换会触发 agent 重建。
- [x] `MemoryMiddleware` 已通过定向脚本验证：OIDC 开启且缺 `user_id` 时返回 `None` 且不会入队写入。

当前仍建议补两条显式回归用例，避免未来回退：

- [ ] 在 `backend/tests/test_client.py` 中增加“tenant/user 切换触发 Embedded Client rebuild”的显式断言。
- [ ] 在 `backend/tests/test_tenant_propagation.py` 或专用用例中增加“OIDC 开启且缺 `user_id` 时 MemoryMiddleware 跳过写入且不抛异常”的显式断言。

环境说明：

- [ ] 当前 Windows 环境下，部分依赖 `tempfile.TemporaryDirectory()` 的 broader suite 仍会被临时目录权限问题污染；该问题会影响大套件稳定性，但不改变本轮对修复本身的复核结论。

## Scope

本清单只面向测试同学，覆盖：

- 跨租户隔离验证
- 同租户跨用户隔离验证
- tenant_id / user_id 断流回归
- Prompt Injection / Memory Poisoning 渗透验证
- RBAC 权限执行验证
- 可观测性与错误语义验证
- 生命周期与兼容迁移验证
- 并发与性能回归

## A. 测试准备

### A1. 基础环境

- [ ] 准备 DeerFlow 后端分支环境
- [ ] 明确本次测试使用 OIDC 模式，而不是匿名本地模式
- [ ] 准备至少两个租户：
  - [ ] `tenant-a`
  - [ ] `tenant-b`
- [ ] 每个租户至少准备两个用户：
  - [ ] 普通用户 `member`
  - [ ] 管理用户 `admin` 或 `owner`

### A2. 测试身份与数据

- [ ] 准备带 `tenant_id`、`user_id`、`role` claim 的测试令牌
- [ ] 准备缺失 `tenant_id` 的令牌
- [ ] 准备缺失 `user_id` 的令牌
- [ ] 准备缺失 `role` 的令牌
- [ ] 准备历史 tenant 级 memory / USER / governance 测试数据

### A3. 基础测试资产

- [ ] 为 `tenant-a/user-1` 创建 thread、uploads、artifacts、governance 记录
- [ ] 为 `tenant-a/user-2` 创建独立 thread、uploads、artifacts、governance 记录
- [ ] 为 `tenant-b/user-3` 创建独立 thread 和 memory
- [ ] 准备至少一份租户自定义 skill
- [ ] 准备至少一份租户自定义 MCP / extensions 配置

## B. 身份传播链与断流回归

### B1. Gateway / 主 Agent

- [ ] 缺失 `tenant_id` 时，请求直接失败，返回 `401`
- [ ] 缺失 `user_id` 时，请求直接失败，返回 `401`
- [ ] 正常 token 下，主 Agent 构建时读取到的 tenant 不再是 `"default"`
- [ ] 不允许出现“接口成功，但实际写入 default 租户目录”的情况

### B2. 子智能体 / Embedded Client

- [ ] 触发子智能体执行时，子智能体也拿到正确的 `tenant_id` / `user_id`
- [ ] 使用 Embedded Client 调用时，若只传 `thread_id` 则直接失败
- [ ] Embedded Client 传完整身份时，运行结果落到正确租户/用户目录

Done when:

- 任意主链路或子链路身份缺失都会显式失败，不会静默降级执行。

## C. 跨租户隔离测试

### C1. Thread 资源

- [ ] `tenant-b` 用户不能读取 `tenant-a` 的 artifacts
- [ ] `tenant-b` 用户不能向 `tenant-a` 的 thread 上传文件
- [ ] `tenant-b` 用户不能列出或删除 `tenant-a` 的 uploads
- [ ] `tenant-b` 用户不能 resolve `tenant-a` 的 interventions

### C2. 运行时数据

- [ ] `tenant-b` 用户不能读到 `tenant-a` 的 memory / USER / governance
- [ ] `tenant-b` 执行不会把数据写入 `tenant-a` 的 user 目录
- [ ] metrics / logs 中可以明确看到租户维度，不会串租户

### C3. 共享资源

- [ ] `tenant-a` 安装的 skill 不会在 `tenant-b` 可见
- [ ] `tenant-a` 修改的 mcp / extensions / policy 不会影响 `tenant-b`

## D. 同租户跨用户隔离测试

### D1. Thread 越权

- [ ] `tenant-a/user-2` 不能读取 `tenant-a/user-1` 的 artifacts
- [ ] `tenant-a/user-2` 不能向 `tenant-a/user-1` 的 thread 上传文件
- [ ] `tenant-a/user-2` 不能列出或删除 `tenant-a/user-1` 的 uploads
- [ ] `tenant-a/user-2` 不能 resolve `tenant-a/user-1` 的 interventions

### D2. 用户私有运行时数据

- [ ] `tenant-a/user-2` 的执行不会读到 `tenant-a/user-1` 的 memory
- [ ] `tenant-a/user-2` 的执行不会读到 `tenant-a/user-1` 的 USER.md
- [ ] `tenant-a/user-2` 默认只能查看自己的 governance 记录
- [ ] 普通用户不能代其他用户审批 governance 记录

Done when:

- 同租户用户间只能共享 tenant 级定义资源，不能共享 user 私有运行时数据。

## E. 租户共享资源隔离与 RBAC 测试

### E1. 共享资源作用域

- [ ] tenant 级 Agents / SOUL 在同租户内可见
- [ ] tenant 级 Agents / SOUL 在跨租户不可见
- [ ] tenant 级 Skills / MCP / Extensions / Policy 在跨租户不可见
- [ ] 平台内置只读资源仍可被各租户读取

### E2. RBAC 权限执行

- [ ] `member` 可以读取共享资源，但不能执行写操作
- [ ] `admin/owner` 可以修改本租户共享资源
- [ ] `member` 不能安装 / 卸载 skill
- [ ] `member` 不能修改 mcp / extensions / policy
- [ ] `member` 不能修改 agent SOUL 或共享 agent 配置
- [ ] `admin` 可以查看全租户 governance，`member` 只能看自己的

### E3. 缺失 role claim

- [ ] 缺失 `role` 时按最低权限处理
- [ ] 缺失 `role` 时所有写类管理接口都返回 `403`

## F. Prompt Injection / Poisoning 渗透测试

### F1. Memory Poisoning

- [ ] 构造包含 `</memory>` 或类似标签闭合的输入
- [ ] 验证该输入只可能污染当前用户自己的 memory，不影响同租户其他用户
- [ ] 验证系统对注入内容的处理符合实现设计，不出现跨用户污染

### F2. SOUL / Skill / MCP

- [ ] 修改 tenant 级 SOUL 后，只影响本租户，不影响其他租户
- [ ] 恶意 skill 安装只影响当前租户，不影响其他租户
- [ ] MCP 工具输出中的恶意提示不会通过全局缓存污染其他租户

## G. 错误语义与可观测性测试

### G1. 错误码

- [ ] 身份缺失统一返回 `401`
- [ ] 越权统一返回 `403`
- [ ] 资源不存在统一返回 `404`
- [ ] 不允许出现 silent ignore 或 silent fallback

### G2. 日志与指标

- [ ] 关键错误日志能看到 `tenant_id`
- [ ] 关键错误日志能看到 `user_id`
- [ ] 指标能区分不同 tenant / user 的请求
- [ ] 能区分跨租户越权、同租户跨用户越权、身份断流、迁移失败

## H. 生命周期与迁移兼容测试

### H1. 历史数据兼容

- [ ] 历史 tenant 级 memory 数据按设计被迁移或兼容读取
- [ ] 历史 USER / governance 数据按设计被迁移或兼容读取
- [ ] 兼容窗口内外的读写行为与文档一致

### H2. 用户删除 / 租户注销 / TTL

- [ ] 用户删除后，用户级 memory / USER / governance / agents memory 被清理
- [ ] 租户注销后，租户级共享资源与用户级数据都按顺序清理
- [ ] thread TTL 触发后，相关 uploads / artifacts / registry 状态符合设计
- [ ] 清理失败时有可见补偿或告警

## I. 性能与并发回归

### I1. 并发访问

- [ ] 多租户并发运行时，不发生 cache 串租户
- [ ] 同租户多用户并发运行时，不发生 user 私有数据串写
- [ ] thread registry 并发操作无明显锁竞争异常

### I2. 性能基线

- [ ] 引入 tenant / user 分桶后，memory 加载性能在可接受范围
- [ ] MCP 连接数增长符合设计预期
- [ ] 日志与指标维度增加后，无明显异常放大

## J. 推荐测试用例落点

- [ ] 优先补或新增以下测试文件：
  - [ ] `backend/tests/test_multi_tenant.py`
  - [ ] `backend/tests/test_multi_tenant_completion.py`
  - [ ] `backend/tests/test_tenant_propagation.py`
  - [ ] `backend/tests/test_oidc_middleware.py`
  - [ ] `backend/tests/test_runtime_router.py`
  - [ ] `backend/tests/test_runtime_hooks.py`
  - [ ] `backend/tests/test_observability.py`
- [ ] 如现有文件承载不下，再新增：
  - [ ] `backend/tests/test_thread_ownership.py`
  - [ ] `backend/tests/test_governance_isolation.py`
  - [ ] `backend/tests/test_skills_tenant_scope.py`
  - [ ] `backend/tests/test_mcp_tenant_scope.py`
  - [ ] `backend/tests/test_user_data_lifecycle.py`

## K. 完成判定

- [ ] 跨租户隔离测试通过
- [ ] 同租户跨用户隔离测试通过
- [ ] 身份断流回归测试通过
- [ ] RBAC 权限执行测试通过
- [ ] Prompt Injection / Poisoning 渗透测试通过
- [ ] 生命周期与迁移兼容测试通过
- [ ] 并发与性能回归结果在可接受范围

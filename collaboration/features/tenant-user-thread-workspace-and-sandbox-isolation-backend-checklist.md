# Tenant User Thread Workspace And Sandbox Isolation Backend Checklist

- Related feature:
  [tenant-user-thread-workspace-and-sandbox-isolation.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation.md)
- Status: `mostly complete`
- Last reviewed: `2026-04-08`
- Owner: `backend`

## Current Backend Conclusion

基于当前代码复核，后端主链路已经完成，剩余主要是生产态容器验证和少量硬化决策：

- 路径模型、ownership 校验、middleware 身份链路、uploads API、sandbox_state 拆分、生命周期清理顺序都已落地。
- 当前快照的聚焦回归已通过：`43 passed, 1 skipped, 2 warnings`。
- 发布前主要还需要真实 AIO/container 挂载边界和 destroy/recover 验证。

## 0. 约束对齐

- [x] 对外 API 继续以 `thread_id` 为主键
- [x] 生产态坚持“每线程一个 sandbox”
- [x] `workspace` 默认临时，不作为长期数据承诺
- [x] `uploads / outputs / checkpoints` 持久化
- [x] `LocalSandboxProvider` 仅作为开发态能力，不作为生产隔离结论
- [x] `thread_id` 保持全局唯一前提
- [x] OIDC 启用时，旧 registry 条目缺失 `user_id` 会 deny
- [x] 当前结论建立在单进程 / 单 writer 前提上

## 1. 路径模型

### 1.1 Paths 与目录树

- [x] 路径模型切换到 `tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/`
- [x] `resolve_virtual_path()` 保持统一能力并映射到新目录模型
- [x] 新 thread 不再使用 `threads/{thread_id}` 作为生产路径
- [x] `sandbox_state/{thread_id}/` 独立于用户数据目录

### 1.2 ThreadContext 与注入链路

- [x] `ThreadContext` 已落地
- [x] `resolve_thread_context()` 已作为统一 ownership 校验入口
- [x] Gateway / runtime 装配链路使用 `ThreadContext`
- [x] 进入 LangGraph runtime 时将 `ThreadContext` 序列化到 `config.configurable["thread_context"]`
- [x] `ThreadDataMiddleware` 只认固定 `thread_context` key
- [x] `SandboxMiddleware` 不再从 `runtime.context` 或零散字段拼身份
- [x] registry 已避免让弱身份回退值覆盖真实 tenant/user 绑定

### 1.3 路径调用点

- [x] thread data 路径注入已改到基于 `ThreadContext`
- [x] uploads 路由已改到基于 `ThreadContext`
- [x] artifacts / runtime / 相关路径解析已走统一 ownership 校验链路
- [x] upload/list 响应不再暴露物理 `path`

## 2. Sandbox 与挂载

### 2.1 AIO Sandbox 挂载

- [x] AIO provider 已切到新路径模型
- [x] 同一 thread 可复用同一 sandbox
- [x] sandbox state 与 thread 数据目录已解耦
- [ ] 真实 AIO/container 最小挂载边界已在 Linux/container 环境完成验证

### 2.2 LocalSandbox 边界

- [x] 文档与评估中已明确 LocalSandbox 不能作为生产安全边界
- [ ] 生产态误用 LocalSandbox 的 hard fail 门禁是否需要落地，仍待决策

## 3. 持久化与恢复

- [x] memory / USER.md / governance ledger 与 sandbox 生命周期解耦
- [x] uploads / outputs / checkpoints 走持久化路径
- [x] workspace 默认视为临时区
- [ ] 真实 container destroy/recover 流程已完成生产态验证

## 4. 生命周期清理

### 4.1 删除用户 / 租户 / 过期 Thread

- [x] 删除用户会清理其名下 thread 数据
- [x] 删除租户会清理该租户下全部用户与 thread 数据
- [x] 清理过期 thread 会同时清理目录与 registry
- [x] `cleanup_expired_threads()` 会删除 thread 根目录与 legacy 路径残留

### 4.2 顺序与容错

- [x] 先停止 sandbox，再删 `sandbox_state/`
- [x] 文件删除发生在 registry 删除之前
- [x] best-effort 失败会记录到 `result.errors`
- [x] 用户、租户、TTL 三个场景都显式包含 `sandbox_state/` 清理

## 5. 访问控制与安全

### 5.1 Ownership

- [x] thread 相关入口统一走 tenant + user 校验
- [x] artifacts / uploads / runtime 入口已收口
- [x] 未注册 thread 统一拒绝
- [x] OIDC 启用时缺失 `tenant_id` 或 `user_id` 返回 `401`

### 5.2 路径安全

- [x] prefix confusion 防护已覆盖
- [x] path traversal 防护已覆盖
- [x] virtual path 通过统一路径工具解析
- [ ] 软链接逃逸仍需在支持 symlink 的环境补完实测

### 5.3 生产态容器约束

- [ ] non-root / 最小权限 / 最小环境变量注入要求仍需结合真实容器环境复核
- [ ] 网络出口默认策略仍需结合真实容器环境复核

## 6. 文档与协作

- [x] 主需求文档已更新到当前状态
- [x] 测试 checklist 已更新到当前状态
- [x] 测试执行记录已更新到当前状态
- [x] 当前没有必须新增的前端实现项

## 7. 完成判断

- [x] `tenant/user/thread` 路径模型已成为当前后端事实标准
- [x] `ThreadContext` resolver/factory 已落地
- [x] `sandbox_state/` 已从用户数据目录独立
- [x] 生命周期清理已覆盖用户、租户、过期 thread 三个场景
- [x] ownership 和 API 路径安全主链路已收口
- [ ] 真实 AIO/container 最小挂载验证完成
- [ ] 真实 container 销毁后恢复验证完成
- [ ] 生产态容器约束全部复核完成

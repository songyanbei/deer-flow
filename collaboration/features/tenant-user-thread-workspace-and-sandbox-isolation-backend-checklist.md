# Tenant User Thread Workspace And Sandbox Isolation Backend Checklist

- Related feature:
  [tenant-user-thread-workspace-and-sandbox-isolation.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation.md)
- Status: `draft`
- Owner: `backend`

## Scope

本清单只面向后端研发同学，覆盖：

- `tenant/user/thread` 路径模型重构
- thread 级沙箱挂载与恢复
- 持久化与生命周期清理闭环
- 路径安全与 ownership 校验收口
- 生产态隔离约束与开发态边界澄清

## 0. 实施前必须先对齐的约束

- [ ] 对外 API 继续以 `thread_id` 为主键，不新增前端必传身份字段
- [ ] 生产态坚持“每线程一个沙箱”，不改成“每用户一个共享 workspace”
- [ ] `workspace` 默认临时，不作为长期数据承诺
- [ ] `uploads / outputs / checkpoints` 必须持久化
- [ ] `LocalSandboxProvider` 只作为开发态能力，不作为生产安全边界

## 1. 路径模型

### 1.1 Paths 重构

- [ ] 更新 [paths.py](/E:/work/deer-flow/backend/src/config/paths.py)
- [ ] 新增或重构 `tenant/user/thread` 级路径工具
- [ ] 明确 thread 目录位于 `tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/`
- [ ] 保留统一 `resolve_virtual_path()` 能力，但底层映射到新目录模型
- [ ] 禁止继续把生产态 thread 工作目录落在 `threads/{thread_id}/`

### 1.2 路径调用方对齐

- [ ] 更新 [thread_data_middleware.py](/E:/work/deer-flow/backend/src/agents/middlewares/thread_data_middleware.py)
- [ ] 更新 [uploads_middleware.py](/E:/work/deer-flow/backend/src/agents/middlewares/uploads_middleware.py)
- [ ] 更新 [path_utils.py](/E:/work/deer-flow/backend/src/gateway/path_utils.py)
- [ ] 更新 artifacts / uploads / client 中依赖 thread 路径的调用点

Done when:

- 线程 runtime 注入的 `workspace/uploads/outputs` 均指向 `tenant/user/thread` 路径树。

## 2. 沙箱与挂载

### 2.1 AIO 沙箱挂载

- [ ] 更新 [aio_sandbox_provider.py](/E:/work/deer-flow/backend/src/community/aio_sandbox/aio_sandbox_provider.py)
- [ ] 按新目录模型挂载 `workspace / uploads / outputs`
- [ ] 保持“同一 thread 可复用同一 sandbox”
- [ ] 不挂整租户目录
- [ ] 不挂整用户目录

### 2.2 LocalSandbox 边界

- [ ] 更新 [local_sandbox_provider.py](/E:/work/deer-flow/backend/src/sandbox/local/local_sandbox_provider.py) 的注释、告警或配置门禁
- [ ] 明确开发态和生产态的隔离结论不能混用
- [ ] 如配置允许，增加生产环境误用 LocalSandbox 的显式警告或拒绝

Done when:

- 生产态 sandbox 的最小挂载边界只覆盖当前 thread 所需目录。

## 3. 持久化与恢复

### 3.1 保留数据

- [ ] 确认以下数据与沙箱生命周期解耦：
  - [ ] memory
  - [ ] USER.md
  - [ ] governance ledger
  - [ ] uploads
  - [ ] outputs
  - [ ] thread checkpoints / state

### 3.2 workspace 语义

- [ ] 明确 workspace 是临时区，不作为长期恢复唯一来源
- [ ] 如现有逻辑隐式依赖 workspace 恢复状态，补充迁移或收口方案
- [ ] 避免将依赖安装缓存、临时脚本误判为用户长期资产

Done when:

- sandbox 销毁后，线程仍可在不依赖旧沙箱的情况下恢复关键业务上下文。

## 4. 生命周期清理

### 4.1 用户/租户清理

- [ ] 更新 [lifecycle_manager.py](/E:/work/deer-flow/backend/src/admin/lifecycle_manager.py)
- [ ] 删除用户时清理其名下所有 thread 目录
- [ ] 删除租户时清理该租户下所有用户与 thread 目录
- [ ] 清理过期 thread 时，同时清理目录与 registry

### 4.2 registry 协同

- [ ] 复用或扩展 [thread_registry.py](/E:/work/deer-flow/backend/src/gateway/thread_registry.py) 的按用户/按租户枚举能力
- [ ] 确保清理顺序不会留下 orphan files
- [ ] 对部分失败场景保留可观察的错误记录

Done when:

- 用户删除、租户注销、TTL 清理三个场景都能完整回收文件型数据。

## 5. 访问控制与安全收口

### 5.1 ownership 校验

- [ ] 复核所有 thread 相关入口统一走 tenant + user 校验
- [ ] 复核 artifacts / uploads / runtime / interventions / client helper 无漏口
- [ ] 复核未注册 thread 是否默认拒绝

### 5.2 路径安全

- [ ] 补强 prefix confusion、path traversal、软链接逃逸防护
- [ ] 统一通过路径工具解析 virtual path，避免散落拼接
- [ ] 禁止通过宿主机绝对路径绕出 thread 根目录

### 5.3 生产态容器约束

- [ ] 梳理 non-root、最小权限、最小环境变量注入要求
- [ ] 梳理网络出口默认策略
- [ ] 若当前仓库无法直接落地全部容器配置，至少补文档和配置校验点

Done when:

- 普通脚本、普通路径绕过和常见 API 猜测路径都无法越权访问其他用户数据。

## 6. 文档与协作收口

- [ ] 如后端落地过程中发现前端依赖，写入 [backend-to-frontend.md](/E:/work/deer-flow/collaboration/handoffs/backend-to-frontend.md)
- [ ] 不在本轮主需求内扩展新的前端实现项
- [ ] 将最终后端实现边界补回主需求文档

## 7. 完成判定

- [ ] `tenant/user/thread` 路径模型已成为生产态事实标准
- [ ] AIO sandbox 已按新路径做 thread 级最小挂载
- [ ] 沙箱销毁后，关键持久化状态仍可恢复
- [ ] 生命周期清理可覆盖用户、租户、过期 thread 三个场景
- [ ] ownership、路径安全、开发态/生产态边界已全部收口
- [ ] 测试 checklist 所需前置条件已满足

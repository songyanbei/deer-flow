# Tenant User Thread Workspace And Sandbox Isolation Test Checklist

- Related feature:
  [tenant-user-thread-workspace-and-sandbox-isolation.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation.md)
- Status: `in progress`
- Last reviewed: `2026-04-08`
- Owner: `test`

## Current Test Conclusion

基于当前代码快照，Windows 本地可执行的聚焦回归已通过：

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_user_thread_isolation_regression.py tests/test_lifecycle_manager.py tests/test_uploads_router.py tests/test_thread_context.py -q
```

结果：

- `43 passed`
- `1 skipped`
- `2 warnings`

说明：

- `skipped` 仍是 symlink 环境能力相关跳过。
- warnings 不影响本特性的隔离结论，但真实容器 mount scope 与 destroy/recover 仍需在 Linux/container 环境补验。

## A. 测试准备

### A1. 环境准备

- [x] 准备可运行后端测试的环境
- [x] 区分开发态 LocalSandbox 与生产态 AIO/container sandbox
- [ ] 本轮“强隔离”最终结论只以生产态容器 sandbox 结果为准
- [x] 确认当前回归结论建立在单进程 / 单 writer 前提上

### A2. 身份样本

- [x] 至少准备两个租户
- [x] 每个租户至少准备两个用户
- [x] 每个用户至少准备两个 thread
- [x] 已覆盖 tenant-a / user-1 / thread-a1
- [x] 已覆盖 tenant-a / user-1 / thread-a2
- [x] 已覆盖 tenant-a / user-2 / thread-a3
- [x] 已覆盖 tenant-b / user-3 / thread-b1

## B. 目录与路径隔离

### B1. 新目录模型

- [x] `workspace / uploads / outputs` 落于 `tenant/user/thread` 目录树
- [x] 同一用户不同 thread 目录完全不同
- [x] 不同用户目录完全不同
- [x] 不同租户目录完全不同

### B2. Virtual path 解析

- [x] `/mnt/user-data/workspace/*` 正确映射到当前 thread
- [x] `/mnt/user-data/uploads/*` 正确映射到当前 thread
- [x] `/mnt/user-data/outputs/*` 正确映射到当前 thread
- [x] prefix confusion 被拒绝
- [x] `..` 路径穿越被拒绝
- [ ] 软链接逃逸被拒绝

## C. ThreadContext Resolver

### C1. Resolver 行为

- [x] tenant 不匹配返回 `403`
- [x] user 不匹配返回 `403`
- [x] 未注册 thread 返回 `403`
- [x] 校验通过后返回的 `ThreadContext` 字段与入参一致
- [x] middleware 只从 `config.configurable["thread_context"]` 读取 identity

### C2. Legacy Registry 条目

- [x] OIDC 启用时，缺失 `user_id` 的旧条目被 deny
- [x] OIDC 关闭时，缺失 `user_id` 的旧条目保留 tenant-only 兼容行为

## D. Ownership 与接口隔离

### D1. 跨租户拒绝

- [x] artifact 越权访问被拒绝
- [x] uploads 列表越权访问被拒绝
- [x] 向其他租户 thread 上传文件被拒绝
- [x] runtime thread 状态越权访问被拒绝

### D2. 同租户跨用户拒绝

- [x] artifact 越权访问被拒绝
- [x] uploads 列表越权访问被拒绝
- [x] uploads 删除越权访问被拒绝
- [x] 上传到其他用户 thread 被拒绝

### D3. 同用户跨 Thread

- [x] artifact 不串
- [x] uploads 不串
- [x] workspace 临时文件不串

## E. Sandbox 隔离

### E1. Thread 级 Sandbox

- [x] 同一 thread 多轮运行可复用同一 sandbox
- [x] 不同 thread 不复用同一个 workspace 挂载
- [x] 不同 thread 的临时文件互不可见

### E2. 最小挂载

- [ ] sandbox 内可见目录只包含当前 thread 所需挂载
- [ ] sandbox 内不能直接看到其他用户目录
- [ ] sandbox 内不能直接看到整个租户目录
- [ ] sandbox 内不能直接看到宿主机无关目录

### E3. 开发态边界

- [x] LocalSandbox 的结果不作为生产隔离结论
- [ ] 生产态误用 LocalSandbox 时的显式警告或拒绝已在真实环境验证

## F. Sandbox 销毁后的恢复

### F1. 持久化数据恢复

- [ ] sandbox 销毁后 memory 可恢复
- [ ] sandbox 销毁后 governance ledger 可恢复
- [ ] sandbox 销毁后 uploads 可恢复
- [ ] sandbox 销毁后 outputs 可恢复
- [ ] sandbox 销毁后 thread state/checkpoints 可恢复

### F2. 临时区不恢复

- [ ] sandbox 销毁后 workspace 临时文件不会被误恢复
- [ ] sandbox 销毁后临时进程状态不会残留
- [ ] sandbox 销毁后依赖安装缓存不会被误判为用户资产

## G. 生命周期清理

### G1. 删除用户

- [x] 删除用户后 memory 被清理
- [x] 删除用户后 ledger 被清理
- [x] 删除用户后 thread 目录被清理
- [x] 删除用户后 uploads / outputs 被清理
- [x] 删除用户后其名下 thread 的 `sandbox_state/{thread_id}/` 被清理

### G2. 删除租户

- [x] 删除租户后该租户全部用户与 thread 数据被清理
- [x] 删除租户后该租户名下 `sandbox_state/` 被清理
- [x] 删除租户后其他租户数据不受影响

### G3. 过期 Thread 清理

- [x] 只清理目标范围内的过期 thread
- [x] 清理后 registry 与文件目录状态一致
- [x] 清理后不留下 orphan workspaces
- [x] 清理后对应 `sandbox_state/{thread_id}/` 不残留

### G4. 清理顺序

- [x] 顺序为：停止 sandbox -> 删除 sandbox_state -> 删除用户/thread 数据目录
- [x] sandbox 仍在运行时，sandbox_state 不会被提前删除
- [x] 单步失败不阻塞后续步骤，且失败被记录到 errors

## H. 负向与对抗

- [x] 猜测其他 thread_id 访问 artifact 被拒绝
- [ ] 通过脚本尝试访问其他用户 uploads 被拒绝
- [x] 通过绝对路径尝试访问宿主机敏感路径失败
- [ ] 通过软链接指向其他目录的访问失败
- [x] 缺失 `tenant_id` 或 `user_id` 的 OIDC 请求返回 `401`

## I. 新旧路径切换兼容

### I1. 新路径生效

- [x] 新 thread 的数据落盘位置为 `tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/`
- [x] 旧 `threads/{thread_id}/` 路径不再被新 thread 使用
- [x] `resolve_virtual_path()` 在新路径模型下正确解析

### I2. 旧数据兼容或迁移

- [ ] 若存在迁移脚本或兼容读取逻辑，验证旧数据可正确迁移或访问
- [ ] 迁移后旧路径不残留业务数据

### I3. Registry 兼容性

- [ ] registry 旧格式条目升级流程已独立验证
- [x] OIDC 启用时旧格式条目 `check_access()` 返回 deny

## J. 回归要求

- [x] memory / uploads / artifacts / runtime 关键回归继续通过
- [x] sandbox / workspace runtime 关键回归继续通过
- [x] 新目录模型没有破坏前端对 `thread_id` 路由和 artifact URL 的消费

## K. 放行判断

- [ ] 目录隔离、接口隔离、sandbox 隔离、恢复能力、生命周期清理五类测试均已在生产态容器环境闭环
- [x] `ThreadContext` resolver 行为正确
- [x] OIDC 启用时 legacy registry 条目被 deny
- [x] 所有跨租户与同租户跨用户越权场景均被稳定拒绝
- [x] 同用户不同 thread 不共享 workspace
- [ ] sandbox 销毁后仅恢复应恢复的数据
- [x] 生命周期清理按正确顺序执行，`sandbox_state/` 无残留
- [ ] 新旧路径切换兼容性验证全部通过
- [x] 已输出测试用例与测试执行记录

## Remaining Work

当前主要剩余的是生产态验证，不是代码侧主缺陷：

- 在 WSL / Linux 中跑真实 AIO/container 最小挂载验证
- 在真实 container 中做 destroy/recover 链路验证
- 在支持 symlink 的环境补完软链接逃逸用例

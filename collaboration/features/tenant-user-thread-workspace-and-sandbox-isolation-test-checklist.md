# Tenant User Thread Workspace And Sandbox Isolation Test Checklist

- Related feature:
  [tenant-user-thread-workspace-and-sandbox-isolation.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation.md)
- Status: `draft`
- Owner: `test`

## Scope

本清单只面向测试同学，覆盖：

- `tenant/user/thread` 目录隔离验证
- thread 级沙箱隔离验证
- 路径安全与 ownership 拒绝验证
- 沙箱销毁后的恢复验证
- 用户/租户/过期 thread 生命周期清理验证

## A. 测试准备

### A1. 环境准备

- [ ] 准备可运行后端测试的环境
- [ ] 区分开发态 LocalSandbox 与生产态 AIO/container sandbox
- [ ] 明确本轮“强隔离”结论只以生产态容器沙箱结果为准

### A2. 身份样本

- [ ] 至少准备两个租户
- [ ] 每个租户至少准备两个用户
- [ ] 每个用户至少准备两个 thread

建议使用如下矩阵：

- [ ] tenant-a / user-1 / thread-a1
- [ ] tenant-a / user-1 / thread-a2
- [ ] tenant-a / user-2 / thread-a3
- [ ] tenant-b / user-3 / thread-b1

## B. 目录与路径隔离测试

### B1. 新目录模型

- [ ] 验证线程运行后，`workspace / uploads / outputs` 落于 `tenant/user/thread` 目录树
- [ ] 验证同一用户不同 thread 的目录完全不同
- [ ] 验证不同用户的目录完全不同
- [ ] 验证不同租户的目录完全不同

### B2. Virtual path 解析

- [ ] 校验 `/mnt/user-data/workspace/*` 正确映射到当前 thread 目录
- [ ] 校验 `/mnt/user-data/uploads/*` 正确映射到当前 thread 目录
- [ ] 校验 `/mnt/user-data/outputs/*` 正确映射到当前 thread 目录
- [ ] 校验 prefix confusion 被拒绝
- [ ] 校验 `..` 路径穿越被拒绝
- [ ] 校验软链接逃逸被拒绝

## C. ownership 与接口隔离测试

### C1. 跨租户拒绝

- [ ] tenant-a 的用户访问 tenant-b 的 artifact 返回拒绝
- [ ] tenant-a 的用户列出 tenant-b 的 uploads 返回拒绝
- [ ] tenant-a 的用户向 tenant-b 的 thread 上传文件返回拒绝
- [ ] tenant-a 的用户访问 tenant-b 的 runtime thread 状态返回拒绝

### C2. 同租户跨用户拒绝

- [ ] tenant-a/user-1 无法访问 tenant-a/user-2 的 artifact
- [ ] tenant-a/user-1 无法列出 tenant-a/user-2 的 uploads
- [ ] tenant-a/user-1 无法删除 tenant-a/user-2 的 uploads
- [ ] tenant-a/user-1 无法向 tenant-a/user-2 的 thread 上传文件

### C3. 同用户跨 thread 行为

- [ ] 同用户不同 thread 之间 artifact 不串
- [ ] 同用户不同 thread 之间 uploads 不串
- [ ] 同用户不同 thread 之间 workspace 临时文件不串

## D. 沙箱隔离测试

### D1. thread 级沙箱

- [ ] 同一 thread 多轮运行可复用同一沙箱
- [ ] 不同 thread 不复用同一个 workspace 挂载
- [ ] 不同 thread 的临时文件互不可见

### D2. 最小挂载

- [ ] 沙箱内可见目录只包含当前 thread 所需挂载
- [ ] 沙箱内不能直接看到其他用户目录
- [ ] 沙箱内不能直接看到整个租户目录
- [ ] 沙箱内不能直接看到宿主机无关目录

### D3. 开发态边界

- [ ] LocalSandbox 的行为不作为生产隔离结论
- [ ] 如系统有配置门禁，验证生产态误用 LocalSandbox 时有显式告警或拒绝

## E. 沙箱销毁后的恢复测试

### E1. 持久化数据恢复

- [ ] sandbox 销毁后 memory 可恢复
- [ ] sandbox 销毁后 governance ledger 可恢复
- [ ] sandbox 销毁后 uploads 可恢复
- [ ] sandbox 销毁后 outputs 可恢复
- [ ] sandbox 销毁后 thread state/checkpoints 可恢复

### E2. 临时区不恢复

- [ ] sandbox 销毁后 workspace 临时文件不被误当作长期状态自动恢复
- [ ] sandbox 销毁后临时进程状态不会残留
- [ ] sandbox 销毁后依赖安装缓存不被误判为用户资产

## F. 生命周期清理测试

### F1. 删除用户

- [ ] 删除用户后，其 memory 被清理
- [ ] 删除用户后，其 ledger 被清理
- [ ] 删除用户后，其 thread 目录被清理
- [ ] 删除用户后，其 uploads / outputs 被清理

### F2. 删除租户

- [ ] 删除租户后，该租户全部用户与 thread 数据被清理
- [ ] 删除租户后，其他租户数据不受影响

### F3. 过期 thread 清理

- [ ] 只清理目标范围内的过期 thread
- [ ] 清理后 registry 与文件目录状态一致
- [ ] 清理后不留下 orphan workspaces

## G. 负向与对抗测试

- [ ] 通过猜测其他 thread_id 访问 artifact 被拒绝
- [ ] 通过脚本尝试访问其他用户 uploads 被拒绝
- [ ] 通过绝对路径尝试访问宿主机敏感路径失败
- [ ] 通过软链接指向其他目录的访问失败
- [ ] 缺失 `tenant_id` 或 `user_id` 的 OIDC 请求被拒绝

## H. 回归要求

- [ ] 现有 memory、uploads、artifacts、runtime 关键回归继续通过
- [ ] 现有 sandbox/workspace runtime 关键回归继续通过
- [ ] 不因为新目录模型破坏前端对 `thread_id` 路由和 artifact URL 的消费

## I. 通过标准

- [ ] 目录隔离、接口隔离、沙箱隔离、恢复能力、生命周期清理五类测试均已覆盖
- [ ] 所有跨租户与同租户跨用户越权场景均被稳定拒绝
- [ ] 同用户不同 thread 不共享 workspace
- [ ] 沙箱销毁后仅恢复应恢复的数据
- [ ] 输出测试用例清单
- [ ] 输出测试执行记录

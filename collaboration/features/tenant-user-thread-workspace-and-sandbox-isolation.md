# Tenant User Thread Workspace And Sandbox Isolation

- Status: `draft`
- Owner suggestion: backend + test
- Related area: multi-tenant / workspace / sandbox / lifecycle / security

## Goal

在 DeerFlow 当前多租户基础上，落地一套面向生产环境的“用户级强隔离”方案：

1. 持久化数据按 `tenant_id / user_id / thread_id` 分层落盘。
2. 运行时继续采用“每线程一个沙箱”模型，不共享 workspace。
3. 沙箱销毁后，关键业务状态仍可安全恢复。
4. 普通用户、普通脚本和常见路径绕过手段无法访问其他用户数据。

本需求的目标是“应用框架层面的强隔离”，不是容器/内核层面的绝对安全隔离。

## Why This Needs Frontend/Backend Collaboration

本轮直接执行方主要是后端和测试，前端不是首要改动方。

但仍需统一约束：

- 前端继续以 `thread_id` 作为路由和 artifact URL 的对外主键，不感知底层目录重构。
- 如果后端实施过程中发现需要前端补充安全态展示、错误提示或交互限制，统一写入 [backend-to-frontend.md](/E:/work/deer-flow/collaboration/handoffs/backend-to-frontend.md)。

## 文档分发

- 主需求文档：
  [tenant-user-thread-workspace-and-sandbox-isolation.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation.md)
- 后端研发 Checklist：
  [tenant-user-thread-workspace-and-sandbox-isolation-backend-checklist.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation-backend-checklist.md)
- 测试 Checklist：
  [tenant-user-thread-workspace-and-sandbox-isolation-test-checklist.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation-test-checklist.md)

使用方式：

1. 先读本文，确认目标边界、当前现状、目标模型和验收口径。
2. 后端只按 backend checklist 拆实现任务，不从本文直接反推研发任务。
3. 测试只按 test checklist 准备环境、设计用例和执行回归，不从本文直接拼测试范围。
4. 若执行中出现上游平台或前端依赖，再通过 handoff 文档补充，不在本文里堆阻塞项。

## Current Behavior

### Backend

当前代码现状已经具备以下基础：

- `thread_id` 已能绑定 `tenant_id / user_id`，并用于 thread ownership 校验。
- `artifacts`、`uploads`、`runtime` 相关路径已存在 tenant/user 维度访问控制。
- `memory` 和 `governance ledger` 已支持按 `tenant/user` 目录落盘。
- AIO 沙箱当前采用 thread 级挂载，挂载对象是 `workspace / uploads / outputs`。

当前还存在以下核心缺口：

- `workspace / uploads / outputs` 的物理路径仍以 `threads/{thread_id}` 为主，不是 `tenant/user/thread` 分层。
- 生命周期清理没有把用户名下所有 thread 工作目录一并收口。
- `LocalSandboxProvider` 是开发态单例，不应被视为生产安全边界。
- 当前隔离更多依赖“访问校验”，目录归属、挂载范围和清理链路尚未完全闭环。

### Frontend

当前前端无需直接改动业务主链路，继续保持：

- 路由主键仍为 `thread_id`
- artifact URL 仍为 `/api/threads/{thread_id}/artifacts/...`
- 上传和展示链路不因为底层目录模型升级而破坏

## Contract To Confirm First

- Event/API:
  - 对外 API 继续以 `thread_id` 为主键，不引入新的前端必传路径主键。
- Payload shape:
  - 请求体中的身份字段不可信，身份仅来自认证上下文。
- Persistence:
  - 长期记忆、治理账本按 `tenant/user` 持久化。
  - `uploads / outputs / checkpoints` 按 `tenant/user/thread` 持久化。
  - `workspace` 默认视为临时区，不作为长期存储承诺。
- Error behavior:
  - OIDC 启用时，缺失 `tenant_id` 或 `user_id` 返回 `401`。
  - ownership 不匹配返回 `403`。
  - thread 不存在或未注册返回 `404` 或显式拒绝，不允许静默降级。
- Dedup/replacement:
  - 继续保持“同一 thread 可复用同一沙箱”的能力。
  - 不引入“同一用户多个 thread 共享一个 workspace”的模型。

## 目标模型

### 1. 目录模型

目标目录结构：

```text
{base_dir}/
  tenants/
    {tenant_id}/
      users/
        {user_id}/
          memory.json
          USER.md
          governance_ledger.jsonl
          agents/
          threads/
            {thread_id}/
              user-data/
                workspace/
                uploads/
                outputs/
```

约束：

- 所有线程文件型数据必须位于所属 `tenant/user/thread` 目录树下。
- 不再以 `threads/{thread_id}` 作为生产态主目录模型。

### 2. 沙箱模型

- 生产态采用“每线程一个容器沙箱”。
- 同一 `thread_id` 多轮运行可复用同一沙箱。
- 不同 `thread_id` 不共享 workspace。
- 沙箱只挂当前 thread 的最小目录，不挂整个用户目录或租户目录。

### 3. 数据持久化模型

必须持久化：

- `memory`
- `USER.md`
- `governance ledger`
- `uploads`
- `outputs`
- thread 状态与 checkpoints

默认不持久化：

- workspace 临时脚本
- 运行进程状态
- 沙箱内依赖安装结果
- 沙箱内临时缓存

## Backend Changes

### A. 路径与目录模型重构

- 将 `workspace / uploads / outputs` 从 thread 根目录迁移到 `tenant/user/thread` 路径树。
- 提供统一的路径生成、路径解析和 virtual path 映射入口。
- 保证对外 API 不因物理路径变更而破坏。

### B. 线程级沙箱与最小挂载

- 保持 thread 级沙箱复用。
- AIO 沙箱挂载源目录切换到新的 `tenant/user/thread` 路径。
- 明确 `LocalSandboxProvider` 仅用于开发，不作为生产方案。

### C. 持久化与恢复

- 让 memory、ledger、uploads、outputs、checkpoints 与沙箱生命周期解耦。
- 沙箱销毁后，线程仍能基于持久化状态恢复。
- 不把 workspace 作为长期状态恢复的唯一来源。

### D. 生命周期清理

- 删除用户时，清理其名下所有 thread 目录和相关持久化数据。
- 删除租户时，清理该租户下所有用户及其 thread 数据。
- 清理过期 thread 时，连带清理对应工作目录和运行时资源。

### E. 安全收口

- 统一 thread ownership 校验模型。
- 容器默认 non-root、最小权限、最小挂载。
- 收紧环境变量注入和网络出口策略。
- 为路径穿越、软链接逃逸、跨 thread 猜测访问补齐测试门禁。

## Frontend Changes

本轮不要求前端直接改代码。

前端需要默认接受以下后端约束保持不变：

- 路由模型不变
- artifact URL 结构不变
- 上传、展示、下载接口主键不变

如果后续要增加隔离态提示、管理员清理反馈或更细粒度错误呈现，另开前端需求，不在本轮主任务内展开。

## Risks

- 旧目录与新目录并存期间可能产生迁移和清理歧义。
- 生命周期清理若只删 registry 不删物理目录，会留下孤儿数据。
- 如果误把整个用户目录挂进容器，会破坏 thread 级边界。
- 本地沙箱若被误用于生产验证，会造成隔离结论失真。
- 该方案能达到应用框架层面的强隔离，但不能承诺容器/内核层面的绝对安全隔离。

## Acceptance Criteria

- 生产态线程文件数据全部落于 `tenant/user/thread` 路径树。
- 生产态保持“每线程一个沙箱”，且只挂最小目录。
- 任一用户无法通过 API、artifact、upload 或普通脚本访问其他用户数据。
- 删除用户/租户后，不残留对应 thread 工作目录和关键持久化数据。
- 沙箱销毁后，memory、ledger、uploads、outputs、checkpoints 可恢复。
- 后端 checklist 与测试 checklist 中的前置项全部闭环。

## Open Questions

- 是否需要引入用户级共享只读区或缓存区。
- 是否需要为生产环境单独定义“禁止 LocalSandboxProvider 启动”的配置门禁。
- 是否需要在后续阶段补充安全态观测看板或管理员清理可视化。

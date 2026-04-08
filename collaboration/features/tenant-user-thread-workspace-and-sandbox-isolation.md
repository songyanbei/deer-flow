# Tenant User Thread Workspace And Sandbox Isolation

- Status: `backend complete, production container validation pending`
- Last reviewed: `2026-04-08`
- Owner suggestion: backend + test
- Related area: multi-tenant / workspace / sandbox / lifecycle / security

## Goal

在 DeerFlow 现有多租户基础上，完成面向生产环境的 thread 级隔离方案：

1. 持久化线程数据按 `tenant_id / user_id / thread_id` 分层落盘。
2. 生产态继续坚持“每线程一个 sandbox”，不引入“每用户共享 workspace”。
3. sandbox 销毁后，关键持久化状态仍可恢复。
4. 普通用户、普通脚本和常见路径绕过手段无法访问其他用户或其他线程的数据。

本需求关注的是应用框架层的强隔离，不承诺容器或内核层的绝对安全。

## Current Status

截至 `2026-04-08`，代码侧主链路已经落地，当前结论是：

- `tenant/user/thread` 路径模型已成为后端事实标准。
- `ThreadContext` 已作为 thread ownership 校验后的统一上下文对象落地。
- middleware 身份来源已统一收敛到 `config.configurable["thread_context"]`，不再从 `runtime.context` 或零散字段 fallback 拼身份。
- uploads API 已不再暴露物理路径，只返回 virtual path 和 artifact URL。
- `sandbox_state/{thread_id}/` 已从用户数据目录剥离。
- 生命周期清理已改为“先停 sandbox，再删 sandbox_state，再删文件目录，最后删 registry”。
- 聚焦回归已在当前快照重跑通过：`43 passed, 1 skipped, 2 warnings`。

当前尚未完成的发布前验证只有一类：

- 真实 Linux / WSL / AIO container 环境下的最小挂载边界验证。
- 真实 container sandbox 的销毁后恢复链路验证。

## Current Behavior

### Backend

当前代码已经具备以下行为：

- 线程文件型数据落盘到 `tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/user-data/`。
- 对外 API 仍以 `thread_id` 为主键，不要求前端新增身份字段。
- `resolve_thread_context()` 统一负责 ownership 校验，unknown thread 和 unauthorized thread 均返回 `403`，避免资源枚举。
- OIDC 启用时，缺失 `tenant_id` 或 `user_id` 的请求返回 `401`。
- OIDC 启用时，旧 registry 条目若缺失 `user_id`，会被 deny；OIDC 关闭时保留 tenant-only 兼容语义。
- AIO sandbox 的运行时状态独立存放在 `sandbox_state/{thread_id}/`。
- 删除用户、删除租户、清理过期 thread 三个生命周期场景都显式包含 sandbox 停止与 `sandbox_state/` 清理。

### Frontend

当前不需要前端改动主链路，仍保持：

- 路由主键为 `thread_id`
- artifact URL 结构为 `/api/threads/{thread_id}/artifacts/...`
- 前端不感知底层物理目录重构

若后端后续在生产态容器验证中发现需要新增提示或限制，再通过 [backend-to-frontend.md](/E:/work/deer-flow/collaboration/handoffs/backend-to-frontend.md) 单独交接。

## Contract

- Event/API:
  - 对外 API 继续以 `thread_id` 为主键。
- Payload shape:
  - 身份只来自认证上下文，不信任请求体自带身份字段。
- Persistence:
  - `memory`、`USER.md`、`governance ledger` 按 `tenant/user` 持久化。
  - `uploads / outputs / checkpoints` 按 `tenant/user/thread` 持久化。
  - `workspace` 默认视为临时区，不承诺长期恢复。
- Error behavior:
  - `401`：OIDC 启用时缺失 `tenant_id` 或 `user_id`
  - `403`：ownership 不匹配，或 thread 未注册
  - `404`：ownership 已确认后，请求的业务对象不存在
- Identity:
  - `thread_id` 必须保持全局唯一。
  - 统一身份链路为：认证上下文 -> `ThreadContext` -> `config.configurable["thread_context"]`
- Sandbox:
  - 同一 thread 可复用同一 sandbox。
  - 不同 thread 不共享 workspace。

## Target Model

```text
{base_dir}/
  sandbox_state/
    {thread_id}/
      sandbox.json
      sandbox.lock
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

约束如下：

- 线程文件型数据必须落在所属 `tenant/user/thread` 树下。
- 不再把生产态 thread 工作目录落在 `threads/{thread_id}`。
- `sandbox_state/` 是运行时编排状态，不属于用户业务数据。

## Acceptance Status

### 已完成

- 路径模型切换到 `tenant/user/thread`
- `ThreadContext` resolver/factory 落地
- middleware fallback 身份链移除
- upload/list API 不再暴露物理路径
- 生命周期清理顺序修正为 file-first / registry-last，并显式 stop sandbox
- OIDC legacy registry deny 行为落地
- 聚焦回归在当前快照通过

### 待补验证

- 真实 AIO/container sandbox 的 thread 级最小挂载
- 真实 container 销毁后恢复 memory / ledger / uploads / outputs / checkpoints
- 生产态误用 `LocalSandboxProvider` 的门禁是否需要从 warning 收紧到 hard fail

## Risks And Release Gate

- 当前“强隔离”结论仍以真实容器 sandbox 验证为最终放行依据，不能仅凭 Windows 本地回归放行。
- 当前 `SandboxMiddleware` 在缺失 `thread_context` 时会 warning 后继续，让 provider 可能走 legacy mount 路径；现有入口链路和测试已覆盖正常注入，但生产态仍应把这类 warning 视为异常信号。
- `thread_registry.json` 仍是单进程 / 单 writer 前提设计；多 worker / 多进程部署不在本轮保证范围内。

## Open Questions

- 是否要将生产环境误用 `LocalSandboxProvider` 升级为硬拒绝。
- 是否需要后续补充更细粒度的隔离观测或管理端告警。
- 是否要在 Linux/container 验证完成后，把 mount scope 和恢复证据固化进单独执行记录。

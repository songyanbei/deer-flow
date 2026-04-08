# Tenant User Thread Workspace And Sandbox Isolation Test Execution

- Date: `2026-04-08`
- Role: `test`
- Scope basis:
  - [tenant-user-thread-workspace-and-sandbox-isolation.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation.md)
  - [tenant-user-thread-workspace-and-sandbox-isolation-test-checklist.md](/E:/work/deer-flow/collaboration/features/tenant-user-thread-workspace-and-sandbox-isolation-test-checklist.md)

## Requirement Summary

当前需求的核心保证有五项：

1. thread 文件型数据落在 `tenant_id / user_id / thread_id` 目录树。
2. ownership 校验稳定拒绝跨租户、跨用户和未注册 thread 访问。
3. thread identity 统一通过校验后的 `ThreadContext` 传递。
4. 生命周期清理覆盖用户、租户、过期 thread，并显式包含 `sandbox_state/`。
5. 真实生产态放行前，还需要补完 AIO/container 最小挂载与 destroy/recover 验证。

## Code Recheck Summary

本轮已重新按当前代码快照复核以下实现：

- `ThreadDataMiddleware` 只从 `config.configurable["thread_context"]` 读取 identity，不再从 `runtime.context` fallback。
- `SandboxMiddleware` 不再拼装替代身份，只在缺失 `thread_context` 时打 warning。
- uploads API 已移除物理 `path` / `markdown_path` 输出。
- `LifecycleManager` 已改为：
  - stop sandbox
  - delete `sandbox_state/{thread_id}/`
  - delete filesystem data
  - delete registry entry
- registry 已避免弱身份回退值覆盖真实 tenant/user 绑定。

## Executed Test Runs

### Latest Focused Regression

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest tests/test_tenant_user_thread_isolation_regression.py tests/test_lifecycle_manager.py tests/test_uploads_router.py tests/test_thread_context.py -q
```

Result:

- `43 passed`
- `1 skipped`
- `2 warnings`

Warnings:

- `backend/src/mcp/runtime_manager.py:271` 的 `DeprecationWarning`
- Windows 下 `.pytest_cache` 写入权限告警

这两项均不影响本特性的通过判断。

### Earlier Broader Regression

```powershell
$env:PYTHONPATH='.'
.\.venv\Scripts\python.exe -m pytest `
  tests/test_paths_tenant_user_threads.py `
  tests/test_thread_context.py `
  tests/test_lifecycle_manager.py `
  tests/test_uploads_router.py `
  tests/test_runtime_router.py `
  tests/test_tenant_user_thread_isolation_regression.py -q
```

Result:

- `120 passed`
- `1 skipped`
- `1 warning`

### Stress Rerun

```powershell
$env:PYTHONPATH='.'
for ($i = 1; $i -le 5; $i++) {
  .\.venv\Scripts\python.exe -m pytest tests/test_tenant_user_thread_isolation_regression.py -q
}
```

Result:

- `5/5` runs passed
- each run: `14 passed, 1 skipped`

## Checklist Mapping

- 目录模型与 virtual path：已覆盖。
- `ThreadContext` resolver 与 identity 固定 key：已覆盖。
- 跨租户、跨用户、未注册 thread 的拒绝语义：已覆盖。
- uploads / artifacts / runtime 关键隔离回归：已覆盖。
- 生命周期清理顺序与 `sandbox_state/` 清理：已覆盖。
- 旧 registry 条目在 OIDC 启用时 deny：已覆盖。
- 真实容器最小挂载与 destroy/recover：当前未在本 Windows 工作区执行。

## Current Conclusion

按当前代码快照与已执行回归来看：

- 代码侧主需求已完成。
- 之前记录中的两个实现缺口已经关闭：
  - middleware identity fallback 已移除
  - lifecycle stop-sandbox-first 顺序已落地
- 当前剩余的不是代码主缺陷，而是生产态容器验证项。

## Remaining Validation

发布前仍建议补跑两类真实环境验证：

1. Linux / WSL / AIO container 下的最小挂载边界验证
2. 真实 container sandbox 的 destroy/recover 链路验证

## Environment Limits

- symlink 相关 case 仍受当前 Windows 环境能力限制，需在支持 symlink 的环境补验。
- 真实生产态 AIO/container sandbox 验证不应以 LocalSandbox 结果替代。

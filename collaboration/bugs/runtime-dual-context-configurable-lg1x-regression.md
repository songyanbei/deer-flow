# Bug Report: LangGraph 1.x 升级后 `/api/runtime/threads/{id}/messages:stream` 全线失效

- **严重级别**：P0（核心聊天入口不可用）
- **影响范围**：所有走 Gateway `/api/runtime/*` 的流式对话调用 —— 含网页端聊天、外部平台对接
- **不影响**：SSO 登录、鉴权、USER.md 供给、审计、`/api/me/*` 等所有非流式接口
- **状态**：已定位，未修复
- **首次复现时间**：2026-04-21 SSO 真实链路联调
- **归属分支**：问题代码存在于 `main / sso / codex/save-multi-agent-work` 等所有活跃分支
- **责任归属**：**非 SSO 功能引入**，根因是 `4ae14587`（2026-04-03，多租户隔离改造）在 `langgraph-api 0.7.x` HTTP 层互斥校验**已生效 28 天**的前提下，仍往 `configurable` 和 `context` 同时写入身份字段，且合并时未跑经过 `langgraph-api` HTTP 层的端到端冒烟；而非"依赖升级暴露历史代码"

---

## 1. 现象（Symptoms）

### 1.1 网页端聊天

用户在 `/workspace/chats/new` 发送任意消息（如"你好"），浏览器控制台看到：

```
ValueError: ThreadDataMiddleware: configurable['thread_context'] is required but missing.
All callers (Gateway, DeerFlowClient, tests) must serialize ThreadContext
into configurable before invoking the agent.
```

对应 HTTP：`POST /api/runtime/threads/{thread_id}/messages:stream` → SSE 流中吐出 `run_failed` 事件，无 assistant 回复。

### 1.2 直连 LangGraph SDK 复现（绕过 Gateway 脱敏）

用 `langgraph_sdk` 客户端以同样 payload 直接调 `http://127.0.0.1:2024`：

```python
BadRequestError: Cannot specify both configurable and context.
Prefer setting context alone. Context was introduced in LangGraph 0.6.0
and is the long term planned replacement for configurable.
```

Gateway 侧因为 `_sanitize_error` 脱敏，用户只看到 `503 "LangGraph submission failed: Runtime execution failed"`，但底层是同一件事。

### 1.3 SSO 全流程压测脚本

`backend/scripts/sso_e2e_smoke.py` 运行结果：

```
Phase 1 — SSO callback                          13/13 PASS
Phase 1b — USER.md provisioning                 5/5  PASS
Phase 1c — Auth audit ledger                    6/6  PASS
Phase 2 — Middleware cookie round-trip          2/2  PASS
Phase 3 — Runtime stream
   POST /api/runtime/threads                    PASS (201 Created)
   POST /messages:stream                        FAIL (503 Runtime execution failed)
Total: 27/28
```

---

## 2. 根因（Root Cause）

### 2.1 两个独立事实，必须分开说

本 Bug 的两种错误表现（`BadRequestError` 和 `ValueError: thread_context is required`）**不是由同一个互斥冲突直接引出**，而是两个独立缺陷，分别在不同入口触发。

#### 事实 A：`configurable` 与 `context` 同传违反 LangGraph 1.x 校验

`backend/src/gateway/runtime_service.py::start_stream()`（line 286-295）同时传入了两个互斥参数：

```python
upstream_iter = client.runs.stream(
    thread_id,
    ENTRY_GRAPH_ASSISTANT_ID,
    input=input_payload,
    config=run_config,      # ← configurable = {thread_id, tenant_id, user_id, auth_user}
    context=context,        # ← {thread_id, tenant_id, user_id, username, allowed_agents,
                            #    group_key, thread_context, auth_user, agent_name, ...}
    stream_mode=["values", "messages"],
    multitask_strategy="reject",
)
```

LangGraph 从 0.6.0 起规定 `configurable` 与 `context` 互斥，**当前本地依赖**（`langgraph-api 0.7.65 / langgraph-runtime-inmem 0.26.0`）的实现是：**只要两边都非空，服务端直接返回 400**，不会做任何"择一取优"。

> *"Cannot specify both configurable and context. Prefer setting context alone.
> Context was introduced in LangGraph 0.6.0 and is the long term planned
> replacement for configurable."*

工程 pin 的版本 `langgraph>=1.0.6` / `langgraph-api>=0.7.0,<0.8.0`（`backend/pyproject.toml`）已越过 0.6 这条红线。

#### 事实 B：进入 `ThreadDataMiddleware` 的身份上下文缺少 `thread_context`

即使事实 A 被修复，只要最终到达 `ThreadDataMiddleware` 的配置里没有 `thread_context`，agent 仍会炸，**原因与互斥规则无关**：

- `runtime_service.start_stream` 当前构造的 `configurable` 只含 `thread_id / tenant_id / user_id / auth_user`（line 271-284，grep `thread_context` 零匹配）。如果 Gateway 改成 `config-only`，必须显式补 `thread_context`。
- Frontend `/api/langgraph` 直连路径当前只传普通 `context` 字段，不经过 Gateway 的 `resolve_thread_context()`，因此也没有可信 `thread_context`。
- `ThreadDataMiddleware._resolve_context` 硬要 `configurable["thread_context"]`（`thread_data_middleware.py:63-69`）：

```python
raw_ctx = cfg.get("thread_context")
if not raw_ctx or not isinstance(raw_ctx, dict):
    raise ValueError(
        "ThreadDataMiddleware: configurable['thread_context'] is required "
        "but missing. ..."
    )
```

这就是用户在网页端直接看到的 `ValueError`：并非"server 静默丢了 configurable"，而是提交入口没有提供 `ThreadDataMiddleware` 需要的可信 `thread_context`。

更准确地说：**Fact A 是 Gateway `/api/runtime` 这条路径的报错源**；**Fact B 是 frontend 直连 `/api/langgraph` 这条路径的报错源**。两者在不同入口各自独立触发，并非串联因果。**`4ae14587` 合入时（2026-04-03），仓库 `pyproject.toml` 已 pin `langgraph>=1.0.6`（`c2a62a22`, 2026-01-14），且 `langgraph-api` 已被 PR #984（`3a5e0b93`, 2026-03-06）升至 0.7.x、HTTP 层互斥校验生效 28 天。双通道在合入当天就是非法的**，没炸只是因为主聊天走 `/api/langgraph` 直连代理绕过了 Gateway `runtime.py`，Gateway HTTP 路径缺少真实冒烟覆盖，trap 被延迟触发到 2026-04-21 SSO 真实链路联调。

### 2.2 两种错误文字的归属

| 观察点 | 归因 | 错误文字 |
|---|---|---|
| 直连 SDK / 进程内 TestClient 经过 SDK 的路径 | **事实 A**（互斥 400） | `BadRequestError: Cannot specify both configurable and context` |
| Gateway 侧 SDK 异常被脱敏后暴露给 HTTP | **事实 A** | `503 "Runtime execution failed"` |
| 网页端 `/api/langgraph` 直连 / 其它缺少 `thread_context` 的单通道提交 | **事实 B**（可信 `thread_context` 缺失） | `ValueError: configurable['thread_context'] is required` |

修复必须**同时**覆盖两个事实，漏掉任何一个都会在某条路径上继续炸。

### 2.3 为什么当前代码要同时传两条通道（历史正当性）

**不是冗余，是喂两个不同时机的读者**：

| 通道 | 读者 | 读取时机 | 为什么不能换通道 |
|------|------|----------|------------------|
| `RunnableConfig.configurable` | `make_lead_agent()` 和 `_build_middlewares()` | **agent build time**（graph 还没开始跑） | build time 早于任何 middleware，此时 `Runtime.context` 还没注入，**无法读**（见 §7.8 关键结论 3 的实证） |
| `Runtime.context` | `uploads_middleware` / `memory_middleware` / `sandbox/*` / `task_tool` / `setup_agent_tool` | **graph runtime**（middleware / tool 执行中） | 历史上这些中间件就用 `runtime.context.get(...)`，当时两条通道都可用 |
| `RunnableConfig.configurable["thread_context"]` | `ThreadDataMiddleware._resolve_context` | **graph runtime** | 4-8 的 isolation 改造后强制走 configurable，硬性检查 |

**历史正当性的适用范围**：LangGraph 0.x 时代"两条都塞"确实是正当做法，因为 build time 和 runtime 是两个不同的读取时机，各自只能通过各自的通道。**但本仓库并不存在这段"历史"**——`c2a62a22`（2026-01-14）就已把 `langgraph>=1.0.6` 钉上，仓库从未在 0.x 下运行过双通道代码。`2217e588`（2026-04-02）首次落地时也是**单通道 context-only**，这是正确的 1.x 写法；`4ae14587`（2026-04-03）在次日**主动**加上 configurable 第二通道，等于在 1.x 的地基上重建了一个 0.x 风格的双通道，且未验证 `langgraph-api` HTTP 层行为。

因此本节的"双通道历史正当性"只用于解释**为什么两条通道会分别被 build-time 与 runtime 读者依赖**（读法问题），不构成**同时写入两条通道**在本仓库的合法性依据（写法问题）。

---

## 3. 历史时间线（Timeline）

通过 `git log -p --follow backend/src/gateway/runtime_service.py` 和 `git log -- backend/pyproject.toml` 重建：

### 2026-01-14 — `c2a62a22 chore: add Python and LangGraph stuff`（Henry Li）
- `backend/pyproject.toml` 首次 pin `langgraph>=1.0.6`
- **从这一天起仓库就在 LG 1.x 上运行**，LG 0.x 的"双通道合法"时代与本仓库无关

### 2026-03-06 — `3a5e0b93 fix(backend): upgrade langgraph-api to 0.7 and stabilize memory path tests (#984)`（Willem Jiang）
- 把 `langgraph-cli[inmem]>=0.4.11` 替换为 `langgraph-api>=0.7.0,<0.8.0` + `langgraph-cli>=0.4.14` + `langgraph-runtime-inmem>=0.22.1`
- `langgraph-api` 0.7.x 开始在 **HTTP 层**强制 `configurable` 与 `context` 互斥，违反直接返回 400
- PR #984 本身是合规升级，不引入双通道代码，未触发 Bug

### 2026-04-02 — `2217e588 feat(runtime): add platform runtime adapter`
- 作者：DESKTOP-6NA1GH5\admin
- runtime adapter 首次落地
- `client.runs.stream(...)` **只传** `context=context`
- 当时还没有 `configurable` 块

```python
# 原始提交的 start_stream 片段
async for chunk in client.runs.stream(
    thread_id,
    ENTRY_GRAPH_ASSISTANT_ID,
    input=input_payload,
    config={"recursion_limit": 1000},
    context=context,        # ← 唯一通道
    stream_mode=["values", "messages"],
    multitask_strategy="reject",
):
    ...
```

### 2026-04-03 — `4ae14587 feat(multi-tenant): implement absolute tenant isolation ...`（根因提交）
- 作者：DESKTOP-6NA1GH5\admin（本地提交）
- **在昨天（`2217e588`）刚落地的正确单通道写法上，主动追加了 configurable 第二通道**
- 注释原话：
  > *"Inject identity into configurable so that make_lead_agent() can read
  > tenant_id/user_id at Agent **build time** (before any middleware runs)."*
- **此时仓库状态**：
  - `langgraph>=1.0.6` 已 pin 80 天（自 `c2a62a22`）
  - `langgraph-api>=0.7.0` 已 pin 28 天（自 `3a5e0b93`）
  - **HTTP 层互斥校验早已生效**，双通道在本仓库从未合法过
- **为什么当天没炸**：PR 覆盖 Skills / MCP / Extensions / Policy / RBAC 多个层面（标题即证），Gateway `/api/runtime` HTTP 路径没有端到端冒烟测试；主聊天走 `/api/langgraph` 直连代理，不经过 `runtime.py`；此后主干没人真实以 HTTP 形式打过 Gateway runtime endpoint
- **定性**：扳机与根因为同一笔提交。并非"旧代码被新版本淘汰"，而是"在已知强校验的版本上加入不合规的双通道写法且未做 HTTP 层冒烟"

```diff
+    # Inject identity into configurable so that make_lead_agent() can read
+    # tenant_id/user_id at Agent *build* time (before any middleware runs).
+    run_config: dict = {"recursion_limit": 1000}
+    configurable: dict = {}
+    for key in ("thread_id", "tenant_id", "user_id"):
+        value = context.get(key)
+        if value:
+            configurable[key] = value
+    if configurable:
+        run_config["configurable"] = configurable
```

### 2026-04-08 — `9ee15678 feat(isolation): implement tenant-user-thread workspace ...`
- `ThreadDataMiddleware._resolve_context` 固化为**只**从 `config.configurable["thread_context"]` 读取，不回落到 `runtime.context`
- 加重了 configurable 一侧的权重，但依赖结构没变

### 2026-04-20 — `c66fd9f7 feat(sso): moss-hub ticket SSO backend with identity guard`
- SSO 分支对 runtime_service.py 的**全部改动**：**+6 行**，把 `auth_user` 塞进已有的 configurable 字典
- **未改变 `config=... context=...` 的调用结构**
- SSO 单测用 `fastapi.testclient`，根本不摸 LangGraph 上游 → 单测全绿
- 任意时刻 `langgraph>=1.0.6` 被安装（pyproject 里已 pin），Bug 立即触发

### 2026-04-21 — 首次触发
- 用户要求跑 SSO 真实链路全流程压测
- 启动 `langgraph dev`（LG 1.x）+ Gateway
- Phase 3 stream 阶段炸掉
- 直连 SDK 复现 → 定位为互斥冲突

---

## 4. 为什么 Bug 延迟 18 天才被发现

注意：**问题不是"SSO 分支引入的"**——`c66fd9f7`（2026-04-20）SSO 提交只是把 `auth_user` 塞进已有的 configurable 字典，没改变通道结构。真正的根因提交是 18 天前的 `4ae14587`（2026-04-03）。之所以延迟暴露，有四层原因：

1. **根因提交（`4ae14587`）自身缺少 HTTP 层冒烟**：当天 PR 范围覆盖 Skills / MCP / Extensions / Policy / RBAC 多个层面，双通道 diff 混在数千行多租户改造里；合并流程没有"动了 runtime 参数装配就必须跑 langgraph dev 冒烟"的强制门禁，单测全部基于 `fastapi.testclient`，不经过 `langgraph_sdk` 的 HTTP submit，互斥校验无从触发。
2. **Gateway `/api/runtime/*` 是冷路径**：主聊天 / intervention resume / governance resume 当时全走前端直连 `/api/langgraph` 代理，不经过 Gateway `runtime.py`。双通道代码虽然合入主干，但日常开发几乎不会走到，相当于"埋了但没人踩"。
3. **`/messages:stream` 的现有测试走 mock**：`tests/test_runtime_router.py` 里 `create_thread` / `start_stream` 都被 patch 成假数据，没有真打过 `langgraph-api` server，所以 trap 在 CI 永远触发不了。
4. **SSO 联调是第一次真实用户视角打 Gateway runtime endpoint**：`c66fd9f7` 合入后，`backend/scripts/sso_e2e_smoke.py` 的 Phase 3 首次真实发起 `POST /messages:stream`，trap 在 2026-04-21 被触发——不是 SSO 带来了 bug，而是 SSO 是第一个真实走 Gateway runtime HTTP 路径的功能。

**教训**：

- **最关键**：动 LG 运行时参数装配（`config` / `configurable` / `context`）的变更，必须单独成 PR，不得混入租户 / 权限 / MCP 等宽域改造；且必须在本地起 `langgraph dev` 跑一次真实 `/messages:stream` 才算过关。单用 `fastapi.testclient` 永远发现不了 SDK / HTTP 层的问题。
- **CI 触发条件按路径**：凡碰 `gateway/routers/runtime.py`、`gateway/runtime_service.py`、`subagents/executor.py` 的 PR 强制跑 LG 真实链路冒烟，**不以依赖变更为触发条件**（`4ae14587` 没升依赖照样埋雷）。
- **Gateway HTTP 路径必须有 always-on 冒烟**：不能等到某个功能分支（本次是 SSO）恰好走到才发现，否则下一次埋雷仍会延迟数周才暴露。

---

## 5. 影响面分析（Blast Radius）

### 5.1 功能影响

- **网页端对话**：100% 不可用，任意消息都失败
- **外部平台对接** (`/api/runtime/*`)：流式消息接口全部失败，仅建 thread 可用
- **SSO 登录本身**：**完全不受影响**。用户能正常登录、拿 cookie、看到自己的资源列表
- **Agent 管理 / MCP / 模型列表 / 治理审计等所有非流式 API**：不受影响

### 5.2 数据安全

- **无数据泄漏风险**：错误发生在 agent 执行前，不会产生错误的工具调用、不会出假身份
- **不会污染 USER.md / audit ledger**：流式调用走不到那些写入点

### 5.3 数据隔离不变量（修复时必须保持）

本问题最早引入双通道，是为了让 **agent build time** 与 **runtime middleware/tool time** 都能拿到同一份 tenant/user/thread 身份。修复时不能只以“消除 LangGraph 互斥”为目标，还必须保持下面这些隔离不变量：

1. **`thread_context` 必须来自可信后端**：只能由 Gateway 在通过 `resolve_thread_context(thread_id, tenant_id, user_id)` 后生成，不能由浏览器、外部平台请求体、前端 local settings 直接伪造。
2. **`auth_user` 必须来自鉴权中间件**：只能由 `get_user_profile()` 从 OIDC / SSO request state 构造，不能由客户端提交。
3. **路径隔离继续以 `ThreadContext` 为准**：`ThreadDataMiddleware` / `SandboxMiddleware` / uploads / artifacts 仍必须围绕 `{tenant_id, user_id, thread_id}` 计算路径，不能回退到未验证的裸 `tenant_id/user_id/thread_id`。
4. **跨租户/跨用户仍 fail-closed**：未知 thread、tenant mismatch、user mismatch 在 Gateway HTTP 层仍返回 403；OIDC/SSO 开启时缺失用户身份仍返回 401/403。
5. **dev / embedded 模式可以保留 configurable 单通道作为本地开发路径，但不能影响 auth 模式**：`DeerFlowClient` 这类进程内调用可以保留单租户开发便利，但 OIDC/SSO 开启时不能因为 fallback 退回 `default/anonymous`。

### 5.4 明确不能采用的“修复”

- **不能让前端自己补 `thread_context` / `auth_user`**。这会把隔离根信任交给浏览器，等价于允许客户端声明自己属于哪个 tenant/user/thread。
- **不能让 `ThreadDataMiddleware` 无条件从裸 `runtime.context["tenant_id"]` / `["user_id"]` 拼路径**。只有经过 Gateway ownership validation 后序列化的 `thread_context` 才是可信身份载体。
- **不能只修 `/api/runtime` Gateway 路径就宣称网页端恢复**。当前前端主聊天默认走 `/api/langgraph` 直连 LangGraph，不经过 Gateway 的 `resolve_thread_context()`。
- **不能把 LangGraph 版本降级作为长期方案**。降级绕开互斥校验，但不会解决前端直连缺少可信 `thread_context` 的隔离问题。

### 5.5 绕行方案（不修代码的临时止血）

- 将 `backend/pyproject.toml` 的 langgraph pin 降级到 `>=0.5,<0.6`（需要同步验证 `langgraph-api`、`langgraph-cli`、`langgraph-runtime-inmem` 兼容版本 —— 非 trivial，大概率引发其它回归）
- **不推荐**。正修比降级风险小。

---

## 6. 涉及的代码位置（Fix Surface）

### 6.1 发送侧（所有运行提交入口）

完整清单（AST + grep `context=` / `config=` / `runs.create` / `thread.submit` 得到）：

| 文件:行 | 调用形态 | 触发路径 | 风险类型 |
|---|---|---|---|
| `backend/src/gateway/runtime_service.py:287-295` | SDK `client.runs.stream(config=..., context=...)` | 外部平台 / Gateway `/api/runtime/threads/{id}/messages:stream` | **互斥 400**；且当前 configurable 缺 `thread_context` |
| `backend/src/gateway/runtime_service.py::stream_message` | 旧版包装，最终调用 `start_stream` | 历史兼容 | 跟随 `start_stream` 修复 |
| `frontend/src/core/threads/hooks.ts:892-929` | `thread.submit(config={recursion_limit}, context={...})` | 网页普通聊天 / agent 聊天 / bootstrap agent | 不撞互斥，但**缺可信 `thread_context/auth_user`** |
| `frontend/src/components/workspace/messages/intervention-card.tsx:377-404` | `thread.submit(config={recursion_limit}, context={...})` | Intervention resume | 不撞互斥，但缺可信 `thread_context/auth_user` |
| `frontend/src/core/governance/utils.ts:231-235` | `client.runs.create(... payload{config, context})` | Governance resume | 不撞互斥，但缺可信 `thread_context/auth_user` |
| `backend/src/client.py:354` | 进程内 `self._agent.stream(state, config=..., context=..., ...)` | DeerFlowClient 原生调用路径 | 双通道兼容风险；configurable 有 `thread_context` |
| `backend/src/subagents/executor.py:265` | 进程内 `agent.stream(state, config=run_config, context=context, ...)` | **task_tool / 子 agent 执行路径** | 双通道兼容风险；configurable 缺 `thread_context` |

其中：

- `runtime_service.py` 走 LangGraph SDK / server → 必撞 **事实 A** 400。
- 前端直连 `/api/langgraph` 默认不经过 Gateway，当前不会因为 `configurable` 非空而撞互斥，但会在 agent 内部因为缺 `configurable["thread_context"]` 失败；即使让前端补字段也不安全。
- 进程内 `agent.stream(...)`（`client.py` / `subagents/executor.py`）经 §7.8.1 实证**不会撞 LG 1.x 互斥 400**、**也不会做通道镜像**。本轮**不改通道模型**，仅需对 `SubagentExecutor` 在 `run_config.configurable` 里补齐父 run 的 `thread_context`（详见 §7.4）；`DeerFlowClient` 本轮不改动。

### 6.2 读取侧（需要跟随调整）

所有读 `runtime.context` 的位置：

```
backend/src/agents/middlewares/uploads_middleware.py    line 137-145
backend/src/agents/middlewares/memory_middleware.py     line 135-138
backend/src/agents/middlewares/thread_data_middleware.py line 63-69  (已强制 configurable)
backend/src/sandbox/middleware.py                        line 88
backend/src/sandbox/tools.py                             line 176
backend/src/tools/builtins/task_tool.py                  line 77-80
backend/src/tools/builtins/setup_agent_tool.py           line 27
```

### 6.3 隔离相关可信边界

| 边界 | 当前实现 | 修复要求 |
|---|---|---|
| HTTP 身份来源 | `OIDCAuthMiddleware` / SSO session → `request.state` → `get_tenant_id/get_user_id/get_user_profile` | 继续由 Gateway 生成，不接受客户端传入 |
| Thread ownership | `resolve_thread_context(thread_id, tenant_id, user_id)` 查 `ThreadRegistry`，未知/mismatch 返回 403 | 所有受保护运行提交必须先过这一步 |
| Graph 身份载体 | `thread_context = ctx.serialize()` | 必须进入单一 LangGraph 通道，并最终出现在 `config.configurable["thread_context"]` |
| Tool 身份守卫 | `auth_user` → `identity_guard.wrap_tools()` | 必须由 Gateway 鉴权态生成并随 run 传入，不能由浏览器伪造 |
| 前端直连 `/api/langgraph` | Nginx 直接代理到 LangGraph server | 认证/多租户模式下不能继续作为可信运行提交入口，除非中间加 Gateway 注入/校验层 |

---

## 7. 推荐修复方案（保持 tenant/user 隔离）

### 7.1 结论：分层修，不允许客户端补身份

这次修复不能只以“消除 LangGraph 1.x 400”为目标，还必须保持 `tenant_id/user_id/thread_id` 的可信来源不变：

- **Gateway `/api/runtime` 路径**：推荐改成 `context-only`。`context` 必须由 Gateway 在认证后通过 `resolve_thread_context()` 生成，包含 `thread_context`、`tenant_id`、`user_id`、`thread_id`、`auth_user` 等字段；`config` 只保留 `recursion_limit`，不再携带 `configurable`。
- **`ThreadDataMiddleware` 权威来源不要弱化**：保持以 `config.configurable["thread_context"]` 作为隔离上下文来源。当前 `langgraph-api 0.7.65` 在只传 `context` 时会把 context copy 到 `config["configurable"]`，Gateway 的 `context-only` 可以满足 middleware/build-time 读取，不需要改成信任任意 `runtime.context`。
- **Frontend `/api/langgraph` 直连路径必须处理可信注入**：网页端现在绕过 Gateway 直接调用 LangGraph，不能通过在浏览器 context 里补 `thread_context/auth_user` 来修；这会把租户/用户隔离边界交给客户端伪造。
- **本地 graph 调用单独处理**：`DeerFlowClient` 和 `SubagentExecutor` 走 `langgraph-core` pregel（非 `langgraph-api` server 路径），经 §7.8.1 实证**不会做 context↔configurable 镜像，也不触发互斥 400**。本轮不改通道数量模型（保持双通道），仅需对 `SubagentExecutor` 在 `configurable` 里补齐当前缺失的 `thread_context` 字段。不能照搬远程 Gateway 的"清空 configurable"方案。

### 7.2 Gateway `/api/runtime` 最小安全修复

修改 `backend/src/gateway/runtime_service.py::start_stream()`：

```python
run_config = {"recursion_limit": 1000}

upstream_iter = client.runs.stream(
    thread_id,
    ENTRY_GRAPH_ASSISTANT_ID,
    input=input_payload,
    config=run_config,
    context=dict(context),
    stream_mode=["values", "messages"],
    multitask_strategy="reject",
)
```

关键点：

- 删除现有 `configurable` 注入块，避免 `config.configurable + context` 同传触发 400。
- 选择 `context-only` 而不是 `config-only`，是因为 LangGraph 官方错误文本已明确提示 `configurable` 是 deprecated 方向，长期替代方案是 `context`；在当前 `langgraph-api 0.7.65` 下两种单通道都能镜像，但迁移方向应跟随官方长期语义。
- 不在 `runtime_service` 里重新信任请求体里的身份字段；只使用 router 已经构造好的 `context`。
- 保持 `backend/src/gateway/routers/runtime.py` 现有链路：先认证出 `tenant_id/user_id/auth_user`，再用 `resolve_thread_context()` 校验 thread 所属关系，最后把序列化后的 `thread_context` 放入 context。
- 保持 `ThreadDataMiddleware` 不变，依赖 LangGraph API 的 context→configurable copy 满足 `config.configurable["thread_context"]` 读取。

隔离影响评估：该修复不降低数据隔离强度，因为 `thread_context` 仍然来自 Gateway 端的认证和 registry 校验；只是把同一份可信上下文从“configurable+context 双通道”改成“context 单通道发送，LangGraph API 内部镜像到 configurable”。

### 7.3 Frontend `/api/langgraph` 路径必须补可信边界

当前网页主路径仍然通过 `useStream` / `thread.submit` / `client.runs.create` 直接打到 `/api/langgraph`。这条路径不是 Gateway `/api/runtime`，所以仅修 `runtime_service.start_stream()` 不能覆盖网页主聊天、intervention resume、governance resume。

推荐二选一：

- **优先方案：迁移 UI submit/stream 到 Gateway-owned endpoint**。Gateway 负责认证、thread ownership 校验、注入 `thread_context/auth_user`，再以单通道 payload 转发到 LangGraph；前端不再直接给 LangGraph 提交 run。
- **兼容方案：实现 Gateway `/api/langgraph` 兼容代理**。Nginx 将 `/api/langgraph` 先打到 Gateway，Gateway 对 thread create/register/run submit 做认证和 registry 绑定，覆盖或丢弃客户端提交的 `thread_context/auth_user`，再转发给 LangGraph，并保持现有 SSE/API contract。

禁止采用：

- 禁止让 frontend 自己拼 `thread_context`、`tenant_id`、`user_id`、`auth_user` 后传给 LangGraph。
- 禁止简单把 `ThreadDataMiddleware` 改成优先信任 `runtime.context` 的裸字段；除非该 run 已经保证只来自 Gateway 可信注入路径。
- 禁止只改 Gateway `/api/runtime` 后宣称网页端已修复；当前网页端主要并不走这条入口。

隔离影响评估：只要前端直连被迁移或代理，且 Gateway 覆盖客户端身份字段，这条修复会增强而不是削弱隔离；如果让浏览器补身份，则会引入跨 tenant/user 伪造风险。

#### 7.3.1 决策结论

**采用方案 A（迁 UI 到 Gateway endpoint），不采用方案 B（Gateway `/api/langgraph` 兼容代理）。**

判定维度：

1. **单一可信边界**：方案 A 只保留"前端 → Gateway"一个入口，trust boundary 收敛在 Gateway 本身；方案 B 让浏览器仍可直达 `/api/langgraph`，Gateway 只做字段覆盖/拦截，拦截漏一个就穿透，多一条需要长期对齐 LangGraph server API 演进的代理链路。
2. **与底层 runtime 解耦**：方案 A 对前端暴露的契约是平台自有的 `/api/runtime/v1`，Gateway 内部可自由替换/升级 `langgraph-api` 或改走 pregel；方案 B 把 `langgraph-api` 的 URL shape / SSE 协议 / assistant id 语义固化成对外契约，每次 LG 升级都要同步改代理。
3. **跨切面治理**：审计 / 配额 / 安全拦截 / identity_guard 注入 / 速率限制等都天然发生在 Gateway 路由层；方案 A 直接享用现成 middleware 栈，方案 B 需要在代理层再实现一套或做双写。
4. **复用资产**：Gateway 已有 `iter_events` / `stream_response` SSE 实现和 `resolve_thread_context()` / `get_user_profile()` 的可信注入链，前端迁移基本是 payload 改写 + 事件名对齐，不是重写。
5. **前端改造成本有限**：直连 submit/stream 入口只有 3 处（`threads/hooks.ts`、`intervention-card.tsx`、`governance/utils.ts`），迁移工作量可控，远小于长期维护 `/api/langgraph` 兼容代理。

实施路径：

- **Phase 0**（本修复周期，配合 P0-1 / P0-2 一起发版）：
  - **P1-2a** 先补 thread binding 创建/注册路径（前端先调 `POST /api/runtime/threads` 或 Gateway 新增 `:adopt` endpoint），避免迁移后首条消息 403；
  - **P1-2** 只迁 **主聊天 submit** 到 `/api/runtime/threads/{id}/messages:stream`；
  - intervention resume / governance resume **保持现状走 `/api/langgraph`**，因为现有 `MessageStreamRequest` 不承载 checkpoint / command / workflow_resume_* 字段，强迁会造成行为回归。
- **Phase 1**（下个迭代，**P1-2b**）：Gateway 新增 resume / interrupt 专用 endpoint（如 `/api/runtime/threads/{id}/resume`、`/api/runtime/threads/{id}/governance:resume`），承载 resume 必需字段；前端 `intervention-card.tsx` / `governance/utils.ts` 切换，彻底下线 `/api/langgraph` 直连。
- **Nginx**：`/api/langgraph` 在 Phase 1 完成后限定为 Gateway 内部访问或下线，不再向浏览器暴露。

隔离影响评估：方案 A 落地后，`thread_context` / `auth_user` 完全来自 Gateway 可信注入，浏览器端无法再伪造身份；Phase 0 已覆盖主聊天与 resume 的绝大部分风险窗口，Phase 1 收尾后彻底关闭直连入口。

### 7.4 本地 `DeerFlowClient` 与 subagent 路径

`backend/src/client.py` 和 `backend/src/subagents/executor.py` 是进程内 graph 调用。经 §7.8.1 实证，本地 `langgraph.pregel` **双向都不做 context↔configurable 镜像**，且**不强制互斥 400**。因此：本地路径紧急度为 0，保留现有双通道写法即可；"修"的唯一动作是在 `SubagentExecutor` 里补齐 `configurable["thread_context"]` 字段（当前缺失，见 §6.1 `executor.py:265` 行备注）。不能直接按 Gateway 远程方案"清空 configurable"——L-B 探针已证明那样会直接触发 `ThreadDataMiddleware` 的 `ValueError`。

处理建议：

- `DeerFlowClient`：保留 `config.configurable["thread_context"]` 作为本地开发/嵌入式调用的兼容路径；如果未来要统一单通道，必须先给所有 `runtime.context` 消费方补从 `runtime.config` / `get_config()` 读取的 helper，再做迁移。
- `SubagentExecutor`：必须把父 run 的 `thread_context` 传入子 agent 的 `run_config.configurable`，并同步传递 `tenant_id/user_id/thread_id/auth_user`。否则子 agent 自己创建的 `ThreadDataMiddleware` 仍会拿不到隔离上下文。
- `task_tool`：当前从 `runtime.context` 取 `thread_id/tenant_id/user_id`、从 `runtime.config.configurable` 取 `auth_user`。如果子 agent 仍使用双来源，必须确保两侧字段一致；如果后续统一 helper，应以父 run 的可信 `thread_context` 为准，不能凭空合成租户/用户。

隔离影响评估：本地路径不是外部浏览器信任边界，但它会影响 task tool / subagent 对租户工作区、上传文件、sandbox、memory 的选择。修复时必须让子 agent 继承父 run 的可信 `thread_context`，否则会出现上下文缺失或落到错误默认空间。

### 7.5 回归验证矩阵

必须覆盖以下验证，才可以认为修复没有破坏数据隔离或原有功能：

- **Gateway SDK kwargs**：单测断言 `start_stream()` 调 `client.runs.stream` 时 `config` 不含 `configurable`，`context` 含 `thread_context/auth_user/tenant_id/user_id/thread_id`。
- **隔离防线**：跨 tenant/thread/user 的 runtime 请求仍然 403；auth 开启且 user 缺失仍然 401/403；uploads/artifacts/sandbox/memory 仍落到对应 tenant/user/thread 空间。
- **Frontend 路径**：网页主聊天、agent chat、intervention resume、governance resume 不再绕过可信注入；如走代理，验证客户端伪造的 `thread_context/auth_user` 会被 Gateway 覆盖或拒绝。
- **Build-time 配置**：`make_lead_agent`、planner、semantic router、executor override 仍能读到 `mode`、`agent_name`、`allowed_agents`、`requested_orchestration_mode` 等原配置字段。
- **Runtime 工具链**：`uploads_middleware`、`memory_middleware`、`sandbox`、`task_tool`、`setup_agent_tool` 仍能取到原先依赖的字段。
- **Subagent 链路**：主聊天触发 `task_tool` 后，子 agent 的 `ThreadDataMiddleware` 能解析父 run 的 `thread_context`，且子 agent 文件/memory/sandbox 操作仍在同一 tenant/user/thread 隔离域内。
- **本地 agent.stream 探针**：本次已在当前依赖版本（`langgraph 1.0.x` / `langgraph-api 0.7.65`）下完成（§7.8 / §7.8.1）。**仅当升级 `langgraph` 或 `langgraph-api` 大版本时**，重跑 `probe_lg_channels.py` + `probe_local_pregel.py` 验证镜像与互斥行为未变。
- **身份约束**：`identity_guard` / `auth_user` 相关检查仍从可信后端注入的数据读取，不接受浏览器伪造身份。

### 7.6 更新后的工作量估算

- Gateway `/api/runtime` 最小修复：**小**，主要是 `runtime_service.start_stream` 和对应单测。
- Frontend 直连治理：**中**，按 §7.3.1 已定的方案 A 落地——Phase 0 只改 3 处 submit/stream 切到 Gateway `/api/runtime` endpoint；Phase 1 Gateway 补 resume 专用 endpoint 后下线 `/api/langgraph` 直连。
- Subagent `thread_context` 字段补齐：**小**，只在 `SubagentExecutor` 构造 `run_config.configurable` 时注入父 run 的 `thread_context`，加 1 个单测 + 1 个集成回归。不涉及通道模型重构。
- `DeerFlowClient`：**本轮 0 改动**。
- 验证：需要覆盖 Gateway 单测、SSO/OIDC 隔离测试、网页主链路冒烟、intervention/governance resume、task_tool/subagent 真实链路。

### 7.7 评审修订记录

第一版文档中的以下判断经评审已被纠正：

| 原判断 | 实际情况 | 修订 |
|---|---|---|
| "LG server 端偏向 context 静默丢弃 configurable" | `langgraph-api 0.7.65` 会直接 400，不存在静默偏向 | §2.1 拆成两个独立事实 |
| "方案 B 代价大，`make_lead_agent` 15+ 处要改" | LG API 只传 context 时会拷贝到 `config.configurable`，Gateway 远程路径 build-time 可保持读取 configurable | §7.2 推荐 Gateway context-only |
| Fix surface 只列 2 个发送点 | 漏了 subagent、本地 client、frontend `/api/langgraph` 直连族 | §6.1 补齐运行提交入口 |
| helper 签名 `get_ctx_field(runtime, config, ...)` | 下游 middleware 方法没有 `config` 参数，实际走 `get_config()` / `runtime.config` | §7.4 将 helper/fallback 限定为本地统一迁移时的可选方案 |
| 只修 Gateway `/api/runtime` 即可覆盖网页端 | 当前网页主路径直连 `/api/langgraph`，不走 Gateway runtime endpoint | §7.3 单独要求迁移或代理前端路径 |
| `ThreadDataMiddleware` 可直接优先信任 `runtime.context` | 对 frontend 直连场景不安全；只有 Gateway 可信注入后的 context 才可作为身份来源 | §7.1 保持 `thread_context` 权威来源不弱化 |

### 7.8 实证验证（2026-04-21，probe_lg_channels.py）

为了把 §7.1 中"LG API 会把 `context` 拷到 `config.configurable`"从推断升级成事实，我们在 `make_lead_agent`（build-time）和 `ThreadDataMiddleware._resolve_context`（runtime）注入了一次性探针，针对同一个 LangGraph `entry_graph`、`langgraph-api 0.7.65` server 跑了 4 个表内变体，直接读 `get_config().get("configurable")` 与 `runtime.context` 两条通道内容。探针代码已经回滚（见 `backend/scripts/_probe_channels.py` 仅保留工具本体，不再被引用）。

| 变体 | 发送方式 | 提交结果 | build 时 `cfg` 可见身份字段 | middleware 时 `cfg` 可见 | middleware 时 `runtime.context` 可见 |
|---|---|---|---|---|---|
| A `config_only` | 只传 `config.configurable`（含 `thread_context/tenant_id/user_id/auth_user`） | 200 | ✅ 全部 | ✅ 全部 | ✅ 全部（LG API 反向镜像） |
| B `context_only` | 只传 `context`（相同字段） | 200 | ✅ 全部（LG API 正向镜像到 configurable） | ✅ 全部 | ✅ 全部 |
| C `both` | `config.configurable` + `context` 双传 | **400 BadRequestError** | — | — | — |
| D `config_only_strip_tc` | 只传 `config.configurable`，但不包含 `thread_context` | 提交被接受；执行期缺 `thread_context` | `tenant_id/user_id/auth_user` ✅，`thread_context` ❌ | 同上 | 同上 |

关键结论：

1. **Fact A 验证**：双通道同传在 `langgraph-api 0.7.65` 直接返回 400，不是"静默丢弃"。与 §2.1 判断一致。
2. **Context↔Configurable 镜像验证**：LG API 在单通道下会双向镜像：
   - `context-only` → build-time 和 runtime 的 `config.configurable` 都能读到原 `context` 全部字段；
   - `config-only` → runtime 的 `runtime.context` 也能读到原 `config.configurable` 全部字段。
   - 这直接证实了 §7.2 推荐的 Gateway `context-only` 修法不会破坏 `make_lead_agent` / `ThreadDataMiddleware` / identity_guard 等读取端——它们继续从 `configurable` 读，仍然能拿到完整 `thread_context/tenant_id/user_id/auth_user`。
3. **Build-time 独立性**：Variant B 的 build 探针显示 `runtime.context` 在 build 阶段为 `None`（`get_runtime()` 早期拿不到），但 `configurable` 已被填充——说明"从 context 读"的迁移必须等到 runtime middleware 阶段才可行，build-time 仍然只能依赖 `configurable`。这也是为什么方案 B 安全：LG API 早在 build 之前就把 context 灌进了 configurable。
4. **Fact B 反证**：Variant D 显示，如果 `thread_context` 两条通道都不带，middleware 会如期触发 `ValueError: configurable['thread_context'] is required but missing`——与网页端报错现象一致，说明任一提交入口只要没有提供可信 `thread_context` 都会触发 Fact B。
5. **隔离前提不变**：镜像是 LG API 在单通道内部完成的，不是客户端可以绕过的。前端直连 `/api/langgraph` 的风险（§7.3）依旧存在：只要浏览器能自己放 `thread_context/auth_user`，无论放在哪一边都会被 middleware 信任。因此 §7.3 要求的"迁移到 Gateway 或代理"保持不变。

若未来 `langgraph-api` 升级后需要重新验证镜像行为：重新在 `agent.py::make_lead_agent` 和 `thread_data_middleware.py::_resolve_context` 注入探针（参考当前 `backend/scripts/_probe_channels.py`），重启 `langgraph dev`，运行 `python backend/scripts/probe_lg_channels.py`，对比 `backend/.probe_out/*.json`。

基于以上证据，§7.1 的"推荐 Gateway 走 context-only"从推断升级为实证。§7.2 的最小修法成立。

#### 7.8.1 本地 `langgraph.pregel` 镜像行为补充验证（2026-04-21，`probe_local_pregel.py`）

针对 §7.4 "本地 pregel 不镜像 context → configurable" 这条原本只来自代码阅读的前提，另起一轮**进程内** `build_entry_graph(...).astream(state, config=..., context=...)` 探针，`langgraph 1.0.x` + `langgraph-runtime-inmem 0.26.0`，**无 HTTP server，纯本地 pregel**，跑了 3 个变体：

| 变体 | 发送方式 | 通道校验结果 | build 时 `cfg` | middleware 时 `cfg` | middleware 时 `runtime.context` |
|---|---|---|---|---|---|
| L-A `config_only` | 只传 `config.configurable` | 通过（后续模型调用失败与通道无关） | ✅ 全部身份字段 | ✅ 全部 | **`None`** |
| L-B `context_only` | 只传 `context` | **`ValueError: configurable['thread_context'] is required but missing`** | — | ❌ 只有 `__pregel_*` 内部字段 | ✅ 全部 |
| L-C `both` | `config.configurable` + `context` 同传 | **通过，不抛 `BadRequestError`** | ✅（来自 configurable） | ✅ | ✅ |

三条硬结论：

1. **本地 pregel 双向都不做镜像**。L-A 的 `runtime.context` 全程 `None`；L-B 的 `configurable` 里只有 pregel 内部字段，完全没有调用方传入的 `tenant_id / user_id / thread_context / auth_user`。
2. **Fact A（互斥 400）只存在于 `langgraph-api` server 层**，不是 `langgraph-core` pregel 的行为。L-C 在本地顺利放行。
3. **`DeerFlowClient` / `SubagentExecutor` 不受 Fact A 威胁**。本地路径保留双通道不会撞 400，不存在与 Gateway 远程路径同等的紧急度。

对 §7.4 修法的影响：

- **"必须保留 `configurable["thread_context"]`" 仍然成立且必要**——L-B 证实在本地 pregel 下，单靠 `context` 根本走不通 `ThreadDataMiddleware`，因为没有镜像；`configurable` 这一路是 middleware 唯一的身份来源。
- **但"本地路径紧急整改"不成立**——本地保留现有双通道写法既不违反 LG 1.x，也不破坏读取侧。**最佳策略：本地 `DeerFlowClient` / `SubagentExecutor` 本修复周期内不迁移通道模型**。这里"不动"的准确语义是：
  - ✅ **不改通道数量**：继续保留 `config.configurable + context` 双通道写法；
  - ✅ **仍需补齐缺失字段**：`SubagentExecutor` 当前 `configurable` 里没有 `thread_context`（§6.1 已标注），这会导致子 agent 的 `ThreadDataMiddleware` 抛 `ValueError`——本轮必须把父 run 的 `thread_context` 透传到子 run 的 `configurable` 里。这是"补字段"不是"迁通道"。
  - ❌ 如果未来统一迁 context-only，必须先给所有 `configurable` 读取方（`ThreadDataMiddleware` / `make_lead_agent` build-time / `identity_guard` / 其他）补上从 `runtime.context` 读的路径，再迁移本地发送侧，属于独立重构，不在本修复范围内。

至此 §7.4 的"本地不能照搬 Gateway 修法" 从代码阅读结论升级为实证结论。

若未来需要重新验证本地镜像行为：重新注入 `backend/scripts/_probe_channels.py` 的探针到 `agent.py::make_lead_agent` / `thread_data_middleware.py::_resolve_context`，运行 `python backend/scripts/probe_local_pregel.py`，对比 `backend/.probe_out/LOCAL*.json`。

### 7.9 附加动作（可选但建议）

- [ ] 把 `_sanitize_error` 加一个 `GATEWAY_DEBUG_LG_ERRORS=true` 开关，调试态下原样透传异常文本。本次定位花了额外 10 分钟在 SDK 直连复现上就是因为脱敏吞掉了关键信息。
- [ ] 在 `backend/pyproject.toml` 的 langgraph pin 注释处写一笔"双通道互斥已从 0.6 起生效，`langgraph-api` 0.7.x HTTP 层强制校验"，避免下次有人又同传。
- [ ] `test_runtime_router.py` 增加一个集成用例，断言 `start_stream` 构造的参数里 `context` 和 `configurable` 不同时出现，并且 `context.thread_context` 来自 Gateway 解析结果。
- [ ] **CI 门禁按路径而非按依赖触发**：任何修改 `backend/src/gateway/runtime_service.py` / `backend/src/gateway/routers/runtime.py` / `backend/src/subagents/executor.py` / `backend/src/client.py` 的 PR，强制跑一次启动 `langgraph dev` 的真实 HTTP 冒烟（至少一轮 `create thread → messages:stream → run_completed`）。本次事故证明仅盯依赖升级（`pyproject.toml`）不够——`4ae14587` 未动依赖照样埋雷。
- [ ] **PR 拆分规范**：在 `CONTRIBUTING.md` 或 MR 模板里加硬规矩——动 LG 运行时参数装配（`config` / `configurable` / `context`）的变更必须单独成 PR，不得混入租户 / 权限 / MCP / Skills 等宽域改造。`4ae14587` 把双通道 diff 混进多层改造的教训。

---

## 8. 建议的修复归属

- **不要合进 SSO 分支** —— 既不是 SSO 回归，修法也涉及 5-6 个与 SSO 无关的中间件/工具
- 建议新建 `fix/runtime-lg1x-trusted-context-submit` 分支
- MR 标题建议：`fix(runtime): preserve tenant context when submitting LangGraph runs`
- MR 关联 commit：`c2a62a22`（2026-01-14 pin `langgraph>=1.0.6`）/ `3a5e0b93` PR #984（2026-03-06 升 `langgraph-api` 至 0.7.x，HTTP 层互斥生效）/ `2217e588`（2026-04-02 正确的单通道实现）/ **`4ae14587`（2026-04-03 根因：在互斥生效 28 天后加入第二通道，且未跑 Gateway HTTP 冒烟）** / `9ee15678`（2026-04-08 固化 configurable 路径）
- MR 描述需显式说明：**本 PR 修复 `4ae14587` 引入的双通道写入。`langgraph-api 0.7.x` 的 HTTP 层互斥校验自 2026-03-06 起生效，双通道代码于 2026-04-03 合入时即违反该约束，仅因 Gateway `/api/runtime` HTTP 路径长期无真实冒烟、且主聊天走 `/api/langgraph` 直连而延迟至 2026-04-21 SSO 联调才暴露。非 SSO 回归，非依赖升级回归。**
- MR 范围不要只包含 `runtime_service.py`：必须同步处理
  - frontend `/api/langgraph` 直连可信边界（按 §7.3.1 Phase 0：**仅迁主聊天 submit**；intervention/governance resume 留到 Phase 1 的专用 endpoint）；
  - Phase 0 前置 **thread binding 创建/注册**（前端先调 `POST /api/runtime/threads` 或 Gateway 新增 `:adopt` endpoint），否则迁过去首条消息即 403；
  - subagent 在 `run_config.configurable` 补齐 `thread_context / auth_user` 字段，需同步改 `task_tool.py`（提取父 runtime 字段）与 `SubagentExecutor.__init__`（新增参数），仅补字段、不动通道模型；
  - Gateway 单通道回归测试、跨 tenant/user/thread 隔离回归测试。

---

## 9. 附录：关键证据索引

### 9.1 复现命令

```bash
# 全量压测（推荐）
cd backend && .venv/Scripts/python -m scripts.sso_e2e_smoke

# 直连 LangGraph SDK 最小复现
cd backend && .venv/Scripts/python -c "
import asyncio
from langgraph_sdk import get_client
async def main():
    c = get_client(url='http://127.0.0.1:2024')
    t = await c.threads.create()
    async for chunk in c.runs.stream(
        t['thread_id'], 'entry_graph',
        input={'messages':[{'type':'human','content':[{'type':'text','text':'hi'}]}]},
        config={'recursion_limit':1000,'configurable':{'tenant_id':'x'}},
        context={'thread_id':t['thread_id']},
        stream_mode=['values','messages'],
    ): print(chunk)
asyncio.run(main())
"
# → BadRequestError: Cannot specify both configurable and context.
```

Fact B 最小复现（单通道，但缺 `thread_context`）：

```bash
cd backend && .venv/Scripts/python -c "
import asyncio
from langgraph_sdk import get_client
async def main():
    c = get_client(url='http://127.0.0.1:2024')
    t = await c.threads.create()
    async for chunk in c.runs.stream(
        t['thread_id'], 'entry_graph',
        input={'messages':[{'type':'human','content':[{'type':'text','text':'hi'}]}]},
        config={'recursion_limit':1000},
        context={'tenant_id':'x','user_id':'y','thread_id':t['thread_id']},
        stream_mode=['values','messages'],
    ): print(chunk)
asyncio.run(main())
"
# → ValueError: ThreadDataMiddleware: configurable['thread_context'] is required but missing.
```

### 9.2 相关 commit 哈希（均在 `origin/main` / `origin/sso` / `origin/codex/save-multi-agent-work` 上）

- `c2a62a22` — 2026-01-14 Henry Li：`pyproject.toml` 首次 pin `langgraph>=1.0.6`（仓库进入 LG 1.x 时代）
- `3a5e0b93` — 2026-03-06 Willem Jiang（PR #984）：升 `langgraph-api` 至 0.7.x，HTTP 层互斥校验生效
- `2217e588` — 2026-04-02 runtime adapter 首次落地（**正确的单通道 context-only 写法**）
- **`4ae14587`** — **2026-04-03 tenant isolation，在互斥生效 28 天后引入第二条 configurable 通道（根因提交）**
- `9ee15678` — 2026-04-08 ThreadDataMiddleware 固化 configurable 路径
- `c66fd9f7` — 2026-04-20 SSO，仅把 auth_user 塞进已有 configurable（非根因，但首次通过真实 HTTP 链路触发了 trap）

### 9.3 关键文件与行号

| 文件 | 行号 | 作用 |
|---|---|---|
| `backend/src/gateway/runtime_service.py` | 287-295 | 病灶：同传 config+context |
| `backend/src/gateway/runtime_service.py` | 271-284 | configurable 注入块（4-03 引入） |
| `backend/src/agents/middlewares/thread_data_middleware.py` | 44-69 | 强制读 configurable["thread_context"] |
| `backend/src/gateway/thread_context.py` | 58-103 | Gateway 可信 thread ownership 校验 |
| `backend/src/gateway/dependencies.py` | 68-127 | 从 OIDC/SSO request state 生成 tenant/user/auth_user |
| `backend/src/gateway/middleware/oidc.py` | 284-303 | 验证 token/session 后写入 request.state |
| `frontend/src/core/threads/hooks.ts` | 892-929 | 网页主聊天直连 `/api/langgraph` submit |
| `frontend/src/components/workspace/messages/intervention-card.tsx` | 377-404 | Intervention resume 直连 submit |
| `frontend/src/core/governance/utils.ts` | 231-235 | Governance resume 直连 runs.create |
| `backend/src/subagents/executor.py` | 265 | 子 agent 本地 graph stream；本轮需在 `run_config.configurable` 补 `thread_context` 字段（不迁通道模型） |
| `backend/src/client.py` | 354 | DeerFlowClient 本地 graph stream；本轮 0 改动。未来统一通道需先迁移所有 configurable 读取方 |
| `backend/pyproject.toml` | `langgraph>=1.0.6` | 触发升级 |
| `backend/scripts/sso_e2e_smoke.py` | Phase 3 | 复现入口 |

### 9.4 参考沙箱（复现时的状态）

- 临时目录：`C:\Users\admin\AppData\Local\Temp\df_sso_smoke_3fzcm8p_`
  - `tenants/moss-hub/users/u_SZDPE5PRBLTT64H2FF767BPG/USER.md` —— 供给正常
  - `.../auth_audit.jsonl` —— 含 `sso_login` 事件
- LangGraph server：`http://127.0.0.1:2024`（`langgraph dev`）
- Gateway：通过 `fastapi.testclient` 在进程内起

---

## 10. 实施任务清单（派发用）

> 按依赖顺序排列，P0 必须本修复周期完成，P1 属于随 PR 一并落地的测试 / 决策，P2 属于可选加固。

### P0-0  [决策已完成] 前端路线 = 方案 A（见 §7.3.1）
- **结论**：采用方案 A——前端迁移到 Gateway 自有 endpoint，不采用 `/api/langgraph` 兼容代理。
- **依据**：§7.3.1 列出的 5 个判定维度（单一可信边界 / 与 runtime 解耦 / 跨切面治理 / 复用资产 / 改造成本）。
- **下游任务**：P1-2 已不再是决策待定，直接按方案 A Phase 0 / Phase 1 推进。

### P0-1  `fix(runtime)`：Gateway `start_stream` 改 context-only（§7.2）
- **文件**：`backend/src/gateway/runtime_service.py::start_stream()` line 271-295。
- **动作**：删除 `configurable` 注入块（line 271-284），`client.runs.stream(...)` 只传 `context=dict(context)`，`config` 只保留 `recursion_limit`。
- **验收**：跑 `backend/scripts/sso_e2e_smoke.py` Phase 3 从 FAIL 变 PASS。
- **单测**：新增断言 `client.runs.stream` 被调用时 `config` kwarg 不含 `"configurable"` 键，`context` 含 `thread_context/auth_user/tenant_id/user_id/thread_id`。

### P0-2  `fix(subagent)`：透传父 run 的 `thread_context` / `auth_user` 到子 run `configurable`（§7.4 / §6.1 `executor.py:265`）

> 说明：`SubagentExecutor` 本身没有 `runtime` 句柄，也没有 `thread_context / auth_user` 构造参数；能读到父 `runtime.context / runtime.config.configurable` 的位置是 `task_tool.py`。因此本任务是 **两文件成对改动**，单改 executor 改不动。

- **文件 A — `backend/src/tools/builtins/task_tool.py`**（约 line 74-130）：
  - 从父 `runtime.config.configurable` 取 `thread_context`（与现有 `auth_user` 提取并列）。
  - 把 `thread_context` 和 `auth_user` 两个值传入 `SubagentExecutor(...)` 构造。
- **文件 B — `backend/src/subagents/executor.py`**：
  - `__init__`（line 125-158）新增参数 `thread_context: dict | None = None, auth_user: dict | None = None`，赋给 `self.thread_context` / `self.auth_user`。
  - 构造 `run_config.configurable`（line 243-258）时写入 `configurable["thread_context"] = self.thread_context`（若存在）和 `configurable["auth_user"] = self.auth_user`（若存在）。
  - **不改通道数量**：继续保留 `config.configurable + context` 双通道，仅补字段。
- **验收**：新增集成用例——主 agent 触发 `task_tool`，断言子 agent 的 `ThreadDataMiddleware` 解析到的 `ThreadContext` 与父 run 相同；子 agent sandbox / uploads / memory 路径落在同一 `{tenant, user, thread}` 空间；`identity_guard` 能在子 run 继续生效。

### P1-1  `test(runtime)`：Gateway 单通道回归测试
- 在 `backend/tests/test_runtime_router.py` 或新增 `test_runtime_service_channels.py` 中：
  - 断言 `start_stream` kwargs 里 `config` 和 `context` 不同时出现 `configurable`；
  - 断言 `context["thread_context"]` 来自 `resolve_thread_context(...)` 的返回。

### P1-2a  [前端 + Gateway] Thread binding 创建 / 注册（Phase 0 前置，阻塞 P1-2）

> 说明：当前 `frontend/src/components/workspace/chats/use-thread-chat.ts:22` 用本地 `uuid()` 铸 `threadId`；而 Gateway `messages:stream`（`runtime.py:286`）入口强制 `resolve_thread_context(thread_id, tenant_id, user_id)`，未在 `ThreadRegistry` 登记过的 thread 一律 403。直接迁主聊天到 Gateway 必然出现首条消息 403，因此必须先落 thread binding 创建 / 注册路径。

- **二选一（在 PR 内明确拍板）**：
  - **(a) 首次进入新会话时，前端先调 `POST /api/runtime/threads`** 拿 server 铸的 `thread_id`，再 `router.replace('/workspace/chats/{thread_id}')`；`use-thread-chat.ts` 去掉本地 `uuid()` 分支。
  - **(b) Gateway 新增 `POST /api/runtime/threads/{id}:adopt`**（或 `PUT /api/runtime/threads/{id}`）：接受已知 LG thread_id，做 ownership / tenant 校验后写入 `ThreadRegistry`。前端保留 uuid 习惯，仅在第一次 stream 前调一次 adopt。
- **验收**：新会话主聊天首条消息 200，且 Gateway 侧 `ThreadRegistry` 有对应 binding 记录；跨 tenant 提交 adopt 仍 403。

### P1-2  [前端 Phase 0] 仅迁"主聊天 submit"（§7.3.1）
- **文件**：仅 `frontend/src/core/threads/hooks.ts:892-929`（网页主聊天 / agent chat / bootstrap）。
- **动作**：把 `thread.submit(...)` 改为调用 Gateway `/api/runtime/threads/{id}/messages:stream`；前端不再拼 `thread_context` / `auth_user` / `tenant_id` / `user_id`；SSE 事件流对齐 Gateway `iter_events` 契约。
- **显式不在 Phase 0 范围**：
  - `intervention-card.tsx:377-404`（intervention resume）
  - `governance/utils.ts:231-235`（governance resume）

  这两处 **保持现状走 `/api/langgraph`**，直到 P1-2b 提供 resume 专用 endpoint 后再切换。理由：现有 `MessageStreamRequest`（`runtime.py:93-102`）不支持 `checkpoint / command / workflow_clarification_resume / workflow_resume_run_id / workflow_resume_task_id` 等 resume 必需字段，强行迁过去会把 resume 变成普通 new message，造成 intervention/governance 行为回归。
- **前置任务**：P1-2a 必须先落地，否则主聊天迁过去后首条消息 403。
- **验收**：主聊天伪造 `thread_context / auth_user` 的浏览器请求被 Gateway 覆盖或拒绝；主聊天端到端 PASS；浏览器 Network 面板显示主聊天不再直连 `/api/langgraph/*`（intervention / governance resume 仍走 `/api/langgraph` 属预期，留待 P1-2b）。

### P1-2b  [Gateway 后端 + 前端 Phase 1] resume / interrupt 专用 endpoint（§7.3.1）
- **Gateway 后端**：新增 `POST /api/runtime/threads/{id}/resume` 和 `POST /api/runtime/threads/{id}/governance:resume` 专用 request 模型，原样承载 `checkpoint / command / workflow_clarification_resume / workflow_resume_run_id / workflow_resume_task_id` 等字段；内部走已验证的 context-only 单通道。
- **前端**：`intervention-card.tsx` / `governance/utils.ts` 切换到上述专用 endpoint，彻底下线 `/api/langgraph` 直连。
- **Nginx**：Phase 1 完成后将 `/api/langgraph` 对浏览器关闭或限定为 Gateway 内部访问。
- **验收**：前端完全不再直连 `/api/langgraph`；intervention resume / governance resume 端到端 PASS；Nginx 配置回归测试确认外网不可达。

### P1-3  `test(isolation)`：跨 tenant / user / thread 回归
- 跨 tenant 的 runtime 请求仍 403；
- 跨 user 的 thread 访问仍 403；
- auth 开启且 user 缺失仍 401/403；
- uploads / artifacts / sandbox / memory 仍落到对应 tenant/user/thread 空间。

### P2-1  `observability`：`GATEWAY_DEBUG_LG_ERRORS` 开关（§7.9）
- `_sanitize_error` 加环境变量开关，调试态原样透传 LG 异常文本。

### P2-2  `docs`：`pyproject.toml` langgraph pin 注释（§7.9）
- 在 `backend/pyproject.toml` 的 `langgraph>=1.0.6` 行附近加注释："双通道互斥已从 0.6 起生效，勿同传 `configurable` 与 `context`"。

### P2-3  `docs`：本次探针脚本说明
- 把 `backend/scripts/_probe_channels.py` / `probe_lg_channels.py` / `probe_local_pregel.py` 的用途写进 `backend/scripts/README.md` 或对应模块 docstring。

---

## 11. 入口路径 × 产品层对照（快速参考）

| 调用入口 | 经过的 LangGraph 产品 | 本轮修法 |
|---|---|---|
| Gateway `/api/runtime/*` | `langgraph-api` server | P0-1（context-only） |
| Frontend 直连 `/api/langgraph/*`（主聊天 submit） | `langgraph-api` server（Phase 0 迁走） | **P1-2a**（前置 thread binding 注册）+ **P1-2** Phase 0 |
| Frontend 直连 `/api/langgraph/*`（intervention resume / governance resume） | `langgraph-api` server（Phase 1 后下线） | **Phase 0 不动**，等 **P1-2b** Phase 1 的 resume 专用 endpoint |
| `DeerFlowClient` 嵌入式 | `langgraph-core` pregel（进程内） | 不动 |
| `SubagentExecutor` | `langgraph-core` pregel（进程内） | P0-2（仅补 `thread_context` 字段） |
| 单测 `fastapi.testclient` | 都不经过 | 不变（但要补 P1-1） |

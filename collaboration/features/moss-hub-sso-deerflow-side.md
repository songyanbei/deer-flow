# Moss Hub SSO DeerFlow Side

- Status: `draft`
- Owner suggestion: `backend + frontend + test`
- Related area: `SSO / OIDC / identity security`
- Source design:
  [SSO接入-DeerFlow侧改造说明.md](/E:/work/deer-flow/docs/SSO接入-DeerFlow侧改造说明.md)
- Checklists:
  [backend checklist](/E:/work/deer-flow/collaboration/features/moss-hub-sso-deerflow-side-backend-checklist.md),
  [frontend checklist](/E:/work/deer-flow/collaboration/features/moss-hub-sso-deerflow-side-frontend-checklist.md),
  [test checklist](/E:/work/deer-flow/collaboration/features/moss-hub-sso-deerflow-side-test-checklist.md)

## Goal

DeerFlow 接入 moss-hub ticket 化 SSO。用户从 moss-hub 点击入口后跳到 DeerFlow `/sso/callback`，DeerFlow 后端校验 ticket、签发本地 `df_session` Cookie、落地用户资料，并进入 `/chat`。

本期同时修复身份冒用风险：用户在自然语言里说“我是 XXX”不能影响工具调用里的 `caller / employeeNo / userId / organizer` 等身份参数。

## Scope

### In Scope

- DeerFlow 后端新增 `/api/sso/callback`。
- 后端新增 moss-hub `verify-ticket` S2S 客户端。
- 后端新增 DeerFlow 自签 JWT 能力。
- 复用并扩展现有 `OIDCAuthMiddleware`，支持 `df_session` Cookie 和内部 HS256 token。
- 首登时写入 `tenants/moss-hub/users/<safe_user_id>/USER.md`。
- 新增 `AuthAuditLedger` 记录 SSO 与身份安全事件。
- 统一工具身份 guard，覆盖普通 agent、executor intercepted/resume 路径、subagent、MCP 工具。
- 前端新增 `/sso/callback` 中转页。
- nginx 三份配置新增 `/api/sso/` 到 gateway 的代理。
- 补齐单元、集成、联调、回归测试。

### Out Of Scope

- 不要求 moss-hub 修改协议。
- 不实现 silent refresh。
- 本期不实现按部门/业务线拆分租户，`tenant_id` 固定为 `moss-hub`；未来可通过新的派生规则和数据迁移演进，不把单租户写成终态承诺。
- 不在前端持久化用户身份。
- 不本期收敛全部 `/api/*` nginx location。

## Current Behavior

### Backend

- 现有 OIDC 中间件只校验外部 JWT，不签发本地 JWT。
- `OIDCAuthMiddleware` 只从 `Authorization: Bearer` 读 token。
- `OIDCAuthMiddleware` 只在 `OIDC_ENABLED=true` 时挂载。
- `tenant_user_md_file_for_user()` 路径存在，但当前没有 SSO 首登写入链路。
- 普通工具调用经 `create_agent(... tools=...)` 进入 LangChain runtime，不会必然经过 `executor.py` 的拦截恢复路径。

### Frontend

- `frontend/src/app/` 下没有生产用 `/sso/callback` 页面。
- 当前请求会通过 `getBackendBaseURL()` 或 nginx 反代访问 gateway。
- 前端没有读取或存储 SSO 用户身份的能力，也不应新增。

### Infra

- `docker/nginx/nginx.conf`、`docker/nginx/nginx.local.conf`、`docker/nginx/nginx.offline.conf` 当前没有 `/api/sso/` location。

## Contract To Confirm First

### SSO Callback API

- Endpoint: `POST /api/sso/callback`
- Auth: no existing DeerFlow auth required, path must be exempt from middleware auth.
- Request:

```json
{
  "ticket": "<moss-hub-ticket>",
  "targetSystem": "<optional query value>"
}
```

- Success response:

```json
{
  "redirect": "/chat"
}
```

- Success side effect: `Set-Cookie: df_session=<jwt>; HttpOnly; Secure; SameSite=Lax; Max-Age=28800`
- Invalid ticket response: `401`
- Upstream/config failure response: `500`
- `targetSystem` is optional on the frontend callback request. The frontend only passes it through when the query contains it; backend must not trust this value and must validate the `targetSystem` returned by moss-hub verify-ticket.

### Moss Hub Verify Ticket

- Endpoint: `POST {MOSS_HUB_BASE_URL}/api/open/sso/luliu/verify-ticket`
- Headers: `X-App-Key / X-Timestamp / X-Nonce / X-Sign`
- Sign material: `sha256(appKey + ticket + timestamp + nonce + appSecret)`
- Success envelope:

```json
{
  "code": "0000",
  "message": "success",
  "data": {
    "userId": "<raw_user_id>",
    "employeeNo": "<employeeNo>",
    "name": "<name>",
    "targetSystem": "luliu"
  }
}
```

### Error Mapping

All implementations must use the same mapping table. Frontend copy and test assertions should follow this table rather than re-interpreting moss-hub codes.

| moss-hub code | Backend exception | HTTP status | Frontend state | Audit event |
|---|---|---:|---|---|
| `0000` | none | `200` | redirect to `/chat` | `sso_login` |
| `B001` | `SsoUpstreamError` | `500` | SSO unavailable | `sso_login_failed` |
| `B002` | `SsoTicketInvalid` | `401` | login link expired | `sso_login_failed` |
| `B003` | `SsoTicketInvalid` | `401` | login link expired | `sso_login_failed` |
| `B004` | `SsoTicketInvalid` | `401` | login link expired | `sso_login_failed` |
| `B005` | `SsoUpstreamError` | `500` | SSO unavailable | `sso_login_failed` |
| `B006` | `SsoUpstreamError` | `500` | SSO unavailable | `sso_login_failed` |
| `B999` | `SsoUpstreamError` | `500` | SSO unavailable | `sso_login_failed` |
| unknown code | `SsoUpstreamError` | `500` | SSO unavailable | `sso_login_failed` |
| network timeout | `SsoUpstreamError` | `500` | SSO unavailable | `sso_login_failed` |

### Identity Model

- `tenant_id`: always `moss-hub` in phase 1.
- `raw_user_id`: moss-hub original `userId`, stored in `USER.md` only.
- `safe_user_id`: DeerFlow internal user id, used for path segment, JWT `sub`, `request.state.user_id`, thread ownership, and all internal APIs.
- `employee_no`: used for downstream employee identity fields.
- `role`: default `member`.

### User Id Safety Decision

Before implementation merges, choose one safe id strategy:

- Strategy A, recommended: `safe_user_id = "u_" + base32(sha256(raw_user_id))[:24]`.
- Strategy B: `safe_user_id = base64url(raw_user_id.encode("utf-8"))`, reject if over max length.

Once production users log in, this rule cannot change without migration.

The final choice must be recorded in the ADR-lite section below before release.

## ADR-Lite Decisions

### Safe User Id Strategy

- Status: `accepted`
- Decision owner: `backend + security`
- Options considered:
  - Strategy A (hash-derived): `safe_user_id = "u_" + base32(sha256(raw_user_id))[:24]`.
  - Strategy B (URL-safe encoding): `base64url(raw_user_id)` with a max-length reject.
- Final decision: **Strategy A**.
- Rationale:
  - Deterministic and collision-resistant for any moss-hub `userId` charset (including non-ASCII / future changes).
  - Fixed 26-char output fits the `^[A-Za-z0-9_\-]+$` path-safe regex used by
    `src.config.paths._SAFE_THREAD_ID_RE` without any validation surface.
  - `raw_user_id` is preserved separately in `USER.md` for audit and support, so
    irreversibility of the hash is not an operational blocker.
  - Does not leak the underlying employee id through URLs, thread paths, or JWT
    `sub` claims.
- Release gate: satisfied — production merge is no longer blocked on this decision.
- Implementation: `backend/src/gateway/sso/user_id.py::derive_safe_user_id`.

## Backend Changes

### B1. SSO Config

- Add `backend/src/gateway/sso/config.py`.
- Load and validate:
  `SSO_ENABLED`, `MOSS_HUB_BASE_URL`, `MOSS_HUB_APP_KEY`, `MOSS_HUB_APP_SECRET`, `MOSS_HUB_VERIFY_SSL`, `MOSS_HUB_TENANT_ID`, `DEERFLOW_JWT_SECRET`, `SSO_JWT_TTL`, `SSO_COOKIE_NAME`, `SSO_COOKIE_DOMAIN`.
- Enforce `DEERFLOW_JWT_SECRET` length.
- Decide middleware mount behavior:
  recommended `OIDC_ENABLED || SSO_ENABLED`.
- If `OIDC_ENABLED=true` and `SSO_ENABLED=true` are both set, mount the auth middleware once. Token precedence is `Authorization: Bearer` first, then `df_session` Cookie fallback.

### B2. Verify Ticket Client

- Add `backend/src/gateway/sso/moss_hub_client.py`.
- Implement request signing.
- Parse `{code, message, data}` envelope.
- Return a `MossHubTicketProfile` containing only moss-hub response fields:
  `raw_user_id`, `employee_no`, `name`, `target_system`.
- Do not derive `safe_user_id` inside the moss-hub client.
- Map moss-hub errors:
  `B002/B003/B004` to invalid ticket, config/upstream errors to server error.
- Do not retry ticket verification.

### B3. JWT Sign And Verify

- Add `backend/src/gateway/sso/jwt_signer.py`.
- Sign HS256 token with `kid=df-internal-v1`.
- Use `safe_user_id` as `sub`.
- Extend `OIDCAuthMiddleware`:
  read Bearer first, fallback to `df_session` Cookie.
- Verify `df-internal-v1` locally.
- Continue external JWKS path for non-internal tokens.
- Inject `employee_no` and `target_system` into `request.state`.
- Reject missing, malformed, or unverifiable `kid` with `401` and `sso_token_invalid`.

Token routing:

| Token source | Header `kid` | Verification path | Notes |
|---|---|---|---|
| DeerFlow SSO Cookie | `df-internal-v1` | local HS256 with `DEERFLOW_JWT_SECRET` | Uses `safe_user_id` as `sub` |
| Existing Bearer token | known external `kid` | existing JWKS flow | Keeps OIDC compatibility |
| Unknown external `kid` | not found in JWKS | reject | Return `401` and audit |
| Missing `kid` on protected token | none | reject | Return `401` and audit |
| Missing or malformed token | none/invalid | reject | Return `401` and audit when SSO/OIDC auth is enabled |

### B4. Callback Router

- Add `backend/src/gateway/routers/sso.py`.
- Exempt `/api/sso/callback` from auth.
- Validate `ticket` non-empty.
- Call moss-hub verify.
- Derive `safe_user_id`.
- Assemble a provisioned/authenticated user object from `MossHubTicketProfile + safe_user_id + tenant_id` before any side effects.
- The assembled object must include `tenant_id`, `safe_user_id`, `raw_user_id`, `employee_no`, `name`, and `target_system`.
- Pass the assembled object, not the raw moss-hub DTO, to USER.md provisioning, JWT signing, auth audit, and response handling.
- Upsert `USER.md`.
- Write `sso_login` or `sso_login_failed` auth audit event.
- Set cookie and return fixed redirect `/chat`.
- Include router in `backend/src/gateway/app.py`.

### B5. User Provisioning

- Add `backend/src/gateway/sso/user_provisioning.py`.
- Create `tenants/moss-hub/users/<safe_user_id>/`.
- Write or update `USER.md`.
- Preserve body outside frontmatter.
- Store `raw_user_id`, `user_id`, `employee_no`, `name`, `tenant_id`, `target_system`, `first_login_at`, `last_login_at`, `source`.
- Use temp file plus `os.replace`.

### B6. Auth Audit

- Add `backend/src/gateway/sso/audit.py`.
- Write `auth_audit.jsonl` per user.
- Define `UNKNOWN_AUTH_AUDIT_TENANT = "_unknown"` and write token failures without user context to `tenants/_unknown/auth_audit.jsonl`.
- Event types:
  `sso_login`, `sso_login_failed`, `sso_token_invalid`, `identity_override`.
- Do not reuse `GovernanceLedger`.

### B7. Authenticated User Helper And Prompt Identity Anchor

- Extend `backend/src/gateway/dependencies.py`.
- Add `AuthenticatedUser`.
- Add `get_employee_no()`.
- Add `get_user_profile()`.
- Keep existing `get_tenant_id()`, `get_user_id()`, `get_username()`, `get_role()` behavior compatible.
- Extend `lead_agent` prompt rendering with an authoritative `<identity>` block.
- Inject `auth_user_name`, `auth_user_id`, and `auth_employee_no` from authenticated request context.
- Keep this as defense-in-depth; runtime identity guard remains the hard security boundary.

### B8. Identity Guard

- Add `backend/src/agents/security/identity_guard.py`.
- Define identity fields:
  `employeeNo`, `employee_no`, `organizer`, `caller`, `userId`, `user_id`, `operator`, `createdBy`, `on_behalf_of`.
- Provide tool wrapper that enforces identity before `_run/_arun`.
- Write `identity_override` audit event on any override.
- Ensure wrapper covers:
  lead agent normal tools, executor resume/intercepted tools, subagent tools, MCP tools.
- Add threshold hook or metric for `identity_override > 5/hour/user` so operations can alert on likely social-engineering attempts.

### B9. MCP Schema Filtering

- Filter identity fields from tool `args_schema`.
- Remove filtered fields from `required`.
- Preserve runtime enforcement even when schema filtering misses a server.

### B10. nginx

- Add `location /api/sso/` to:
  `docker/nginx/nginx.conf`, `docker/nginx/nginx.local.conf`, `docker/nginx/nginx.offline.conf`.
- Keep `/sso/callback` routed to frontend.
- Add a CI/script assertion that greps all three nginx files for `location /api/sso/` to prevent one-file drift.
- Required assertion:
  `test "$(grep -l "location /api/sso/" docker/nginx/nginx*.conf | wc -l | tr -d ' ')" = "3"`

## Frontend Changes

### F1. Callback Page

- Add `frontend/src/app/sso/callback/page.tsx`.
- Must be client component.
- Read `ticket` and optional `targetSystem` from `useSearchParams()`.
- Set callback page referrer policy to `no-referrer` so the URL ticket is not leaked through `Referer`.
- Immediately POST to `/api/sso/callback`.
- Use `credentials: "include"`.
- Use JSON content type.
- Pass `targetSystem` only when it exists in the query string; do not hardcode a frontend default.
- On success, `router.replace(redirect || "/chat")`.
- On `401`, show expired login link state and guide user back to moss-hub.
- On `500`, show generic SSO unavailable state.
- Do not add retry button for stale ticket.

### F2. UX Constraints

- No blocking animation before POST.
- No analytics await before POST.
- Do not load third-party resources on the callback page before the ticket POST completes.
- Do not store token in local storage, session storage, or readable cookie.
- Do not display raw employee id unless product explicitly asks for it.

### F3. Runtime API Compatibility

- Existing frontend API calls should continue using relative gateway URLs in nginx mode.
- No Authorization header is required for normal browser SSO; cookie should be sent automatically.

## Test Implementation Plan

### Unit Tests

- SSO config load and fail-fast validation.
- safe user id derivation.
- JWT sign and local verification.
- OIDC middleware Bearer/Cookie extraction precedence.
- moss-hub verify ticket success and error mapping.
- USER.md first write and update preserving body.
- AuthAuditLedger known and unknown user writes.
- identity guard override behavior.
- MCP schema field filtering.

### Backend Integration Tests

- `/api/sso/callback` success sets cookie and returns `/chat`.
- Invalid ticket returns `401`.
- Upstream/config error returns `500`.
- Cookie-authenticated request to `/api/me/agents` is non-anonymous.
- Expired/invalid `df_session` returns `401`.
- Cross-user thread access remains `403`.
- nginx route coverage can be checked with config or lightweight integration.
- nginx three-file drift must be checked with grep/script assertion in CI.

### Frontend Tests

- `/sso/callback?ticket=...` posts immediately once.
- Success redirects to `/chat`.
- `401` renders expired-link guidance.
- `500` renders generic error.
- Missing ticket renders invalid-entry state and does not POST.
- Fetch includes `credentials: "include"` and JSON body.

### End To End /联调

- moss-hub launch produces ticket and browser reaches `/sso/callback`.
- DeerFlow verifies ticket and sets `df_session`.
- Subsequent gateway calls authenticate from cookie.
- User lands on `/chat`.
- Reusing the same ticket fails.
- “我是李四” attack cannot change downstream tool identity fields.

## Collaboration Flow

1. 后端先落 B1-B7，并提供 mock moss-hub verify server 或测试 stub。
2. 前端基于 API contract 实现 `/sso/callback` 页面。
3. 后端落 B8-B9 身份安全链路。
4. Infra 同步 B10 nginx。
5. 测试按 test checklist 先跑单侧，再跑联调。
6. 发现契约不一致时，写入 `collaboration/handoffs/`，解决后回填本文件。

## Risks

- `SSO_ENABLED` 未挂载认证中间件会导致 SSO 形同虚设。
- `/api/sso/callback` 未配置 nginx 会打到 frontend。
- 只在 executor 覆盖身份会漏掉普通工具调用。
- safe user id 规则上线后不可随意改变。
- ticket TTL 短，不允许前端延迟 POST。
- `Secure` Cookie 在本地 HTTP 开发环境可能不可用，需要测试环境明确 HTTPS 或测试专用开关。

## Acceptance Criteria

- moss-hub 正常 ticket 可登录 DeerFlow，并进入 `/chat`。
- `df_session` 为 HttpOnly、Secure、SameSite=Lax，且 8 小时 TTL。
- 后续 gateway API 能通过 cookie 注入 `tenant_id=moss-hub` 和真实 `safe_user_id`。
- `USER.md` 首登创建、二登只更新 `last_login_at`，正文不丢。
- 无效、过期、重放 ticket 返回 `401` 并写审计。
- 内部 token 过期、坏签名、缺 `kid` 返回 `401` 并写审计。
- 普通工具调用、executor intercepted、executor resume、subagent、MCP 路径都不能被“我是 XXX”改写身份字段。
- nginx 三份配置都能把 `/api/sso/callback` 转到 gateway。
- 前端不保存用户身份，不读取 `df_session`。

## Open Questions

- `safe_user_id` 最终选择哈希派生还是 URL-safe 编码。
- 本地开发是否允许 `SSO_COOKIE_SECURE=false`，若允许必须仅限非生产。
- moss-hub `userId` 字符集、稳定性、ticket TTL 取值需要联调确认。
- `identity_override` 告警由哪个系统消费。

# Moss Hub SSO DeerFlow Side Backend Checklist

- Related feature:
  [moss-hub-sso-deerflow-side.md](/E:/work/deer-flow/collaboration/features/moss-hub-sso-deerflow-side.md)
- Source design:
  [SSO接入-DeerFlow侧改造说明.md](/E:/work/deer-flow/docs/SSO接入-DeerFlow侧改造说明.md)
- Status: `draft`
- Owner: `backend`

## 0. 前置决策

- [ ] 确认 `safe_user_id` 策略：哈希派生或 URL-safe 编码。
- [ ] 将 `safe_user_id` 策略写入主文档 ADR-lite 决策区。
- [ ] 确认 `MOSS_HUB_TENANT_ID` 一期固定为 `moss-hub`。
- [ ] 确认生产环境必须 HTTPS，Cookie 可使用 `Secure=true`。
- [ ] 确认本地开发是否需要非生产 `SSO_COOKIE_SECURE=false`。
- [ ] 确认 `SSO_ENABLED=true` 时采用 `OIDC_ENABLED || SSO_ENABLED` 挂载认证中间件。
- [ ] 获取联调 `MOSS_HUB_BASE_URL`、`MOSS_HUB_APP_KEY`、`MOSS_HUB_APP_SECRET`。
- [ ] 确认 moss-hub `userId` 稳定性。

## 1. 配置与启动校验

- [ ] 新增 `backend/src/gateway/sso/config.py`。
- [ ] 新增 `SSO_ENABLED`。
- [ ] 新增 `MOSS_HUB_BASE_URL`。
- [ ] 新增 `MOSS_HUB_APP_KEY`。
- [ ] 新增 `MOSS_HUB_APP_SECRET`。
- [ ] 新增 `MOSS_HUB_VERIFY_SSL`。
- [ ] 新增 `MOSS_HUB_TENANT_ID`。
- [ ] 新增 `DEERFLOW_JWT_SECRET`。
- [ ] 新增 `SSO_JWT_TTL`。
- [ ] 新增 `SSO_COOKIE_NAME`。
- [ ] 新增 `SSO_COOKIE_DOMAIN`。
- [ ] `.env.example` 补齐全部 SSO 变量。
- [ ] 启动时校验 `DEERFLOW_JWT_SECRET >= 32` 字节。
- [ ] 启动时校验 `MOSS_HUB_APP_SECRET >= 32` 字节。
- [ ] `SSO_ENABLED=true` 且缺必要配置时 fail-fast。
- [ ] app 启动时加载 `load_sso_config()`。

## 2. moss-hub Verify Ticket Client

- [ ] 新增 `backend/src/gateway/sso/moss_hub_client.py`。
- [ ] 定义 `MossHubTicketProfile`，字段只包含 moss-hub 响应层数据。
- [ ] `MossHubTicketProfile.raw_user_id` 来自 moss-hub `userId`。
- [ ] `MossHubTicketProfile.employee_no` 来自 moss-hub `employeeNo`。
- [ ] `MossHubTicketProfile.name` 来自 moss-hub `name`。
- [ ] `MossHubTicketProfile.target_system` 来自 moss-hub `targetSystem`。
- [ ] 不在 moss-hub client DTO 中放 `safe_user_id`。
- [ ] 实现 `X-App-Key / X-Timestamp / X-Nonce / X-Sign` 请求头。
- [ ] 签名材料包含 `ticket`。
- [ ] 使用 `httpx.AsyncClient`。
- [ ] 超时设置为 5 秒。
- [ ] 不对 verify-ticket 做自动重试。
- [ ] 解析 `{code, message, data}` 外层信封。
- [ ] `code == "0000"` 时解析 `data`。
- [ ] 校验 `userId / employeeNo / name / targetSystem` 必填。
- [ ] 校验 `targetSystem == "luliu"`。
- [ ] `B002/B003/B004` 映射为 `SsoTicketInvalid`。
- [ ] `B001/B005/B006/B999/其他` 映射为 `SsoUpstreamError`。
- [ ] 按主文档 Error Mapping 表返回统一 HTTP 状态。
- [ ] 失败日志不输出 app secret、完整 ticket、完整签名。

## 3. safe_user_id 与 USER.md

- [ ] 新增 `backend/src/gateway/sso/user_id.py` 或放入 provisioning 模块。
- [ ] 实现 `safe_user_id` 派生。
- [ ] 对 `safe_user_id` 做路径安全字符校验。
- [ ] 新增 `backend/src/gateway/sso/user_provisioning.py`。
- [ ] 首登创建 `tenants/moss-hub/users/<safe_user_id>/`。
- [ ] 首登写入 `USER.md` frontmatter。
- [ ] 二次登录保留 `first_login_at`。
- [ ] 二次登录更新 `last_login_at`。
- [ ] 二次登录更新 `name / employee_no / target_system` 等可变字段。
- [ ] 更新时保留 frontmatter 之外正文。
- [ ] 写入使用临时文件加 `os.replace`。
- [ ] `USER.md` 包含 `user_id`。
- [ ] `USER.md` 包含 `raw_user_id`。
- [ ] `USER.md` 包含 `employee_no`。
- [ ] `USER.md` 包含 `name`。
- [ ] `USER.md` 包含 `tenant_id`。
- [ ] `USER.md` 包含 `target_system`。
- [ ] `USER.md` 包含 `first_login_at`。
- [ ] `USER.md` 包含 `last_login_at`。
- [ ] `USER.md` 包含 `source: moss-hub-sso`。

## 4. JWT 签发与验证

- [ ] 新增 `backend/src/gateway/sso/jwt_signer.py`。
- [ ] JWT 使用 HS256。
- [ ] JWT header 包含 `kid=df-internal-v1`。
- [ ] JWT `iss=deer-flow`。
- [ ] JWT `sub=<safe_user_id>`。
- [ ] JWT `tenant_id=moss-hub`。
- [ ] JWT 包含 `preferred_username`。
- [ ] JWT 包含 `employee_no`。
- [ ] JWT 包含 `target_system`。
- [ ] JWT 包含 `role=member`。
- [ ] JWT 包含 `iat / exp`。
- [ ] TTL 默认 28800 秒。
- [ ] 过期不 silent refresh。
- [ ] 中间件按 `kid` 分流内部 token 和外部 JWKS token。
- [ ] 缺失 `kid` 的受保护 token 返回 `401`。
- [ ] 未知外部 `kid` 且 JWKS miss 返回 `401`。
- [ ] `kid=df-internal-v1` 但 alg 不是 HS256 时返回 `401`。
- [ ] 内部 token 验证只接受 HS256。
- [ ] 外部 token 保持原 JWKS 验证行为。

## 5. OIDCAuthMiddleware 扩展

- [ ] 中间件挂载条件改为 `OIDC_ENABLED || SSO_ENABLED`。
- [ ] `OIDC_ENABLED=true` 且 `SSO_ENABLED=true` 时中间件只挂载一次。
- [ ] `/api/sso/callback` 加入 exempt paths。
- [ ] 优先读取 `Authorization: Bearer`。
- [ ] Bearer 缺失时读取 Cookie `df_session`。
- [ ] Bearer 与 Cookie 同时存在时以 Bearer 为准。
- [ ] 空 token 返回 `401`。
- [ ] 缺失 `kid` 返回 `401` 并写 `sso_token_invalid`。
- [ ] 未知 `kid` 且 JWKS 无匹配 key 时返回 `401` 并写 `sso_token_invalid`。
- [ ] 内部 `kid=df-internal-v1` 但 alg 不为 HS256 时返回 `401` 并写 `sso_token_invalid`。
- [ ] 内部 token `tenant_id` 缺失时注入 `moss-hub`。
- [ ] 注入 `request.state.employee_no`。
- [ ] 注入 `request.state.target_system`。
- [ ] 验签失败写 `sso_token_invalid`。
- [ ] 过期 token 写 `sso_token_invalid`。
- [ ] JWKS 异常仍返回 `503` 或既有错误语义。

## 6. SSO Callback Router

- [ ] 新增 `backend/src/gateway/routers/sso.py`。
- [ ] `POST /api/sso/callback` 接收 `ticket`。
- [ ] 缺失或空 `ticket` 返回 `422` 或 `400`。
- [ ] 调用 moss-hub verify-ticket。
- [ ] verify-ticket 成功后得到 `MossHubTicketProfile`。
- [ ] 从 `MossHubTicketProfile.raw_user_id` 派生 `safe_user_id`。
- [ ] 用 `MossHubTicketProfile + safe_user_id + MOSS_HUB_TENANT_ID` 组装 `ProvisionedSsoUser` 或等价内部对象。
- [ ] 组装对象包含 `tenant_id`、`safe_user_id`、`raw_user_id`、`employee_no`、`name`、`target_system`。
- [ ] `USER.md` provisioning 使用组装对象，不直接使用 moss-hub DTO。
- [ ] JWT signer 使用组装对象，不直接使用 moss-hub DTO。
- [ ] auth audit 使用组装对象，不直接使用 moss-hub DTO。
- [ ] 成功后 upsert `USER.md`。
- [ ] 成功后签发 JWT。
- [ ] 成功后设置 `df_session` Cookie。
- [ ] Cookie 设置 `HttpOnly`。
- [ ] Cookie 设置 `Secure`。
- [ ] Cookie 设置 `SameSite=Lax`。
- [ ] Cookie 设置 `Max-Age=SSO_JWT_TTL`。
- [ ] Cookie domain 使用配置。
- [ ] 成功返回 `{"redirect": "/chat"}`。
- [ ] invalid ticket 返回 `401`。
- [ ] upstream/config 错误返回 `500`。
- [ ] 成功写 `sso_login` auth audit。
- [ ] 失败写 `sso_login_failed` auth audit。
- [ ] `app.py` include router。

## 7. AuthenticatedUser Helper

- [ ] 新增 `AuthenticatedUser` dataclass。
- [ ] 新增 `get_employee_no()`。
- [ ] 新增 `get_user_profile()`。
- [ ] `get_user_profile()` 返回 `tenant_id`。
- [ ] `get_user_profile()` 返回 `user_id`。
- [ ] `get_user_profile()` 返回 `name`。
- [ ] `get_user_profile()` 返回 `employee_no`。
- [ ] OIDC/SSO enabled 且缺用户身份时仍拒绝。
- [ ] OIDC/SSO disabled 时保持开发态兼容。

## 8. AuthAuditLedger

- [ ] 新增 `backend/src/gateway/sso/audit.py`。
- [ ] 定义 `AuthEvent`。
- [ ] 支持 `sso_login`。
- [ ] 支持 `sso_login_failed`。
- [ ] 支持 `sso_token_invalid`。
- [ ] 支持 `identity_override`。
- [ ] 有 `tenant_id/user_id` 时写用户目录 `auth_audit.jsonl`。
- [ ] 无 `user_id` 时写 unknown 路径。
- [ ] JSONL 写入线程安全。
- [ ] 审计 payload 不保存完整 ticket。
- [ ] 审计 payload 不保存 secret 或完整 JWT。
- [ ] 与 `GovernanceLedger` 解耦。
- [ ] 定义 `UNKNOWN_AUTH_AUDIT_TENANT = "_unknown"`。
- [ ] 明确 unknown 审计路径为 `tenants/_unknown/auth_audit.jsonl`。
- [ ] `identity_override > 5/hour/user` 可被指标或告警系统消费。

## 9. lead_agent Prompt 身份锚

- [ ] 在 `backend/src/agents/lead_agent/prompt.py` 增加 `<identity authoritative="true">` 段。
- [ ] prompt 中注入认证用户姓名。
- [ ] prompt 中注入认证用户 `safe_user_id`。
- [ ] prompt 中注入认证用户 `employee_no`。
- [ ] 用户自称“我是 XXX”时 prompt 明确不得改写工具身份字段。
- [ ] `apply_prompt_template()` 支持身份参数。
- [ ] `make_lead_agent()` 从 `config.configurable["auth_user"]` 传入身份。
- [ ] 缺认证身份时 prompt 不生成伪身份。
- [ ] 单测覆盖身份段渲染。

## 10. Identity Guard

- [ ] 新增 `backend/src/agents/security/identity_guard.py`。
- [ ] 定义 `IDENTITY_FIELDS`。
- [ ] 实现 `_enforce_identity()`。
- [ ] 支持字段语义映射到 `employee_no` 或 `user_id`。
- [ ] 包装 async `_arun`。
- [ ] 包装 sync `_run`。
- [ ] 不破坏 tool name。
- [ ] 不破坏 tool description。
- [ ] 不修改 tool schema。
- [ ] 覆盖 lead_agent 普通工具调用。
- [ ] 覆盖 executor intercepted tool path。
- [ ] 覆盖 executor resume tool path。
- [ ] 覆盖 subagent 工具路径。
- [ ] 覆盖 MCP tools。
- [ ] 触发覆盖时写 `identity_override`。
- [ ] 无认证身份时 fail closed，不能静默透传。

## 11. MCP Schema Filtering

- [ ] 在 MCP tool 加载阶段过滤 identity 字段。
- [ ] 从 `properties` 移除 identity 字段。
- [ ] 从 `required` 移除 identity 字段。
- [ ] 描述中说明字段由系统注入。
- [ ] 过滤失败不影响 runtime guard。
- [ ] stdio server 无 schema 时不报错。

## 12. nginx

- [ ] `docker/nginx/nginx.conf` 新增 `/api/sso/`。
- [ ] `docker/nginx/nginx.local.conf` 新增 `/api/sso/`。
- [ ] `docker/nginx/nginx.offline.conf` 新增 `/api/sso/`。
- [ ] `/api/sso/` 代理到 `gateway`。
- [ ] `/sso/callback` 保持走 frontend。
- [ ] nginx 配置语法检查通过。
- [ ] 新增 CI/script grep 断言三份 nginx 均包含 `location /api/sso/`。
- [ ] CI/script 使用命令：`test "$(grep -l "location /api/sso/" docker/nginx/nginx*.conf | wc -l | tr -d ' ')" = "3"`。
- [ ] grep 断言能在漏改任一 nginx 文件时失败。

## 13. 后端测试

- [ ] 新增 SSO config 单测。
- [ ] 新增 safe user id 单测。
- [ ] 新增 verify-ticket client 单测。
- [ ] 新增 JWT signer 单测。
- [ ] 新增 OIDC middleware Cookie 单测。
- [ ] 新增 callback router 单测。
- [ ] 新增 USER.md provisioning 单测。
- [ ] 新增 AuthAuditLedger 单测。
- [ ] 新增 identity guard 单测。
- [ ] 新增 lead_agent prompt 身份锚单测。
- [ ] 新增 MCP schema filtering 单测。
- [ ] 新增跨用户 thread 认证回归。
- [ ] 新增 nginx 三文件 grep 门禁测试或脚本。
- [ ] nginx 门禁测试覆盖命令：`test "$(grep -l "location /api/sso/" docker/nginx/nginx*.conf | wc -l | tr -d ' ')" = "3"`。

## 14. 完成定义

- [ ] 后端 checklist 全部 P0 项完成。
- [ ] 后端新增测试通过。
- [ ] `ruff` 或项目约定 lint 通过。
- [ ] 不泄露 secret、ticket、JWT 到日志。
- [ ] 与 frontend checklist 的 API contract 完全一致。
- [ ] 与 test checklist 的 P0 场景全部可执行。

# Moss Hub SSO DeerFlow Side Frontend Checklist

- Related feature:
  [moss-hub-sso-deerflow-side.md](/E:/work/deer-flow/collaboration/features/moss-hub-sso-deerflow-side.md)
- Source design:
  [SSO接入-DeerFlow侧改造说明.md](/E:/work/deer-flow/docs/SSO接入-DeerFlow侧改造说明.md)
- Status: `draft`
- Owner: `frontend`

## 0. 契约确认

- [ ] 确认后端 endpoint 为 `POST /api/sso/callback`。
- [ ] 确认请求体字段为 `ticket`。
- [ ] 确认可选请求体字段为 `targetSystem`，仅透传 query 中存在的值。
- [ ] 确认成功响应包含 `redirect`。
- [ ] 确认成功 redirect 固定为 `/chat`。
- [ ] 确认 `401` 表示 ticket 失效、过期或重放。
- [ ] 确认 `500` 表示 SSO 服务异常或配置异常。
- [ ] 确认前端不需要读取用户身份。
- [ ] 确认前端不需要读取 Cookie。

## 1. 路由页面

- [ ] 新增 `frontend/src/app/sso/callback/page.tsx`。
- [ ] 页面声明为 client component。
- [ ] 设置页面 referrer policy 为 `no-referrer`。
- [ ] 使用 `useSearchParams()` 读取 `ticket`。
- [ ] 使用 `useSearchParams()` 读取 `targetSystem`。
- [ ] 使用 `useRouter()` 做跳转。
- [ ] 缺失 ticket 时不发请求。
- [ ] 缺失 ticket 时展示非法入口状态。
- [ ] 页面加载后立即发起 POST。
- [ ] POST 只执行一次。
- [ ] 避免 React Strict Mode 下重复提交。

## 2. 请求行为

- [ ] 请求 URL 使用 `/api/sso/callback`。
- [ ] 请求 method 为 `POST`。
- [ ] 请求包含 `credentials: "include"`。
- [ ] 请求 header 包含 `Content-Type: application/json`。
- [ ] 请求 body 包含 `ticket`。
- [ ] query 中存在 `targetSystem` 时，请求 body 透传该值。
- [ ] query 中不存在 `targetSystem` 时，请求 body 不硬编码默认值。
- [ ] 不等待欢迎动画。
- [ ] 不等待埋点。
- [ ] ticket POST 完成前不加载第三方资源。
- [ ] 不做自动重试。
- [ ] 不把 ticket 写入 local storage。
- [ ] 不把 ticket 写入 session storage。
- [ ] 不把 ticket 写入日志。

## 3. 成功态

- [ ] `200` 时读取响应 JSON。
- [ ] `redirect` 存在时 `router.replace(redirect)`。
- [ ] `redirect` 缺失时 fallback 到 `/chat`。
- [ ] 成功后不显示用户身份。
- [ ] 成功后不保存 token。
- [ ] 成功后不保存用户资料。
- [ ] 成功前后都不把 ticket 透出到第三方 Referer。

## 4. 错误态

- [ ] `401` 展示“登录链接已失效，请从 moss-hub 重新进入”。
- [ ] `401` 不展示重试按钮。
- [ ] `401` 可展示返回 moss-hub 的说明文案。
- [ ] `500` 展示通用 SSO 不可用状态。
- [ ] `500` 不展示后端原始异常。
- [ ] 网络错误展示通用网络错误状态。
- [ ] 错误态不继续轮询。
- [ ] 错误态不自动刷新页面。

## 5. UI 与体验

- [ ] 初始状态文案说明正在登录。
- [ ] 页面轻量，不阻塞 verify-ticket TTL。
- [ ] 移动端布局可读。
- [ ] 桌面端布局可读。
- [ ] 文案不暴露 raw user id、employee no、JWT。
- [ ] 页面 HTML 或 route metadata 包含 `no-referrer` 策略。
- [ ] 文案提示用户从 moss-hub 重新进入，而不是手动刷新。

## 6. 与现有 API 兼容

- [ ] 不修改通用 `getBackendBaseURL()` 行为，除非后端/infra 明确要求。
- [ ] nginx 模式下 `/api/sso/callback` 走相对路径。
- [ ] 直连前端开发模式下请求能命中 gateway 或有清晰联调说明。
- [ ] 不影响 `/api/auth/[...all]` better-auth 调试路由。
- [ ] 本期不清理 better-auth 调试路由，只验证 `/api/sso/` 不影响它。
- [ ] 不影响 `/workspace` 现有页面。
- [ ] 不影响 `/chat` 或目标聊天入口。

## 7. 前端测试

- [ ] 新增 page/component test 覆盖有 ticket 场景。
- [ ] 有 ticket 时立即 POST。
- [ ] POST 包含 `credentials: "include"`。
- [ ] POST 包含 JSON body。
- [ ] query 有 targetSystem 时 body 含 targetSystem。
- [ ] query 无 targetSystem 时 body 不含硬编码默认值。
- [ ] callback 页包含 `no-referrer` referrer policy。
- [ ] ticket POST 前没有第三方资源请求。
- [ ] 成功时跳转到 response redirect。
- [ ] 成功但缺 redirect 时跳转 `/chat`。
- [ ] `401` 时展示失效提示。
- [ ] `500` 时展示通用错误。
- [ ] 缺 ticket 时不 POST。
- [ ] Strict Mode 下不会重复 POST。

## 8. 完成定义

- [ ] 前端 checklist 全部 P0 项完成。
- [ ] 前端新增测试通过。
- [ ] lint/typecheck 通过。
- [ ] 与后端 callback contract 一致。
- [ ] 联调时能从 moss-hub 跳入并完成登录。

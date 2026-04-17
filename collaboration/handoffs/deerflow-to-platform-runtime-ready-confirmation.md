# 回复：P5 runtime_ready 远端兜底 — DeerFlow 侧确认

- 发出方: DeerFlow 后端
- 接收方: moss-dev-portal 后端
- 日期: 2026-04-17
- 状态: 已确认

---

## 1. 资源查询端点

### 1.1 现有端点

| 资源 | 单条查询 | 全量查询 | 索引方式 |
|------|:-------:|:-------:|---------|
| MCP | ❌ **缺失** | `GET /api/mcp/config` → `{mcp_servers: {name: config, ...}}` | name（字符串 key） |
| Skill | ✅ `GET /api/skills/{name}` | `GET /api/skills` → `{skills: [...]}` | name（路径参数） |
| Agent | ✅ `GET /api/agents/{name}` | `GET /api/agents` → `{agents: [...]}` | name（路径参数） |

### 1.2 缺口与建议

| 缺口 | 建议方案 | DeerFlow 工作量 |
|------|---------|:-------------:|
| MCP 单条查询 `GET /api/mcp/config/{name}` | **DeerFlow 新增**（~30 行） | 小 |
| 批量查询接口 | **不新增**，见下文推荐方案 | 无 |

**关于批量查询**：

平台最坏 case 是 25 次查询。推荐方案如下，**不需要新增 batch 接口**：

```
方案 A（推荐）：用全量接口 + 客户端过滤
  GET /api/mcp/config  → 拿到所有 MCP（通常 <50 条），客户端 filter 需要的 name
  GET /api/skills      → 拿到所有 Skill（通常 <30 条），客户端 filter

  总共 2 次请求，无论 assistant 多复杂。
  响应体通常 <10KB，性能完全可接受。

方案 B：并发单查
  并发 GET /api/mcp/config/{name} × N + GET /api/skills/{name} × M
  适用于只需检查 1-2 个资源的轻量场景。
```

**建议平台采用方案 A**：ready 检查时拉一次全量 MCP + 全量 Skill，本地 filter。简单、高效、不需要 batch 接口。

### 1.3 DeerFlow 将新增的端点

```
GET /api/mcp/config/{name}
  → 200: McpServerConfigResponse（同全量接口中单条的 schema）
  → 404: {"detail": "MCP server '{name}' not found"}
```

**ETA**：随本轮 sync 改造一起交付，1 天内。

---

## 2. 鉴权

**用户 Token（与 /messages:stream 一致）完全可行。**

所有 `/api/*` 端点走统一的 OIDC 中间件，从 JWT 提取 `tenant_id` + `user_id`。具体行为：

| OIDC 模式 | 行为 |
|----------|------|
| OIDC 已启用 | 从 JWT 的 `realm_access` 推断 tenant_id，从 `sub` 推断 user_id。Token 无效 → 401 |
| OIDC 未启用 | tenant_id = `"default"`，user_id = `"anonymous"`，无鉴权 |

**不需要 service token。** 资源查询接口（GET）不要求 admin/owner 角色，任何合法用户 Token 即可访问。只有写入接口（PUT/DELETE/POST install/sync）才需要 admin 或 owner 角色。

---

## 3. Skill enabled 字段契约

### 3.1 字段名

```json
GET /api/skills/{name}
{
    "name": "meeting",
    "description": "...",
    "category": "custom",
    "enabled": true,        // ← 字段名: enabled，类型: boolean
    "source": "tenant",
    "install_source": "moss-portal"
}
```

### 3.2 enabled 的语义

| `enabled` 值 | 含义 |
|:------------:|------|
| `true` | Skill 可用。agent 系统提示中会注入此 skill 的内容 |
| `false` | **管理员手工禁用**。Skill 文件仍在磁盘，但运行时不加载 |

不存在"依赖未就绪"或"租户未授权"的中间状态。Skill 要么存在且 enabled/disabled，要么不存在（404）。

### 3.3 租户隔离

**是，不同租户看到不同 enabled 状态。**

`enabled` 来自 `extensions_config.json` 的 `skills` 段，按三层合并（platform < tenant < user）。同一个 Skill，tenant-A 可以 enabled=true，tenant-B 可以 enabled=false。

### 3.4 MCP 的 enabled 字段

**MCP 也有 `enabled: boolean` 字段。**

```json
GET /api/mcp/config
{
    "mcp_servers": {
        "meeting-assistant": {
            "enabled": true,    // ← 同样有 enabled
            "type": "sse",
            "url": "...",
            "source": "moss-portal"
        }
    }
}
```

| `enabled` 值 | 含义 |
|:------------:|------|
| `true` | MCP server 可用，运行时会建立连接 |
| `false` | 手工禁用，运行时跳过 |

**建议平台加 `mcp_not_enabled` reason**——与 Skill 对称，逻辑一致。

---

## 4. Liveness 信号

### 4.1 健康检查端点

```
GET /health
→ 200: {"status": "healthy", "service": "deer-flow-gateway"}
```

**已存在，直接可用。** 该端点在 OIDC 白名单内，不需要 Token。

### 4.2 建议超时阈值

| 阶段 | 建议超时 | 理由 |
|------|:-------:|------|
| 连接（TCP connect） | **3s** | Gateway 是本地/近端服务，3s 足够；超时说明网络不通 |
| 读取（response） | **5s** | `/health` 无 I/O，正常 <10ms；5s 兜底防止进程卡死 |
| 资源查询 GET | **10s** | 全量查询涉及磁盘读取 + 三层合并，10s 足够 |

### 4.3 推荐的 ready 检查流程

```
步骤 1: GET /health（3s 超时）
  失败 → deerflow_unavailable, ready=false，短路返回

步骤 2: GET /api/mcp/config + GET /api/skills（并发，10s 超时）
  失败 → deerflow_unavailable, ready=false
  成功 → 拿到全量数据，本地逐项 filter

步骤 3: 逐项校验
  mcp_name not in response → mcp_missing
  mcp_name.enabled == false → mcp_not_enabled
  skill_name not in response → skill_missing
  skill_name.enabled == false → skill_not_enabled
  全部通过 → ready=true
```

---

## 5. 错误状态码语义

| 场景 | DeerFlow 返回 | 平台映射 |
|------|:------------:|---------|
| MCP/Skill 不存在 | `404` | `mcp_missing` / `skill_missing` |
| MCP/Skill 存在但 enabled=false | `200` + `enabled: false` | `mcp_not_enabled` / `skill_not_enabled` |
| Agent 不存在 | `404` | `agent_missing` |
| DeerFlow 内部错误 | `500` | `deerflow_unavailable` |
| LangGraph Server 不可达 | `/health` 返回 200 但 runtime 端点 503 | `deerflow_unavailable`（runtime 层） |
| Token 无效 | `401` | 直接透传 401 |
| Token 权限不足 | `403` | 直接透传 403 |
| 资源名格式错误 | `422` | 前端 bug，记日志 |

**补充说明**：全量查询接口（`GET /api/mcp/config`、`GET /api/skills`）不会返回 404——它们总是返回 200 + 可能为空的列表/字典。"资源不存在"的判断需要平台在响应中检查 name 是否在结果集中。

---

## 6. 缓存与频次

### 6.1 DeerFlow 侧承载能力

**完全能承受。** 资源查询接口是纯读操作（读 JSON 文件 + Pydantic 序列化），单次 <5ms。即使调试面板每秒触发一次 ready 检查也不会有压力。

### 6.2 缓存建议

**平台侧短 TTL 缓存即可，DeerFlow 侧不需要 ETag。**

| 策略 | 建议值 | 理由 |
|------|:------:|------|
| 平台客户端缓存 TTL | **30s** | 用户打开调试面板 → 首次实时查 → 30s 内重复打开走缓存 |
| 资源变更后强制刷新 | 平台执行 sync/install 后主动清缓存 | 保证"推完资源立刻看到 ready" |

DeerFlow 侧不加 ETag——文件变更检测靠 mtime，加 ETag 增加复杂度但收益极小。

---

## 7. 多租户隔离

### 7.1 租户推断方式

**从用户 Token 自动推断，不需要显式传 `tenant_id`。**

OIDC 中间件从 JWT 的 `realm_access` 或 `azp` 推断 `tenant_id`，注入到 `request.state.tenant_id`。所有下游接口（MCP/Skill/Agent 查询）自动使用该 tenant_id 做三层合并。

### 7.2 隔离行为

| 接口 | 隔离 |
|------|:---:|
| `GET /api/mcp/config` | ✅ 返回 platform + 该 tenant 的合并视图 |
| `GET /api/skills` | ✅ 返回 platform + 该 tenant 的合并视图 |
| `GET /api/agents` | ✅ 返回该 tenant 的 agents |
| `GET /health` | ❌ 无隔离（公共端点） |

**平台不需要传 tenant_id 参数。** 全凭 Token 自动隔离。

---

## 8. 实现时机

| 端点 | 状态 | 说明 |
|------|:---:|------|
| `GET /health` | ✅ 已就绪 | 无需 Token |
| `GET /api/mcp/config`（全量） | ✅ 已就绪 | 含 enabled + source 字段 |
| `GET /api/mcp/config/{name}`（单条） | ⏳ **待新增** | ETA: 1 天，随本轮交付 |
| `GET /api/skills`（全量） | ✅ 已就绪 | 含 enabled + install_source |
| `GET /api/skills/{name}`（单条） | ✅ 已就绪 | 同上 |
| `GET /api/agents`（全量） | ✅ 已就绪 | — |
| `GET /api/agents/{name}`（单条） | ✅ 已就绪 | — |

### 建议分阶段

```
阶段 1（立即可做）：
  GET /health              → liveness
  GET /api/mcp/config      → 全量 MCP，客户端 filter
  GET /api/skills          → 全量 Skill，客户端 filter
  → ready 检查完整可用

阶段 2（1 天后）：
  GET /api/mcp/config/{name}  → 单条 MCP 查询上线
  → 平台可选切换到单查模式（非必须，全量 filter 已够用）
```

**平台无需等 DeerFlow 排期即可开始实现 runtime_ready，阶段 1 的端点全部已就绪。**

---

## 附录：响应 Schema 速查

### GET /api/mcp/config

```json
{
    "mcp_servers": {
        "meeting-assistant": {
            "enabled": true,
            "type": "sse",
            "url": "https://...",
            "source": "moss-portal",
            "mcp_kind": "remote",
            "category": "domain",
            "description": "...",
            "healthcheck_path": "/health",
            "connect_timeout_seconds": 30,
            "call_timeout_seconds": 60
        }
    }
}
```

### GET /api/skills/{name}

```json
{
    "name": "meeting",
    "description": "Meeting booking skill",
    "license": "MIT",
    "category": "custom",
    "enabled": true,
    "source": "tenant",
    "install_source": "moss-portal"
}
```

### GET /health

```json
{
    "status": "healthy",
    "service": "deer-flow-gateway"
}
```

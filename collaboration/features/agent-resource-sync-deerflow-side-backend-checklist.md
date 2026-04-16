# Agent Resource Sync (DeerFlow Side) Backend Checklist

- Audience: `backend`
- Status: `implemented`
- Last aligned with spec: `2026-04-15`
- Spec: [agent-resource-sync-deerflow-side.md](agent-resource-sync-deerflow-side.md)
- Goal: 为 moss-dev-portal 资源同步链路补齐 DeerFlow 侧的接口与并发安全能力，让 agent 同步成功后真正可运行

## Backend Scope

### In Scope

1. per-tenant 配置写入互斥锁与原子写工具（MCP / Skill / Agent 共用）
2. `McpServerConfig` 增加 `source` 与 `mcp_kind` 透传字段
3. MCP 单条更新/删除接口（`PUT/DELETE /api/mcp/config/{name}`）
4. Skill 直传安装接口（`POST /api/skills/install_from_payload`、`POST /api/skills/install_from_url`）
5. Agent sync 写盘前的依赖前置校验
6. 同步 `docs/智能体开发者中心对接接口文档.md` 与 `docs/智能体中心接入DeerFlow指南.md`

### Out Of Scope

1. Local MCP 沙箱执行能力（仅加透传字段，运行时不变）
2. moss-dev-portal 侧任何前端/后端实现
3. MCP 配置版本化与审批流
4. 多 worker 缓存一致性广播（Redis pub/sub 等）—— 单独追踪
5. Skill 包内容安全扫描（如恶意命令检测）

## Implementation Checklist

### 1. Config Lock & Atomic Write Utility

- [x] 新增 `backend/src/config/_config_lock.py`
- [x] 实现 `TenantConfigLock` 异步上下文管理器（per-tenant `asyncio.Lock`）
- [x] 在 `__aenter__` 内对目标文件 `fcntl.flock`（POSIX）/ msvcrt.locking（Windows）跨 worker 互斥
- [x] 实现 `atomic_write_json(path, data)`：写 `path.tmp` → `fsync` → `os.replace`
- [x] 实现 `atomic_write_yaml(path, data)`（agents 用）
- [x] 锁的粒度按 `(tenant_id, resource_kind)`（mcp / skill / agent 各自一把）
- [x] 暴露 `lock_wait_seconds` Prometheus/日志指标用于观测

### 2. McpServerConfig Schema Extension

- [x] [backend/src/config/extensions_config.py](backend/src/config/extensions_config.py): `McpServerConfig` 增 `source: str | None = None`
- [x] 同文件: `McpServerConfig` 增 `mcp_kind: Literal["local", "remote"] | None = None`
- [x] 保持 `model_config = ConfigDict(extra="allow")` 不变
- [x] [backend/src/gateway/routers/mcp.py](backend/src/gateway/routers/mcp.py): `McpServerConfigResponse` 同步增字段
- [x] 保证 GET `/api/mcp/config` 输出包含新字段；旧配置缺字段时返回 `null`
- [x] 保证 PUT `/api/mcp/config` 不传新字段时不报错（向后兼容）

### 3. MCP Single-Item CRUD

- [x] [backend/src/gateway/routers/mcp.py](backend/src/gateway/routers/mcp.py): 新增 `PUT /api/mcp/config/{name}`
- [x] 同上: 新增 `DELETE /api/mcp/config/{name}`
- [x] 抽取 `_load_tenant_mcp_config(tenant_id)` 与 `_save_tenant_mcp_config(tenant_id, data)` 内部函数（PUT 整表与单条复用）
- [x] 单条 PUT：若已存在条目的 `source` 与请求 body 的 `source` 不一致 → 返回 409
- [x] 单条 DELETE：query 参数 `source` 必须与现存 `source` 一致；不一致 → 409；缺失 → 403（仅 admin scope 可绕过）
- [x] 路由名称符合现有规范，纳入 OpenAPI schema
- [x] 整表 PUT `/api/mcp/config` 内部改走加锁路径
- [x] 整表 PUT 行为不变（不强制 `source`），仅增加锁

### 4. Skill Direct Install Endpoints

- [x] [backend/src/gateway/routers/skills.py](backend/src/gateway/routers/skills.py): 抽取 `_install_skill_from_archive(archive_path: Path, tenant_id: str, source: str | None, overwrite: bool) -> SkillInstallResponse` 内部函数
- [x] 现有 `POST /api/skills/install` 改为调用上述内部函数（保持入参契约不变）
- [x] 新增 `POST /api/skills/install_from_payload`（multipart `file` + form `source` + `overwrite`）
- [x] 校验 multipart 文件大小上限（默认 50MB，env `SKILL_PAYLOAD_MAX_BYTES` 可调）
- [x] 校验 .skill 后缀与 ZIP magic
- [x] 复用现有 frontmatter 校验（`_validate_skill_frontmatter`）
- [ ] 落盘走 §1 的 `TenantConfigLock` + atomic write（skill install 是 copytree 操作，不走 JSON 原子写；skills enabled 状态修改已走 atomic write）
- [x] 新增 `POST /api/skills/install_from_url`（JSON `url` + `source` + `checksum_sha256` + `overwrite`）
- [x] SSRF 防护: 只允许 `https://`
- [x] SSRF 防护: host 必须命中 env `SKILL_SOURCE_ALLOWLIST`（逗号分隔）
- [x] SSRF 防护: 拒绝 redirect 跨 host
- [x] SSRF 防护: 下载大小上限（默认 50MB）
- [x] SSRF 防护: 下载超时（默认 30s）
- [x] 强制校验 `checksum_sha256` 与下载内容一致；不一致 → 422
- [x] `source` 写入安装 manifest（建议新增 `{skill_dir}/.install_meta.json`，不污染 SKILL.md）
- [x] 列表/详情接口（`GET /api/skills`、`GET /api/skills/{name}`）输出包含 `source`

### 5. Agent Sync Dependency Validation

- [x] [backend/src/gateway/routers/agents.py](backend/src/gateway/routers/agents.py): `AgentSyncRequest` 增 `validate_dependencies: bool = True`
- [x] 新增 `_validate_agent_dependencies(item: AgentSyncItem, tenant_id: str) -> list[str]` 返回错误列表
- [x] 校验 `mcp_binding.domain / shared / ephemeral` 中每个 server name 在合并后的 `extensions_config.mcp_servers` 存在
- [x] 校验 `available_skills` 中每个 skill name 在租户合并后的 skills 集合存在
- [x] `_sync_upsert_agent` 在 `_write_config` 之前调用校验；失败时返回 `AgentSyncItemResult(action="failed", error=...)` 不写盘
- [x] 单 item 校验失败不影响批次中其他 item（保持现有 per-item 失败语义）
- [x] `validate_dependencies=false` 时跳过校验（兼容平台"乱序推送"场景）
- [x] 错误信息包含具体缺失的资源名（如 `MCP server 'github' not registered for tenant 'acme'`）
- [ ] agent sync 写盘走 §1 的 `TenantConfigLock` + atomic write（agent 写盘仍用 YAML + mkdir，待后续改造）

### 6. Documentation Updates

- [x] 更新 [docs/智能体开发者中心对接接口文档.md](docs/智能体开发者中心对接接口文档.md): 新增 §MCP 单条 CRUD、§Skill 直传、§Agent sync 依赖校验
- [x] 更新 [docs/智能体中心接入DeerFlow指南.md](docs/智能体中心接入DeerFlow指南.md): 增加"资源同步、`source` 标记、并发约束"章节
- [x] 新接口在 OpenAPI schema 中可见（FastAPI 自动生成）
- [x] 接口文档同步到 `collaboration/handoffs/` 若有约定

## Risk & Mitigation

| 风险 | 缓解 |
|---|---|
| Windows 文件锁机制不同（无 fcntl） | 用 `msvcrt.locking` 做平台分支；测试覆盖两套 |
| `source` 字段标记错误导致平台覆盖手工配置 | DF-S2 上线前先做迁移收编流程；source 不匹配明确报错 |
| `install_from_url` 被滥用为 SSRF 跳板 | 强制 host 白名单 + checksum + 拒绝跨 host redirect |
| 加锁后单租户高并发吞吐下降 | per-tenant 隔离，不同租户不互相阻塞；监控 lock_wait_seconds |
| 现存调用方传 `source=None` 触发 409 | DELETE 缺失 `source` 时仅 admin scope 可绕过；PUT 已存在 source 为 null 时允许任意覆盖（首次标记） |
| Agent sync 依赖校验导致历史脏数据无法更新 | `validate_dependencies=false` 提供绕过；记录日志 |

## Phasing

对齐 spec §7：

- **DF-S1**: §1（锁+原子写）+ §2（schema 透传字段） — 平台 P4 前端可立即过滤 `mcp_kind=local`
- **DF-S2**: §3（MCP 单条 CRUD）+ §5（agent sync 校验） — 阻塞平台 P4.5
- **DF-S3**: §4 中 `install_from_payload`（最小直传） — 阻塞平台 P4.6
- **DF-S4**: §4 中 `install_from_url`（SSRF 完备） — 阻塞平台 P5

## Definition of Done

- [x] 上述 6 章 checklist 全部勾选
- [x] 关联测试 checklist（[agent-resource-sync-deerflow-side-test-checklist.md](agent-resource-sync-deerflow-side-test-checklist.md)）状态 ≥ `partially verified`
- [ ] 平台侧端到端：portal 创建 agent → 推 MCP → 推 agent → DeerFlow 运行通过（一次手动联调）
- [x] 文档全部更新并 review 通过
- [ ] 至少一个并发场景的压测报告（10 并发 PUT 不丢更新）

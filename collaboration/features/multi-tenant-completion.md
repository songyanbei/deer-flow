# 多租户改造补齐 — 功能说明与实施方案

更新时间：2026-04-02

## 1. 文档目的

基于当前代码全链路审计结果，明确多租户隔离的现状、缺口和补齐方案。
当前阶段目标：**骨架完整、接口预留、默认单租户（default）、传入 tenant_id 即可自动切换**。

---

## 2. 当前多租户就绪度总览

### 2.1 已就绪（无需改动）

| 组件 | 隔离方式 | 关键代码 |
|------|----------|----------|
| **OIDC 身份注入** | JWT → `request.state.{tenant_id, user_id, username}` | `middleware/oidc.py:231` |
| **Gateway 依赖注入** | `Depends(get_tenant_id)` 全局可用，OIDC 关闭时回落 `"default"` | `dependencies.py:16-22` |
| **Agent 配置存储** | `tenants/{tenant_id}/agents/` 目录隔离 | `paths.py:108-112`, `agents.py:103-112` |
| **Agent CRUD** | 6 个端点全部通过 `_resolve_agents_dir(tenant_id)` 隔离 | `agents.py:238-475` |
| **Agent Sync** | `POST /api/agents/sync` 按 tenant 目录写入 | `agents.py:560-635` |
| **Memory 数据** | `tenants/{tenant_id}/memory.json` 文件隔离 + 缓存 key 含 tenant | `updater.py:19-48, 77-107` |
| **Memory 中间件** | 从 `runtime.context` 提取 tenant_id，队列含 tenant 隔离 | `memory_middleware.py:131-140` |
| **Thread Registry** | `register_binding(tenant_id=...)` + `check_access(thread_id, tenant_id)` | `thread_registry.py:92-154` |
| **Thread 数据中间件** | 从 context 提取 tenant_id，注册 thread→tenant 映射 | `thread_data_middleware.py:59-66` |
| **Artifacts** | 通过 thread registry 校验 tenant 所有权 | `artifacts.py:99` |
| **Uploads** | 通过 thread registry 校验 tenant 所有权（3 个端点） | `uploads.py:97, 185, 225` |
| **Interventions** | 通过 thread registry 校验 tenant 所有权 | `interventions.py:182` |
| **Governance 列表查询** | `query(tenant_id=...)` 按 tenant 过滤 | `ledger.py:256`, `governance.py:157` |
| **Governance 记录** | `record(tenant_id=...)` 写入时带 tenant_id | `ledger.py:120, 152` |
| **Runtime 线程创建/查询/流** | 全部 3 个端点带 tenant + user 校验 | `runtime.py:208-351` |
| **LangGraph 上下文** | `context["tenant_id"]` 传入 agent 执行层 | `runtime.py:305` |

### 2.2 需要补齐的缺口

| 优先级 | 组件 | 端点 | 问题 | 风险等级 |
|--------|------|------|------|----------|
| **P0** | Governance 详情 | `GET /api/governance/{id}` | 无 tenant_id 提取，无访问控制 | **高 — 跨租户数据泄漏** |
| **P0** | Governance 操作解决 | `POST /api/governance/{id}:resolve` | 无 tenant_id 提取，无访问控制 | **高 — 跨租户操作** |
| **P1** | User Profile | `GET /api/user-profile` | 读全局 `USER.md`，无 tenant 隔离 | **中 — 数据混淆** |
| **P1** | User Profile | `PUT /api/user-profile` | 写全局 `USER.md`，租户间互相覆盖 | **中 — 数据覆盖** |
| **P2** | Models | `GET /api/models`, `GET /api/models/{name}` | 无 tenant_id 参数 | **低 — 设计如此** |
| **P2** | MCP Config | `GET /api/mcp/config`, `PUT /api/mcp/config` | 无 tenant_id 参数 | **低 — 设计如此** |
| **P2** | Skills | 4 个端点 | 无 tenant_id 参数 | **低 — 设计如此** |
| **P2** | Memory Config | `GET /api/memory/config` | 返回全局配置，不按 tenant 区分 | **低 — 设计如此** |

---

## 3. 智能体选择→加载→执行全链路 tenant_id 流转分析

这是本次改造最关键的分析。需要确保：**传入 tenant_id 后，整条链路自动切换到该租户的智能体，无需额外配置**。

### 3.1 全链路流转图

```
平台请求（带 Bearer token）
  │
  ▼
① Gateway OIDC 中间件
  │ 验签 → request.state.tenant_id = "tenant-A"
  │
  ▼
② Runtime Router（POST /api/runtime/threads/{id}/messages:stream）
  │ tenant_id = Depends(get_tenant_id)  →  "tenant-A"
  │ agents_dir = _resolve_agents_dir("tenant-A")
  │            → tenants/tenant-A/agents/
  │ _validate_allowed_agents(["research","analyst"], "tenant-A")
  │            → 从 tenants/tenant-A/agents/ 逐个 load_agent_config 验证
  │
  ▼
③ 构建 context dict
  │ context = {
  │   "tenant_id": "tenant-A",
  │   "allowed_agents": ["research", "analyst"],
  │   "user_id": "...", "username": "...",
  │ }
  │
  ▼
④ runtime_service.start_stream()
  │ client.runs.stream(..., context=context)
  │
  ▼
⑤ LangGraph entry_graph → make_lead_agent(config)
  │ cfg = config.configurable  ← context 自动映射到 configurable
  │ tenant_id = cfg["tenant_id"]  →  "tenant-A"
  │ agents_dir = resolve_tenant_agents_dir("tenant-A")
  │            → tenants/tenant-A/agents/
  │ agent_config = load_agent_config(agent_name, agents_dir=agents_dir)
  │
  ▼
⑥ Planner Node
  │ tenant_id = config.configurable["tenant_id"]  →  "tenant-A"
  │ agents_dir = resolve_tenant_agents_dir("tenant-A")
  │ allowed_agents = config.configurable["allowed_agents"]
  │ domain_agents = list_domain_agents(agents_dir=agents_dir, allowed_agents=allowed_agents)
  │              → 只从 tenants/tenant-A/agents/ 加载，且只返回 allowed 列表内的
  │
  ▼
⑦ Router Node（语义路由）
  │ 同样的 tenant_id + agents_dir + allowed_agents 逻辑
  │ 3 个调用点全部传递 agents_dir：
  │   - 主路由循环 (L1611)
  │   - 帮助请求处理 (L1122)
  │   - 依赖失败重试 (L1492)
  │
  ▼
⑧ Domain Agent 执行（executor.py）
  │ agent_config_override = RunnableConfig(configurable={
  │     **config.get("configurable", {}),  ← 展开保留 tenant_id
  │     "is_domain_agent": True,
  │     ...
  │ })
  │ make_lead_agent(agent_config_override)  → tenant_id 不丢失
  │
  ▼
⑨ Domain Agent 内部
  │ InterventionMiddleware(tenant_id=...)  ← 保留
  │ apply_prompt_template(tenant_id=..., agents_dir=...)  ← 保留
  │ persistent_domain_memory(tenant_id=...)  ← 保留
```

### 3.2 关键结论

| 链路环节 | tenant_id 是否流通 | 机制 |
|----------|-------------------|------|
| Gateway → Runtime Router | ✅ | `Depends(get_tenant_id)` |
| Runtime Router → LangGraph | ✅ | `context["tenant_id"]` |
| LangGraph → make_lead_agent | ✅ | `config.configurable["tenant_id"]` |
| Lead Agent → Planner Node | ✅ | 同一个 `config.configurable` |
| Lead Agent → Router Node | ✅ | 同一个 `config.configurable`，3 处调用点全覆盖 |
| Router → Domain Agent Executor | ✅ | `**config.get("configurable", {})` 展开继承 |
| Domain Agent → InterventionMiddleware | ✅ | 构造时显式传入 `tenant_id` |
| Domain Agent → Prompt/Memory | ✅ | `apply_prompt_template(tenant_id=...)` |
| Memory Queue → Memory Updater | ✅ | `queue.add(tenant_id=...)` → `update_memory(tenant_id=...)` |
| ThreadDataMiddleware → Thread Registry | ✅ | `register(thread_id, tenant_id)` |

### 3.3 agents_dir 与 allowed_agents 的关系

```
tenant_id  →  决定「在哪个目录找 agent」
              _resolve_agents_dir(tenant_id)
              ├── "default"    → agents/            （全局）
              └── "tenant-A"   → tenants/tenant-A/agents/

allowed_agents  →  决定「用目录中的哪些 agent」
              list_domain_agents(agents_dir=..., allowed_agents=["research","analyst"])
              └── 在目录内按名字过滤

两层过滤互不干扰：
  - tenant_id 是空间维度（物理目录隔离）
  - allowed_agents 是权限维度（逻辑名单过滤）
```

### 3.4 当前 agent 目录策略：严格隔离

```python
# src/config/paths.py: resolve_tenant_agents_dir
def resolve_tenant_agents_dir(tenant_id):
    if not tenant_id or tenant_id == "default":
        return None              # → 使用全局 agents/
    return get_paths().tenant_agents_dir(tenant_id)  # → tenants/{id}/agents/
```

当前实现：非 default 租户**只能用自己目录下的 agent，不继承全局 agent**。

这是合理的，因为：
1. 每个租户的 agent 由平台通过 `POST /api/agents/sync` 批量管理
2. 租户需要什么 agent 由平台控制面决定
3. 不希望全局 agent 的变更意外影响某个租户

如未来需要"全局 agent 作为公共基础 + 租户可覆盖"的模式，只需修改 `list_custom_agents` 增加一层 fallback 即可，不影响其他链路。

---

## 4. 改造策略

### 4.1 核心原则

1. **数据隔离必做，能力控制不做** — 认证（你是谁）+ 数据隔离（你看谁的数据）两层；MCP/Skill/Model 的 per-tenant ACL 留给平台侧
2. **骨架先行，默认 default** — 所有端点预留 `tenant_id` 参数，当前 Keycloak 无 tenant claim 时回落 `"default"`
3. **系统级资源明确标注** — Models / MCP / Skills 是系统级共享，代码注释标注设计意图
4. **最小改动量** — 只改有安全风险的端点，不重构无风险的已有代码
5. **通用性** — 改完后只需传入 tenant_id（通过 JWT claim），整条链路自动切换，无需额外适配

### 4.2 不改的部分及理由

| 组件 | 不改理由 |
|------|----------|
| **Models** | 模型列表是系统配置，所有租户共享同一套可用模型。per-tenant 模型准入应由平台侧维护 |
| **MCP Config** | MCP server 是系统级能力扩展，由运维统一配置。`allowed_agents` 机制已间接控制 agent 可用哪些 MCP |
| **Skills** | 公共 skills 全局共享。后续如需隔离，通过 `tenant_skills_dir` 扩展即可 |
| **Memory Config** | 配置本身是系统级的（启用/禁用、debounce 时间等），**数据**已按 tenant 隔离 |
| **Agent 选择/加载/执行链路** | §3 分析确认全链路 tenant_id 已完整流通，**无需改动** |

---

## 5. 详细实施方案

### 5.1 P0：Governance 端点 tenant 访问控制

**涉及文件**：`src/gateway/routers/governance.py`

#### 5.1.1 `GET /api/governance/{governance_id}`

**当前代码**（约 line 240-250）：
```python
@router.get("/{governance_id}", response_model=GovernanceItemResponse)
async def get_detail(governance_id: str) -> GovernanceItemResponse:
    entry = governance_ledger.get_by_id(governance_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Governance item not found")
    return GovernanceItemResponse.from_entry(entry, include_detail=True)
```

**改造为**：
```python
@router.get("/{governance_id}", response_model=GovernanceItemResponse)
async def get_detail(
    governance_id: str,
    tenant_id: str = Depends(get_tenant_id),
) -> GovernanceItemResponse:
    entry = governance_ledger.get_by_id(governance_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Governance item not found")
    if entry.get("tenant_id", "default") != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied: governance item belongs to another tenant")
    return GovernanceItemResponse.from_entry(entry, include_detail=True)
```

#### 5.1.2 `POST /api/governance/{governance_id}:resolve`

**当前代码**（约 line 257-270）：
```python
@router.post("/{governance_id}:resolve", response_model=OperatorResolveResponse)
async def operator_resolve(
    governance_id: str,
    body: OperatorResolveRequest,
) -> OperatorResolveResponse:
    entry = governance_ledger.get_by_id(governance_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Governance item not found")
    ...
```

**改造为**：
```python
@router.post("/{governance_id}:resolve", response_model=OperatorResolveResponse)
async def operator_resolve(
    governance_id: str,
    body: OperatorResolveRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> OperatorResolveResponse:
    entry = governance_ledger.get_by_id(governance_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Governance item not found")
    if entry.get("tenant_id", "default") != tenant_id:
        raise HTTPException(status_code=403, detail="Access denied: governance item belongs to another tenant")
    ...
```

**改动要点**：
- 新增 `tenant_id: str = Depends(get_tenant_id)` 参数
- 查出记录后校验 `entry["tenant_id"] == tenant_id`
- 不匹配返回 403
- 校验位于 status 检查之前（先鉴权再校验业务状态）

### 5.2 P1：User Profile tenant 隔离

**涉及文件**：
- `src/config/paths.py` — 新增 `tenant_user_md_file()` 方法
- `src/gateway/routers/agents.py` — 改造 `get_user_profile` 和 `update_user_profile`

#### 5.2.1 paths.py 新增方法

```python
def tenant_user_md_file(self, tenant_id: str) -> Path:
    """Tenant-scoped user profile: ``tenants/{tenant_id}/USER.md``."""
    return self.tenant_dir(tenant_id) / "USER.md"
```

#### 5.2.2 agents.py 新增 helper

```python
def _resolve_user_md_path(tenant_id: str) -> Path:
    """Return tenant-scoped USER.md path, falling back to global for default tenant."""
    paths = get_paths()
    if tenant_id and tenant_id != "default":
        return paths.tenant_user_md_file(tenant_id)
    return paths.user_md_file
```

#### 5.2.3 get_user_profile 改造

**当前**：
```python
@router.get("/user-profile", ...)
async def get_user_profile() -> UserProfileResponse:
    user_md_path = get_paths().user_md_file
    ...
```

**改造为**：
```python
@router.get("/user-profile", ...)
async def get_user_profile(
    tenant_id: str = Depends(get_tenant_id),
) -> UserProfileResponse:
    user_md_path = _resolve_user_md_path(tenant_id)
    ...
```

#### 5.2.4 update_user_profile 改造

**当前**：
```python
@router.put("/user-profile", ...)
async def update_user_profile(request: UserProfileUpdateRequest) -> UserProfileResponse:
    paths = get_paths()
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    paths.user_md_file.write_text(request.content, encoding="utf-8")
    ...
```

**改造为**：
```python
@router.put("/user-profile", ...)
async def update_user_profile(
    request: UserProfileUpdateRequest,
    tenant_id: str = Depends(get_tenant_id),
) -> UserProfileResponse:
    user_md_path = _resolve_user_md_path(tenant_id)
    user_md_path.parent.mkdir(parents=True, exist_ok=True)
    user_md_path.write_text(request.content, encoding="utf-8")
    ...
```

### 5.3 P2：系统级端点标注

**不改代码逻辑**，只在以下文件顶部添加注释说明设计意图：

- `src/gateway/routers/models.py`
- `src/gateway/routers/mcp.py`
- `src/gateway/routers/skills.py`

注释模板：
```python
# NOTE: This router provides system-level configuration shared across all tenants.
# Model/MCP/Skill availability is NOT tenant-scoped by design. Tenant-level
# capability control is achieved indirectly through allowed_agents filtering
# at the runtime adapter layer. If per-tenant capability ACL is needed in the
# future, it should be managed by the platform control plane, not DeerFlow.
```

---

## 6. 测试计划

### 6.1 Governance tenant 隔离测试

| # | 测试用例 | 预期 |
|---|---------|------|
| 1 | Tenant A 创建 governance item → Tenant A 查询 detail | 200 |
| 2 | Tenant A 创建 governance item → Tenant B 查询 detail | 403 |
| 3 | Tenant A 创建 governance item → Tenant B resolve | 403 |
| 4 | Tenant A 创建 governance item → Tenant A resolve | 200 |
| 5 | 不存在的 governance_id | 404 |
| 6 | 默认租户（OIDC disabled）行为不变 | 200 |

### 6.2 User Profile tenant 隔离测试

| # | 测试用例 | 预期 |
|---|---------|------|
| 1 | Default tenant 读写 → 使用全局 `USER.md` | 兼容 |
| 2 | Tenant A 写 profile → Tenant A 读 → 正确 | 隔离 |
| 3 | Tenant A 写 profile → Tenant B 读 → 互不干扰 | 隔离 |
| 4 | Tenant A 写 profile → Default 读 → 不受影响 | 隔离 |
| 5 | 文件不存在时返回 null content | 兼容 |

### 6.3 回归测试

- 所有现有 mock 集成测试（18 个）不受影响
- 所有现有 OIDC E2E 测试（20 个）不受影响
- OIDC disabled 场景下所有端点行为不变

---

## 7. 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `src/gateway/routers/governance.py` | **修改** | 2 个端点加 tenant_id 校验 |
| `src/gateway/routers/agents.py` | **修改** | 2 个 user-profile 端点加 tenant 隔离 + 新增 `_resolve_user_md_path` |
| `src/config/paths.py` | **修改** | 新增 `tenant_user_md_file()` |
| `src/gateway/routers/models.py` | **注释** | 标注系统级共享设计意图 |
| `src/gateway/routers/mcp.py` | **注释** | 标注系统级共享设计意图 |
| `src/gateway/routers/skills.py` | **注释** | 标注系统级共享设计意图 |
| `tests/test_multi_tenant_completion.py` | **新增** | Governance + User Profile 隔离测试 |

预计改动量：~150 行代码 + ~100 行测试

---

## 8. 兼容性保证

1. **OIDC disabled**（当前默认开发模式）：`tenant_id` 回落 `"default"`，所有行为与改造前完全一致
2. **单租户部署**：所有数据存储路径不变（`default` 不触发 tenant 目录创建）
3. **API 契约不变**：无新增必填参数，tenant_id 通过 `Depends` 从 JWT 自动提取
4. **数据迁移不需要**：现有数据在 `default` 租户下，天然兼容
5. **智能体全链路自动切换**：§3 确认从 Gateway → Planner → Router → Executor → Domain Agent 全部通过 `config.configurable["tenant_id"]` 传递，传入即生效

---

## 9. 后续扩展路径

### 9.1 多租户正式启用（等 Keycloak 准备好）

当 Keycloak 准备好稳定的 tenant claim（`organization` / `tenant_id` / `org_id`）后：

1. 配置 `OIDC_TENANT_CLAIMS` 指向正确的 claim name
2. 中间件自动从 JWT 提取真实 tenant_id
3. 所有 `Depends(get_tenant_id)` 自动获得真实租户
4. 数据自动按 `tenants/{tenant_id}/` 目录隔离
5. **无需再改任何代码** — 骨架已完整

### 9.2 Agent 目录策略可选演进

当前：**严格隔离**（非 default 租户只用自己目录的 agent）

如未来需要"全局 agent 作为公共基础 + 租户可覆盖"：
```python
# list_custom_agents 增加 fallback：
def list_custom_agents(*, agents_dir=None, include_global=False):
    agents = _scan_dir(agents_dir or get_paths().agents_dir)
    if include_global and agents_dir:
        global_agents = _scan_dir(get_paths().agents_dir)
        # 去重：tenant 目录的同名 agent 优先
        ...
    return agents
```

只需改一个函数，不影响其他链路。

### 9.3 能力级 ACL（按需）

如未来平台需要控制"租户 A 只能用 model-X，不能用 model-Y"：
- 在平台控制面维护 tenant → capability 映射表
- Gateway 层增加一个轻量中间件，根据映射表过滤响应
- **不在 DeerFlow 内部做** — 这是平台侧的职责

---

## 10. 数据目录结构总览

```
.deer-flow/
├── memory.json                          # default 租户全局 memory
├── USER.md                              # default 租户 user profile
├── thread_registry.json                 # 全局 thread→tenant 映射
├── agents/                              # default 租户 agents
│   ├── research/config.yaml
│   └── analyst/config.yaml
├── threads/                             # 所有租户共享（按 thread_id 隔离）
│   └── {thread_id}/user-data/
│       ├── workspace/
│       ├── uploads/
│       └── outputs/
└── tenants/                             # 非 default 租户数据根目录
    └── {tenant_id}/
        ├── memory.json                  # 租户级 memory
        ├── USER.md                      # 租户 user profile（本次新增）
        └── agents/                      # 租户 agents
            ├── {agent_name}/config.yaml
            └── {agent_name}/memory.json # per-agent memory
```

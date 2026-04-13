# MCP 文档索引

> **最后更新**: 2026-04-10

本索引整理了项目中所有 MCP 相关文档，按阅读顺序和文档定位分类。

---

## 当前权威文档（日常参考）

以下文档描述的是**当前代码真实行为**，是日常开发和配置的首选参考：

| 文档 | 定位 | 说明 |
|------|------|------|
| [MCP逻辑、配置与使用说明.md](MCP逻辑、配置与使用说明.md) | **操作指南（中文）** | 当前代码行为的完整描述：四层架构（配置层→装配层→绑定解析层→运行时层）、分类规则、配置方式、加载时机、排障方式。**新人首先阅读此文档** |
| [../backend/docs/MCP_SERVER.md](../backend/docs/MCP_SERVER.md) | **配置参考（英文）** | 面向快速配置的精简文档：server 字段说明、OAuth、scope-based runtime、multi-tenant、Playwright 示例 |
| [workflow模式下-subagent-mcp-skill-agent-接入与加载说明.md](workflow模式下-subagent-mcp-skill-agent-接入与加载说明.md) | **集成指南** | 澄清 workflow 模式下两套子代理机制的共存关系，以及 MCP/Skill/Agent 在 workflow 中的加载时机和执行流程 |

### 推荐阅读顺序

1. 先看 [MCP逻辑、配置与使用说明.md](MCP逻辑、配置与使用说明.md) 了解整体架构和配置方式
2. 日常配置参考 [MCP_SERVER.md](../backend/docs/MCP_SERVER.md)
3. 需要在 workflow 模式下集成外部 Agent/MCP 时参考 [workflow 集成指南](workflow模式下-subagent-mcp-skill-agent-接入与加载说明.md)

---

## 前瞻设计文档（仍有参考价值）

以下文档包含尚未完全落地的前瞻设计，适合了解未来架构方向：

| 文档 | 定位 |
|------|------|
| [业务型MCP驱动的多智能体框架详细方案设计.md](业务型MCP驱动的多智能体框架详细方案设计.md) | 下一代 MCP 能力管理设计：写操作治理、审批审计、幂等性、增量同步、事件驱动工具等 |

---

## 历史设计文档（已归档）

以下文档记录了 MCP 改造的设计过程，其中的核心理念已落地到当前代码。保留为历史参考，**不应作为当前配置或开发的依据**：

| 文档 | 内容 | 归档原因 |
|------|------|----------|
| [MCP轻管理接入方案.md](MCP轻管理接入方案.md) | 确立"轻管理、重装配"的架构方向和职责边界 | 方向已落地，被权威文档取代 |
| [MCP分类与装配详细方案.md](MCP分类与装配详细方案.md) | 四类 MCP 分类定义和装配规则设计 | 分类已实现，被权威文档取代 |
| [MCP配置Schema_运行时设计_改造清单.md](MCP配置Schema_运行时设计_改造清单.md) | 配置 Schema、运行时类设计、逐步改造清单 | 改造已完成，被权威文档取代 |
| [MCP轻管理改造后端与测试实施文档.md](MCP轻管理改造后端与测试实施文档.md) | 后端和测试团队的实施指南 | 实施已完成，被权威文档取代 |

---

## 对应代码模块

| 模块 | 路径 | 职责 |
|------|------|------|
| 平台配置模型 | `backend/src/config/extensions_config.py` | `McpServerConfig` 定义，三层配置加载 |
| Agent 装配模型 | `backend/src/config/agents_config.py` | `McpBindingConfig`，三层 agent 配置合并 |
| 绑定解析器 | `backend/src/mcp/binding_resolver.py` | 声明式 → 具体配置转换 |
| 运行时管理器 | `backend/src/mcp/runtime_manager.py` | Scope-based 连接生命周期（6 种 scope key） |
| 全局缓存 | `backend/src/mcp/cache.py` | 主 Agent 全局 MCP 工具缓存（多租户分区） |
| MCP 客户端 | `backend/src/mcp/client.py` | Transport 参数构造（stdio/sse/http） |
| 只读过滤 | `backend/src/mcp/tool_filter.py` | 写操作关键字过滤 |
| 健康检查 | `backend/src/mcp/health.py` | SSE/HTTP 健康探测 |
| OAuth | `backend/src/mcp/oauth.py` | Token 获取和自动刷新 |
| Gateway API | `backend/src/gateway/routers/mcp.py` | `GET/PUT /api/mcp/config` |
| 个人 MCP API | `backend/src/gateway/routers/me.py` | `GET/PUT /api/me/mcp/config` |

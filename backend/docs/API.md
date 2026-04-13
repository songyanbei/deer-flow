# API Reference

This document provides a complete reference for the DeerFlow backend APIs.

> **Last updated**: 2026-04-10

## Overview

DeerFlow backend exposes two sets of APIs:

1. **LangGraph API** - Agent interactions, threads, and streaming (`/api/langgraph/*`)
2. **Gateway API** - Models, MCP, skills, agents, runtime, governance, uploads, artifacts, and more (`/api/*`)

All APIs are accessed through the Nginx reverse proxy at port 2026.

**Authentication**: OIDC-based authentication is conditionally enabled via `OIDC_ENABLED` environment variable. When enabled, all Gateway API requests require a valid Bearer token. Multi-tenant identity (`tenant_id`, `user_id`) is extracted from the token.

## LangGraph API

Base URL: `/api/langgraph`

The LangGraph API is provided by the LangGraph server and follows the LangGraph SDK conventions.

### Threads

#### Create Thread

```http
POST /api/langgraph/threads
Content-Type: application/json
```

**Request Body:**
```json
{
  "metadata": {}
}
```

**Response:**
```json
{
  "thread_id": "abc123",
  "created_at": "2024-01-15T10:30:00Z",
  "metadata": {}
}
```

#### Get Thread State

```http
GET /api/langgraph/threads/{thread_id}/state
```

**Response:**
```json
{
  "values": {
    "messages": [...],
    "sandbox": {...},
    "artifacts": [...],
    "thread_data": {...},
    "title": "Conversation Title",
    "resolved_orchestration_mode": "leader",
    "workflow_stage": null,
    "task_pool": [],
    "verified_facts": {}
  },
  "next": [],
  "config": {...}
}
```

### Runs

#### Create Run

Execute the agent with input.

```http
POST /api/langgraph/threads/{thread_id}/runs
Content-Type: application/json
```

**Request Body:**
```json
{
  "input": {
    "messages": [
      {
        "role": "user",
        "content": "Hello, can you help me?"
      }
    ]
  },
  "config": {
    "configurable": {
      "model_name": "gpt-4",
      "thinking_enabled": false,
      "is_plan_mode": false,
      "requested_orchestration_mode": "auto",
      "subagent_enabled": true
    }
  },
  "stream_mode": ["values", "messages"]
}
```

**Configurable Options:**
- `model_name` (string): Override the default model
- `thinking_enabled` (boolean): Enable extended thinking for supported models
- `is_plan_mode` (boolean): Enable TodoList middleware for task tracking
- `requested_orchestration_mode` (string): `"auto"` | `"leader"` | `"workflow"` — force orchestration mode
- `subagent_enabled` (boolean): Enable task delegation tool

**Response:** Server-Sent Events (SSE) stream

```
event: values
data: {"messages": [...], "title": "...", "task_pool": [...]}

event: messages
data: {"content": "Hello! I'd be happy to help.", "role": "assistant"}

event: end
data: {}
```

**Workflow mode** additionally emits task lifecycle events in the SSE stream:
- `task_started` — domain agent begins execution
- `task_running` — progress updates during execution
- `task_completed` / `task_failed` / `task_timed_out` — terminal states

#### Get Run History

```http
GET /api/langgraph/threads/{thread_id}/runs
```

#### Stream Run

```http
POST /api/langgraph/threads/{thread_id}/runs/stream
Content-Type: application/json
```

Same request body as Create Run. Returns SSE stream.

---

## Gateway API

Base URL: `/api`

Built-in endpoints: `GET /health` (health check), `GET /debug/metrics` (debug metrics snapshot).

### Models (`/api/models`)

#### List Models

```http
GET /api/models
```

**Response:** `ModelsListResponse`
```json
{
  "models": [
    {
      "name": "gpt-4",
      "display_name": "GPT-4",
      "supports_thinking": false,
      "supports_vision": true
    }
  ]
}
```

#### Get Model Details

```http
GET /api/models/{model_name}
```

**Response:** `ModelResponse`

### MCP Configuration (`/api/mcp`)

#### Get MCP Config

```http
GET /api/mcp/config
```

**Response:** `McpConfigResponse`
```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "***" },
      "description": "GitHub operations"
    }
  }
}
```

#### Update MCP Config

```http
PUT /api/mcp/config
Content-Type: application/json
```

### Skills (`/api/skills`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/skills` | List all skills |
| `GET` | `/api/skills/{skill_name}` | Get skill details |
| `PUT` | `/api/skills/{skill_name}` | Update skill enabled status |
| `POST` | `/api/skills/install` | Install from `.skill` archive |

### Memory (`/api/memory`)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/memory` | Get memory data (tenant/user scoped) |
| `POST` | `/api/memory/reload` | Force reload from storage file |
| `GET` | `/api/memory/config` | Get memory system configuration |
| `GET` | `/api/memory/status` | Get config + data in one request |

### File Uploads (`/api/threads/{thread_id}/uploads`)

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/threads/{thread_id}/uploads` | Upload files (multipart/form-data) |
| `GET` | `/api/threads/{thread_id}/uploads/list` | List uploaded files |
| `DELETE` | `/api/threads/{thread_id}/uploads/{filename}` | Delete uploaded file |

**Supported Document Formats** (auto-converted to Markdown): PDF, PPT/PPTX, XLS/XLSX, DOC/DOCX.

### Artifacts (`/api/threads/{thread_id}/artifacts`)

```http
GET /api/threads/{thread_id}/artifacts/{path}
```

Query: `?download=true` for Content-Disposition download header.

### Agents (`/api/agents`)

Platform-level agent management (CRUD). Requires `admin` or `owner` role for write operations.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/agents` | List all agents (merged multi-layer view) |
| `GET` | `/api/agents/check?name=xxx` | Check if agent name is available |
| `GET` | `/api/agents/{name}` | Get agent details |
| `POST` | `/api/agents` | Create new agent (admin/owner) |
| `PUT` | `/api/agents/{name}` | Update agent config (admin/owner) |
| `DELETE` | `/api/agents/{name}` | Delete agent (admin/owner) |
| `POST` | `/api/agents/sync` | Batch sync agents (admin/owner) |
| `GET` | `/api/user-profile` | Get user profile (USER.md) |
| `PUT` | `/api/user-profile` | Update user profile |

**Response models**: `AgentsListResponse`, `AgentResponse`, `AgentSyncResponse`, `UserProfileResponse`

### Runtime (`/api/runtime`)

Platform runtime thread management with normalized SSE streaming.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/runtime/threads` | Create runtime thread |
| `GET` | `/api/runtime/threads/{thread_id}` | Get thread binding metadata and state |
| `POST` | `/api/runtime/threads/{thread_id}/messages:stream` | Stream messages into thread (SSE) |

**Response models**: `ThreadCreateResponse`, `ThreadBindingResponse`

### Interventions (`/api/threads/{thread_id}/interventions`)

Resolve safety gate intervention requests during workflow execution.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/threads/{thread_id}/interventions/{request_id}:resolve` | Resolve intervention |

**Request model**: `InterventionResolveRequest`
**Response model**: `InterventionResolveResponse`

### Governance (`/api/governance`)

Operator governance console for policy enforcement review.

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/governance/queue` | List pending governance items |
| `GET` | `/api/governance/history` | List resolved/rejected/failed/expired items |
| `GET` | `/api/governance/{governance_id}` | Get governance item detail |
| `POST` | `/api/governance/{governance_id}:resolve` | Operator resolve action |

**Query filters** (queue/history): `thread_id`, `run_id`, `risk_level`, `source_agent`, `created_from`, `created_to`, `resolved_from`, `resolved_to`, `limit`, `offset`.

**Response models**: `GovernanceListResponse`, `GovernanceItemResponse`, `OperatorResolveResponse`

### Personal Resources (`/api/me`)

User-scoped resource management. Requires identified user (tenant_id + user_id).

#### Personal Agents

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/me/agents` | List personal agents |
| `POST` | `/api/me/agents` | Create personal agent |
| `GET` | `/api/me/agents/{name}` | Get personal agent |
| `PUT` | `/api/me/agents/{name}` | Update personal agent |
| `DELETE` | `/api/me/agents/{name}` | Delete personal agent |

#### Personal Skills

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/me/skills` | List personal skills |
| `GET` | `/api/me/skills/{skill_name}` | Get personal skill |
| `PUT` | `/api/me/skills/{skill_name}` | Update personal skill |
| `POST` | `/api/me/skills/install` | Install personal skill |
| `DELETE` | `/api/me/skills/{skill_name}` | Delete personal skill |

#### Personal MCP Config

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/me/mcp/config` | Get personal MCP config |
| `PUT` | `/api/me/mcp/config` | Update personal MCP config |

### Promotions (`/api/promotions`, `/api/me`)

Workflow for promoting personal agents/skills to tenant-level.

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/me/agents/{name}:promote` | Submit agent for promotion |
| `POST` | `/api/me/skills/{name}:promote` | Submit skill for promotion |
| `GET` | `/api/promotions` | List promotion requests (admin) |
| `POST` | `/api/promotions/{request_id}:resolve` | Admin resolve promotion (admin/owner) |

**Response models**: `PromotionRequestResponse`, `PromotionListResponse`

### Admin (`/api/admin`)

Lifecycle management endpoints. Requires `admin` or `owner` role.

| Method | Path | Description |
|--------|------|-------------|
| `DELETE` | `/api/admin/users/{user_id}?tenant_id=xxx` | Delete all data for a user |
| `DELETE` | `/api/admin/tenants/{tenant_id}` | Decommission entire tenant |
| `POST` | `/api/admin/cleanup/expired-threads?max_age_seconds=604800` | Clean up stale threads |

---

## Error Responses

All APIs return errors in a consistent format:

```json
{
  "detail": "Error message describing what went wrong"
}
```

**HTTP Status Codes:**
- `400` - Bad Request: Invalid input
- `401` - Unauthorized: Missing or invalid token (when OIDC enabled)
- `403` - Forbidden: Insufficient role
- `404` - Not Found: Resource not found
- `422` - Validation Error: Request validation failed
- `500` - Internal Server Error: Server-side error

---

## Authentication

DeerFlow supports OIDC-based authentication via `OIDCAuthMiddleware`, conditionally enabled with the `OIDC_ENABLED` environment variable. When enabled:

- All Gateway API requests require a `Authorization: Bearer <token>` header
- Token is validated against the configured Keycloak JWKS endpoint
- `tenant_id`, `user_id`, `username`, and `role` are extracted from token claims
- Role-based access control: `admin`, `owner`, `member` roles

When OIDC is disabled, fallback identity headers or defaults are used.

---

## Rate Limiting

No rate limiting is implemented by default. For production deployments, configure rate limiting in Nginx:

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;

location /api/ {
    limit_req zone=api burst=20 nodelay;
    proxy_pass http://backend;
}
```

---

## WebSocket Support

The LangGraph server supports WebSocket connections for real-time streaming. Connect to:

```
ws://localhost:2026/api/langgraph/threads/{thread_id}/runs/stream
```

---

## SDK Usage

### Python (LangGraph SDK)

```python
from langgraph_sdk import get_client

client = get_client(url="http://localhost:2026/api/langgraph")

# Create thread
thread = await client.threads.create()

# Run agent (workflow mode)
async for event in client.runs.stream(
    thread["thread_id"],
    "lead_agent",
    input={"messages": [{"role": "user", "content": "Research and compare X vs Y"}]},
    config={"configurable": {
        "model_name": "gpt-4",
        "requested_orchestration_mode": "workflow",
    }},
    stream_mode=["values", "messages"],
):
    print(event)
```

### JavaScript/TypeScript

```typescript
// Using fetch for Gateway API
const response = await fetch('/api/models');
const data = await response.json();
console.log(data.models);

// Using EventSource for streaming
const eventSource = new EventSource(
  `/api/langgraph/threads/${threadId}/runs/stream`
);
eventSource.onmessage = (event) => {
  console.log(JSON.parse(event.data));
};
```

### cURL Examples

```bash
# List models
curl http://localhost:2026/api/models

# Get MCP config
curl http://localhost:2026/api/mcp/config

# Upload file
curl -X POST http://localhost:2026/api/threads/abc123/uploads \
  -F "files=@document.pdf"

# List agents
curl http://localhost:2026/api/agents

# Create runtime thread
curl -X POST http://localhost:2026/api/runtime/threads \
  -H "Content-Type: application/json" \
  -d '{"agent_name": "meeting-agent"}'

# Governance queue
curl http://localhost:2026/api/governance/queue

# Personal agents
curl http://localhost:2026/api/me/agents

# Create thread and run agent
curl -X POST http://localhost:2026/api/langgraph/threads \
  -H "Content-Type: application/json" \
  -d '{}'

curl -X POST http://localhost:2026/api/langgraph/threads/abc123/runs \
  -H "Content-Type: application/json" \
  -d '{
    "input": {"messages": [{"role": "user", "content": "Hello"}]},
    "config": {"configurable": {"model_name": "gpt-4"}}
  }'
```

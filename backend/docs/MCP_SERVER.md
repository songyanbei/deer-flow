# MCP (Model Context Protocol) Configuration

> **Last updated**: 2026-04-10

DeerFlow supports configurable MCP servers and skills to extend its capabilities, which are loaded from a dedicated `extensions_config.json` file in the project root directory.

## Setup

1. Copy `extensions_config.example.json` to `extensions_config.json` in the project root directory.
   ```bash
   cp extensions_config.example.json extensions_config.json
   ```
   
2. Enable the desired MCP servers or skills by setting `"enabled": true`.
3. Configure each server’s command, arguments, and environment variables as needed.
4. No restart needed — MCP runtime detects config file changes via mtime and reloads on next use.

## Server Configuration

Each MCP server supports the following fields:

```json
{
  "mcpServers": {
    "server-name": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "$GITHUB_TOKEN" },
      "description": "GitHub operations",
      "category": "global",
      "domain": null,
      "readonly": false,
      "connect_timeout_seconds": 30,
      "call_timeout_seconds": 60,
      "retry_count": 0,
      "healthcheck_path": null,
      "circuit_breaker_enabled": false
    }
  }
}
```

### Server Categories

MCP servers are classified into four categories for scope-based isolation:

| Category | Description | Scope |
|----------|-------------|-------|
| `global` | Platform-wide, available to main agent | All agents |
| `domain` | Bound to a specific domain agent (set `domain` field) | Single agent |
| `shared` | Available to multiple agents | Cross-agent |
| `ephemeral` | Per-run lifecycle | Reserved for future use |

### Domain-Scoped Example

```json
{
  "mcpServers": {
    "contacts-server": {
      "enabled": true,
      "type": "stdio",
      "command": "node",
      "args": ["contacts-mcp-server.js"],
      "category": "domain",
      "domain": "contacts",
      "description": "Contacts management MCP"
    }
  }
}
```

## Scope-Based MCP Runtime

DeerFlow uses a `McpRuntimeManager` (process-level singleton) that manages MCP connections by scope key:

| Scope Key | Use Case |
|-----------|----------|
| `global` | Platform-wide servers |
| `domain:{agent_name}` | Domain agent-specific servers |
| `tenant:{tenant_id}:global` | Tenant-level global servers |
| `tenant:{tenant_id}:domain:{agent_name}` | Tenant + agent scoped |
| `tenant:{tenant_id}:user:{user_id}:global` | User personal servers |
| `run:{run_id}` | Per-run ephemeral (reserved) |

Each scope has an independent `_ScopedMCPClient` with:
- Lazy connection (on first use)
- Independent tool cache
- Idle time monitoring with automatic eviction
- Async lock-protected lifecycle

### Agent MCP Binding

Agents declare their MCP needs via `McpBindingConfig` in agent configuration:

```yaml
# In agents_config (agent YAML)
mcp_bindings:
  use_global: true          # Include global MCP servers
  domain:
    - contacts-server       # Domain-specific servers
  shared:
    - calendar-server       # Shared servers
  ephemeral: []             # Per-run servers (reserved)
```

The `BindingResolver` (`src/mcp/binding_resolver.py`) translates these declarations to concrete server configs at runtime.

## Multi-Tenant MCP

In multi-tenant mode, MCP configs are loaded with three-layer precedence:

1. **Platform** — `extensions_config.json` in project root
2. **Tenant** — `.deer-flow/tenants/{tenant_id}/extensions_config.json`
3. **User** — `.deer-flow/tenants/{tid}/users/{uid}/extensions_config.json`

Cache invalidation tracks mtime across all three layers. Personal MCP configs are managed via `PUT /api/me/mcp/config`.

## OAuth Support (HTTP/SSE MCP Servers)

For `http` and `sse` MCP servers, DeerFlow supports OAuth token acquisition and automatic token refresh.

- Supported grants: `client_credentials`, `refresh_token`
- Configure per-server `oauth` block in `extensions_config.json`
- Secrets should be provided via environment variables (for example: `$MCP_OAUTH_CLIENT_SECRET`)

Example:

```json
{
   "mcpServers": {
      "secure-http-server": {
         "enabled": true,
         "type": "http",
         "url": "https://api.example.com/mcp",
         "oauth": {
            "enabled": true,
            "token_url": "https://auth.example.com/oauth/token",
            "grant_type": "client_credentials",
            "client_id": "$MCP_OAUTH_CLIENT_ID",
            "client_secret": "$MCP_OAUTH_CLIENT_SECRET",
            "scope": "mcp.read",
            "refresh_skew_seconds": 60
         }
      }
   }
}
```

## Playwright MCP Example

To enable browser automation for frontend validation, add a stdio MCP server using the official Playwright MCP package:

```json
{
  "mcpServers": {
    "playwright": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": [
        "-y",
        "@playwright/mcp@latest",
        "--headless",
        "--isolated",
        "--output-dir",
        "/path/to/project/logs/playwright-mcp"
      ],
      "description": "Official Playwright MCP server for browser automation and frontend UI validation"
    }
  }
}
```

Notes:

- `--headless` is the pragmatic default for agent-driven runs.
- `--isolated` avoids reusing a persistent browser profile between sessions.
- `--output-dir` gives Playwright MCP a stable place to write artifacts such as traces or snapshots if you enable them later.
- If the target machine does not already have a compatible browser installed, install one with `npx playwright install chromium`.

## How It Works

MCP servers expose tools that are automatically discovered and integrated into DeerFlow’s agent system at runtime. Once enabled, these tools become available to agents without additional code changes.

**Tool loading flow**:
1. `McpRuntimeManager.load_scope()` connects to servers for the requested scope
2. Tools are cached per scope with mtime-based invalidation
3. `get_available_tools()` assembles tools based on agent type (main vs domain) and tenant/user context
4. Domain agents only receive their bound MCP tools; main agents receive global-scope tools

## Example Capabilities

MCP servers can provide access to:

- **File systems**
- **Databases** (e.g., PostgreSQL)
- **External APIs** (e.g., GitHub, Brave Search)
- **Browser automation** (e.g., Puppeteer, Playwright)
- **Business systems** (e.g., contacts, meetings, HCM)
- **Custom MCP server implementations**

## Learn More

For detailed documentation about the Model Context Protocol, visit:  
https://modelcontextprotocol.io

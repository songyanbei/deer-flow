# Setup Guide

Quick setup instructions for DeerFlow.

> **Last updated**: 2026-04-10

## Configuration Setup

DeerFlow uses two configuration files in the **project root directory**:
- `config.yaml` — Models, tools, sandbox, memory, summarization, subagents
- `extensions_config.json` — MCP servers and skills enabled state

### Steps

1. **Navigate to project root**:
   ```bash
   cd /path/to/deer-flow
   ```

2. **Copy example configurations**:
   ```bash
   cp config.example.yaml config.yaml
   cp extensions_config.example.json extensions_config.json
   ```

3. **Set environment variables** (recommended):
   ```bash
   # Required: at least one LLM API key
   export OPENAI_API_KEY="your-key-here"

   # Optional: OIDC authentication (multi-tenant)
   export OIDC_ENABLED=true
   export OIDC_ISSUER="https://keycloak.example.com/realms/your-realm"
   export OIDC_JWKS_URI="https://keycloak.example.com/realms/your-realm/protocol/openid-connect/certs"
   export OIDC_AUDIENCE="your-client-id"
   ```

4. **Verify configuration**:
   ```bash
   cd backend
   python -c "from src.config import get_app_config; print('Config loaded:', get_app_config().models[0].name)"
   ```

## Important Notes

- **Location**: Config files should be in `deer-flow/` (project root), not `deer-flow/backend/`
- **Git**: `config.yaml` and `extensions_config.json` are automatically ignored by git (contain secrets)
- **Priority**: If both `backend/config.yaml` and `../config.yaml` exist, backend version takes precedence

## Configuration File Locations

Both config files follow the same search order:

| Priority | `config.yaml` | `extensions_config.json` |
|----------|---------------|--------------------------|
| 1 | `DEER_FLOW_CONFIG_PATH` env var | `DEER_FLOW_EXTENSIONS_CONFIG_PATH` env var |
| 2 | Current directory (`backend/`) | Current directory (`backend/`) |
| 3 | Parent directory (project root) | Parent directory (project root) |

**Recommended**: Place both files in project root (`deer-flow/`).

## Environment Variables

### Required
- One or more LLM API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, etc.

### OIDC Authentication (Optional)
- `OIDC_ENABLED` — Enable OIDC auth (`true`/`1`/`yes`)
- `OIDC_ISSUER` — OAuth issuer URL
- `OIDC_JWKS_URI` — JWKS endpoint for token validation
- `OIDC_AUDIENCE` — OAuth audience

### Gateway
- `GATEWAY_HOST` — Bind host (default: `0.0.0.0`)
- `GATEWAY_PORT` — Bind port (default: `8001`)
- `CORS_ORIGINS` — CORS allowed origins

### Observability (Optional)
- `OTEL_ENABLED` — Enable OpenTelemetry tracing (`false`)
- `OTEL_EXPORTER_OTLP_ENDPOINT` — OTEL endpoint (`http://localhost:4317`)
- `LANGSMITH_TRACING` — Enable LangSmith tracing (`false`)
- `LANGSMITH_API_KEY` — LangSmith API key

### Data Storage
- `DEER_FLOW_HOME` — Custom home directory for `.deer-flow` data

### MCP
- `DEER_FLOW_MCP_INIT_TIMEOUT_SECONDS` — MCP init timeout (default: `15`)

## Sandbox Setup (Optional but Recommended)

If you plan to use Docker/Container-based sandbox (configured in `config.yaml` under `sandbox.use: src.community.aio_sandbox:AioSandboxProvider`), it's highly recommended to pre-pull the container image:

```bash
# From project root
make setup-sandbox
```

**Why pre-pull?**
- The sandbox image (~500MB+) is pulled on first use, causing a long wait
- Pre-pulling provides clear progress indication
- Avoids confusion when first using the agent

If you skip this step, the image will be automatically pulled on first agent execution, which may take several minutes depending on your network speed.

## Troubleshooting

### Config file not found

```bash
# Check where the backend is looking
cd deer-flow/backend
python -c "from src.config.app_config import AppConfig; print(AppConfig.resolve_config_path())"
```

If it can't find the config:
1. Ensure you've copied `config.example.yaml` to `config.yaml`
2. Verify you're in the correct directory
3. Check the file exists: `ls -la ../config.yaml`

### Permission denied

```bash
chmod 600 ../config.yaml  # Protect sensitive configuration
```

## See Also

- [CONFIGURATION.md](CONFIGURATION.md) - Detailed configuration options
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture
- [MCP_SERVER.md](MCP_SERVER.md) - MCP server configuration

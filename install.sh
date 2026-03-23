#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_DIR=${DEER_FLOW_INSTALL_DIR:-/opt/deer-flow}
PORT=${DEER_FLOW_PORT:-2026}
MODEL_BASE_URL=${DEER_FLOW_MODEL_BASE_URL:-}
MODEL_API_KEY=${DEER_FLOW_MODEL_API_KEY:-}
MODEL_NAME=${DEER_FLOW_MODEL_NAME:-qwen-max}
IMAGE_DIR=${DEER_FLOW_IMAGE_DIR:-"$SCRIPT_DIR/images"}

usage() {
  cat <<EOF
Usage: ./install.sh [options]

Options:
  --install-dir PATH      Install directory. Default: /opt/deer-flow
  --port PORT             Public HTTP port. Default: 2026
  --model-base-url URL    Internal Qwen OpenAI-compatible base URL
  --model-api-key KEY     Internal Qwen API key
  --model-name NAME       Model name. Default: qwen-max
  --image-dir PATH        Directory containing offline image tar files
  --help                  Show this help

You can also provide the same values with environment variables:
  DEER_FLOW_INSTALL_DIR
  DEER_FLOW_PORT
  DEER_FLOW_MODEL_BASE_URL
  DEER_FLOW_MODEL_API_KEY
  DEER_FLOW_MODEL_NAME
  DEER_FLOW_IMAGE_DIR
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

find_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    printf '%s\n' 'docker compose'
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    printf '%s\n' 'docker-compose'
    return
  fi

  printf '%s\n' ''
}

prompt_if_empty() {
  var_name=$1
  prompt_text=$2
  current_value=$3

  if [ -n "$current_value" ]; then
    printf '%s' "$current_value"
    return
  fi

  printf "%s: " "$prompt_text" >&2
  IFS= read -r input_value
  printf '%s' "$input_value"
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    date +%s | sha256sum | awk '{print $1}'
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR=$2
      shift 2
      ;;
    --port)
      PORT=$2
      shift 2
      ;;
    --model-base-url)
      MODEL_BASE_URL=$2
      shift 2
      ;;
    --model-api-key)
      MODEL_API_KEY=$2
      shift 2
      ;;
    --model-name)
      MODEL_NAME=$2
      shift 2
      ;;
    --image-dir)
      IMAGE_DIR=$2
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

require_command docker

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not reachable. Please start Docker first." >&2
  exit 1
fi

COMPOSE_CMD=$(find_compose_command)

if [ ! -d "$IMAGE_DIR" ]; then
  echo "Image directory not found: $IMAGE_DIR" >&2
  exit 1
fi

if [ ! -f "$SCRIPT_DIR/docker-compose.yaml" ] && [ ! -f "$SCRIPT_DIR/docker/docker-compose-offline.yaml" ]; then
  echo "Cannot find docker compose template beside install.sh." >&2
  exit 1
fi

EXISTING_ENV_FILE="$INSTALL_DIR/runtime/app.env"
if [ -f "$EXISTING_ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$EXISTING_ENV_FILE"
  MODEL_BASE_URL=${MODEL_BASE_URL:-${OPENAI_BASE_URL:-}}
  MODEL_API_KEY=${MODEL_API_KEY:-${OPENAI_API_KEY:-}}
  MODEL_NAME=${MODEL_NAME:-${DEER_FLOW_MODEL_NAME:-qwen-max}}
fi

MODEL_BASE_URL=$(prompt_if_empty "DEER_FLOW_MODEL_BASE_URL" "Qwen base URL" "$MODEL_BASE_URL")
MODEL_API_KEY=$(prompt_if_empty "DEER_FLOW_MODEL_API_KEY" "Qwen API key" "$MODEL_API_KEY")
MODEL_NAME=$(prompt_if_empty "DEER_FLOW_MODEL_NAME" "Qwen model name" "$MODEL_NAME")

if [ -z "$MODEL_BASE_URL" ] || [ -z "$MODEL_API_KEY" ] || [ -z "$MODEL_NAME" ]; then
  echo "Model base URL, API key, and model name are required." >&2
  exit 1
fi

RUNTIME_DIR="$INSTALL_DIR/runtime"
DATA_DIR="$RUNTIME_DIR/data/deer-flow-home"
BIN_DIR="$INSTALL_DIR/bin"
COMPOSE_TARGET="$INSTALL_DIR/docker-compose.yaml"
COMPOSE_ENV_FILE="$INSTALL_DIR/compose.env"
APP_ENV_FILE="$RUNTIME_DIR/app.env"
CONFIG_FILE="$RUNTIME_DIR/config.yaml"
NETWORK_NAME="deer-flow"

mkdir -p "$RUNTIME_DIR" "$DATA_DIR" "$BIN_DIR"

BETTER_AUTH_SECRET=""
if [ -f "$APP_ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$APP_ENV_FILE"
  BETTER_AUTH_SECRET=${BETTER_AUTH_SECRET:-}
fi

if [ -z "$BETTER_AUTH_SECRET" ]; then
  BETTER_AUTH_SECRET=$(random_secret)
fi

cat > "$APP_ENV_FILE" <<EOF
DEER_FLOW_HOME=/app/backend/.deer-flow
OPENAI_BASE_URL=$MODEL_BASE_URL
OPENAI_API_KEY=$MODEL_API_KEY
DEER_FLOW_MODEL_NAME=$MODEL_NAME
BETTER_AUTH_SECRET=$BETTER_AUTH_SECRET
NODE_ENV=production
EOF

cat > "$CONFIG_FILE" <<'EOF'
models:
  - name: internal-qwen
    display_name: Internal Qwen
    use: langchain_openai:ChatOpenAI
    model: $DEER_FLOW_MODEL_NAME
    api_key: $OPENAI_API_KEY
    base_url: $OPENAI_BASE_URL
    max_tokens: 8192
    temperature: 0.1
    supports_vision: true
    supports_thinking: true
    supports_reasoning_effort: false

tool_groups:
  - name: file:read
  - name: file:write
  - name: bash

tools:
  - name: ls
    group: file:read
    use: src.sandbox.tools:ls_tool

  - name: read_file
    group: file:read
    use: src.sandbox.tools:read_file_tool

  - name: write_file
    group: file:write
    use: src.sandbox.tools:write_file_tool

  - name: str_replace
    group: file:write
    use: src.sandbox.tools:str_replace_tool

  - name: bash
    group: bash
    use: src.sandbox.tools:bash_tool

sandbox:
  use: src.sandbox.local:LocalSandboxProvider

skills:
  container_path: /mnt/skills

title:
  enabled: true
  max_words: 6
  max_chars: 60
  model_name: null

summarization:
  enabled: true
  model_name: null
  trigger:
    - type: tokens
      value: 15564
  keep:
    type: messages
    value: 10
  trim_tokens_to_summarize: 15564
  summary_prompt: null

memory:
  enabled: true
  storage_path: memory.json
  debounce_seconds: 30
  model_name: null
  max_facts: 100
  fact_confidence_threshold: 0.7
  injection_enabled: true
  max_injection_tokens: 2000
EOF

if [ -f "$SCRIPT_DIR/docker-compose.yaml" ]; then
  cp "$SCRIPT_DIR/docker-compose.yaml" "$COMPOSE_TARGET"
else
  cp "$SCRIPT_DIR/docker/docker-compose-offline.yaml" "$COMPOSE_TARGET"
fi

cat > "$COMPOSE_ENV_FILE" <<EOF
DEER_FLOW_RUNTIME_DIR=$RUNTIME_DIR
DEER_FLOW_PORT=$PORT
EOF

for image_tar in "$IMAGE_DIR"/*.tar; do
  if [ ! -f "$image_tar" ]; then
    echo "No image tar files found in $IMAGE_DIR" >&2
    exit 1
  fi
  echo "Loading image: $(basename "$image_tar")"
  docker load -i "$image_tar"
done

cat > "$BIN_DIR/up.sh" <<'EOF'
#!/usr/bin/env sh
set -eu

INSTALL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_DIR="$INSTALL_DIR/runtime"
COMPOSE_TARGET="$INSTALL_DIR/docker-compose.yaml"
COMPOSE_ENV_FILE="$INSTALL_DIR/compose.env"
NETWORK_NAME="deer-flow"

find_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    printf '%s\n' 'docker compose'
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    printf '%s\n' 'docker-compose'
    return
  fi

  printf '%s\n' ''
}

run_without_compose() {
  if ! docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    docker network create "$NETWORK_NAME" >/dev/null
  fi

  docker rm -f deer-flow-frontend deer-flow-gateway deer-flow-langgraph deer-flow-nginx >/dev/null 2>&1 || true

  docker run -d \
    --name deer-flow-frontend \
    --network "$NETWORK_NAME" \
    --network-alias frontend \
    --restart unless-stopped \
    --env-file "$RUNTIME_DIR/app.env" \
    -e NODE_ENV=production \
    -e HOSTNAME=0.0.0.0 \
    -e PORT=3000 \
    -e SKIP_ENV_VALIDATION=1 \
    deer-flow/frontend:offline >/dev/null

  docker run -d \
    --name deer-flow-gateway \
    --network "$NETWORK_NAME" \
    --network-alias gateway \
    --restart unless-stopped \
    --env-file "$RUNTIME_DIR/app.env" \
    -v "$RUNTIME_DIR/app.env:/app/backend/.env:ro" \
    -v "$RUNTIME_DIR/config.yaml:/app/config.yaml:ro" \
    -v "$RUNTIME_DIR/data/deer-flow-home:/app/backend/.deer-flow" \
    -w /app/backend \
    deer-flow/backend:offline \
    uvicorn src.gateway.app:app --host 0.0.0.0 --port 8001 >/dev/null

  docker run -d \
    --name deer-flow-langgraph \
    --network "$NETWORK_NAME" \
    --network-alias langgraph \
    --restart unless-stopped \
    --env-file "$RUNTIME_DIR/app.env" \
    -v "$RUNTIME_DIR/app.env:/app/backend/.env:ro" \
    -v "$RUNTIME_DIR/config.yaml:/app/config.yaml:ro" \
    -v "$RUNTIME_DIR/data/deer-flow-home:/app/backend/.deer-flow" \
    -w /app/backend \
    deer-flow/backend:offline \
    langgraph dev --no-browser --allow-blocking --no-reload --host 0.0.0.0 --port 2024 >/dev/null

  DEER_FLOW_PORT=$(awk -F= '/^DEER_FLOW_PORT=/{print $2}' "$COMPOSE_ENV_FILE")
  docker run -d \
    --name deer-flow-nginx \
    --network "$NETWORK_NAME" \
    --restart unless-stopped \
    -p "${DEER_FLOW_PORT}:2026" \
    deer-flow/nginx:offline >/dev/null
}

COMPOSE_CMD=$(find_compose_command)
if [ -n "$COMPOSE_CMD" ]; then
  # shellcheck disable=SC2086
  $COMPOSE_CMD --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_TARGET" up -d
else
  run_without_compose
fi
EOF

cat > "$BIN_DIR/down.sh" <<'EOF'
#!/usr/bin/env sh
set -eu

INSTALL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMPOSE_TARGET="$INSTALL_DIR/docker-compose.yaml"
COMPOSE_ENV_FILE="$INSTALL_DIR/compose.env"
NETWORK_NAME="deer-flow"

find_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    printf '%s\n' 'docker compose'
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    printf '%s\n' 'docker-compose'
    return
  fi

  printf '%s\n' ''
}

COMPOSE_CMD=$(find_compose_command)
if [ -n "$COMPOSE_CMD" ]; then
  # shellcheck disable=SC2086
  $COMPOSE_CMD --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_TARGET" down
else
  docker rm -f deer-flow-nginx deer-flow-langgraph deer-flow-gateway deer-flow-frontend >/dev/null 2>&1 || true
  docker network rm "$NETWORK_NAME" >/dev/null 2>&1 || true
fi
EOF

cat > "$BIN_DIR/logs.sh" <<'EOF'
#!/usr/bin/env sh
set -eu

INSTALL_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
COMPOSE_TARGET="$INSTALL_DIR/docker-compose.yaml"
COMPOSE_ENV_FILE="$INSTALL_DIR/compose.env"

find_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    printf '%s\n' 'docker compose'
    return
  fi

  if command -v docker-compose >/dev/null 2>&1; then
    printf '%s\n' 'docker-compose'
    return
  fi

  printf '%s\n' ''
}

COMPOSE_CMD=$(find_compose_command)
if [ -n "$COMPOSE_CMD" ]; then
  # shellcheck disable=SC2086
  $COMPOSE_CMD --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_TARGET" logs -f
else
  echo "Use one of the following commands:"
  echo "  docker logs -f deer-flow-nginx"
  echo "  docker logs -f deer-flow-langgraph"
  echo "  docker logs -f deer-flow-gateway"
  echo "  docker logs -f deer-flow-frontend"
fi
EOF

chmod +x "$BIN_DIR/up.sh" "$BIN_DIR/down.sh" "$BIN_DIR/logs.sh"

"$BIN_DIR/up.sh"

cat <<EOF

DeerFlow is installed.

Install dir : $INSTALL_DIR
Access URL  : http://<server-ip>:$PORT

Manage services:
  Start : $BIN_DIR/up.sh
  Stop  : $BIN_DIR/down.sh
  Logs  : $BIN_DIR/logs.sh
EOF

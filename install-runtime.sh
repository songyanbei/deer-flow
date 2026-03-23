#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PAYLOAD_DIR="$SCRIPT_DIR/payload"

INSTALL_DIR=${DEER_FLOW_INSTALL_DIR:-/opt/deer-flow}
MODEL_BASE_URL=${DEER_FLOW_MODEL_BASE_URL:-}
MODEL_API_KEY=${DEER_FLOW_MODEL_API_KEY:-}
MODEL_NAME=${DEER_FLOW_MODEL_NAME:-}
PUBLIC_PORT=${DEER_FLOW_PUBLIC_PORT:-2026}
FORCE=0
NO_START=0

usage() {
  cat <<EOF
Usage: ./install.sh [options]

Options:
  --install-dir PATH      Install directory. Default: /opt/deer-flow
  --model-base-url URL    Internal OpenAI-compatible Qwen base URL
  --model-api-key KEY     Internal OpenAI-compatible Qwen API key
  --model-name NAME       Model name
  --public-port PORT      Public nginx port. Default: 2026
  --force                 Overwrite runtime payload files
  --no-start              Install only, do not start services
  --help                  Show this help
EOF
}

random_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    date +%s | sha256sum | awk '{print $1}'
  fi
}

require_file() {
  if [ ! -f "$1" ]; then
    echo "Missing required file: $1" >&2
    exit 1
  fi
}

require_dir() {
  if [ ! -d "$1" ]; then
    echo "Missing required directory: $1" >&2
    exit 1
  fi
}

set_env_value() {
  key=$1
  value=$2
  file=$3
  tmp_file="${file}.tmp"

  if [ ! -f "$file" ]; then
    printf '%s=%s\n' "$key" "$value" > "$file"
    return
  fi

  awk -v k="$key" -v v="$value" -F= '
    BEGIN { updated = 0 }
    $1 == k { print k "=" v; updated = 1; next }
    { print }
    END {
      if (!updated) {
        print k "=" v
      }
    }
  ' "$file" > "$tmp_file"
  mv "$tmp_file" "$file"
}

get_env_value() {
  key=$1
  file=$2

  if [ ! -f "$file" ]; then
    return
  fi

  awk -F= -v k="$key" '
    $1 == k {
      print substr($0, index($0, "=") + 1)
    }
  ' "$file" | tail -n 1
}

copy_tree() {
  src=$1
  dst=$2

  mkdir -p "$dst"
  cp -a "$src"/. "$dst"/
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --install-dir)
      INSTALL_DIR=$2
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
    --public-port)
      PUBLIC_PORT=$2
      shift 2
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --no-start)
      NO_START=1
      shift
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

if [ ! -d "$PAYLOAD_DIR" ]; then
  echo "Payload directory not found: $PAYLOAD_DIR" >&2
  exit 1
fi

if [ ! -f "$PAYLOAD_DIR/runtime/python/bin/python3" ] && [ ! -f "$PAYLOAD_DIR/runtime/python/bin/python" ]; then
  echo "Bundled Python runtime is missing from payload." >&2
  exit 1
fi

if [ ! -f "$PAYLOAD_DIR/runtime/node/bin/node" ]; then
  echo "Bundled Node runtime is missing from payload." >&2
  exit 1
fi

mkdir -p "$INSTALL_DIR"

for required_dir in backend frontend-runtime runtime skills templates; do
  if [ ! -d "$PAYLOAD_DIR/$required_dir" ]; then
    echo "Bundle payload is incomplete: missing $required_dir" >&2
    exit 1
  fi
done

if [ "$FORCE" -eq 1 ]; then
  rm -rf \
    "$INSTALL_DIR/backend" \
    "$INSTALL_DIR/frontend-runtime" \
    "$INSTALL_DIR/runtime" \
    "$INSTALL_DIR/skills" \
    "$INSTALL_DIR/templates"
fi

copy_tree "$PAYLOAD_DIR/backend" "$INSTALL_DIR/backend"
copy_tree "$PAYLOAD_DIR/frontend-runtime" "$INSTALL_DIR/frontend-runtime"
copy_tree "$PAYLOAD_DIR/runtime" "$INSTALL_DIR/runtime"
copy_tree "$PAYLOAD_DIR/skills" "$INSTALL_DIR/skills"
copy_tree "$PAYLOAD_DIR/templates" "$INSTALL_DIR/templates"

cp "$PAYLOAD_DIR/start-services.sh" "$INSTALL_DIR/start-services.sh"
cp "$PAYLOAD_DIR/stop-services.sh" "$INSTALL_DIR/stop-services.sh"

require_dir "$INSTALL_DIR/backend/src"
require_file "$INSTALL_DIR/backend/langgraph.json"
require_file "$INSTALL_DIR/backend/pyproject.toml"
require_file "$INSTALL_DIR/frontend-runtime/server.js"
require_dir "$INSTALL_DIR/runtime/python-packages"
require_dir "$INSTALL_DIR/runtime/python-packages/langgraph_cli"
require_file "$INSTALL_DIR/runtime/python-packages/langgraph_cli/__init__.py"
require_file "$INSTALL_DIR/runtime/node/bin/node"
require_file "$INSTALL_DIR/runtime/nginx/sbin/nginx"
require_file "$INSTALL_DIR/start-services.sh"
require_file "$INSTALL_DIR/stop-services.sh"
require_file "$INSTALL_DIR/templates/nginx.conf.template"

mkdir -p \
  "$INSTALL_DIR/logs" \
  "$INSTALL_DIR/nginx/conf" \
  "$INSTALL_DIR/nginx/tmp/client_body" \
  "$INSTALL_DIR/nginx/tmp/proxy" \
  "$INSTALL_DIR/nginx/tmp/fastcgi" \
  "$INSTALL_DIR/nginx/tmp/uwsgi" \
  "$INSTALL_DIR/nginx/tmp/scgi" \
  "$INSTALL_DIR/.dev-runtime" \
  "$INSTALL_DIR/backend/.deer-flow"

ENV_FILE="$INSTALL_DIR/.env"
CONFIG_FILE="$INSTALL_DIR/config.yaml"
TEMPLATE_CONFIG="$INSTALL_DIR/templates/config.yaml"
TEMPLATE_ENV="$INSTALL_DIR/templates/.env.example"
EXTENSIONS_TEMPLATE="$INSTALL_DIR/templates/extensions_config.json"
NGINX_TEMPLATE="$INSTALL_DIR/templates/nginx.conf.template"

if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$TEMPLATE_ENV" ]; then
    cp "$TEMPLATE_ENV" "$ENV_FILE"
  else
    : > "$ENV_FILE"
  fi
fi

if [ ! -f "$CONFIG_FILE" ] && [ -f "$TEMPLATE_CONFIG" ]; then
  cp "$TEMPLATE_CONFIG" "$CONFIG_FILE"
fi

if [ ! -f "$INSTALL_DIR/extensions_config.json" ] && [ -f "$EXTENSIONS_TEMPLATE" ]; then
  cp "$EXTENSIONS_TEMPLATE" "$INSTALL_DIR/extensions_config.json"
fi

EXISTING_MODEL_BASE_URL=$(get_env_value "OPENAI_BASE_URL" "$ENV_FILE")
EXISTING_MODEL_API_KEY=$(get_env_value "OPENAI_API_KEY" "$ENV_FILE")
EXISTING_MODEL_NAME=$(get_env_value "DEER_FLOW_MODEL_NAME" "$ENV_FILE")

BETTER_AUTH_SECRET=""
if [ -f "$ENV_FILE" ]; then
  BETTER_AUTH_SECRET=$(awk -F= '/^BETTER_AUTH_SECRET=/{print substr($0, index($0, "=") + 1)}' "$ENV_FILE" | tail -n 1)
fi
if [ -z "$BETTER_AUTH_SECRET" ]; then
  BETTER_AUTH_SECRET=$(random_secret)
fi

NGINX_USER=$(id -un 2>/dev/null || printf '%s' root)

if [ -n "$MODEL_BASE_URL" ]; then
  EFFECTIVE_MODEL_BASE_URL=$MODEL_BASE_URL
else
  EFFECTIVE_MODEL_BASE_URL=${EXISTING_MODEL_BASE_URL:-}
fi

if [ -n "$MODEL_API_KEY" ]; then
  EFFECTIVE_MODEL_API_KEY=$MODEL_API_KEY
else
  EFFECTIVE_MODEL_API_KEY=${EXISTING_MODEL_API_KEY:-}
fi

if [ -n "$MODEL_NAME" ]; then
  EFFECTIVE_MODEL_NAME=$MODEL_NAME
else
  EFFECTIVE_MODEL_NAME=${EXISTING_MODEL_NAME:-}
fi

set_env_value "DEER_FLOW_HOME" "$INSTALL_DIR/backend/.deer-flow" "$ENV_FILE"
set_env_value "DEER_FLOW_CONFIG_PATH" "$INSTALL_DIR/config.yaml" "$ENV_FILE"
set_env_value "OPENAI_BASE_URL" "$EFFECTIVE_MODEL_BASE_URL" "$ENV_FILE"
set_env_value "OPENAI_API_KEY" "$EFFECTIVE_MODEL_API_KEY" "$ENV_FILE"
set_env_value "DEER_FLOW_MODEL_NAME" "$EFFECTIVE_MODEL_NAME" "$ENV_FILE"
set_env_value "BETTER_AUTH_SECRET" "$BETTER_AUTH_SECRET" "$ENV_FILE"
set_env_value "DEER_FLOW_PUBLIC_PORT" "$PUBLIC_PORT" "$ENV_FILE"
set_env_value "NODE_ENV" "production" "$ENV_FILE"
set_env_value "NEXT_PUBLIC_BACKEND_BASE_URL" "" "$ENV_FILE"
set_env_value "NEXT_PUBLIC_LANGGRAPH_BASE_URL" "/api/langgraph" "$ENV_FILE"

ln -sfn ../.env "$INSTALL_DIR/backend/.env"

if [ -f "$NGINX_TEMPLATE" ]; then
  sed \
    -e "s|__INSTALL_DIR__|$INSTALL_DIR|g" \
    -e "s|__PUBLIC_PORT__|$PUBLIC_PORT|g" \
    -e "s|__NGINX_USER__|$NGINX_USER|g" \
    "$NGINX_TEMPLATE" > "$INSTALL_DIR/nginx/conf/nginx.conf"
fi
require_file "$INSTALL_DIR/nginx/conf/nginx.conf"

chmod +x \
  "$INSTALL_DIR/start-services.sh" \
  "$INSTALL_DIR/stop-services.sh" \
  "$INSTALL_DIR/runtime/node/bin/node" \
  "$INSTALL_DIR/runtime/nginx/sbin/nginx"

if [ -f "$INSTALL_DIR/runtime/python/bin/python3" ]; then
  chmod +x "$INSTALL_DIR/runtime/python/bin/python3"
fi
if [ -f "$INSTALL_DIR/runtime/python/bin/python" ]; then
  chmod +x "$INSTALL_DIR/runtime/python/bin/python"
fi

mkdir -p "$INSTALL_DIR/bin"

cat > "$INSTALL_DIR/bin/up.sh" <<EOF
#!/usr/bin/env sh
set -eu
"$INSTALL_DIR/start-services.sh"
EOF

cat > "$INSTALL_DIR/bin/down.sh" <<EOF
#!/usr/bin/env sh
set -eu
"$INSTALL_DIR/stop-services.sh"
EOF

cat > "$INSTALL_DIR/bin/logs.sh" <<EOF
#!/usr/bin/env sh
set -eu
echo "Frontend : $INSTALL_DIR/logs/frontend.log"
echo "Gateway  : $INSTALL_DIR/logs/gateway.log"
echo "LangGraph: $INSTALL_DIR/logs/langgraph.log"
echo "Nginx boot: $INSTALL_DIR/logs/nginx-bootstrap.log"
echo "Nginx    : $INSTALL_DIR/logs/nginx-error.log"
tail -n 200 -f \
  "$INSTALL_DIR/logs/frontend.log" \
  "$INSTALL_DIR/logs/gateway.log" \
  "$INSTALL_DIR/logs/langgraph.log" \
  "$INSTALL_DIR/logs/nginx-bootstrap.log" \
  "$INSTALL_DIR/logs/nginx-error.log" \
  "$INSTALL_DIR/logs/nginx-access.log"
EOF

chmod +x "$INSTALL_DIR/bin/up.sh" "$INSTALL_DIR/bin/down.sh" "$INSTALL_DIR/bin/logs.sh"

MODEL_CONFIG_COMPLETE=1
if [ -z "$EFFECTIVE_MODEL_BASE_URL" ] || [ -z "$EFFECTIVE_MODEL_API_KEY" ] || [ -z "$EFFECTIVE_MODEL_NAME" ]; then
  MODEL_CONFIG_COMPLETE=0
fi

if [ "$NO_START" -eq 0 ] && [ "$MODEL_CONFIG_COMPLETE" -eq 1 ]; then
  "$INSTALL_DIR/start-services.sh"
fi

cat <<EOF

DeerFlow non-Docker runtime is installed.

Install dir : $INSTALL_DIR
Web URL     : http://<server-ip>:$PUBLIC_PORT
Gateway API : http://<server-ip>:$PUBLIC_PORT/health
LangGraph   : http://<server-ip>:$PUBLIC_PORT/api/langgraph/docs

Manage services:
  Start : $INSTALL_DIR/bin/up.sh
  Stop  : $INSTALL_DIR/bin/down.sh
  Logs  : $INSTALL_DIR/bin/logs.sh
EOF

if [ "$MODEL_CONFIG_COMPLETE" -eq 0 ]; then
  cat <<EOF

Model configuration is incomplete, so services were not started automatically.
Update these values in $ENV_FILE, then run:
  $INSTALL_DIR/bin/up.sh
EOF
fi

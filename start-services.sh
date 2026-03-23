#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_DIR="$SCRIPT_DIR/.dev-runtime"
LOG_DIR="$SCRIPT_DIR/logs"
ENV_FILE="$SCRIPT_DIR/.env"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend-runtime"
PYTHON_BIN="$SCRIPT_DIR/runtime/python/bin/python3"
PYTHON_PACKAGES="$SCRIPT_DIR/runtime/python-packages"
NODE_BIN="$SCRIPT_DIR/runtime/node/bin/node"
NGINX_BIN="$SCRIPT_DIR/runtime/nginx/sbin/nginx"
NGINX_LIB_DIR="$SCRIPT_DIR/runtime/nginx/lib"
NGINX_CONF="$SCRIPT_DIR/nginx/conf/nginx.conf"

if [ ! -x "$PYTHON_BIN" ] && [ -x "$SCRIPT_DIR/runtime/python/bin/python" ]; then
  PYTHON_BIN="$SCRIPT_DIR/runtime/python/bin/python"
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Bundled Python runtime not found." >&2
  exit 1
fi

if [ ! -x "$NODE_BIN" ]; then
  echo "Bundled Node runtime not found." >&2
  exit 1
fi

if [ ! -x "$NGINX_BIN" ]; then
  echo "Bundled nginx runtime not found." >&2
  exit 1
fi

if [ ! -f "$NGINX_CONF" ]; then
  echo "Bundled nginx config not found." >&2
  exit 1
fi

ensure_dir() {
  if [ ! -d "$1" ]; then
    mkdir -p "$1"
  fi
}

load_env_file() {
  if [ ! -f "$1" ]; then
    return
  fi

  while IFS= read -r line || [ -n "$line" ]; do
    trimmed=$(printf '%s' "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    case "$trimmed" in
      ''|\#*)
        continue
        ;;
    esac

    key=${trimmed%%=*}
    value=${trimmed#*=}

    if [ "${value#\"}" != "$value" ] && [ "${value%\"}" != "$value" ]; then
      value=${value#\"}
      value=${value%\"}
    fi

    if [ "${value#\'}" != "$value" ] && [ "${value%\'}" != "$value" ]; then
      value=${value#\'}
      value=${value%\'}
    fi

    export "$key=$value"
  done < "$1"
}

wait_for_port() {
  name=$1
  port=$2
  timeout_seconds=$3

  "$PYTHON_BIN" - "$name" "$port" "$timeout_seconds" <<'PY'
import socket
import sys
import time

name = sys.argv[1]
port = int(sys.argv[2])
timeout_seconds = int(sys.argv[3])
deadline = time.time() + timeout_seconds

while time.time() < deadline:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            time.sleep(0.5)
            continue
        sys.exit(0)

print(f"{name} did not start listening on 127.0.0.1:{port} within {timeout_seconds} seconds.", file=sys.stderr)
sys.exit(1)
PY
}

start_process() {
  name=$1
  workdir=$2
  logfile=$3
  port=$4
  timeout_seconds=$5
  shift 5

  ensure_dir "$STATE_DIR/pids"

  (
    cd "$workdir"
    nohup "$@" > "$logfile" 2>&1 &
    printf '%s' "$!" > "$STATE_DIR/pids/$name.pid"
  )

  wait_for_port "$name" "$port" "$timeout_seconds"
}

ensure_dir "$STATE_DIR"
ensure_dir "$LOG_DIR"
ensure_dir "$SCRIPT_DIR/nginx/conf"
ensure_dir "$SCRIPT_DIR/nginx/tmp/client_body"
ensure_dir "$SCRIPT_DIR/nginx/tmp/proxy"
ensure_dir "$SCRIPT_DIR/nginx/tmp/fastcgi"
ensure_dir "$SCRIPT_DIR/nginx/tmp/uwsgi"
ensure_dir "$SCRIPT_DIR/nginx/tmp/scgi"
load_env_file "$ENV_FILE"

if [ -z "${DEER_FLOW_HOME:-}" ]; then
  export DEER_FLOW_HOME="$BACKEND_DIR/.deer-flow"
fi

export PYTHONPATH="$PYTHON_PACKAGES:$BACKEND_DIR${PYTHONPATH:+:$PYTHONPATH}"
export NODE_ENV="${NODE_ENV:-production}"
export HOSTNAME=0.0.0.0
export PORT=3000
export DEER_FLOW_PUBLIC_PORT="${DEER_FLOW_PUBLIC_PORT:-2026}"

ln -sfn ../.env "$BACKEND_DIR/.env"

"$SCRIPT_DIR/stop-services.sh" --quiet || true

start_process \
  langgraph \
  "$BACKEND_DIR" \
  "$LOG_DIR/langgraph.log" \
  2024 \
  90 \
  "$PYTHON_BIN" -m langgraph_cli dev \
    --config "$BACKEND_DIR/langgraph.json" \
    --host 0.0.0.0 \
    --port 2024 \
    --no-browser \
    --allow-blocking \
    --no-reload

start_process \
  gateway \
  "$BACKEND_DIR" \
  "$LOG_DIR/gateway.log" \
  8001 \
  60 \
  "$PYTHON_BIN" -m uvicorn src.gateway.app:app \
    --host 0.0.0.0 \
    --port 8001

start_process \
  frontend \
  "$FRONTEND_DIR" \
  "$LOG_DIR/frontend.log" \
  3000 \
  120 \
  "$NODE_BIN" server.js

start_process \
  nginx \
  "$SCRIPT_DIR" \
  "$LOG_DIR/nginx-bootstrap.log" \
  "$DEER_FLOW_PUBLIC_PORT" \
  60 \
  env "LD_LIBRARY_PATH=$NGINX_LIB_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  "$NGINX_BIN" -e stderr -p "$SCRIPT_DIR/runtime/nginx" -c "$NGINX_CONF" -g "daemon off;"

cat > "$STATE_DIR/services.env" <<EOF
LANGGRAPH_PID=$(cat "$STATE_DIR/pids/langgraph.pid")
GATEWAY_PID=$(cat "$STATE_DIR/pids/gateway.pid")
FRONTEND_PID=$(cat "$STATE_DIR/pids/frontend.pid")
NGINX_PID=$(cat "$STATE_DIR/pids/nginx.pid")
EOF

cat <<EOF

DeerFlow services are ready.

Web      : http://127.0.0.1:$DEER_FLOW_PUBLIC_PORT
Gateway  : http://127.0.0.1:$DEER_FLOW_PUBLIC_PORT/health
LangGraph: http://127.0.0.1:$DEER_FLOW_PUBLIC_PORT/api/langgraph/docs
Internal frontend : http://127.0.0.1:3000
Internal gateway  : http://127.0.0.1:8001
Internal langgraph: http://127.0.0.1:2024

Logs:
  $LOG_DIR/frontend.log
  $LOG_DIR/gateway.log
  $LOG_DIR/langgraph.log
  $LOG_DIR/nginx-bootstrap.log
  $LOG_DIR/nginx-access.log
  $LOG_DIR/nginx-error.log
EOF

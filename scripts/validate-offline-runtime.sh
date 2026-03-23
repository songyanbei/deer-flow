#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

BUNDLE_ARCHIVE_DEFAULT="$REPO_ROOT/dist/offline-runtime/deer-flow-runtime-linux-amd64.tar.gz"
BUNDLE_DIR_DEFAULT="$REPO_ROOT/dist/offline-runtime/deer-flow-runtime-linux-amd64"
BUNDLE_ARCHIVE=""
BUNDLE_DIR=""
INSTALL_DIR=""
KEEP_WORKDIR=0
SKIP_START=0

usage() {
  cat <<EOF
Usage: ./scripts/validate-offline-runtime.sh [options]

Options:
  --bundle-archive PATH   Validate a built tar.gz bundle
  --bundle-dir PATH       Validate an already extracted bundle directory
  --install-dir PATH      Temporary install directory used for smoke testing
  --keep-workdir          Keep temporary extracted/install directories
  --skip-start            Skip install/start smoke test, only validate structure
  --help                  Show this help

Defaults:
  --bundle-archive $BUNDLE_ARCHIVE_DEFAULT
  --bundle-dir     $BUNDLE_DIR_DEFAULT
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
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

wait_http_ok() {
  name=$1
  url=$2
  timeout_seconds=$3

  deadline=$(( $(date +%s) + timeout_seconds ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    status_code=$(curl -sS -o /dev/null -w "%{http_code}" "$url" || true)
    case "$status_code" in
      200|204|301|302|307|308)
        return 0
        ;;
    esac
    sleep 1
  done

  echo "$name did not return a healthy HTTP response from $url within $timeout_seconds seconds." >&2
  exit 1
}

cleanup() {
  if [ "${ACTIVE_INSTALL_DIR:-}" != "" ] && [ -x "$ACTIVE_INSTALL_DIR/bin/down.sh" ]; then
    "$ACTIVE_INSTALL_DIR/bin/down.sh" >/dev/null 2>&1 || true
  fi

  if [ "$KEEP_WORKDIR" -eq 0 ]; then
    if [ "${TEMP_BUNDLE_ROOT:-}" != "" ] && [ -d "$TEMP_BUNDLE_ROOT" ]; then
      rm -rf "$TEMP_BUNDLE_ROOT"
    fi
    if [ "${ACTIVE_INSTALL_DIR:-}" != "" ] && [ -d "$ACTIVE_INSTALL_DIR" ]; then
      rm -rf "$ACTIVE_INSTALL_DIR"
    fi
  fi
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --bundle-archive)
      BUNDLE_ARCHIVE=$2
      shift 2
      ;;
    --bundle-dir)
      BUNDLE_DIR=$2
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR=$2
      shift 2
      ;;
    --keep-workdir)
      KEEP_WORKDIR=1
      shift
      ;;
    --skip-start)
      SKIP_START=1
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

require_command tar
require_command curl
require_command mktemp

if [ -z "$BUNDLE_ARCHIVE" ] && [ -z "$BUNDLE_DIR" ]; then
  if [ -f "$BUNDLE_ARCHIVE_DEFAULT" ]; then
    BUNDLE_ARCHIVE=$BUNDLE_ARCHIVE_DEFAULT
  elif [ -d "$BUNDLE_DIR_DEFAULT" ]; then
    BUNDLE_DIR=$BUNDLE_DIR_DEFAULT
  else
    echo "No bundle archive or bundle directory found. Pass --bundle-archive or --bundle-dir." >&2
    exit 1
  fi
fi

if [ -n "$BUNDLE_ARCHIVE" ] && [ -n "$BUNDLE_DIR" ]; then
  echo "Pass either --bundle-archive or --bundle-dir, not both." >&2
  exit 1
fi

ACTIVE_INSTALL_DIR=""
TEMP_BUNDLE_ROOT=""
trap cleanup EXIT INT TERM

if [ -n "$BUNDLE_ARCHIVE" ]; then
  require_file "$BUNDLE_ARCHIVE"
  TEMP_BUNDLE_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/deer-flow-bundle-XXXXXX")
  echo "Extracting bundle archive into $TEMP_BUNDLE_ROOT ..."
  tar -xzf "$BUNDLE_ARCHIVE" -C "$TEMP_BUNDLE_ROOT"
  extracted_roots=$(find "$TEMP_BUNDLE_ROOT" -mindepth 1 -maxdepth 1 -type d | wc -l | awk '{print $1}')
  if [ "$extracted_roots" -ne 1 ]; then
    echo "Expected exactly one extracted bundle root inside $TEMP_BUNDLE_ROOT." >&2
    find "$TEMP_BUNDLE_ROOT" -mindepth 1 -maxdepth 1 >&2 || true
    exit 1
  fi
  BUNDLE_DIR=$(find "$TEMP_BUNDLE_ROOT" -mindepth 1 -maxdepth 1 -type d | head -n 1)
fi

require_dir "$BUNDLE_DIR"

echo "Validating bundle structure in $BUNDLE_DIR ..."
require_file "$BUNDLE_DIR/install.sh"
require_dir "$BUNDLE_DIR/payload"
require_dir "$BUNDLE_DIR/payload/backend/src"
require_file "$BUNDLE_DIR/payload/backend/langgraph.json"
require_file "$BUNDLE_DIR/payload/backend/pyproject.toml"
require_file "$BUNDLE_DIR/payload/frontend-runtime/server.js"
require_file "$BUNDLE_DIR/payload/runtime/node/bin/node"
require_file "$BUNDLE_DIR/payload/runtime/nginx/sbin/nginx"
require_dir "$BUNDLE_DIR/payload/runtime/python-packages"
require_file "$BUNDLE_DIR/payload/start-services.sh"
require_file "$BUNDLE_DIR/payload/stop-services.sh"
require_dir "$BUNDLE_DIR/payload/templates"
require_file "$BUNDLE_DIR/payload/templates/nginx.conf.template"

PYTHON_BIN="$BUNDLE_DIR/payload/runtime/python/bin/python3"
if [ ! -x "$PYTHON_BIN" ] && [ -x "$BUNDLE_DIR/payload/runtime/python/bin/python" ]; then
  PYTHON_BIN="$BUNDLE_DIR/payload/runtime/python/bin/python"
fi
require_file "$PYTHON_BIN"

echo "Validating bundled Python imports ..."
PYTHONPATH="$BUNDLE_DIR/payload/runtime/python-packages:$BUNDLE_DIR/payload/backend" \
  "$PYTHON_BIN" - <<'PY'
import importlib
import sys

required_modules = [
    "langgraph_cli",
    "langgraph_api",
    "uvicorn",
    "fastapi",
    "src.agents",
]

missing = []
for module_name in required_modules:
    try:
        importlib.import_module(module_name)
    except Exception as exc:
        missing.append(f"{module_name} ({exc})")

if missing:
    print("Offline runtime validation failed. Missing or broken imports:", file=sys.stderr)
    for item in missing:
        print(f"  - {item}", file=sys.stderr)
    sys.exit(1)
PY

echo "Bundle structure and Python imports look good."

if [ "$SKIP_START" -eq 1 ]; then
  echo "Skipped install/start smoke test."
  exit 0
fi

if [ -z "$INSTALL_DIR" ]; then
  INSTALL_DIR=$(mktemp -d "${TMPDIR:-/tmp}/deer-flow-install-XXXXXX")
else
  mkdir -p "$INSTALL_DIR"
fi
ACTIVE_INSTALL_DIR="$INSTALL_DIR"

echo "Installing bundle into $ACTIVE_INSTALL_DIR ..."
sh "$BUNDLE_DIR/install.sh" \
  --install-dir "$ACTIVE_INSTALL_DIR" \
  --public-port 2026 \
  --model-base-url "http://127.0.0.1:11434/v1" \
  --model-api-key "offline-test-key" \
  --model-name "qwen-test" \
  --no-start

echo "Starting services for smoke test ..."
sh "$ACTIVE_INSTALL_DIR/bin/up.sh"

echo "Checking service HTTP endpoints ..."
wait_http_ok "gateway" "http://127.0.0.1:8001/health" 30
wait_http_ok "langgraph" "http://127.0.0.1:2024/docs" 30
wait_http_ok "frontend" "http://127.0.0.1:3000" 30
wait_http_ok "nginx" "http://127.0.0.1:2026/health" 30
wait_http_ok "nginx-frontend" "http://127.0.0.1:2026" 30
wait_http_ok "nginx-langgraph" "http://127.0.0.1:2026/api/langgraph/docs" 30

echo
echo "Offline runtime validation passed."
echo "Bundle dir   : $BUNDLE_DIR"
echo "Install dir  : $ACTIVE_INSTALL_DIR"
echo "Web URL      : http://127.0.0.1:2026"
echo "Gateway URL  : http://127.0.0.1:2026/health"
echo "LangGraph URL: http://127.0.0.1:2026/api/langgraph/docs"

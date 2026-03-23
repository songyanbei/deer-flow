#!/usr/bin/env sh
set -eu

OUTPUT_DIR=./dist/offline-runtime
BUNDLE_NAME=deer-flow-runtime-linux-amd64
NODE_VERSION=${NODE_VERSION:-22.22.1}
NODE_TARBALL_URL=${NODE_TARBALL_URL:-"https://nodejs.org/download/release/latest-v22.x/node-v${NODE_VERSION}-linux-x64.tar.xz"}
PYTHON_STANDALONE_URL=${PYTHON_STANDALONE_URL:-"https://github.com/indygreg/python-build-standalone/releases/download/20240415/cpython-3.12.3+20240415-x86_64-unknown-linux-gnu-install_only.tar.gz"}
NODE_TARBALL_FILE=${NODE_TARBALL_FILE:-}
PYTHON_STANDALONE_FILE=${PYTHON_STANDALONE_FILE:-}
NGINX_BINARY=${NGINX_BINARY:-}
PNPM_VERSION=${PNPM_VERSION:-10.26.2}
PIP_INDEX_URL=${PIP_INDEX_URL:-}
PIP_EXTRA_INDEX_URL=${PIP_EXTRA_INDEX_URL:-}
NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY:-}
PNPM_REGISTRY=${PNPM_REGISTRY:-${NPM_CONFIG_REGISTRY:-}}
BETTER_AUTH_SECRET=${BETTER_AUTH_SECRET:-offline-build-secret-change-me}

usage() {
  cat <<EOF
Usage: ./scripts/build-offline-runtime.sh [options]

Options:
  --output-dir PATH            Output directory. Default: ./dist/offline-runtime
  --bundle-name NAME           Bundle name. Default: deer-flow-runtime-linux-amd64
  --node-version VERSION       Node version. Default: ${NODE_VERSION}
  --node-url URL               Override Node tarball URL
  --python-standalone-url URL  Override Python standalone tarball URL
  --node-file PATH             Use a local Node tarball instead of downloading
  --python-file PATH           Use a local Python standalone tarball instead of downloading
  --nginx-binary PATH          Use a specific nginx binary for the embedded proxy
  --pnpm-version VERSION       pnpm version used for the frontend build. Default: ${PNPM_VERSION}
  --help                       Show this help

Environment overrides:
  NODE_TARBALL_URL
  PYTHON_STANDALONE_URL
  NODE_TARBALL_FILE
  PYTHON_STANDALONE_FILE
  NGINX_BINARY
  PIP_INDEX_URL
  PIP_EXTRA_INDEX_URL
  NPM_CONFIG_REGISTRY
  PNPM_REGISTRY
  BETTER_AUTH_SECRET
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

resolve_path() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$2" "$1" ;;
  esac
}

download_file() {
  url=$1
  destination=$2
  local_file=${3:-}

  if [ -n "$local_file" ]; then
    if [ ! -f "$local_file" ]; then
      echo "Local file not found: $local_file" >&2
      exit 1
    fi
    echo "Using local file $(basename "$local_file") ..."
    cp "$local_file" "$destination"
    return
  fi

  if [ -f "$destination" ] && [ -s "$destination" ]; then
    return
  fi

  echo "Downloading $(basename "$destination") ..."
  curl -L --fail --retry 3 --output "$destination" "$url"
}

copy_if_exists() {
  src=$1
  dst=$2

  if [ -e "$src" ]; then
    cp "$src" "$dst"
  fi
}

copy_tree_if_exists() {
  src=$1
  dst=$2

  if [ -d "$src" ]; then
    mkdir -p "$dst"
    cp -a "$src"/. "$dst"/
  fi
}

copy_nginx_runtime() {
  nginx_source=$1
  runtime_root=$2

  mkdir -p "$runtime_root/sbin" "$runtime_root/lib"
  cp "$nginx_source" "$runtime_root/sbin/nginx"
  chmod +x "$runtime_root/sbin/nginx"

  if ! command -v ldd >/dev/null 2>&1; then
    echo "Missing required command: ldd" >&2
    exit 1
  fi

  ldd "$nginx_source" | awk '
    /=> \// { print $3 }
    /^\// { print $1 }
  ' | while IFS= read -r library_path; do
    case "$library_path" in
      ""|*/libc.so.*|*/ld-linux*.so.*|*/libpthread.so.*|*/libm.so.*|*/libdl.so.*|*/librt.so.*)
        continue
        ;;
    esac
    if [ -f "$library_path" ]; then
      cp -L "$library_path" "$runtime_root/lib/"
    fi
  done
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR=$2
      shift 2
      ;;
    --bundle-name)
      BUNDLE_NAME=$2
      shift 2
      ;;
    --node-version)
      NODE_VERSION=$2
      NODE_TARBALL_URL="https://nodejs.org/download/release/latest-v22.x/node-v${NODE_VERSION}-linux-x64.tar.xz"
      shift 2
      ;;
    --node-url)
      NODE_TARBALL_URL=$2
      shift 2
      ;;
    --python-standalone-url)
      PYTHON_STANDALONE_URL=$2
      shift 2
      ;;
    --node-file)
      NODE_TARBALL_FILE=$2
      shift 2
      ;;
    --python-file)
      PYTHON_STANDALONE_FILE=$2
      shift 2
      ;;
    --nginx-binary)
      NGINX_BINARY=$2
      shift 2
      ;;
    --pnpm-version)
      PNPM_VERSION=$2
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

require_command curl
require_command tar

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUTPUT_ROOT=$(resolve_path "$OUTPUT_DIR" "$REPO_ROOT")
BUNDLE_ROOT="$OUTPUT_ROOT/$BUNDLE_NAME"
PAYLOAD_DIR="$BUNDLE_ROOT/payload"
RUNTIME_DIR="$PAYLOAD_DIR/runtime"
WORK_DIR="$OUTPUT_ROOT/.build-$BUNDLE_NAME"
CACHE_DIR="$OUTPUT_ROOT/.cache"
ARCHIVE_PATH="$OUTPUT_ROOT/$BUNDLE_NAME.tar.gz"
PIP_CACHE_DIR="$CACHE_DIR/pip"
NPM_CACHE_DIR="$CACHE_DIR/npm"
PNPM_STORE_DIR="$CACHE_DIR/pnpm-store"

require_file "$REPO_ROOT/install-runtime.sh"
require_file "$REPO_ROOT/start-services.sh"
require_file "$REPO_ROOT/stop-services.sh"
require_file "$REPO_ROOT/templates/nginx.offline-runtime.conf.template"
require_file "$REPO_ROOT/backend/pyproject.toml"
require_file "$REPO_ROOT/backend/langgraph.json"
require_file "$REPO_ROOT/frontend/package.json"
require_dir "$REPO_ROOT/backend/src"
require_dir "$REPO_ROOT/frontend"

if [ -z "$NGINX_BINARY" ]; then
  if command -v nginx >/dev/null 2>&1; then
    NGINX_BINARY=$(command -v nginx)
  else
    echo "nginx binary not found on the build machine. Install nginx first or pass --nginx-binary PATH." >&2
    exit 1
  fi
fi
require_file "$NGINX_BINARY"

rm -rf "$BUNDLE_ROOT" "$WORK_DIR"
rm -f "$ARCHIVE_PATH"
mkdir -p "$PAYLOAD_DIR" "$RUNTIME_DIR" "$WORK_DIR" "$CACHE_DIR" "$PIP_CACHE_DIR" "$NPM_CACHE_DIR" "$PNPM_STORE_DIR"

NODE_TARBALL="$CACHE_DIR/node-v${NODE_VERSION}-linux-x64.tar.xz"
PYTHON_TARBALL="$CACHE_DIR/$(basename "$PYTHON_STANDALONE_URL")"

download_file "$NODE_TARBALL_URL" "$NODE_TARBALL" "$NODE_TARBALL_FILE"
download_file "$PYTHON_STANDALONE_URL" "$PYTHON_TARBALL" "$PYTHON_STANDALONE_FILE"

echo "Extracting Node runtime ..."
mkdir -p "$RUNTIME_DIR/node"
tar -xJf "$NODE_TARBALL" --strip-components=1 -C "$RUNTIME_DIR/node"

echo "Extracting Python runtime ..."
tar -xzf "$PYTHON_TARBALL" -C "$RUNTIME_DIR"

echo "Collecting nginx runtime ..."
copy_nginx_runtime "$NGINX_BINARY" "$RUNTIME_DIR/nginx"

PYTHON_BIN="$RUNTIME_DIR/python/bin/python3"
if [ ! -x "$PYTHON_BIN" ] && [ -x "$RUNTIME_DIR/python/bin/python" ]; then
  PYTHON_BIN="$RUNTIME_DIR/python/bin/python"
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Unable to find the bundled Python executable after extraction." >&2
  exit 1
fi

NODE_BIN="$RUNTIME_DIR/node/bin/node"
COREPACK_BIN="$RUNTIME_DIR/node/bin/corepack"
export PATH="$RUNTIME_DIR/node/bin:$PATH"
export COREPACK_HOME="$WORK_DIR/corepack"
export PNPM_HOME="$WORK_DIR/pnpm"
export HOME="$WORK_DIR/home"
export npm_config_cache="$NPM_CACHE_DIR"
mkdir -p "$COREPACK_HOME" "$PNPM_HOME" "$HOME"

echo "Preparing backend Python dependencies ..."
"$PYTHON_BIN" -m ensurepip --upgrade >/dev/null 2>&1 || true
PIP_ARGS="--cache-dir $PIP_CACHE_DIR"
if [ -n "$PIP_INDEX_URL" ]; then
  PIP_ARGS="$PIP_ARGS --index-url $PIP_INDEX_URL"
fi
if [ -n "$PIP_EXTRA_INDEX_URL" ]; then
  PIP_ARGS="$PIP_ARGS --extra-index-url $PIP_EXTRA_INDEX_URL"
fi
# shellcheck disable=SC2086
"$PYTHON_BIN" -m pip install $PIP_ARGS --upgrade pip
"$PYTHON_BIN" - "$REPO_ROOT/backend/pyproject.toml" <<'PY' > "$WORK_DIR/backend-requirements.txt"
import sys
import tomllib
from pathlib import Path

pyproject = Path(sys.argv[1])
data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
for dependency in data["project"]["dependencies"]:
    print(dependency)
PY
# shellcheck disable=SC2086
"$PYTHON_BIN" -m pip install $PIP_ARGS --prefer-binary --target "$RUNTIME_DIR/python-packages" -r "$WORK_DIR/backend-requirements.txt"
PYTHONPATH="$RUNTIME_DIR/python-packages:$REPO_ROOT/backend" "$PYTHON_BIN" - <<'PY'
import importlib
import sys

required_modules = [
    "langgraph_cli",
    "langgraph_api",
    "uvicorn",
    "fastapi",
]

missing = []
for module_name in required_modules:
    try:
        importlib.import_module(module_name)
    except Exception:
        missing.append(module_name)

if missing:
    print("Missing required Python modules in offline runtime: " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
PY

echo "Installing pnpm ${PNPM_VERSION} ..."
"$COREPACK_BIN" enable
"$COREPACK_BIN" install -g "pnpm@${PNPM_VERSION}"
if [ -n "$NPM_CONFIG_REGISTRY" ]; then
  npm config set registry "$NPM_CONFIG_REGISTRY"
fi
if [ -n "$PNPM_REGISTRY" ]; then
  pnpm config set registry "$PNPM_REGISTRY"
fi
pnpm config set store-dir "$PNPM_STORE_DIR"

echo "Building frontend standalone output ..."
rm -rf "$REPO_ROOT/frontend/.next"
(
  cd "$REPO_ROOT/frontend"
  NEXT_STANDALONE=1 \
  SKIP_ENV_VALIDATION=1 \
  NODE_ENV=production \
  BETTER_AUTH_SECRET="$BETTER_AUTH_SECRET" \
  pnpm install --frozen-lockfile
  NEXT_STANDALONE=1 \
  SKIP_ENV_VALIDATION=1 \
  NODE_ENV=production \
  BETTER_AUTH_SECRET="$BETTER_AUTH_SECRET" \
  node scripts/run-next-with-root-env.mjs build --webpack
)

FRONTEND_RUNTIME_DIR="$PAYLOAD_DIR/frontend-runtime"
mkdir -p "$FRONTEND_RUNTIME_DIR"

if [ -f "$REPO_ROOT/frontend/.next/standalone/server.js" ]; then
  cp -R "$REPO_ROOT/frontend/.next/standalone"/. "$FRONTEND_RUNTIME_DIR"/
elif [ -f "$REPO_ROOT/frontend/.next/standalone/frontend/server.js" ]; then
  cp -R "$REPO_ROOT/frontend/.next/standalone/frontend"/. "$FRONTEND_RUNTIME_DIR"/
else
  echo "Next standalone server.js was not produced. Build output is incomplete." >&2
  if [ -d "$REPO_ROOT/frontend/.next" ]; then
    echo "Found .next contents:" >&2
    find "$REPO_ROOT/frontend/.next" -maxdepth 3 -type f | sort >&2 || true
  fi
  exit 1
fi

mkdir -p "$FRONTEND_RUNTIME_DIR/.next"
copy_tree_if_exists "$REPO_ROOT/frontend/.next/static" "$FRONTEND_RUNTIME_DIR/.next/static"
copy_tree_if_exists "$REPO_ROOT/frontend/public" "$FRONTEND_RUNTIME_DIR/public"

echo "Collecting backend sources and runtime files ..."
mkdir -p "$PAYLOAD_DIR/backend"
copy_tree_if_exists "$REPO_ROOT/backend/src" "$PAYLOAD_DIR/backend/src"
copy_tree_if_exists "$REPO_ROOT/backend/.deer-flow/agents" "$PAYLOAD_DIR/backend/.deer-flow/agents"
copy_if_exists "$REPO_ROOT/backend/langgraph.json" "$PAYLOAD_DIR/backend/langgraph.json"
copy_if_exists "$REPO_ROOT/backend/pyproject.toml" "$PAYLOAD_DIR/backend/pyproject.toml"

mkdir -p "$PAYLOAD_DIR/skills"
copy_tree_if_exists "$REPO_ROOT/skills" "$PAYLOAD_DIR/skills"

mkdir -p "$PAYLOAD_DIR/templates"
if [ -f "$REPO_ROOT/config.yaml" ]; then
  cp "$REPO_ROOT/config.yaml" "$PAYLOAD_DIR/templates/config.yaml"
else
  cp "$REPO_ROOT/config.example.yaml" "$PAYLOAD_DIR/templates/config.yaml"
fi
if [ -f "$REPO_ROOT/extensions_config.json" ]; then
  cp "$REPO_ROOT/extensions_config.json" "$PAYLOAD_DIR/templates/extensions_config.json"
else
  copy_if_exists "$REPO_ROOT/extensions_config.example.json" "$PAYLOAD_DIR/templates/extensions_config.json"
fi
copy_if_exists "$REPO_ROOT/.env.example" "$PAYLOAD_DIR/templates/.env.example"
copy_if_exists "$REPO_ROOT/templates/nginx.offline-runtime.conf.template" "$PAYLOAD_DIR/templates/nginx.conf.template"

cp "$REPO_ROOT/start-services.sh" "$PAYLOAD_DIR/start-services.sh"
cp "$REPO_ROOT/stop-services.sh" "$PAYLOAD_DIR/stop-services.sh"
cp "$REPO_ROOT/install-runtime.sh" "$BUNDLE_ROOT/install.sh"
copy_if_exists "$REPO_ROOT/docs/offline-runtime-install.md" "$BUNDLE_ROOT/README-offline-runtime.md"

require_file "$BUNDLE_ROOT/install.sh"
require_dir "$PAYLOAD_DIR/backend/src"
require_file "$PAYLOAD_DIR/backend/langgraph.json"
require_file "$PAYLOAD_DIR/backend/pyproject.toml"
require_file "$PAYLOAD_DIR/frontend-runtime/server.js"
require_file "$PAYLOAD_DIR/runtime/node/bin/node"
require_file "$PAYLOAD_DIR/runtime/nginx/sbin/nginx"
require_dir "$PAYLOAD_DIR/runtime/python-packages"
require_file "$PAYLOAD_DIR/start-services.sh"
require_file "$PAYLOAD_DIR/stop-services.sh"
require_dir "$PAYLOAD_DIR/templates"
require_file "$PAYLOAD_DIR/templates/nginx.conf.template"

echo "Creating archive $ARCHIVE_PATH ..."
tar -C "$OUTPUT_ROOT" -czf "$ARCHIVE_PATH" "$BUNDLE_NAME"

if [ ! -f "$ARCHIVE_PATH" ] || [ ! -s "$ARCHIVE_PATH" ]; then
  echo "Archive was not created successfully: $ARCHIVE_PATH" >&2
  echo "Current output directory contents:" >&2
  ls -lah "$OUTPUT_ROOT" >&2 || true
  exit 1
fi

echo "Archive size:"
ls -lh "$ARCHIVE_PATH"

cat <<EOF

Non-Docker offline runtime bundle is ready:
  Bundle folder: $BUNDLE_ROOT
  Archive file : $ARCHIVE_PATH

Install on the offline Linux server:
  tar -xzf $BUNDLE_NAME.tar.gz
  cd $BUNDLE_NAME
  chmod +x install.sh
  ./install.sh --install-dir /opt/deer-flow
EOF

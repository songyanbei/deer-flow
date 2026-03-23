#!/usr/bin/env sh
set -eu

OUTPUT_DIR=./dist/offline
PLATFORM=linux/amd64
BUNDLE_NAME=deer-flow-offline-linux-amd64

usage() {
  cat <<EOF
Usage: ./scripts/build-offline-bundle.sh [options]

Options:
  --output-dir PATH    Output directory. Default: ./dist/offline
  --platform VALUE     Docker build platform. Default: linux/amd64
  --bundle-name NAME   Bundle name. Default: deer-flow-offline-linux-amd64
  --help               Show this help
EOF
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

resolve_path() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$2" "$1" ;;
  esac
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR=$2
      shift 2
      ;;
    --platform)
      PLATFORM=$2
      shift 2
      ;;
    --bundle-name)
      BUNDLE_NAME=$2
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
require_command tar

if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose is required." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not reachable. Please start Docker first." >&2
  exit 1
fi

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_ROOT=$(resolve_path "$OUTPUT_DIR" "$REPO_ROOT")
BUNDLE_ROOT="$OUTPUT_ROOT/$BUNDLE_NAME"
IMAGES_DIR="$BUNDLE_ROOT/images"
ARCHIVE_PATH="$OUTPUT_ROOT/$BUNDLE_NAME.tar.gz"

rm -rf "$BUNDLE_ROOT"
rm -f "$ARCHIVE_PATH"
mkdir -p "$IMAGES_DIR"

build_and_save() {
  image_name=$1
  dockerfile_path=$2
  tar_name=$3

  echo "Building $image_name for $PLATFORM ..."
  docker build \
    --platform "$PLATFORM" \
    -f "$REPO_ROOT/$dockerfile_path" \
    -t "$image_name" \
    "$REPO_ROOT"

  tar_path="$IMAGES_DIR/$tar_name"
  echo "Saving $image_name to $tar_path ..."
  docker save -o "$tar_path" "$image_name"
}

build_and_save "deer-flow/backend:offline" "docker/offline/backend.Dockerfile" "deer-flow-backend-offline.tar"
build_and_save "deer-flow/frontend:offline" "docker/offline/frontend.Dockerfile" "deer-flow-frontend-offline.tar"
build_and_save "deer-flow/nginx:offline" "docker/offline/nginx.Dockerfile" "deer-flow-nginx-offline.tar"

cp "$REPO_ROOT/install.sh" "$BUNDLE_ROOT/install.sh"
cp "$REPO_ROOT/docker/docker-compose-offline.yaml" "$BUNDLE_ROOT/docker-compose.yaml"
cp "$REPO_ROOT/docs/offline-install.md" "$BUNDLE_ROOT/README-offline.md"

tar -C "$OUTPUT_ROOT" -czf "$ARCHIVE_PATH" "$BUNDLE_NAME"

cat <<EOF

Offline bundle is ready:
  Bundle folder: $BUNDLE_ROOT
  Archive file : $ARCHIVE_PATH

Send the archive to the Linux server, extract it, then run:
  tar -xzf $BUNDLE_NAME.tar.gz
  cd $BUNDLE_NAME
  chmod +x install.sh
  ./install.sh
EOF

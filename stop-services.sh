#!/usr/bin/env sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
STATE_DIR="$SCRIPT_DIR/.dev-runtime"
PYTHON_BIN="$SCRIPT_DIR/runtime/python/bin/python3"
ENV_FILE="$SCRIPT_DIR/.env"
QUIET=0

if [ ! -x "$PYTHON_BIN" ] && [ -x "$SCRIPT_DIR/runtime/python/bin/python" ]; then
  PYTHON_BIN="$SCRIPT_DIR/runtime/python/bin/python"
fi

if [ "${1:-}" = "--quiet" ]; then
  QUIET=1
fi

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
    export "$key=$value"
  done < "$1"
}

stop_pid() {
  pid=$1

  if kill -0 "$pid" >/dev/null 2>&1; then
    kill "$pid" >/dev/null 2>&1 || true
    sleep 1
  fi

  if kill -0 "$pid" >/dev/null 2>&1; then
    kill -9 "$pid" >/dev/null 2>&1 || true
  fi
}

stop_listeners_on_port() {
  port=$1

  if [ ! -x "$PYTHON_BIN" ]; then
    return
  fi

  "$PYTHON_BIN" - "$port" <<'PY'
import os
import signal
import socket
import sys
from pathlib import Path

port = int(sys.argv[1])
target_hex = f"{port:04X}"
inode_targets = set()

for proc_net in ("/proc/net/tcp", "/proc/net/tcp6"):
    path = Path(proc_net)
    if not path.exists():
        continue
    with path.open(encoding="utf-8") as fh:
        next(fh, None)
        for line in fh:
            parts = line.split()
            if len(parts) < 10:
                continue
            local_address = parts[1]
            state = parts[3]
            inode = parts[9]
            if ":" not in local_address:
                continue
            _, port_hex = local_address.rsplit(":", 1)
            if port_hex.upper() == target_hex and state == "0A":
                inode_targets.add(inode)

if not inode_targets:
    sys.exit(0)

pids = set()
for proc_dir in Path("/proc").iterdir():
    if not proc_dir.name.isdigit():
        continue
    fd_dir = proc_dir / "fd"
    if not fd_dir.exists():
        continue
    try:
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:[") and target.endswith("]"):
                inode = target[8:-1]
                if inode in inode_targets:
                    pids.add(int(proc_dir.name))
                    break
    except OSError:
        continue

for pid in sorted(pids):
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        continue

sys.exit(0)
PY
}

if [ -d "$STATE_DIR/pids" ]; then
  for pid_file in "$STATE_DIR"/pids/*.pid; do
    if [ ! -f "$pid_file" ]; then
      continue
    fi

    pid=$(cat "$pid_file")
    stop_pid "$pid"
    rm -f "$pid_file"
  done
fi

load_env_file "$ENV_FILE"
PUBLIC_PORT=${DEER_FLOW_PUBLIC_PORT:-2026}

stop_listeners_on_port 3000
stop_listeners_on_port 8001
stop_listeners_on_port 2024
stop_listeners_on_port "$PUBLIC_PORT"
sleep 1
stop_listeners_on_port 3000
stop_listeners_on_port 8001
stop_listeners_on_port 2024
stop_listeners_on_port "$PUBLIC_PORT"

rm -f "$STATE_DIR/services.env"

if [ "$QUIET" -eq 0 ]; then
  echo "All managed services are stopped."
fi

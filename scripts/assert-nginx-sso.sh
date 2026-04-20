#!/usr/bin/env bash
# Asserts that all three nginx configs declare ``location /api/sso/``.
#
# Required by backend SSO checklist §12. One-file drift (forgetting to update
# ``nginx.offline.conf`` when updating ``nginx.conf``) would silently break
# moss-hub SSO in the affected deployment.
#
# Run from the repository root:
#
#   bash scripts/assert-nginx-sso.sh
#
# Exit code 0 when all three files match, non-zero otherwise.
set -euo pipefail

EXPECTED=3
ACTUAL="$(grep -l "location /api/sso/" docker/nginx/nginx*.conf | wc -l | tr -d ' ')"

if [ "$ACTUAL" != "$EXPECTED" ]; then
  echo "[assert-nginx-sso] FAIL: expected $EXPECTED nginx configs to declare 'location /api/sso/', found $ACTUAL" >&2
  grep -l "location /api/sso/" docker/nginx/nginx*.conf >&2 || true
  exit 1
fi

echo "[assert-nginx-sso] OK: /api/sso/ present in all $EXPECTED nginx configs"

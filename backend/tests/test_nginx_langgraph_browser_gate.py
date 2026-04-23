"""D2.4 regression: every shipped nginx config must gate browser access
to ``/api/langgraph/*`` through ``$deer_block_langgraph_browser``.

Why: the Gateway reaches LangGraph directly via ``LANGGRAPH_URL`` and
does **not** route through nginx. The nginx ``/api/langgraph/`` location
therefore only serves the browser. Protected deployments must be able to
refuse this path without touching Gateway → LangGraph connectivity.

This test is structural — it verifies the guard is present in each
config file, not that nginx accepts the file (that requires a live
``nginx -t`` which is unavailable in CI). The default value of the
guard is deployment-specific:

- ``nginx.offline-runtime.conf.template`` / ``nginx.offline.conf``
  → default ``1`` (blocked — protected deployment)
- ``nginx.conf`` / ``nginx.local.conf`` → default ``0`` (open — dev)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

_MAP_RE = re.compile(
    r"map\s+\$host\s+\$deer_block_langgraph_browser\s*\{[^}]*default\s+(0|1)\s*;",
    re.DOTALL,
)
_LOC_START_RE = re.compile(r"location\s+/api/langgraph/\s*\{", re.DOTALL)
_GUARD_RE = re.compile(
    r"if\s*\(\s*\$deer_block_langgraph_browser\s*=\s*1\s*\)\s*\{\s*return\s+404\s*;\s*\}",
    re.DOTALL,
)


def _extract_location_body(text: str) -> str | None:
    """Brace-matched extraction of the ``/api/langgraph/`` location body.

    Needed because nested ``if { ... }`` blocks confuse a non-greedy regex.
    """
    m = _LOC_START_RE.search(text)
    if not m:
        return None
    depth = 1
    i = m.end()
    while i < len(text) and depth > 0:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[m.end() : i]
        i += 1
    return None


CONFIGS: list[tuple[str, str]] = [
    # (relative path, expected default value)
    ("templates/nginx.offline-runtime.conf.template", "1"),
    ("docker/nginx/nginx.offline.conf", "1"),
    ("docker/nginx/nginx.conf", "0"),
    ("docker/nginx/nginx.local.conf", "0"),
]


@pytest.mark.parametrize("rel_path,expected_default", CONFIGS)
def test_langgraph_browser_gate_present(rel_path: str, expected_default: str) -> None:
    path = REPO_ROOT / rel_path
    assert path.is_file(), f"nginx config missing: {path}"
    text = path.read_text(encoding="utf-8")

    # 1. `map` directive defining the gate variable with the expected default.
    map_match = _MAP_RE.search(text)
    assert map_match, (
        f"{rel_path}: missing `map $host $deer_block_langgraph_browser "
        f"{{ default <0|1>; }}`"
    )
    assert map_match.group(1) == expected_default, (
        f"{rel_path}: gate default is {map_match.group(1)!r}, "
        f"expected {expected_default!r} for this deployment type"
    )

    # 2. `/api/langgraph/` location contains the `if ... return 404;` guard.
    body = _extract_location_body(text)
    assert body is not None, f"{rel_path}: no `/api/langgraph/` location block found"
    assert _GUARD_RE.search(body), (
        f"{rel_path}: `/api/langgraph/` location is missing the gate guard "
        f"`if ($deer_block_langgraph_browser = 1) {{ return 404; }}`"
    )


def test_gateway_runtime_location_not_gated() -> None:
    """Acceptance: the Gateway /api/runtime/* path (browsers use this
    after migration) must NOT be behind the langgraph-browser gate."""
    nginx_conf = (REPO_ROOT / "docker/nginx/nginx.conf").read_text(encoding="utf-8")
    # Brace-match the /api/runtime location body (same helper as above).
    m = re.search(r"location\s+/api/runtime\s*\{", nginx_conf)
    assert m, "/api/runtime location missing in nginx.conf"
    depth = 1
    i = m.end()
    while i < len(nginx_conf) and depth > 0:
        c = nginx_conf[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    runtime_body = nginx_conf[m.end() : i]
    assert "deer_block_langgraph_browser" not in runtime_body, (
        "/api/runtime must not reference the langgraph-browser gate — "
        "Gateway runtime must keep streaming regardless of the gate flag"
    )

"""Diagnostic probe helper for LangGraph 1.x configurable-vs-context behavior.

Called by a monkey-patched ``make_lead_agent`` and ``_resolve_context`` (see
``_install_probes`` in the runner scripts) to capture what is visible at
each observation point. Writes a JSON dump to ``PROBE_DIR`` so the runner
script can read it after the SDK call returns.

Not an entry point — imported by ``probe_lg_channels.py`` and
``probe_local_pregel.py``. See ``backend/docs/langgraph_channel_probes.md``
for when and how to run the probes.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Default probe output directory is derived from the script's location so the
# probes work on any developer machine and any OS without setup. Layout is
#   <repo-root>/backend/scripts/_probe_channels.py  →  parents[2] == <repo-root>
# yielding <repo-root>/.probe_out. Override with the ``DF_PROBE_DIR`` env var
# when running outside the repo (CI sandboxes, ad-hoc reproductions, etc.).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_PROBE_DIR = _REPO_ROOT / ".probe_out"
PROBE_DIR = Path(os.environ.get("DF_PROBE_DIR") or _DEFAULT_PROBE_DIR)


def dump(tag: str, payload: dict[str, Any]) -> None:
    PROBE_DIR.mkdir(parents=True, exist_ok=True)
    path = PROBE_DIR / f"{tag}.json"

    def _safe(v):
        try:
            json.dumps(v)
            return v
        except TypeError:
            return repr(v)

    safe_payload = {k: _safe(v) for k, v in payload.items()}
    path.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2))


def clear() -> None:
    if PROBE_DIR.exists():
        for p in PROBE_DIR.glob("*.json"):
            p.unlink()


def read_all() -> dict[str, dict[str, Any]]:
    if not PROBE_DIR.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in PROBE_DIR.glob("*.json"):
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            out[p.stem] = {"__parse_error__": str(exc)}
    return out

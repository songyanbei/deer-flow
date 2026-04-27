"""Empirical probe: does local langgraph.pregel mirror context ↔ configurable?

Directly builds the same entry_graph that Gateway / DeerFlowClient use, then
calls `.astream()` in-process with 3 variants:

  (L-A) config-only        config={configurable:{...}}, context=None
  (L-B) context-only       config={recursion_limit}, context={...}
  (L-C) both               config={configurable:{...}}, context={...}

For each variant, probes inside make_lead_agent (build-time) and
ThreadDataMiddleware._resolve_context (runtime) dump what's visible in
cfg/runtime.context. Results go to E:/work/deer-flow/.probe_out/.

Requires no server — pure in-process langgraph (1.0.x), matching the
DeerFlowClient / SubagentExecutor path.

See ``backend/docs/langgraph_channel_probes.md`` for when to rerun and how
to interpret the results.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

# Give it a sandbox home so Paths() is happy
_tmp = Path(tempfile.mkdtemp(prefix="df_local_probe_"))
os.environ["DEER_FLOW_HOME"] = str(_tmp)
# Copy platform agents
_repo_agents = BACKEND / ".deer-flow" / "agents"
if _repo_agents.exists():
    import shutil
    shutil.copytree(_repo_agents, _tmp / "agents", dirs_exist_ok=True)

from _probe_channels import clear as probe_clear, read_all as probe_read  # type: ignore

from src.agents.entry_graph import build_entry_graph
from langchain_core.messages import HumanMessage

TENANT = "moss-hub"
USER = "u_local_probe_123"
AUTH_USER = {
    "tenant_id": TENANT,
    "user_id": USER,
    "employee_no": "E9999",
    "preferred_username": "Probe",
    "target_system": "luliu",
}


def _thread_context(thread_id: str) -> dict:
    return {
        "tenant_id": TENANT,
        "user_id": USER,
        "thread_id": thread_id,
        "client_id": "probe",
    }


async def run_variant(tag: str, *, use_config: bool, use_context: bool):
    thread_id = str(uuid.uuid4())
    state = {"messages": [HumanMessage(content=f"probe {tag}")]}

    config: dict = {"recursion_limit": 5}
    if use_config:
        config["configurable"] = {
            "__probe__": tag,
            "thread_id": thread_id,
            "tenant_id": TENANT,
            "user_id": USER,
            "auth_user": AUTH_USER,
            "thread_context": _thread_context(thread_id),
            "is_bootstrap": True,  # short-circuit to minimize heavy init
        }

    context = None
    if use_context:
        context = {
            "__probe__": tag,
            "thread_id": thread_id,
            "tenant_id": TENANT,
            "user_id": USER,
            "auth_user": AUTH_USER,
            "thread_context": _thread_context(thread_id),
        }

    graph = build_entry_graph(config)
    try:
        if context is not None:
            aiter = graph.astream(state, config=config, context=context, stream_mode="values")
        else:
            aiter = graph.astream(state, config=config, stream_mode="values")
        n = 0
        async for _ in aiter:
            n += 1
            if n >= 3:
                break
        return "ok", None
    except Exception as exc:
        return "failed", f"{type(exc).__name__}: {str(exc)[:200]}"


async def main():
    probe_clear()

    variants = [
        ("L_config_only", True, False),
        ("L_context_only", False, True),
        ("L_both", True, True),
    ]

    for tag, uc, ux in variants:
        print(f"\n== local variant: {tag} (config={uc}, context={ux}) ==")
        status, err = await run_variant(tag, use_config=uc, use_context=ux)
        print(f"  status: {status}")
        if err:
            print(f"  error: {err}")

    print("\n========== LOCAL PROBE RESULTS ==========")
    results = probe_read()
    if not results:
        print("NO PROBE FILES")
        return
    for tag in sorted(results.keys()):
        if not tag.startswith("LOCAL"):
            continue
        print(f"\n--- {tag} ---")
        print(json.dumps(results[tag], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

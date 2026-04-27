"""Empirical probe of LangGraph 1.x configurable vs context channels.

Submits several SDK variants to a running LangGraph dev server, then reads
back probe JSON files captured by ``_probe_channels.py`` (injected into
``make_lead_agent`` and ``ThreadDataMiddleware._resolve_context``).

Run this while ``langgraph dev`` is serving on ``http://127.0.0.1:2024``.

Clean up the probe injections in agent.py / thread_data_middleware.py after
you've collected results — this whole thing is diagnostic-only.

See ``backend/docs/langgraph_channel_probes.md`` for when to rerun and how
to interpret the results.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

# Ensure we can import _probe_channels from the scripts directory
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _probe_channels import clear as probe_clear, read_all as probe_read  # type: ignore

from langgraph_sdk import get_client

LG_URL = os.environ.get("LANGGRAPH_URL", "http://127.0.0.1:2024")
ASSISTANT = "entry_graph"

# Synthetic principal — fields mirror what Gateway normally injects
TENANT = "moss-hub"
USER = "u_probe_test_123"
AUTH_USER = {
    "tenant_id": TENANT,
    "user_id": USER,
    "employee_no": "E9999",
    "preferred_username": "Probe",
    "target_system": "luliu",
}


def thread_context_dict(thread_id: str) -> dict:
    # Shape mirroring ThreadContext.serialize()
    return {
        "tenant_id": TENANT,
        "user_id": USER,
        "thread_id": thread_id,
        "client_id": "probe",
    }


async def submit_variant(
    client,
    *,
    tag: str,
    use_config: bool,
    use_context: bool,
    include_thread_context: bool = True,
):
    """Create a thread, then submit one run under the chosen channel mix.

    Parameters
    ----------
    use_config / use_context:
        Toggle the two channels independently.
    include_thread_context:
        When False, the channel(s) carry identity scalars (``tenant_id`` /
        ``user_id`` / ``auth_user``) but **omit** the serialized
        ``thread_context`` blob. This is the single fail-closed scenario the
        ``ThreadDataMiddleware`` regression matrix relies on — without this
        flag the variant tag would lie about what was actually submitted
        (the previous bug: ``config_only_no_tc`` always shipped
        ``thread_context``).

    Returns (status, error_text_or_none).
    """
    try:
        th = await client.threads.create()
    except Exception as exc:
        return "thread_create_failed", repr(exc)
    thread_id = th["thread_id"]

    input_payload = {
        "messages": [
            {"type": "human", "content": [{"type": "text", "text": f"probe {tag}"}]},
        ]
    }

    run_kwargs: dict = {
        "input": input_payload,
        "stream_mode": ["values"],
        "multitask_strategy": "reject",
    }

    def _identity_block() -> dict:
        block = {
            "__probe__": tag,
            "thread_id": thread_id,
            "tenant_id": TENANT,
            "user_id": USER,
            "auth_user": AUTH_USER,
        }
        if include_thread_context:
            block["thread_context"] = thread_context_dict(thread_id)
        return block

    if use_config:
        run_kwargs["config"] = {
            "recursion_limit": 50,
            "configurable": _identity_block(),
        }
    if use_context:
        run_kwargs["context"] = _identity_block()

    try:
        # Pull a handful of chunks then stop.
        aiter = client.runs.stream(thread_id, ASSISTANT, **run_kwargs).__aiter__()
        n = 0
        while n < 4:
            try:
                await asyncio.wait_for(aiter.__anext__(), timeout=15.0)
                n += 1
            except StopAsyncIteration:
                break
            except asyncio.TimeoutError:
                break
        return "ok", None
    except Exception as exc:
        return "submit_failed", f"{type(exc).__name__}: {exc}"


async def main():
    print(f"== clearing probe dir ==")
    probe_clear()

    client = get_client(url=LG_URL)

    # Variant matrix. ``include_tc`` toggles whether the serialized
    # ``thread_context`` blob is shipped alongside the identity scalars on the
    # chosen channel(s). ``config_only_no_tc`` MUST set it to False — that is
    # the entire point of the tag. Earlier revisions of this script forgot the
    # flag, so ``config_only_no_tc`` produced a duplicate of ``config_only``
    # and the real strip case lived in a separate inline block under a
    # different name (``strip_tc``). Don't reintroduce that asymmetry.
    variants = [
        ("config_only", True, False, True),
        ("context_only", False, True, True),
        ("both", True, True, True),  # expected LG 1.x 400
        ("config_only_no_tc", True, False, False),  # truly omits thread_context
    ]

    for name, use_cfg, use_ctx, include_tc in variants:
        print(
            f"\n== variant: {name} "
            f"(config={use_cfg}, context={use_ctx}, thread_context={include_tc}) =="
        )
        status, err = await submit_variant(
            client,
            tag=name,
            use_config=use_cfg,
            use_context=use_ctx,
            include_thread_context=include_tc,
        )
        print(f"  status: {status}")
        if err:
            print(f"  error: {err[:300]}")

    # Give the server a sec to flush probe writes
    await asyncio.sleep(2)

    print("\n\n========== PROBE RESULTS ==========")
    results = probe_read()
    if not results:
        print("NO PROBE FILES — injection may not have fired")
        return
    for tag in sorted(results.keys()):
        print(f"\n--- {tag} ---")
        print(json.dumps(results[tag], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

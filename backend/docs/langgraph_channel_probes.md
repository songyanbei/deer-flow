# LangGraph Channel Probes

Diagnostic scripts under `backend/scripts/` for empirically verifying how
LangGraph treats the **`config.configurable`** vs **`context`** channels on
both the remote LangGraph API and the in-process pregel runtime.

These exist because LG 0.6+ / `langgraph-api` 0.7.x rejects remote submits
that populate both channels at once with HTTP 400
(`Cannot specify both configurable and context`), while local pregel still
mirrors them. The Gateway runtime path therefore sends identity via
`context` only, but the local `DeerFlowClient` / `SubagentExecutor` path
keeps the dual channel — see `src/gateway/runtime_service.py::start_stream`
and the bug report at
`collaboration/bugs/runtime-dual-context-configurable-lg1x-regression.md`.

## When to rerun

Rerun the probes whenever **any** of the following happens:

- LangGraph dependency bump
  - Major: `langgraph` 1.x → 2.x.
  - Minor: `langgraph` 1.0.x → 1.1.x, or `langgraph-api` 0.7.x → 0.8.x.
- Gateway returns `Cannot specify both configurable and context` 400 for a
  flow that previously worked.
- A `ThreadDataMiddleware` regression where `thread_context` is unexpectedly
  missing from the child config.
- A new submit path is added to the Gateway (e.g. a future
  `messages:stream`-style endpoint) and you need to confirm channel
  compatibility before wiring it up.

After every rerun, compare the new `*.json` files in `.probe_out/` with the
previous results so that any silent behavior change is visible at review
time.

## Files

| File | Purpose |
|------|---------|
| [`backend/scripts/_probe_channels.py`](../scripts/_probe_channels.py) | Module-level helper. Provides `dump(tag, payload)`, `read_all()`, and `clear()` for the diagnostic JSON store. **Not an entry point** — the other two scripts import it. |
| [`backend/scripts/probe_lg_channels.py`](../scripts/probe_lg_channels.py) | Probes the **remote LangGraph API** (`langgraph dev` / `langgraph-api`) by submitting four channel variants (`config_only`, `context_only`, `both`, `config_only_no_thread_context`) and recording which combinations the server accepts. |
| [`backend/scripts/probe_local_pregel.py`](../scripts/probe_local_pregel.py) | Probes the **in-process pregel** runtime — the same path `DeerFlowClient` and `SubagentExecutor` take — with three variants (`L-A` config-only, `L-B` context-only, `L-C` both). Captures what `make_lead_agent` and `ThreadDataMiddleware._resolve_context` actually see at build time and runtime. |

`PROBE_DIR` defaults to `E:/work/deer-flow/.probe_out` and can be overridden
with the `DF_PROBE_DIR` env var.

## How to run

### Remote API probe

```bash
# Prereq: a running LangGraph dev server with the current entry_graph built.
cd backend
make dev   # starts langgraph dev on port 2024

# In a second terminal:
cd backend
LANGGRAPH_URL=http://127.0.0.1:2024 PYTHONPATH=. uv run python scripts/probe_lg_channels.py
```

The script prints per-variant status to stdout and writes machine-readable
captures to `.probe_out/*.json`.

### Local pregel probe

No server needed — this drives `langgraph.pregel` in-process:

```bash
cd backend
PYTHONPATH=. uv run python scripts/probe_local_pregel.py
```

It builds the same `entry_graph` the Gateway uses, runs three variants, and
dumps what `make_lead_agent` and `ThreadDataMiddleware` observe.

## Interpreting results

Each variant writes a JSON snapshot into `.probe_out/`. The expected matrix
on the currently-pinned LG (`langgraph>=1.0.6`, `langgraph-api>=0.7.0,<0.8.0`):

| Variant | `configurable` sent | `context` sent | Remote API result | Local pregel result |
|---------|---------------------|----------------|-------------------|---------------------|
| A | ✅ identity | — | 200 OK on LG ≤ 0.5; **400** on LG ≥ 0.6 | 200 OK |
| B | — | ✅ identity | 200 OK (LG mirrors `context` → `configurable`) | 200 OK if middleware reads `context`; **fails** otherwise |
| C | ✅ | ✅ | **400** on LG ≥ 0.6 (`Cannot specify both…`) | 200 OK (pregel mirrors silently) |
| D (`config_only_no_tc`) | ✅ identity minus `thread_context` | — | 200 OK on remote; ThreadDataMiddleware fail-closed | Same |

Key invariants to verify on every rerun:

1. **Remote variant C must 400.** If it suddenly succeeds, LangGraph relaxed
   the channel rule — update the Gateway to send dual-channel only after
   audit.
2. **Local variant C must succeed.** If it starts failing, the local pregel
   path now enforces the same exclusion as remote — Phase 0 / D0.2 channel
   model needs revisiting (`SubagentExecutor` would have to migrate to
   context-only).
3. **Variant B on remote must succeed and `ThreadDataMiddleware` must
   resolve a non-empty `ThreadContext`.** This is the Gateway's only allowed
   identity channel for remote submits.

## Cleanup after a probe run

`probe_lg_channels.py` and `probe_local_pregel.py` rely on a
monkey-patched `make_lead_agent` and `ThreadDataMiddleware._resolve_context`
that call `_probe_channels.dump(...)`. The probe install/uninstall is
self-contained inside each script's `main()`, so a normal exit leaves the
codebase clean.

If a probe crashes mid-run:

- Use `python -c "from scripts._probe_channels import clear; clear()"` to
  empty the diagnostic store.
- Search `agent.py` and `thread_data_middleware.py` for any leftover
  `from _probe_channels import dump` and remove it.

The contents of `.probe_out/` are git-ignored; do not commit them.

## References

- Feature spec: [`collaboration/features/runtime-lg1x-trusted-context-submit.md`](../../collaboration/features/runtime-lg1x-trusted-context-submit.md)
- Bug report: `collaboration/bugs/runtime-dual-context-configurable-lg1x-regression.md`
- Channel constraint comment: see `dependencies` block in
  [`backend/pyproject.toml`](../pyproject.toml).

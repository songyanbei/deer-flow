# DeerFlow Benchmarks

Phase 0 baseline evaluation cases for the workflow multi-agent system.

## Directory Structure

```
benchmarks/
  phase0/
    meeting/      # Meeting agent baseline cases
    contacts/     # Contacts agent baseline cases
    hr/           # HR agent baseline cases
    workflows/    # Cross-domain workflow baseline cases
```

## Running

```bash
# Run all phase0-core cases
uv run python -m src.evals.cli run --suite phase0-core

# Run by domain
uv run python -m src.evals.cli run --domain meeting

# Run by tag
uv run python -m src.evals.cli run --tag regression

# Run a specific case
uv run python -m src.evals.cli run --case-id meeting.happy_path.basic
```

## Case Format

Cases are YAML files following the `BenchmarkCase` schema. See `src/evals/schema.py`.

## Tagging

- `regression` — case derived from a real bug
- `cross_domain` — involves multiple domain agents
- `clarification` — tests clarification flow
- `intervention` — tests intervention flow

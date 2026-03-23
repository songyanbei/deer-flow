"""CLI entry point for benchmark evaluation.

Usage:
    uv run python -m src.evals.cli run --suite phase0-core
    uv run python -m src.evals.cli run --domain meeting
    uv run python -m src.evals.cli run --tag regression
    uv run python -m src.evals.cli run --case-id meeting.happy_path.basic
"""

from __future__ import annotations

import argparse
import asyncio
import io
import logging
import sys
from pathlib import Path

from .loader import DEFAULT_BENCHMARKS_DIR
from .report import generate_markdown_report, write_reports
from .runner import run_suite

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deer-flow-evals",
        description="DeerFlow Phase 0 Baseline & Metrics evaluation runner",
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run benchmark suite")
    run_parser.add_argument("--suite", type=str, default=None, help="Filter by suite name (e.g. phase0-core)")
    run_parser.add_argument("--domain", type=str, default=None, help="Filter by domain (meeting, contacts, hr, workflows)")
    run_parser.add_argument("--tag", type=str, default=None, help="Filter by tag (e.g. regression)")
    run_parser.add_argument("--case-id", type=str, default=None, help="Run a specific case by ID")
    run_parser.add_argument("--base-dir", type=str, default=None, help="Base directory for benchmark cases")
    run_parser.add_argument("--output-dir", type=str, default=None, help="Directory for output reports")
    run_parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    # Ensure UTF-8 output on Windows
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    log_level = logging.DEBUG if getattr(args, "verbose", False) else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s %(name)s: %(message)s")

    if args.command == "run":
        return _run_command(args)

    parser.print_help()
    return 1


def _run_command(args: argparse.Namespace) -> int:
    base_dir = Path(args.base_dir) if args.base_dir else DEFAULT_BENCHMARKS_DIR / "phase0"
    output_dir = Path(args.output_dir) if args.output_dir else DEFAULT_BENCHMARKS_DIR.parent / "benchmark_reports"

    print(f"Loading cases from: {base_dir}")
    print(f"Reports will be written to: {output_dir}")
    print()

    try:
        result = asyncio.run(run_suite(
            base_dir=base_dir,
            suite=args.suite,
            domain=args.domain,
            tag=args.tag,
            case_id=args.case_id,
        ))
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error running suite: {e}", file=sys.stderr)
        logger.exception("Suite execution failed")
        return 1

    # Print summary to console
    print(generate_markdown_report(result))

    # Write reports
    json_path, md_path = write_reports(result, output_dir)
    print(f"\nJSON report: {json_path}")
    print(f"Markdown report: {md_path}")

    # Return non-zero if any failures or errors
    if result.failed > 0 or result.errored > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

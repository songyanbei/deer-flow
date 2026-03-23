"""Benchmark case loader - loads and filters YAML case files."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from .schema import BenchmarkCase

logger = logging.getLogger(__name__)

DEFAULT_BENCHMARKS_DIR = Path(__file__).resolve().parent.parent.parent / "benchmarks"


class CaseLoadError(Exception):
    """Raised when a case file fails to load or validate."""

    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"Failed to load case {path}: {reason}")


def load_case_file(path: Path) -> BenchmarkCase:
    """Load and validate a single YAML case file."""
    if not path.exists():
        raise CaseLoadError(path, "file does not exist")
    if not path.suffix in (".yaml", ".yml"):
        raise CaseLoadError(path, f"unsupported file extension: {path.suffix}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise CaseLoadError(path, f"invalid YAML: {e}") from e

    if not isinstance(raw, dict):
        raise CaseLoadError(path, "YAML root must be a mapping")

    try:
        return BenchmarkCase(**raw)
    except ValidationError as e:
        raise CaseLoadError(path, f"schema validation failed:\n{e}") from e


def load_cases(
    base_dir: Path | None = None,
    *,
    suite: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
    case_id: str | None = None,
) -> list[BenchmarkCase]:
    """Load benchmark cases from a directory tree, with optional filtering.

    Scans ``base_dir`` recursively for *.yaml/*.yml files.  Every file must
    parse and validate successfully — no silent skipping.
    """
    base_dir = base_dir or DEFAULT_BENCHMARKS_DIR
    if not base_dir.is_dir():
        raise FileNotFoundError(f"Benchmarks directory does not exist: {base_dir}")

    yaml_files = sorted(base_dir.rglob("*.yaml")) + sorted(base_dir.rglob("*.yml"))
    if not yaml_files:
        raise FileNotFoundError(f"No YAML case files found under {base_dir}")

    cases: list[BenchmarkCase] = []
    for path in yaml_files:
        case = load_case_file(path)
        cases.append(case)

    return filter_cases(cases, suite=suite, domain=domain, tag=tag, case_id=case_id)


def filter_cases(
    cases: list[BenchmarkCase],
    *,
    suite: str | None = None,
    domain: str | None = None,
    tag: str | None = None,
    case_id: str | None = None,
) -> list[BenchmarkCase]:
    """Filter a list of cases by suite, domain, tag, or case_id."""
    result = cases
    if suite:
        result = [c for c in result if c.suite == suite]
    if domain:
        result = [c for c in result if c.domain.value == domain]
    if tag:
        result = [c for c in result if tag in c.tags]
    if case_id:
        result = [c for c in result if c.id == case_id]
    return result

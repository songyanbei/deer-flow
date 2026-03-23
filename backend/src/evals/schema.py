"""Benchmark case schema definitions for Phase 0 baseline evaluation."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaseDomain(str, Enum):
    MEETING = "meeting"
    CONTACTS = "contacts"
    HR = "hr"
    WORKFLOWS = "workflows"


class CaseCategory(str, Enum):
    HAPPY_PATH = "happy_path"
    CLARIFICATION = "clarification"
    INTERVENTION = "intervention"
    DEPENDENCY = "dependency"
    CONFLICT = "conflict"
    GOVERNANCE = "governance"
    AMBIGUITY = "ambiguity"
    NOT_FOUND = "not_found"
    READ_ONLY = "read_only"
    UNSUPPORTED = "unsupported"
    CROSS_DOMAIN = "cross_domain"
    RESUME = "resume"


class CaseType(str, Enum):
    SINGLE = "single"
    WORKFLOW = "workflow"


class CaseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(..., min_length=1, description="User input message")
    clarification_answers: list[str] | None = Field(None, description="Pre-defined clarification answers for multi-turn cases")
    intervention_resolutions: list[dict[str, Any]] | None = Field(None, description="Pre-defined intervention resolutions")


class CaseExpected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_orchestration_mode: str | None = Field(None, description="Expected orchestration mode: leader or workflow")
    assigned_agent: str | None = Field(None, description="Expected single assigned agent")
    assigned_agents: list[str] | None = Field(None, description="Expected assigned agents (order-insensitive)")
    clarification_expected: bool | None = Field(None, description="Whether clarification should occur")
    intervention_expected: bool | None = Field(None, description="Whether intervention should occur")
    verified_facts_min_count: int | None = Field(None, ge=0, description="Minimum verified facts count")
    final_result_contains: list[str] | None = Field(None, description="Substrings that must appear in final result")
    final_result_not_contains: list[str] | None = Field(None, description="Substrings that must NOT appear in final result")
    task_statuses: dict[str, str] | None = Field(None, description="Expected task statuses by task description pattern")


class CaseLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_route_count: int | None = Field(None, ge=1, description="Maximum routing passes allowed")
    max_task_count: int | None = Field(None, ge=1, description="Maximum tasks allowed")
    max_duration_ms: int | None = Field(None, ge=1, description="Maximum execution duration in ms")


class CaseFixtureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str = Field(..., min_length=1, description="Fixture profile name to load")
    overrides: dict[str, Any] | None = Field(None, description="Per-case fixture overrides")


class CaseRunStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"


class BenchmarkCase(BaseModel):
    """A single benchmark case definition."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="Unique case identifier, e.g. meeting.happy_path.basic")
    title: str = Field(..., min_length=1, description="Human-readable case title")
    suite: str = Field(..., min_length=1, description="Suite name, e.g. phase0-core")
    domain: CaseDomain = Field(..., description="Domain this case belongs to")
    category: CaseCategory = Field(..., description="Case category")
    type: CaseType = Field(CaseType.SINGLE, description="Case type: single agent or workflow")

    input: CaseInput = Field(..., description="Case input specification")
    fixtures: CaseFixtureConfig = Field(..., description="Fixture configuration")
    expected: CaseExpected = Field(..., description="Expected outcomes for assertion")
    limits: CaseLimits | None = Field(None, description="Performance limits")

    tags: list[str] = Field(default_factory=list, description="Tags for filtering, e.g. regression, cross_domain")

    @model_validator(mode="after")
    def validate_agent_expectations(self) -> "BenchmarkCase":
        if self.expected.assigned_agent and self.expected.assigned_agents:
            raise ValueError("Cannot specify both assigned_agent and assigned_agents; use one.")
        return self


class AssertionFailure(BaseModel):
    """A single assertion failure."""

    field: str
    expected: Any
    actual: Any
    message: str


class CaseRunResult(BaseModel):
    """Result of running a single benchmark case."""

    case_id: str
    status: CaseRunStatus
    duration_ms: float = 0.0

    resolved_orchestration_mode: str | None = None
    assigned_agents: list[str] = Field(default_factory=list)
    task_count: int = 0
    route_count: int = 0
    clarification_count: int = 0
    intervention_count: int = 0
    verified_fact_count: int = 0

    # Phase 4: Verification fields
    verification_status: str | None = Field(None, description="Overall verification status: passed | needs_replan | hard_fail")
    verification_reports: list[dict[str, Any]] = Field(default_factory=list, description="Verification reports from task/workflow verifiers")
    verification_retry_count: int = 0

    llm_metrics: dict[str, Any] = Field(default_factory=dict)
    failed_assertions: list[AssertionFailure] = Field(default_factory=list)
    error: str | None = None


class SuiteRunResult(BaseModel):
    """Result of running an entire benchmark suite."""

    suite: str
    started_at: str
    finished_at: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0
    case_results: list[CaseRunResult] = Field(default_factory=list)
    aggregate_metrics: dict[str, Any] = Field(default_factory=dict)

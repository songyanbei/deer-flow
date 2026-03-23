"""Verification contract: core types, base verifier, and structured report definitions.

All runtime verification (task-level, workflow-final, artifact) shares these types.
"""

from __future__ import annotations

import abc
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class VerificationScope(str, Enum):
    """What is being verified."""
    TASK_RESULT = "task_result"
    WORKFLOW_RESULT = "workflow_result"
    ARTIFACT = "artifact"


class VerificationVerdict(str, Enum):
    """Unified verdict returned by every verifier."""
    PASSED = "passed"
    NEEDS_REPLAN = "needs_replan"
    HARD_FAIL = "hard_fail"


# ---------------------------------------------------------------------------
# Structured findings & report
# ---------------------------------------------------------------------------

class VerificationFinding(BaseModel):
    """A single issue discovered during verification."""
    field: str = Field(..., description="Which field or aspect failed")
    severity: str = Field("error", description="error | warning | info")
    message: str = Field(..., description="Human-readable description")
    expected: Any = Field(None, description="What was expected")
    actual: Any = Field(None, description="What was found")


class VerificationReport(BaseModel):
    """Structured report produced by a verifier."""
    verifier_name: str
    scope: VerificationScope
    verdict: VerificationVerdict
    summary: str = ""
    findings: list[VerificationFinding] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class VerificationResult(BaseModel):
    """Top-level result returned from a verification gate."""
    verdict: VerificationVerdict
    report: VerificationReport


# ---------------------------------------------------------------------------
# Verification feedback (remediation contract for planner consumption)
# ---------------------------------------------------------------------------

class VerificationFeedback(BaseModel):
    """Structured remediation contract consumed by planner on needs_replan / hard_fail."""
    source_scope: VerificationScope
    source_target: str = Field(..., description="task_id / workflow_kind / artifact_id")
    verifier_name: str
    verdict: VerificationVerdict
    summary: str
    findings: list[VerificationFinding] = Field(default_factory=list)
    recommended_action: str = Field("replan", description="replan | retry_task | revise_summary | fail")
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# Verification context (input to verifier)
# ---------------------------------------------------------------------------

class VerificationContext(BaseModel):
    """Input context passed to a verifier."""
    scope: VerificationScope
    domain: str | None = None
    task_id: str | None = None
    task_description: str | None = None
    task_result: str | None = None
    assigned_agent: str | None = None
    resolved_inputs: dict[str, Any] | None = None
    verified_facts: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    task_pool: list[dict[str, Any]] = Field(default_factory=list)
    final_result: str | None = None
    workflow_kind: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Base verifier (abstract)
# ---------------------------------------------------------------------------

class BaseVerifier(abc.ABC):
    """Abstract base for all verifiers."""

    name: str = "base_verifier"
    scope: VerificationScope = VerificationScope.TASK_RESULT

    @abc.abstractmethod
    def verify(self, ctx: VerificationContext) -> VerificationResult:
        """Execute verification and return a result."""
        ...

    def _pass(
        self,
        summary: str = "Verification passed.",
        findings: list[VerificationFinding] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VerificationResult:
        return VerificationResult(
            verdict=VerificationVerdict.PASSED,
            report=VerificationReport(
                verifier_name=self.name,
                scope=self.scope,
                verdict=VerificationVerdict.PASSED,
                summary=summary,
                findings=findings or [],
                metadata=metadata or {},
            ),
        )

    def _fail(
        self,
        verdict: VerificationVerdict,
        summary: str,
        findings: list[VerificationFinding] | None = None,
    ) -> VerificationResult:
        return VerificationResult(
            verdict=verdict,
            report=VerificationReport(
                verifier_name=self.name,
                scope=self.scope,
                verdict=verdict,
                summary=summary,
                findings=findings or [],
            ),
        )


class NoOpVerifier(BaseVerifier):
    """Fallback verifier that always passes."""

    name: str = "noop_verifier"

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        return self._pass("No verifier configured; default pass.")

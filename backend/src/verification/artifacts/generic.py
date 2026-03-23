"""Generic artifact validator - checks basic artifact validity."""

from __future__ import annotations

from pathlib import Path

from src.verification.base import (
    BaseVerifier,
    VerificationContext,
    VerificationFinding,
    VerificationResult,
    VerificationScope,
    VerificationVerdict,
)


class GenericArtifactValidator(BaseVerifier):
    """Generic validator that checks artifacts are non-empty and present."""

    name = "generic_artifact_validator"
    scope = VerificationScope.ARTIFACT

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        findings: list[VerificationFinding] = []

        artifacts = ctx.artifacts or []

        if not artifacts:
            # No artifacts to validate is acceptable
            return self._pass("No artifacts to validate.")

        for artifact_path in artifacts:
            if not artifact_path or not artifact_path.strip():
                findings.append(VerificationFinding(
                    field="artifact",
                    severity="error",
                    message="Artifact path is empty.",
                    actual=repr(artifact_path),
                ))
                continue

            path = Path(artifact_path)
            if not path.exists():
                findings.append(VerificationFinding(
                    field="artifact",
                    severity="error",
                    message="Artifact does not exist.",
                    actual=str(path),
                ))
                continue

            if not path.is_file():
                findings.append(VerificationFinding(
                    field="artifact",
                    severity="error",
                    message="Artifact path is not a file.",
                    actual=str(path),
                ))
                continue

            try:
                size = path.stat().st_size
            except OSError as exc:
                findings.append(VerificationFinding(
                    field="artifact",
                    severity="error",
                    message="Artifact is not readable.",
                    actual=f"{path}: {exc}",
                ))
                continue

            if size <= 0:
                findings.append(VerificationFinding(
                    field="artifact",
                    severity="error",
                    message="Artifact file is empty.",
                    actual=str(path),
                ))

        errors = [f for f in findings if f.severity == "error"]
        if errors:
            return self._fail(
                VerificationVerdict.NEEDS_REPLAN,
                f"Artifact validation failed with {len(errors)} error(s).",
                findings,
            )
        return self._pass(f"All {len(artifacts)} artifact(s) validated.")

from __future__ import annotations

from src.verification.artifacts.generic import GenericArtifactValidator
from src.verification.base import VerificationContext, VerificationScope, VerificationVerdict


def test_generic_artifact_validator_passes_when_no_artifacts_are_present():
    result = GenericArtifactValidator().verify(
        VerificationContext(scope=VerificationScope.ARTIFACT, artifacts=[])
    )

    assert result.verdict == VerificationVerdict.PASSED


def test_generic_artifact_validator_rejects_blank_artifact_path():
    result = GenericArtifactValidator().verify(
        VerificationContext(scope=VerificationScope.ARTIFACT, artifacts=["   "])
    )

    assert result.verdict == VerificationVerdict.NEEDS_REPLAN
    assert result.report.findings[0].message == "Artifact path is empty."


def test_generic_artifact_validator_rejects_missing_file():
    result = GenericArtifactValidator().verify(
        VerificationContext(scope=VerificationScope.ARTIFACT, artifacts=["missing-file.txt"])
    )

    assert result.verdict == VerificationVerdict.NEEDS_REPLAN
    assert result.report.findings[0].message == "Artifact does not exist."


def test_generic_artifact_validator_rejects_empty_file(tmp_path):
    artifact = tmp_path / "empty.txt"
    artifact.write_text("", encoding="utf-8")

    result = GenericArtifactValidator().verify(
        VerificationContext(scope=VerificationScope.ARTIFACT, artifacts=[str(artifact)])
    )

    assert result.verdict == VerificationVerdict.NEEDS_REPLAN
    assert result.report.findings[0].message == "Artifact file is empty."


def test_generic_artifact_validator_passes_existing_non_empty_file(tmp_path):
    artifact = tmp_path / "report.txt"
    artifact.write_text("verification output", encoding="utf-8")

    result = GenericArtifactValidator().verify(
        VerificationContext(scope=VerificationScope.ARTIFACT, artifacts=[str(artifact)])
    )

    assert result.verdict == VerificationVerdict.PASSED
    assert result.report.summary == "All 1 artifact(s) validated."

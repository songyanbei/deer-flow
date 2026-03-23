from __future__ import annotations

from src.verification.base import BaseVerifier, VerificationContext, VerificationResult, VerificationScope
from src.verification.registry import VerifierRegistry, verifier_registry


class DummyTaskVerifier(BaseVerifier):
    name = "dummy_task_verifier"
    scope = VerificationScope.TASK_RESULT

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        return self._pass("task ok")


class DummyWorkflowVerifier(BaseVerifier):
    name = "dummy_workflow_verifier"
    scope = VerificationScope.WORKFLOW_RESULT

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        return self._pass("workflow ok")


class DummyArtifactVerifier(BaseVerifier):
    name = "dummy_artifact_verifier"
    scope = VerificationScope.ARTIFACT

    def verify(self, ctx: VerificationContext) -> VerificationResult:
        return self._pass("artifact ok")


def test_get_task_verifier_resolves_exact_domain():
    registry = VerifierRegistry()
    verifier = DummyTaskVerifier()
    registry.register_task_verifier("meeting", verifier)

    assert registry.get_task_verifier("meeting") is verifier


def test_get_task_verifier_resolves_from_agent_name():
    registry = VerifierRegistry()
    verifier = DummyTaskVerifier()
    registry.register_task_verifier("meeting", verifier)

    assert registry.get_task_verifier("meeting-agent") is verifier
    assert registry.get_task_verifier("meeting_agent") is verifier


def test_get_task_verifier_falls_back_to_noop_when_missing():
    registry = VerifierRegistry()

    resolved = registry.get_task_verifier("unknown-agent")

    assert resolved.name == "noop_verifier"


def test_get_workflow_verifier_uses_specific_kind_before_default():
    registry = VerifierRegistry()
    specific = DummyWorkflowVerifier()
    default = DummyWorkflowVerifier()
    default.name = "default_workflow_verifier"
    registry.register_workflow_verifier("special", specific)
    registry.register_workflow_verifier("default", default)

    assert registry.get_workflow_verifier("special") is specific


def test_get_workflow_verifier_falls_back_to_default_kind():
    registry = VerifierRegistry()
    default = DummyWorkflowVerifier()
    registry.register_workflow_verifier("default", default)

    assert registry.get_workflow_verifier("missing") is default


def test_get_workflow_verifier_falls_back_to_noop_without_default():
    registry = VerifierRegistry()

    resolved = registry.get_workflow_verifier("missing")

    assert resolved.name == "noop_verifier"


def test_get_artifact_validators_returns_registered_validators():
    registry = VerifierRegistry()
    validator = DummyArtifactVerifier()
    registry.register_artifact_validator(validator)

    assert registry.get_artifact_validators() == [validator]


def test_get_artifact_validators_returns_noop_when_empty():
    registry = VerifierRegistry()

    validators = registry.get_artifact_validators()

    assert len(validators) == 1
    assert validators[0].name == "noop_verifier"


def test_list_registered_verifiers_reports_registered_names():
    registry = VerifierRegistry()
    registry.register_task_verifier("meeting", DummyTaskVerifier())
    registry.register_workflow_verifier("default", DummyWorkflowVerifier())
    registry.register_artifact_validator(DummyArtifactVerifier())

    listed = registry.list_registered_verifiers()

    assert listed["task_verifiers"]["meeting"] == "dummy_task_verifier"
    assert listed["workflow_verifiers"]["default"] == "dummy_workflow_verifier"
    assert listed["artifact_validators"] == ["dummy_artifact_verifier"]


def test_builtin_singleton_registry_contains_phase4_verifiers():
    listed = verifier_registry.list_registered_verifiers()

    assert listed["task_verifiers"]["meeting"] == "meeting_task_verifier"
    assert listed["task_verifiers"]["contacts"] == "contacts_task_verifier"
    assert listed["task_verifiers"]["hr"] == "hr_task_verifier"
    assert listed["workflow_verifiers"]["default"] == "default_workflow_verifier"
    assert "generic_artifact_validator" in listed["artifact_validators"]

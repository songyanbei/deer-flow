"""Verifier registry: registration, resolution, and lookup for all verifier types."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .base import BaseVerifier, NoOpVerifier, VerificationScope

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class VerifierRegistry:
    """Central registry for task-level, workflow-level, and artifact verifiers."""

    def __init__(self) -> None:
        self._task_verifiers: dict[str, BaseVerifier] = {}
        self._workflow_verifiers: dict[str, BaseVerifier] = {}
        self._artifact_validators: list[BaseVerifier] = []
        self._noop = NoOpVerifier()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_task_verifier(self, domain: str, verifier: BaseVerifier) -> None:
        self._task_verifiers[domain] = verifier
        logger.info("[VerifierRegistry] Registered task verifier '%s' for domain '%s'", verifier.name, domain)

    def register_workflow_verifier(self, workflow_kind: str, verifier: BaseVerifier) -> None:
        self._workflow_verifiers[workflow_kind] = verifier
        logger.info("[VerifierRegistry] Registered workflow verifier '%s' for kind '%s'", verifier.name, workflow_kind)

    def register_artifact_validator(self, verifier: BaseVerifier) -> None:
        self._artifact_validators.append(verifier)
        logger.info("[VerifierRegistry] Registered artifact validator '%s'", verifier.name)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def get_task_verifier(self, domain_or_agent: str | None) -> BaseVerifier:
        """Resolve a task-level verifier by domain or agent name.

        Falls back to noop if no verifier is registered for the given domain.
        """
        if domain_or_agent is None:
            return self._noop

        # Try exact match first
        if domain_or_agent in self._task_verifiers:
            return self._task_verifiers[domain_or_agent]

        # Try extracting domain from agent name (e.g. "meeting-agent" -> "meeting")
        domain = domain_or_agent.replace("-agent", "").replace("_agent", "")
        if domain in self._task_verifiers:
            return self._task_verifiers[domain]

        logger.debug("[VerifierRegistry] No task verifier for '%s', using noop.", domain_or_agent)
        return self._noop

    def get_workflow_verifier(self, workflow_kind: str | None) -> BaseVerifier:
        """Resolve a workflow-final verifier by workflow_kind.

        Falls back to 'default' kind, then to noop.
        """
        kind = workflow_kind or "default"
        if kind in self._workflow_verifiers:
            return self._workflow_verifiers[kind]
        if "default" in self._workflow_verifiers:
            return self._workflow_verifiers["default"]
        logger.debug("[VerifierRegistry] No workflow verifier for kind '%s', using noop.", kind)
        return self._noop

    def get_artifact_validators(self) -> list[BaseVerifier]:
        """Return all registered artifact validators."""
        return list(self._artifact_validators) if self._artifact_validators else [self._noop]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_registered_verifiers(self) -> dict[str, list[str]]:
        """Return a summary of all registered verifiers."""
        return {
            "task_verifiers": {domain: v.name for domain, v in self._task_verifiers.items()},
            "workflow_verifiers": {kind: v.name for kind, v in self._workflow_verifiers.items()},
            "artifact_validators": [v.name for v in self._artifact_validators],
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

verifier_registry = VerifierRegistry()


_builtin_registered = False


def _register_builtin_verifiers() -> None:
    """Register all built-in verifiers. Idempotent - safe to call multiple times."""
    global _builtin_registered
    if _builtin_registered:
        return
    _builtin_registered = True

    from .domains.meeting import MeetingTaskVerifier
    from .domains.contacts import ContactsTaskVerifier
    from .domains.hr import HrTaskVerifier
    from .workflows.default import DefaultWorkflowVerifier
    from .artifacts.generic import GenericArtifactValidator

    verifier_registry.register_task_verifier("meeting", MeetingTaskVerifier())
    verifier_registry.register_task_verifier("contacts", ContactsTaskVerifier())
    verifier_registry.register_task_verifier("hr", HrTaskVerifier())
    verifier_registry.register_workflow_verifier("default", DefaultWorkflowVerifier())
    verifier_registry.register_artifact_validator(GenericArtifactValidator())


_register_builtin_verifiers()

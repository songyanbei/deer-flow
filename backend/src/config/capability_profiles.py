"""Capability profile admission contracts and validators.

Each capability profile defines:

1. **Profile definition** — what it does, why it is not Platform Core, target domains.
2. **Admission requirements** — config, docs, artifacts that must exist before enablement.
3. **Acceptance criteria** — regressions / checks that must pass after enablement.
4. **Rollback semantics** — what happens when the profile is disabled.

Usage::

    from src.config.capability_profiles import validate_profile_admission
    report = validate_profile_admission("persistent_domain_memory", agent_config)
    if not report.ok:
        for issue in report.issues:
            print(issue)
"""

from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.config.paths import get_paths

if TYPE_CHECKING:
    from src.config.agents_config import AgentConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admission issue / report
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class AdmissionIssue:
    """One admission check finding."""

    profile: str
    check: str
    severity: str   # "error" | "warning"
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.upper()}] {self.profile}/{self.check}: {self.message}"


@dataclasses.dataclass(slots=True)
class AdmissionReport:
    """Aggregated result of profile admission validation."""

    profile: str
    agent_name: str
    issues: list[AdmissionIssue] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[AdmissionIssue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[AdmissionIssue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "agent_name": self.agent_name,
            "ok": self.ok,
            "issues": [
                {"check": i.check, "severity": i.severity, "message": i.message}
                for i in self.issues
            ],
        }


# ---------------------------------------------------------------------------
# Profile definitions (immutable metadata)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True, slots=True)
class ProfileDefinition:
    """Static metadata describing one capability profile."""

    key: str
    display_name: str
    goal: str
    why_not_core: str
    target_domains: str  # human-readable description
    required_config_doc: str
    required_artifacts_doc: str
    required_tests_doc: str
    rollback_doc: str


PROFILE_DEFINITIONS: dict[str, ProfileDefinition] = {}


def _register(p: ProfileDefinition) -> ProfileDefinition:
    PROFILE_DEFINITIONS[p.key] = p
    return p


# ── persistent_domain_memory ───────────────────────────────────────────────

_register(ProfileDefinition(
    key="persistent_domain_memory",
    display_name="Persistent Domain Memory",
    goal=(
        "Enable per-domain persistent memory that stores stable user preferences "
        "and reusable hints across sessions, reducing repeated clarification."
    ),
    why_not_core=(
        "Requires domain-specific boundary definitions (allowlist, denylist, "
        "truth priority) that vary per domain; cannot be a one-size-fits-all default."
    ),
    target_domains="Domains with recurring user workflows and stable preference patterns.",
    required_config_doc=(
        "persistent_memory_enabled=true in config.yaml; domain field must be set."
    ),
    required_artifacts_doc=(
        "RUNBOOK.md with: allowed reuse scope, must-stay-in-thread-truth items, "
        "conflict resolution order, and safety rules. "
        "Persistence boundary definition (allowlist + denylist)."
    ),
    required_tests_doc=(
        "Profile regression: memory injection present when enabled, absent when disabled. "
        "Truth precedence regression: current-thread facts override persistent memory. "
        "Rollback regression: disabling switch reverts to thread-only truth."
    ),
    rollback_doc=(
        "Set persistent_memory_enabled=false. Agent reverts to default thread-truth "
        "behavior; no persistent hints injected into prompt."
    ),
))

# ── domain_runbook_support ─────────────────────────────────────────────────

_register(ProfileDefinition(
    key="domain_runbook_support",
    display_name="Domain Runbook Support",
    goal="Inject a domain-specific runbook into the agent's system prompt for workflow guidance.",
    why_not_core="Not all domains need runbooks; optional for simple query-response agents.",
    target_domains="Domains with multi-step SOP workflows (e.g., booking, approval chains).",
    required_config_doc="persistent_runbook_file set in config.yaml (or default RUNBOOK.md).",
    required_artifacts_doc="RUNBOOK.md or equivalent file in the agent directory.",
    required_tests_doc=(
        "Runbook injection regression: runbook content appears in agent prompt when enabled. "
        "Absent when file missing or profile disabled."
    ),
    rollback_doc="Remove persistent_runbook_file config. Runbook is no longer injected.",
))

# ── domain_verifier_pack ───────────────────────────────────────────────────

_register(ProfileDefinition(
    key="domain_verifier_pack",
    display_name="Domain Verifier Pack",
    goal="Register domain-specific verifier families for task-level verification.",
    why_not_core="Platform provides verifier runtime, but domain packs are opt-in per domain.",
    target_domains="Domains with structured outputs requiring validation (booking confirmations, HR records).",
    required_config_doc="Domain verifier binding registered in verifier registry.",
    required_artifacts_doc="Verifier contract documentation describing scope and verdicts.",
    required_tests_doc="Verifier regression: domain verifiers invoked on AFTER_TASK_COMPLETE for matching domain.",
    rollback_doc="Unregister domain verifier. Falls back to platform default verifier path.",
))

# ── governance_strict_mode ─────────────────────────────────────────────────

_register(ProfileDefinition(
    key="governance_strict_mode",
    display_name="Governance Strict Mode",
    goal="Apply stricter governance policies for a domain beyond the base governance path.",
    why_not_core="Base governance applies to all agents; strict mode is per-domain opt-in.",
    target_domains="Domains handling sensitive operations (financial, HR, compliance).",
    required_config_doc="Governance policy rules scoped to the domain registered in PolicyRegistry.",
    required_artifacts_doc="Policy/guard boundary documentation for the domain.",
    required_tests_doc="Governance regression: domain-scoped rules trigger REQUIRE_INTERVENTION for matching tools.",
    rollback_doc="Remove domain-scoped policy rules. Falls back to base governance path.",
))


# ---------------------------------------------------------------------------
# Admission validators (per-profile)
# ---------------------------------------------------------------------------

def _check_persistent_domain_memory(config: AgentConfig, report: AdmissionReport) -> None:
    """Admission checks for ``persistent_domain_memory`` profile."""

    # 1. domain must be set
    if not config.domain:
        report.issues.append(AdmissionIssue(
            report.profile, "domain_required", "error",
            "persistent_domain_memory requires a non-empty 'domain' field.",
        ))

    # 2. persistent_memory_enabled must be true
    if not config.persistent_memory_enabled:
        report.issues.append(AdmissionIssue(
            report.profile, "enable_switch", "error",
            "persistent_memory_enabled must be true to admit this profile.",
        ))

    # 3. RUNBOOK.md (or override) must exist
    agent_dir = get_paths().agent_dir(config.name)
    runbook_filename = config.persistent_runbook_file or "RUNBOOK.md"
    runbook_path = agent_dir / runbook_filename
    if not runbook_path.exists():
        report.issues.append(AdmissionIssue(
            report.profile, "runbook_exists", "error",
            f"Runbook file '{runbook_filename}' not found in {agent_dir}.",
        ))
    else:
        # 3b. Runbook must contain minimum required sections
        _check_runbook_content(runbook_path, report)

    # 4. Warn if no domain-specific hint extractor is registered (pilot boundary)
    #    This is a soft check — the platform will still function without it,
    #    but memory updates won't extract hints.
    if config.domain:
        try:
            from src.agents.persistent_domain_memory import get_hint_extractor
            if get_hint_extractor(config.domain) is None:
                report.issues.append(AdmissionIssue(
                    report.profile, "hint_extractor", "warning",
                    f"No domain hint extractor registered for domain '{config.domain}'. "
                    f"Register one via persistent_domain_memory.register_hint_extractor() "
                    f"to enable memory hint extraction for this domain.",
                ))
        except ImportError:
            report.issues.append(AdmissionIssue(
                report.profile, "hint_extractor", "warning",
                "Could not import hint extractor registry for admission check.",
            ))


_RUNBOOK_REQUIRED_SECTIONS = (
    "allowed",       # Allowed reuse / allowlist
    "must stay",     # Must-stay-in-thread / denylist
    "conflict",      # Conflict resolution order
    "safety",        # Safety rules
)


def _check_runbook_content(runbook_path: Path, report: AdmissionReport) -> None:
    """Verify the runbook contains the minimum required sections."""
    try:
        content = runbook_path.read_text(encoding="utf-8").lower()
    except Exception:
        report.issues.append(AdmissionIssue(
            report.profile, "runbook_readable", "error",
            f"Cannot read runbook file: {runbook_path}",
        ))
        return

    for keyword in _RUNBOOK_REQUIRED_SECTIONS:
        if keyword not in content:
            report.issues.append(AdmissionIssue(
                report.profile, f"runbook_section_{keyword.replace(' ', '_')}",
                "warning",
                f"Runbook may be missing a section covering '{keyword}'. "
                f"Review {runbook_path.name} for completeness.",
            ))


def _check_domain_runbook_support(config: AgentConfig, report: AdmissionReport) -> None:
    """Admission checks for ``domain_runbook_support`` profile."""
    agent_dir = get_paths().agent_dir(config.name)
    runbook_filename = config.persistent_runbook_file or "RUNBOOK.md"
    runbook_path = agent_dir / runbook_filename
    if not runbook_path.exists():
        report.issues.append(AdmissionIssue(
            report.profile, "runbook_exists", "error",
            f"Runbook file '{runbook_filename}' not found in {agent_dir}.",
        ))


def _check_domain_verifier_pack(config: AgentConfig, report: AdmissionReport) -> None:
    """Admission checks for ``domain_verifier_pack`` profile."""
    if not config.domain:
        report.issues.append(AdmissionIssue(
            report.profile, "domain_required", "error",
            "domain_verifier_pack requires a non-empty 'domain' field.",
        ))

    # We cannot import the verifier registry at module level to avoid
    # circular imports, so we do a lazy check.
    try:
        from src.verification.registry import verifier_registry
        registered = verifier_registry.list_registered_verifiers()
        task_domains = registered.get("task_verifiers", {})
        if config.domain and config.domain not in task_domains:
            report.issues.append(AdmissionIssue(
                report.profile, "verifier_registered", "warning",
                f"No verifier family registered for domain '{config.domain}'. "
                f"Register verifiers via the verifier registry before enabling this profile.",
            ))
    except ImportError:
        report.issues.append(AdmissionIssue(
            report.profile, "verifier_registry_import", "warning",
            "Could not import verifier registry for admission check.",
        ))


def _check_governance_strict_mode(config: AgentConfig, report: AdmissionReport) -> None:
    """Admission checks for ``governance_strict_mode`` profile."""
    if not config.domain:
        report.issues.append(AdmissionIssue(
            report.profile, "domain_required", "error",
            "governance_strict_mode requires a non-empty 'domain' field.",
        ))


_PROFILE_VALIDATORS: dict[str, Any] = {
    "persistent_domain_memory": _check_persistent_domain_memory,
    "domain_runbook_support": _check_domain_runbook_support,
    "domain_verifier_pack": _check_domain_verifier_pack,
    "governance_strict_mode": _check_governance_strict_mode,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_profile_admission(
    profile_key: str,
    config: AgentConfig,
) -> AdmissionReport:
    """Run admission checks for a capability profile against an agent config.

    Returns an :class:`AdmissionReport` whose ``.ok`` property tells whether
    all *error*-level checks passed.

    Raises :class:`ValueError` if the profile key is not recognised.
    """
    if profile_key not in PROFILE_DEFINITIONS:
        raise ValueError(
            f"Unknown capability profile '{profile_key}'. "
            f"Known profiles: {sorted(PROFILE_DEFINITIONS)}"
        )

    report = AdmissionReport(profile=profile_key, agent_name=config.name)
    validator = _PROFILE_VALIDATORS.get(profile_key)
    if validator:
        validator(config, report)
    return report


def _has_runbook_file(config: AgentConfig) -> bool:
    """Return True if the agent has a runbook file on disk (explicit or default)."""
    try:
        agent_dir = get_paths().agent_dir(config.name)
        runbook_filename = config.persistent_runbook_file or "RUNBOOK.md"
        return (agent_dir / runbook_filename).exists()
    except Exception:
        return False


def _has_domain_verifier(config: AgentConfig) -> bool:
    """Return True if a verifier is registered for this agent's domain."""
    if not config.domain:
        return False
    try:
        from src.verification.registry import verifier_registry
        registered = verifier_registry.list_registered_verifiers()
        return config.domain in registered.get("task_verifiers", {})
    except ImportError:
        return False


def _has_governance_strict_signals(config: AgentConfig) -> bool:
    """Return True if the agent config carries governance-strict-mode signals."""
    return bool(config.intervention_policies) or bool(config.hitl_keywords)


def validate_all_active_profiles(config: AgentConfig) -> list[AdmissionReport]:
    """Detect which profiles an agent config implicitly activates and run
    admission checks for each.

    Recognised activation signals:

    * ``persistent_memory_enabled=true`` → ``persistent_domain_memory``
    * ``persistent_runbook_file`` set OR default ``RUNBOOK.md`` exists → ``domain_runbook_support``
    * domain verifier registered in verifier registry → ``domain_verifier_pack``
    * ``intervention_policies`` or ``hitl_keywords`` non-empty → ``governance_strict_mode``
    """
    reports: list[AdmissionReport] = []

    if config.persistent_memory_enabled:
        reports.append(validate_profile_admission("persistent_domain_memory", config))

    if config.persistent_runbook_file or _has_runbook_file(config):
        reports.append(validate_profile_admission("domain_runbook_support", config))

    if _has_domain_verifier(config):
        reports.append(validate_profile_admission("domain_verifier_pack", config))

    if _has_governance_strict_signals(config):
        reports.append(validate_profile_admission("governance_strict_mode", config))

    return reports


def get_profile_admission_matrix() -> list[dict[str, str]]:
    """Return a JSON-serialisable admission matrix for all profiles."""
    result = []
    for defn in PROFILE_DEFINITIONS.values():
        result.append({
            "profile": defn.key,
            "display_name": defn.display_name,
            "goal": defn.goal,
            "why_not_core": defn.why_not_core,
            "target_domains": defn.target_domains,
            "required_config": defn.required_config_doc,
            "required_artifacts": defn.required_artifacts_doc,
            "required_tests": defn.required_tests_doc,
            "rollback": defn.rollback_doc,
        })
    return result

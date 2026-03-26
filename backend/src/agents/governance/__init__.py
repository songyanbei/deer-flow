"""Phase 5A Governance Core — unified risk taxonomy, policy, decision engine, and audit ledger.

Public API:
- ``governance_engine`` — singleton decision engine
- ``policy_registry`` — singleton policy rule registry
- ``governance_ledger`` — singleton audit ledger
- ``install_governance_audit_hooks()`` — register audit hooks on the runtime hook registry
"""

from .audit_hooks import install_governance_audit_hooks
from .engine import GovernanceEngine, GovernanceEvaluation, governance_engine
from .ledger import GovernanceLedger, governance_ledger
from .policy import PolicyMatchResult, PolicyRegistry, policy_registry
from .types import (
    GovernanceDecision,
    GovernanceLedgerEntry,
    GovernanceLedgerStatus,
    PolicyRule,
    RiskLevel,
    parse_risk_level,
)

__all__ = [
    "GovernanceDecision",
    "GovernanceEngine",
    "GovernanceEvaluation",
    "GovernanceLedger",
    "GovernanceLedgerEntry",
    "GovernanceLedgerStatus",
    "PolicyMatchResult",
    "PolicyRegistry",
    "PolicyRule",
    "RiskLevel",
    "governance_engine",
    "governance_ledger",
    "install_governance_audit_hooks",
    "parse_risk_level",
    "policy_registry",
]

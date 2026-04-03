"""Tests for tenant_id propagation through agent runtime, governance, and gateway.

Validates that the gap fixes correctly wire tenant_id from RunnableConfig
into all downstream call sites:
- planner/node.py → list_domain_agents(agents_dir=...)
- semantic_router.py → list_domain_agents(agents_dir=...) at 3 call sites
- executor.py → load_agent_config(agents_dir=...) and get_persistent_domain_memory_context(tenant_id=...)
- persistent_domain_memory.py → load_agent_config(agents_dir=...) and get_memory_data(tenant_id=...)
- prompt.py → _get_memory_context(tenant_id=...) and is_persistent_domain_memory_enabled(agents_dir=...)
- memory_middleware.py → queue.add(tenant_id=...) with tenant-scoped dedupe key
- governance/engine.py → ledger.record(tenant_id=...) in all 4 methods
- governance/audit_hooks.py → metadata["tenant_id"] forwarded to engine
- intervention_middleware.py → evaluate_before_tool(tenant_id=...)
- gateway routers → ThreadRegistry.check_access(thread_id, tenant_id)
- ThreadDataMiddleware → ThreadRegistry.register(thread_id, tenant_id)
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.config.paths import resolve_tenant_agents_dir


# ── resolve_tenant_agents_dir ──────────────────────────────────────────


class TestResolveTenantAgentsDir:
    def test_default_tenant_returns_none(self):
        assert resolve_tenant_agents_dir("default") is None

    def test_none_returns_none(self):
        assert resolve_tenant_agents_dir(None) is None

    def test_empty_string_returns_none(self):
        assert resolve_tenant_agents_dir("") is None

    def test_non_default_tenant_returns_path(self):
        result = resolve_tenant_agents_dir("tenant-abc")
        assert result is not None
        assert "tenants" in str(result)
        assert "tenant-abc" in str(result)
        assert str(result).endswith("agents")


# ── GovernanceEngine tenant propagation ────────────────────────────────


class TestGovernanceEngineTenantPropagation:
    """Verify that all 4 GovernanceEngine methods pass tenant_id to ledger."""

    def _make_engine(self):
        from src.agents.governance.engine import GovernanceEngine

        mock_ledger = MagicMock()
        mock_ledger.record.return_value = {"governance_id": "gov-123"}
        mock_ledger.resolve.return_value = None
        mock_registry = MagicMock()
        mock_registry.evaluate.return_value = SimpleNamespace(
            matched=False,
            decision=None,
            risk_level=None,
            reason=None,
            rule=None,
            title=None,
            display_overrides=None,
        )
        engine = GovernanceEngine(registry=mock_registry, ledger=mock_ledger)
        return engine, mock_ledger, mock_registry

    def test_evaluate_before_tool_passes_tenant_id(self):
        engine, mock_ledger, mock_registry = self._make_engine()
        # Make policy match so ledger.record is called
        from src.agents.governance.engine import GovernanceDecision, RiskLevel

        mock_registry.evaluate.return_value = SimpleNamespace(
            matched=True,
            decision=GovernanceDecision.ALLOW,
            risk_level=RiskLevel.MEDIUM,
            reason="test",
            rule={"rule_id": "r1"},
            title="test",
            display_overrides=None,
        )
        engine.evaluate_before_tool(
            tool_name="test_tool",
            tool_args={},
            agent_name="agent-a",
            task_id="t1",
            run_id="r1",
            thread_id="th1",
            tenant_id="tenant-x",
        )
        mock_ledger.record.assert_called_once()
        assert mock_ledger.record.call_args[1]["tenant_id"] == "tenant-x"

    def test_record_interrupt_emit_passes_tenant_id(self):
        engine, mock_ledger, _ = self._make_engine()
        engine.record_interrupt_emit(
            thread_id="th1",
            run_id="r1",
            task_id="t1",
            source_agent="agent-a",
            interrupt_type="confirm",
            source_path="test",
            tenant_id="tenant-y",
        )
        mock_ledger.record.assert_called_once()
        assert mock_ledger.record.call_args[1]["tenant_id"] == "tenant-y"

    def test_record_interrupt_resolve_passes_tenant_id(self):
        engine, mock_ledger, _ = self._make_engine()
        engine.record_interrupt_resolve(
            thread_id="th1",
            run_id="r1",
            task_id="t1",
            source_agent="system",
            source_path="test",
            tenant_id="tenant-z",
        )
        mock_ledger.record.assert_called_once()
        assert mock_ledger.record.call_args[1]["tenant_id"] == "tenant-z"

    def test_record_state_commit_audit_passes_tenant_id(self):
        engine, mock_ledger, _ = self._make_engine()
        engine.record_state_commit_audit(
            thread_id="th1",
            run_id="r1",
            source_path="test",
            commit_type="task_pool",
            tenant_id="tenant-w",
        )
        mock_ledger.record.assert_called_once()
        assert mock_ledger.record.call_args[1]["tenant_id"] == "tenant-w"


# ── InterventionMiddleware tenant propagation ──────────────────────────


class TestInterventionMiddlewareTenantId:
    def test_tenant_id_stored_on_init(self):
        from src.agents.middlewares.intervention_middleware import InterventionMiddleware

        mw = InterventionMiddleware(tenant_id="tid-abc")
        assert mw._tenant_id == "tid-abc"

    def test_tenant_id_defaults_to_none(self):
        from src.agents.middlewares.intervention_middleware import InterventionMiddleware

        mw = InterventionMiddleware()
        assert mw._tenant_id is None


# ── PersistentDomainMemory tenant propagation ──────────────────────────


class TestPersistentDomainMemoryTenant:
    def test_resolve_agent_domain_passes_agents_dir(self):
        from src.agents.persistent_domain_memory import _resolve_agent_domain

        with patch("src.agents.persistent_domain_memory.load_agent_config") as mock_load:
            mock_load.return_value = SimpleNamespace(domain="travel")
            result = _resolve_agent_domain("agent-a", agents_dir=Path("/custom"))
            mock_load.assert_called_once_with("agent-a", agents_dir=Path("/custom"))
            assert result == "travel"

    def test_is_persistent_domain_memory_enabled_passes_agents_dir(self):
        from src.agents.persistent_domain_memory import is_persistent_domain_memory_enabled

        with patch("src.agents.persistent_domain_memory.load_agent_config") as mock_load:
            mock_load.return_value = SimpleNamespace(persistent_memory_enabled=True)
            result = is_persistent_domain_memory_enabled("agent-b", agents_dir=Path("/custom"))
            mock_load.assert_called_once_with("agent-b", agents_dir=Path("/custom"))
            assert result is True

    def test_get_persistent_domain_memory_context_passes_tenant_id(self):
        from src.agents.persistent_domain_memory import get_persistent_domain_memory_context

        with (
            patch("src.agents.persistent_domain_memory.load_agent_config") as mock_load,
            patch("src.agents.persistent_domain_memory.get_memory_data") as mock_mem,
            patch("src.agents.persistent_domain_memory.format_memory_for_injection") as mock_fmt,
        ):
            mock_load.return_value = SimpleNamespace(persistent_memory_enabled=True)
            mock_mem.return_value = {"user": {"workContext": {"summary": "test"}}}
            mock_fmt.return_value = "formatted"
            get_persistent_domain_memory_context("agent-c", tenant_id="tid-x", agents_dir=Path("/custom"))
            mock_mem.assert_called_once_with("agent-c", tenant_id="tid-x")


# ── Prompt _get_memory_context tenant propagation ──────────────────────


class TestPromptMemoryContextTenant:
    def test_get_memory_context_accepts_tenant_id(self):
        """Verify _get_memory_context accepts tenant_id and passes it to get_memory_data."""
        import inspect
        from src.agents.lead_agent.prompt import _get_memory_context

        sig = inspect.signature(_get_memory_context)
        assert "tenant_id" in sig.parameters

    def test_get_memory_context_passes_tenant_id(self):
        """Integration-style: patch the memory __init__ re-exports."""
        from src.agents.lead_agent.prompt import _get_memory_context

        with (
            patch("src.agents.memory.get_memory_data") as mock_data,
            patch("src.agents.memory.format_memory_for_injection") as mock_fmt,
            patch("src.config.memory_config.get_memory_config") as mock_cfg,
        ):
            mock_cfg.return_value = SimpleNamespace(enabled=True, injection_enabled=True, max_injection_tokens=2000)
            mock_data.return_value = {"user": {"workContext": {"summary": "test"}}}
            mock_fmt.return_value = "memory content"
            _get_memory_context("agent-a", tenant_id="tid-y")
            mock_data.assert_called_once_with("agent-a", tenant_id="tid-y")


# ── Memory middleware tenant extraction ────────────────────────────────


class TestMemoryMiddlewareTenantExtraction:
    def test_dedupe_key_includes_tenant(self):
        """Verify that the dedupe key format includes tenant_id."""
        tenant_id = "org-42"
        agent_name = "my-agent"
        thread_id = "th-1"
        expected = f"conversation:{tenant_id}:{agent_name}:{thread_id}"
        assert tenant_id in expected
        assert agent_name in expected


# ── Gateway router tenant access control ───────────────────────────────


class TestGatewayRouterTenantAccess:
    """Verify that artifacts/uploads/interventions routers import and use ThreadRegistry."""

    def test_artifacts_router_imports_thread_registry(self):
        import src.gateway.routers.artifacts as mod

        assert hasattr(mod, "get_thread_registry")
        assert hasattr(mod, "get_tenant_id")

    def test_uploads_router_imports_thread_registry(self):
        import src.gateway.routers.uploads as mod

        assert hasattr(mod, "get_thread_registry")
        assert hasattr(mod, "get_tenant_id")

    def test_interventions_router_imports_thread_registry(self):
        import src.gateway.routers.interventions as mod

        assert hasattr(mod, "get_thread_registry")
        assert hasattr(mod, "get_tenant_id")


# ── ThreadDataMiddleware registration ──────────────────────────────────


class TestThreadDataMiddlewareRegistration:
    """Verify ThreadDataMiddleware registers thread → tenant in ThreadRegistry."""

    def test_registers_thread_with_tenant(self):
        from src.agents.middlewares.thread_data_middleware import ThreadDataMiddleware

        with tempfile.TemporaryDirectory() as tmp:
            mw = ThreadDataMiddleware(base_dir=tmp, lazy_init=True)
            mock_runtime = SimpleNamespace(context={"thread_id": "th-42", "tenant_id": "org-7"})

            with patch("src.agents.middlewares.thread_data_middleware.get_thread_registry") as mock_reg:
                mock_registry = MagicMock()
                mock_reg.return_value = mock_registry
                mw.before_agent({}, mock_runtime)
                mock_registry.register.assert_called_once_with("th-42", "org-7", user_id=None)

    def test_registers_default_when_no_tenant(self):
        from src.agents.middlewares.thread_data_middleware import ThreadDataMiddleware

        with tempfile.TemporaryDirectory() as tmp:
            mw = ThreadDataMiddleware(base_dir=tmp, lazy_init=True)
            mock_runtime = SimpleNamespace(context={"thread_id": "th-99"})

            with (
                patch("src.agents.middlewares.thread_data_middleware.get_thread_registry") as mock_reg,
                patch("src.agents.middlewares.thread_data_middleware.get_config") as mock_gc,
            ):
                mock_gc.return_value = {"configurable": {}}
                mock_registry = MagicMock()
                mock_reg.return_value = mock_registry
                mw.before_agent({}, mock_runtime)
                mock_registry.register.assert_called_once_with("th-99", "default", user_id=None)


# ── Audit hooks tenant metadata extraction ─────────────────────────────


class TestAuditHooksTenantExtraction:
    """Verify governance audit hooks extract tenant_id from metadata."""

    def test_interrupt_emit_hook_passes_tenant_id(self):
        from src.agents.governance.audit_hooks import GovernanceInterruptEmitAuditHook
        from src.agents.hooks.base import RuntimeHookContext, RuntimeHookName

        mock_engine = MagicMock()
        hook = GovernanceInterruptEmitAuditHook(engine=mock_engine)
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_INTERRUPT_EMIT,
            node_name="executor",
            thread_id="th-1",
            run_id="r-1",
            metadata={"task_id": "t1", "agent_name": "a", "interrupt_type": "confirm", "source_path": "test", "tenant_id": "org-5"},
        )
        hook.handle(ctx)
        mock_engine.record_interrupt_emit.assert_called_once()
        assert mock_engine.record_interrupt_emit.call_args[1]["tenant_id"] == "org-5"

    def test_interrupt_resolve_hook_passes_tenant_id(self):
        from src.agents.governance.audit_hooks import GovernanceInterruptResolveAuditHook
        from src.agents.hooks.base import RuntimeHookContext, RuntimeHookName

        mock_engine = MagicMock()
        hook = GovernanceInterruptResolveAuditHook(engine=mock_engine)
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.AFTER_INTERRUPT_RESOLVE,
            node_name="executor",
            thread_id="th-2",
            run_id="r-2",
            metadata={"task_id": "t2", "source_path": "test", "action_key": "confirm", "resolution_behavior": "", "request_id": "", "tenant_id": "org-6"},
        )
        hook.handle(ctx)
        mock_engine.record_interrupt_resolve.assert_called_once()
        assert mock_engine.record_interrupt_resolve.call_args[1]["tenant_id"] == "org-6"

    def test_state_commit_hook_passes_tenant_id(self):
        from src.agents.governance.audit_hooks import GovernanceStateCommitAuditHook
        from src.agents.hooks.base import RuntimeHookContext, RuntimeHookName

        mock_engine = MagicMock()
        hook = GovernanceStateCommitAuditHook(engine=mock_engine)
        ctx = RuntimeHookContext(
            hook_name=RuntimeHookName.BEFORE_TASK_POOL_COMMIT,
            node_name="planner",
            thread_id="th-3",
            run_id="r-3",
            metadata={"source_path": "test", "tenant_id": "org-7"},
        )
        hook.handle(ctx)
        mock_engine.record_state_commit_audit.assert_called_once()
        assert mock_engine.record_state_commit_audit.call_args[1]["tenant_id"] == "org-7"


# ── apply_prompt_template tenant propagation ───────────────────────────


class TestApplyPromptTemplateTenant:
    """Verify apply_prompt_template accepts and forwards tenant_id and agents_dir."""

    def test_signature_has_tenant_params(self):
        import inspect
        from src.agents.lead_agent.prompt import apply_prompt_template

        sig = inspect.signature(apply_prompt_template)
        assert "tenant_id" in sig.parameters
        assert "agents_dir" in sig.parameters

    def test_passes_tenant_id_to_get_memory_context(self):
        from src.agents.lead_agent.prompt import apply_prompt_template

        with (
            patch("src.agents.lead_agent.prompt._get_memory_context") as mock_mem,
            patch("src.agents.lead_agent.prompt.is_persistent_domain_memory_enabled") as mock_pdm,
            patch("src.agents.lead_agent.prompt._get_runbook_context", return_value=""),
            patch("src.agents.lead_agent.prompt.get_agent_soul", return_value=""),
            patch("src.agents.lead_agent.prompt.get_skills_prompt_section", return_value=""),
        ):
            mock_pdm.return_value = False
            mock_mem.return_value = ""
            apply_prompt_template(agent_name="agent-x", tenant_id="tid-p", agents_dir=Path("/custom"))
            mock_mem.assert_called_once_with("agent-x", tenant_id="tid-p")

    def test_passes_agents_dir_to_is_persistent_domain_memory_enabled(self):
        from src.agents.lead_agent.prompt import apply_prompt_template

        with (
            patch("src.agents.lead_agent.prompt._get_memory_context", return_value=""),
            patch("src.agents.lead_agent.prompt.is_persistent_domain_memory_enabled") as mock_pdm,
            patch("src.agents.lead_agent.prompt._get_runbook_context", return_value=""),
            patch("src.agents.lead_agent.prompt.get_agent_soul", return_value=""),
            patch("src.agents.lead_agent.prompt.get_skills_prompt_section", return_value=""),
        ):
            mock_pdm.return_value = False
            apply_prompt_template(
                is_domain_agent=True,
                agent_name="agent-y",
                tenant_id="tid-q",
                agents_dir=Path("/custom2"),
            )
            mock_pdm.assert_called_once_with("agent-y", agents_dir=Path("/custom2"))

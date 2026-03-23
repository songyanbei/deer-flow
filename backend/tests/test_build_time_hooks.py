"""Tests for Phase 1 Addendum: build-time hook contract.

Covers:
- Default no-op hooks do not change existing behavior
- Hook call order is stable and predictable
- Hooks receive correct BuildContext fields
- Hooks can modify writable fields (available_skills, extra_tools, metadata)
- set_build_time_hooks / get_build_time_hooks lifecycle
- Integration with make_lead_agent()
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.agents.lead_agent.engines.base import (
    BuildContext,
    BuildTimeHooks,
    get_build_time_hooks,
    set_build_time_hooks,
)


# ===========================================================================
# 1. BuildContext
# ===========================================================================


class TestBuildContext:
    def test_default_values(self):
        ctx = BuildContext()
        assert ctx.agent_name is None
        assert ctx.engine_type is None
        assert ctx.model_name is None
        assert ctx.is_domain_agent is False
        assert ctx.is_bootstrap is False
        assert ctx.available_skills is None
        assert ctx.extra_tools == []
        assert ctx.metadata == {}

    def test_writable_fields(self):
        ctx = BuildContext(agent_name="test")
        ctx.available_skills = {"skill-a", "skill-b"}
        ctx.extra_tools = [SimpleNamespace(name="tool1")]
        ctx.metadata["audit_tag"] = "v1"
        assert ctx.available_skills == {"skill-a", "skill-b"}
        assert len(ctx.extra_tools) == 1
        assert ctx.metadata["audit_tag"] == "v1"

    def test_read_only_fields_set_at_creation(self):
        ctx = BuildContext(
            agent_name="my-agent",
            engine_type="react",
            model_name="gpt-4",
            is_domain_agent=True,
            is_bootstrap=False,
        )
        assert ctx.agent_name == "my-agent"
        assert ctx.engine_type == "react"
        assert ctx.model_name == "gpt-4"
        assert ctx.is_domain_agent is True


# ===========================================================================
# 2. BuildTimeHooks default no-op behavior
# ===========================================================================


class TestBuildTimeHooksNoOp:
    """Default BuildTimeHooks must be no-ops that do not alter context."""

    def test_before_agent_build_noop(self):
        hooks = BuildTimeHooks()
        ctx = BuildContext(agent_name="test", available_skills={"s1"})
        hooks.before_agent_build(ctx)
        assert ctx.available_skills == {"s1"}
        assert ctx.extra_tools == []

    def test_after_agent_build_noop(self):
        hooks = BuildTimeHooks()
        ctx = BuildContext(agent_name="test")
        hooks.after_agent_build(ctx)

    def test_before_skill_resolve_noop(self):
        hooks = BuildTimeHooks()
        ctx = BuildContext(available_skills={"a", "b"})
        hooks.before_skill_resolve(ctx)
        assert ctx.available_skills == {"a", "b"}

    def test_before_mcp_bind_noop(self):
        hooks = BuildTimeHooks()
        ctx = BuildContext(extra_tools=[SimpleNamespace(name="t")])
        hooks.before_mcp_bind(ctx)
        assert len(ctx.extra_tools) == 1


# ===========================================================================
# 3. Hook call order
# ===========================================================================


class TestHookCallOrder:
    """Hooks must be called in the documented order."""

    def test_call_order_is_stable(self):
        call_log = []

        class OrderTracker(BuildTimeHooks):
            def before_agent_build(self, ctx):
                call_log.append("before_agent_build")

            def before_skill_resolve(self, ctx):
                call_log.append("before_skill_resolve")

            def before_mcp_bind(self, ctx):
                call_log.append("before_mcp_bind")

            def after_agent_build(self, ctx):
                call_log.append("after_agent_build")

        hooks = OrderTracker()
        ctx = BuildContext()

        # Simulate the documented call order
        hooks.before_agent_build(ctx)
        hooks.before_skill_resolve(ctx)
        hooks.before_mcp_bind(ctx)
        hooks.after_agent_build(ctx)

        assert call_log == [
            "before_agent_build",
            "before_skill_resolve",
            "before_mcp_bind",
            "after_agent_build",
        ]


# ===========================================================================
# 4. Hook mutation capability
# ===========================================================================


class TestHookMutation:
    """Hooks can modify writable BuildContext fields."""

    def test_before_skill_resolve_can_add_skills(self):
        class SkillInjector(BuildTimeHooks):
            def before_skill_resolve(self, ctx):
                if ctx.available_skills is None:
                    ctx.available_skills = set()
                ctx.available_skills.add("injected-skill")

        hooks = SkillInjector()
        ctx = BuildContext(available_skills={"existing"})
        hooks.before_skill_resolve(ctx)
        assert "injected-skill" in ctx.available_skills
        assert "existing" in ctx.available_skills

    def test_before_skill_resolve_can_remove_skills(self):
        class SkillFilter(BuildTimeHooks):
            def before_skill_resolve(self, ctx):
                if ctx.available_skills:
                    ctx.available_skills.discard("blocked-skill")

        hooks = SkillFilter()
        ctx = BuildContext(available_skills={"ok-skill", "blocked-skill"})
        hooks.before_skill_resolve(ctx)
        assert ctx.available_skills == {"ok-skill"}

    def test_before_mcp_bind_can_add_metadata(self):
        class AuditHook(BuildTimeHooks):
            def before_mcp_bind(self, ctx):
                ctx.metadata["mcp_audit"] = True

        hooks = AuditHook()
        ctx = BuildContext()
        hooks.before_mcp_bind(ctx)
        assert ctx.metadata["mcp_audit"] is True

    def test_after_agent_build_can_record_metadata(self):
        class PostBuildRecorder(BuildTimeHooks):
            def after_agent_build(self, ctx):
                ctx.metadata["build_completed"] = True

        hooks = PostBuildRecorder()
        ctx = BuildContext()
        hooks.after_agent_build(ctx)
        assert ctx.metadata["build_completed"] is True


# ===========================================================================
# 5. set_build_time_hooks / get_build_time_hooks lifecycle
# ===========================================================================


class TestHooksRegistration:
    """get/set_build_time_hooks manage the active hooks singleton."""

    def setup_method(self):
        # Reset to defaults before each test
        set_build_time_hooks(None)

    def teardown_method(self):
        set_build_time_hooks(None)

    def test_default_hooks_is_noop_instance(self):
        hooks = get_build_time_hooks()
        assert isinstance(hooks, BuildTimeHooks)

    def test_set_custom_hooks(self):
        class Custom(BuildTimeHooks):
            pass

        custom = Custom()
        set_build_time_hooks(custom)
        assert get_build_time_hooks() is custom

    def test_reset_to_default(self):
        class Custom(BuildTimeHooks):
            pass

        set_build_time_hooks(Custom())
        set_build_time_hooks(None)
        hooks = get_build_time_hooks()
        assert type(hooks) is BuildTimeHooks

    def test_multiple_replacements(self):
        class A(BuildTimeHooks):
            pass

        class B(BuildTimeHooks):
            pass

        set_build_time_hooks(A())
        assert isinstance(get_build_time_hooks(), A)
        set_build_time_hooks(B())
        assert isinstance(get_build_time_hooks(), B)


# ===========================================================================
# 6. Integration: hooks fire during make_lead_agent()
# ===========================================================================


class TestMakeLeadAgentHooksIntegration:
    """Verify hooks are actually called during make_lead_agent()."""

    def setup_method(self):
        set_build_time_hooks(None)

    def teardown_method(self):
        set_build_time_hooks(None)

    def _setup_monkeypatches(self, monkeypatch):
        """Common monkeypatches for make_lead_agent integration tests."""
        from src.agents.lead_agent import agent as lead_agent_module
        from src.config.app_config import AppConfig
        from src.config.model_config import ModelConfig
        from src.config.sandbox_config import SandboxConfig
        import src.tools as tools_module

        app_config = AppConfig(
            models=[
                ModelConfig(
                    name="test-model",
                    display_name="test-model",
                    description=None,
                    use="langchain_openai:ChatOpenAI",
                    model="test-model",
                    supports_thinking=False,
                    supports_vision=False,
                )
            ],
            sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
        )

        monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
        monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
        monkeypatch.setattr(lead_agent_module, "_build_middlewares", lambda config, model_name, agent_name=None: [])
        monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: object())
        monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)
        return lead_agent_module

    def test_all_hooks_fire_during_build(self, monkeypatch):
        """All 4 hooks fire in order during a normal (non-bootstrap) make_lead_agent call."""
        lead_agent_module = self._setup_monkeypatches(monkeypatch)
        monkeypatch.setattr(lead_agent_module, "apply_prompt_template", lambda **kwargs: "prompt")
        monkeypatch.setattr(
            lead_agent_module,
            "load_agent_config",
            lambda _name: SimpleNamespace(
                model=None,
                tool_groups=[],
                max_tool_calls=20,
                available_skills=["skill-a"],
                engine_type="react",
            ),
        )

        call_log = []

        class Tracker(BuildTimeHooks):
            def before_agent_build(self, ctx):
                call_log.append(("before_agent_build", ctx.agent_name, ctx.engine_type))

            def before_skill_resolve(self, ctx):
                call_log.append(("before_skill_resolve", ctx.available_skills.copy() if ctx.available_skills else None))

            def before_mcp_bind(self, ctx):
                call_log.append(("before_mcp_bind", ctx.agent_name))

            def after_agent_build(self, ctx):
                call_log.append(("after_agent_build", ctx.agent_name))

        set_build_time_hooks(Tracker())

        lead_agent_module.make_lead_agent(
            {
                "configurable": {
                    "agent_name": "test-agent",
                    "model_name": "test-model",
                    "thinking_enabled": False,
                }
            }
        )

        assert len(call_log) == 4
        assert call_log[0] == ("before_agent_build", "test-agent", "react")
        assert call_log[1] == ("before_skill_resolve", {"skill-a"})
        assert call_log[2] == ("before_mcp_bind", "test-agent")
        assert call_log[3] == ("after_agent_build", "test-agent")

    def test_noop_hooks_preserve_existing_behavior(self, monkeypatch):
        """Default no-op hooks produce the same result as no hooks at all."""
        lead_agent_module = self._setup_monkeypatches(monkeypatch)

        captured_prompt_kwargs = {}

        def _fake_apply_prompt_template(**kwargs):
            captured_prompt_kwargs.update(kwargs)
            return "prompt"

        monkeypatch.setattr(lead_agent_module, "apply_prompt_template", _fake_apply_prompt_template)
        monkeypatch.setattr(
            lead_agent_module,
            "load_agent_config",
            lambda _name: SimpleNamespace(
                model=None,
                tool_groups=[],
                max_tool_calls=20,
                available_skills=["skill-a"],
                engine_type="sop",
            ),
        )

        set_build_time_hooks(None)
        lead_agent_module.make_lead_agent(
            {
                "configurable": {
                    "agent_name": "test-agent",
                    "model_name": "test-model",
                    "thinking_enabled": False,
                }
            }
        )

        assert captured_prompt_kwargs["engine_mode"] == "sop"
        assert captured_prompt_kwargs["available_skills"] == {"skill-a"}

    def test_hook_can_modify_skills_before_resolve(self, monkeypatch):
        """A custom hook can inject skills via before_skill_resolve."""
        lead_agent_module = self._setup_monkeypatches(monkeypatch)

        captured_prompt_kwargs = {}

        def _fake_apply_prompt_template(**kwargs):
            captured_prompt_kwargs.update(kwargs)
            return "prompt"

        monkeypatch.setattr(lead_agent_module, "apply_prompt_template", _fake_apply_prompt_template)
        monkeypatch.setattr(
            lead_agent_module,
            "load_agent_config",
            lambda _name: SimpleNamespace(
                model=None,
                tool_groups=[],
                max_tool_calls=20,
                available_skills=["skill-a"],
                engine_type=None,
            ),
        )

        class SkillInjector(BuildTimeHooks):
            def before_skill_resolve(self, ctx):
                if ctx.available_skills is None:
                    ctx.available_skills = set()
                ctx.available_skills.add("injected-skill")

        set_build_time_hooks(SkillInjector())

        lead_agent_module.make_lead_agent(
            {
                "configurable": {
                    "agent_name": "test-agent",
                    "model_name": "test-model",
                    "thinking_enabled": False,
                }
            }
        )

        assert "injected-skill" in captured_prompt_kwargs["available_skills"]
        assert "skill-a" in captured_prompt_kwargs["available_skills"]

    def test_before_mcp_bind_can_inject_extra_tools_into_built_agent(self, monkeypatch):
        """A custom hook can inject extra tools before the final agent is created."""
        lead_agent_module = self._setup_monkeypatches(monkeypatch)
        monkeypatch.setattr(lead_agent_module, "apply_prompt_template", lambda **kwargs: "prompt")
        monkeypatch.setattr(
            lead_agent_module,
            "load_agent_config",
            lambda _name: SimpleNamespace(
                model=None,
                tool_groups=[],
                max_tool_calls=20,
                available_skills=None,
                engine_type="react",
            ),
        )
        monkeypatch.setattr(
            "src.mcp.runtime_manager.mcp_runtime",
            SimpleNamespace(
                scope_key_for_agent=lambda name: f"domain:{name}",
                get_tools_sync=lambda _scope: [],
            ),
        )

        class ExtraToolInjector(BuildTimeHooks):
            def before_mcp_bind(self, ctx):
                ctx.extra_tools.append(SimpleNamespace(name="hook_injected_tool"))

        set_build_time_hooks(ExtraToolInjector())

        result = lead_agent_module.make_lead_agent(
            {
                "configurable": {
                    "agent_name": "test-agent",
                    "model_name": "test-model",
                    "thinking_enabled": False,
                }
            }
        )

        assert [tool.name for tool in result["tools"]] == ["hook_injected_tool"]

    def test_bootstrap_agent_still_fires_before_agent_build(self, monkeypatch):
        """Bootstrap path fires before_agent_build (skill/mcp hooks are after the bootstrap return)."""
        lead_agent_module = self._setup_monkeypatches(monkeypatch)

        call_log = []

        class Tracker(BuildTimeHooks):
            def before_agent_build(self, ctx):
                call_log.append("before_agent_build")

            def before_skill_resolve(self, ctx):
                call_log.append("before_skill_resolve")

            def before_mcp_bind(self, ctx):
                call_log.append("before_mcp_bind")

            def after_agent_build(self, ctx):
                call_log.append("after_agent_build")

        set_build_time_hooks(Tracker())

        lead_agent_module.make_lead_agent(
            {
                "configurable": {
                    "is_bootstrap": True,
                    "model_name": "test-model",
                    "thinking_enabled": False,
                }
            }
        )

        # Bootstrap path returns early, so only before_agent_build fires
        assert "before_agent_build" in call_log

    def test_bootstrap_agent_also_fires_after_agent_build(self, monkeypatch):
        """Bootstrap path should still trigger after_agent_build once the agent object exists."""
        lead_agent_module = self._setup_monkeypatches(monkeypatch)

        call_log = []

        class Tracker(BuildTimeHooks):
            def before_agent_build(self, ctx):
                call_log.append("before_agent_build")

            def before_skill_resolve(self, ctx):
                call_log.append("before_skill_resolve")

            def before_mcp_bind(self, ctx):
                call_log.append("before_mcp_bind")

            def after_agent_build(self, ctx):
                call_log.append("after_agent_build")

        set_build_time_hooks(Tracker())

        lead_agent_module.make_lead_agent(
            {
                "configurable": {
                    "is_bootstrap": True,
                    "model_name": "test-model",
                    "thinking_enabled": False,
                }
            }
        )

        assert call_log == ["before_agent_build", "after_agent_build"]

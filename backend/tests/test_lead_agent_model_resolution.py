"""Tests for lead agent runtime model resolution behavior."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents.lead_agent import agent as lead_agent_module
from src.agents.lead_agent.engine_registry import get_engine_builder
from src.agents.lead_agent.prompt import apply_prompt_template
from src.config.app_config import AppConfig
from src.config.model_config import ModelConfig
from src.config.sandbox_config import SandboxConfig


def _make_app_config(models: list[ModelConfig]) -> AppConfig:
    return AppConfig(
        models=models,
        sandbox=SandboxConfig(use="src.sandbox.local:LocalSandboxProvider"),
    )


def _make_model(name: str, *, supports_thinking: bool) -> ModelConfig:
    return ModelConfig(
        name=name,
        display_name=name,
        description=None,
        use="langchain_openai:ChatOpenAI",
        model=name,
        supports_thinking=supports_thinking,
        supports_vision=False,
    )


def test_resolve_model_name_falls_back_to_default(monkeypatch, caplog):
    app_config = _make_app_config(
        [
            _make_model("default-model", supports_thinking=False),
            _make_model("other-model", supports_thinking=True),
        ]
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)

    with caplog.at_level("WARNING"):
        resolved = lead_agent_module._resolve_model_name("missing-model")

    assert resolved == "default-model"
    assert "fallback to default model 'default-model'" in caplog.text


def test_resolve_model_name_uses_default_when_none(monkeypatch):
    app_config = _make_app_config(
        [
            _make_model("default-model", supports_thinking=False),
            _make_model("other-model", supports_thinking=True),
        ]
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)

    resolved = lead_agent_module._resolve_model_name(None)

    assert resolved == "default-model"


def test_resolve_model_name_raises_when_no_models_configured(monkeypatch):
    app_config = _make_app_config([])

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)

    with pytest.raises(
        ValueError,
        match="No chat models are configured",
    ):
        lead_agent_module._resolve_model_name("missing-model")


def test_make_lead_agent_disables_thinking_when_model_does_not_support_it(monkeypatch):
    app_config = _make_app_config([_make_model("safe-model", supports_thinking=False)])

    import src.tools as tools_module

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "_build_middlewares", lambda config, model_name, agent_name=None: [])

    captured: dict[str, object] = {}

    def _fake_create_chat_model(*, name, thinking_enabled, reasoning_effort=None):
        captured["name"] = name
        captured["thinking_enabled"] = thinking_enabled
        captured["reasoning_effort"] = reasoning_effort
        return object()

    monkeypatch.setattr(lead_agent_module, "create_chat_model", _fake_create_chat_model)
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    result = lead_agent_module.make_lead_agent(
        {
            "configurable": {
                "model_name": "safe-model",
                "thinking_enabled": True,
                "is_plan_mode": False,
                "subagent_enabled": False,
            }
        }
    )

    assert captured["name"] == "safe-model"
    assert captured["thinking_enabled"] is False
    assert result["model"] is not None


def test_build_middlewares_uses_resolved_model_name_for_vision(monkeypatch):
    app_config = _make_app_config(
        [
            _make_model("stale-model", supports_thinking=False),
            ModelConfig(
                name="vision-model",
                display_name="vision-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="vision-model",
                supports_thinking=False,
                supports_vision=True,
            ),
        ]
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda: None)
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)

    middlewares = lead_agent_module._build_middlewares(
        {"configurable": {"model_name": "stale-model", "is_plan_mode": False, "subagent_enabled": False}},
        model_name="vision-model",
    )

    assert any(isinstance(m, lead_agent_module.ViewImageMiddleware) for m in middlewares)


def test_build_middlewares_auto_composes_top_level_chain(monkeypatch):
    app_config = _make_app_config(
        [
            ModelConfig(
                name="vision-model",
                display_name="vision-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="vision-model",
                supports_thinking=False,
                supports_vision=True,
            )
        ]
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda: None)
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)

    middlewares = lead_agent_module._build_middlewares(
        {"configurable": {"is_plan_mode": False, "subagent_enabled": False, "is_domain_agent": False, "max_tool_calls": 20}},
        model_name="vision-model",
    )
    names = [type(m).__name__ for m in middlewares]

    assert names == [
        "ThreadDataMiddleware",
        "UploadsMiddleware",
        "SandboxMiddleware",
        "DanglingToolCallMiddleware",
        "TitleMiddleware",
        "MemoryMiddleware",
        "ToolCallLimitMiddleware",
        "ViewImageMiddleware",
        "ClarificationMiddleware",
    ]


def test_build_middlewares_auto_composes_domain_chain(monkeypatch):
    app_config = _make_app_config(
        [
            ModelConfig(
                name="vision-model",
                display_name="vision-model",
                description=None,
                use="langchain_openai:ChatOpenAI",
                model="vision-model",
                supports_thinking=False,
                supports_vision=True,
            )
        ]
    )

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda: None)
    monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)

    middlewares = lead_agent_module._build_middlewares(
        {
            "configurable": {
                "is_plan_mode": False,
                "subagent_enabled": True,
                "max_concurrent_subagents": 5,
                "is_domain_agent": True,
                "max_tool_calls": 20,
                "intervention_policies": {"tool_x": "require_approval"},
                "hitl_keywords": ["approve"],
                "thread_id": "thread-1",
                "run_id": "run-1",
                "task_id": "task-1",
                "agent_name": "meeting-agent",
            }
        },
        model_name="vision-model",
        agent_name="meeting-agent",
    )
    names = [type(m).__name__ for m in middlewares]

    assert names == [
        "ThreadDataMiddleware",
        "UploadsMiddleware",
        "SandboxMiddleware",
        "DanglingToolCallMiddleware",
        "ToolCallLimitMiddleware",
        "ViewImageMiddleware",
        "SubagentLimitMiddleware",
        "InterventionMiddleware",
        "HelpRequestMiddleware",
        "ClarificationMiddleware",
    ]


def test_make_lead_agent_disables_global_mcp_for_domain_agents(monkeypatch):
    app_config = _make_app_config([_make_model("safe-model", supports_thinking=False)])

    import src.tools as tools_module

    captured_tool_kwargs: dict[str, object] = {}

    def _fake_get_available_tools(**kwargs):
        captured_tool_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", _fake_get_available_tools)
    monkeypatch.setattr(lead_agent_module, "_build_middlewares", lambda config, model_name, agent_name=None: [])
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: object())
    _fake_cfg_no_engine = lambda _name, **_kw: SimpleNamespace(
        model=None,
        tool_groups=[],
        max_tool_calls=20,
        available_skills=None,
        engine_type=None,
    )
    monkeypatch.setattr(lead_agent_module, "load_agent_config", _fake_cfg_no_engine)
    monkeypatch.setattr(lead_agent_module, "load_agent_config_layered", _fake_cfg_no_engine)
    monkeypatch.setattr("src.execution.mcp_pool.mcp_pool", SimpleNamespace(get_agent_tools_sync=lambda _name: []))
    monkeypatch.setattr(
        "src.mcp.runtime_manager.mcp_runtime",
        SimpleNamespace(
            scope_key_for_agent=lambda name, tenant_id=None: f"domain:{name}",
            scope_key_for_user_agent=lambda name, tenant_id=None, user_id=None: f"domain:{name}",
            get_tools_sync=lambda _scope: [],
        ),
    )
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    result = lead_agent_module.make_lead_agent(
        {
            "configurable": {
                "agent_name": "contacts-agent",
                "is_domain_agent": True,
                "model_name": "safe-model",
                "thinking_enabled": False,
            }
        }
    )

    assert captured_tool_kwargs["include_mcp"] is False
    assert captured_tool_kwargs["is_domain_agent"] is True
    assert result["tools"] == []


def test_make_lead_agent_filters_write_like_tools_for_read_only_explorer(monkeypatch):
    app_config = _make_app_config([_make_model("safe-model", supports_thinking=False)])

    import src.tools as tools_module

    class DummyTool:
        def __init__(self, name: str):
            self.name = name

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "_build_middlewares", lambda config, model_name, agent_name=None: [])
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: object())
    _fake_cfg_read_only = lambda _name, **_kw: SimpleNamespace(
        model=None,
        tool_groups=[],
        max_tool_calls=20,
        available_skills=None,
        engine_type="ReadOnly_Explorer",
    )
    monkeypatch.setattr(lead_agent_module, "load_agent_config", _fake_cfg_read_only)
    monkeypatch.setattr(lead_agent_module, "load_agent_config_layered", _fake_cfg_read_only)
    monkeypatch.setattr(
        "src.mcp.runtime_manager.mcp_runtime",
        SimpleNamespace(
            scope_key_for_agent=lambda name, tenant_id=None: f"domain:{name}",
            scope_key_for_user_agent=lambda name, tenant_id=None, user_id=None: f"domain:{name}",
            get_tools_sync=lambda _scope: [
                DummyTool("lookup_employee"),
                DummyTool("create_contact"),
                DummyTool("update_employee_record"),
            ],
        ),
    )
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    result = lead_agent_module.make_lead_agent(
        {
            "configurable": {
                "agent_name": "contacts-agent",
                "is_domain_agent": True,
                "model_name": "safe-model",
                "thinking_enabled": False,
            }
        }
    )

    assert [tool.name for tool in result["tools"]] == ["lookup_employee"]


@pytest.mark.parametrize(
    ("configured_engine_type", "expected_engine_mode"),
    [
        (None, "default"),
        ("ReAct", "react"),
        ("SOP", "sop"),
    ],
)
def test_make_lead_agent_resolves_engine_mode_from_config(monkeypatch, configured_engine_type, expected_engine_mode):
    app_config = _make_app_config([_make_model("safe-model", supports_thinking=False)])

    import src.tools as tools_module

    captured_prompt_kwargs: dict[str, object] = {}

    def _fake_apply_prompt_template(**kwargs):
        captured_prompt_kwargs.update(kwargs)
        return "prompt"

    monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
    monkeypatch.setattr(tools_module, "get_available_tools", lambda **kwargs: [])
    monkeypatch.setattr(lead_agent_module, "_build_middlewares", lambda config, model_name, agent_name=None: [])
    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: object())
    _fake_cfg_engine = lambda _name, **_kw: SimpleNamespace(
        model=None,
        tool_groups=[],
        max_tool_calls=20,
        available_skills=None,
        engine_type=configured_engine_type,
        name="meeting-agent",
    )
    monkeypatch.setattr(lead_agent_module, "load_agent_config", _fake_cfg_engine)
    monkeypatch.setattr(lead_agent_module, "load_agent_config_layered", _fake_cfg_engine)
    monkeypatch.setattr("src.execution.mcp_pool.mcp_pool", SimpleNamespace(get_agent_tools_sync=lambda _name: []))
    monkeypatch.setattr(lead_agent_module, "apply_prompt_template", _fake_apply_prompt_template)
    monkeypatch.setattr(lead_agent_module, "create_agent", lambda **kwargs: kwargs)

    lead_agent_module.make_lead_agent(
        {
            "configurable": {
                "agent_name": "meeting-agent",
                "is_domain_agent": True,
                "model_name": "safe-model",
                "thinking_enabled": False,
            }
        }
    )

    assert captured_prompt_kwargs["engine_mode"] == expected_engine_mode


def test_get_engine_builder_falls_back_to_default_for_unknown_engine():
    builder = get_engine_builder("UnknownEngine")

    assert builder.canonical_name == "default"
    assert builder.prepare_runtime_options().filter_read_only_tools is False


def test_apply_prompt_template_adds_read_only_explorer_rules():
    prompt = apply_prompt_template(
        agent_name="contacts-agent",
        is_domain_agent=True,
        engine_mode="read_only_explorer",
    )

    assert "Read-Only Explorer" in prompt
    assert "ReadOnly_Explorer" in prompt
    assert "execute the lookup directly instead of calling `request_help`" in prompt


def test_apply_prompt_template_adds_react_engine_rules():
    prompt = apply_prompt_template(
        agent_name="meeting-agent",
        is_domain_agent=True,
        engine_mode="react",
    )

    assert "explicit `ReAct` mode" in prompt
    assert "short think-act-observe loops" in prompt


def test_apply_prompt_template_adds_sop_engine_rules():
    prompt = apply_prompt_template(
        agent_name="meeting-agent",
        is_domain_agent=True,
        engine_mode="sop",
    )

    assert "explicit `SOP` mode" in prompt
    assert "Follow the domain procedure step by step" in prompt

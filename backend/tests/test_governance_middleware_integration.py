"""Tests for governance engine integration with intervention middleware."""

import json
import tempfile
from unittest.mock import MagicMock

from src.agents.governance.engine import GovernanceEngine
from src.agents.governance.ledger import GovernanceLedger
from src.agents.governance.policy import PolicyRegistry
from src.agents.middlewares.intervention_middleware import InterventionMiddleware


def _make_tool_call_request(tool_name: str, tool_args: dict | None = None, tool_call_id: str = "tc_1"):
    """Create a mock ToolCallRequest."""
    mock = MagicMock()
    mock.tool_call = {
        "name": tool_name,
        "args": tool_args or {},
        "id": tool_call_id,
    }
    return mock


def _make_handler():
    """Create a mock handler that returns a simple ToolMessage."""
    mock = MagicMock()
    mock.return_value = MagicMock(content="tool_result")
    return mock


class TestGovernanceMiddlewareIntegration:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.registry = PolicyRegistry()
        self.ledger = GovernanceLedger(data_dir=self._tmpdir)
        self.engine = GovernanceEngine(registry=self.registry, ledger=self.ledger)

    def teardown_method(self):
        self.ledger.clear()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _make_middleware(self, **kwargs):
        defaults = {
            "run_id": "run-1",
            "task_id": "task-1",
            "agent_name": "test-agent",
            "thread_id": "thread-1",
            "engine": self.engine,
        }
        defaults.update(kwargs)
        return InterventionMiddleware(**defaults)

    def test_no_policy_fallback_to_keyword_detection(self):
        """When no policy matches, existing risky-keyword detection still works."""
        middleware = self._make_middleware()
        request = _make_tool_call_request("create_event", {"title": "Meeting"})
        handler = _make_handler()

        result = middleware.wrap_tool_call(request, handler)
        # create_event contains "create" → risky keyword → intervention
        assert hasattr(result, "goto")  # Command(goto=END)
        handler.assert_not_called()

    def test_no_policy_safe_tool_passes_through(self):
        """Safe tools pass through when no policy matches."""
        middleware = self._make_middleware()
        request = _make_tool_call_request("get_events", {})
        handler = _make_handler()

        middleware.wrap_tool_call(request, handler)
        handler.assert_called_once()

    def test_policy_allow_overrides_risky_keyword(self):
        """A policy 'allow' rule lets a risky tool pass without intervention."""
        self.registry.load([{
            "rule_id": "allow_create_event",
            "tool": "create_event",
            "risk_level": "medium",
            "decision": "allow",
        }])
        middleware = self._make_middleware()
        request = _make_tool_call_request("create_event", {"title": "Standup"})
        handler = _make_handler()

        middleware.wrap_tool_call(request, handler)
        handler.assert_called_once()
        # Ledger records the allow decision
        assert self.ledger.total_count == 1
        entry = self.ledger.query()[0]
        assert entry["decision"] == "allow"

    def test_policy_deny_blocks_tool(self):
        """A policy 'deny' rule blocks the tool with a governance_denied message."""
        self.registry.load([{
            "rule_id": "deny_delete",
            "tool": "delete_everything",
            "risk_level": "critical",
            "decision": "deny",
            "reason": "Deletion not allowed",
        }])
        middleware = self._make_middleware()
        request = _make_tool_call_request("delete_everything", {})
        handler = _make_handler()

        result = middleware.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert hasattr(result, "goto")  # Command(goto=END)
        # Check the deny message content
        messages = result.update.get("messages", [])
        assert len(messages) == 1
        content = json.loads(messages[0].content)
        assert content["error"] == "governance_denied"
        assert content["reason"] == "Deletion not allowed"
        # Ledger records deny
        entry = self.ledger.query()[0]
        assert entry["decision"] == "deny"

    def test_policy_require_intervention_triggers_interrupt(self):
        """A policy 'require_intervention' rule triggers intervention."""
        self.registry.load([{
            "rule_id": "approve_cancel",
            "tool": "cancel_meeting",
            "risk_level": "high",
            "decision": "require_intervention",
            "reason": "Cancellation needs approval",
            "title": "Cancel Meeting Approval",
        }])
        middleware = self._make_middleware()
        request = _make_tool_call_request("cancel_meeting", {"meeting_id": "m1"})
        handler = _make_handler()

        result = middleware.wrap_tool_call(request, handler)
        handler.assert_not_called()
        assert hasattr(result, "goto")  # Command(goto=END)
        # Intervention message emitted
        messages = result.update.get("messages", [])
        assert len(messages) == 1
        assert messages[0].name == "intervention_required"
        # Parse intervention request
        intv = json.loads(messages[0].content)
        assert intv["risk_level"] == "high"
        assert intv["reason"] == "Cancellation needs approval"
        assert intv["title"] == "Cancel Meeting Approval"

    def test_empty_registry_backward_compatible(self):
        """Empty policy registry preserves existing behavior exactly."""
        middleware = self._make_middleware()

        # Risky tool → intervention (keyword detection)
        req1 = _make_tool_call_request("update_record", {"id": "1"})
        result1 = middleware.wrap_tool_call(req1, _make_handler())
        assert hasattr(result1, "goto")

        # Safe tool → pass through
        req2 = _make_tool_call_request("search_records", {"q": "test"})
        handler2 = _make_handler()
        middleware.wrap_tool_call(req2, handler2)
        handler2.assert_called_once()

    def test_dedup_still_works_with_policy(self):
        """Deduplication (resolved fingerprints) still works when policy matches."""
        self.registry.load([{
            "rule_id": "r1",
            "tool": "send_email",
            "risk_level": "high",
            "decision": "require_intervention",
        }])
        # Pre-populate resolved fingerprints for this tool
        from src.agents.intervention.fingerprint import generate_tool_interrupt_fingerprint
        fp = generate_tool_interrupt_fingerprint("run-1", "task-1", "test-agent", "send_email", {"to": "user"})

        middleware = self._make_middleware(resolved_fingerprints={fp})
        request = _make_tool_call_request("send_email", {"to": "user"})
        handler = _make_handler()

        middleware.wrap_tool_call(request, handler)
        handler.assert_called_once()  # Bypassed due to dedup

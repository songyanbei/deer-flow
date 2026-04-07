"""Tests for governance ledger — audit trail persistence and queries."""

import os
import tempfile

from src.agents.governance.ledger import GovernanceLedger
from src.agents.governance.types import GovernanceDecision, RiskLevel


class TestGovernanceLedger:
    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.ledger = GovernanceLedger(data_dir=self._tmpdir)

    def teardown_method(self):
        self.ledger.clear()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _record_entry(self, **overrides):
        defaults = {
            "thread_id": "thread-1",
            "run_id": "run-1",
            "task_id": "task-1",
            "source_agent": "meeting-agent",
            "hook_name": "before_tool",
            "source_path": "middleware.intervention",
            "risk_level": RiskLevel.HIGH,
            "category": "tool_execution",
            "decision": GovernanceDecision.REQUIRE_INTERVENTION,
        }
        defaults.update(overrides)
        return self.ledger.record(**defaults)

    def test_record_creates_entry(self):
        entry = self._record_entry()
        assert entry["governance_id"].startswith("gov_")
        assert entry["thread_id"] == "thread-1"
        assert entry["decision"] == "require_intervention"
        assert entry["status"] == "pending_intervention"
        assert entry["created_at"] is not None

    def test_record_allow_status_decided(self):
        entry = self._record_entry(decision=GovernanceDecision.ALLOW)
        assert entry["status"] == "decided"

    def test_record_deny_status_decided(self):
        entry = self._record_entry(decision=GovernanceDecision.DENY)
        assert entry["status"] == "decided"

    def test_get_by_id(self):
        entry = self._record_entry()
        found = self.ledger.get_by_id(entry["governance_id"])
        assert found is not None
        assert found["governance_id"] == entry["governance_id"]

    def test_get_by_request_id(self):
        entry = self._record_entry(request_id="intv_abc123")
        found = self.ledger.get_by_request_id("intv_abc123")
        assert found is not None
        assert found["governance_id"] == entry["governance_id"]

    def test_resolve_by_request_id(self):
        self._record_entry(request_id="intv_abc123")
        resolved = self.ledger.resolve(request_id="intv_abc123", status="resolved", resolved_by="operator")
        assert resolved is not None
        assert resolved["status"] == "resolved"
        assert resolved["resolved_at"] is not None
        assert resolved["resolved_by"] == "operator"

    def test_resolve_rejected(self):
        self._record_entry(request_id="intv_rej")
        resolved = self.ledger.resolve(request_id="intv_rej", status="rejected")
        assert resolved["status"] == "rejected"

    def test_resolve_not_found(self):
        result = self.ledger.resolve(request_id="nonexistent")
        assert result is None

    def test_resolve_non_pending_returns_none(self):
        """Only pending_intervention entries can be resolved."""
        entry = self._record_entry(decision=GovernanceDecision.ALLOW, request_id="intv_decided")
        assert entry["status"] == "decided"
        result = self.ledger.resolve(request_id="intv_decided", status="resolved")
        assert result is None  # not pending, so no transition

    def test_resolve_idempotent(self):
        """Resolving an already-resolved entry returns None."""
        self._record_entry(request_id="intv_once")
        first = self.ledger.resolve(request_id="intv_once", status="resolved")
        assert first is not None
        assert first["status"] == "resolved"
        # Second resolve attempt should return None
        second = self.ledger.resolve(request_id="intv_once", status="resolved")
        assert second is None

    def test_query_by_thread(self):
        self._record_entry(thread_id="t1")
        self._record_entry(thread_id="t2")
        self._record_entry(thread_id="t1")
        results = self.ledger.query(thread_id="t1")
        assert len(results) == 2

    def test_query_by_status(self):
        self._record_entry(decision=GovernanceDecision.ALLOW)  # status=decided
        self._record_entry(decision=GovernanceDecision.REQUIRE_INTERVENTION)  # status=pending_intervention
        results = self.ledger.query(status="pending_intervention")
        assert len(results) == 1

    def test_query_by_risk_level(self):
        self._record_entry(risk_level=RiskLevel.HIGH)
        self._record_entry(risk_level=RiskLevel.CRITICAL)
        results = self.ledger.query(risk_level="critical")
        assert len(results) == 1

    def test_query_by_agent(self):
        self._record_entry(source_agent="meeting-agent")
        self._record_entry(source_agent="research-agent")
        results = self.ledger.query(source_agent="meeting-agent")
        assert len(results) == 1

    def test_query_limit_offset(self):
        for i in range(5):
            self._record_entry(task_id=f"task-{i}")
        results = self.ledger.query(limit=2, offset=1)
        assert len(results) == 2

    def test_pending_count(self):
        self._record_entry(decision=GovernanceDecision.REQUIRE_INTERVENTION)
        self._record_entry(decision=GovernanceDecision.ALLOW)
        self._record_entry(decision=GovernanceDecision.REQUIRE_INTERVENTION)
        assert self.ledger.pending_count() == 2

    def test_pending_count_by_thread(self):
        self._record_entry(thread_id="t1", decision=GovernanceDecision.REQUIRE_INTERVENTION)
        self._record_entry(thread_id="t2", decision=GovernanceDecision.REQUIRE_INTERVENTION)
        assert self.ledger.pending_count(thread_id="t1") == 1

    def test_total_count(self):
        self._record_entry()
        self._record_entry()
        assert self.ledger.total_count == 2

    def test_persistence_across_instances(self):
        self._record_entry(request_id="persist_test")
        # Create a new instance reading from the same directory
        ledger2 = GovernanceLedger(data_dir=self._tmpdir)
        assert ledger2.total_count == 1
        found = ledger2.get_by_request_id("persist_test")
        assert found is not None

    def test_clear(self):
        self._record_entry()
        self._record_entry()
        self.ledger.clear()
        assert self.ledger.total_count == 0

    def test_metadata_stored(self):
        entry = self._record_entry(
            metadata={"tool_name": "create_event", "extra": "data"},
            action_summary="Execute create_event",
            reason="High risk operation",
        )
        assert entry["metadata"]["tool_name"] == "create_event"
        assert entry["action_summary"] == "Execute create_event"
        assert entry["reason"] == "High risk operation"

    def test_query_limit_zero_returns_all(self):
        """limit=0 should return all matching entries without pagination cap."""
        for i in range(5):
            self._record_entry(thread_id=f"th_{i}")
        results = self.ledger.query(limit=0)
        assert len(results) == 5

    def test_query_limit_zero_with_offset(self):
        for i in range(5):
            self._record_entry(thread_id=f"th_{i}")
        results = self.ledger.query(limit=0, offset=3)
        assert len(results) == 2

    def test_query_created_from(self):
        """created_from filters entries by created_at >=."""
        self._record_entry(thread_id="th_old")
        e2 = self._record_entry(thread_id="th_new")
        # Use the later entry's timestamp as the cutoff
        cutoff = e2["created_at"]
        results = self.ledger.query(created_from=cutoff, limit=0)
        assert all(r["created_at"] >= cutoff for r in results)
        assert any(r["thread_id"] == "th_new" for r in results)

    def test_query_created_to(self):
        """created_to filters entries by created_at <=."""
        e1 = self._record_entry(thread_id="th_old")
        self._record_entry(thread_id="th_new")
        cutoff = e1["created_at"]
        results = self.ledger.query(created_to=cutoff, limit=0)
        assert all(r["created_at"] <= cutoff for r in results)

    def test_query_created_from_to_range(self):
        """Both created_from and created_to together form a range."""
        entries = [self._record_entry(thread_id=f"th_{i}") for i in range(3)]
        mid = entries[1]["created_at"]
        results = self.ledger.query(created_from=mid, created_to=mid, limit=0)
        assert all(r["created_at"] == mid for r in results)

    def test_query_resolved_from_excludes_unresolved(self):
        """resolved_from excludes entries without resolved_at."""
        self._record_entry(thread_id="th_decided")  # status=decided, no resolved_at
        self._record_entry(
            thread_id="th_pending",
            decision="require_intervention",
            request_id="intv_rf",
        )
        # Resolve the second entry
        self.ledger.resolve(request_id="intv_rf", status="resolved", resolved_by="operator")
        resolved_entry = self.ledger.get_by_request_id("intv_rf")
        cutoff = resolved_entry["resolved_at"]

        results = self.ledger.query(resolved_from=cutoff, limit=0)
        # Only the resolved entry should match — decided has no resolved_at
        assert len(results) >= 1
        assert all(r.get("resolved_at") for r in results)

    def test_query_resolved_to(self):
        """resolved_to filters entries by resolved_at <=."""
        self._record_entry(
            thread_id="th_r1",
            decision="require_intervention",
            request_id="intv_rt1",
        )
        self.ledger.resolve(request_id="intv_rt1", status="resolved", resolved_by="op")
        resolved = self.ledger.get_by_request_id("intv_rt1")
        cutoff = resolved["resolved_at"]

        results = self.ledger.query(resolved_to=cutoff, limit=0)
        assert all(r.get("resolved_at") and r["resolved_at"] <= cutoff for r in results)


# ── Per-user file isolation ────────────────────────────────────────────


class TestLedgerPerUserFileIsolation:
    """Verify that entries with tenant_id + user_id are stored in per-user files."""

    def setup_method(self):
        self._tmpdir = tempfile.mkdtemp()
        self.ledger = GovernanceLedger(data_dir=self._tmpdir)

    def teardown_method(self):
        self.ledger.clear()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _base_kwargs(self, **overrides):
        defaults = {
            "thread_id": "t1",
            "run_id": "r1",
            "task_id": "tk1",
            "source_agent": "agent-a",
            "hook_name": "before_tool",
            "source_path": "governance.engine",
            "risk_level": RiskLevel.MEDIUM,
            "category": "tool_execution",
            "decision": GovernanceDecision.ALLOW,
        }
        defaults.update(overrides)
        return defaults

    def test_entry_with_tenant_and_user_writes_to_per_user_file(self):
        """Entries with tenant_id != 'default' and user_id should be stored
        in tenants/{tid}/users/{uid}/governance_ledger.jsonl."""
        self.ledger.record(**self._base_kwargs(tenant_id="acme", user_id="alice"))
        per_user_file = os.path.join(
            self._tmpdir, "tenants", "acme", "users", "alice", "governance_ledger.jsonl",
        )
        global_file = os.path.join(self._tmpdir, "governance_ledger.jsonl")
        assert os.path.isfile(per_user_file), "Per-user file should exist"
        assert not os.path.isfile(global_file), "Global file should NOT be created"

    def test_entry_without_user_writes_to_global_file(self):
        """Entries without user_id fall back to the global file."""
        self.ledger.record(**self._base_kwargs(tenant_id="acme", user_id=None))
        global_file = os.path.join(self._tmpdir, "governance_ledger.jsonl")
        assert os.path.isfile(global_file)

    def test_default_tenant_writes_to_global_file(self):
        """Entries with tenant_id='default' fall back to global file."""
        self.ledger.record(**self._base_kwargs(tenant_id="default", user_id="bob"))
        global_file = os.path.join(self._tmpdir, "governance_ledger.jsonl")
        assert os.path.isfile(global_file)

    def test_different_users_get_separate_files(self):
        """Two users in the same tenant should write to different files."""
        self.ledger.record(**self._base_kwargs(tenant_id="acme", user_id="alice"))
        self.ledger.record(**self._base_kwargs(tenant_id="acme", user_id="bob"))
        alice_file = os.path.join(self._tmpdir, "tenants", "acme", "users", "alice", "governance_ledger.jsonl")
        bob_file = os.path.join(self._tmpdir, "tenants", "acme", "users", "bob", "governance_ledger.jsonl")
        assert os.path.isfile(alice_file)
        assert os.path.isfile(bob_file)
        assert self.ledger.total_count == 2

    def test_load_from_disk_discovers_per_user_files(self):
        """A new ledger instance should load entries from per-user files."""
        self.ledger.record(**self._base_kwargs(tenant_id="acme", user_id="alice"))
        self.ledger.record(**self._base_kwargs(tenant_id="acme", user_id="bob"))
        self.ledger.record(**self._base_kwargs(tenant_id="default", user_id=None))

        ledger2 = GovernanceLedger(data_dir=self._tmpdir)
        assert ledger2.total_count == 3

    def test_resolve_rewrites_only_affected_file(self):
        """Resolving an entry should rewrite only the file that entry belongs to."""
        self.ledger.record(**self._base_kwargs(
            tenant_id="acme", user_id="alice",
            decision=GovernanceDecision.REQUIRE_INTERVENTION,
            request_id="req-1",
        ))
        self.ledger.record(**self._base_kwargs(tenant_id="acme", user_id="bob"))

        self.ledger.resolve(request_id="req-1", status="resolved", resolved_by="admin")

        # Reload and verify resolve persisted correctly
        ledger2 = GovernanceLedger(data_dir=self._tmpdir)
        alice_entry = ledger2.get_by_request_id("req-1")
        assert alice_entry is not None
        assert alice_entry["status"] == "resolved"

    def test_archive_by_user_deletes_per_user_file(self):
        """archive_by_user should remove the per-user file when all entries are archived."""
        self.ledger.record(**self._base_kwargs(tenant_id="acme", user_id="alice"))
        per_user_file = os.path.join(
            self._tmpdir, "tenants", "acme", "users", "alice", "governance_ledger.jsonl",
        )
        assert os.path.isfile(per_user_file)
        removed = self.ledger.archive_by_user("acme", "alice")
        assert removed == 1
        assert not os.path.isfile(per_user_file), "Per-user file should be deleted after archive"
        assert self.ledger.total_count == 0

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.gateway.thread_context import ThreadContext

# Virtual path prefix seen by agents inside the sandbox
VIRTUAL_PATH_PREFIX = "/mnt/user-data"

_SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class Paths:
    """
    Centralized path configuration for DeerFlow application data.

    Directory layout (host side — production model):
        {base_dir}/
        ├── memory.json
        ├── USER.md
        ├── agents/
        │   └── {agent_name}/
        │       ├── config.yaml
        │       ├── SOUL.md
        │       └── memory.json
        ├── sandbox_state/              <-- runtime sandbox state (independent)
        │   └── {thread_id}/
        │       ├── sandbox.json
        │       └── sandbox.lock
        └── tenants/
            └── {tenant_id}/
                ├── memory.json
                ├── USER.md
                ├── agents/
                └── users/
                    └── {user_id}/
                        ├── memory.json
                        ├── USER.md
                        ├── governance_ledger.jsonl
                        ├── agents/
                        └── threads/
                            └── {thread_id}/
                                └── user-data/     <-- mounted as /mnt/user-data/ inside sandbox
                                    ├── workspace/
                                    ├── uploads/
                                    └── outputs/

    BaseDir resolution (in priority order):
        1. Constructor argument `base_dir`
        2. DEER_FLOW_HOME environment variable
        3. Local dev fallback: cwd/.deer-flow  (when cwd is the backend/ dir)
        4. Default: $HOME/.deer-flow
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None

    @property
    def base_dir(self) -> Path:
        """Root directory for all application data."""
        if self._base_dir is not None:
            return self._base_dir

        if env_home := os.getenv("DEER_FLOW_HOME"):
            return Path(env_home).resolve()

        cwd = Path.cwd()
        local_data = cwd / ".deer-flow"
        if cwd.name == "backend":
            return local_data

        if local_data.exists():
            return local_data

        # When the process starts from the repository root, agent data still
        # lives under backend/.deer-flow.
        repo_backend_data = cwd / "backend" / ".deer-flow"
        if repo_backend_data.exists():
            return repo_backend_data

        if (cwd / "pyproject.toml").exists():
            return local_data

        return Path.home() / ".deer-flow"

    @property
    def memory_file(self) -> Path:
        """Path to the persisted memory file: `{base_dir}/memory.json`."""
        return self.base_dir / "memory.json"

    @property
    def user_md_file(self) -> Path:
        """Path to the global user profile file: `{base_dir}/USER.md`."""
        return self.base_dir / "USER.md"

    @property
    def agents_dir(self) -> Path:
        """Root directory for all custom agents: `{base_dir}/agents/`."""
        return self.base_dir / "agents"

    def agent_dir(self, name: str) -> Path:
        """Directory for a specific agent: `{base_dir}/agents/{name}/`."""
        return self.agents_dir / name.lower()

    def agent_memory_file(self, name: str) -> Path:
        """Per-agent memory file: `{base_dir}/agents/{name}/memory.json`."""
        return self.agent_dir(name) / "memory.json"

    # ── Tenant-scoped paths ────────────────────────────────────────────

    def tenant_dir(self, tenant_id: str) -> Path:
        """Tenant data root: ``{base_dir}/tenants/{tenant_id}/``.

        Raises:
            ValueError: If *tenant_id* contains unsafe characters.
        """
        if not _SAFE_THREAD_ID_RE.match(tenant_id):
            raise ValueError(f"Invalid tenant_id: {tenant_id!r}")
        return self.base_dir / "tenants" / tenant_id

    def tenant_memory_file(self, tenant_id: str) -> Path:
        """Tenant-level memory: ``{base_dir}/tenants/{tenant_id}/memory.json``."""
        return self.tenant_dir(tenant_id) / "memory.json"

    def tenant_user_md_file(self, tenant_id: str) -> Path:
        """Tenant-scoped user profile: ``tenants/{tenant_id}/USER.md``."""
        return self.tenant_dir(tenant_id) / "USER.md"

    def tenant_agents_dir(self, tenant_id: str) -> Path:
        """Tenant's agents directory: ``{base_dir}/tenants/{tenant_id}/agents/``."""
        return self.tenant_dir(tenant_id) / "agents"

    def tenant_agent_dir(self, tenant_id: str, agent_name: str) -> Path:
        """Tenant-scoped agent directory: ``tenants/{tid}/agents/{name}/``."""
        return self.tenant_agents_dir(tenant_id) / agent_name.lower()

    def tenant_agent_memory_file(self, tenant_id: str, agent_name: str) -> Path:
        """Tenant + agent scoped memory: ``tenants/{tid}/agents/{name}/memory.json``."""
        return self.tenant_agent_dir(tenant_id, agent_name) / "memory.json"

    # ── User-scoped paths (within a tenant) ───────────────────────────

    def tenant_user_dir(self, tenant_id: str, user_id: str) -> Path:
        """User data root within a tenant: ``tenants/{tid}/users/{uid}/``.

        Raises:
            ValueError: If *user_id* contains unsafe characters.
        """
        if not _SAFE_THREAD_ID_RE.match(user_id):
            raise ValueError(f"Invalid user_id: {user_id!r}")
        return self.tenant_dir(tenant_id) / "users" / user_id

    def tenant_user_memory_file(self, tenant_id: str, user_id: str) -> Path:
        """User-level global memory: ``tenants/{tid}/users/{uid}/memory.json``."""
        return self.tenant_user_dir(tenant_id, user_id) / "memory.json"

    def tenant_user_agent_memory_file(self, tenant_id: str, user_id: str, agent_name: str) -> Path:
        """User × Agent memory: ``tenants/{tid}/users/{uid}/agents/{name}/memory.json``."""
        return self.tenant_user_dir(tenant_id, user_id) / "agents" / agent_name.lower() / "memory.json"

    def tenant_user_md_file_for_user(self, tenant_id: str, user_id: str) -> Path:
        """User profile: ``tenants/{tid}/users/{uid}/USER.md``."""
        return self.tenant_user_dir(tenant_id, user_id) / "USER.md"

    def tenant_user_governance_ledger(self, tenant_id: str, user_id: str) -> Path:
        """User governance ledger: ``tenants/{tid}/users/{uid}/governance_ledger.jsonl``."""
        return self.tenant_user_dir(tenant_id, user_id) / "governance_ledger.jsonl"

    # ── User-scoped resource paths (personal agents, skills, extensions) ─

    def tenant_user_agents_dir(self, tenant_id: str, user_id: str) -> Path:
        """User personal agents directory: ``tenants/{tid}/users/{uid}/agents/``."""
        return self.tenant_user_dir(tenant_id, user_id) / "agents"

    def tenant_user_agent_dir(self, tenant_id: str, user_id: str, agent_name: str) -> Path:
        """User personal agent directory: ``tenants/{tid}/users/{uid}/agents/{name}/``."""
        return self.tenant_user_agents_dir(tenant_id, user_id) / agent_name.lower()

    def tenant_user_skills_dir(self, tenant_id: str, user_id: str) -> Path:
        """User personal skills directory: ``tenants/{tid}/users/{uid}/skills/``."""
        return self.tenant_user_dir(tenant_id, user_id) / "skills"

    def tenant_user_extensions_config(self, tenant_id: str, user_id: str) -> Path:
        """User personal extensions config: ``tenants/{tid}/users/{uid}/extensions_config.json``."""
        return self.tenant_user_dir(tenant_id, user_id) / "extensions_config.json"

    # ── Tenant-scoped thread paths (production model) ───────────────────

    def tenant_user_thread_dir(self, tenant_id: str, user_id: str, thread_id: str) -> Path:
        """Thread directory under tenant/user hierarchy.

        ``{base_dir}/tenants/{tenant_id}/users/{user_id}/threads/{thread_id}/``

        Raises:
            ValueError: If any ID contains unsafe characters.
        """
        if not _SAFE_THREAD_ID_RE.match(thread_id):
            raise ValueError(f"Invalid thread_id {thread_id!r}: only alphanumeric characters, hyphens, and underscores are allowed.")
        return self.tenant_user_dir(tenant_id, user_id) / "threads" / thread_id

    def tenant_user_sandbox_user_data_dir(self, tenant_id: str, user_id: str, thread_id: str) -> Path:
        """Host path for user-data root under tenant/user/thread.

        ``tenants/{tid}/users/{uid}/threads/{tid}/user-data/``
        """
        return self.tenant_user_thread_dir(tenant_id, user_id, thread_id) / "user-data"

    def tenant_user_sandbox_work_dir(self, tenant_id: str, user_id: str, thread_id: str) -> Path:
        """Host workspace dir: ``tenants/{tid}/users/{uid}/threads/{tid}/user-data/workspace/``"""
        return self.tenant_user_sandbox_user_data_dir(tenant_id, user_id, thread_id) / "workspace"

    def tenant_user_sandbox_uploads_dir(self, tenant_id: str, user_id: str, thread_id: str) -> Path:
        """Host uploads dir: ``tenants/{tid}/users/{uid}/threads/{tid}/user-data/uploads/``"""
        return self.tenant_user_sandbox_user_data_dir(tenant_id, user_id, thread_id) / "uploads"

    def tenant_user_sandbox_outputs_dir(self, tenant_id: str, user_id: str, thread_id: str) -> Path:
        """Host outputs dir: ``tenants/{tid}/users/{uid}/threads/{tid}/user-data/outputs/``"""
        return self.tenant_user_sandbox_user_data_dir(tenant_id, user_id, thread_id) / "outputs"

    def ensure_tenant_user_thread_dirs(self, tenant_id: str, user_id: str, thread_id: str) -> None:
        """Create workspace, uploads, outputs under tenant/user/thread."""
        self.tenant_user_sandbox_work_dir(tenant_id, user_id, thread_id).mkdir(parents=True, exist_ok=True)
        self.tenant_user_sandbox_uploads_dir(tenant_id, user_id, thread_id).mkdir(parents=True, exist_ok=True)
        self.tenant_user_sandbox_outputs_dir(tenant_id, user_id, thread_id).mkdir(parents=True, exist_ok=True)

    def resolve_tenant_user_virtual_path(
        self, tenant_id: str, user_id: str, thread_id: str, virtual_path: str,
    ) -> Path:
        """Resolve a sandbox virtual path to host path under tenant/user/thread.

        Same logic as :meth:`resolve_virtual_path` but against the new hierarchy.

        Raises:
            ValueError: If prefix mismatch or path traversal detected.
        """
        stripped = virtual_path.lstrip("/")
        prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

        if stripped != prefix and not stripped.startswith(prefix + "/"):
            raise ValueError(f"Path must start with /{prefix}")

        relative = stripped[len(prefix):].lstrip("/")
        base = self.tenant_user_sandbox_user_data_dir(tenant_id, user_id, thread_id).resolve()
        actual = (base / relative).resolve()

        try:
            actual.relative_to(base)
        except ValueError:
            raise ValueError("Access denied: path traversal detected")

        return actual

    # ── ThreadContext convenience methods ──────────────────────────────

    def thread_dir_ctx(self, ctx: "ThreadContext") -> Path:
        """Thread dir from a validated ThreadContext."""
        return self.tenant_user_thread_dir(ctx.tenant_id, ctx.user_id, ctx.thread_id)

    def sandbox_user_data_dir_ctx(self, ctx: "ThreadContext") -> Path:
        return self.tenant_user_sandbox_user_data_dir(ctx.tenant_id, ctx.user_id, ctx.thread_id)

    def sandbox_work_dir_ctx(self, ctx: "ThreadContext") -> Path:
        return self.tenant_user_sandbox_work_dir(ctx.tenant_id, ctx.user_id, ctx.thread_id)

    def sandbox_uploads_dir_ctx(self, ctx: "ThreadContext") -> Path:
        return self.tenant_user_sandbox_uploads_dir(ctx.tenant_id, ctx.user_id, ctx.thread_id)

    def sandbox_outputs_dir_ctx(self, ctx: "ThreadContext") -> Path:
        return self.tenant_user_sandbox_outputs_dir(ctx.tenant_id, ctx.user_id, ctx.thread_id)

    def ensure_thread_dirs_ctx(self, ctx: "ThreadContext") -> None:
        self.ensure_tenant_user_thread_dirs(ctx.tenant_id, ctx.user_id, ctx.thread_id)

    def resolve_virtual_path_ctx(self, ctx: "ThreadContext", virtual_path: str) -> Path:
        return self.resolve_tenant_user_virtual_path(ctx.tenant_id, ctx.user_id, ctx.thread_id, virtual_path)

    # ── Sandbox state (independent of user data) ──────────────────────

    def sandbox_state_dir(self, thread_id: str) -> Path:
        """Runtime sandbox state directory: ``{base_dir}/sandbox_state/{thread_id}/``.

        Independent of tenant/user hierarchy — sandbox recovery only needs thread_id.
        """
        if not _SAFE_THREAD_ID_RE.match(thread_id):
            raise ValueError(f"Invalid thread_id {thread_id!r}: only alphanumeric characters, hyphens, and underscores are allowed.")
        return self.base_dir / "sandbox_state" / thread_id

    # ── DEPRECATED: flat thread paths (to be removed in Phase 5) ──────

    def thread_dir(self, thread_id: str) -> Path:  # DEPRECATED
        """
        Host path for a thread's data: `{base_dir}/threads/{thread_id}/`

        .. deprecated:: Use :meth:`tenant_user_thread_dir` instead.

        This directory contains a `user-data/` subdirectory that is mounted
        as `/mnt/user-data/` inside the sandbox.

        Raises:
            ValueError: If `thread_id` contains unsafe characters (path separators
                        or `..`) that could cause directory traversal.
        """
        if not _SAFE_THREAD_ID_RE.match(thread_id):
            raise ValueError(f"Invalid thread_id {thread_id!r}: only alphanumeric characters, hyphens, and underscores are allowed.")
        return self.base_dir / "threads" / thread_id

    def sandbox_work_dir(self, thread_id: str) -> Path:  # DEPRECATED
        """DEPRECATED: Use tenant_user_sandbox_work_dir instead."""
        return self.thread_dir(thread_id) / "user-data" / "workspace"

    def sandbox_uploads_dir(self, thread_id: str) -> Path:  # DEPRECATED
        """DEPRECATED: Use tenant_user_sandbox_uploads_dir instead."""
        return self.thread_dir(thread_id) / "user-data" / "uploads"

    def sandbox_outputs_dir(self, thread_id: str) -> Path:  # DEPRECATED
        """DEPRECATED: Use tenant_user_sandbox_outputs_dir instead."""
        return self.thread_dir(thread_id) / "user-data" / "outputs"

    def sandbox_user_data_dir(self, thread_id: str) -> Path:  # DEPRECATED
        """DEPRECATED: Use tenant_user_sandbox_user_data_dir instead."""
        return self.thread_dir(thread_id) / "user-data"

    def ensure_thread_dirs(self, thread_id: str) -> None:  # DEPRECATED
        """DEPRECATED: Use ensure_tenant_user_thread_dirs instead."""
        self.sandbox_work_dir(thread_id).mkdir(parents=True, exist_ok=True)
        self.sandbox_uploads_dir(thread_id).mkdir(parents=True, exist_ok=True)
        self.sandbox_outputs_dir(thread_id).mkdir(parents=True, exist_ok=True)

    def resolve_virtual_path(self, thread_id: str, virtual_path: str) -> Path:  # DEPRECATED
        """Resolve a sandbox virtual path to the actual host filesystem path.

        Args:
            thread_id: The thread ID.
            virtual_path: Virtual path as seen inside the sandbox, e.g.
                          ``/mnt/user-data/outputs/report.pdf``.
                          Leading slashes are stripped before matching.

        Returns:
            The resolved absolute host filesystem path.

        Raises:
            ValueError: If the path does not start with the expected virtual
                        prefix or a path-traversal attempt is detected.
        """
        stripped = virtual_path.lstrip("/")
        prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

        # Require an exact segment-boundary match to avoid prefix confusion
        # (e.g. reject paths like "mnt/user-dataX/...").
        if stripped != prefix and not stripped.startswith(prefix + "/"):
            raise ValueError(f"Path must start with /{prefix}")

        relative = stripped[len(prefix) :].lstrip("/")
        base = self.sandbox_user_data_dir(thread_id).resolve()
        actual = (base / relative).resolve()

        try:
            actual.relative_to(base)
        except ValueError:
            raise ValueError("Access denied: path traversal detected")

        return actual


# ── Singleton ────────────────────────────────────────────────────────────

_paths: Paths | None = None


def get_paths() -> Paths:
    """Return the global Paths singleton (lazy-initialized)."""
    global _paths
    if _paths is None:
        _paths = Paths()
    return _paths


def resolve_tenant_agents_dir(tenant_id: str | None) -> Path | None:
    """Resolve tenant-scoped agents directory.

    Returns ``None`` for the default tenant so callers fall back to the
    global agents directory.  For non-default tenants returns the
    ``tenants/{tenant_id}/agents/`` path.
    """
    if not tenant_id or tenant_id == "default":
        return None
    return get_paths().tenant_agents_dir(tenant_id)


def resolve_tenant_user_agents_dir(tenant_id: str | None, user_id: str | None) -> Path | None:
    """Resolve user-scoped agents directory.

    Returns ``None`` for the default tenant or anonymous user so callers
    skip the personal layer.  For identified users returns the
    ``tenants/{tenant_id}/users/{user_id}/agents/`` path.
    """
    if not tenant_id or tenant_id == "default":
        return None
    if not user_id or user_id == "anonymous":
        return None
    return get_paths().tenant_user_agents_dir(tenant_id, user_id)

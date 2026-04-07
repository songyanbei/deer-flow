"""Memory updater for reading, writing, and updating memory data."""

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.agents.memory.prompt import (
    MEMORY_UPDATE_PROMPT,
    format_conversation_for_update,
)
from src.config.memory_config import get_memory_config
from src.config.paths import get_paths
from src.models import create_chat_model


def _get_memory_file_path(agent_name: str | None = None, tenant_id: str | None = None, user_id: str | None = None) -> Path:
    """Get the path to the memory file.

    Args:
        agent_name: If provided, returns the per-agent memory file path.
                    If None, returns the global/tenant-level memory file path.
        tenant_id: If provided (and not "default"), returns the tenant-scoped
                   memory file path.  When None or "default", falls back to
                   the global path for backward compatibility.
        user_id: If provided (and not "anonymous"), returns the user-scoped
                 path within the tenant.

    Returns:
        Path to the memory file.
    """
    paths = get_paths()
    effective_tenant = tenant_id if tenant_id and tenant_id != "default" else None
    effective_user = user_id if user_id and user_id != "anonymous" else None

    # User-level isolation: tenants/{tid}/users/{uid}/...
    if effective_tenant and effective_user:
        if agent_name is not None:
            return paths.tenant_user_agent_memory_file(effective_tenant, effective_user, agent_name)
        return paths.tenant_user_memory_file(effective_tenant, effective_user)

    # Tenant-level fallback (no user_id)
    if agent_name is not None:
        if effective_tenant:
            return paths.tenant_agent_memory_file(effective_tenant, agent_name)
        return paths.agent_memory_file(agent_name)

    if effective_tenant:
        return paths.tenant_memory_file(effective_tenant)

    config = get_memory_config()
    if config.storage_path:
        p = Path(config.storage_path)
        return p if p.is_absolute() else paths.base_dir / p
    return paths.memory_file


def _create_empty_memory() -> dict[str, Any]:
    """Create an empty memory structure."""
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "version": "1.0",
        "lastUpdated": now,
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


# Memory cache: keyed by (tenant_id, user_id, agent_name) tuple.
# Value: (memory_data, file_mtime)
_CacheKey = tuple[str | None, str | None, str | None]
_memory_cache: dict[_CacheKey, tuple[dict[str, Any], float | None]] = {}


def get_memory_data(agent_name: str | None = None, tenant_id: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    """Get the current memory data (cached with file modification time check).

    The cache is automatically invalidated if the memory file has been modified
    since the last load, ensuring fresh data is always returned.

    Args:
        agent_name: If provided, loads per-agent memory. If None, loads global memory.
        tenant_id: If provided, loads tenant-scoped memory.
        user_id: If provided, loads user-scoped memory within the tenant.

    Returns:
        The memory data dictionary.
    """
    cache_key: _CacheKey = (tenant_id, user_id, agent_name)
    file_path = _get_memory_file_path(agent_name, tenant_id, user_id)

    # Get current file modification time
    try:
        current_mtime = file_path.stat().st_mtime if file_path.exists() else None
    except OSError:
        current_mtime = None

    cached = _memory_cache.get(cache_key)

    # Invalidate cache if file has been modified or doesn't exist
    if cached is None or cached[1] != current_mtime:
        memory_data = _load_memory_from_file(agent_name, tenant_id, user_id)
        _memory_cache[cache_key] = (memory_data, current_mtime)
        return memory_data

    return cached[0]


def reload_memory_data(agent_name: str | None = None, tenant_id: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    """Reload memory data from file, forcing cache invalidation.

    Args:
        agent_name: If provided, reloads per-agent memory. If None, reloads global memory.
        tenant_id: If provided, reloads tenant-scoped memory.
        user_id: If provided, reloads user-scoped memory within the tenant.

    Returns:
        The reloaded memory data dictionary.
    """
    cache_key: _CacheKey = (tenant_id, user_id, agent_name)
    file_path = _get_memory_file_path(agent_name, tenant_id, user_id)
    memory_data = _load_memory_from_file(agent_name, tenant_id, user_id)

    try:
        mtime = file_path.stat().st_mtime if file_path.exists() else None
    except OSError:
        mtime = None

    _memory_cache[cache_key] = (memory_data, mtime)
    return memory_data


def _load_memory_from_file(agent_name: str | None = None, tenant_id: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    """Load memory data from file.

    Args:
        agent_name: If provided, loads per-agent memory file. If None, loads global.
        tenant_id: If provided, loads tenant-scoped file.
        user_id: If provided, loads user-scoped file within the tenant.

    Returns:
        The memory data dictionary.
    """
    file_path = _get_memory_file_path(agent_name, tenant_id, user_id)

    if not file_path.exists():
        return _create_empty_memory()

    try:
        with open(file_path, encoding="utf-8") as f:
            data = json.load(f)
        return data
    except (json.JSONDecodeError, OSError) as e:
        print(f"Failed to load memory file: {e}")
        return _create_empty_memory()


# Matches sentences that describe a file-upload *event* rather than general
# file-related work.  Deliberately narrow to avoid removing legitimate facts
# such as "User works with CSV files" or "prefers PDF export".
_UPLOAD_SENTENCE_RE = re.compile(
    r"[^.!?]*\b(?:"
    r"upload(?:ed|ing)?(?:\s+\w+){0,3}\s+(?:file|files?|document|documents?|attachment|attachments?)"
    r"|file\s+upload"
    r"|/mnt/user-data/uploads/"
    r"|<uploaded_files>"
    r")[^.!?]*[.!?]?\s*",
    re.IGNORECASE,
)


def _strip_upload_mentions_from_memory(memory_data: dict[str, Any]) -> dict[str, Any]:
    """Remove sentences about file uploads from all memory summaries and facts.

    Uploaded files are session-scoped; persisting upload events in long-term
    memory causes the agent to search for non-existent files in future sessions.
    """
    # Scrub summaries in user/history sections
    for section in ("user", "history"):
        section_data = memory_data.get(section, {})
        for _key, val in section_data.items():
            if isinstance(val, dict) and "summary" in val:
                cleaned = _UPLOAD_SENTENCE_RE.sub("", val["summary"]).strip()
                cleaned = re.sub(r"  +", " ", cleaned)
                val["summary"] = cleaned

    # Also remove any facts that describe upload events
    facts = memory_data.get("facts", [])
    if facts:
        memory_data["facts"] = [
            f
            for f in facts
            if not _UPLOAD_SENTENCE_RE.search(f.get("content", ""))
        ]

    return memory_data


def _save_memory_to_file(memory_data: dict[str, Any], agent_name: str | None = None, tenant_id: str | None = None, user_id: str | None = None) -> bool:
    """Save memory data to file and update cache.

    Args:
        memory_data: The memory data to save.
        agent_name: If provided, saves to per-agent memory file. If None, saves to global.
        tenant_id: If provided, saves to tenant-scoped file.
        user_id: If provided, saves to user-scoped file within the tenant.

    Returns:
        True if successful, False otherwise.
    """
    file_path = _get_memory_file_path(agent_name, tenant_id, user_id)

    try:
        # Ensure directory exists
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Update lastUpdated timestamp
        memory_data["lastUpdated"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        # Write atomically using temp file
        temp_path = file_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump(memory_data, f, indent=2, ensure_ascii=False)

        # Rename temp file to actual file (atomic on most systems)
        temp_path.replace(file_path)

        # Update cache and file modification time
        try:
            mtime = file_path.stat().st_mtime
        except OSError:
            mtime = None

        _memory_cache[(tenant_id, user_id, agent_name)] = (memory_data, mtime)

        print(f"Memory saved to {file_path}")
        return True
    except OSError as e:
        print(f"Failed to save memory file: {e}")
        return False


class MemoryUpdater:
    """Updates memory using LLM based on conversation context."""

    def __init__(self, model_name: str | None = None):
        """Initialize the memory updater.

        Args:
            model_name: Optional model name to use. If None, uses config or default.
        """
        self._model_name = model_name

    def _get_model(self):
        """Get the model for memory updates."""
        config = get_memory_config()
        model_name = self._model_name or config.model_name
        return create_chat_model(name=model_name, thinking_enabled=False)

    def update_memory(self, messages: list[Any], thread_id: str | None = None, agent_name: str | None = None, tenant_id: str | None = None, user_id: str | None = None) -> bool:
        """Update memory based on conversation messages.

        Args:
            messages: List of conversation messages.
            thread_id: Optional thread ID for tracking source.
            agent_name: If provided, updates per-agent memory. If None, updates global memory.
            tenant_id: If provided, updates tenant-scoped memory.
            user_id: If provided, updates user-scoped memory within the tenant.

        Returns:
            True if update was successful, False otherwise.
        """
        config = get_memory_config()
        if not config.enabled:
            return False

        if not messages:
            return False

        try:
            # Get current memory
            current_memory = get_memory_data(agent_name, tenant_id, user_id)

            # Format conversation for prompt
            conversation_text = format_conversation_for_update(messages)

            if not conversation_text.strip():
                return False

            # Build prompt
            prompt = MEMORY_UPDATE_PROMPT.format(
                current_memory=json.dumps(current_memory, indent=2),
                conversation=conversation_text,
            )

            # Call LLM
            model = self._get_model()
            response = model.invoke(prompt)
            response_text = str(response.content).strip()

            # Parse response
            # Remove markdown code blocks if present
            if response_text.startswith("```"):
                lines = response_text.split("\n")
                response_text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])

            update_data = json.loads(response_text)

            # Apply updates
            updated_memory = self._apply_updates(current_memory, update_data, thread_id)

            # Strip file-upload mentions from all summaries before saving.
            # Uploaded files are session-scoped and won't exist in future sessions,
            # so recording upload events in long-term memory causes the agent to
            # try (and fail) to locate those files in subsequent conversations.
            updated_memory = _strip_upload_mentions_from_memory(updated_memory)

            # Save
            return _save_memory_to_file(updated_memory, agent_name, tenant_id, user_id)

        except json.JSONDecodeError as e:
            print(f"Failed to parse LLM response for memory update: {e}")
            return False
        except Exception as e:
            print(f"Memory update failed: {e}")
            return False

    def _apply_updates(
        self,
        current_memory: dict[str, Any],
        update_data: dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Apply LLM-generated updates to memory.

        Args:
            current_memory: Current memory data.
            update_data: Updates from LLM.
            thread_id: Optional thread ID for tracking.

        Returns:
            Updated memory data.
        """
        config = get_memory_config()
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")

        # Update user sections
        user_updates = update_data.get("user", {})
        for section in ["workContext", "personalContext", "topOfMind"]:
            section_data = user_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["user"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # Update history sections
        history_updates = update_data.get("history", {})
        for section in ["recentMonths", "earlierContext", "longTermBackground"]:
            section_data = history_updates.get(section, {})
            if section_data.get("shouldUpdate") and section_data.get("summary"):
                current_memory["history"][section] = {
                    "summary": section_data["summary"],
                    "updatedAt": now,
                }

        # Remove facts
        facts_to_remove = set(update_data.get("factsToRemove", []))
        if facts_to_remove:
            current_memory["facts"] = [f for f in current_memory.get("facts", []) if f.get("id") not in facts_to_remove]

        # Add new facts
        new_facts = update_data.get("newFacts", [])
        for fact in new_facts:
            confidence = fact.get("confidence", 0.5)
            if confidence >= config.fact_confidence_threshold:
                fact_entry = {
                    "id": f"fact_{uuid.uuid4().hex[:8]}",
                    "content": fact.get("content", ""),
                    "category": fact.get("category", "context"),
                    "confidence": confidence,
                    "createdAt": now,
                    "source": thread_id or "unknown",
                }
                current_memory["facts"].append(fact_entry)

        # Enforce max facts limit
        if len(current_memory["facts"]) > config.max_facts:
            # Sort by confidence and keep top ones
            current_memory["facts"] = sorted(
                current_memory["facts"],
                key=lambda f: f.get("confidence", 0),
                reverse=True,
            )[: config.max_facts]

        return current_memory


def update_memory_from_conversation(messages: list[Any], thread_id: str | None = None, agent_name: str | None = None, tenant_id: str | None = None, user_id: str | None = None) -> bool:
    """Convenience function to update memory from a conversation.

    Args:
        messages: List of conversation messages.
        thread_id: Optional thread ID.
        agent_name: If provided, updates per-agent memory. If None, updates global memory.
        tenant_id: If provided, updates tenant-scoped memory.
        user_id: If provided, updates user-scoped memory within the tenant.

    Returns:
        True if successful, False otherwise.
    """
    updater = MemoryUpdater()
    return updater.update_memory(messages, thread_id, agent_name, tenant_id, user_id)


def migrate_tenant_memory_to_user_level(tenant_id: str, user_id: str) -> bool:
    """Copy tenant-level memory to user-level path if the user-level file doesn't exist.

    This is a one-time migration helper for upgrading from tenant-level to
    user-level memory isolation.  If the user already has a memory file,
    this function does nothing (returns False).

    Args:
        tenant_id: The tenant to migrate from.
        user_id: The user to migrate to.

    Returns:
        True if migration was performed, False if skipped.
    """
    import shutil

    tenant_path = _get_memory_file_path(agent_name=None, tenant_id=tenant_id, user_id=None)
    user_path = _get_memory_file_path(agent_name=None, tenant_id=tenant_id, user_id=user_id)

    if user_path.exists():
        return False  # already migrated

    if not tenant_path.exists():
        return False  # nothing to migrate

    user_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tenant_path, user_path)
    return True

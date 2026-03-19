from typing import Any, Mapping

from src.agents.intervention.fingerprint import generate_tool_semantic_fingerprint

DEFAULT_TOOL_INTERVENTION_MAX_REUSE = 3
DEFAULT_CLARIFICATION_MAX_REUSE = -1


def is_intervention_cache_valid(
    cached: Mapping[str, Any] | None,
    *,
    require_resume_behavior: bool,
) -> bool:
    """Return True when a cached decision is still reusable."""
    if not isinstance(cached, Mapping):
        return False
    if require_resume_behavior and cached.get("resolution_behavior") != "resume_current_task":
        return False
    max_reuse = cached.get("max_reuse", -1)
    reuse_count = cached.get("reuse_count", 0)
    if not isinstance(max_reuse, int):
        max_reuse = -1
    if not isinstance(reuse_count, int):
        reuse_count = 0
    if max_reuse != -1 and reuse_count >= max_reuse:
        return False
    return True


def increment_cache_reuse_count(cached: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copied cache entry with its reuse count incremented."""
    reuse_count = cached.get("reuse_count", 0)
    if not isinstance(reuse_count, int):
        reuse_count = 0
    return {
        **cached,
        "reuse_count": reuse_count + 1,
    }


def default_max_reuse_for_intervention_type(intervention_type: str) -> int:
    if intervention_type == "before_tool":
        return DEFAULT_TOOL_INTERVENTION_MAX_REUSE
    return DEFAULT_CLARIFICATION_MAX_REUSE


def derive_intervention_cache_key(intervention_request: Mapping[str, Any] | None) -> str | None:
    """Derive the semantic cache key for an intervention request."""
    if not isinstance(intervention_request, Mapping):
        return None
    intervention_type = str(intervention_request.get("intervention_type") or "").strip()
    if intervention_type == "before_tool":
        context = intervention_request.get("context")
        tool_args = context.get("tool_args", {}) if isinstance(context, Mapping) else {}
        tool_name = str(intervention_request.get("tool_name") or "").strip()
        source_agent = str(intervention_request.get("source_agent") or "").strip()
        if not tool_name or not source_agent or not isinstance(tool_args, dict):
            return None
        return generate_tool_semantic_fingerprint(source_agent, tool_name, tool_args)
    if intervention_type == "clarification":
        fingerprint = str(intervention_request.get("fingerprint") or "").strip()
        return fingerprint or None
    return None


def build_cached_intervention_entry(
    intervention_request: Mapping[str, Any] | None,
    *,
    action_key: str,
    payload: dict[str, Any],
    resolution_behavior: str,
    resolved_at: str,
) -> tuple[str | None, dict[str, Any] | None]:
    """Build a normalized cache entry from a resolved intervention."""
    semantic_fp = derive_intervention_cache_key(intervention_request)
    if semantic_fp is None:
        return None, None
    intervention_type = str((intervention_request or {}).get("intervention_type") or "").strip()
    entry = {
        "action_key": action_key,
        "payload": payload,
        "resolution_behavior": resolution_behavior,
        "resolved_at": resolved_at,
        "intervention_type": intervention_type,
        "source_agent": str((intervention_request or {}).get("source_agent") or "").strip(),
        "max_reuse": default_max_reuse_for_intervention_type(intervention_type),
        "reuse_count": 0,
    }
    return semantic_fp, entry

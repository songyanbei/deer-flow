"""Display projection builder for intervention requests.

Transforms raw tool-level intervention data into user-readable display payloads.

Projection strategy (layered):
1. Scenario-specific projection (e.g., meeting booking)
2. Operation-type projection (e.g., create resource, send notification)
3. Generic fallback projection (always produces readable content)
"""

import logging
import os
from datetime import UTC, datetime, timezone, tzinfo as TzInfo
from typing import Any
from zoneinfo import ZoneInfo

from src.agents.thread_state import (
    InterventionDisplay,
    InterventionDisplayDebug,
    InterventionDisplayItem,
    InterventionDisplaySection,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Timezone resolution
# ---------------------------------------------------------------------------

def _resolve_display_timezone(tz: str | None = None) -> TzInfo:
    """Resolve the timezone used for formatting timestamps in display cards.

    Resolution order:
    1. Explicit ``tz`` argument (passed through the call chain).
    2. ``DEER_FLOW_TIMEZONE`` environment variable (e.g. ``"Asia/Shanghai"``).
    3. System local timezone (the machine's default).

    Falls back to UTC only when resolution fails.
    """
    # 1. Explicit argument
    if tz:
        try:
            return ZoneInfo(tz)
        except (KeyError, Exception):
            logger.warning("[DisplayProjection] Invalid timezone '%s', falling back.", tz)

    # 2. Environment variable
    env_tz = os.environ.get("DEER_FLOW_TIMEZONE")
    if env_tz:
        try:
            return ZoneInfo(env_tz)
        except (KeyError, Exception):
            logger.warning("[DisplayProjection] Invalid DEER_FLOW_TIMEZONE '%s', falling back.", env_tz)

    # 3. System local timezone
    try:
        local_offset = datetime.now(timezone.utc).astimezone().tzinfo
        if local_offset is not None:
            return local_offset
    except Exception:
        pass

    # 4. Last resort
    return UTC


# ---------------------------------------------------------------------------
# Timestamp formatting
# ---------------------------------------------------------------------------

def _format_epoch_ms(epoch_ms: int | float | str | None, tz: TzInfo | None = None) -> str:
    """Convert epoch milliseconds to human-readable datetime string.

    Uses the provided timezone for display.  Falls back to
    ``_resolve_display_timezone()`` when *tz* is ``None``.
    """
    if epoch_ms is None:
        return ""
    try:
        display_tz = tz or _resolve_display_timezone()
        ts = int(epoch_ms) / 1000
        dt = datetime.fromtimestamp(ts, tz=display_tz)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OSError):
        return str(epoch_ms)


def _format_notice_times(notice_times: list | None) -> str:
    """Format notification times list into readable string."""
    if not notice_times:
        return ""
    labels = []
    for minutes in notice_times:
        try:
            m = int(minutes)
            if m < 60:
                labels.append(f"{m}分钟前")
            else:
                labels.append(f"{m // 60}小时前")
        except (ValueError, TypeError):
            labels.append(str(minutes))
    return "、".join(labels)


def _format_actors(actors: list | None) -> str:
    """Format attendee list into readable string."""
    if not actors:
        return ""
    names = []
    for actor in actors:
        if isinstance(actor, dict):
            name = actor.get("personName") or actor.get("name") or actor.get("openId", "")
            if name:
                names.append(str(name))
        elif isinstance(actor, str):
            names.append(actor)
    return "、".join(names) if names else ""


# ---------------------------------------------------------------------------
# Value normalization helpers
# ---------------------------------------------------------------------------

_HIDDEN_FIELD_PATTERNS = {"id", "openid", "roomid", "userid", "token", "secret", "key"}


def _is_internal_field(key: str) -> bool:
    """Check if a field key looks like an internal ID that should be hidden."""
    lower = key.lower()
    return any(pattern in lower for pattern in _HIDDEN_FIELD_PATTERNS)


def _humanize_key(key: str) -> str:
    """Convert camelCase/snake_case key to readable label."""
    # Common known mappings
    known = {
        "title": "标题",
        "content": "内容",
        "description": "描述",
        "startDate": "开始时间",
        "endDate": "结束时间",
        "startTime": "开始时间",
        "endTime": "结束时间",
        "start_date": "开始时间",
        "end_date": "结束时间",
        "personName": "发起人",
        "person_name": "发起人",
        "actors": "参与人",
        "attendees": "参与人",
        "participants": "参与人",
        "noticeTimes": "提醒",
        "notice_times": "提醒",
        "roomName": "会议室",
        "room_name": "会议室",
        "subject": "主题",
        "topic": "主题",
        "location": "地点",
        "organizer": "组织者",
        "status": "状态",
        "name": "名称",
        "email": "邮箱",
        "phone": "电话",
        "city": "城市",
        "comment": "备注",
        "reason": "原因",
        "priority": "优先级",
        "type": "类型",
        "category": "分类",
        "amount": "金额",
        "count": "数量",
        "message": "消息",
        "recipient": "收件人",
        "sender": "发送者",
    }
    if key in known:
        return known[key]
    # Fallback: keep the key as-is (it's still better than nothing)
    return key


def _humanize_value(key: str, value: Any, tz: TzInfo | None = None) -> str:
    """Convert a raw value to a human-readable string based on key hints."""
    if value is None:
        return ""
    # Timestamp detection by key name
    key_lower = key.lower()
    if any(ts_hint in key_lower for ts_hint in ("date", "time", "timestamp", "created", "updated")):
        if isinstance(value, (int, float)) and value > 1_000_000_000:
            # Likely epoch (seconds or milliseconds)
            if value > 1_000_000_000_000:
                return _format_epoch_ms(value, tz=tz)
            return _format_epoch_ms(value * 1000, tz=tz)
    # List of actors/attendees
    if key_lower in ("actors", "attendees", "participants") and isinstance(value, list):
        return _format_actors(value)
    # Notice times
    if key_lower in ("noticetimes", "notice_times") and isinstance(value, list):
        return _format_notice_times(value)
    # Boolean
    if isinstance(value, bool):
        return "是" if value else "否"
    # Dict - just summarize
    if isinstance(value, dict):
        return f"({len(value)} 项)"
    # List
    if isinstance(value, list):
        if all(isinstance(v, str) for v in value):
            return "、".join(value)
        return f"({len(value)} 项)"
    return str(value)


# ---------------------------------------------------------------------------
# Operation-type classification
# ---------------------------------------------------------------------------

_OPERATION_TYPE_MAP = {
    "create": ("创建", "将要创建以下资源，请确认："),
    "update": ("更新", "将要更新以下内容，请确认："),
    "delete": ("删除", "将要删除以下内容，请确认："),
    "remove": ("删除", "将要移除以下内容，请确认："),
    "cancel": ("取消", "将要取消以下内容，请确认："),
    "send": ("发送", "将要发送以下内容，请确认："),
    "submit": ("提交", "将要提交以下内容，请确认："),
    "book": ("预定", "将要预定以下资源，请确认："),
    "reserve": ("预定", "将要预定以下资源，请确认："),
    "schedule": ("安排", "将要安排以下内容，请确认："),
    "publish": ("发布", "将要发布以下内容，请确认："),
    "deploy": ("部署", "将要执行部署操作，请确认："),
    "transfer": ("转移", "将要执行转移操作，请确认："),
    "pay": ("支付", "将要执行支付操作，请确认："),
    "approve": ("审批", "将要执行审批操作，请确认："),
    "reject": ("拒绝", "将要执行拒绝操作，请确认："),
    "modify": ("修改", "将要修改以下内容，请确认："),
    "insert": ("添加", "将要添加以下内容，请确认："),
    "execute": ("执行", "将要执行以下操作，请确认："),
    "run": ("执行", "将要执行以下操作，请确认："),
    "release": ("发布", "将要发布以下内容，请确认："),
    "drop": ("删除", "将要删除以下内容，请确认："),
    "write": ("写入", "将要写入以下内容，请确认："),
    "confirm": ("确认", "将要确认以下内容："),
}


def _classify_operation(tool_name: str) -> tuple[str, str] | None:
    """Classify a tool name into an operation type."""
    name_lower = tool_name.lower()
    for keyword, (label, summary) in _OPERATION_TYPE_MAP.items():
        if keyword in name_lower:
            return label, summary
    return None


# ---------------------------------------------------------------------------
# Scenario-specific projections
# ---------------------------------------------------------------------------

def _project_meeting_create(tool_name: str, tool_args: dict[str, Any], tz: TzInfo | None = None) -> InterventionDisplay | None:
    """Scenario projection for meeting creation tools."""
    title_text = tool_args.get("title") or "未命名会议"
    person_name = tool_args.get("personName") or ""
    start = _format_epoch_ms(tool_args.get("startDate"), tz=tz)
    end = _format_epoch_ms(tool_args.get("endDate"), tz=tz)
    content = tool_args.get("content") or ""
    actors_str = _format_actors(tool_args.get("actors"))
    notice_str = _format_notice_times(tool_args.get("noticeTimes"))

    # Build time range
    time_range = ""
    if start and end:
        # If same date, show compact: 2026-03-18 09:00 - 10:00
        if start[:10] == end[:10]:
            time_range = f"{start} ~ {end[11:]}"
        else:
            time_range = f"{start} ~ {end}"
    elif start:
        time_range = start

    # Build summary
    summary = f"即将预定会议「{title_text}」"
    if person_name:
        summary += f"，发起人：{person_name}"

    # Build detail items
    items: list[InterventionDisplayItem] = []
    items.append({"label": "会议主题", "value": title_text})
    if time_range:
        items.append({"label": "会议时间", "value": time_range})
    if person_name:
        items.append({"label": "发起人", "value": person_name})
    if content:
        items.append({"label": "会议内容", "value": content})
    if actors_str:
        items.append({"label": "参与人", "value": actors_str})
    if notice_str:
        items.append({"label": "提醒", "value": notice_str})

    sections: list[InterventionDisplaySection] = [{"title": "会议详情", "items": items}]

    display: InterventionDisplay = {
        "title": "确认预定会议",
        "summary": summary,
        "sections": sections,
        "risk_tip": "确认后将创建会议并通知参与人",
        "primary_action_label": "确认预定",
        "secondary_action_label": "取消",
        "respond_action_label": "修改后预定",
        "respond_placeholder": "请输入需要修改的内容...",
        "debug": {
            "tool_name": tool_name,
            "raw_args": tool_args,
        },
    }
    return display


def _project_meeting_update(tool_name: str, tool_args: dict[str, Any], tz: TzInfo | None = None) -> InterventionDisplay | None:
    """Scenario projection for meeting update tools."""
    items: list[InterventionDisplayItem] = []
    if tool_args.get("title"):
        items.append({"label": "会议主题", "value": tool_args["title"]})
    start = _format_epoch_ms(tool_args.get("startDate"), tz=tz)
    end = _format_epoch_ms(tool_args.get("endDate"), tz=tz)
    if start:
        items.append({"label": "开始时间", "value": start})
    if end:
        items.append({"label": "结束时间", "value": end})
    if tool_args.get("content"):
        items.append({"label": "会议内容", "value": tool_args["content"]})
    add_actors = _format_actors(tool_args.get("addActors"))
    if add_actors:
        items.append({"label": "新增参与人", "value": add_actors})
    del_actors = _format_actors(tool_args.get("delActors"))
    if del_actors:
        items.append({"label": "移除参与人", "value": del_actors})

    if not items:
        return None

    display: InterventionDisplay = {
        "title": "确认修改会议",
        "summary": "即将修改会议信息，请确认以下变更：",
        "sections": [{"title": "变更内容", "items": items}],
        "primary_action_label": "确认修改",
        "secondary_action_label": "取消",
        "respond_action_label": "修改后提交",
        "respond_placeholder": "请输入需要调整的内容...",
        "debug": {
            "tool_name": tool_name,
            "raw_args": tool_args,
        },
    }
    return display


def _project_meeting_cancel(tool_name: str, tool_args: dict[str, Any], tz: TzInfo | None = None) -> InterventionDisplay | None:
    """Scenario projection for meeting cancellation tools."""
    display: InterventionDisplay = {
        "title": "确认取消会议",
        "summary": "即将取消会议，取消后无法恢复。",
        "risk_tip": "取消后会议将被删除，已通知的参与人将收到取消通知",
        "primary_action_label": "确认取消",
        "secondary_action_label": "保留会议",
        "debug": {
            "tool_name": tool_name,
            "raw_args": tool_args,
        },
    }
    return display


# Scenario registry: maps tool_name patterns to projection functions
_SCENARIO_PROJECTIONS: list[tuple[str, Any]] = [
    ("meeting_createmeeting", _project_meeting_create),
    ("meeting_updatemeeting", _project_meeting_update),
    ("meeting_cancelmeeting", _project_meeting_cancel),
]


def _try_scenario_projection(tool_name: str, tool_args: dict[str, Any], tz: TzInfo | None = None) -> InterventionDisplay | None:
    """Try to find a scenario-specific projection for the tool."""
    name_lower = tool_name.lower().replace("-", "").replace("_", "")
    for pattern, projector in _SCENARIO_PROJECTIONS:
        normalized_pattern = pattern.replace("-", "").replace("_", "")
        if normalized_pattern in name_lower:
            try:
                return projector(tool_name, tool_args, tz=tz)
            except Exception as e:
                logger.warning("[DisplayProjection] Scenario projection failed for '%s': %s", tool_name, e)
                return None
    return None


# ---------------------------------------------------------------------------
# Operation-type projection
# ---------------------------------------------------------------------------

def _build_operation_type_display(
    tool_name: str,
    tool_args: dict[str, Any],
    operation_label: str,
    operation_summary: str,
    tz: TzInfo | None = None,
) -> InterventionDisplay:
    """Build a display from operation type classification."""
    # Extract readable items from tool_args
    items: list[InterventionDisplayItem] = []
    for key, value in tool_args.items():
        if _is_internal_field(key):
            continue
        if value is None or value == "":
            continue
        label = _humanize_key(key)
        display_value = _humanize_value(key, value, tz=tz)
        if display_value:
            items.append({"label": label, "value": display_value})

    sections: list[InterventionDisplaySection] | None = None
    if items:
        sections = [{"title": "操作详情", "items": items}]

    # Derive a readable tool label
    readable_tool = tool_name.replace("_", " ").replace("-", " ")
    # Strip common prefixes like "meeting " to reduce redundancy
    for prefix in ("meeting ", "contacts ", "hr ", "hcm "):
        if readable_tool.lower().startswith(prefix):
            readable_tool = readable_tool[len(prefix):]
            break

    display: InterventionDisplay = {
        "title": f"确认{operation_label}操作",
        "summary": operation_summary,
        "sections": sections,
        "primary_action_label": f"确认{operation_label}",
        "secondary_action_label": "取消",
        "respond_action_label": "修改后执行",
        "respond_placeholder": "请输入修改意见...",
        "debug": {
            "tool_name": tool_name,
            "raw_args": tool_args,
        },
    }
    return display


# ---------------------------------------------------------------------------
# Generic fallback projection
# ---------------------------------------------------------------------------

def _build_fallback_display(
    tool_name: str,
    tool_args: dict[str, Any],
    agent_name: str,
    tz: TzInfo | None = None,
) -> InterventionDisplay:
    """Build a generic fallback display when no specialized projection exists."""
    items: list[InterventionDisplayItem] = []
    for key, value in tool_args.items():
        if _is_internal_field(key):
            continue
        if value is None or value == "":
            continue
        label = _humanize_key(key)
        display_value = _humanize_value(key, value, tz=tz)
        if display_value:
            items.append({"label": label, "value": display_value})

    sections: list[InterventionDisplaySection] | None = None
    if items:
        sections = [{"items": items}]

    display: InterventionDisplay = {
        "title": "操作确认",
        "summary": "以下操作需要您确认后才能继续执行。",
        "sections": sections,
        "primary_action_label": "确认执行",
        "secondary_action_label": "取消",
        "respond_action_label": "修改后执行",
        "respond_placeholder": "请输入修改意见...",
        "debug": {
            "source_agent": agent_name,
            "tool_name": tool_name,
            "raw_args": tool_args,
        },
    }
    return display


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_display_projection(
    tool_name: str,
    tool_args: dict[str, Any],
    agent_name: str = "",
    timezone: str | None = None,
) -> InterventionDisplay:
    """Build a user-facing display projection for an intervention request.

    Projection strategy (layered):
    1. Scenario-specific projection (highest quality)
    2. Operation-type projection (medium quality)
    3. Generic fallback projection (always works)

    Args:
        tool_name: The tool being intercepted.
        tool_args: The tool call arguments.
        agent_name: The agent executing the tool.
        timezone: IANA timezone identifier (e.g. ``"Asia/Shanghai"``).
            When ``None``, resolved via ``DEER_FLOW_TIMEZONE`` env var
            or the system local timezone.

    Returns:
        InterventionDisplay with user-readable content.
    """
    display_tz = _resolve_display_timezone(timezone)

    # 1. Try scenario-specific projection
    display = _try_scenario_projection(tool_name, tool_args, tz=display_tz)
    if display is not None:
        logger.info("[DisplayProjection] Scenario projection matched for '%s'.", tool_name)
        return display

    # 2. Try operation-type projection
    operation = _classify_operation(tool_name)
    if operation is not None:
        label, summary = operation
        logger.info("[DisplayProjection] Operation-type projection '%s' for '%s'.", label, tool_name)
        return _build_operation_type_display(tool_name, tool_args, label, summary, tz=display_tz)

    # 3. Generic fallback
    logger.info("[DisplayProjection] Fallback projection for '%s'.", tool_name)
    return _build_fallback_display(tool_name, tool_args, agent_name, tz=display_tz)

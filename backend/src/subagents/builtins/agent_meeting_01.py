"""Laifu meeting subagent configuration."""

from src.subagents.config import SubagentConfig

from .laifu_shared import build_react_system_prompt


MEETING_TOOL_NAMES = [
    "meeting_hasNewMeetingRoomBooking",
    "meeting_getMeetingRoomBookings",
    "meeting_getFreeMeetingRooms",
    "meeting_getMeetingRoomAttendees",
    "meeting_createMeeting",
    "meeting_getMeetingDetail",
    "meeting_cancelMeeting",
    "meeting_queryMeetingsByDay",
    "meeting_updateMeeting",
    "meeting_queryMeetingsByRange",
    "meeting_getRecentMeetings",
    "meeting_queryUserMeetings",
    "get_current_time",
    "time_toTimestamp",
    "yield_for_help",
    "ask_human",
    "ask_clarification",
]


AGENT_MEETING_01_CONFIG = SubagentConfig(
    name="agent_meeting_01",
    description=(
        "负责处理所有会议室预定、查询、修改、取消的请求，以及多地协同会议的全流程。"
        "可以查询空闲会议室、创建/修改/取消会议、查看指定用户的会议列表、查看会议详情和参会人。"
        "除了以上功能之外的所有功能，你必须像其他智能体发起求助。"
    ),
    system_prompt=build_react_system_prompt(
        description=(
            "负责处理所有会议室预定、查询、修改、取消的请求，以及多地协同会议的全流程。"
            "可以查询空闲会议室、创建/修改/取消会议、查看指定用户的会议列表、查看会议详情和参会人。"
            "除了以上功能之外的所有功能，你必须像其他智能体发起求助。"
        ),
        tool_names=MEETING_TOOL_NAMES,
        skill_name="meeting",
    ),
    tools=MEETING_TOOL_NAMES,
    model="inherit",
    max_turns=30,
)

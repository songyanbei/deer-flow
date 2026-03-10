"""Tool-name sets used to keep subagent-only tools hidden from the lead agent."""

CONTACTS_PRIVATE_TOOL_NAMES = {
    "getUserDetailByCode",
    "getUserByName",
    "searchUsersByName",
    "getDepartmentMembers",
    "getUsersBatchByCodes",
    "getDepartmentInfoByName",
}

HR_PRIVATE_TOOL_NAMES = {
    "hr_attendance_read",
}

MEETING_PRIVATE_TOOL_NAMES = {
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
}

PRIVATE_SUBAGENT_TOOL_NAMES = (
    CONTACTS_PRIVATE_TOOL_NAMES
    | HR_PRIVATE_TOOL_NAMES
    | MEETING_PRIVATE_TOOL_NAMES
    | {"yield_for_help", "ask_human"}
)

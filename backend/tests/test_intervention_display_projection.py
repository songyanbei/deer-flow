from src.agents.intervention.display_projection import build_display_projection


def test_meeting_booking_projection_returns_user_readable_display():
    display = build_display_projection(
        "meeting_createMeeting",
        {
            "title": "产品介绍会",
            "personName": "孙琦",
            "startDate": 1773824400000,
            "endDate": 1773828000000,
            "content": "介绍新产品方案",
            "actors": [{"personName": "王敏"}, {"personName": "李雷"}],
            "noticeTimes": [30, 120],
            "roomId": "room_123",
        },
        "meeting-agent",
        timezone="Asia/Shanghai",
    )

    assert display["title"] == "确认预定会议"
    assert display["summary"] == "即将预定会议「产品介绍会」，发起人：孙琦"
    assert display["risk_tip"] == "确认后将创建会议并通知参与人"
    assert display["primary_action_label"] == "确认预定"
    assert display["secondary_action_label"] == "取消"
    assert display["respond_action_label"] == "修改后预定"
    items = display["sections"][0]["items"]
    assert {"label": "会议主题", "value": "产品介绍会"} in items
    # 1773824400000 = 2026-03-18 17:00 CST, 1773828000000 = 2026-03-18 18:00 CST
    assert {"label": "会议时间", "value": "2026-03-18 17:00 ~ 18:00"} in items
    assert {"label": "发起人", "value": "孙琦"} in items
    assert {"label": "参与人", "value": "王敏、李雷"} in items
    assert {"label": "提醒", "value": "30分钟前、2小时前"} in items
    assert all(item["label"] != "roomId" for item in items)
    assert display["debug"]["tool_name"] == "meeting_createMeeting"


def test_operation_projection_hides_internal_ids_and_formats_timestamps():
    display = build_display_projection(
        "send_notification",
        {
            "recipient": "全员群",
            "message": "请按时参会",
            "scheduledTime": 1773824400000,
            "openId": "ou_secret",
        },
        "ops-agent",
        timezone="Asia/Shanghai",
    )

    assert display["title"] == "确认发送操作"
    assert display["summary"] == "将要发送以下内容，请确认："
    items = display["sections"][0]["items"]
    assert {"label": "收件人", "value": "全员群"} in items
    assert {"label": "消息", "value": "请按时参会"} in items
    # 1773824400000 = 2026-03-18 17:00 CST
    assert any(item["label"] == "scheduledTime" and item["value"] == "2026-03-18 17:00" for item in items)
    assert all(item["label"] != "openId" for item in items)


def test_fallback_projection_stays_readable_without_special_adapter():
    display = build_display_projection(
        "sync_workspace",
        {
            "name": "研发知识库",
            "city": "上海",
            "token": "secret-token",
        },
        "sync-agent",
    )

    assert display["title"] == "操作确认"
    assert display["summary"] == "以下操作需要您确认后才能继续执行。"
    assert display["primary_action_label"] == "确认执行"
    assert display["secondary_action_label"] == "取消"
    items = display["sections"][0]["items"]
    assert {"label": "名称", "value": "研发知识库"} in items
    assert {"label": "城市", "value": "上海"} in items
    assert all(item["label"] != "token" for item in items)
    assert display["debug"]["source_agent"] == "sync-agent"
    assert display["debug"]["tool_name"] == "sync_workspace"


def test_unknown_scenario_does_not_require_raw_fields_for_primary_display():
    display = build_display_projection(
        "custom_tool",
        {
            "resourceId": "res_123",
            "status": "pending",
            "amount": 3,
        },
        "custom-agent",
    )

    items = display["sections"][0]["items"]
    assert {"label": "状态", "value": "pending"} in items
    assert {"label": "金额", "value": "3"} in items
    assert all(item["label"] != "resourceId" for item in items)
    assert display["debug"]["raw_args"]["resourceId"] == "res_123"


def test_timezone_converts_epoch_to_correct_local_time():
    """Verify that the same epoch renders differently in different timezones."""
    epoch_ms = 1773824400000  # 2026-03-18 09:00 UTC

    display_utc = build_display_projection(
        "meeting_createMeeting",
        {"title": "会议", "startDate": epoch_ms, "endDate": epoch_ms + 3600000},
        timezone="UTC",
    )
    display_shanghai = build_display_projection(
        "meeting_createMeeting",
        {"title": "会议", "startDate": epoch_ms, "endDate": epoch_ms + 3600000},
        timezone="Asia/Shanghai",
    )
    display_tokyo = build_display_projection(
        "meeting_createMeeting",
        {"title": "会议", "startDate": epoch_ms, "endDate": epoch_ms + 3600000},
        timezone="Asia/Tokyo",
    )

    utc_items = display_utc["sections"][0]["items"]
    shanghai_items = display_shanghai["sections"][0]["items"]
    tokyo_items = display_tokyo["sections"][0]["items"]

    utc_time = next(i["value"] for i in utc_items if i["label"] == "会议时间")
    shanghai_time = next(i["value"] for i in shanghai_items if i["label"] == "会议时间")
    tokyo_time = next(i["value"] for i in tokyo_items if i["label"] == "会议时间")

    assert utc_time == "2026-03-18 09:00 ~ 10:00"
    assert shanghai_time == "2026-03-18 17:00 ~ 18:00"
    assert tokyo_time == "2026-03-18 18:00 ~ 19:00"


def test_env_var_timezone_fallback(monkeypatch):
    """Verify DEER_FLOW_TIMEZONE env var is respected when no explicit timezone."""
    monkeypatch.setenv("DEER_FLOW_TIMEZONE", "Asia/Shanghai")
    epoch_ms = 1773824400000  # 2026-03-18 09:00 UTC

    display = build_display_projection(
        "meeting_createMeeting",
        {"title": "会议", "startDate": epoch_ms, "endDate": epoch_ms + 3600000},
    )

    items = display["sections"][0]["items"]
    time_value = next(i["value"] for i in items if i["label"] == "会议时间")
    assert time_value == "2026-03-18 17:00 ~ 18:00"


def test_cross_day_timezone_rendering():
    """Verify timestamps near midnight render correct date after timezone shift."""
    # 2026-03-18 23:00 UTC = 2026-03-19 07:00 CST (crosses midnight)
    epoch_start = 1773874800000  # 2026-03-18 23:00 UTC
    epoch_end = 1773878400000    # 2026-03-19 00:00 UTC

    display = build_display_projection(
        "meeting_createMeeting",
        {"title": "跨天会议", "startDate": epoch_start, "endDate": epoch_end},
        timezone="Asia/Shanghai",
    )

    items = display["sections"][0]["items"]
    time_value = next(i["value"] for i in items if i["label"] == "会议时间")
    # Both land on 2026-03-19 in CST: 07:00 ~ 08:00
    assert time_value == "2026-03-19 07:00 ~ 08:00"

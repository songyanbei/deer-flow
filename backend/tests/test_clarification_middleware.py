from src.agents.middlewares.clarification_middleware import ClarificationMiddleware


def test_format_clarification_message_keeps_list_options_readable():
    middleware = ClarificationMiddleware()

    message = middleware._format_clarification_message(
        {
            "question": "请选择会议室。",
            "clarification_type": "approach_choice",
            "options": ["济南舜泰广场会议室", "济南42-1会议室"],
        }
    )

    assert "1. 济南舜泰广场会议室" in message
    assert "2. 济南42-1会议室" in message


def test_format_clarification_message_parses_json_string_options():
    middleware = ClarificationMiddleware()

    message = middleware._format_clarification_message(
        {
            "question": "请选择会议室。",
            "clarification_type": "approach_choice",
            "options": '["济南舜泰广场会议室","济南42-1会议室"]',
        }
    )

    assert "1. 济南舜泰广场会议室" in message
    assert "2. 济南42-1会议室" in message
    assert "\n  3. 济\n" not in message


def test_format_clarification_message_treats_plain_string_option_as_single_choice():
    middleware = ClarificationMiddleware()

    message = middleware._format_clarification_message(
        {
            "question": "请选择会议室。",
            "clarification_type": "suggestion",
            "options": "建议优先选择济南42-1会议室",
        }
    )

    assert "1. 建议优先选择济南42-1会议室" in message

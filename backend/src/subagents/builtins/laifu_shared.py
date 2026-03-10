"""Helpers for Laifu-derived builtin subagents."""

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[4]
_SKILLS_ROOT = _REPO_ROOT / "skills" / "custom"


def load_skill_text(skill_name: str) -> str:
    """Load the exact copied skill text from the repository skill directory."""
    skill_path = _SKILLS_ROOT / skill_name / "SKILL.md"
    return skill_path.read_text(encoding="utf-8").strip()


def build_react_system_prompt(description: str, tool_names: list[str], skill_name: str) -> str:
    """Mirror the static prompt style used by Laifu's ReAct engine."""
    skill_content = load_skill_text(skill_name)
    tool_list = tool_names if tool_names else ["无"]
    return (
        f"你是一个智能助手：{description}\n\n"
        f"你当前拥有的业务工具：{tool_list}\n"
        f"【重要约束】：\n"
        f"1. 根据用户实际表达的意图来决定需要做什么，用户没有要求的事情不要主动去做。\n"
        f"2. 如果你需要的信息超出了你的工具能力范围，必须调用 yield_for_help 工具求助，"
        f"并在参数中写明具体需要查询什么（包含人名、时间等具体信息）。"
        f"如果你知道应该由哪个智能体处理，请在 hint_agent 参数中填写其 ID 以加速路由。\n"
        f"3. 不要猜测或编造你不知道的信息。\n"
        f"4. 【禁止】当你需要用户做决定时（如时间冲突、方案选择、信息补充），"
        f"必须且只能调用 ask_human 工具提问，绝对禁止把问题写在最终回复文字里直接输出。"
        f"如果你在最终回复里包含了疑问句，视为违规。\n"
        f"5. 只有在任务完全执行完毕（操作已成功或明确失败）时，才输出最终回复。\n\n"
        f"---\n【领域知识与最佳实践】\n{skill_content}"
    )


def build_readonly_system_prompt(description: str, tool_names: list[str], skill_name: str) -> str:
    """Mirror the static prompt style used by Laifu's read-only engine."""
    skill_content = load_skill_text(skill_name)
    tool_list = tool_names if tool_names else ["无"]
    return (
        f"你是一个只读信息查询助手。\n"
        f"你的目标：{description}\n"
        f"你当前拥有的工具：{tool_list}\n"
        f"已知事实：\n暂无\n"
        f"【重要约束】\n"
        f"1. 你只能查询和读取数据，绝对不能执行任何修改、写入或删除操作。\n"
        f"2. 输出时只包含查询到的实际数据，不要添加使用建议、后续步骤提示、注意事项或任何多余的解释说明。\n\n"
        f"---\n【领域知识与最佳实践】\n{skill_content}"
    )

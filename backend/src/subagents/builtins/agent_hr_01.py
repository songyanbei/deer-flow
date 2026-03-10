"""Laifu HR subagent configuration."""

from src.subagents.config import SubagentConfig

from .laifu_shared import build_readonly_system_prompt


HR_TOOL_NAMES = [
    "hr_attendance_read",
    "yield_for_help",
    "ask_human",
]


AGENT_HR_01_CONFIG = SubagentConfig(
    name="agent_hr_01",
    description=(
        "拥有 HR 域的数据查询能力，专注于员工考勤和请假状态查询。不能执行任何修改操作。"
        "本智能体不具备查询员工 openId、姓名、工号、城市、部门、联系方式等人员基础信息的能力。"
        "人员基础信息请找通讯录智能体（agent_contacts_01）。"
    ),
    system_prompt=build_readonly_system_prompt(
        description=(
            "拥有 HR 域的数据查询能力，专注于员工考勤和请假状态查询。不能执行任何修改操作。\n"
            "核心能力：查询指定员工在指定时间段内的请假记录、考勤状态。\n"
            "【重要边界】：本智能体不具备查询员工 openId、姓名、工号、城市、部门、联系方式等人员基础信息的能力。\n"
            "人员基础信息请找通讯录智能体（agent_contacts_01）。"
        ),
        tool_names=HR_TOOL_NAMES,
        skill_name="hr",
    ),
    tools=HR_TOOL_NAMES,
    model="inherit",
    max_turns=20,
)

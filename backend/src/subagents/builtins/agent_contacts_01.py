"""Laifu contacts subagent configuration."""

from src.subagents.config import SubagentConfig

from .laifu_shared import build_readonly_system_prompt


CONTACTS_TOOL_NAMES = [
    "getUserDetailByCode",
    "getUserByName",
    "searchUsersByName",
    "getDepartmentMembers",
    "getUsersBatchByCodes",
    "getDepartmentInfoByName",
    "yield_for_help",
    "ask_human",
]


AGENT_CONTACTS_01_CONFIG = SubagentConfig(
    name="agent_contacts_01",
    description=(
        "拥有完整的企业员工和组织架构查询能力，数据覆盖约2000名员工。"
        "适用场景：为会议系统提供 openId、查联系方式、查部门归属、查同事信息。"
        "不具备任何写入、修改、创建或删除操作的能力。"
    ),
    system_prompt=build_readonly_system_prompt(
        description=(
            "拥有完整的企业员工和组织架构查询能力，数据覆盖约2000名员工。\n"
            "核心能力：\n"
            "1. 按姓名搜索员工，获取工号、手机、邮箱、openId、部门路径等信息\n"
            "2. 按工号批量查询员工详情（支持字段过滤）\n"
            "3. 查询部门成员列表，支持按城市(Base地)过滤，返回 openId 等信息\n"
            "4. 查询部门基础信息（成员数、层级等）\n"
            "5. 获取全量部门列表（扁平或树状结构）\n"
            "适用场景：为会议系统提供 openId、查联系方式、查部门归属、查同事信息。\n"
            "不具备任何写入、修改、创建或删除操作的能力。"
        ),
        tool_names=CONTACTS_TOOL_NAMES,
        skill_name="contacts",
    ),
    tools=CONTACTS_TOOL_NAMES,
    model="inherit",
    max_turns=20,
)

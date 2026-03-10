"""Built-in subagent configurations."""

from .agent_contacts_01 import AGENT_CONTACTS_01_CONFIG
from .agent_hr_01 import AGENT_HR_01_CONFIG
from .agent_meeting_01 import AGENT_MEETING_01_CONFIG
from .bash_agent import BASH_AGENT_CONFIG
from .general_purpose import GENERAL_PURPOSE_CONFIG

__all__ = [
    "AGENT_MEETING_01_CONFIG",
    "AGENT_CONTACTS_01_CONFIG",
    "AGENT_HR_01_CONFIG",
    "GENERAL_PURPOSE_CONFIG",
    "BASH_AGENT_CONFIG",
]

# Registry of built-in subagents
BUILTIN_SUBAGENTS = {
    "general-purpose": GENERAL_PURPOSE_CONFIG,
    "bash": BASH_AGENT_CONFIG,
    "agent_meeting_01": AGENT_MEETING_01_CONFIG,
    "agent_contacts_01": AGENT_CONTACTS_01_CONFIG,
    "agent_hr_01": AGENT_HR_01_CONFIG,
}

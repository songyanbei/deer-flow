from __future__ import annotations

import asyncio
import json
from pathlib import Path

from src.execution.mcp_pool import _AgentMCPClient


def test_hr_mcp_server_exposes_attendance_lookup():
    async def _run():
        backend_dir = Path(__file__).resolve().parent.parent
        python_exe = backend_dir / ".venv" / "Scripts" / "python.exe"
        server_script = backend_dir / ".deer-flow" / "agents" / "hr-agent" / "hr_mcp_server.py"

        client = _AgentMCPClient(
            "hr-agent-test",
            [
                {
                    "name": "hr-attendance",
                    "command": str(python_exe),
                    "args": [str(server_script)],
                }
            ],
        )

        try:
            assert await client.connect() is True
            tools = await client.get_tools()
            tool = next(tool for tool in tools if tool.name == "hr_attendance_read")

            result = await tool.ainvoke(
                {
                    "employee_name": "李建国",
                    "time_period": "2026-03-13",
                }
            )
            text_payload = result[0]["text"] if isinstance(result, list) else result
            payload = json.loads(text_payload)

            assert payload["employee_name"] == "李建国"
            assert payload["status"] == "未请假"
            assert payload["query_date"] == "2026-03-13"
        finally:
            await client.disconnect()

    asyncio.run(_run())

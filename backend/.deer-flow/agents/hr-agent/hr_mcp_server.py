from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


SERVER_DIR = Path(__file__).resolve().parent
DATA_FILE = SERVER_DIR / "leave_records.json"

DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
MORNING_MARKERS = ("上午", "morning", "am")
AFTERNOON_MARKERS = ("下午", "afternoon", "pm")
FULL_DAY_MARKERS = ("全天", "all day", "整天")


def _load_records() -> dict[str, Any]:
    with DATA_FILE.open(encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict):
        raise ValueError("leave_records.json must contain a JSON object")
    return payload


def _resolve_date(raw_time_period: str) -> str:
    match = DATE_PATTERN.search(raw_time_period)
    if match:
        return match.group(1)

    today = date.today()
    if "后天" in raw_time_period:
        return (today + timedelta(days=2)).isoformat()
    if "明天" in raw_time_period:
        return (today + timedelta(days=1)).isoformat()
    if "今天" in raw_time_period:
        return today.isoformat()

    return today.isoformat()


def _resolve_period(raw_time_period: str) -> str:
    lowered = raw_time_period.lower()
    if any(marker in raw_time_period or marker in lowered for marker in MORNING_MARKERS):
        return "上午"
    if any(marker in raw_time_period or marker in lowered for marker in AFTERNOON_MARKERS):
        return "下午"
    if any(marker in raw_time_period or marker in lowered for marker in FULL_DAY_MARKERS):
        return "全天"
    return "全天"


def _find_matching_records(employee_name: str, query_date: str, query_period: str) -> list[dict[str, Any]]:
    payload = _load_records()
    records = payload.get("records") or []
    if not isinstance(records, list):
        return []

    matches: list[dict[str, Any]] = []
    for entry in records:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("employee_name", "")).strip() != employee_name:
            continue
        if str(entry.get("date", "")).strip() != query_date:
            continue

        entry_period = str(entry.get("period", "全天")).strip() or "全天"
        if query_period != "全天" and entry_period not in ("全天", query_period):
            continue
        matches.append(entry)
    return matches


def _build_response(employee_name: str, raw_time_period: str) -> str:
    query_date = _resolve_date(raw_time_period)
    query_period = _resolve_period(raw_time_period)
    matches = _find_matching_records(employee_name, query_date, query_period)

    if matches:
        on_leave = any(str(item.get("status", "")).strip() == "请假" for item in matches)
        normalized_matches = [
            {
                "employee_name": str(item.get("employee_name", "")).strip(),
                "date": str(item.get("date", "")).strip(),
                "period": str(item.get("period", "全天")).strip() or "全天",
                "status": str(item.get("status", "")).strip(),
                "leave_type": str(item.get("leave_type", "")).strip(),
                "detail": str(item.get("detail", "")).strip(),
            }
            for item in matches
        ]
        payload = {
            "employee_name": employee_name,
            "query_date": query_date,
            "query_period": query_period,
            "on_leave": on_leave,
            "status": "请假" if on_leave else "未请假",
            "records": normalized_matches,
        }
        return json.dumps(payload, ensure_ascii=False)

    payload = {
        "employee_name": employee_name,
        "query_date": query_date,
        "query_period": query_period,
        "on_leave": False,
        "status": "未请假",
        "records": [],
        "detail": "未查询到请假记录",
    }
    return json.dumps(payload, ensure_ascii=False)


mcp = FastMCP(
    name="hr-attendance",
    instructions=(
        "Query employee leave and attendance status. "
        "Use hr_attendance_read with employee_name and time_period."
    ),
)


@mcp.tool(
    name="hr_attendance_read",
    description=(
        "Read-only HR attendance lookup. "
        "Input employee_name and time_period, then return leave status as JSON."
    ),
)
def hr_attendance_read(employee_name: str, time_period: str = "全天") -> str:
    employee_name = str(employee_name).strip()
    time_period = str(time_period).strip() or "全天"
    if not employee_name:
        raise ValueError("employee_name is required")
    return _build_response(employee_name, time_period)


if __name__ == "__main__":
    mcp.run(transport="stdio")

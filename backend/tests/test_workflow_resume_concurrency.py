from __future__ import annotations

import asyncio
from unittest.mock import patch

from src.agents.workflow_resume import extract_latest_clarification_answer


def _clarification_task(task_id: str, key: str, label: str, prompt: str) -> dict:
    return {
        "task_id": task_id,
        "description": prompt,
        "status": "RUNNING",
        "run_id": "run-clarification",
        "clarification_prompt": prompt,
        "clarification_request": {
            "title": prompt,
            "description": prompt,
            "questions": [
                {"key": key, "label": label, "kind": "input"},
            ],
        },
        "continuation_mode": "continue_after_clarification",
    }


class TestConcurrentClarificationResume:
    def test_extractor_targets_first_pending_clarification_task(self):
        state = {
            "task_pool": [
                _clarification_task(
                    "task-first",
                    "employee_name",
                    "Employee name",
                    "What is the employee name?",
                ),
                _clarification_task(
                    "task-second",
                    "department",
                    "Department",
                    "What department is involved?",
                ),
            ],
            "messages": [],
        }
        config = {
            "configurable": {
                "workflow_clarification_response": {
                    "answers": {
                        "employee_name": {"text": "Alice"},
                        "department": {"text": "Platform"},
                    }
                }
            }
        }

        result = extract_latest_clarification_answer(state, config)

        assert result == "Employee name Alice"

    def test_router_binds_answer_only_to_first_clarification_task(self):
        async def _run() -> None:
            from src.agents.router.semantic_router import router_node

            state = {
                "task_pool": [
                    _clarification_task(
                        "task-first",
                        "employee_name",
                        "Employee name",
                        "What is the employee name?",
                    ),
                    _clarification_task(
                        "task-second",
                        "department",
                        "Department",
                        "What department is involved?",
                    ),
                ],
                "messages": [],
                "route_count": 0,
            }
            config = {
                "configurable": {
                    "workflow_clarification_response": {
                        "answers": {
                            "employee_name": {"text": "Alice"},
                            "department": {"text": "Platform"},
                        }
                    }
                }
            }

            with patch("src.agents.router.semantic_router.get_stream_writer", return_value=lambda _event: None):
                result = await router_node(state, config)

            assert result["execution_state"] == "ROUTING_DONE"
            assert [task["task_id"] for task in result["task_pool"]] == ["task-first"]

            updated_task = result["task_pool"][0]
            assert updated_task["resolved_inputs"]["clarification_answer"] == "Employee name Alice"
            assert "Department" not in updated_task["resolved_inputs"]["clarification_answer"]

        asyncio.run(_run())

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.monitoring_watchdog_tool import ensure_monitoring_watchdog_schedule, run_check
from src.scheduler import ScheduleManager


class _NoopTaskManager:
    async def submit(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("not used in watchdog schedule tests")


def test_run_check_alerts_on_down_and_recovery(tmp_path: Path) -> None:
    state_path = tmp_path / "watchdog-state.json"

    first = run_check(
        host="127.0.0.1",
        ports=[1],
        timeout_seconds=0.1,
        fail_threshold=1,
        state_path=state_path,
    ).payload
    assert first["status"] == "critical"
    assert first["should_alert"] is True
    assert first["change_type"] == "new_issue"

    second = run_check(
        host="127.0.0.1",
        ports=[1],
        timeout_seconds=0.1,
        fail_threshold=1,
        state_path=state_path,
    ).payload
    assert second["status"] == "critical"
    assert second["should_alert"] is False
    assert second["change_type"] == "still_down"

    state_path.write_text(
        json.dumps(
            {
                "last_state": "up",
                "consecutive_failures": 0,
                "down_since": None,
                "last_summary": "ok",
            }
        )
    )
    third = run_check(
        host="127.0.0.1",
        ports=[1],
        timeout_seconds=0.1,
        fail_threshold=3,
        state_path=state_path,
    ).payload
    assert third["status"] == "warn"
    assert third["should_alert"] is False
    assert third["change_type"] == "degraded"

    state_path.write_text(
        json.dumps(
            {
                "last_state": "down",
                "consecutive_failures": 4,
                "down_since": "2026-03-23T10:00:00+00:00",
                "last_summary": "down",
            }
        )
    )
    recovered = run_check(
        host="127.0.0.1",
        ports=[22],
        timeout_seconds=0.1,
        fail_threshold=3,
        state_path=state_path,
    ).payload
    assert recovered["status"] == "ok"
    assert recovered["should_alert"] is True
    assert recovered["change_type"] == "recovery"


@pytest.mark.asyncio
async def test_ensure_monitoring_watchdog_schedule_is_idempotent(tmp_path: Path) -> None:
    manager = ScheduleManager(_NoopTaskManager(), tmp_path / "schedules.db")
    state_path = tmp_path / "state.json"

    first = await ensure_monitoring_watchdog_schedule(
        manager=manager,
        chat_id=-1001,
        user_id=42,
        message_thread_id=11,
        model="haiku",
        provider_cli="codex",
        resume_arg=None,
        interval_minutes=1,
        host="45.151.30.146",
        ports=[22, 15443],
        timeout_seconds=4.0,
        fail_threshold=3,
        state_path=state_path,
        python_bin="/usr/bin/python3",
    )
    assert first is not None
    first_id, first_created = first
    assert first_created is True

    second = await ensure_monitoring_watchdog_schedule(
        manager=manager,
        chat_id=-1001,
        user_id=42,
        message_thread_id=11,
        model="haiku",
        provider_cli="codex",
        resume_arg=None,
        interval_minutes=1,
        host="45.151.30.146",
        ports=[22, 15443],
        timeout_seconds=4.0,
        fail_threshold=3,
        state_path=state_path,
        python_bin="/usr/bin/python3",
    )
    assert second is not None
    second_id, second_created = second
    assert second_created is False
    assert second_id == first_id

    schedules = await manager.list_for_chat(-1001, 11)
    assert len(schedules) == 1
    assert "-m src.monitoring_watchdog_tool check" in schedules[0].prompt

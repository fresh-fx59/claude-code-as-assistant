import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.scheduler import ScheduleManager


class _StubTaskManager:
    def __init__(self) -> None:
        self.submissions: list[dict] = []

    async def submit(self, **kwargs):  # noqa: ANN003
        self.submissions.append(kwargs)
        return "task-id"


@pytest.mark.asyncio
async def test_create_and_list_schedule(tmp_path) -> None:
    manager = ScheduleManager(_StubTaskManager(), tmp_path / "schedules.db")

    sid = await manager.create_every(
        chat_id=1,
        user_id=2,
        prompt="do work",
        interval_minutes=5,
        model="sonnet",
        session_id=None,
    )

    items = await manager.list_for_chat(1)
    assert len(items) == 1
    assert items[0].id == sid
    assert items[0].schedule_type == "interval"
    assert items[0].interval_minutes == 5


@pytest.mark.asyncio
async def test_cancel_schedule(tmp_path) -> None:
    manager = ScheduleManager(_StubTaskManager(), tmp_path / "schedules.db")
    sid = await manager.create_every(
        chat_id=1,
        user_id=2,
        prompt="do work",
        interval_minutes=5,
        model="sonnet",
    )

    cancelled = await manager.cancel(sid)
    assert cancelled is True
    items = await manager.list_for_chat(1)
    assert not items


@pytest.mark.asyncio
async def test_create_daily_schedule(tmp_path) -> None:
    manager = ScheduleManager(_StubTaskManager(), tmp_path / "schedules.db")

    sid = await manager.create_daily(
        chat_id=1,
        user_id=2,
        prompt="daily report",
        daily_time="09:30",
        timezone_name="UTC",
        model="sonnet",
    )

    items = await manager.list_for_chat(1)
    assert len(items) == 1
    assert items[0].id == sid
    assert items[0].schedule_type == "daily"
    assert items[0].daily_time == "09:30"
    assert items[0].timezone_name == "UTC"


@pytest.mark.asyncio
async def test_due_schedule_submits_background_task(tmp_path) -> None:
    stub = _StubTaskManager()
    manager = ScheduleManager(stub, tmp_path / "schedules.db")
    sid = await manager.create_every(
        chat_id=10,
        user_id=20,
        prompt="scheduled prompt",
        interval_minutes=1,
        model="opus",
        session_id="sess-1",
    )

    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await asyncio.to_thread(manager._update_next_run, sid, past)  # noqa: SLF001

    await manager._run_due_once()  # noqa: SLF001
    assert len(stub.submissions) == 1
    assert stub.submissions[0]["chat_id"] == 10
    assert stub.submissions[0]["model"] == "opus"
    runs = await manager.list_runs_for_chat(10)
    assert len(runs) == 1
    assert runs[0].status == "submitted"
    assert runs[0].background_task_id == "task-id"


@pytest.mark.asyncio
async def test_due_daily_schedule_submits_and_rolls_next_run(tmp_path) -> None:
    stub = _StubTaskManager()
    manager = ScheduleManager(stub, tmp_path / "schedules.db")
    sid = await manager.create_daily(
        chat_id=10,
        user_id=20,
        prompt="daily prompt",
        daily_time="00:00",
        timezone_name="UTC",
        model="haiku",
        session_id="sess-2",
    )
    past = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    await asyncio.to_thread(manager._update_next_run, sid, past)  # noqa: SLF001

    before = (await manager.list_for_chat(10))[0].next_run_at
    await manager._run_due_once()  # noqa: SLF001
    after = (await manager.list_for_chat(10))[0].next_run_at

    assert len(stub.submissions) == 1
    assert stub.submissions[0]["model"] == "haiku"
    assert after > before


@pytest.mark.asyncio
async def test_schedule_run_updated_when_background_task_finishes(tmp_path) -> None:
    stub = _StubTaskManager()
    manager = ScheduleManager(stub, tmp_path / "schedules.db")
    sid = await manager.create_every(
        chat_id=42,
        user_id=7,
        prompt="scheduled prompt",
        interval_minutes=1,
        model="opus",
    )
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    await asyncio.to_thread(manager._update_next_run, sid, past)  # noqa: SLF001

    await manager._run_due_once()  # noqa: SLF001
    run = (await manager.list_runs_for_chat(42))[0]

    finished_task = type(
        "FinishedTask",
        (),
        {
            "id": "task-id",
            "status": type("TaskStatusValue", (), {"value": "completed"})(),
            "started_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
            "error": None,
            "response": "report delivered",
        },
    )()
    await manager.on_task_finished(finished_task)

    updated_run = (await manager.list_runs_for_chat(42, schedule_id=sid))[0]
    assert updated_run.id == run.id
    assert updated_run.status == "completed"
    assert updated_run.response_preview == "report delivered"


@pytest.mark.asyncio
async def test_create_weekly_schedule(tmp_path) -> None:
    manager = ScheduleManager(_StubTaskManager(), tmp_path / "schedules.db")

    sid = await manager.create_weekly(
        chat_id=1,
        user_id=2,
        prompt="weekly report",
        weekly_day=0,
        daily_time="09:30",
        timezone_name="UTC",
        model="sonnet",
    )

    items = await manager.list_for_chat(1)
    assert len(items) == 1
    assert items[0].id == sid
    assert items[0].schedule_type == "weekly"
    assert items[0].weekly_day == 0
    assert items[0].daily_time == "09:30"

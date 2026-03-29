from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.scheduler import ScheduleManager
from src.topic_proactive_tool import ensure_proactive_topic_schedule, run_check


class _NoopTaskManager:
    async def submit(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("not used in proactive topic schedule tests")


def test_run_check_classifies_actionable_and_long_running(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    state_path = memory_dir / "topic_proactive_state.json"
    sessions_path = tmp_path / "sessions.json"

    (memory_dir / "topic_state_store.json").write_text(
        json.dumps(
            {
                "-100:11": {
                    "scope_key": "-100:11",
                    "topic_version": 3,
                    "updated_at": "2026-03-28T08:00:00+00:00",
                    "events": [
                        {
                            "version": 3,
                            "provider_name": "codex",
                            "summary": "Need to implement Gmail setup links and fix onboarding steps.",
                            "decisions": [],
                            "open_tasks": ["Implement direct setup links in onboarding UI"],
                            "artifacts": [],
                            "updated_at": "2026-03-28T08:00:00+00:00",
                        }
                    ],
                },
                "-100:12": {
                    "scope_key": "-100:12",
                    "topic_version": 5,
                    "updated_at": "2026-03-28T07:50:00+00:00",
                    "events": [
                        {
                            "version": 5,
                            "provider_name": "codex",
                            "summary": "Ongoing self-improvement loop monitoring task.",
                            "decisions": [],
                            "open_tasks": ["Continue monitoring self-improvement loop health"],
                            "artifacts": [],
                            "updated_at": "2026-03-28T07:50:00+00:00",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    sessions_path.write_text(
        json.dumps(
            {
                "-100:11": {"topic_label": "Gmail onboarding"},
                "-100:12": {"topic_label": "Self-improvement loop"},
            }
        ),
        encoding="utf-8",
    )

    first = run_check(
        memory_dir=memory_dir,
        state_path=state_path,
        sessions_path=sessions_path,
        max_topics=3,
        cooldown_hours=6,
    )
    assert first["should_alert"] is True
    assert first["status"] == "warn"
    scheduled_now = first["payload"]["scheduled_now"]
    assert len(scheduled_now) == 1
    assert scheduled_now[0]["scope_key"] == "-100:11"
    long_running = first["payload"]["long_running_topics"]
    assert len(long_running) == 1
    assert long_running[0]["scope_key"] == "-100:12"

    second = run_check(
        memory_dir=memory_dir,
        state_path=state_path,
        sessions_path=sessions_path,
        max_topics=3,
        cooldown_hours=6,
    )
    assert second["should_alert"] is False
    assert len(second["payload"]["suppressed_due_cooldown"]) == 1
    assert second["payload"]["suppressed_due_cooldown"][0]["scope_key"] == "-100:11"


@pytest.mark.asyncio
async def test_ensure_proactive_topic_schedule_is_idempotent(tmp_path: Path) -> None:
    manager = ScheduleManager(_NoopTaskManager(), tmp_path / "schedules.db")
    memory_dir = tmp_path / "memory"
    state_path = memory_dir / "topic_proactive_state.json"
    sessions_path = tmp_path / "sessions.json"

    first = await ensure_proactive_topic_schedule(
        manager=manager,
        chat_id=-1001,
        user_id=42,
        message_thread_id=11,
        model="haiku",
        provider_cli="codex",
        resume_arg=None,
        interval_minutes=60,
        memory_dir=memory_dir,
        state_path=state_path,
        sessions_path=sessions_path,
        max_topics=5,
        cooldown_hours=6.0,
        python_bin="/usr/bin/python3",
    )
    assert first is not None
    first_id, first_created = first
    assert first_created is True

    second = await ensure_proactive_topic_schedule(
        manager=manager,
        chat_id=-1001,
        user_id=42,
        message_thread_id=11,
        model="haiku",
        provider_cli="codex",
        resume_arg=None,
        interval_minutes=60,
        memory_dir=memory_dir,
        state_path=state_path,
        sessions_path=sessions_path,
        max_topics=5,
        cooldown_hours=6.0,
        python_bin="/usr/bin/python3",
    )
    assert second is not None
    second_id, second_created = second
    assert second_created is False
    assert second_id == first_id

    schedules = await manager.list_for_chat(-1001, 11)
    assert len(schedules) == 1
    assert "-m src.topic_proactive_tool check" in schedules[0].prompt

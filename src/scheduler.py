"""Persistent recurring task scheduler."""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .tasks import TaskManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScheduledTask:
    id: str
    chat_id: int
    user_id: int
    prompt: str
    interval_minutes: int
    model: str
    session_id: str | None
    next_run_at: datetime
    created_at: datetime


class ScheduleManager:
    """Recurring task scheduler with SQLite persistence."""

    _POLL_SECONDS = 5

    def __init__(self, task_manager: TaskManager, db_path: Path) -> None:
        self._task_manager = task_manager
        self._db_path = db_path
        self._worker_task: asyncio.Task | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS scheduled_tasks (
                    id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    prompt TEXT NOT NULL,
                    interval_minutes INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    session_id TEXT,
                    next_run_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    async def start(self) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop(), name="schedule_worker")

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def create_every(
        self,
        chat_id: int,
        user_id: int,
        prompt: str,
        interval_minutes: int,
        model: str,
        session_id: str | None = None,
    ) -> str:
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        next_run = now + timedelta(minutes=interval_minutes)
        await asyncio.to_thread(
            self._insert_schedule,
            task_id,
            chat_id,
            user_id,
            prompt,
            interval_minutes,
            model,
            session_id,
            next_run.isoformat(),
            now.isoformat(),
        )
        return task_id

    def _insert_schedule(
        self,
        task_id: str,
        chat_id: int,
        user_id: int,
        prompt: str,
        interval_minutes: int,
        model: str,
        session_id: str | None,
        next_run_at: str,
        created_at: str,
    ) -> None:
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO scheduled_tasks
                (id, chat_id, user_id, prompt, interval_minutes, model, session_id, next_run_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    chat_id,
                    user_id,
                    prompt,
                    interval_minutes,
                    model,
                    session_id,
                    next_run_at,
                    created_at,
                ),
            )

    async def list_for_chat(self, chat_id: int) -> list[ScheduledTask]:
        rows = await asyncio.to_thread(self._list_rows, chat_id)
        return [self._row_to_scheduled_task(row) for row in rows]

    def _list_rows(self, chat_id: int) -> list[sqlite3.Row]:
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT id, chat_id, user_id, prompt, interval_minutes, model, session_id, next_run_at, created_at
                FROM scheduled_tasks
                WHERE chat_id = ?
                ORDER BY next_run_at ASC
                """,
                (chat_id,),
            )
            return list(cur.fetchall())

    async def cancel(self, task_id: str) -> bool:
        deleted = await asyncio.to_thread(self._delete_schedule, task_id)
        return deleted > 0

    def _delete_schedule(self, task_id: str) -> int:
        with self._connect() as con:
            cur = con.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            return cur.rowcount

    async def _worker_loop(self) -> None:
        while True:
            await self._run_due_once()
            await asyncio.sleep(self._POLL_SECONDS)

    async def _run_due_once(self) -> None:
        due_rows = await asyncio.to_thread(self._fetch_due_rows, datetime.now(timezone.utc).isoformat())
        for row in due_rows:
            schedule = self._row_to_scheduled_task(row)
            try:
                await self._task_manager.submit(
                    chat_id=schedule.chat_id,
                    user_id=schedule.user_id,
                    prompt=schedule.prompt,
                    model=schedule.model,
                    session_id=schedule.session_id,
                )
            except Exception:
                logger.exception("Failed to submit scheduled task %s", schedule.id)
            finally:
                next_run = datetime.now(timezone.utc) + timedelta(minutes=schedule.interval_minutes)
                await asyncio.to_thread(self._update_next_run, schedule.id, next_run.isoformat())

    def _fetch_due_rows(self, now_iso: str) -> list[sqlite3.Row]:
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT id, chat_id, user_id, prompt, interval_minutes, model, session_id, next_run_at, created_at
                FROM scheduled_tasks
                WHERE next_run_at <= ?
                ORDER BY next_run_at ASC
                LIMIT 20
                """,
                (now_iso,),
            )
            return list(cur.fetchall())

    def _update_next_run(self, task_id: str, next_run_at: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
                (next_run_at, task_id),
            )

    @staticmethod
    def _row_to_scheduled_task(row: sqlite3.Row) -> ScheduledTask:
        return ScheduledTask(
            id=row["id"],
            chat_id=row["chat_id"],
            user_id=row["user_id"],
            prompt=row["prompt"],
            interval_minutes=row["interval_minutes"],
            model=row["model"],
            session_id=row["session_id"],
            next_run_at=datetime.fromisoformat(row["next_run_at"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

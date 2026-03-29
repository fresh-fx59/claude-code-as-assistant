"""PAC1 benchmark watchdog native schedule helper."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from . import config
    from .scheduler import ScheduleManager
except ImportError:  # pragma: no cover - direct script execution fallback
    import importlib

    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    config = importlib.import_module("src.config")
    ScheduleManager = importlib.import_module("src.scheduler").ScheduleManager

_CHECK_COMMAND_MARKER = "-m src.pac1_benchmark_watchdog_tool check"
_DEFAULT_STATE_PATH = config.MEMORY_DIR / "pac1_benchmark_watchdog_state.json"
_DEFAULT_OBSIDIAN_NOTE = Path(
    "/home/claude-developer/syncthing/data/syncthing-main/Obsidian/DefaultObsidianVault/Projects/"
    "Iron Lady Assistant/Research/BitGN Competition Documentation/docs/08-PAC1-AutoLoop-Runbook.md"
)
_DEFAULT_BITGN_ROOT = Path("/home/claude-developer/bitgn-contest")


@dataclass(frozen=True)
class Pac1WatchdogResult:
    payload: dict[str, Any]


class _NoopTaskManager:
    async def submit(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("submit() is not used when installing native watchdog schedules")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_shell(cmd: str) -> str:
    completed = subprocess.run(
        ["/bin/bash", "-lc", cmd],
        check=False,
        capture_output=True,
        text=True,
    )
    return (completed.stdout or "").strip()


def _is_benchmark_running() -> tuple[bool, list[str]]:
    completed = subprocess.run(
        ["ps", "-eo", "pid,etime,command"],
        check=False,
        capture_output=True,
        text=True,
    )
    raw = (completed.stdout or "").splitlines()
    lines: list[str] = []
    for line in raw:
        text = line.strip()
        if not text:
            continue
        if "pac1_benchmark_watchdog_tool check" in text:
            continue
        if "run_pac1_after_step1_benchmark.sh" in text or (
            "python -m bitgn_contest_agent.cli run-task --benchmark bitgn/pac1-dev" in text
        ) or (
            "python -m bitgn_contest_agent.cli run-benchmark --benchmark bitgn/pac1-dev" in text
        ):
            lines.append(text)
    return bool(lines), lines


def _latest_progress_timestamp(run_dir: Path) -> datetime | None:
    candidates: list[Path] = []
    summary = run_dir / "summary.json"
    if summary.exists():
        candidates.append(summary)
    tasks_dir = run_dir / "tasks"
    if tasks_dir.exists():
        candidates.extend(sorted(tasks_dir.glob("task_*.json")))
    if not candidates:
        return None
    latest = max(path.stat().st_mtime for path in candidates if path.exists())
    return datetime.fromtimestamp(latest, tz=timezone.utc)


def _send_latest_status_message(
    *,
    token: str,
    chat_id: int,
    message_thread_id: int | None,
    state_path: Path,
    text: str,
) -> dict[str, Any]:
    state = _load_state(state_path)
    previous_message_id = state.get("heartbeat_message_id")

    def _call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{token}/{method}"
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
            return json.loads(resp.read().decode("utf-8"))

    payload_base: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if message_thread_id is not None:
        payload_base["message_thread_id"] = message_thread_id

    send_result = _call("sendMessage", payload_base)
    if bool(send_result.get("ok")):
        msg = send_result.get("result") or {}
        new_message_id = msg.get("message_id")
        if isinstance(previous_message_id, int) and isinstance(new_message_id, int) and previous_message_id != new_message_id:
            try:
                _call("deleteMessage", {"chat_id": chat_id, "message_id": previous_message_id})
            except Exception:
                # Best effort: keep status delivery non-blocking.
                pass
        if isinstance(msg.get("message_id"), int):
            state["heartbeat_message_id"] = int(msg["message_id"])
        state["last_status_text"] = text
        state["last_status_at"] = _iso_utc(_utc_now())
        _save_state(state_path, state)
        return {"mode": "send_latest", "ok": True}
    return {"mode": "none", "ok": False}


def run_check(
    *,
    run_dir: Path,
    stale_seconds: int,
    state_path: Path,
    chat_id: int | None,
    message_thread_id: int | None,
    update_topic_message: bool,
) -> Pac1WatchdogResult:
    now = _utc_now()
    running, process_lines = _is_benchmark_running()
    latest_progress = _latest_progress_timestamp(run_dir)
    progress_age_seconds = None
    if latest_progress is not None:
        progress_age_seconds = int((now - latest_progress).total_seconds())

    if running:
        if progress_age_seconds is not None and progress_age_seconds > stale_seconds:
            status = "critical"
            should_alert = True
            change_type = "new_issue"
            summary = (
                f"PAC1 benchmark appears stuck: running process exists, but no progress updates for "
                f"{progress_age_seconds}s (> {stale_seconds}s)."
            )
        else:
            status = "ok"
            should_alert = False
            change_type = "steady_ok"
            summary = "PAC1 benchmark is running and progress is healthy."
    else:
        status = "critical"
        should_alert = True
        change_type = "recovery"
        summary = "PAC1 benchmark is not running (completed or stopped)."

    message_result: dict[str, Any] | None = None
    if status == "ok" and update_topic_message and chat_id is not None and config.BOT_TOKEN:
        heartbeat = f"{now.strftime('%Y-%m-%d %H:%M:%SZ')} | pac1-check | running_not_stuck"
        try:
            message_result = _send_latest_status_message(
                token=config.BOT_TOKEN,
                chat_id=chat_id,
                message_thread_id=message_thread_id,
                state_path=state_path,
                text=heartbeat,
            )
        except Exception as exc:  # noqa: BLE001
            message_result = {"ok": False, "error": str(exc)}

    payload = {
        "status": status,
        "should_alert": should_alert,
        "change_type": change_type,
        "summary": summary,
        "payload": {
            "checked_at": _iso_utc(now),
            "run_dir": str(run_dir),
            "running": running,
            "latest_progress_at": _iso_utc(latest_progress) if latest_progress else None,
            "progress_age_seconds": progress_age_seconds,
            "stale_seconds": stale_seconds,
            "process_lines": process_lines[:20],
            "topic_message": message_result,
            "state_path": str(state_path),
        },
    }
    return Pac1WatchdogResult(payload=payload)


def _build_prompt(
    *,
    python_bin: str,
    run_dir: Path,
    stale_seconds: int,
    state_path: Path,
    chat_id: int,
    message_thread_id: int | None,
) -> str:
    thread_part = f" --message-thread-id {message_thread_id}" if message_thread_id is not None else ""
    quoted_python = shlex_quote(str(python_bin))
    quoted_run_dir = shlex_quote(str(run_dir))
    quoted_state_path = shlex_quote(str(state_path))
    quoted_schedules_db = shlex_quote(str(config.MEMORY_DIR / "schedules.db"))
    quoted_bitgn_root = shlex_quote(str(_DEFAULT_BITGN_ROOT))
    quoted_obsidian_note = shlex_quote(str(_DEFAULT_OBSIDIAN_NOTE))
    escalation = (
        "If status is critical (benchmark stuck or finished), ensure auto-recovery actions are documented.\n"
        "Summarize root cause from logs, the fix applied, and next checks.\n"
        "Prioritize successful completion of all PAC1 tasks (agentic, no hardcoding)."
    )
    return (
        "[[SCHEDULE_NATIVE]]\n"
        f"command: {quoted_python} -m src.pac1_benchmark_watchdog_tool check --run-dir {quoted_run_dir} "
        f"--stale-seconds {stale_seconds} --state-path {quoted_state_path} --chat-id {chat_id}{thread_part} --update-topic-message true\n"
        "auto_remediate: true\n"
        f"remediate_command: {quoted_python} -m src.pac1_benchmark_watchdog_tool recover "
        f"--run-dir {quoted_run_dir} --state-path {quoted_state_path} --chat-id {chat_id}{thread_part} "
        f"--stale-seconds {stale_seconds} --schedules-db {quoted_schedules_db} "
        f"--bitgn-root {quoted_bitgn_root} --obsidian-note {quoted_obsidian_note}\n"
        f"{escalation}\n"
    )


def _send_topic_message(
    *,
    token: str,
    chat_id: int,
    message_thread_id: int | None,
    text: str,
) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:  # nosec B310
        return json.loads(resp.read().decode("utf-8"))


def _append_obsidian_note(note_path: Path, text: str) -> None:
    note_path.parent.mkdir(parents=True, exist_ok=True)
    if not note_path.exists():
        note_path.write_text("# PAC1 Auto Loop Runbook\n\n", encoding="utf-8")
    with note_path.open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip() + "\n\n")


def _detect_stop_reason(run_dir: Path) -> str:
    tasks_dir = run_dir / "tasks"
    latest_task_json = None
    if tasks_dir.exists():
        candidates = sorted(tasks_dir.glob("task_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            latest_task_json = candidates[0]
    if latest_task_json and latest_task_json.exists():
        try:
            if latest_task_json.stat().st_size == 0:
                return "last task json is empty (interrupted or hung before result write)"
            payload = json.loads(latest_task_json.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("error"):
                return f"task error marker: {payload.get('error')}"
            if isinstance(payload, dict):
                return f"last completed task={payload.get('task_index')} score={payload.get('score')}"
        except Exception as exc:
            return f"failed to parse last task json: {exc}"
    events_log = run_dir / "run_events.jsonl"
    if events_log.exists():
        lines = [line for line in events_log.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines:
            try:
                evt = json.loads(lines[-1])
                return f"last event: {evt.get('event')} details={evt.get('details')}"
            except Exception:
                return "events log exists but last event is unreadable"
    return "no run artifacts found for stop reason"


def _benchmark_running_now() -> bool:
    running, _ = _is_benchmark_running()
    return running


def _kill_benchmark_processes() -> int:
    patterns = [
        "scripts/run_pac1_after_step1_benchmark.sh",
        "python -m bitgn_contest_agent.cli run-task --benchmark bitgn/pac1-dev",
    ]
    killed = 0
    for pattern in patterns:
        completed = subprocess.run(
            ["/bin/bash", "-lc", f"pkill -f {shlex_quote(pattern)}"],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            killed += 1
    return killed


def _start_benchmark(run_dir: Path, bitgn_root: Path) -> tuple[bool, str]:
    if _benchmark_running_now():
        return False, "benchmark already running"
    cmd = (
        "CODEX_BIN=codex MAX_STEPS=48 TASK_TIMEOUT_SEC=3600 TASK_RETRIES=1 RUN_TAG=after_step1 "
        f"RUN_DIR={shlex_quote(str(run_dir))} scripts/run_pac1_after_step1_benchmark.sh"
    )
    log_file = run_dir / "watchdog_recover.log"
    with log_file.open("ab") as fh:
        subprocess.Popen(
            ["/bin/bash", "-lc", cmd],
            cwd=str(bitgn_root),
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=dict(os.environ),
        )
    return True, f"restart command submitted (log={log_file})"


def shlex_quote(value: str) -> str:
    import shlex
    return shlex.quote(value)


def _summary_status(run_dir: Path) -> dict[str, Any]:
    expected = None
    task_indices = run_dir / "task_indices.txt"
    if task_indices.exists():
        expected = len([line for line in task_indices.read_text(encoding="utf-8").splitlines() if line.strip()])

    summary_path = run_dir / "summary.json"
    summary: list[Any] = []
    if summary_path.exists():
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                summary = payload
        except Exception:
            summary = []

    total = len(summary)
    all_pass = total > 0 and all(isinstance(item, dict) and float(item.get("score", 0.0)) >= 1.0 for item in summary)
    complete = expected is not None and total >= expected
    return {
        "expected_tasks": expected,
        "summary_items": total,
        "complete": complete,
        "all_pass": all_pass,
    }


def _find_existing_schedule(schedules: list[Any], run_dir: Path) -> str | None:
    marker = f"--run-dir {run_dir}"
    marker_quoted = f"--run-dir {shlex_quote(str(run_dir))}"
    for schedule in schedules:
        prompt = str(getattr(schedule, "prompt", "") or "")
        if _CHECK_COMMAND_MARKER in prompt and (marker in prompt or marker_quoted in prompt):
            return str(getattr(schedule, "id"))
    return None


def _is_watchdog_schedule(schedule: Any) -> bool:
    prompt = str(getattr(schedule, "prompt", "") or "")
    return _CHECK_COMMAND_MARKER in prompt


async def ensure_schedule(
    *,
    manager: ScheduleManager,
    chat_id: int | None,
    user_id: int | None,
    message_thread_id: int | None,
    model: str,
    provider_cli: str,
    resume_arg: str | None,
    interval_minutes: int,
    run_dir: Path,
    stale_seconds: int,
    state_path: Path,
    python_bin: str,
) -> tuple[str, bool] | None:
    if chat_id is None or user_id is None:
        return None
    schedules = await manager.list_for_chat(chat_id, message_thread_id)
    existing = _find_existing_schedule(schedules, run_dir)
    # Keep exactly one active PAC1 watchdog per topic/run context.
    for schedule in schedules:
        schedule_id = str(getattr(schedule, "id"))
        if not _is_watchdog_schedule(schedule):
            continue
        if existing is not None and schedule_id == existing:
            continue
        await manager.cancel(schedule_id)
    if existing:
        return existing, False
    prompt = _build_prompt(
        python_bin=python_bin,
        run_dir=run_dir,
        stale_seconds=stale_seconds,
        state_path=state_path,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
    )
    schedule_id = await manager.create_every(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        user_id=user_id,
        prompt=prompt,
        interval_minutes=interval_minutes,
        model=model,
        session_id=None,
        provider_cli=provider_cli,
        resume_arg=resume_arg,
    )
    return schedule_id, True


def _deactivate_matching_schedule(
    *,
    schedules_db: Path,
    chat_id: int,
    message_thread_id: int | None,
    run_dir: Path,
) -> dict[str, Any]:
    import sqlite3

    marker = f"--run-dir {run_dir}"
    marker_quoted = f"--run-dir {shlex_quote(str(run_dir))}"
    with sqlite3.connect(schedules_db) as con:
        con.row_factory = sqlite3.Row
        if message_thread_id is None:
            row = con.execute(
                """
                SELECT id FROM scheduled_tasks
                WHERE chat_id = ? AND message_thread_id IS NULL AND state = 'active'
                  AND instr(prompt, ?) > 0 AND (instr(prompt, ?) > 0 OR instr(prompt, ?) > 0)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (chat_id, _CHECK_COMMAND_MARKER, marker, marker_quoted),
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT id FROM scheduled_tasks
                WHERE chat_id = ? AND message_thread_id = ? AND state = 'active'
                  AND instr(prompt, ?) > 0 AND (instr(prompt, ?) > 0 OR instr(prompt, ?) > 0)
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (chat_id, message_thread_id, _CHECK_COMMAND_MARKER, marker, marker_quoted),
            ).fetchone()
        if row is None:
            return {"status": "noop", "reason": "schedule_not_found"}
        schedule_id = str(row["id"])
        con.execute(
            """
            UPDATE scheduled_tasks
            SET state = 'cancelled',
                current_run_id = NULL,
                current_background_task_id = NULL,
                current_planned_for = NULL,
                current_submitted_at = NULL,
                current_started_at = NULL,
                current_status = NULL
            WHERE id = ?
            """,
            (schedule_id,),
        )
        con.commit()
        return {"status": "ok", "schedule_id": schedule_id}


def _cmd_check(args: argparse.Namespace) -> int:
    result = run_check(
        run_dir=Path(args.run_dir),
        stale_seconds=int(args.stale_seconds),
        state_path=Path(args.state_path),
        chat_id=args.chat_id,
        message_thread_id=args.message_thread_id,
        update_topic_message=str(args.update_topic_message).strip().lower() in {"1", "true", "yes", "on"},
    )
    print(json.dumps(result.payload, ensure_ascii=False))
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    manager = ScheduleManager(_NoopTaskManager(), Path(args.schedules_db))

    async def _install() -> dict[str, Any]:
        ensured = await ensure_schedule(
            manager=manager,
            chat_id=args.chat_id,
            user_id=args.user_id,
            message_thread_id=args.message_thread_id,
            model=args.model,
            provider_cli=args.provider_cli,
            resume_arg=args.resume_arg,
            interval_minutes=args.interval_minutes,
            run_dir=Path(args.run_dir),
            stale_seconds=args.stale_seconds,
            state_path=Path(args.state_path),
            python_bin=args.python_bin,
        )
        if ensured is None:
            return {"status": "skipped", "reason": "chat_id_or_user_id_missing"}
        schedule_id, created = ensured
        return {"status": "ok", "schedule_id": schedule_id, "created": created}

    print(json.dumps(asyncio.run(_install()), ensure_ascii=False))
    return 0


def _cmd_deactivate(args: argparse.Namespace) -> int:
    result = _deactivate_matching_schedule(
        schedules_db=Path(args.schedules_db),
        chat_id=args.chat_id,
        message_thread_id=args.message_thread_id,
        run_dir=Path(args.run_dir),
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def _cmd_recover(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    state_path = Path(args.state_path)
    note_path = Path(args.obsidian_note)
    bitgn_root = Path(args.bitgn_root)
    now = _utc_now()

    reason = _detect_stop_reason(run_dir)
    summary_status = _summary_status(run_dir)

    running, _ = _is_benchmark_running()
    latest_progress = _latest_progress_timestamp(run_dir)
    progress_age_seconds = int((now - latest_progress).total_seconds()) if latest_progress else None
    killed = 0
    if running and progress_age_seconds is not None and progress_age_seconds > int(args.stale_seconds):
        killed = _kill_benchmark_processes()
        running = _benchmark_running_now()
        if running:
            reason = f"{reason}; stale runner not terminated"
        else:
            reason = f"{reason}; stale runner terminated (pkill groups={killed})"

    restarted = False
    restart_msg = "no action"
    if summary_status["complete"]:
        # Terminal condition for this run directory: stop watchdog schedule to avoid repeated critical noise.
        if args.chat_id is not None:
            deactivated = _deactivate_matching_schedule(
                schedules_db=Path(args.schedules_db),
                chat_id=args.chat_id,
                message_thread_id=args.message_thread_id,
                run_dir=run_dir,
            )
            if summary_status["all_pass"]:
                restart_msg = f"all tasks successful; schedule deactivated: {deactivated}"
            else:
                restart_msg = (
                    "run complete with failures; schedule deactivated for analysis/next-step planning: "
                    f"{deactivated}"
                )
        else:
            if summary_status["all_pass"]:
                restart_msg = "all tasks successful; schedule deactivation skipped (missing chat_id)"
            else:
                restart_msg = "run complete with failures; schedule deactivation skipped (missing chat_id)"
    else:
        restarted, restart_msg = _start_benchmark(run_dir, bitgn_root)

    summary = (
        f"[AUTO-RECOVER] {now.strftime('%Y-%m-%d %H:%M:%SZ')} | "
        f"reason: {reason} | action: {restart_msg} | summary_items={summary_status['summary_items']}"
    )
    _append_obsidian_note(
        note_path,
        (
            f"## {now.strftime('%Y-%m-%d %H:%M:%SZ')}\n"
            f"- reason: {reason}\n"
            f"- action: {restart_msg}\n"
            f"- summary_status: {json.dumps(summary_status, ensure_ascii=False)}\n"
            f"- run_dir: {run_dir}\n"
        ),
    )

    state = _load_state(state_path)
    state["last_recover_at"] = _iso_utc(now)
    state["last_recover_reason"] = reason
    state["last_recover_action"] = restart_msg
    _save_state(state_path, state)

    topic_result = None
    if args.chat_id is not None and config.BOT_TOKEN:
        try:
            topic_result = _send_topic_message(
                token=config.BOT_TOKEN,
                chat_id=args.chat_id,
                message_thread_id=args.message_thread_id,
                text=summary,
            )
        except Exception as exc:
            topic_result = {"ok": False, "error": str(exc)}

    print(
        json.dumps(
            {
                "status": "ok",
                "restarted": restarted,
                "reason": reason,
                "action": restart_msg,
                "summary_status": summary_status,
                "obsidian_note": str(note_path),
                "topic_result": topic_result,
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.pac1_benchmark_watchdog_tool")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--run-dir", required=True)
    check.add_argument("--stale-seconds", type=int, default=600)
    check.add_argument("--state-path", default=str(_DEFAULT_STATE_PATH))
    check.add_argument("--chat-id", type=int)
    check.add_argument("--message-thread-id", type=int)
    check.add_argument("--update-topic-message", default="true")
    check.set_defaults(func=_cmd_check)

    install = sub.add_parser("install")
    install.add_argument("--schedules-db", default=str(config.MEMORY_DIR / "schedules.db"))
    install.add_argument("--python-bin", default=sys.executable)
    install.add_argument("--chat-id", type=int, required=True)
    install.add_argument("--user-id", type=int, required=True)
    install.add_argument("--message-thread-id", type=int)
    install.add_argument("--model", default="gpt-5-codex")
    install.add_argument("--provider-cli", default="codex")
    install.add_argument("--resume-arg")
    install.add_argument("--interval-minutes", type=int, default=1)
    install.add_argument("--run-dir", required=True)
    install.add_argument("--stale-seconds", type=int, default=600)
    install.add_argument("--state-path", default=str(_DEFAULT_STATE_PATH))
    install.set_defaults(func=_cmd_install)

    deactivate = sub.add_parser("deactivate")
    deactivate.add_argument("--schedules-db", default=str(config.MEMORY_DIR / "schedules.db"))
    deactivate.add_argument("--chat-id", type=int, required=True)
    deactivate.add_argument("--message-thread-id", type=int)
    deactivate.add_argument("--run-dir", required=True)
    deactivate.set_defaults(func=_cmd_deactivate)

    recover = sub.add_parser("recover")
    recover.add_argument("--run-dir", required=True)
    recover.add_argument("--state-path", default=str(_DEFAULT_STATE_PATH))
    recover.add_argument("--chat-id", type=int)
    recover.add_argument("--message-thread-id", type=int)
    recover.add_argument("--stale-seconds", type=int, default=600)
    recover.add_argument("--schedules-db", default="memory/schedules.db")
    recover.add_argument("--bitgn-root", default=str(_DEFAULT_BITGN_ROOT))
    recover.add_argument("--obsidian-note", default=str(_DEFAULT_OBSIDIAN_NOTE))
    recover.set_defaults(func=_cmd_recover)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

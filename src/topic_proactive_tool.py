"""Proactive topic triage and hourly schedule installer."""

from __future__ import annotations

import argparse
import asyncio
import json
import shlex
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config
from .scheduler import ScheduleManager

_CHECK_COMMAND_MARKER = "-m src.topic_proactive_tool check"
_ACTION_HINTS = (
    "todo",
    "to do",
    "next",
    "need to",
    "should",
    "must",
    "pending",
    "follow up",
    "implement",
    "fix",
    "investigate",
    "schedule",
    "deploy",
)
_LONG_RUNNING_KEYWORDS = (
    "monitoring",
    "maintenance",
    "research",
    "self-improvement",
    "self improvement",
    "continuous",
    "ongoing",
    "loop",
)


@dataclass(frozen=True)
class TopicCandidate:
    scope_key: str
    topic_version: int
    updated_at: str
    summary: str
    next_action: str
    topic_label: str | None
    long_running: bool


class _NoopTaskManager:
    async def submit(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("submit() is not used when installing proactive topic schedules")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        text = raw.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _load_topic_labels(sessions_path: Path) -> dict[str, str]:
    payload = _load_json(sessions_path, {})
    if not isinstance(payload, dict):
        return {}
    labels: dict[str, str] = {}
    for scope_key, row in payload.items():
        if not isinstance(row, dict):
            continue
        label = str(row.get("topic_label") or "").strip()
        if label:
            labels[str(scope_key)] = label
    return labels


def _has_action_signal(summary: str, open_tasks: list[str]) -> bool:
    if open_tasks:
        return True
    normalized = (summary or "").lower()
    return any(hint in normalized for hint in _ACTION_HINTS)


def _is_long_running(topic_label: str | None, summary: str, next_action: str) -> bool:
    merged = " ".join([topic_label or "", summary or "", next_action or ""]).lower()
    return any(item in merged for item in _LONG_RUNNING_KEYWORDS)


def _extract_next_action(summary: str, open_tasks: list[str]) -> str:
    for task in open_tasks:
        text = str(task).strip()
        if text:
            return text
    summary_text = " ".join((summary or "").split())
    if not summary_text:
        return "Review latest topic updates and choose the next concrete action."
    for chunk in summary_text.split("."):
        candidate = chunk.strip()
        if not candidate:
            continue
        lowered = candidate.lower()
        if any(hint in lowered for hint in _ACTION_HINTS):
            return candidate
    return summary_text[:220].rstrip()


def _candidate_from_scope(scope_key: str, state_row: dict[str, Any], topic_label: str | None) -> TopicCandidate | None:
    events = state_row.get("events")
    if not isinstance(events, list) or not events:
        return None
    latest = events[-1] if isinstance(events[-1], dict) else None
    if latest is None:
        return None
    summary = " ".join(str(latest.get("summary") or "").split())
    raw_tasks = latest.get("open_tasks")
    open_tasks = [str(item).strip() for item in (raw_tasks or []) if str(item).strip()] if isinstance(raw_tasks, list) else []
    if not _has_action_signal(summary, open_tasks):
        return None
    next_action = _extract_next_action(summary, open_tasks)
    updated_at = str(latest.get("updated_at") or state_row.get("updated_at") or _now_utc().isoformat())
    return TopicCandidate(
        scope_key=scope_key,
        topic_version=int(state_row.get("topic_version") or 0),
        updated_at=updated_at,
        summary=summary[:280],
        next_action=next_action,
        topic_label=topic_label,
        long_running=_is_long_running(topic_label, summary, next_action),
    )


def _state_scope_row(state_payload: dict[str, Any], scope_key: str) -> dict[str, Any]:
    scopes = state_payload.get("scopes")
    if not isinstance(scopes, dict):
        scopes = {}
        state_payload["scopes"] = scopes
    row = scopes.get(scope_key)
    if not isinstance(row, dict):
        row = {}
        scopes[scope_key] = row
    return row


def _within_cooldown(last_enqueued_at: str | None, cooldown_hours: float) -> bool:
    if cooldown_hours <= 0:
        return False
    last_dt = _parse_iso(last_enqueued_at)
    if last_dt is None:
        return False
    return _now_utc() - last_dt < timedelta(hours=float(cooldown_hours))


def run_check(
    *,
    memory_dir: Path,
    state_path: Path,
    sessions_path: Path,
    max_topics: int,
    cooldown_hours: float,
) -> dict[str, Any]:
    topic_state_path = memory_dir / "topic_state_store.json"
    topic_states = _load_json(topic_state_path, {})
    if not isinstance(topic_states, dict):
        topic_states = {}
    labels = _load_topic_labels(sessions_path)
    state_payload = _load_json(state_path, {})
    if not isinstance(state_payload, dict):
        state_payload = {}

    candidates: list[TopicCandidate] = []
    for scope_key, row in topic_states.items():
        if not isinstance(row, dict):
            continue
        candidate = _candidate_from_scope(str(scope_key), row, labels.get(str(scope_key)))
        if candidate is not None:
            candidates.append(candidate)

    candidates.sort(
        key=lambda item: _parse_iso(item.updated_at) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    actionable = [item for item in candidates if not item.long_running]
    long_running = [item for item in candidates if item.long_running]
    due: list[TopicCandidate] = []
    suppressed: list[TopicCandidate] = []
    has_new_action = False

    for item in actionable:
        row = _state_scope_row(state_payload, item.scope_key)
        last_alerted_version = int(row.get("last_alerted_version") or 0)
        last_enqueued_at = str(row.get("last_enqueued_at") or "")
        version_is_new = int(item.topic_version) > last_alerted_version
        if version_is_new:
            has_new_action = True
        if version_is_new or not _within_cooldown(last_enqueued_at, cooldown_hours):
            due.append(item)
        else:
            suppressed.append(item)

    due = due[: max(1, int(max_topics))]

    should_alert = bool(due)
    if should_alert:
        now_iso = _now_utc().isoformat()
        for item in due:
            row = _state_scope_row(state_payload, item.scope_key)
            row["last_alerted_version"] = int(item.topic_version)
            row["last_enqueued_at"] = now_iso

    previous_pending = int(state_payload.get("last_pending_count") or 0)
    pending_now = len(actionable) + len(long_running)
    state_payload["last_pending_count"] = pending_now
    state_payload["last_run_at"] = _now_utc().isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    change_type = "steady_ok"
    if should_alert:
        change_type = "new_issue" if has_new_action else "still_pending"
    elif previous_pending > 0 and pending_now == 0:
        change_type = "recovery"

    status = "warn" if pending_now > 0 else "ok"
    summary = (
        f"Actionable topics: {len(actionable)}, long-running topics: {len(long_running)}, "
        f"scheduled now: {len(due)}, suppressed by cooldown: {len(suppressed)}."
    )
    payload = {
        "checked_at": _now_utc().isoformat(),
        "topic_state_path": str(topic_state_path),
        "sessions_path": str(sessions_path),
        "actionable_topics_total": len(actionable),
        "long_running_topics_total": len(long_running),
        "scheduled_now": [
            {
                "scope_key": item.scope_key,
                "topic_label": item.topic_label,
                "topic_version": item.topic_version,
                "updated_at": item.updated_at,
                "summary": item.summary,
                "next_action": item.next_action,
            }
            for item in due
        ],
        "long_running_topics": [
            {
                "scope_key": item.scope_key,
                "topic_label": item.topic_label,
                "topic_version": item.topic_version,
                "updated_at": item.updated_at,
                "summary": item.summary,
                "next_action": item.next_action,
            }
            for item in long_running[: max(1, int(max_topics))]
        ],
        "suppressed_due_cooldown": [
            {
                "scope_key": item.scope_key,
                "topic_label": item.topic_label,
                "topic_version": item.topic_version,
                "updated_at": item.updated_at,
            }
            for item in suppressed[: max(1, int(max_topics))]
        ],
    }
    return {
        "status": status,
        "should_alert": should_alert,
        "change_type": change_type,
        "summary": summary,
        "payload": payload,
    }


def _build_schedule_prompt(
    *,
    python_bin: str,
    memory_dir: Path,
    state_path: Path,
    sessions_path: Path,
    max_topics: int,
    cooldown_hours: float,
) -> str:
    check_command = [
        python_bin,
        "-m",
        "src.topic_proactive_tool",
        "check",
        "--memory-dir",
        str(memory_dir),
        "--state-path",
        str(state_path),
        "--sessions-path",
        str(sessions_path),
        "--max-topics",
        str(max_topics),
        "--cooldown-hours",
        str(cooldown_hours),
    ]
    return (
        "[[SCHEDULE_NATIVE]]\n"
        f"command: {shlex.join(check_command)}\n"
        "Classify topics into actionable outcomes vs long-running streams.\n"
        "For actionable topics, propose one next concrete step and execute it immediately.\n"
        "For long-running topics, do not force closure; execute one bounded progress action only.\n"
        "Keep updates concise and execution-first."
    )


def _find_existing_schedule(
    schedules: list[Any],
    *,
    state_path: Path,
) -> str | None:
    marker = f"--state-path {state_path}"
    for schedule in schedules:
        prompt = str(getattr(schedule, "prompt", "") or "")
        if _CHECK_COMMAND_MARKER in prompt and marker in prompt:
            return str(getattr(schedule, "id"))
    return None


async def ensure_proactive_topic_schedule(
    *,
    manager: ScheduleManager,
    chat_id: int | None,
    user_id: int | None,
    message_thread_id: int | None,
    model: str,
    provider_cli: str,
    resume_arg: str | None,
    interval_minutes: int,
    memory_dir: Path,
    state_path: Path,
    sessions_path: Path,
    max_topics: int,
    cooldown_hours: float,
    python_bin: str,
) -> tuple[str, bool] | None:
    if chat_id is None or user_id is None:
        return None
    schedules = await manager.list_for_chat(chat_id, message_thread_id)
    existing = _find_existing_schedule(schedules, state_path=state_path)
    if existing:
        return existing, False
    prompt = _build_schedule_prompt(
        python_bin=python_bin,
        memory_dir=memory_dir,
        state_path=state_path,
        sessions_path=sessions_path,
        max_topics=max_topics,
        cooldown_hours=cooldown_hours,
    )
    schedule_id = await manager.create_every(
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        user_id=user_id,
        prompt=prompt,
        interval_minutes=max(1, int(interval_minutes)),
        model=model,
        session_id=None,
        provider_cli=provider_cli,
        resume_arg=resume_arg,
    )
    return schedule_id, True


def _cmd_check(args: argparse.Namespace) -> int:
    payload = run_check(
        memory_dir=Path(args.memory_dir),
        state_path=Path(args.state_path),
        sessions_path=Path(args.sessions_path),
        max_topics=args.max_topics,
        cooldown_hours=args.cooldown_hours,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    manager = ScheduleManager(_NoopTaskManager(), Path(args.schedules_db))

    async def _install() -> dict[str, Any]:
        ensured = await ensure_proactive_topic_schedule(
            manager=manager,
            chat_id=args.chat_id,
            user_id=args.user_id,
            message_thread_id=args.message_thread_id,
            model=args.model,
            provider_cli=args.provider_cli,
            resume_arg=args.resume_arg,
            interval_minutes=args.interval_minutes,
            memory_dir=Path(args.memory_dir),
            state_path=Path(args.state_path),
            sessions_path=Path(args.sessions_path),
            max_topics=args.max_topics,
            cooldown_hours=args.cooldown_hours,
            python_bin=args.python_bin,
        )
        if ensured is None:
            return {"status": "skipped", "reason": "chat_id_or_user_id_missing"}
        schedule_id, created = ensured
        return {"status": "ok", "schedule_id": schedule_id, "created": created}

    print(json.dumps(asyncio.run(_install()), ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.topic_proactive_tool")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--memory-dir", default=str(config.MEMORY_DIR))
    check.add_argument("--state-path", default=str(config.PROACTIVE_TOPIC_STATE_PATH))
    check.add_argument("--sessions-path", default=str(config.PROACTIVE_TOPIC_SESSIONS_PATH))
    check.add_argument("--max-topics", type=int, default=config.PROACTIVE_TOPIC_MAX_TOPICS)
    check.add_argument("--cooldown-hours", type=float, default=config.PROACTIVE_TOPIC_COOLDOWN_HOURS)
    check.set_defaults(func=_cmd_check)

    install = sub.add_parser("install")
    install.add_argument("--schedules-db", default=str(config.MEMORY_DIR / "schedules.db"))
    install.add_argument("--python-bin", default=sys.executable)
    install.add_argument("--chat-id", type=int, default=config.PROACTIVE_TOPIC_CHAT_ID)
    install.add_argument("--user-id", type=int, default=config.PROACTIVE_TOPIC_USER_ID)
    install.add_argument("--message-thread-id", type=int, default=config.PROACTIVE_TOPIC_THREAD_ID)
    install.add_argument("--model", default=config.PROACTIVE_TOPIC_MODEL)
    install.add_argument("--provider-cli", default=config.PROACTIVE_TOPIC_PROVIDER_CLI)
    install.add_argument("--resume-arg", default=config.PROACTIVE_TOPIC_RESUME_ARG)
    install.add_argument("--interval-minutes", type=int, default=config.PROACTIVE_TOPIC_INTERVAL_MINUTES)
    install.add_argument("--memory-dir", default=str(config.MEMORY_DIR))
    install.add_argument("--state-path", default=str(config.PROACTIVE_TOPIC_STATE_PATH))
    install.add_argument("--sessions-path", default=str(config.PROACTIVE_TOPIC_SESSIONS_PATH))
    install.add_argument("--max-topics", type=int, default=config.PROACTIVE_TOPIC_MAX_TOPICS)
    install.add_argument("--cooldown-hours", type=float, default=config.PROACTIVE_TOPIC_COOLDOWN_HOURS)
    install.set_defaults(func=_cmd_install)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

"""Monitoring server watchdog native schedule helper."""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import config
from .scheduler import ScheduleManager

_CHECK_COMMAND_MARKER = "-m src.monitoring_watchdog_tool check"
_STATE_UP = "up"
_STATE_DOWN = "down"


@dataclass(frozen=True)
class MonitoringWatchdogCheckResult:
    payload: dict[str, Any]


class _NoopTaskManager:
    async def submit(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("submit() is not used when installing monitoring watchdog schedules")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _load_state(state_path: Path) -> dict[str, Any]:
    if not state_path.exists():
        return {
            "last_state": _STATE_UP,
            "consecutive_failures": 0,
            "down_since": None,
            "last_summary": None,
        }
    try:
        data = json.loads(state_path.read_text())
    except Exception:
        return {
            "last_state": _STATE_UP,
            "consecutive_failures": 0,
            "down_since": None,
            "last_summary": None,
        }
    if not isinstance(data, dict):
        return {
            "last_state": _STATE_UP,
            "consecutive_failures": 0,
            "down_since": None,
            "last_summary": None,
        }
    return {
        "last_state": str(data.get("last_state") or _STATE_UP),
        "consecutive_failures": int(data.get("consecutive_failures") or 0),
        "down_since": data.get("down_since"),
        "last_summary": data.get("last_summary"),
    }


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, ensure_ascii=True, indent=2))


def _tcp_check(host: str, port: int, timeout_seconds: float) -> dict[str, Any]:
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            return {"name": f"tcp_{port}", "port": port, "status": "ok", "detail": "reachable"}
    except Exception as exc:
        return {"name": f"tcp_{port}", "port": port, "status": "critical", "detail": str(exc)}


def run_check(
    *,
    host: str,
    ports: list[int],
    timeout_seconds: float,
    fail_threshold: int,
    state_path: Path,
) -> MonitoringWatchdogCheckResult:
    now = _utc_now()
    checks = [_tcp_check(host, port, timeout_seconds) for port in ports]
    failed_checks = [item for item in checks if item["status"] != "ok"]
    failed_ports = [int(item["port"]) for item in failed_checks]

    state = _load_state(state_path)
    last_state = str(state.get("last_state") or _STATE_UP)
    consecutive_failures = int(state.get("consecutive_failures") or 0)
    down_since = state.get("down_since")

    should_alert = False
    change_type = "steady_ok"
    status = "ok"
    summary = f"Monitoring host {host} is reachable on all watched ports ({', '.join(str(p) for p in ports)})."

    if failed_ports:
        consecutive_failures += 1
        status = "warn" if consecutive_failures < fail_threshold else "critical"
        if consecutive_failures >= fail_threshold:
            status = "critical"
            if last_state != _STATE_DOWN:
                should_alert = True
                change_type = "new_issue"
                down_since = _iso_utc(now)
                last_state = _STATE_DOWN
            else:
                change_type = "still_down"
            summary = (
                f"Monitoring host {host} is DOWN for watched ports: {', '.join(str(p) for p in failed_ports)} "
                f"(consecutive_failures={consecutive_failures}, threshold={fail_threshold})."
            )
        else:
            change_type = "degraded"
            summary = (
                f"Monitoring host {host} check failed on ports {', '.join(str(p) for p in failed_ports)} "
                f"(consecutive_failures={consecutive_failures}/{fail_threshold}); waiting for threshold."
            )
    else:
        if last_state == _STATE_DOWN:
            should_alert = True
            change_type = "recovery"
            status = "ok"
            if down_since:
                summary = (
                    f"Monitoring host {host} RECOVERED; watched ports are reachable again. "
                    f"Down since: {down_since}."
                )
            else:
                summary = f"Monitoring host {host} RECOVERED; watched ports are reachable again."
        consecutive_failures = 0
        down_since = None
        last_state = _STATE_UP

    new_state = {
        "last_state": last_state,
        "consecutive_failures": consecutive_failures,
        "down_since": down_since,
        "last_summary": summary,
    }
    _save_state(state_path, new_state)

    payload = {
        "status": status,
        "should_alert": should_alert,
        "change_type": change_type,
        "summary": summary,
        "payload": {
            "host": host,
            "ports": ports,
            "failed_ports": failed_ports,
            "consecutive_failures": consecutive_failures,
            "fail_threshold": fail_threshold,
            "checked_at": _iso_utc(now),
            "down_since": down_since,
            "checks": checks,
            "state_path": str(state_path),
        },
    }
    return MonitoringWatchdogCheckResult(payload=payload)


def _build_watchdog_prompt(
    *,
    python_bin: str,
    host: str,
    ports: list[int],
    timeout_seconds: float,
    fail_threshold: int,
    state_path: Path,
) -> str:
    ports_csv = ",".join(str(port) for port in ports)
    return (
        "[[SCHEDULE_NATIVE]]\n"
        f"command: {python_bin} -m src.monitoring_watchdog_tool check --host {host} "
        f"--ports {ports_csv} --timeout-seconds {timeout_seconds} --fail-threshold {fail_threshold} "
        f"--state-path {state_path}\n"
        "Monitor monitoring-server reachability from Contabo.\n"
        "Alert only on state transitions (DOWN after threshold, and RECOVERED).\n"
        "Do not escalate on steady-state success or repeated DOWN checks."
    )


def _find_existing_watchdog_schedule(
    schedules: list[Any],
    *,
    host: str,
    ports: list[int],
) -> str | None:
    expected_marker = f"--host {host}"
    expected_ports = f"--ports {','.join(str(port) for port in ports)}"
    for schedule in schedules:
        prompt = str(getattr(schedule, "prompt", "") or "")
        if _CHECK_COMMAND_MARKER in prompt and expected_marker in prompt and expected_ports in prompt:
            return str(getattr(schedule, "id"))
    return None


async def ensure_monitoring_watchdog_schedule(
    *,
    manager: ScheduleManager,
    chat_id: int | None,
    user_id: int | None,
    message_thread_id: int | None,
    model: str,
    provider_cli: str,
    resume_arg: str | None,
    interval_minutes: int,
    host: str,
    ports: list[int],
    timeout_seconds: float,
    fail_threshold: int,
    state_path: Path,
    python_bin: str,
) -> tuple[str, bool] | None:
    if chat_id is None or user_id is None:
        return None

    schedules = await manager.list_for_chat(chat_id, message_thread_id)
    existing_id = _find_existing_watchdog_schedule(schedules, host=host, ports=ports)
    if existing_id:
        return existing_id, False

    prompt = _build_watchdog_prompt(
        python_bin=python_bin,
        host=host,
        ports=ports,
        timeout_seconds=timeout_seconds,
        fail_threshold=fail_threshold,
        state_path=state_path,
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


def _cmd_check(args: argparse.Namespace) -> int:
    result = run_check(
        host=args.host,
        ports=[int(port.strip()) for port in args.ports.split(",") if port.strip()],
        timeout_seconds=args.timeout_seconds,
        fail_threshold=args.fail_threshold,
        state_path=Path(args.state_path),
    )
    print(json.dumps(result.payload, ensure_ascii=False))
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    manager = ScheduleManager(_NoopTaskManager(), Path(args.schedules_db))

    async def _install() -> dict[str, Any]:
        ensured = await ensure_monitoring_watchdog_schedule(
            manager=manager,
            chat_id=args.chat_id,
            user_id=args.user_id,
            message_thread_id=args.message_thread_id,
            model=args.model,
            provider_cli=args.provider_cli,
            resume_arg=args.resume_arg,
            interval_minutes=args.interval_minutes,
            host=args.host,
            ports=[int(port.strip()) for port in args.ports.split(",") if port.strip()],
            timeout_seconds=args.timeout_seconds,
            fail_threshold=args.fail_threshold,
            state_path=Path(args.state_path),
            python_bin=args.python_bin,
        )
        if ensured is None:
            return {"status": "skipped", "reason": "chat_id_or_user_id_missing"}
        schedule_id, created = ensured
        return {"status": "ok", "schedule_id": schedule_id, "created": created}

    print(json.dumps(asyncio.run(_install()), ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.monitoring_watchdog_tool")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check")
    check.add_argument("--host", default=config.MONITORING_WATCHDOG_HOST)
    check.add_argument(
        "--ports",
        default=",".join(str(port) for port in config.MONITORING_WATCHDOG_PORTS),
    )
    check.add_argument("--timeout-seconds", type=float, default=config.MONITORING_WATCHDOG_TIMEOUT_SECONDS)
    check.add_argument("--fail-threshold", type=int, default=config.MONITORING_WATCHDOG_FAIL_THRESHOLD)
    check.add_argument("--state-path", default=str(config.MONITORING_WATCHDOG_STATE_PATH))
    check.set_defaults(func=_cmd_check)

    install = sub.add_parser("install")
    install.add_argument("--schedules-db", default=str(config.MEMORY_DIR / "schedules.db"))
    install.add_argument("--python-bin", default=sys.executable)
    install.add_argument("--chat-id", type=int, default=config.MONITORING_WATCHDOG_CHAT_ID)
    install.add_argument("--user-id", type=int, default=config.MONITORING_WATCHDOG_USER_ID)
    install.add_argument("--message-thread-id", type=int, default=config.MONITORING_WATCHDOG_THREAD_ID)
    install.add_argument("--model", default=config.MONITORING_WATCHDOG_MODEL)
    install.add_argument("--provider-cli", default=config.MONITORING_WATCHDOG_PROVIDER_CLI)
    install.add_argument("--resume-arg", default=config.MONITORING_WATCHDOG_RESUME_ARG)
    install.add_argument("--interval-minutes", type=int, default=config.MONITORING_WATCHDOG_INTERVAL_MINUTES)
    install.add_argument("--host", default=config.MONITORING_WATCHDOG_HOST)
    install.add_argument(
        "--ports",
        default=",".join(str(port) for port in config.MONITORING_WATCHDOG_PORTS),
    )
    install.add_argument("--timeout-seconds", type=float, default=config.MONITORING_WATCHDOG_TIMEOUT_SECONDS)
    install.add_argument("--fail-threshold", type=int, default=config.MONITORING_WATCHDOG_FAIL_THRESHOLD)
    install.add_argument("--state-path", default=str(config.MONITORING_WATCHDOG_STATE_PATH))
    install.set_defaults(func=_cmd_install)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

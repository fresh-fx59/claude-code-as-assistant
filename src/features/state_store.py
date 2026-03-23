from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_text(text: str | None) -> str:
    payload = (text or "").encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()


@dataclass
class ResumeEnvelope:
    scope_key: str
    task_id: str
    step_id: str
    provider_cli: str
    model: str
    session_id: str
    input_hash: str
    output_hash: str
    attempt_no: int
    updated_at: str
    state_version: int
    resume_reason: str
    status: str  # running|completed|failed


class ResumeStateStore:
    """Simple persisted envelope store for restart-safe resume decisions."""

    _STATE_VERSION = 1

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load_all_unlocked(self) -> dict[str, ResumeEnvelope]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}

        envelopes: dict[str, ResumeEnvelope] = {}
        for scope_key, row in data.items():
            if not isinstance(row, dict):
                continue
            try:
                envelopes[scope_key] = ResumeEnvelope(**row)
            except TypeError:
                continue
        return envelopes

    def _save_all_unlocked(self, envelopes: dict[str, ResumeEnvelope]) -> None:
        payload = {k: asdict(v) for k, v in envelopes.items()}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def record_start(
        self,
        *,
        scope_key: str,
        task_id: str,
        step_id: str,
        provider_cli: str,
        model: str,
        session_id: str | None,
        input_text: str,
        resume_reason: str = "restart",
    ) -> ResumeEnvelope:
        with self._lock:
            envelopes = self._load_all_unlocked()
            prev = envelopes.get(scope_key)
            attempt_no = (prev.attempt_no + 1) if prev else 1
            env = ResumeEnvelope(
                scope_key=scope_key,
                task_id=task_id,
                step_id=step_id,
                provider_cli=provider_cli,
                model=model,
                session_id=session_id or "",
                input_hash=_hash_text(input_text),
                output_hash="",
                attempt_no=attempt_no,
                updated_at=_now_iso(),
                state_version=self._STATE_VERSION,
                resume_reason=resume_reason,
                status="running",
            )
            envelopes[scope_key] = env
            self._save_all_unlocked(envelopes)
            return env

    def record_success(self, *, scope_key: str, output_text: str | None) -> None:
        with self._lock:
            envelopes = self._load_all_unlocked()
            env = envelopes.get(scope_key)
            if not env:
                return
            env.output_hash = _hash_text(output_text)
            env.status = "completed"
            env.updated_at = _now_iso()
            envelopes[scope_key] = env
            self._save_all_unlocked(envelopes)

    def record_failure(self, *, scope_key: str) -> None:
        with self._lock:
            envelopes = self._load_all_unlocked()
            env = envelopes.get(scope_key)
            if not env:
                return
            env.status = "failed"
            env.updated_at = _now_iso()
            envelopes[scope_key] = env
            self._save_all_unlocked(envelopes)

    def can_fast_resume(
        self,
        *,
        scope_key: str,
        input_text: str,
        ttl_seconds: int = 1800,
    ) -> tuple[bool, str]:
        with self._lock:
            envelopes = self._load_all_unlocked()
            env = envelopes.get(scope_key)
            if not env:
                return False, "missing"
            if env.status != "running":
                return False, "not_running"
            if env.input_hash != _hash_text(input_text):
                return False, "input_mismatch"
            try:
                ts = datetime.fromisoformat(env.updated_at)
            except Exception:
                return False, "bad_timestamp"
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            if age > ttl_seconds:
                return False, "stale"
            return True, "ok"

    def clear(self, *, scope_key: str) -> None:
        with self._lock:
            envelopes = self._load_all_unlocked()
            if scope_key in envelopes:
                envelopes.pop(scope_key, None)
                self._save_all_unlocked(envelopes)


SteeringEventType = Literal[
    "clarify",
    "constraint_add",
    "constraint_remove",
    "priority_shift",
    "correction",
    "cancel",
]


@dataclass
class SteeringEvent:
    event_id: str
    created_at: str
    source_message_id: str
    event_type: SteeringEventType
    text: str
    intent_patch: str
    conflict_flags: list[str]
    applied: bool = False


class SteeringLedgerStore:
    """Append-only per-scope steering ledger with applied markers."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load_all_unlocked(self) -> dict[str, list[SteeringEvent]]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}

        result: dict[str, list[SteeringEvent]] = {}
        for scope_key, rows in data.items():
            if not isinstance(rows, list):
                continue
            parsed: list[SteeringEvent] = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    parsed.append(SteeringEvent(**row))
                except TypeError:
                    continue
            if parsed:
                result[scope_key] = parsed
        return result

    def _save_all_unlocked(self, payload: dict[str, list[SteeringEvent]]) -> None:
        serializable = {k: [asdict(item) for item in rows] for k, rows in payload.items()}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def append(self, *, scope_key: str, event: SteeringEvent) -> None:
        with self._lock:
            rows = self._load_all_unlocked()
            existing = rows.get(scope_key, [])
            existing.append(event)
            rows[scope_key] = existing
            self._save_all_unlocked(rows)

    def get_unapplied(self, *, scope_key: str) -> list[SteeringEvent]:
        with self._lock:
            rows = self._load_all_unlocked()
            return [item for item in rows.get(scope_key, []) if not item.applied]

    def mark_applied(self, *, scope_key: str, event_ids: list[str]) -> None:
        if not event_ids:
            return
        targets = set(event_ids)
        with self._lock:
            rows = self._load_all_unlocked()
            changed = False
            for item in rows.get(scope_key, []):
                if item.event_id in targets and not item.applied:
                    item.applied = True
                    changed = True
            if changed:
                self._save_all_unlocked(rows)

    def clear(self, *, scope_key: str) -> None:
        with self._lock:
            rows = self._load_all_unlocked()
            if scope_key in rows:
                rows.pop(scope_key, None)
                self._save_all_unlocked(rows)


@dataclass
class ProviderSyncCursor:
    scope_key: str
    provider_name: str
    last_synced_worklog_id: int
    last_injected_hash: str
    updated_at: str


class ProviderSyncStore:
    """Persist per-scope/per-provider sync cursors for context injection."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(scope_key: str, provider_name: str) -> str:
        return f"{scope_key}|{provider_name}"

    def _load_all_unlocked(self) -> dict[str, ProviderSyncCursor]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}

        cursors: dict[str, ProviderSyncCursor] = {}
        for key, row in data.items():
            if not isinstance(row, dict):
                continue
            try:
                cursors[key] = ProviderSyncCursor(**row)
            except TypeError:
                continue
        return cursors

    def _save_all_unlocked(self, cursors: dict[str, ProviderSyncCursor]) -> None:
        payload = {k: asdict(v) for k, v in cursors.items()}
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, *, scope_key: str, provider_name: str) -> ProviderSyncCursor:
        key = self._key(scope_key, provider_name)
        with self._lock:
            cursors = self._load_all_unlocked()
            current = cursors.get(key)
            if current is not None:
                return current
            return ProviderSyncCursor(
                scope_key=scope_key,
                provider_name=provider_name,
                last_synced_worklog_id=0,
                last_injected_hash="",
                updated_at=_now_iso(),
            )

    def mark_synced(
        self,
        *,
        scope_key: str,
        provider_name: str,
        latest_worklog_id: int,
        injected_hash: str | None = None,
    ) -> ProviderSyncCursor:
        key = self._key(scope_key, provider_name)
        with self._lock:
            cursors = self._load_all_unlocked()
            current = cursors.get(key)
            if current is None:
                current = ProviderSyncCursor(
                    scope_key=scope_key,
                    provider_name=provider_name,
                    last_synced_worklog_id=0,
                    last_injected_hash="",
                    updated_at=_now_iso(),
                )

            if latest_worklog_id >= current.last_synced_worklog_id:
                current.last_synced_worklog_id = latest_worklog_id
            if injected_hash is not None:
                current.last_injected_hash = injected_hash
            current.updated_at = _now_iso()

            cursors[key] = current
            self._save_all_unlocked(cursors)
            return current

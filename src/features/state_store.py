from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


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

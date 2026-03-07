from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from src.features.state_store import ResumeStateStore, SteeringEvent, SteeringLedgerStore


def test_record_start_and_success_persists(tmp_path) -> None:
    store = ResumeStateStore(tmp_path / "resume_envelopes.json")

    env = store.record_start(
        scope_key="123:main",
        task_id="msg:1",
        step_id="interactive_turn",
        provider_cli="claude",
        model="sonnet",
        session_id="sess-1",
        input_text="hello",
    )

    assert env.scope_key == "123:main"
    assert env.status == "running"
    assert env.input_hash

    store.record_success(scope_key="123:main", output_text="world")

    payload = json.loads((tmp_path / "resume_envelopes.json").read_text(encoding="utf-8"))
    assert payload["123:main"]["status"] == "completed"
    assert payload["123:main"]["output_hash"]


def test_fast_resume_valid_and_rejects_mismatch(tmp_path) -> None:
    store = ResumeStateStore(tmp_path / "resume_envelopes.json")

    store.record_start(
        scope_key="123:main",
        task_id="msg:2",
        step_id="interactive_turn",
        provider_cli="codex",
        model="gpt-5-codex",
        session_id="sess-c",
        input_text="same input",
    )

    ok, reason = store.can_fast_resume(scope_key="123:main", input_text="same input")
    assert ok is True
    assert reason == "ok"

    ok2, reason2 = store.can_fast_resume(scope_key="123:main", input_text="different")
    assert ok2 is False
    assert reason2 == "input_mismatch"


def test_fast_resume_rejects_stale(tmp_path) -> None:
    path = tmp_path / "resume_envelopes.json"
    store = ResumeStateStore(path)

    store.record_start(
        scope_key="123:main",
        task_id="msg:3",
        step_id="interactive_turn",
        provider_cli="claude",
        model="sonnet",
        session_id="",
        input_text="old",
    )

    data = json.loads(path.read_text(encoding="utf-8"))
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    data["123:main"]["updated_at"] = old_ts
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    ok, reason = store.can_fast_resume(scope_key="123:main", input_text="old", ttl_seconds=60)
    assert ok is False
    assert reason == "stale"


def test_steering_ledger_append_get_mark_and_clear(tmp_path) -> None:
    store = SteeringLedgerStore(tmp_path / "steering_ledger.json")

    event = SteeringEvent(
        event_id="evt-1",
        created_at=datetime.now(timezone.utc).isoformat(),
        source_message_id="42",
        event_type="clarify",
        text="Use pytest, not unittest",
        intent_patch="clarify: Use pytest, not unittest",
        conflict_flags=[],
    )
    store.append(scope_key="123:main", event=event)

    unapplied = store.get_unapplied(scope_key="123:main")
    assert len(unapplied) == 1
    assert unapplied[0].event_id == "evt-1"

    store.mark_applied(scope_key="123:main", event_ids=["evt-1"])
    assert store.get_unapplied(scope_key="123:main") == []

    store.clear(scope_key="123:main")
    payload = json.loads((tmp_path / "steering_ledger.json").read_text(encoding="utf-8"))
    assert "123:main" not in payload

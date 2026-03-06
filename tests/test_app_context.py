from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.bot import (
    cmd_bg,
    get_app_context,
    set_app_context,
    _state_store,
    _submit_current_step_plan_task,
)
from src.features.app_context import AppContext
from src.features.state_store import get_default_state_store


class _DummyStateStore:
    pass


def test_state_store_uses_injected_app_context() -> None:
    dummy_store = _DummyStateStore()
    set_app_context(
        AppContext(
            provider_manager=object(),
            session_manager=object(),
            memory_manager=object(),
            task_manager=None,
            schedule_manager=None,
            state_store=dummy_store,  # type: ignore[arg-type]
        )
    )
    assert _state_store() is dummy_store

    set_app_context(None)
    assert _state_store() is get_default_state_store()
    assert get_app_context() is None


@pytest.mark.asyncio
async def test_step_plan_submit_uses_app_context_managers(monkeypatch) -> None:
    fake_provider = type(
        "Provider",
        (),
        {"cli": "codex", "resume_arg": "resume", "model": "gpt-5-codex", "models": ["gpt-5-codex", "default"]},
    )()
    fake_session = type(
        "Session",
        (),
        {
            "codex_session_id": "ctx-codex-sess",
            "claude_session_id": None,
            "model": "sonnet",
            "provider": "codex",
            "codex_model": "gpt-5-codex",
        },
    )()
    fake_provider_manager = type("PM", (), {"get_provider": lambda self, _scope: fake_provider})()
    fake_session_manager = type("SM", (), {"get": lambda self, _chat, _thread: fake_session})()
    fake_memory_manager = type(
        "MM",
        (),
        {"build_context": lambda self, _prompt: "", "build_instructions": lambda self: ""},
    )()
    fake_task_manager = type(
        "TM",
        (),
        {"submit": AsyncMock(return_value="ctx-task-1"), "bot": AsyncMock()},
    )()

    try:
        set_app_context(
            AppContext(
                provider_manager=fake_provider_manager,
                session_manager=fake_session_manager,
                memory_manager=fake_memory_manager,
                task_manager=fake_task_manager,
                schedule_manager=None,
                state_store=get_default_state_store(),
            )
        )
        monkeypatch.setattr("src.bot._build_augmented_prompt", lambda prompt: prompt)
        monkeypatch.setattr(
            "src.bot._save_step_plan_state",
            lambda state: state.update({"saved_at": datetime.now(timezone.utc).isoformat()}),
        )

        task_id = await _submit_current_step_plan_task(
            {
                "chat_id": 123456789,
                "message_thread_id": 77,
                "user_id": 123456789,
                "steps": ["/tmp/01 - A.md"],
                "current_index": 0,
            }
        )

        assert task_id == "ctx-task-1"
        fake_task_manager.submit.assert_awaited_once()
        kwargs = fake_task_manager.submit.await_args.kwargs
        assert kwargs["provider_cli"] == "codex"
        assert kwargs["resume_arg"] == "resume"
        assert kwargs["session_id"] == "ctx-codex-sess"
    finally:
        set_app_context(None)


@pytest.mark.asyncio
async def test_bg_command_uses_app_context_managers(monkeypatch, mock_message) -> None:
    fake_provider = type(
        "Provider",
        (),
        {"cli": "codex", "resume_arg": "resume", "model": "gpt-5-codex", "models": ["gpt-5-codex", "default"]},
    )()
    fake_session = type(
        "Session",
        (),
        {
            "codex_session_id": "ctx-codex-sess",
            "claude_session_id": None,
            "model": "sonnet",
            "provider": "codex",
            "codex_model": "gpt-5-codex",
        },
    )()
    fake_provider_manager = type("PM", (), {"get_provider": lambda self, _scope: fake_provider})()
    fake_session_manager = type("SM", (), {"get": lambda self, _chat, _thread: fake_session})()
    fake_memory_manager = type(
        "MM",
        (),
        {"build_context": lambda self, _prompt: "", "build_instructions": lambda self: ""},
    )()
    fake_task_manager = type(
        "TM",
        (),
        {"submit": AsyncMock(return_value="ctx-task-bg"), "bot": AsyncMock()},
    )()

    mock_message.text = "/bg check context path"

    try:
        set_app_context(
            AppContext(
                provider_manager=fake_provider_manager,
                session_manager=fake_session_manager,
                memory_manager=fake_memory_manager,
                task_manager=fake_task_manager,
                schedule_manager=None,
                state_store=get_default_state_store(),
            )
        )
        monkeypatch.setattr("src.bot._find_provider_cli", lambda _name: "/usr/bin/codex")
        monkeypatch.setattr("src.bot._build_augmented_prompt", lambda prompt: prompt)

        await cmd_bg(mock_message)

        fake_task_manager.submit.assert_awaited_once()
        kwargs = fake_task_manager.submit.await_args.kwargs
        assert kwargs["provider_cli"] == "codex"
        assert kwargs["resume_arg"] == "resume"
        assert kwargs["session_id"] == "ctx-codex-sess"
        assert kwargs["model"] == "gpt-5-codex"
        assert kwargs["prompt"] == "check context path"
    finally:
        set_app_context(None)

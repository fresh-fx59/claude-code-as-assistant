from unittest.mock import AsyncMock

import pytest

from src import main


@pytest.mark.asyncio
async def test_send_startup_notification_sends_boot_message_only(monkeypatch) -> None:
    bot = AsyncMock()
    monkeypatch.setattr(main, "ALLOWED_USER_IDS", {12345})

    await main.send_startup_notification(bot, commit="abc12345")

    assert bot.send_message.await_count == 1
    first = bot.send_message.await_args_list[0].kwargs

    assert first["chat_id"] == 12345
    assert "Bot restarted" in first["text"]
    assert "Starting up" in first["text"]


@pytest.mark.asyncio
async def test_send_ready_notification_separate_message(monkeypatch) -> None:
    bot = AsyncMock()
    monkeypatch.setattr(main, "ALLOWED_USER_IDS", {12345})

    await main.send_ready_notification(bot)

    bot.send_message.assert_awaited_once_with(
        chat_id=12345,
        text="💬 Ready to accept messages.",
    )


@pytest.mark.asyncio
async def test_restart_process_for_step_plan_deferred_when_blocked(monkeypatch) -> None:
    monkeypatch.setattr(main, "should_restart_step_plan_now", AsyncMock(return_value=(False, ["123:7"])))
    kill_mock = AsyncMock()
    monkeypatch.setattr(main.os, "kill", kill_mock)

    restarted = await main.restart_process_for_step_plan("step_plan_next_step")

    assert restarted is False
    kill_mock.assert_not_called()

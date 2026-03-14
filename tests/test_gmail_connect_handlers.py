from __future__ import annotations

import pytest

from src.features import gmail_connect_handlers


def test_generate_project_id_is_google_cloud_friendly() -> None:
    project_id = gmail_connect_handlers._generate_project_id(123456789, 42)

    assert project_id.startswith("ila-gmail-")
    assert len(project_id) <= 30
    assert project_id.lower() == project_id


@pytest.mark.asyncio
async def test_cmd_gmail_connect_sends_ready_link(mock_message) -> None:
    async def fake_ensure_service_running():
        return True, "https://bot.example.com"

    async def fake_create_session(*, chat_id: int, thread_id: int | None):
        assert chat_id == 123456789
        assert thread_id is None
        return {
            "session_id": "sess-1",
            "urls": {
                "session_page_url": "https://bot.example.com/gmail/bootstrap/session/sess-1",
            },
            "google_auth_url": "https://accounts.google.com/o/oauth2/v2/auth?x=1",
        }

    await gmail_connect_handlers.cmd_gmail_connect(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        thread_id_fn=lambda _: None,
        ensure_service_running_fn=fake_ensure_service_running,
        create_session_fn=fake_create_session,
    )

    mock_message.answer.assert_awaited_once()
    text = mock_message.answer.await_args.args[0]
    markup = mock_message.answer.await_args.kwargs["reply_markup"]
    assert "Gmail setup is ready" in text
    assert markup.inline_keyboard[0][0].url.endswith("/gmail/bootstrap/session/sess-1")


@pytest.mark.asyncio
async def test_cmd_gmail_connect_reports_autostart_failure(mock_message) -> None:
    async def fake_ensure_service_running():
        return False, "service unavailable"

    await gmail_connect_handlers.cmd_gmail_connect(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        thread_id_fn=lambda _: None,
        ensure_service_running_fn=fake_ensure_service_running,
    )

    mock_message.answer.assert_awaited_once_with("service unavailable")


@pytest.mark.asyncio
async def test_cmd_gmail_status_reports_latest_session(mock_message, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("src.config.MEMORY_DIR", tmp_path)
    store = gmail_connect_handlers.GmailBootstrapStateStore(tmp_path / "gmail_bootstrap_sessions.json")
    session = store.start_session(
        project_id="ila-demo-project",
        project_name="ILA Demo Project",
        redirect_uri="https://bot.example.com/gmail/oauth/callback",
        callback_base_url="https://bot.example.com",
        oauth_client_name="ILA Gmail OAuth",
        telegram_chat_id=mock_message.chat.id,
        telegram_thread_id=None,
    )
    store.record_completed(session_id=session.session_id, gmail_account_email="alex@gmail.com")

    await gmail_connect_handlers.cmd_gmail_status(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        thread_id_fn=lambda _: None,
    )

    mock_message.answer.assert_awaited_once()
    text = mock_message.answer.await_args.args[0]
    assert "Gmail status: completed" in text
    assert "alex@gmail.com" in text

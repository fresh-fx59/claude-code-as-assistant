from __future__ import annotations

import pytest

from src.features import gmail_gateway_command_handlers
from src.gmail_gateway_client import GatewayClientError


class _FakeClient:
    async def get_account(self, *, account_id: str):
        assert account_id == "acc-1"
        return {
            "status": "connected",
            "gmail_email": "alex@example.com",
            "connected_at": "2026-03-15T10:00:00Z",
        }

    async def search_messages(self, *, account_id: str, query: str, page_size: int = 10):
        assert account_id == "acc-1"
        assert query == "subject:invoice"
        assert page_size == 10
        return {
            "messages": [
                {"id": "m1", "subject": "Invoice March", "from": "billing@example.com"},
            ]
        }

    async def read_message(self, *, account_id: str, message_id: str):
        assert account_id == "acc-1"
        assert message_id == "m1"
        return {
            "subject": "Invoice March",
            "from": "billing@example.com",
            "body_text": "Please find invoice attached.",
        }

    async def trash_message(self, *, account_id: str, message_id: str):
        assert account_id == "acc-1"
        assert message_id == "m1"

    async def delete_message(self, *, account_id: str, message_id: str):
        assert account_id == "acc-1"
        assert message_id == "m1"

    async def send_message(self, *, account_id: str, to, subject: str, body_text: str, idempotency_key: str):
        assert account_id == "acc-1"
        assert to == ["x@example.com"]
        assert subject == "Hello"
        assert body_text == "Body"
        assert idempotency_key
        return {"provider_message_id": "gmail-123"}


@pytest.mark.asyncio
async def test_cmd_gmail_account_reports_status(mock_message) -> None:
    await gmail_gateway_command_handlers.cmd_gmail_account(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "acc-1",
        client_factory=lambda: _FakeClient(),
    )
    mock_message.answer.assert_awaited_once()
    text = mock_message.answer.await_args.args[0]
    assert "acc-1" in text
    assert "connected" in text


@pytest.mark.asyncio
async def test_cmd_gmail_search_reports_messages(mock_message) -> None:
    await gmail_gateway_command_handlers.cmd_gmail_search(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "acc-1 subject:invoice",
        client_factory=lambda: _FakeClient(),
    )
    mock_message.answer.assert_awaited_once()
    assert "Found 1 message(s)" in mock_message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_gmail_send_requires_pipe_payload(mock_message) -> None:
    await gmail_gateway_command_handlers.cmd_gmail_send(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "acc-1 missing separators",
        client_factory=lambda: _FakeClient(),
    )
    mock_message.answer.assert_awaited_once_with(
        "Usage: /gmail_send <account_id> <to_csv> | <subject> | <body>"
    )


@pytest.mark.asyncio
async def test_cmd_gmail_send_success(mock_message) -> None:
    await gmail_gateway_command_handlers.cmd_gmail_send(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "acc-1 x@example.com | Hello | Body",
        client_factory=lambda: _FakeClient(),
    )
    mock_message.answer.assert_awaited_once()
    assert "Gmail message sent" in mock_message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_gmail_read_reports_gateway_error(mock_message) -> None:
    class _FailClient:
        async def read_message(self, *, account_id: str, message_id: str):
            raise GatewayClientError(
                status=401,
                code="reauth_required",
                message="Reconnect account",
                retryable=False,
            )

    await gmail_gateway_command_handlers.cmd_gmail_read(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "acc-1 m1",
        client_factory=lambda: _FailClient(),
    )
    mock_message.answer.assert_awaited_once()
    assert "reauth_required" in mock_message.answer.await_args.args[0]

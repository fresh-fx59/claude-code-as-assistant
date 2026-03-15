from __future__ import annotations

import pytest

from src.features import gmail_connect_handlers
from src.gmail_gateway_client import GatewayClientError


class _FakeClient:
    async def connect_account(self, *, account_id: str, redirect_url: str):
        assert account_id == "acc-1"
        assert redirect_url
        return {
            "connect_url": "https://gateway.example.com/oauth/connect/abc",
            "expires_at": "2026-03-16T10:00:00Z",
        }

    async def get_account(self, *, account_id: str):
        assert account_id == "acc-1"
        return {
            "account_id": account_id,
            "status": "active",
            "auth_state": "connected",
            "email": "alex@example.com",
        }


@pytest.mark.asyncio
async def test_cmd_gmail_connect_requires_account_id(mock_message) -> None:
    await gmail_connect_handlers.cmd_gmail_connect(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "",
    )

    mock_message.answer.assert_awaited_once_with("Usage: /gmail_connect <account_id> [redirect_url]")


@pytest.mark.asyncio
async def test_cmd_gmail_connect_sends_gateway_link(mock_message) -> None:
    await gmail_connect_handlers.cmd_gmail_connect(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "acc-1",
        client_factory=lambda: _FakeClient(),
    )

    mock_message.answer.assert_awaited_once()
    text = mock_message.answer.await_args.args[0]
    markup = mock_message.answer.await_args.kwargs["reply_markup"]
    assert "Gmail gateway connect session is ready" in text
    assert "acc-1" in text
    assert markup.inline_keyboard[0][0].url == "https://gateway.example.com/oauth/connect/abc"


@pytest.mark.asyncio
async def test_cmd_gmail_status_reports_gateway_account(mock_message) -> None:
    await gmail_connect_handlers.cmd_gmail_status(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "acc-1",
        client_factory=lambda: _FakeClient(),
    )

    mock_message.answer.assert_awaited_once()
    text = mock_message.answer.await_args.args[0]
    assert "Gmail account: acc-1" in text
    assert "Auth state: connected" in text
    assert "alex@example.com" in text


@pytest.mark.asyncio
async def test_cmd_gmail_status_reports_gateway_error(mock_message) -> None:
    class _FailClient:
        async def get_account(self, *, account_id: str):
            raise GatewayClientError(
                status=404,
                code="not_found",
                message="account missing",
                retryable=False,
            )

    await gmail_connect_handlers.cmd_gmail_status(
        mock_message,
        is_authorized=lambda user_id, chat_id: True,
        command_args_fn=lambda message, command: "acc-1",
        client_factory=lambda: _FailClient(),
    )

    mock_message.answer.assert_awaited_once()
    assert "not_found" in mock_message.answer.await_args.args[0]

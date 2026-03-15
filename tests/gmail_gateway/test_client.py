from pathlib import Path

import pytest
from aiohttp.test_utils import TestServer

from src.gmail_gateway.http import AUTH_STORE_KEY, GMAIL_API_KEY, create_app
from src.gmail_gateway_client import GatewayClientError, GmailGatewayClient


@pytest.mark.asyncio
async def test_client_connect_callback_send_and_disconnect(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "gateway.db")
    class _FakeGmailApi:
        async def send_message(self, *, access_token: str, to: list[str], subject: str, body_text: str) -> str:
            return "gmail-msg-1"
    app[GMAIL_API_KEY] = _FakeGmailApi()
    server = TestServer(app)
    await server.start_server()
    try:
        client = GmailGatewayClient(base_url=str(server.make_url("/")).rstrip("/"))

        connect = await client.connect_account(
            account_id="acc-1",
            redirect_url="https://app.example.com/connected",
        )
        session_id = connect["connect_url"].split("session_id=")[-1]

        account = await client.oauth_callback(
            session_id=session_id,
            gmail_email="alex@example.com",
            access_token="access-token",
            refresh_token="refresh-token",
            scopes="gmail.readonly gmail.send",
        )
        assert account["auth_state"] == "connected"

        receipt = await client.send_message(
            account_id="acc-1",
            to=["bob@example.com"],
            subject="Hello",
            body_text="Body",
            idempotency_key="idem-client-1",
        )
        assert receipt["status"] == "sent"

        await client.disconnect_account(account_id="acc-1")

        disconnected = await client.get_account(account_id="acc-1")
        assert disconnected["status"] == "disabled"
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_client_search_read_trash_delete(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "gateway.db")
    app[AUTH_STORE_KEY].upsert_account(account_id="acc-1", gmail_email="alex@example.com")
    app[AUTH_STORE_KEY].upsert_token(
        token_id="tok-1",
        account_id="acc-1",
        access_token_ciphertext=b"access-token",
        refresh_token_ciphertext=b"refresh-token",
        scopes="gmail.readonly gmail.modify",
        kms_key_version="kms-v1",
        expires_at=None,
    )
    class _FakeGmailApi:
        def __init__(self) -> None:
            self.msg = {
                "message_id": "msg-1",
                "thread_id": "thr-1",
                "subject": "Project update",
                "from": "alice@example.com",
                "snippet": "latest update",
                "internal_date": "1710000000",
                "body_text": "full body",
                "body_html": None,
                "labels": ["INBOX"],
            }
        async def send_message(self, *, access_token: str, to: list[str], subject: str, body_text: str) -> str:
            return "gmail-msg-1"
        async def search_messages(self, *, access_token: str, query: str, max_results: int):
            return [self.msg]
        async def read_message(self, *, access_token: str, message_id: str):
            return self.msg
        async def trash_message(self, *, access_token: str, message_id: str):
            if "TRASH" not in self.msg["labels"]:
                self.msg["labels"].append("TRASH")
        async def delete_message(self, *, access_token: str, message_id: str):
            if "DELETED" not in self.msg["labels"]:
                self.msg["labels"].append("DELETED")
    app[GMAIL_API_KEY] = _FakeGmailApi()
    server = TestServer(app)
    await server.start_server()
    try:
        client = GmailGatewayClient(base_url=str(server.make_url("/")).rstrip("/"))
        search = await client.search_messages(account_id="acc-1", query="Project", page_size=10)
        assert search["messages"][0]["message_id"] == "msg-1"

        read = await client.read_message(account_id="acc-1", message_id="msg-1")
        assert read["body_text"] == "full body"

        await client.trash_message(account_id="acc-1", message_id="msg-1")
        after_trash = await client.read_message(account_id="acc-1", message_id="msg-1")
        assert "TRASH" in after_trash["labels"]

        await client.delete_message(account_id="acc-1", message_id="msg-1")
        after_delete = await client.read_message(account_id="acc-1", message_id="msg-1")
        assert "DELETED" in after_delete["labels"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_client_raises_typed_error(tmp_path: Path) -> None:
    app = create_app(db_path=tmp_path / "gateway.db")
    server = TestServer(app)
    await server.start_server()
    try:
        client = GmailGatewayClient(base_url=str(server.make_url("/")).rstrip("/"))
        with pytest.raises(GatewayClientError) as exc:
            await client.get_account(account_id="missing")

        assert exc.value.status == 404
        assert exc.value.code == "not_found"
    finally:
        await server.close()

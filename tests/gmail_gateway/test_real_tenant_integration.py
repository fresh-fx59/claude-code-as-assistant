from __future__ import annotations

import os
import secrets

import pytest

from src.gmail_gateway_client import GmailGatewayClient


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.skip(f"{name} is required for real-tenant integration checks")
    return value


@pytest.mark.asyncio
async def test_real_tenant_account_status_and_search_contract() -> None:
    base_url = _required_env("GMAIL_GATEWAY_REAL_BASE_URL")
    account_id = _required_env("GMAIL_GATEWAY_REAL_ACCOUNT_ID")

    client = GmailGatewayClient(base_url=base_url, timeout_seconds=30.0)
    account = await client.get_account(account_id=account_id)
    assert account["account_id"] == account_id
    assert account["auth_state"] in {"connected", "expired", "revoked", "not_connected"}

    # Safe read-only probe for staged environments.
    search = await client.search_messages(
        account_id=account_id,
        query="in:anywhere",
        page_size=1,
    )
    assert "messages" in search
    assert isinstance(search["messages"], list)


@pytest.mark.asyncio
async def test_real_tenant_optional_read_probe() -> None:
    base_url = _required_env("GMAIL_GATEWAY_REAL_BASE_URL")
    account_id = _required_env("GMAIL_GATEWAY_REAL_ACCOUNT_ID")
    message_id = os.getenv("GMAIL_GATEWAY_REAL_MESSAGE_ID", "").strip()
    if not message_id:
        pytest.skip("Set GMAIL_GATEWAY_REAL_MESSAGE_ID to run the read-message probe")

    client = GmailGatewayClient(base_url=base_url, timeout_seconds=30.0)
    payload = await client.read_message(account_id=account_id, message_id=message_id)
    assert payload["message_id"] == message_id
    assert isinstance(payload.get("labels", []), list)


@pytest.mark.asyncio
async def test_real_tenant_optional_send_probe() -> None:
    base_url = _required_env("GMAIL_GATEWAY_REAL_BASE_URL")
    account_id = _required_env("GMAIL_GATEWAY_REAL_ACCOUNT_ID")
    recipient = os.getenv("GMAIL_GATEWAY_REAL_SEND_TO", "").strip()
    if not recipient:
        pytest.skip("Set GMAIL_GATEWAY_REAL_SEND_TO to run the send probe")

    client = GmailGatewayClient(base_url=base_url, timeout_seconds=30.0)
    result = await client.send_message(
        account_id=account_id,
        to=[recipient],
        subject="Gateway stage probe",
        body_text="Automated stage probe message.",
        idempotency_key=f"stage-probe-{secrets.token_hex(8)}",
    )
    assert result.get("status") in {"queued", "sent"}

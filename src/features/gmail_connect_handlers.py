from __future__ import annotations

from typing import Any, Callable

from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import config
from ..gmail_gateway_client import GatewayClientError, GmailGatewayClient


def _default_redirect_url() -> str:
    if config.GMAIL_BOOTSTRAP_PUBLIC_BASE_URL:
        return f"{config.GMAIL_BOOTSTRAP_PUBLIC_BASE_URL}/gmail/connected"
    return f"{config.GMAIL_GATEWAY_BASE_URL}/gmail/connected"


def _gateway_error_text(exc: GatewayClientError) -> str:
    retry_hint = " Please retry." if exc.retryable else ""
    return f"Gmail gateway error ({exc.code}): {exc.message}.{retry_hint}".strip()


def _parse_connect_args(raw_args: str) -> tuple[str, str]:
    parts = raw_args.split(maxsplit=1)
    if not parts:
        return "", ""
    account_id = parts[0].strip()
    if not account_id:
        return "", ""
    if len(parts) == 1:
        return account_id, _default_redirect_url()
    redirect_url = parts[1].strip()
    return account_id, (redirect_url or _default_redirect_url())


async def cmd_gmail_connect(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    command_args_fn: Callable[[Any, Any | None], str],
    command: Any | None = None,
    client_factory: Callable[[], GmailGatewayClient] = GmailGatewayClient.from_config,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return

    raw_args = command_args_fn(message, command)
    account_id, redirect_url = _parse_connect_args(raw_args)
    if not account_id:
        await message.answer("Usage: /gmail_connect <account_id> [redirect_url]")
        return

    client = client_factory()
    try:
        payload = await client.connect_account(account_id=account_id, redirect_url=redirect_url)
    except GatewayClientError as exc:
        await message.answer(_gateway_error_text(exc))
        return
    except Exception as exc:
        await message.answer(f"Failed to create gateway connect session: {exc}")
        return

    connect_url = str(payload.get("connect_url") or "").strip()
    if not connect_url:
        await message.answer("Gateway did not return a connect URL.")
        return

    expires_at = str(payload.get("expires_at") or "-")
    lines = [
        "Gmail gateway connect session is ready.",
        "",
        "1. Open the connect link.",
        "2. Authorize the Gmail account.",
        "3. Return here and run /gmail_status <account_id>.",
        "",
        f"Account: {account_id}",
        f"Expires at: {expires_at}",
    ]
    kb = InlineKeyboardBuilder()
    kb.button(text="Open Gmail Connect", url=connect_url)
    await message.answer("\n".join(lines), reply_markup=kb.as_markup())


async def cmd_gmail_status(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    command_args_fn: Callable[[Any, Any | None], str],
    command: Any | None = None,
    client_factory: Callable[[], GmailGatewayClient] = GmailGatewayClient.from_config,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return

    account_id = command_args_fn(message, command).strip()
    if not account_id:
        await message.answer("Usage: /gmail_status <account_id>")
        return

    client = client_factory()
    try:
        payload = await client.get_account(account_id=account_id)
    except GatewayClientError as exc:
        await message.answer(_gateway_error_text(exc))
        return
    except Exception as exc:
        await message.answer(f"Failed to fetch Gmail gateway account status: {exc}")
        return

    lines = [
        f"Gmail account: {account_id}",
        f"Status: {payload.get('status', 'unknown')}",
        f"Auth state: {payload.get('auth_state', 'unknown')}",
    ]
    email = str(payload.get("email") or "").strip()
    if email:
        lines.append(f"Email: {email}")
    await message.answer("\n".join(lines))

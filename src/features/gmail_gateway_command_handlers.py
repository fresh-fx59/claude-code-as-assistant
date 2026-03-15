from __future__ import annotations

import html
import secrets
from typing import Any, Callable

from ..gmail_gateway_client import GatewayClientError, GmailGatewayClient


def _error_text(exc: GatewayClientError) -> str:
    retry_hint = " Please retry." if exc.retryable else ""
    return f"Gmail gateway error ({exc.code}): {exc.message}.{retry_hint}".strip()


def _parse_account_and_rest(raw_args: str) -> tuple[str, str]:
    parts = raw_args.split(maxsplit=1)
    if len(parts) < 2:
        return "", ""
    return parts[0].strip(), parts[1].strip()


async def cmd_gmail_account(
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
        await message.answer("Usage: /gmail_account <account_id>")
        return

    client = client_factory()
    try:
        payload = await client.get_account(account_id=account_id)
    except GatewayClientError as exc:
        await message.answer(_error_text(exc))
        return
    except Exception as exc:
        await message.answer(f"Gmail gateway request failed: {exc}")
        return

    status = payload.get("status") or "unknown"
    gmail_email = payload.get("gmail_email") or "(not linked)"
    connected_at = payload.get("connected_at") or "-"
    await message.answer(
        (
            f"<b>Account:</b> <code>{html.escape(account_id)}</code>\n"
            f"<b>Status:</b> {html.escape(str(status))}\n"
            f"<b>Gmail:</b> {html.escape(str(gmail_email))}\n"
            f"<b>Connected:</b> {html.escape(str(connected_at))}"
        ),
        parse_mode="HTML",
    )


async def cmd_gmail_search(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    command_args_fn: Callable[[Any, Any | None], str],
    command: Any | None = None,
    client_factory: Callable[[], GmailGatewayClient] = GmailGatewayClient.from_config,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    account_id, query = _parse_account_and_rest(command_args_fn(message, command))
    if not account_id or not query:
        await message.answer("Usage: /gmail_search <account_id> <query>")
        return

    client = client_factory()
    try:
        payload = await client.search_messages(account_id=account_id, query=query, page_size=10)
    except GatewayClientError as exc:
        await message.answer(_error_text(exc))
        return
    except Exception as exc:
        await message.answer(f"Gmail gateway request failed: {exc}")
        return

    messages = payload.get("messages") or []
    if not messages:
        await message.answer("No Gmail messages matched your query.")
        return

    lines = [f"Found {len(messages)} message(s):"]
    for item in messages[:10]:
        message_id = str(item.get("id", ""))
        subject = str(item.get("subject", "(no subject)"))
        sender = str(item.get("from", "?"))
        lines.append(f"- <code>{html.escape(message_id)}</code> | {html.escape(subject)} | {html.escape(sender)}")
    await message.answer("\n".join(lines), parse_mode="HTML")


async def cmd_gmail_read(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    command_args_fn: Callable[[Any, Any | None], str],
    command: Any | None = None,
    client_factory: Callable[[], GmailGatewayClient] = GmailGatewayClient.from_config,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    account_id, message_id = _parse_account_and_rest(command_args_fn(message, command))
    if not account_id or not message_id:
        await message.answer("Usage: /gmail_read <account_id> <message_id>")
        return

    client = client_factory()
    try:
        payload = await client.read_message(account_id=account_id, message_id=message_id)
    except GatewayClientError as exc:
        await message.answer(_error_text(exc))
        return
    except Exception as exc:
        await message.answer(f"Gmail gateway request failed: {exc}")
        return

    subject = str(payload.get("subject") or "(no subject)")
    sender = str(payload.get("from") or "?")
    snippet = str(payload.get("snippet") or "")
    body = str(payload.get("body_text") or "")
    text = body.strip() or snippet.strip() or "(empty message)"
    if len(text) > 3000:
        text = text[:3000] + "..."
    await message.answer(
        (
            f"<b>Subject:</b> {html.escape(subject)}\n"
            f"<b>From:</b> {html.escape(sender)}\n\n"
            f"{html.escape(text)}"
        ),
        parse_mode="HTML",
    )


async def cmd_gmail_trash(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    command_args_fn: Callable[[Any, Any | None], str],
    command: Any | None = None,
    client_factory: Callable[[], GmailGatewayClient] = GmailGatewayClient.from_config,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    account_id, message_id = _parse_account_and_rest(command_args_fn(message, command))
    if not account_id or not message_id:
        await message.answer("Usage: /gmail_trash <account_id> <message_id>")
        return
    client = client_factory()
    try:
        await client.trash_message(account_id=account_id, message_id=message_id)
    except GatewayClientError as exc:
        await message.answer(_error_text(exc))
        return
    except Exception as exc:
        await message.answer(f"Gmail gateway request failed: {exc}")
        return
    await message.answer(f"Moved Gmail message <code>{html.escape(message_id)}</code> to trash.", parse_mode="HTML")


async def cmd_gmail_delete(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    command_args_fn: Callable[[Any, Any | None], str],
    command: Any | None = None,
    client_factory: Callable[[], GmailGatewayClient] = GmailGatewayClient.from_config,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    account_id, message_id = _parse_account_and_rest(command_args_fn(message, command))
    if not account_id or not message_id:
        await message.answer("Usage: /gmail_delete <account_id> <message_id>")
        return
    client = client_factory()
    try:
        await client.delete_message(account_id=account_id, message_id=message_id)
    except GatewayClientError as exc:
        await message.answer(_error_text(exc))
        return
    except Exception as exc:
        await message.answer(f"Gmail gateway request failed: {exc}")
        return
    await message.answer(f"Deleted Gmail message <code>{html.escape(message_id)}</code>.", parse_mode="HTML")


async def cmd_gmail_send(
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
    account_id, rest = _parse_account_and_rest(raw_args)
    if not account_id or not rest:
        await message.answer("Usage: /gmail_send <account_id> <to_csv> | <subject> | <body>")
        return
    pieces = [item.strip() for item in rest.split("|", maxsplit=2)]
    if len(pieces) != 3 or not all(pieces):
        await message.answer("Usage: /gmail_send <account_id> <to_csv> | <subject> | <body>")
        return
    recipients = [item.strip() for item in pieces[0].split(",") if item.strip()]
    if not recipients:
        await message.answer("Usage: /gmail_send <account_id> <to_csv> | <subject> | <body>")
        return
    subject = pieces[1]
    body_text = pieces[2]
    idem = f"tg-{message.chat.id}-{message.message_id}-{secrets.token_hex(4)}"

    client = client_factory()
    try:
        payload = await client.send_message(
            account_id=account_id,
            to=recipients,
            subject=subject,
            body_text=body_text,
            idempotency_key=idem,
        )
    except GatewayClientError as exc:
        await message.answer(_error_text(exc))
        return
    except Exception as exc:
        await message.answer(f"Gmail gateway request failed: {exc}")
        return

    provider_id = str(payload.get("provider_message_id") or "")
    await message.answer(
        (
            "Gmail message sent.\n"
            f"To: {html.escape(', '.join(recipients))}\n"
            f"Subject: {html.escape(subject)}\n"
            f"Provider id: <code>{html.escape(provider_id or '-')}</code>"
        ),
        parse_mode="HTML",
    )

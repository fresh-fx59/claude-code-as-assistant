from __future__ import annotations

import asyncio
import html
import inspect
import os
from dataclasses import replace
from datetime import datetime, timezone as tz
from typing import Any, Callable

from ..memory import MemoryManager


async def cmd_start(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    config: Any,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return

    user_tz = None
    try:
        user_tz = MemoryManager(config.MEMORY_DIR).get_timezone(default="UTC")
    except Exception:
        pass

    status_lines = [
        f"Hello! I'm a Claude Code assistant. <b>v{config.VERSION}</b>",
    ]
    if user_tz:
        try:
            tz_obj = tz.timezone(user_tz)
            now = datetime.now(tz.utc).astimezone(tz_obj)
            time_str = now.strftime("%H:%M")
            status_lines.append(f"<b>Time:</b> {time_str} ({user_tz})")
        except Exception:
            pass

    status_lines.extend([
        "",
        "Send me any message and I'll respond using Claude.",
        "",
        "<b>Commands:</b>",
        "/new — Start a fresh conversation",
        "/model — Switch model",
        "/provider — Switch LLM provider",
        "/status — Show current session info",
        "/threads — Show tracked forum topics/threads",
        "/memory — Show what I remember",
        "/tools — Show available tools",
        "/gmail_connect — Start Gmail API setup",
        "/gmail_status — Show Gmail setup status",
        "/gmail_account <account_id> — Show Gmail account link status",
        "/gmail_search <account_id> <query> — Search Gmail messages",
        "/gmail_read <account_id> <message_id> — Read a Gmail message",
        "/gmail_trash <account_id> <message_id> — Move message to trash",
        "/gmail_delete <account_id> <message_id> — Permanently delete message",
        "/gmail_send <account_id> <to_csv> | <subject> | <body> — Send email",
        "/rollback — Roll back to previous version (admin)",
        "/selfmod_stage — Stage sandbox plugin (admin)",
        "/selfmod_apply — Validate+promote sandbox plugin (admin)",
        "/schedule_every <min> <task> — Schedule recurring task",
        "/schedule_daily <HH:MM> <task> — Schedule daily recurring task",
        "/schedule_weekly <day> <HH:MM> <task> — Schedule weekly task",
        "/schedule_list — List recurring schedules",
        "/schedule_cancel <id> — Cancel recurring schedule",
        "/bg <task> — Run task in background",
        "/bg_cancel <id> — Cancel background task",
        "/cancel — Cancel current request",
    ])

    await message.answer("\n".join(status_lines), parse_mode="HTML")


async def cmd_new(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    thread_id_fn: Callable[[Any], int | None],
    scope_key_fn: Callable[[int, int | None], str],
    provider_manager: Any,
    session_manager: Any,
    steering_ledger_store: Any,
    clear_errors_fn: Callable[[str], None],
    get_state_fn: Callable[[str], Any],
    reflect_fn: Callable[[int, Any, Any], Any],
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    chat_id = message.chat.id
    thread_id = thread_id_fn(message)
    scope_key = scope_key_fn(chat_id, thread_id)
    session = session_manager.get(chat_id, thread_id)
    provider = provider_manager.get_provider(scope_key)
    if session.provider and session.provider != provider.name:
        restored_provider = provider_manager.set_provider(scope_key, session.provider)
        if restored_provider:
            provider = restored_provider
        else:
            session_manager.set_provider(chat_id, provider.name, thread_id)
    elif not session.provider:
        session_manager.set_provider(chat_id, provider.name, thread_id)
    if (
        os.getenv("DISABLE_REFLECTION") != "1"
        and (session.claude_session_id or session.codex_session_id)
    ):
        reflection_session = replace(session)
        asyncio.create_task(reflect_fn(chat_id, reflection_session, provider))
    state = get_state_fn(scope_key)
    if state.lock.locked():
        state.cancel_requested = True
        state.reset_requested = True
        proc = state.process_handle.get("proc") if state.process_handle else None
        if proc:
            kill_result = proc.kill()
            if inspect.isawaitable(kill_result):
                await kill_result
    session_manager.new_conversation(chat_id, thread_id)
    session_manager.new_codex_conversation(chat_id, thread_id)
    steering_ledger_store.clear(scope_key=scope_key)
    clear_errors_fn(scope_key)
    if state.lock.locked():
        await message.answer(
            "Conversation reset requested. If a request was running, it is being cancelled. "
            "Send your next message in a moment."
        )
    else:
        await message.answer("Conversation cleared. Send a message to start fresh.")


async def cmd_status(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    thread_id_fn: Callable[[Any], int | None],
    scope_key_fn: Callable[[int, int | None], str],
    session_manager: Any,
    provider_manager: Any,
    is_codex_family_cli_fn: Callable[[str | None], bool],
    current_model_label_fn: Callable[[Any, Any], str],
    version: str,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    chat_id = message.chat.id
    thread_id = thread_id_fn(message)
    scope_key = scope_key_fn(chat_id, thread_id)
    session = session_manager.get(chat_id, thread_id)
    provider = provider_manager.get_provider(scope_key)
    if is_codex_family_cli_fn(provider.cli):
        sid = session.codex_session_id or "none (new conversation)"
    else:
        sid = session.claude_session_id or "none (new conversation)"
    current_model = current_model_label_fn(session, provider)
    await message.answer(
        f"<b>Version:</b> {version}\n"
        f"<b>Thread:</b> <code>{thread_id if thread_id is not None else 'main'}</code>\n"
        f"<b>Session:</b> <code>{sid}</code>\n"
        f"<b>Model:</b> {current_model}\n"
        f"<b>Provider:</b> {provider.name} — {provider.description}",
        parse_mode="HTML",
    )


async def cmd_memory(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    memory_manager: Any,
    split_message_fn: Callable[[str], list[str]],
    strip_html_fn: Callable[[str], str],
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    content = memory_manager.format_for_display()
    for chunk in split_message_fn(content):
        try:
            await message.answer(chunk, parse_mode="HTML")
        except Exception:
            await message.answer(strip_html_fn(chunk))


async def cmd_threads(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    session_manager: Any,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    rows = session_manager.list_tracked_threads(message.chat.id)
    if not rows:
        await message.answer("No tracked threads yet for this chat.")
        return

    lines = ["<b>Tracked threads</b>", ""]
    for row in rows:
        thread = row.get("message_thread_id")
        topic = row.get("topic_label") or "(untitled)"
        last_seen = row.get("last_activity_at") or "n/a"
        lines.append(
            f"• <code>{thread if thread is not None else 'main'}</code> — {html.escape(str(topic))}"
        )
        lines.append(f"  last: {html.escape(str(last_seen))}")

    await message.answer("\n".join(lines), parse_mode="HTML")


async def cmd_tools(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    tool_registry: Any,
    strip_html_fn: Callable[[str], str],
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return
    content = tool_registry.format_for_display()
    try:
        await message.answer(content, parse_mode="HTML")
    except Exception:
        await message.answer(strip_html_fn(content))


async def cmd_cancel(
    message: Any,
    *,
    is_authorized: Callable[[int | None, int | None], bool],
    thread_id_fn: Callable[[Any], int | None],
    scope_key_fn: Callable[[int, int | None], str],
    get_state_fn: Callable[[str], Any],
    session_manager: Any,
    current_provider_fn: Callable[[str], Any],
    current_model_label_fn: Callable[[Any, Any], str],
    metrics: Any,
) -> None:
    if not is_authorized(message.from_user and message.from_user.id, message.chat.id):
        return

    chat_id = message.chat.id
    thread_id = thread_id_fn(message)
    scope_key = scope_key_fn(chat_id, thread_id)
    state = get_state_fn(scope_key)

    if not state.lock.locked() or not state.process_handle or not state.process_handle.get("proc"):
        await message.answer("Nothing to cancel.")
        return

    proc = state.process_handle["proc"]
    kill_result = proc.kill()
    if inspect.isawaitable(kill_result):
        await kill_result
    state.cancel_requested = True
    session = session_manager.get(chat_id, thread_id)
    provider = current_provider_fn(scope_key)
    metrics.CLAUDE_REQUESTS_TOTAL.labels(
        model=current_model_label_fn(session, provider),
        status="cancelled",
    ).inc()

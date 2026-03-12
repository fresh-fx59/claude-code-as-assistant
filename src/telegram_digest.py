"""Telegram user-account collection and briefing helpers for daily digests."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SourceRecord:
    peer_key: str
    title: str
    username: str | None
    kind: str
    linked_channel_key: str | None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def _peer_key(kind: str, entity_id: int) -> str:
    return f"{kind}:{entity_id}"


def _truncate(text: str, limit: int) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


class TelegramDigestStore:
    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or config.TELEGRAM_DIGEST_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS digest_sources (
                    peer_key TEXT PRIMARY KEY,
                    entity_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    username TEXT,
                    kind TEXT NOT NULL,
                    linked_channel_key TEXT,
                    last_collected_message_id INTEGER,
                    last_collected_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS digest_messages (
                    peer_key TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    posted_at TEXT NOT NULL,
                    sender_id INTEGER,
                    views INTEGER,
                    forwards INTEGER,
                    replies INTEGER,
                    link TEXT,
                    text TEXT,
                    raw_json TEXT,
                    PRIMARY KEY(peer_key, message_id)
                )
                """
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_digest_messages_posted_at ON digest_messages(posted_at DESC)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_digest_messages_peer_time ON digest_messages(peer_key, posted_at DESC)"
            )

    def upsert_source(
        self,
        *,
        peer_key: str,
        entity_id: int,
        title: str,
        username: str | None,
        kind: str,
        linked_channel_key: str | None,
    ) -> None:
        now = _isoformat(_utc_now())
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO digest_sources(peer_key, entity_id, title, username, kind, linked_channel_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(peer_key) DO UPDATE SET
                    title = excluded.title,
                    username = excluded.username,
                    kind = excluded.kind,
                    linked_channel_key = excluded.linked_channel_key,
                    updated_at = excluded.updated_at
                """,
                (peer_key, entity_id, title, username, kind, linked_channel_key, now, now),
            )

    def last_message_id(self, peer_key: str) -> int:
        with self._connect() as con:
            row = con.execute(
                "SELECT COALESCE(MAX(message_id), 0) AS max_message_id FROM digest_messages WHERE peer_key = ?",
                (peer_key,),
            ).fetchone()
            return int(row["max_message_id"] or 0)

    def insert_message(
        self,
        *,
        peer_key: str,
        message_id: int,
        posted_at: datetime,
        sender_id: int | None,
        views: int | None,
        forwards: int | None,
        replies: int | None,
        link: str | None,
        text: str,
        raw_json: dict[str, Any],
    ) -> bool:
        raw = json.dumps(raw_json, ensure_ascii=False)
        with self._connect() as con:
            cur = con.execute(
                """
                INSERT OR IGNORE INTO digest_messages
                (peer_key, message_id, posted_at, sender_id, views, forwards, replies, link, text, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    peer_key,
                    message_id,
                    _isoformat(posted_at),
                    sender_id,
                    views,
                    forwards,
                    replies,
                    link,
                    text,
                    raw,
                ),
            )
            return cur.rowcount == 1

    def mark_collected(self, peer_key: str, message_id: int | None) -> None:
        now = _isoformat(_utc_now())
        with self._connect() as con:
            con.execute(
                """
                UPDATE digest_sources
                SET last_collected_message_id = COALESCE(?, last_collected_message_id),
                    last_collected_at = ?,
                    updated_at = ?
                WHERE peer_key = ?
                """,
                (message_id, now, now, peer_key),
            )

    def source_count(self) -> int:
        with self._connect() as con:
            row = con.execute("SELECT COUNT(*) AS count FROM digest_sources").fetchone()
            return int(row["count"] or 0)

    def recent_message_count(self, window_hours: int) -> int:
        cutoff = _isoformat(_utc_now() - timedelta(hours=window_hours))
        with self._connect() as con:
            row = con.execute(
                "SELECT COUNT(*) AS count FROM digest_messages WHERE posted_at >= ?",
                (cutoff,),
            ).fetchone()
            return int(row["count"] or 0)

    def render_briefing(
        self,
        *,
        window_hours: int,
        per_source_limit: int = 8,
        source_limit: int = 80,
    ) -> str:
        cutoff = _isoformat(_utc_now() - timedelta(hours=window_hours))
        with self._connect() as con:
            sources = con.execute(
                """
                SELECT s.peer_key, s.title, s.username, s.kind, s.linked_channel_key,
                       COUNT(m.message_id) AS message_count,
                       MAX(m.posted_at) AS latest_posted_at
                FROM digest_sources s
                JOIN digest_messages m ON m.peer_key = s.peer_key
                WHERE m.posted_at >= ?
                GROUP BY s.peer_key, s.title, s.username, s.kind, s.linked_channel_key
                ORDER BY latest_posted_at DESC
                LIMIT ?
                """,
                (cutoff, source_limit),
            ).fetchall()

            lines = [
                "# Telegram digest briefing",
                f"Generated at: {_isoformat(_utc_now())}",
                f"Window hours: {window_hours}",
                f"Sources with activity: {len(sources)}",
                "",
                "Summarize this into a short executive Russian digest.",
                "Focus on trends, important events, repeated signals across sources, and what changed.",
                "Use only a few source links when truly important.",
                "",
            ]

            for source in sources:
                kind_label = "linked_chat" if source["kind"] == "linked_chat" else "channel"
                lines.append(
                    f"## {source['title']} [{kind_label}] messages={source['message_count']}"
                )
                messages = con.execute(
                    """
                    SELECT message_id, posted_at, views, forwards, replies, link, text
                    FROM digest_messages
                    WHERE peer_key = ? AND posted_at >= ?
                    ORDER BY posted_at DESC
                    LIMIT ?
                    """,
                    (source["peer_key"], cutoff, per_source_limit),
                ).fetchall()
                for message in messages:
                    stats: list[str] = []
                    if message["views"] is not None:
                        stats.append(f"views={message['views']}")
                    if message["forwards"] is not None:
                        stats.append(f"forwards={message['forwards']}")
                    if message["replies"] is not None:
                        stats.append(f"replies={message['replies']}")
                    stat_suffix = f" ({', '.join(stats)})" if stats else ""
                    lines.append(
                        f"- {message['posted_at']}: {_truncate(message['text'] or '', 500)}{stat_suffix}"
                    )
                    if message["link"]:
                        lines.append(f"  link: {message['link']}")
                lines.append("")
            return "\n".join(lines).strip() + "\n"


async def collect_digest(
    *,
    db_path: Path | None = None,
    brief_path: Path | None = None,
    window_hours: int | None = None,
    source_limit: int | None = None,
    collect_limit: int | None = None,
) -> dict[str, Any]:
    store = TelegramDigestStore(db_path)
    brief_target = brief_path or config.TELEGRAM_DIGEST_BRIEF_PATH
    window_hours = window_hours or config.TELEGRAM_DIGEST_WINDOW_HOURS
    source_limit = source_limit or config.TELEGRAM_DIGEST_SOURCE_LIMIT
    collect_limit = collect_limit or config.TELEGRAM_DIGEST_COLLECT_LIMIT

    if not config.TELEGRAM_USER_API_ID or not config.TELEGRAM_USER_API_HASH:
        return {
            "status": "error",
            "should_alert": False,
            "change_type": "missing_config",
            "summary": "Telegram user API credentials are not configured.",
        }

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.channels import GetFullChannelRequest
        from telethon.tl.types import Channel
    except Exception as exc:  # pragma: no cover - dependency/import failure
        return {
            "status": "error",
            "should_alert": False,
            "change_type": "missing_dependency",
            "summary": f"Telethon import failed: {exc}",
        }

    session: object
    if config.TELEGRAM_USER_SESSION:
        session = StringSession(config.TELEGRAM_USER_SESSION)
    else:
        session = str(config.TELEGRAM_USER_SESSION_PATH)

    collected_messages = 0
    tracked_sources = 0
    linked_chat_to_channel: dict[int, str] = {}

    client = TelegramClient(session, config.TELEGRAM_USER_API_ID, config.TELEGRAM_USER_API_HASH)
    async with client:
        dialogs = [dialog async for dialog in client.iter_dialogs(limit=source_limit)]
        entity_by_id: dict[int, Any] = {}
        channel_entities: list[Any] = []
        for dialog in dialogs:
            entity = dialog.entity
            if not isinstance(entity, Channel):
                continue
            entity_by_id[int(entity.id)] = entity
            if getattr(entity, "broadcast", False):
                channel_entities.append(entity)

        for entity in channel_entities:
            channel_key = _peer_key("channel", int(entity.id))
            store.upsert_source(
                peer_key=channel_key,
                entity_id=int(entity.id),
                title=(getattr(entity, "title", None) or "Unnamed channel").strip(),
                username=getattr(entity, "username", None),
                kind="channel",
                linked_channel_key=None,
            )
            tracked_sources += 1
            try:
                full = await client(GetFullChannelRequest(entity))
                linked_chat_id = getattr(full.full_chat, "linked_chat_id", None)
                if linked_chat_id:
                    linked_chat_to_channel[int(linked_chat_id)] = channel_key
            except Exception:
                logger.debug("Could not resolve linked chat for channel=%s", getattr(entity, "id", None), exc_info=True)

        target_entities: list[tuple[str, Any, str | None]] = []
        for entity in channel_entities:
            target_entities.append((_peer_key("channel", int(entity.id)), entity, None))
        for linked_chat_id in sorted(linked_chat_to_channel):
            linked_entity = entity_by_id.get(linked_chat_id)
            if linked_entity is None:
                try:
                    linked_entity = await client.get_entity(linked_chat_id)
                except Exception:
                    logger.debug("Could not resolve linked chat entity id=%s", linked_chat_id, exc_info=True)
                    continue
            linked_key = _peer_key("linked_chat", linked_chat_id)
            store.upsert_source(
                peer_key=linked_key,
                entity_id=linked_chat_id,
                title=(getattr(linked_entity, "title", None) or "Unnamed linked chat").strip(),
                username=getattr(linked_entity, "username", None),
                kind="linked_chat",
                linked_channel_key=linked_chat_to_channel[linked_chat_id],
            )
            tracked_sources += 1
            target_entities.append((linked_key, linked_entity, None))

        for peer_key, entity, _ in target_entities:
            last_message_id = store.last_message_id(peer_key)
            latest_seen = last_message_id
            async for message in client.iter_messages(entity, min_id=last_message_id, reverse=True, limit=collect_limit):
                text = (getattr(message, "message", None) or "").strip()
                if not text:
                    continue
                entity_username = getattr(entity, "username", None)
                link = f"https://t.me/{entity_username}/{message.id}" if entity_username else None
                replies = None
                reply_info = getattr(message, "replies", None)
                if reply_info is not None:
                    replies = getattr(reply_info, "replies", None)
                inserted = store.insert_message(
                    peer_key=peer_key,
                    message_id=int(message.id),
                    posted_at=message.date or _utc_now(),
                    sender_id=getattr(message, "sender_id", None),
                    views=getattr(message, "views", None),
                    forwards=getattr(message, "forwards", None),
                    replies=replies,
                    link=link,
                    text=text,
                    raw_json=message.to_dict(),
                )
                if inserted:
                    collected_messages += 1
                latest_seen = max(latest_seen, int(message.id))
            store.mark_collected(peer_key, latest_seen if latest_seen > 0 else None)

    brief = store.render_briefing(window_hours=window_hours)
    brief_target.write_text(brief)
    recent_count = store.recent_message_count(window_hours)
    return {
        "status": "ok",
        "should_alert": False,
        "change_type": "collected",
        "summary": (
            f"Collected {collected_messages} new messages across {tracked_sources} sources. "
            f"Recent window contains {recent_count} messages."
        ),
        "payload": {
            "brief_path": str(brief_target),
            "collected_messages": collected_messages,
            "tracked_sources": tracked_sources,
            "recent_messages": recent_count,
        },
    }


def collect_digest_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(collect_digest(**kwargs))

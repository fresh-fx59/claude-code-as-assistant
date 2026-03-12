import sys
import sqlite3
import types
from datetime import datetime, timedelta, timezone

import pytest

from src.telegram_digest import collect_digest
from src.telegram_digest import TelegramDigestStore


def test_render_briefing_groups_recent_channel_and_linked_chat_messages(tmp_path) -> None:
    store = TelegramDigestStore(tmp_path / "telegram-digest.db")
    store.upsert_source(
        peer_key="channel:1",
        entity_id=1,
        title="Channel One",
        username="channel_one",
        kind="channel",
        linked_channel_key=None,
    )
    store.upsert_source(
        peer_key="linked_chat:2",
        entity_id=2,
        title="Channel One Chat",
        username=None,
        kind="linked_chat",
        linked_channel_key="channel:1",
    )
    now = datetime.now(timezone.utc)
    store.insert_message(
        peer_key="channel:1",
        message_id=101,
        posted_at=now - timedelta(hours=2),
        sender_id=10,
        views=123,
        forwards=4,
        replies=2,
        link="https://t.me/channel_one/101",
        text="Important channel update",
        raw_json={"id": 101},
    )
    store.insert_message(
        peer_key="linked_chat:2",
        message_id=55,
        posted_at=now - timedelta(hours=1),
        sender_id=20,
        views=None,
        forwards=None,
        replies=7,
        link=None,
        text="Discussion about the channel update",
        raw_json={"id": 55},
    )

    briefing = store.render_briefing(window_hours=24, per_source_limit=4, source_limit=10)

    assert "# Telegram digest briefing" in briefing
    assert "## Channel One [channel]" in briefing
    assert "## Channel One Chat [linked_chat]" in briefing
    assert "Important channel update" in briefing
    assert "Discussion about the channel update" in briefing


@pytest.mark.asyncio
async def test_collect_digest_fetches_linked_chat_via_get_entity_when_missing_from_dialogs(
    tmp_path,
    monkeypatch,
) -> None:
    class FakeChannel:
        def __init__(self, entity_id: int, title: str, username: str | None, *, broadcast: bool) -> None:
            self.id = entity_id
            self.title = title
            self.username = username
            self.broadcast = broadcast

    class FakeDialog:
        def __init__(self, entity) -> None:  # noqa: ANN001
            self.entity = entity

    class FakeStringSession:
        def __init__(self, value: str) -> None:
            self.value = value

    class FakeGetFullChannelRequest:
        def __init__(self, entity) -> None:  # noqa: ANN001
            self.entity = entity

    class FakeClient:
        def __init__(self, session, api_id: int, api_hash: str) -> None:  # noqa: ANN001
            self.session = session
            self.api_id = api_id
            self.api_hash = api_hash
            self.channel = FakeChannel(100, "Main Channel", "main_channel", broadcast=True)
            self.linked_chat = FakeChannel(200, "Main Channel Chat", None, broadcast=False)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # noqa: ANN001
            return False

        async def iter_dialogs(self, limit: int):  # noqa: ARG002
            yield FakeDialog(self.channel)

        async def __call__(self, request):  # noqa: ANN001
            assert isinstance(request, FakeGetFullChannelRequest)
            return types.SimpleNamespace(full_chat=types.SimpleNamespace(linked_chat_id=200))

        async def get_entity(self, entity_id: int):
            assert entity_id == 200
            return self.linked_chat

        async def iter_messages(self, entity, min_id: int, reverse: bool, limit: int):  # noqa: ANN001, ARG002
            if False:
                yield entity  # pragma: no cover

    telethon_mod = types.ModuleType("telethon")
    telethon_mod.TelegramClient = FakeClient
    telethon_sessions_mod = types.ModuleType("telethon.sessions")
    telethon_sessions_mod.StringSession = FakeStringSession
    telethon_channels_mod = types.ModuleType("telethon.tl.functions.channels")
    telethon_channels_mod.GetFullChannelRequest = FakeGetFullChannelRequest
    telethon_types_mod = types.ModuleType("telethon.tl.types")
    telethon_types_mod.Channel = FakeChannel

    monkeypatch.setitem(sys.modules, "telethon", telethon_mod)
    monkeypatch.setitem(sys.modules, "telethon.sessions", telethon_sessions_mod)
    monkeypatch.setitem(sys.modules, "telethon.tl.functions.channels", telethon_channels_mod)
    monkeypatch.setitem(sys.modules, "telethon.tl.types", telethon_types_mod)

    monkeypatch.setattr("src.telegram_digest.config.TELEGRAM_USER_API_ID", 1)
    monkeypatch.setattr("src.telegram_digest.config.TELEGRAM_USER_API_HASH", "hash")
    monkeypatch.setattr("src.telegram_digest.config.TELEGRAM_USER_SESSION", "")
    monkeypatch.setattr("src.telegram_digest.config.TELEGRAM_USER_SESSION_PATH", tmp_path / "telethon_user")

    db_path = tmp_path / "digest.db"
    brief_path = tmp_path / "brief.md"
    payload = await collect_digest(db_path=db_path, brief_path=brief_path, source_limit=10, collect_limit=10)

    assert payload["status"] == "ok"
    con = sqlite3.connect(db_path)
    row = con.execute(
        "SELECT linked_channel_key FROM digest_sources WHERE peer_key = 'linked_chat:200'"
    ).fetchone()
    assert row is not None
    assert row[0] == "channel:100"

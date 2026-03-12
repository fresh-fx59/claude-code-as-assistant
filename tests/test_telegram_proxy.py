import types
from datetime import datetime, timezone

import pytest

from src.telegram_proxy import _json_safe
from src.telegram_proxy import TelegramProxy


def test_json_safe_converts_datetime_values() -> None:
    payload = {
        "when": datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc),
        "nested": [{"at": datetime(2026, 3, 12, 14, 1, tzinfo=timezone.utc)}],
    }

    result = _json_safe(payload)

    assert result["when"] == "2026-03-12 14:00:00+00:00"
    assert result["nested"][0]["at"] == "2026-03-12 14:01:00+00:00"


@pytest.mark.asyncio
async def test_read_messages_recent_first_omits_zero_min_id(monkeypatch) -> None:
    seen_kwargs: dict[str, object] = {}

    class FakeClient:
        async def iter_messages(self, entity, **kwargs):  # noqa: ARG002
            seen_kwargs.update(kwargs)
            yield types.SimpleNamespace(
                id=42,
                date=datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc),
                sender_id=10,
                views=123,
                forwards=4,
                replies=None,
                username=None,
                message="Recent message",
                to_dict=lambda: {"id": 42},
            )

    proxy = TelegramProxy()
    proxy._client = FakeClient()
    proxy._entity_cache[("linked_chat", 123)] = types.SimpleNamespace(username="chat_name")

    async def fake_resolve_entity(**kwargs):  # noqa: ARG001
        return proxy._entity_cache[("linked_chat", 123)]

    monkeypatch.setattr(proxy, "_resolve_entity", fake_resolve_entity)

    messages = await proxy.read_messages(
        kind="linked_chat",
        entity_id=123,
        min_id=0,
        limit=5,
        recent_first=True,
    )

    assert "min_id" not in seen_kwargs
    assert seen_kwargs["reverse"] is False
    assert seen_kwargs["limit"] == 5
    assert messages[0]["message_id"] == 42

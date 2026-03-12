from datetime import datetime, timezone

from src.telegram_proxy import _json_safe


def test_json_safe_converts_datetime_values() -> None:
    payload = {
        "when": datetime(2026, 3, 12, 14, 0, tzinfo=timezone.utc),
        "nested": [{"at": datetime(2026, 3, 12, 14, 1, tzinfo=timezone.utc)}],
    }

    result = _json_safe(payload)

    assert result["when"] == "2026-03-12 14:00:00+00:00"
    assert result["nested"][0]["at"] == "2026-03-12 14:01:00+00:00"

from pathlib import Path

from src.telegram_digest_tool import _build_daily_prompt


def test_daily_prompt_uses_edge_tts_safe_tool() -> None:
    prompt = _build_daily_prompt(Path("/tmp/brief.md"))

    assert "[[SCHEDULE_DELIVER]]" in prompt
    assert "USE_TOOL: edge-tts-safe" in prompt
    assert "USE_TOOL: sag" not in prompt
    assert "Samarin" in prompt
    assert "last 24 hours" in prompt
    assert "linked discussion chats" in prompt

from src import tts


def test_prepare_spoken_text_strips_code_and_urls() -> None:
    text = "Привет! ```python\nprint('x')\n``` подробнее на https://example.com и `inline`."
    prepared = tts._prepare_spoken_text(text)
    assert "print('x')" not in prepared
    assert "https://example.com" not in prepared
    assert "inline" not in prepared
    assert "Привет" in prepared


def test_select_voice_prefers_cyrillic(monkeypatch) -> None:
    monkeypatch.setattr(tts, "TTS_VOICE", "auto")
    monkeypatch.setattr(tts, "TTS_VOICE_CYRILLIC", "ru")
    monkeypatch.setattr(tts, "TTS_VOICE_LATIN", "en")
    assert tts._select_voice("Это тест русского текста") == "ru"


def test_select_voice_prefers_latin(monkeypatch) -> None:
    monkeypatch.setattr(tts, "TTS_VOICE", "auto")
    monkeypatch.setattr(tts, "TTS_VOICE_CYRILLIC", "ru")
    monkeypatch.setattr(tts, "TTS_VOICE_LATIN", "en")
    assert tts._select_voice("This is a test sentence in English") == "en"


def test_select_speed_prefers_cyrillic(monkeypatch) -> None:
    monkeypatch.setattr(tts, "TTS_SPEED_CYRILLIC", "170")
    monkeypatch.setattr(tts, "TTS_SPEED_LATIN", "220")
    assert tts._select_speed("Это тест русского текста") == "170"


def test_select_speed_prefers_latin(monkeypatch) -> None:
    monkeypatch.setattr(tts, "TTS_SPEED_CYRILLIC", "170")
    monkeypatch.setattr(tts, "TTS_SPEED_LATIN", "220")
    assert tts._select_speed("This is a test sentence in English") == "220"

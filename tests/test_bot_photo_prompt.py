import pytest

from src import bot


class _MessageStub:
    def __init__(self, text=None, caption=None):
        self.text = text
        self.caption = caption


@pytest.mark.asyncio
async def test_compose_incoming_prompt_without_photo_uses_caption(monkeypatch):
    message = _MessageStub(text=None, caption="look at this")

    async def _fake_download(_m):
        return None

    def _fail_ocr(_path):
        raise AssertionError("OCR should not run for non-image messages")

    monkeypatch.setattr(bot, "_download_photo_attachment", _fake_download)
    monkeypatch.setattr(bot, "extract_ocr_text", _fail_ocr)

    prompt = await bot._compose_incoming_prompt(message)

    assert prompt == "look at this"


@pytest.mark.asyncio
async def test_compose_incoming_prompt_with_photo_appends_local_path(monkeypatch):
    message = _MessageStub(text="what is on the image?", caption=None)

    async def _fake_download(_m):
        return "/tmp/incoming/test.jpg"

    def _fake_ocr(_path):
        return "Total: 1234"

    monkeypatch.setattr(bot, "_download_photo_attachment", _fake_download)
    monkeypatch.setattr(bot, "extract_ocr_text", _fake_ocr)

    prompt = await bot._compose_incoming_prompt(message)

    assert "what is on the image?" in prompt
    assert "User attached an image." in prompt
    assert "Local image path: /tmp/incoming/test.jpg" in prompt
    assert "Local OCR text (best-effort; low-quality images may include misreads):" in prompt
    assert "Total: 1234" in prompt

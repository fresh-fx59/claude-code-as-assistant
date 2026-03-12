"""Shared media directive parsing and Telegram media helpers."""

from __future__ import annotations

import re
import os
import shutil
import tempfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlparse

from aiogram import Bot
from aiogram.types import FSInputFile

_AUDIO_AS_VOICE_TAG_RE = re.compile(r"\[\[\s*audio_as_voice\s*\]\]", re.IGNORECASE)
_MEDIA_LINE_RE = re.compile(r"^\s*MEDIA:\s*(.+?)\s*$", re.IGNORECASE)
_USE_TOOL_LINE_RE = re.compile(r"^\s*USE_TOOL:\s*[A-Za-z0-9_.-]+\s*$", re.IGNORECASE | re.MULTILINE)
_VOICE_COMPATIBLE_EXTENSIONS = {".ogg", ".opus", ".mp3", ".m4a"}
_AUDIO_EXTENSIONS = _VOICE_COMPATIBLE_EXTENSIONS | {".wav", ".aac", ".flac"}


def media_extension(media_ref: str) -> str:
    raw = media_ref.strip().strip("`").strip("\"'")
    if not raw:
        return ""
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        return Path(parsed.path).suffix.lower()
    return Path(raw).suffix.lower()


def is_voice_compatible_media(media_ref: str) -> bool:
    return media_extension(media_ref) in _VOICE_COMPATIBLE_EXTENSIONS


def is_audio_media(media_ref: str) -> bool:
    return media_extension(media_ref) in _AUDIO_EXTENSIONS


def resolve_media_input(media_ref: str):
    raw = media_ref.strip().strip("`").strip("\"'")
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        return raw
    path = Path(raw).expanduser()
    if path.exists() and path.is_file():
        return FSInputFile(path)
    return raw


@asynccontextmanager
async def prepared_media_input(media_ref: str):
    """Yield a stable media handle for Telegram sends.

    Snapshot local files before sending so concurrent conversions that reuse the
    same output path cannot overwrite the bytes while Telegram is opening them.
    """
    raw = media_ref.strip().strip("`").strip("\"'")
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        yield raw
        return

    path = Path(raw).expanduser()
    if not path.exists() or not path.is_file():
        yield raw
        return

    fd, temp_name = tempfile.mkstemp(
        prefix=f".telegram-send-{path.stem}-",
        suffix=path.suffix,
        dir=str(path.parent),
    )
    temp_path = Path(temp_name)
    try:
        os.close(fd)
        shutil.copyfile(path, temp_path)
        yield FSInputFile(temp_path, filename=path.name)
    finally:
        with suppress(FileNotFoundError):
            temp_path.unlink()


def extract_media_directives(text: str) -> tuple[str, list[str], bool]:
    if not text:
        return "", [], False

    audio_as_voice = bool(_AUDIO_AS_VOICE_TAG_RE.search(text))
    without_tag = _AUDIO_AS_VOICE_TAG_RE.sub("", text)

    media_refs: list[str] = []
    text_lines: list[str] = []
    for line in without_tag.splitlines():
        match = _MEDIA_LINE_RE.match(line)
        if match:
            media = match.group(1).strip().strip("`").strip("\"'")
            if media:
                media_refs.append(media)
            continue
        text_lines.append(line)

    cleaned_text = "\n".join(text_lines).strip()
    return cleaned_text, media_refs, audio_as_voice


def strip_tool_directive_lines(text: str) -> str:
    stripped = _USE_TOOL_LINE_RE.sub("", text or "")
    return "\n".join(line for line in (ln.strip() for ln in stripped.splitlines()) if line).strip()


async def send_media(bot: Bot, chat_id: int, message_thread_id: int | None, media_ref: str, *, audio_as_voice: bool) -> None:
    async with prepared_media_input(media_ref) as media_input:
        kwargs = {"chat_id": chat_id}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        if audio_as_voice and is_voice_compatible_media(media_ref):
            await bot.send_voice(voice=media_input, **kwargs)
            return
        if is_audio_media(media_ref):
            await bot.send_audio(audio=media_input, **kwargs)
            return
        await bot.send_document(document=media_input, **kwargs)

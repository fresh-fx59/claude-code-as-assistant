"""Local text-to-speech helpers for Telegram voice bubbles."""

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_LOCAL_BIN = Path.home() / "local" / "bin"

TTS_BIN: str = os.getenv(
    "LOCAL_TTS_BIN",
    shutil.which("espeak") or shutil.which("espeak-ng") or str(_LOCAL_BIN / "espeak"),
)
TTS_VOICE: str = os.getenv("LOCAL_TTS_VOICE", "en")
TTS_SPEED: str = os.getenv("LOCAL_TTS_SPEED_WPM", "200")
FFMPEG_BIN: str = shutil.which("ffmpeg") or str(_LOCAL_BIN / "ffmpeg")


def is_available() -> bool:
    return (
        os.path.isfile(TTS_BIN)
        and os.access(TTS_BIN, os.X_OK)
        and os.path.isfile(FFMPEG_BIN)
        and os.access(FFMPEG_BIN, os.X_OK)
    )


async def synthesize_voice(text: str) -> str:
    """Synthesize text to OGG/Opus suitable for Telegram sendVoice."""
    spoken_text = (text or "").strip()
    if not spoken_text:
        raise RuntimeError("Cannot synthesize empty text")

    tmp_dir = Path(tempfile.mkdtemp(prefix="ila_tts_"))
    wav_path = tmp_dir / "speech.wav"
    ogg_path = tmp_dir / "speech.ogg"

    try:
        tts_proc = await asyncio.create_subprocess_exec(
            TTS_BIN,
            "--stdin",
            "-v",
            TTS_VOICE,
            "-s",
            TTS_SPEED,
            "-w",
            str(wav_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, tts_stderr = await tts_proc.communicate(spoken_text.encode("utf-8"))
        if tts_proc.returncode != 0:
            raise RuntimeError(f"TTS synthesis failed: {tts_stderr.decode()[-200:]}")

        ffmpeg_proc = await asyncio.create_subprocess_exec(
            FFMPEG_BIN,
            "-y",
            "-i",
            str(wav_path),
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            "-vbr",
            "on",
            "-compression_level",
            "10",
            "-application",
            "voip",
            "-ar",
            "48000",
            "-ac",
            "1",
            str(ogg_path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, ffmpeg_stderr = await ffmpeg_proc.communicate()
        if ffmpeg_proc.returncode != 0:
            raise RuntimeError(f"ffmpeg audio conversion failed: {ffmpeg_stderr.decode()[-200:]}")

        if not ogg_path.exists():
            raise RuntimeError("TTS output file was not generated")

        cleanup_file(str(wav_path))
        return str(ogg_path)
    except Exception:
        cleanup_file(str(ogg_path))
        cleanup_file(str(wav_path))
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
        raise


def cleanup_file(path: str) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except Exception:
        logger.debug("Failed to cleanup temporary TTS file: %s", path)

"""Offline OCR helpers used for image-attached Telegram turns."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

_DEFAULT_LANG = "rus+eng"
_DEFAULT_TIMEOUT_SEC = 12.0
_DEFAULT_MAX_CHARS = 2000


def extract_ocr_text(
    image_path: str | Path,
    *,
    lang: str = _DEFAULT_LANG,
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> str:
    """Run local Tesseract OCR and return best-effort extracted text."""
    path = Path(image_path)
    if not path.exists():
        return ""

    tesseract_bin = shutil.which("tesseract")
    if not tesseract_bin:
        return ""

    cmd = [
        tesseract_bin,
        str(path),
        "stdout",
        "-l",
        lang,
        "--oem",
        "1",
        "--psm",
        "6",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except Exception:
        return ""

    if proc.returncode != 0:
        return ""

    text = _normalize_ocr_text(proc.stdout)
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _normalize_ocr_text(raw: str) -> str:
    text = raw.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

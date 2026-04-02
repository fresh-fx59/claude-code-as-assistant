from __future__ import annotations

import re

_STALE_CODEX_SESSION_ERROR_PATTERNS = (
    re.compile(r"thread/resume", re.IGNORECASE),
    re.compile(r"no rollout found for thread id", re.IGNORECASE),
    re.compile(r"resume failed", re.IGNORECASE),
    re.compile(r"thread id\s+[0-9a-f-]{8,}.*not found", re.IGNORECASE),
)


def is_stale_codex_session_error(text: str | None) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in _STALE_CODEX_SESSION_ERROR_PATTERNS)

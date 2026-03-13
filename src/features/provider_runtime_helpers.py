from __future__ import annotations

import re

from .. import bridge


def is_transient_codex_error(
    text: str | None,
    *,
    patterns: tuple[re.Pattern[str], ...],
) -> bool:
    if not text:
        return False
    return any(pattern.search(text) for pattern in patterns)


def sanitize_transient_codex_error_response(
    response: bridge.ClaudeResponse,
    *,
    attempts: int,
) -> bridge.ClaudeResponse:
    return bridge.ClaudeResponse(
        text=(
            "The Codex stream disconnected repeatedly and did not recover after "
            f"{attempts} attempt(s). Please retry."
        ),
        session_id=response.session_id,
        is_error=True,
        cost_usd=response.cost_usd,
        duration_ms=response.duration_ms,
        num_turns=response.num_turns,
        cancelled=response.cancelled,
        idle_timeout=response.idle_timeout,
    )

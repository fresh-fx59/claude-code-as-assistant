from __future__ import annotations

from .. import config
from ..memory import MemoryManager


def truncate_label(text: str, max_len: int = 52) -> str:
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


def truncate_output(text: str, max_len: int = 2000) -> str:
    if len(text) <= max_len:
        return text
    remaining = len(text) - max_len
    return f"{text[:max_len]}\n... ({remaining} chars omitted)"


def as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


def inject_tool_request(prompt_text: str, tool_name: str) -> str:
    base = prompt_text.rstrip()
    return f"{base}\n\nUSE_TOOL: {tool_name}\n"


def default_timezone_name() -> str:
    try:
        manager = MemoryManager(config.MEMORY_DIR)
        return manager.get_timezone(default="UTC")
    except Exception:
        return "UTC"


def strip_markdown_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 2:
            return "\n".join(lines[1:-1]).strip()
    return stripped


def weekday_to_int(name: str) -> int | None:
    mapping = {
        "mon": 0,
        "monday": 0,
        "tue": 1,
        "tues": 1,
        "tuesday": 1,
        "wed": 2,
        "wednesday": 2,
        "thu": 3,
        "thur": 3,
        "thurs": 3,
        "thursday": 3,
        "fri": 4,
        "friday": 4,
        "sat": 5,
        "saturday": 5,
        "sun": 6,
        "sunday": 6,
    }
    return mapping.get(name.strip().lower())

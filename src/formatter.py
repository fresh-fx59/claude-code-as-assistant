import re

MAX_MESSAGE_LENGTH = 4096


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_text_segment(text: str) -> str:
    """Format markdown in plain-text segments (outside fenced code blocks)."""
    if not text:
        return text

    escaped = _escape_html(text)

    # Protect inline code before other markdown substitutions.
    code_tokens: list[str] = []

    def _inline_code(match: re.Match[str]) -> str:
        code_tokens.append(f"<code>{match.group(1)}</code>")
        return f"\u0000CODE{len(code_tokens) - 1}\u0000"

    escaped = re.sub(r"`([^`\n]+?)`", _inline_code, escaped)

    # Headings: #, ##, ### (convert to bold with emoji prefix)
    def _heading_replace(match: re.Match[str]) -> str:
        level = len(match.group(1))
        heading_text = match.group(2).strip()
        if level == 1:
            return f"\n<b>📍 {heading_text}</b>\n"
        if level == 2:
            return f"\n<b>▫️ {heading_text}</b>"
        return f"\n<b>• {heading_text}</b>"

    escaped = re.sub(r"(?m)^(#{1,3})\s+(.+)$", _heading_replace, escaped)

    # Emphasis and strikethrough (including multiline content).
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"__(.+?)__", r"<b>\1</b>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"~~(.+?)~~", r"<s>\1</s>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"(?<!\w)\*([^\*]+?)\*(?!\w)", r"<i>\1</i>", escaped, flags=re.DOTALL)
    escaped = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", escaped, flags=re.DOTALL)

    # Restore inline code tokens.
    for i, token in enumerate(code_tokens):
        escaped = escaped.replace(f"\u0000CODE{i}\u0000", token)

    return escaped


def markdown_to_html(text: str) -> str:
    """Convert Claude's markdown output to Telegram-compatible HTML."""
    lines = text.split("\n")
    parts: list[str] = []
    buffer: list[str] = []
    in_code_block = False
    code_lang = ""
    code_lines: list[str] = []

    for line in lines:
        if re.match(r"^```", line):
            if not in_code_block:
                if buffer:
                    parts.append(_format_text_segment("\n".join(buffer)))
                    buffer = []
                in_code_block = True
                code_lang = line[3:].strip()
                code_lines = []
            else:
                code_content = _escape_html("\n".join(code_lines))
                if code_lang:
                    parts.append(
                        f'<pre><code class="language-{_escape_html(code_lang)}">'
                        f"{code_content}</code></pre>"
                    )
                else:
                    parts.append(f"<pre><code>{code_content}</code></pre>")
                in_code_block = False
                code_lang = ""
                code_lines = []
            continue

        if in_code_block:
            code_lines.append(line)
        else:
            buffer.append(line)

    if in_code_block:
        code_content = _escape_html("\n".join(code_lines))
        parts.append(f"<pre><code>{code_content}</code></pre>")
    elif buffer:
        parts.append(_format_text_segment("\n".join(buffer)))

    return "\n".join(parts)


def split_message(text: str) -> list[str]:
    """Split text into chunks that fit Telegram's message limit."""
    if text == "":
        return [""]
    if re.search(r"<[^>]+>", text) and len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    def _split_oversized(chunk: str) -> list[str]:
        if len(chunk) <= MAX_MESSAGE_LENGTH:
            return [chunk]

        out: list[str] = []
        remaining = chunk
        while remaining:
            if len(remaining) <= MAX_MESSAGE_LENGTH:
                out.append(remaining)
                break
            split_at = remaining.rfind(" ", 0, MAX_MESSAGE_LENGTH + 1)
            if split_at <= 0:
                split_at = MAX_MESSAGE_LENGTH
            out.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip(" ")
        return out

    if "\n\n" in text:
        units = text.split("\n\n")
    elif "\n" in text:
        units = text.split("\n")
    else:
        units = [text]

    chunks: list[str] = []
    for unit in units:
        for piece in _split_oversized(unit):
            trimmed = piece.strip()
            if trimmed:
                chunks.append(trimmed)

    return chunks or [""]


def strip_html(text: str) -> str:
    """Remove HTML tags for plain-text fallback."""
    return re.sub(r"<[^>]+>", "", text)

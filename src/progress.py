import asyncio
import logging
from collections import deque
from typing import Optional

from aiogram.types import Message
from aiogram.exceptions import TelegramAPIError

from . import config

logger = logging.getLogger(__name__)


class ProgressReporter:
    """Manages a single editable Telegram message showing Claude's current activity.

    Shows recent tool actions (Reading, Editing, Running, etc.) with debounced edits
    to avoid hitting Telegram rate limits.
    """

    def __init__(self, message: Message, debounce_seconds: float | None = None):
        self._message = message
        self._chat_id = message.chat.id
        self._bot = message.bot
        self._debounce_seconds = debounce_seconds or config.PROGRESS_DEBOUNCE_SECONDS

        self._progress_message_id: int | None = None
        self._history: deque[str] = deque(maxlen=5)  # Keep last ~5 actions
        self._last_update_text: str = ""
        self._dirty: bool = False
        self._task: asyncio.Task | None = None
        self._shutdown: bool = False

    async def report_tool(self, tool_name: str, tool_input: str | None) -> None:
        """Report a tool action being performed.

        Args:
            tool_name: Name of the tool (e.g., "Bash", "Read", "Edit")
            tool_input: Primary argument (e.g., command, file_path, pattern)
        """
        # Translate tool events to human-readable lines
        text = self._format_tool_action(tool_name, tool_input)

        # Skip if this is a duplicate of the most recent action
        if self._history and self._history[-1] == text:
            return

        self._history.append(text)
        self._dirty = True

        # Cancel any pending update task and start a new debounced one
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = asyncio.create_task(self._debounced_update())

    def _format_tool_action(self, tool_name: str, tool_input: str | None) -> str:
        """Format a tool action into a human-readable line."""
        tool_name = tool_name.lower()
        match tool_name:
            case "bash":
                prefix = "Running"
            case "read":
                prefix = "Reading"
            case "edit":
                prefix = "Editing"
            case "write":
                prefix = "Writing"
            case "grep" | "glob":
                prefix = "Searching"
            case "task":
                prefix = "Delegating task"
            case "askuserquestion":
                prefix = "Waiting for input"
            case "skill":
                prefix = "Running skill"
            case "enterplanmode":
                prefix = "Planning"
            case "exitplanmode":
                prefix = "Approving plan"
            case _:
                prefix = f"Using {tool_name}"

        if tool_input:
            return f"{prefix}: {tool_input}"
        return f"{prefix}..."

    async def _debounced_update(self) -> None:
        """Debounced update of the progress message.

        Wait for the debounce period, then update if there are still uncommitted changes.
        """
        try:
            await asyncio.sleep(self._debounce_seconds)

            if self._shutdown:
                return

            if not self._dirty:
                return

            # Build the message text
            if self._history:
                lines = list(self._history)
                text = "🔄 <b>Working...</b>\n" + "\n".join(f"• {line}" for line in lines)
            else:
                text = "🔄 <b>Working...</b>"

            # Only update if text changed
            if text == self._last_update_text:
                self._dirty = False
                return

            self._last_update_text = text
            self._dirty = False

            if self._progress_message_id is None:
                # Send new message
                try:
                    msg = await self._bot.send_message(
                        chat_id=self._chat_id,
                        text=text,
                        parse_mode="HTML",
                    )
                    self._progress_message_id = msg.message_id
                except TelegramAPIError as e:
                    logger.warning("Failed to send progress message: %s", e)
            else:
                # Edit existing message
                try:
                    await self._bot.edit_message_text(
                        chat_id=self._chat_id,
                        message_id=self._progress_message_id,
                        text=text,
                        parse_mode="HTML",
                    )
                except TelegramAPIError as e:
                    # MessageNotModified is harmless, other errors log warning
                    if "message is not modified" not in str(e).lower():
                        logger.warning("Failed to update progress message: %s", e)

        except asyncio.CancelledError:
            # Task was cancelled by a newer one
            pass

    async def finish(self) -> None:
        """Clean up the progress message after final response is sent.

        Deletes the progress message if it exists.
        """
        self._shutdown = True

        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        if self._progress_message_id is not None:
            try:
                await self._bot.delete_message(
                    chat_id=self._chat_id,
                    message_id=self._progress_message_id,
                )
            except TelegramAPIError as e:
                # Message might have been deleted already or doesn't exist
                logger.debug("Could not delete progress message: %s", e)

    async def show_cancelled(self) -> None:
        """Update the progress message to show cancellation before deletion."""
        self._shutdown = True

        if self._progress_message_id is not None:
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._progress_message_id,
                    text="❌ <b>Request cancelled</b>",
                    parse_mode="HTML",
                )
                # Small delay so the user sees the cancellation message
                await asyncio.sleep(1)
            except TelegramAPIError:
                pass

    async def show_idle_timeout(self) -> None:
        """Update the progress message to show idle timeout before deletion."""
        self._shutdown = True

        if self._progress_message_id is not None:
            try:
                await self._bot.edit_message_text(
                    chat_id=self._chat_id,
                    message_id=self._progress_message_id,
                    text="⏱️ <b>Timed out</b> — Claude stopped producing output",
                    parse_mode="HTML",
                )
                await asyncio.sleep(1)
            except TelegramAPIError:
                pass
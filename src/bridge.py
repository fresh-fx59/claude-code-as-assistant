import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator

from . import config, metrics

logger = logging.getLogger(__name__)


class StreamEventType(Enum):
    """Types of events that can be streamed from Claude."""
    TOOL_START = "tool_start"
    TOOL_INPUT = "tool_input"
    TEXT_DELTA = "text_delta"
    RESULT = "result"
    ERROR = "error"


@dataclass
class StreamEvent:
    """A single event from the Claude stream."""
    event_type: StreamEventType
    tool_name: str | None = None
    tool_input: str | None = None
    text: str | None = None
    response: "ClaudeResponse | None" = None


@dataclass
class ClaudeResponse:
    text: str
    session_id: str | None
    is_error: bool
    cost_usd: float
    duration_ms: float = 0
    num_turns: int = 0
    cancelled: bool = False
    idle_timeout: bool = False


def _try_extract_tool_input(tool_name: str, partial_json: str) -> str | None:
    """Try to extract a meaningful tool input from partial JSON.

    For different tools, we try to extract the primary argument:
    - Bash: command
    - Read/Edit/Write: file_path
    - Grep: pattern
    - Glob: pattern
    - Task, AskUserQuestion: description
    """
    tool_name = tool_name.lower()

    # Try to parse as JSON and extract the relevant field
    try:
        data = json.loads(partial_json)
        match tool_name:
            case "bash":
                return data.get("command")
            case "read" | "edit" | "write":
                return data.get("file_path")
            case "grep" | "glob":
                return data.get("pattern")
            case "task" | "askuserquestion":
                return data.get("description")
            case _:
                # For other tools, return the whole json compactly
                return json.dumps(data, separators=(",", ":"))
    except json.JSONDecodeError:
        pass

    # Fallback: use regex to extract the most common fields
    match tool_name:
        case "bash":
            m = re.search(r'"command"\s*:\s*"([^"]+)', partial_json)
            if m:
                return m.group(1)[:50] + "..." if len(m.group(1)) > 50 else m.group(1)
        case "read" | "edit" | "write":
            m = re.search(r'"file_path"\s*:\s*"([^"]+)', partial_json)
            if m:
                return m.group(1)
        case "grep" | "glob":
            m = re.search(r'"pattern"\s*:\s*"([^"]+)', partial_json)
            if m:
                return m.group(1)[:40] + "..." if len(m.group(1)) > 40 else m.group(1)
        case "task" | "askuserquestion":
            m = re.search(r'"description"\s*:\s*"([^"]+)', partial_json)
            if m:
                return m.group(1)[:40] + "..." if len(m.group(1)) > 40 else m.group(1)

    # If we can't extract anything meaningful, return None
    return None


async def stream_message(
    prompt: str,
    session_id: str | None = None,
    model: str = "sonnet",
    working_dir: str | None = None,
    process_handle: dict | None = None,
) -> AsyncGenerator[StreamEvent, None]:
    """Stream Claude's response as events with idle timeout.

    Yields StreamEvent objects as they occur during processing.
    The idle timeout only triggers when Claude stops producing output.

    Args:
        prompt: The message to send to Claude
        session_id: Optional session ID for conversation continuity
        model: Model to use (sonnet/opus/haiku)
        working_dir: Working directory for file operations
        process_handle: Optional dict to store the process handle for cancellation.
                       Will be populated with {"proc": proc} if provided.
    """
    cmd = [
        "claude",
        "-p",
        prompt,
        "--output-format", "json",
        "--model", model,
        "--dangerously-skip-permissions",
    ]
    if session_id:
        cmd.extend(["--resume", session_id])

    logger.info("Running: %s", " ".join(cmd[:6]) + " ...")

    start = time.monotonic()
    current_tool: str | None = None
    accumulated_input_json: str = ""

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=working_dir,
    )

    if process_handle is not None:
        process_handle["proc"] = proc

    try:
        while True:
            try:
                line = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=config.IDLE_TIMEOUT
                )
            except asyncio.TimeoutError:
                # Idle timeout - kill the process
                logger.warning("Claude process idle timeout (%d s)", config.IDLE_TIMEOUT)
                proc.kill()
                await proc.wait()
                elapsed = time.monotonic() - start
                metrics.CLAUDE_REQUESTS_TOTAL.labels(model=model, status="timeout").inc()
                metrics.CLAUDE_RESPONSE_DURATION.labels(model=model).observe(elapsed)
                yield StreamEvent(
                    event_type=StreamEventType.RESULT,
                    response=ClaudeResponse(
                        text="Request idle timed out. Claude stopped producing output.",
                        session_id=session_id,
                        is_error=True,
                        cost_usd=0,
                        duration_ms=elapsed * 1000,
                        num_turns=0,
                        idle_timeout=True,
                    )
                )
                return

            if not line:
                break

            line_str = line.decode().strip()
            if not line_str:
                continue

            try:
                data = json.loads(line_str)
                event_type = data.get("type")

                if event_type == "content_block_start":
                    # A tool is being started
                    block = data.get("content_block", {})
                    if block.get("type") == "tool_use":
                        tool_name = block.get("name")
                        current_tool = tool_name
                        accumulated_input_json = ""
                        yield StreamEvent(
                            event_type=StreamEventType.TOOL_START,
                            tool_name=tool_name,
                        )

                elif event_type == "content_block_delta":
                    # Tool input is being streamed
                    if current_tool:
                        delta = data.get("delta", {})
                        if "input_json_delta" in delta:
                            accumulated_input_json += delta["input_json_delta"]
                            # Try to extract meaningful input
                            tool_input = _try_extract_tool_input(current_tool, accumulated_input_json)
                            if tool_input:
                                yield StreamEvent(
                                    event_type=StreamEventType.TOOL_INPUT,
                                    tool_name=current_tool,
                                    tool_input=tool_input,
                                )
                        elif "text" in delta:
                            # Regular text delta
                            yield StreamEvent(
                                event_type=StreamEventType.TEXT_DELTA,
                                text=delta["text"],
                            )

                elif event_type == "result":
                    # Final result
                    result_text = data.get("result", "")
                    is_error = bool(data.get("is_error"))
                    cost_usd = float(data.get("total_cost_usd", 0))
                    num_turns = int(data.get("num_turns", 0))

                    if is_error:
                        result_text = result_text or "Claude returned an error."

                    # Record metrics
                    status = "error" if is_error else "success"
                    metrics.CLAUDE_REQUESTS_TOTAL.labels(model=model, status=status).inc()
                    elapsed = time.monotonic() - start
                    metrics.CLAUDE_RESPONSE_DURATION.labels(model=model).observe(elapsed)
                    if cost_usd > 0:
                        metrics.CLAUDE_COST_USD.labels(model=model).inc(cost_usd)
                    if num_turns > 0:
                        metrics.CLAUDE_TURNS_TOTAL.labels(model=model).inc(num_turns)

                    yield StreamEvent(
                        event_type=StreamEventType.RESULT,
                        response=ClaudeResponse(
                            text=result_text,
                            session_id=data.get("session_id", session_id),
                            is_error=is_error,
                            cost_usd=cost_usd,
                            duration_ms=float(data.get("duration_ms", 0)),
                            num_turns=num_turns,
                        )
                    )
                    return

            except json.JSONDecodeError:
                logger.warning("Failed to parse stream line: %s", line_str[:100])

        # Process exited without result event
        elapsed = time.monotonic() - start
        stderr = (await proc.stderr.read()).decode()
        if stderr:
            logger.warning("Claude stderr: %s", stderr.strip())

        if proc.returncode != 0:
            metrics.CLAUDE_REQUESTS_TOTAL.labels(model=model, status="error").inc()
            metrics.CLAUDE_RESPONSE_DURATION.labels(model=model).observe(elapsed)
            yield StreamEvent(
                event_type=StreamEventType.RESULT,
                response=ClaudeResponse(
                    text=f"Error: {stderr.strip() or f'Claude exited with code {proc.returncode}'}",
                    session_id=session_id,
                    is_error=True,
                    cost_usd=0,
                    duration_ms=0,
                    num_turns=0,
                )
            )
        else:
            # Treat as error since we didn't get a proper result
            metrics.CLAUDE_REQUESTS_TOTAL.labels(model=model, status="error").inc()
            metrics.CLAUDE_RESPONSE_DURATION.labels(model=model).observe(elapsed)
            yield StreamEvent(
                event_type=StreamEventType.RESULT,
                response=ClaudeResponse(
                    text="Claude process exited without producing a result.",
                    session_id=session_id,
                    is_error=True,
                    cost_usd=0,
                    duration_ms=0,
                    num_turns=0,
                )
            )

    except Exception:
        logger.exception("Unexpected error in stream_message")
        yield StreamEvent(
            event_type=StreamEventType.RESULT,
            response=ClaudeResponse(
                text="An unexpected error occurred while processing your request.",
                session_id=session_id,
                is_error=True,
                cost_usd=0,
                duration_ms=0,
                num_turns=0,
            )
        )
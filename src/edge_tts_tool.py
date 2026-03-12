"""Safe wrapper around edge-tts that never exposes partially written output."""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence


DEFAULT_MIN_BYTES = 4096
DEFAULT_VOICE = "ru-RU-SvetlanaNeural"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="edge-tts-safe")
    sub = parser.add_subparsers(dest="command", required=True)

    speak = sub.add_parser("speak", help="Synthesize text to audio via edge-tts.")
    source = speak.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Inline text to synthesize.")
    source.add_argument("--file", help="Path to a UTF-8 text file to synthesize.")
    speak.add_argument("-o", "--output", required=True, help="Final output media path.")
    speak.add_argument("--voice", default=os.getenv("EDGE_TTS_VOICE", DEFAULT_VOICE))
    speak.add_argument("--rate", default=os.getenv("EDGE_TTS_RATE", "-12%"))
    speak.add_argument("--volume", default=os.getenv("EDGE_TTS_VOLUME", "+0%"))
    speak.add_argument("--pitch", default=os.getenv("EDGE_TTS_PITCH", "+0Hz"))
    speak.add_argument(
        "--min-bytes",
        type=int,
        default=DEFAULT_MIN_BYTES,
        help="Minimum acceptable output size in bytes.",
    )
    speak.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output metadata format.",
    )
    return parser


def _resolve_edge_tts_command() -> list[str]:
    if importlib.util.find_spec("edge_tts") is not None:
        return [sys.executable, "-m", "edge_tts"]

    executable = shutil.which("edge-tts") or shutil.which("edge_tts")
    if executable:
        return [executable]

    raise RuntimeError(
        "edge-tts is not installed in the current environment. "
        "Install it in the repo venv, for example: ./venv/bin/pip install edge-tts"
    )


def _ffprobe_duration_seconds(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None

    raw = result.stdout.strip()
    if not raw:
        return None

    try:
        return float(raw)
    except ValueError:
        return None


def _validate_output(path: Path, min_bytes: int) -> dict[str, object]:
    if not path.exists():
        raise RuntimeError(f"edge-tts completed without producing output: {path}")

    size_bytes = path.stat().st_size
    if size_bytes < min_bytes:
        raise RuntimeError(
            f"edge-tts produced suspiciously small output ({size_bytes} bytes < {min_bytes} bytes)"
        )

    duration_seconds = _ffprobe_duration_seconds(path)
    if duration_seconds is not None and duration_seconds <= 0:
        raise RuntimeError(f"edge-tts produced invalid audio duration for {path}")

    return {
        "path": str(path),
        "size_bytes": size_bytes,
        "duration_seconds": duration_seconds,
    }


def _edge_tts_args(args: argparse.Namespace, temp_output: Path) -> list[str]:
    command = [*_resolve_edge_tts_command()]
    if args.text is not None:
        command.extend(["--text", args.text])
    else:
        command.extend(["--file", str(Path(args.file).expanduser().resolve())])
    command.extend(
        [
            "--voice",
            str(args.voice),
            "--rate",
            str(args.rate),
            "--volume",
            str(args.volume),
            "--pitch",
            str(args.pitch),
            "--write-media",
            str(temp_output),
        ]
    )
    return command


def _render_metadata(metadata: dict[str, object], output_format: str) -> str:
    if output_format == "text":
        duration = metadata["duration_seconds"]
        duration_text = "unknown" if duration is None else f"{duration:.2f}"
        return (
            f"path={metadata['path']}\n"
            f"size_bytes={metadata['size_bytes']}\n"
            f"duration_seconds={duration_text}"
        )
    return json.dumps(metadata, ensure_ascii=False, indent=2)


def _speak(args: argparse.Namespace) -> int:
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}-",
        suffix=output_path.suffix or ".mp3",
        dir=str(output_path.parent),
    )
    os.close(fd)
    temp_path = Path(temp_name)

    try:
        command = _edge_tts_args(args, temp_path)
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip() or "unknown edge-tts failure"
            raise RuntimeError(stderr)

        metadata = _validate_output(temp_path, args.min_bytes)
        temp_path.replace(output_path)
        metadata["path"] = str(output_path)
        print(_render_metadata(metadata, args.format))
        return 0
    except Exception as exc:
        print(f"edge-tts-safe failed: {exc}", file=sys.stderr)
        return 1
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "speak":
        return _speak(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Tests for the whisper.cpp transcription wrapper."""

import asyncio

import pytest

from src import transcribe


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        on_communicate=None,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._on_communicate = on_communicate

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._on_communicate is not None:
            await self._on_communicate()
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_transcribe_serializes_jobs_and_passes_thread_count(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(transcribe.config, "VOICE_TRANSCRIPTION_MAX_CONCURRENCY", 1)
    monkeypatch.setattr(transcribe.config, "VOICE_TRANSCRIPTION_THREADS", 3)
    transcribe._TRANSCRIPTION_SEMAPHORES.clear()  # noqa: SLF001

    active_subprocesses = 0
    max_active_subprocesses = 0
    whisper_calls: list[tuple[object, ...]] = []

    async def on_communicate() -> None:
        nonlocal active_subprocesses, max_active_subprocesses
        active_subprocesses += 1
        max_active_subprocesses = max(max_active_subprocesses, active_subprocesses)
        await asyncio.sleep(0.02)
        active_subprocesses -= 1

    async def fake_create_subprocess_exec(*args, **kwargs):
        binary = args[0]
        if binary == transcribe.WHISPER_BIN:
            whisper_calls.append(args)
            return _FakeProcess(stdout=b"hello world", on_communicate=on_communicate)
        return _FakeProcess(on_communicate=on_communicate)

    monkeypatch.setattr(transcribe.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    audio_a = tmp_path / "a.oga"
    audio_b = tmp_path / "b.oga"
    audio_a.write_bytes(b"a")
    audio_b.write_bytes(b"b")

    results = await asyncio.gather(
        transcribe.transcribe(str(audio_a)),
        transcribe.transcribe(str(audio_b)),
    )

    assert results == ["hello world", "hello world"]
    assert max_active_subprocesses == 1
    assert len(whisper_calls) == 2
    assert all("-t" in call and "3" in call for call in whisper_calls)

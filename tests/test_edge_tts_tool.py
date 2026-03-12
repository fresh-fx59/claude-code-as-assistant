import json
import subprocess
from pathlib import Path

from src.edge_tts_tool import main as edge_tts_tool_main


def test_edge_tts_tool_writes_temp_file_then_promotes_final_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    final_output = tmp_path / "reply.mp3"
    observed = {}

    def fake_run(cmd, capture_output, text, check):  # noqa: ANN001
        assert capture_output is True
        assert text is True
        assert check is False
        temp_output = Path(cmd[cmd.index("--write-media") + 1])
        observed["temp_output"] = temp_output
        observed["final_exists_during_run"] = final_output.exists()
        temp_output.write_bytes(b"x" * 8192)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("src.edge_tts_tool._resolve_edge_tts_command", lambda: ["edge-tts"])
    monkeypatch.setattr("src.edge_tts_tool.subprocess.run", fake_run)
    monkeypatch.setattr("src.edge_tts_tool._ffprobe_duration_seconds", lambda path: 12.34)

    rc = edge_tts_tool_main(
        [
            "speak",
            "--text",
            "hello",
            "--output",
            str(final_output),
        ]
    )

    assert rc == 0
    assert final_output.exists()
    assert final_output.read_bytes() == b"x" * 8192
    assert observed["temp_output"] != final_output
    assert observed["final_exists_during_run"] is False

    payload = json.loads(capsys.readouterr().out)
    assert payload["path"] == str(final_output)
    assert payload["size_bytes"] == 8192
    assert payload["duration_seconds"] == 12.34


def test_edge_tts_tool_rejects_suspiciously_small_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    final_output = tmp_path / "reply.mp3"

    def fake_run(cmd, capture_output, text, check):  # noqa: ANN001
        temp_output = Path(cmd[cmd.index("--write-media") + 1])
        temp_output.write_bytes(b"tiny")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr("src.edge_tts_tool._resolve_edge_tts_command", lambda: ["edge-tts"])
    monkeypatch.setattr("src.edge_tts_tool.subprocess.run", fake_run)
    monkeypatch.setattr("src.edge_tts_tool._ffprobe_duration_seconds", lambda path: None)

    rc = edge_tts_tool_main(
        [
            "speak",
            "--text",
            "hello",
            "--output",
            str(final_output),
            "--min-bytes",
            "32",
        ]
    )

    assert rc == 1
    assert not final_output.exists()
    assert "suspiciously small output" in capsys.readouterr().err

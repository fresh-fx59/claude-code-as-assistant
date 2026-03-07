import json

from src.memory import MemoryManager
from src.summary_inspector_tool import main as summary_inspector_main


def test_latest_episode_details_includes_linked_worklog(tmp_path, capsys) -> None:
    manager = MemoryManager(tmp_path / "memory")
    episode_id = manager.add_episode(
        chat_id=42,
        message_thread_id=7,
        scope_key="42:7",
        provider="codex",
        session_type="codex",
        session_id="sess-42",
        repo_path="/repo",
        branch="main",
        summary="Fixed summary lookup",
        topics=["memory", "tooling"],
        decisions=["avoid repeated shell calls"],
        entities=["summary-inspector"],
    )
    manager.record_commit_link(
        chat_id=42,
        message_thread_id=7,
        scope_key="42:7",
        provider="codex",
        session_type="codex",
        session_id="sess-42",
        repo_path="/repo",
        branch="main",
        commit_sha="abcdef1234567890",
        short_sha="abcdef1",
        subject="Add summary inspector",
        authored_at="2026-03-07T10:00:00+00:00",
        committed_at="2026-03-07T10:05:00+00:00",
        files=[
            {"path": "src/summary_inspector_tool.py", "additions": 40, "deletions": 0},
        ],
    )

    rc = summary_inspector_main(
        [
            "--memory-dir",
            str(tmp_path / "memory"),
            "latest",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["episode"]["id"] == episode_id
    assert payload["episode"]["summary"] == "Fixed summary lookup"
    assert payload["episode"]["topics"] == ["memory", "tooling"]
    assert payload["worklog"]["scope_key"] == "42:7"
    assert payload["worklog"]["commits"][0]["short_sha"] == "abcdef1"
    assert payload["worklog"]["files"][0]["path"] == "src/summary_inspector_tool.py"


def test_summary_inspector_text_output_without_episodes(tmp_path, capsys) -> None:
    rc = summary_inspector_main(
        [
            "--memory-dir",
            str(tmp_path / "memory"),
            "--format",
            "text",
            "latest",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "empty"

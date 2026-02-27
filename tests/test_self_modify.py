from pathlib import Path
from unittest.mock import patch

import pytest

from src.self_modify import SelfModificationManager


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src" / "plugins").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    return repo


def test_stage_and_promote_plugin(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    manager = SelfModificationManager(repo)

    staged = manager.stage_plugin("tools/sample_plugin.py", "VALUE = 1\n")
    assert staged.exists()
    assert staged.read_text(encoding="utf-8") == "VALUE = 1\n"

    promoted = manager.promote_plugin("tools/sample_plugin.py")
    assert promoted == repo / "src" / "plugins" / "tools" / "sample_plugin.py"
    assert promoted.read_text(encoding="utf-8") == "VALUE = 1\n"


def test_rejects_path_traversal(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    manager = SelfModificationManager(repo)

    with pytest.raises(ValueError, match="traversal"):
        manager.stage_plugin("../escape.py", "bad\n")


def test_validate_invokes_pytest(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    manager = SelfModificationManager(repo)

    with patch("src.self_modify.subprocess.run") as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = "ok\n"
        run_mock.return_value.stderr = ""

        result = manager.validate("tests/test_context_plugins.py")

    assert result.ok is True
    assert "ok" in result.output
    cmd = run_mock.call_args.args[0]
    assert cmd[1:4] == ["-m", "pytest", "-q"]


def test_rollback_uses_good_commit(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    (repo / ".deploy").mkdir(parents=True)
    (repo / ".deploy" / "good_commit").write_text("abc123\n", encoding="utf-8")
    manager = SelfModificationManager(repo)

    with patch("src.self_modify.subprocess.run") as run_mock:
        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = ""
        run_mock.return_value.stderr = ""

        ok, details = manager.rollback_to_good_commit()

    assert ok is True
    assert details == "abc123"
    assert run_mock.call_args.args[0] == ["git", "reset", "--hard", "abc123"]

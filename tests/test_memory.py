from pathlib import Path

import yaml

from src.memory import MemoryManager
from src.memory_tool import main as memory_tool_main


def _write_profile(path: Path, payload: dict) -> None:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def test_profile_is_normalized_with_fact_types(tmp_path: Path) -> None:
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    profile_path = memory_dir / "user_profile.yaml"
    _write_profile(
        profile_path,
        {
            "name": "Alex",
            "preferences": {"timezone": "UTC+3", "languages": ["Russian", "English"]},
            "facts": [
                {
                    "key": "commit_versioning_rule",
                    "value": "Always bump version on every commit",
                    "confidence": 1.0,
                    "source": "explicit",
                    "updated": "2026-03-07",
                }
            ],
        },
    )

    MemoryManager(memory_dir)
    normalized = yaml.safe_load(profile_path.read_text(encoding="utf-8"))

    assert "fact_types" in normalized
    assert normalized["facts"][0]["type"] == "workflow"


def test_build_context_groups_relevant_facts_by_type(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory")
    profile_path = tmp_path / "memory" / "user_profile.yaml"
    _write_profile(
        profile_path,
        {
            "name": "Alex",
            "preferences": {"timezone": "UTC+3", "languages": ["Russian", "English"]},
            "facts": [
                {
                    "key": "location",
                    "value": "Ryazan, Russia",
                    "type": "identity",
                    "confidence": 1.0,
                    "source": "explicit",
                    "updated": "2026-03-07",
                },
                {
                    "key": "feature_apply_commit_push_verify_preference",
                    "value": "After applying a feature, commit and push, then verify",
                    "type": "workflow",
                    "confidence": 1.0,
                    "source": "explicit",
                    "updated": "2026-03-07",
                },
                {
                    "key": "monitoring_server_connection",
                    "value": "ssh user1@45.151.30.146",
                    "type": "infrastructure",
                    "confidence": 1.0,
                    "source": "explicit",
                    "updated": "2026-03-07",
                },
            ],
        },
    )

    context = manager.build_context("continue commit push workflow")

    assert "<relevant_facts>" in context
    assert "[workflow]" in context
    assert "feature_apply_commit_push_verify_preference" in context


def test_build_instructions_require_direct_yaml_edit() -> None:
    manager = MemoryManager(Path("memory"))
    instructions = manager.build_instructions()

    assert "no bash/cat/sed/awk" in instructions
    assert "Allowed fact types:" in instructions


def test_memory_tool_upsert_and_delete(tmp_path: Path, capsys) -> None:
    memory_dir = tmp_path / "memory"
    MemoryManager(memory_dir)

    upsert_rc = memory_tool_main(
        [
            "--memory-dir",
            str(memory_dir),
            "upsert",
            "--key",
            "test_fact",
            "--value",
            "value",
            "--type",
            "workflow",
        ]
    )
    assert upsert_rc == 0
    capsys.readouterr()

    list_rc = memory_tool_main(
        [
            "--memory-dir",
            str(memory_dir),
            "list",
            "--type",
            "workflow",
        ]
    )
    assert list_rc == 0
    listed = capsys.readouterr().out
    assert "test_fact" in listed

    delete_rc = memory_tool_main(
        [
            "--memory-dir",
            str(memory_dir),
            "delete",
            "--key",
            "test_fact",
        ]
    )
    assert delete_rc == 0
    deleted = capsys.readouterr().out
    assert '"removed": true' in deleted

    list_after_delete_rc = memory_tool_main(
        [
            "--memory-dir",
            str(memory_dir),
            "list",
            "--type",
            "workflow",
        ]
    )
    assert list_after_delete_rc == 0
    listed_after_delete = capsys.readouterr().out
    assert "test_fact" not in listed_after_delete

    list_deleted_rc = memory_tool_main(
        [
            "--memory-dir",
            str(memory_dir),
            "list",
            "--type",
            "workflow",
            "--include-deleted",
        ]
    )
    assert list_deleted_rc == 0
    listed_with_deleted = capsys.readouterr().out
    assert "test_fact" in listed_with_deleted


def test_upsert_append_and_replace_modes(tmp_path: Path) -> None:
    manager = MemoryManager(tmp_path / "memory")
    manager.upsert_fact(
        key="environment",
        value="staging",
        fact_type="operation",
        mode="append",
    )
    manager.upsert_fact(
        key="environment",
        value="prod",
        fact_type="operation",
        mode="append",
    )
    active_before_replace = manager.list_facts(fact_type="operation")
    env_values_before = sorted(f["value"] for f in active_before_replace if f["key"] == "environment")
    assert env_values_before == ["prod", "staging"]

    manager.upsert_fact(
        key="environment",
        value="production",
        fact_type="operation",
        mode="replace",
    )
    active_after_replace = manager.list_facts(fact_type="operation")
    env_values_after = [f["value"] for f in active_after_replace if f["key"] == "environment"]
    assert env_values_after == ["production"]

    with_deleted = manager.list_facts(fact_type="operation", include_deleted=True)
    deleted_versions = [f for f in with_deleted if f["key"] == "environment" and f["status"] == "deleted"]
    assert len(deleted_versions) >= 1

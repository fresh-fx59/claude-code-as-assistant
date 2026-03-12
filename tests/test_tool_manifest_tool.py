import json

import yaml

from src.tool_manifest_tool import main as tool_manifest_main
from src.tool_manifest_tool import scaffold_manifest
from src.tool_manifest_tool import validate_manifest_data


def test_validate_manifest_data_accepts_valid_manifest() -> None:
    payload = validate_manifest_data(
        {
            "name": "demo-tool",
            "description": "Demo tool.",
            "tier": "extended",
            "triggers": ["demo", "example"],
            "instructions": "Run demo command.",
            "setup": "Ensure demo installed.",
        },
        expected_name="demo-tool",
    )

    assert payload["ok"] is True
    assert payload["issues"] == []
    assert payload["manifest"]["triggers"] == ["demo", "example"]


def test_validate_manifest_data_reports_errors_and_warnings() -> None:
    payload = validate_manifest_data(
        {
            "name": "Bad Name",
            "description": " ",
            "tier": "default",
            "triggers": ["dup", "dup", ""],
            "instructions": "",
        },
        expected_name="bad-name",
    )

    assert payload["ok"] is False
    issues = payload["issues"]
    assert any(issue["field"] == "name" and issue["level"] == "error" for issue in issues)
    assert any(issue["field"] == "description" and issue["level"] == "error" for issue in issues)
    assert any(issue["field"] == "tier" and issue["level"] == "error" for issue in issues)
    assert any(issue["field"] == "instructions" and issue["level"] == "error" for issue in issues)
    assert any(issue["field"] == "triggers" and issue["level"] == "warning" for issue in issues)


def test_scaffold_command_writes_manifest(tmp_path, capsys) -> None:
    rc = tool_manifest_main(
        [
            "--format",
            "json",
            "scaffold",
            "demo-tool",
            "--description",
            "Demo tool.",
            "--tier",
            "core",
            "--trigger",
            "demo",
            "--trigger",
            "tool demo",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    output_path = tmp_path / "demo-tool.yaml"
    assert payload["ok"] is True
    assert output_path.exists()
    manifest = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert manifest["name"] == "demo-tool"
    assert manifest["tier"] == "core"
    assert manifest["triggers"] == ["demo", "tool demo"]


def test_validate_command_returns_nonzero_for_invalid_manifest(tmp_path, capsys) -> None:
    manifest_path = tmp_path / "broken.yaml"
    manifest_path.write_text(
        "name: broken tool\ndescription: Demo\ntier: extended\ntriggers: oops\ninstructions: ok\n",
        encoding="utf-8",
    )

    rc = tool_manifest_main(["validate", str(manifest_path)])

    assert rc == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(issue["field"] == "name" for issue in payload["issues"])
    assert any(issue["field"] == "triggers" for issue in payload["issues"])


def test_scaffold_manifest_defaults_trigger_from_name() -> None:
    manifest = scaffold_manifest(
        name="summary-inspector",
        description="Inspect summaries.",
        tier="extended",
        triggers=None,
    )

    assert manifest["triggers"] == ["summary inspector"]

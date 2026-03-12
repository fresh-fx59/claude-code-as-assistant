"""CLI for scaffolding and validating tool manifest YAML files."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from . import config

_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_VALID_TIERS = {"core", "extended"}


@dataclass
class ValidationIssue:
    level: str
    field: str
    message: str


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tool-manifest")
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="Output format.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="Validate an existing tool YAML manifest.")
    validate.add_argument("path", help="Path to a YAML tool manifest.")

    scaffold = sub.add_parser("scaffold", help="Generate a starter tool YAML manifest.")
    scaffold.add_argument("name", help="Tool name, for example summary-inspector.")
    scaffold.add_argument(
        "--description",
        default="Describe what this tool does.",
        help="One-line tool description.",
    )
    scaffold.add_argument(
        "--tier",
        choices=sorted(_VALID_TIERS),
        default="extended",
        help="Activation tier for the tool.",
    )
    scaffold.add_argument(
        "--trigger",
        dest="triggers",
        action="append",
        default=None,
        help="Trigger phrase. Repeat to add more than one.",
    )
    scaffold.add_argument(
        "--output-dir",
        default=str(config.TOOLS_DIR),
        help="Directory where the YAML manifest will be created.",
    )
    scaffold.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing manifest file.",
    )
    return parser


def _normalize_triggers(raw_triggers: object) -> tuple[list[str], list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    if raw_triggers is None:
        return [], issues
    if not isinstance(raw_triggers, list):
        return [], [ValidationIssue("error", "triggers", "must be a YAML list of strings")]

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_triggers):
        if not isinstance(item, str):
            issues.append(ValidationIssue("error", f"triggers[{index}]", "must be a string"))
            continue
        value = item.strip()
        if not value:
            issues.append(ValidationIssue("error", f"triggers[{index}]", "must not be empty"))
            continue
        lowered = value.lower()
        if lowered in seen:
            issues.append(ValidationIssue("warning", "triggers", f"duplicate trigger: {value}"))
            continue
        normalized.append(value)
        seen.add(lowered)
    return normalized, issues


def validate_manifest_data(data: object, *, expected_name: str | None = None) -> dict[str, object]:
    issues: list[ValidationIssue] = []
    if not isinstance(data, dict):
        issues.append(ValidationIssue("error", "manifest", "top-level YAML value must be a mapping"))
        return {"ok": False, "issues": [asdict(issue) for issue in issues], "manifest": None}

    manifest = dict(data)
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        issues.append(ValidationIssue("error", "name", "is required and must be a non-empty string"))
    else:
        name = name.strip()
        if not _VALID_NAME.match(name):
            issues.append(
                ValidationIssue(
                    "error",
                    "name",
                    "must match ^[a-z0-9][a-z0-9_.-]*$",
                )
            )
        if expected_name and name != expected_name:
            issues.append(
                ValidationIssue(
                    "warning",
                    "name",
                    f"does not match filename stem '{expected_name}'",
                )
            )
        manifest["name"] = name

    description = manifest.get("description")
    if not isinstance(description, str) or not description.strip():
        issues.append(
            ValidationIssue("error", "description", "is required and must be a non-empty string")
        )
    else:
        manifest["description"] = description.strip()

    tier = manifest.get("tier")
    if not isinstance(tier, str) or tier.strip() not in _VALID_TIERS:
        issues.append(
            ValidationIssue("error", "tier", "is required and must be one of: core, extended")
        )
    else:
        manifest["tier"] = tier.strip()

    instructions = manifest.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        issues.append(
            ValidationIssue("error", "instructions", "is required and must be a non-empty string")
        )
    else:
        manifest["instructions"] = instructions.rstrip()

    setup = manifest.get("setup")
    if setup is not None and (not isinstance(setup, str) or not setup.strip()):
        issues.append(ValidationIssue("error", "setup", "must be a non-empty string when present"))
    elif isinstance(setup, str):
        manifest["setup"] = setup.strip()

    normalized_triggers, trigger_issues = _normalize_triggers(manifest.get("triggers"))
    issues.extend(trigger_issues)
    manifest["triggers"] = normalized_triggers

    return {
        "ok": not any(issue.level == "error" for issue in issues),
        "issues": [asdict(issue) for issue in issues],
        "manifest": manifest,
    }


def _default_instructions(name: str) -> str:
    title = name.replace("-", " ").replace("_", " ").title()
    return (
        f"# {title}\n\n"
        "Describe when to use this tool, which command to run, and what the result means.\n\n"
        "Command:\n"
        f"- `python -m src.{name.replace('-', '_')}_tool --help`\n"
    )


def scaffold_manifest(
    *,
    name: str,
    description: str,
    tier: str,
    triggers: list[str] | None,
) -> dict[str, object]:
    clean_triggers = [item.strip() for item in (triggers or []) if item and item.strip()]
    if not clean_triggers:
        clean_triggers = [name.replace("-", " ")]
    return {
        "name": name,
        "description": description.strip(),
        "tier": tier,
        "triggers": clean_triggers,
        "instructions": _default_instructions(name),
        "setup": f"Ensure command is available: python -m src.{name.replace('-', '_')}_tool --help",
    }


def _format_text(payload: dict[str, object]) -> str:
    lines = [f"ok: {payload['ok']}"]
    manifest = payload.get("manifest")
    if isinstance(manifest, dict) and manifest.get("name"):
        lines.append(f"name: {manifest['name']}")
    issues = payload.get("issues") or []
    if issues:
        lines.append("issues:")
        for issue in issues:
            lines.append(f"- {issue['level']} {issue['field']}: {issue['message']}")
    else:
        lines.append("issues: none")
    if "path" in payload:
        lines.append(f"path: {payload['path']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        path = Path(args.path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = {
                "ok": False,
                "path": str(path),
                "issues": [
                    asdict(ValidationIssue("error", "path", "file does not exist")),
                ],
                "manifest": None,
            }
        except yaml.YAMLError as exc:
            payload = {
                "ok": False,
                "path": str(path),
                "issues": [
                    asdict(ValidationIssue("error", "yaml", f"failed to parse YAML: {exc}")),
                ],
                "manifest": None,
            }
        else:
            payload = validate_manifest_data(data, expected_name=path.stem)
            payload["path"] = str(path)

        if args.format == "text":
            print(_format_text(payload))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1

    manifest = scaffold_manifest(
        name=args.name,
        description=args.description,
        tier=args.tier,
        triggers=args.triggers,
    )
    validation = validate_manifest_data(manifest, expected_name=args.name)
    if not validation["ok"]:
        if args.format == "text":
            print(_format_text(validation))
        else:
            print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{args.name}.yaml"
    if output_path.exists() and not args.force:
        payload = {
            "ok": False,
            "path": str(output_path),
            "issues": [
                asdict(
                    ValidationIssue(
                        "error",
                        "path",
                        "file already exists; pass --force to overwrite",
                    )
                )
            ],
            "manifest": validation["manifest"],
        }
        if args.format == "text":
            print(_format_text(payload))
        else:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    output_path.write_text(
        yaml.safe_dump(validation["manifest"], sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    payload = {
        "ok": True,
        "path": str(output_path),
        "issues": validation["issues"],
        "manifest": validation["manifest"],
    }
    if args.format == "text":
        print(_format_text(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Sandboxed self-modification workflow helpers.

Implements the core loop primitives from the architecture plan:
stage candidate code -> validate -> promote -> rollback helper.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    output: str


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    message: str
    validation_output: str = ""


class SelfModificationManager:
    """Manage plugin candidates in sandbox before promotion."""

    def __init__(self, repo_root: Path, sandbox_dir: Path | None = None) -> None:
        self.repo_root = repo_root
        self.sandbox_dir = sandbox_dir or (repo_root / "sandbox")
        self.sandbox_plugins_dir = self.sandbox_dir / "plugins"
        self.plugins_dir = self.repo_root / "src" / "plugins"

    def stage_plugin(self, relative_plugin_path: str, content: str) -> Path:
        rel_path = self._normalize_relative_path(relative_plugin_path)
        target = self.sandbox_plugins_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def promote_plugin(self, relative_plugin_path: str) -> Path:
        rel_path = self._normalize_relative_path(relative_plugin_path)
        source = self.sandbox_plugins_dir / rel_path
        if not source.exists():
            raise FileNotFoundError(f"Sandbox candidate not found: {source}")

        target = self.plugins_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return target

    def validate(self, test_target: str = "tests/test_context_plugins.py", timeout: int = 180) -> ValidationResult:
        python_bin = self._select_python()
        cmd = [str(python_bin), "-m", "pytest", "-q", test_target]
        proc = subprocess.run(
            cmd,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        trimmed = output[-4000:] if len(output) > 4000 else output
        return ValidationResult(ok=proc.returncode == 0, output=trimmed.strip())

    def reload_plugin_module(self, relative_plugin_path: str) -> tuple[bool, str]:
        rel_path = self._normalize_relative_path(relative_plugin_path)
        if rel_path.suffix != ".py":
            return False, "Only .py plugin modules can be hot-reloaded"

        module_suffix = ".".join(rel_path.with_suffix("").parts)
        module_name = f"src.plugins.{module_suffix}"
        try:
            importlib.invalidate_caches()
            module = importlib.import_module(module_name)
            importlib.reload(module)
            return True, module_name
        except Exception as exc:
            return False, f"{module_name}: {exc}"

    def apply_candidate(
        self,
        relative_plugin_path: str,
        test_target: str = "tests/test_context_plugins.py",
        timeout: int = 180,
    ) -> ApplyResult:
        validation = self.validate(test_target=test_target, timeout=timeout)
        if not validation.ok:
            return ApplyResult(
                ok=False,
                message="Validation failed",
                validation_output=validation.output,
            )

        try:
            self.promote_plugin(relative_plugin_path)
        except Exception as exc:
            return ApplyResult(
                ok=False,
                message=f"Promotion failed: {exc}",
                validation_output=validation.output,
            )

        reloaded, reload_msg = self.reload_plugin_module(relative_plugin_path)
        if reloaded:
            return ApplyResult(
                ok=True,
                message=f"Applied and hot-reloaded {reload_msg}",
                validation_output=validation.output,
            )

        rb_ok, rb_details = self.rollback_to_good_commit()
        rollback_text = f"rollback to {rb_details}" if rb_ok else f"rollback failed: {rb_details}"
        return ApplyResult(
            ok=False,
            message=f"Reload failed ({reload_msg}); {rollback_text}",
            validation_output=validation.output,
        )

    def rollback_to_good_commit(self) -> tuple[bool, str]:
        good_commit = self._read_good_commit()
        if not good_commit:
            return False, "No .deploy/good_commit found"

        if self._has_uncommitted_changes():
            return False, "Working tree has uncommitted changes; refusing hard reset rollback"

        branch_name = self._create_recovery_branch_name()
        branch_proc = subprocess.run(
            ["git", "branch", branch_name, "HEAD"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if branch_proc.returncode != 0:
            err = (branch_proc.stderr or branch_proc.stdout or "git branch failed").strip()
            return False, f"Failed to create recovery branch '{branch_name}': {err}"

        proc = subprocess.run(
            ["git", "reset", "--hard", good_commit],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "git reset failed").strip()
            return False, err
        return True, f"{good_commit} (recovery branch: {branch_name})"

    def _read_good_commit(self) -> str | None:
        good_commit_file = self.repo_root / ".deploy" / "good_commit"
        if not good_commit_file.exists():
            return None

        commit = good_commit_file.read_text(encoding="utf-8").strip()
        return commit or None

    def _select_python(self) -> Path:
        candidates = [
            self.repo_root / "venv" / "bin" / "python3",
            self.repo_root / "venv" / "bin" / "python",
            Path(sys.executable),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return Path(sys.executable)

    def _has_uncommitted_changes(self) -> bool:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return True
        return bool((proc.stdout or "").strip())

    @staticmethod
    def _create_recovery_branch_name() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        return f"rollback-safety/{stamp}"

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute():
            raise ValueError("Plugin path must be relative")
        if ".." in raw.parts:
            raise ValueError("Plugin path traversal is not allowed")
        return raw

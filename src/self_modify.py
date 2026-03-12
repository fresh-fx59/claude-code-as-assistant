"""Sandboxed self-modification workflow helpers.

Implements the core loop primitives from the architecture plan:
stage candidate code -> validate -> promote -> rollback helper.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import metrics


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
        started = time.monotonic()
        rel_path = self._normalize_relative_path(relative_plugin_path)
        target = self.sandbox_plugins_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._observe(
            event="stage_plugin",
            status="success",
            decision="advisory",
            started=started,
        )
        return target

    def promote_plugin(self, relative_plugin_path: str) -> Path:
        started = time.monotonic()
        rel_path = self._normalize_relative_path(relative_plugin_path)
        source = self.sandbox_plugins_dir / rel_path
        if not source.exists():
            self._observe(
                event="promote_plugin",
                status="error",
                decision="advisory",
                started=started,
            )
            raise FileNotFoundError(f"Sandbox candidate not found: {source}")

        target = self.plugins_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self._observe(
            event="promote_plugin",
            status="success",
            decision="advisory",
            started=started,
        )
        return target

    def validate(self, test_target: str = "tests/test_context_plugins.py", timeout: int = 180) -> ValidationResult:
        started = time.monotonic()
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
        self._observe(
            event="validate_candidate",
            status="success" if proc.returncode == 0 else "failed",
            decision="advisory",
            started=started,
        )
        return ValidationResult(ok=proc.returncode == 0, output=trimmed.strip())

    def reload_plugin_module(self, relative_plugin_path: str) -> tuple[bool, str]:
        started = time.monotonic()
        rel_path = self._normalize_relative_path(relative_plugin_path)
        if rel_path.suffix != ".py":
            self._observe(
                event="reload_plugin",
                status="failed",
                decision="advisory",
                started=started,
            )
            return False, "Only .py plugin modules can be hot-reloaded"

        module_suffix = ".".join(rel_path.with_suffix("").parts)
        module_name = f"src.plugins.{module_suffix}"
        try:
            importlib.invalidate_caches()
            module = importlib.import_module(module_name)
            importlib.reload(module)
            self._observe(
                event="reload_plugin",
                status="success",
                decision="advisory",
                started=started,
            )
            return True, module_name
        except Exception as exc:
            self._observe(
                event="reload_plugin",
                status="error",
                decision="advisory",
                started=started,
            )
            return False, f"{module_name}: {exc}"

    def apply_candidate(
        self,
        relative_plugin_path: str,
        test_target: str = "tests/test_context_plugins.py",
        timeout: int = 180,
    ) -> ApplyResult:
        started = time.monotonic()
        validation = self.validate(test_target=test_target, timeout=timeout)
        if not validation.ok:
            self._observe(
                event="apply_candidate",
                status="failed",
                decision="block",
                started=started,
            )
            return ApplyResult(
                ok=False,
                message="Validation failed",
                validation_output=validation.output,
            )

        try:
            self.promote_plugin(relative_plugin_path)
        except Exception as exc:
            self._observe(
                event="apply_candidate",
                status="error",
                decision="block",
                started=started,
            )
            return ApplyResult(
                ok=False,
                message=f"Promotion failed: {exc}",
                validation_output=validation.output,
            )

        reloaded, reload_msg = self.reload_plugin_module(relative_plugin_path)
        if reloaded:
            self._observe(
                event="apply_candidate",
                status="success",
                decision="allow",
                started=started,
            )
            return ApplyResult(
                ok=True,
                message=f"Applied and hot-reloaded {reload_msg}",
                validation_output=validation.output,
            )

        rb_ok, rb_details = self.rollback_to_good_commit()
        rollback_text = f"rollback to {rb_details}" if rb_ok else f"rollback failed: {rb_details}"
        self._observe(
            event="apply_candidate",
            status="failed",
            decision="rollback" if rb_ok else "block",
            started=started,
        )
        return ApplyResult(
            ok=False,
            message=f"Reload failed ({reload_msg}); {rollback_text}",
            validation_output=validation.output,
        )

    def rollback_to_good_commit(self) -> tuple[bool, str]:
        started = time.monotonic()
        good_commit = self._read_good_commit()
        if not good_commit:
            self._observe(
                event="rollback_to_good_commit",
                status="failed",
                decision="block",
                started=started,
            )
            return False, "No .deploy/good_commit found"

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
            self._observe(
                event="rollback_to_good_commit",
                status="error",
                decision="block",
                started=started,
            )
            return False, err
        self._observe(
            event="rollback_to_good_commit",
            status="success",
            decision="rollback",
            started=started,
        )
        return True, good_commit

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

    @staticmethod
    def _normalize_relative_path(relative_path: str) -> Path:
        raw = Path(relative_path)
        if raw.is_absolute():
            raise ValueError("Plugin path must be relative")
        if ".." in raw.parts:
            raise ValueError("Plugin path traversal is not allowed")
        return raw

    def _observe(self, *, event: str, status: str, decision: str, started: float | None = None) -> None:
        duration_ms = None
        if started is not None:
            duration_ms = (time.monotonic() - started) * 1000.0
        metrics.observe_f08_governance_event(
            mode=_f08_governance_mode(),
            scope=_f08_enforcement_scope(),
            event=event,
            status=status,
            decision=decision,
            duration_ms=duration_ms,
        )


def _f08_governance_mode() -> str:
    mode = os.getenv("F08_GOVERNANCE_MODE", "shadow").strip().lower()
    if mode in {"shadow", "enforce_limited", "enforce_scoped", "enforce_full"}:
        return mode
    return "shadow"


def _f08_enforcement_scope() -> str:
    return os.getenv("F08_ENFORCEMENT_SCOPE", "self_mod_only").strip().lower() or "self_mod_only"

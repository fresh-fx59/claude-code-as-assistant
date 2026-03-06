"""Sandboxed self-modification workflow helpers.

Implements the core loop primitives from the architecture plan:
stage candidate code -> validate -> promote -> rollback helper.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
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
    review_artifact: str = ""
    review_summary: str = ""


class ReviewDecision(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    CONCERNS = "CONCERNS"


@dataclass(frozen=True)
class ReviewVote:
    reviewer: str
    decision: ReviewDecision
    summary: str
    severe: bool = False


@dataclass(frozen=True)
class ReviewArtifact:
    timestamp: str
    relative_plugin_path: str
    test_target: str
    risk: str
    required: bool
    override_used: bool
    approved: bool
    blocked_reason: str
    votes: tuple[ReviewVote, ...]

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "relative_plugin_path": self.relative_plugin_path,
            "test_target": self.test_target,
            "risk": self.risk,
            "required": self.required,
            "override_used": self.override_used,
            "approved": self.approved,
            "blocked_reason": self.blocked_reason,
            "votes": [
                {
                    "reviewer": vote.reviewer,
                    "decision": vote.decision.value,
                    "summary": vote.summary,
                    "severe": vote.severe,
                }
                for vote in self.votes
            ],
        }


class SelfModificationManager:
    """Manage plugin candidates in sandbox before promotion."""

    def __init__(self, repo_root: Path, sandbox_dir: Path | None = None) -> None:
        self.repo_root = repo_root
        self.sandbox_dir = sandbox_dir or (repo_root / "sandbox")
        self.sandbox_plugins_dir = self.sandbox_dir / "plugins"
        self.plugins_dir = self.repo_root / "src" / "plugins"
        self._review_artifacts_dir = self.repo_root / ".deploy" / "review_artifacts"
        self._review_gate_enabled = self._env_bool("SELF_MOD_REVIEW_GATE_ENABLED", True)
        self._review_required_min_risk = (os.getenv("SELF_MOD_REVIEW_MIN_RISK", "medium") or "medium").strip().lower()

    @staticmethod
    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _risk_rank(risk: str) -> int:
        return {"low": 1, "medium": 2, "high": 3}.get(risk, 1)

    def classify_risk(self, relative_plugin_path: str, test_target: str) -> str:
        rel_path = self._normalize_relative_path(relative_plugin_path)
        source = ""
        candidate_file = self.sandbox_plugins_dir / rel_path
        if candidate_file.exists():
            source = candidate_file.read_text(encoding="utf-8")

        suspicious_patterns = (
            r"\bsubprocess\.",
            r"\bos\.system\(",
            r"\beval\(",
            r"\bexec\(",
            r"git\s+reset\s+--hard",
            r"rm\s+-rf",
            r"TELEGRAM_BOT_TOKEN",
            r"OPENAI_API_KEY",
            r"ANTHROPIC_API_KEY",
        )
        if any(re.search(pattern, source, re.IGNORECASE) for pattern in suspicious_patterns):
            return "high"
        if rel_path.suffix != ".py":
            return "high"
        if "bot" in rel_path.parts or "main" in rel_path.parts:
            return "high"
        if test_target.strip() != "tests/test_context_plugins.py":
            return "medium"
        return "low"

    @staticmethod
    def _normalize_decision(decision: str) -> ReviewDecision:
        normalized = (decision or "").strip().upper()
        if normalized == ReviewDecision.PASS.value:
            return ReviewDecision.PASS
        if normalized == ReviewDecision.FAIL.value:
            return ReviewDecision.FAIL
        return ReviewDecision.CONCERNS

    def _review_vote(
        self,
        reviewer: str,
        relative_plugin_path: str,
        source: str,
        risk: str,
    ) -> ReviewVote:
        lowered = source.lower()
        if reviewer == "security":
            if any(token in lowered for token in ("os.system(", "subprocess.", "eval(", "exec(", "rm -rf")):
                return ReviewVote(
                    reviewer=reviewer,
                    decision=ReviewDecision.FAIL,
                    summary="Potential command execution / destructive primitive detected.",
                    severe=True,
                )
            if any(token in lowered for token in ("token", "api_key", "secret")):
                return ReviewVote(
                    reviewer=reviewer,
                    decision=ReviewDecision.CONCERNS,
                    summary="Potential credential handling markers present; verify no secret exposure.",
                )
            return ReviewVote(reviewer=reviewer, decision=ReviewDecision.PASS, summary="No severe security markers.")
        if reviewer == "reliability":
            if "rollback_to_good_commit" in source or "reset --hard" in lowered:
                return ReviewVote(
                    reviewer=reviewer,
                    decision=ReviewDecision.CONCERNS,
                    summary="Rollback-sensitive operations present; validate guardrails carefully.",
                )
            if risk == "high":
                return ReviewVote(
                    reviewer=reviewer,
                    decision=ReviewDecision.CONCERNS,
                    summary="High-risk change requires extra validation evidence.",
                )
            return ReviewVote(reviewer=reviewer, decision=ReviewDecision.PASS, summary="Operational risk acceptable.")
        if reviewer == "correctness":
            if not source.strip():
                return ReviewVote(
                    reviewer=reviewer,
                    decision=ReviewDecision.FAIL,
                    summary="Candidate source is empty.",
                )
            if "TODO" in source:
                return ReviewVote(
                    reviewer=reviewer,
                    decision=ReviewDecision.CONCERNS,
                    summary="TODO marker found; implementation may be incomplete.",
                )
            return ReviewVote(reviewer=reviewer, decision=ReviewDecision.PASS, summary="Basic correctness checks passed.")
        return ReviewVote(reviewer=reviewer, decision=ReviewDecision.CONCERNS, summary="Unknown reviewer policy.")

    def _run_review_gate(
        self,
        *,
        relative_plugin_path: str,
        test_target: str,
        risk: str,
        override_review: bool,
    ) -> tuple[bool, ReviewArtifact, Path]:
        rel_path = self._normalize_relative_path(relative_plugin_path)
        source = ""
        candidate_file = self.sandbox_plugins_dir / rel_path
        if candidate_file.exists():
            source = candidate_file.read_text(encoding="utf-8")

        required = self._review_gate_enabled and self._risk_rank(risk) >= self._risk_rank(self._review_required_min_risk)
        reviewers = ("correctness", "security", "reliability") if required else ("correctness",)
        votes = tuple(
            self._review_vote(
                reviewer=reviewer,
                relative_plugin_path=relative_plugin_path,
                source=source,
                risk=risk,
            )
            for reviewer in reviewers
        )
        normalized_votes = tuple(
            ReviewVote(
                reviewer=vote.reviewer,
                decision=self._normalize_decision(vote.decision.value),
                summary=vote.summary,
                severe=vote.severe,
            )
            for vote in votes
        )
        pass_count = sum(1 for vote in normalized_votes if vote.decision == ReviewDecision.PASS)
        fail_count = sum(1 for vote in normalized_votes if vote.decision == ReviewDecision.FAIL)
        severe_count = sum(1 for vote in normalized_votes if vote.severe)
        approved = True
        blocked_reason = ""
        if required:
            if severe_count > 0:
                approved = False
                blocked_reason = "severe_concern"
            elif fail_count > 0:
                approved = False
                blocked_reason = "review_failed"
            elif pass_count < 2:
                approved = False
                blocked_reason = "consensus_not_reached"
        if override_review and not approved:
            blocked_reason = ""
            approved = True

        artifact = ReviewArtifact(
            timestamp=datetime.now(timezone.utc).isoformat(),
            relative_plugin_path=relative_plugin_path,
            test_target=test_target,
            risk=risk,
            required=required,
            override_used=override_review,
            approved=approved,
            blocked_reason=blocked_reason,
            votes=normalized_votes,
        )
        artifact_path = self._write_review_artifact(artifact)
        return approved, artifact, artifact_path

    def _write_review_artifact(self, artifact: ReviewArtifact) -> Path:
        self._review_artifacts_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", artifact.relative_plugin_path).strip("-") or "candidate"
        target = self._review_artifacts_dir / f"{stamp}-{safe_name}.json"
        target.write_text(json.dumps(artifact.to_dict(), ensure_ascii=True, indent=2), encoding="utf-8")
        return target

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
        override_review: bool = False,
    ) -> ApplyResult:
        risk = self.classify_risk(relative_plugin_path, test_target)
        approved, artifact, artifact_path = self._run_review_gate(
            relative_plugin_path=relative_plugin_path,
            test_target=test_target,
            risk=risk,
            override_review=override_review,
        )
        review_summary = (
            f"risk={artifact.risk}; required={artifact.required}; approved={artifact.approved}; "
            f"override={artifact.override_used}; blocked_reason={artifact.blocked_reason or '-'}"
        )
        if not approved:
            return ApplyResult(
                ok=False,
                message="Review gate blocked change before validation/promote",
                review_artifact=str(artifact_path),
                review_summary=review_summary,
            )

        validation = self.validate(test_target=test_target, timeout=timeout)
        if not validation.ok:
            return ApplyResult(
                ok=False,
                message="Validation failed",
                validation_output=validation.output,
                review_artifact=str(artifact_path),
                review_summary=review_summary,
            )

        try:
            self.promote_plugin(relative_plugin_path)
        except Exception as exc:
            return ApplyResult(
                ok=False,
                message=f"Promotion failed: {exc}",
                validation_output=validation.output,
                review_artifact=str(artifact_path),
                review_summary=review_summary,
            )

        reloaded, reload_msg = self.reload_plugin_module(relative_plugin_path)
        if reloaded:
            return ApplyResult(
                ok=True,
                message=f"Applied and hot-reloaded {reload_msg}",
                validation_output=validation.output,
                review_artifact=str(artifact_path),
                review_summary=review_summary,
            )

        rb_ok, rb_details = self.rollback_to_good_commit()
        rollback_text = f"rollback to {rb_details}" if rb_ok else f"rollback failed: {rb_details}"
        return ApplyResult(
            ok=False,
            message=f"Reload failed ({reload_msg}); {rollback_text}",
            validation_output=validation.output,
            review_artifact=str(artifact_path),
            review_summary=review_summary,
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

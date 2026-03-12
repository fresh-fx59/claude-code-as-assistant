"""F08 governance advisory lane (shadow mode, non-blocking)."""

from __future__ import annotations

import asyncio
import logging
import os
from time import monotonic

from . import metrics

logger = logging.getLogger(__name__)


_RISKY_CHAT_MARKERS = (
    "git reset --hard",
    "rm -rf",
    "drop database",
    "delete all",
    "rollback now",
)


def _governance_mode() -> str:
    mode = os.getenv("F08_GOVERNANCE_MODE", "shadow").strip().lower()
    if mode in {"shadow", "enforce_limited", "enforce_scoped", "enforce_full"}:
        return mode
    return "shadow"


class F08GovernanceAdvisory:
    """Asynchronous, non-blocking governance checks for Phase 1 shadow rollout."""

    def submit_chat_turn(self, *, scope_key: str, prompt: str) -> None:
        asyncio.create_task(self._review_chat_turn(scope_key=scope_key, prompt=prompt))

    def submit_selfmod_apply(self, *, scope_key: str, relative_path: str, test_target: str) -> None:
        asyncio.create_task(
            self._review_selfmod_apply(
                scope_key=scope_key,
                relative_path=relative_path,
                test_target=test_target,
            )
        )

    async def _review_chat_turn(self, *, scope_key: str, prompt: str) -> None:
        started = monotonic()
        try:
            lowered = (prompt or "").lower()
            markers = [token for token in _RISKY_CHAT_MARKERS if token in lowered]
            status = "warn" if markers else "success"
            metrics.observe_f08_governance_event(
                mode=_governance_mode(),
                scope="chat_lane",
                event="chat_turn_advisory",
                status=status,
                decision="advisory",
                duration_ms=(monotonic() - started) * 1000.0,
            )
            logger.info(
                "f08_advisory_chat scope=%s status=%s risky_markers=%s prompt_len=%d",
                scope_key,
                status,
                ",".join(markers) if markers else "-",
                len(prompt or ""),
            )
        except Exception:
            logger.exception("f08_advisory_chat_failed scope=%s", scope_key)
            metrics.observe_f08_governance_event(
                mode=_governance_mode(),
                scope="chat_lane",
                event="chat_turn_advisory",
                status="error",
                decision="advisory",
                duration_ms=(monotonic() - started) * 1000.0,
            )

    async def _review_selfmod_apply(self, *, scope_key: str, relative_path: str, test_target: str) -> None:
        started = monotonic()
        try:
            warnings: list[str] = []
            normalized_path = (relative_path or "").strip()
            normalized_target = (test_target or "").strip()
            if normalized_path.startswith("/") or ".." in normalized_path:
                warnings.append("path_boundary")
            if normalized_target != "tests/test_context_plugins.py":
                warnings.append("custom_test_target")
            status = "warn" if warnings else "success"
            metrics.observe_f08_governance_event(
                mode=_governance_mode(),
                scope="self_mod_only",
                event="selfmod_apply_advisory",
                status=status,
                decision="advisory",
                duration_ms=(monotonic() - started) * 1000.0,
            )
            logger.info(
                "f08_advisory_selfmod scope=%s status=%s path=%s test_target=%s flags=%s",
                scope_key,
                status,
                normalized_path,
                normalized_target,
                ",".join(warnings) if warnings else "-",
            )
        except Exception:
            logger.exception("f08_advisory_selfmod_failed scope=%s", scope_key)
            metrics.observe_f08_governance_event(
                mode=_governance_mode(),
                scope="self_mod_only",
                event="selfmod_apply_advisory",
                status="error",
                decision="advisory",
                duration_ms=(monotonic() - started) * 1000.0,
            )

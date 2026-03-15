from __future__ import annotations

from scripts.gmail_gateway_canary_guardrails import _evaluate


def test_guardrails_pass_within_thresholds() -> None:
    result = _evaluate(
        baseline_success_rate=99.9,
        canary_success_rate=99.0,
        max_success_drop=1.5,
        duplicate_rate_per_1000=0.2,
        max_duplicate_rate_per_1000=0.5,
        sync_lag_minutes=4.0,
        max_sync_lag_minutes=15.0,
    )
    assert result.ok is True
    assert result.failures == []


def test_guardrails_fail_when_any_threshold_exceeded() -> None:
    result = _evaluate(
        baseline_success_rate=99.9,
        canary_success_rate=97.9,
        max_success_drop=1.5,
        duplicate_rate_per_1000=0.7,
        max_duplicate_rate_per_1000=0.5,
        sync_lag_minutes=20.0,
        max_sync_lag_minutes=15.0,
    )
    assert result.ok is False
    assert len(result.failures) == 3

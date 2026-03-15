#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass


@dataclass
class GuardrailResult:
    ok: bool
    baseline_success_rate: float
    canary_success_rate: float
    success_drop: float
    max_success_drop: float
    duplicate_rate_per_1000: float
    max_duplicate_rate_per_1000: float
    sync_lag_minutes: float
    max_sync_lag_minutes: float
    failures: list[str]


def _evaluate(
    *,
    baseline_success_rate: float,
    canary_success_rate: float,
    max_success_drop: float,
    duplicate_rate_per_1000: float,
    max_duplicate_rate_per_1000: float,
    sync_lag_minutes: float,
    max_sync_lag_minutes: float,
) -> GuardrailResult:
    failures: list[str] = []
    success_drop = baseline_success_rate - canary_success_rate
    if success_drop > max_success_drop:
        failures.append(
            f"send_success_drop_exceeded: drop={success_drop:.3f} > allowed={max_success_drop:.3f}"
        )
    if duplicate_rate_per_1000 > max_duplicate_rate_per_1000:
        failures.append(
            "duplicate_rate_exceeded: "
            f"rate={duplicate_rate_per_1000:.3f} > allowed={max_duplicate_rate_per_1000:.3f}"
        )
    if sync_lag_minutes > max_sync_lag_minutes:
        failures.append(
            f"sync_lag_exceeded: lag={sync_lag_minutes:.2f}m > allowed={max_sync_lag_minutes:.2f}m"
        )
    return GuardrailResult(
        ok=not failures,
        baseline_success_rate=baseline_success_rate,
        canary_success_rate=canary_success_rate,
        success_drop=success_drop,
        max_success_drop=max_success_drop,
        duplicate_rate_per_1000=duplicate_rate_per_1000,
        max_duplicate_rate_per_1000=max_duplicate_rate_per_1000,
        sync_lag_minutes=sync_lag_minutes,
        max_sync_lag_minutes=max_sync_lag_minutes,
        failures=failures,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python scripts/gmail_gateway_canary_guardrails.py")
    parser.add_argument("--baseline-success-rate", type=float, required=True)
    parser.add_argument("--canary-success-rate", type=float, required=True)
    parser.add_argument("--duplicate-rate-per-1000", type=float, required=True)
    parser.add_argument("--sync-lag-minutes", type=float, required=True)
    parser.add_argument("--max-success-drop", type=float, default=1.5)
    parser.add_argument("--max-duplicate-rate-per-1000", type=float, default=0.5)
    parser.add_argument("--max-sync-lag-minutes", type=float, default=15.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result = _evaluate(
        baseline_success_rate=args.baseline_success_rate,
        canary_success_rate=args.canary_success_rate,
        max_success_drop=args.max_success_drop,
        duplicate_rate_per_1000=args.duplicate_rate_per_1000,
        max_duplicate_rate_per_1000=args.max_duplicate_rate_per_1000,
        sync_lag_minutes=args.sync_lag_minutes,
        max_sync_lag_minutes=args.max_sync_lag_minutes,
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

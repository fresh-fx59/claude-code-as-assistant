#!/usr/bin/env python3
"""Validate monitor-only F08 governance observability signals from Prometheus."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class CheckResult:
    name: str
    status: str
    value: float | None
    threshold: float | None
    details: str


def build_alert_fingerprint(status: str, checks: list[CheckResult], error_text: str | None = None) -> dict[str, Any]:
    problem_checks = [
        {
            "name": item.name,
            "status": item.status,
            "value": item.value,
        }
        for item in checks
        if item.status in {"warn", "critical"}
    ]
    return {
        "status": status,
        "problems": problem_checks,
        "error": error_text,
    }


def load_previous_fingerprint(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        return data
    return None


def save_fingerprint(path: Path | None, fingerprint: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fingerprint, ensure_ascii=True), encoding="utf-8")


def summarize_change(current: dict[str, Any], previous: dict[str, Any] | None) -> tuple[bool, str, str]:
    current_status = current.get("status", "unknown")
    current_problems = current.get("problems", [])
    previous_status = previous.get("status", "unknown") if previous else None
    previous_problems = previous.get("problems", []) if previous else []

    if previous is None:
        if current_status == "ok":
            return False, "steady_ok", "No new issues."
        return True, "new_issue", f"New {current_status} issue detected."

    if current == previous:
        return False, "unchanged", "No change since last run."

    if previous_status in {"warn", "critical"} and current_status == "ok":
        return True, "recovery", "Recovered to ok from a previous issue."

    if current_status in {"warn", "critical"}:
        previous_names = {(item.get("name"), item.get("status")) for item in previous_problems}
        current_names = {(item.get("name"), item.get("status")) for item in current_problems}
        if current_names - previous_names:
            return True, "new_issue", f"New {current_status} signal detected."
        return True, "changed_issue", f"{current_status.capitalize()} signal changed."

    return False, "steady_ok", "No new issues."


def run_promql(base_url: str, query: str, timeout_s: int) -> Any:
    encoded = urllib.parse.urlencode({"query": query})
    url = f"{base_url.rstrip('/')}/api/v1/query?{encoded}"
    req = urllib.request.Request(url=url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if payload.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {payload}")
    return payload.get("data", {}).get("result", [])


def parse_scalar(result: Any) -> float | None:
    if not result:
        return None
    value = result[0].get("value")
    if not value or len(value) < 2:
        return None
    try:
        return float(value[1])
    except (TypeError, ValueError):
        return None


def evaluate(base_url: str, timeout_s: int) -> list[CheckResult]:
    checks: list[CheckResult] = []

    up = parse_scalar(run_promql(base_url, 'up{job="telegram_bot_metrics"}', timeout_s))
    checks.append(
        CheckResult(
            name="scrape_up",
            status="ok" if up and up >= 1 else "critical",
            value=up,
            threshold=1.0,
            details='Expected up{job="telegram_bot_metrics"} >= 1',
        )
    )

    series_missing = parse_scalar(
        run_promql(base_url, "absent(telegrambot_f08_governance_events_total)", timeout_s)
    )
    checks.append(
        CheckResult(
            name="f08_series_presence",
            status="critical" if series_missing == 1.0 else "ok",
            value=1.0 if series_missing == 1.0 else 0.0,
            threshold=0.0,
            details="Expected telegrambot_f08_governance_events_total to be present",
        )
    )

    non_shadow_events = parse_scalar(
        run_promql(
            base_url,
            'sum(increase(telegrambot_f08_governance_events_total{mode!="shadow"}[1h]))',
            timeout_s,
        )
    )
    checks.append(
        CheckResult(
            name="non_shadow_events_1h",
            status="warn" if non_shadow_events is not None and non_shadow_events > 0 else "ok",
            value=non_shadow_events,
            threshold=0.0,
            details="Warn when any non-shadow F08 events are observed in the last hour",
        )
    )

    apply_fail_ratio = parse_scalar(
        run_promql(
            base_url,
            'sum(increase(telegrambot_f08_governance_events_total{event="apply_candidate",status=~"failed|error"}[24h])) '
            '/ clamp_min(sum(increase(telegrambot_f08_governance_events_total{event="apply_candidate"}[24h])), 1)',
            timeout_s,
        )
    )
    checks.append(
        CheckResult(
            name="apply_candidate_failure_ratio_24h",
            status="warn" if apply_fail_ratio is not None and apply_fail_ratio > 0.30 else "ok",
            value=apply_fail_ratio,
            threshold=0.30,
            details="Warn when F08 apply_candidate failed/error ratio over 24h exceeds 30%",
        )
    )

    rollback_success = parse_scalar(
        run_promql(
            base_url,
            'sum(increase(telegrambot_f08_governance_events_total{event="rollback_to_good_commit",status="success"}[24h]))',
            timeout_s,
        )
    )
    checks.append(
        CheckResult(
            name="rollback_success_count_24h",
            status="warn" if rollback_success is not None and rollback_success >= 1 else "ok",
            value=rollback_success,
            threshold=1.0,
            details="Warn when rollback_to_good_commit succeeds at least once in 24h",
        )
    )

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate monitor-only F08 governance observability health.")
    parser.add_argument("--prometheus-url", default="http://45.151.30.146:9090")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--format", choices=["json", "text"], default="json")
    parser.add_argument("--state-file")
    parser.add_argument("--alert-on-change", action="store_true")
    args = parser.parse_args()

    now = datetime.now(UTC).isoformat()
    state_file = Path(args.state_file).expanduser() if args.state_file else None
    try:
        checks = evaluate(args.prometheus_url, args.timeout)
    except Exception as exc:  # pragma: no cover
        fingerprint = build_alert_fingerprint("critical", [], str(exc))
        previous = load_previous_fingerprint(state_file)
        should_alert, change_type, summary = summarize_change(fingerprint, previous)
        save_fingerprint(state_file, fingerprint)
        payload = {
            "timestamp": now,
            "status": "critical",
            "error": str(exc),
            "checks": [],
            "should_alert": should_alert if args.alert_on_change else True,
            "change_type": change_type,
            "summary": summary,
        }
        if args.format == "json":
            print(json.dumps(payload, ensure_ascii=True))
        else:
            print(f"[critical] validator_error: {exc}")
        return 2

    status = "ok"
    for item in checks:
        if item.status == "critical":
            status = "critical"
            break
        if item.status == "warn" and status != "critical":
            status = "warn"

    payload = {
        "timestamp": now,
        "status": status,
        "checks": [
            {
                "name": item.name,
                "status": item.status,
                "value": item.value,
                "threshold": item.threshold,
                "details": item.details,
            }
            for item in checks
        ],
    }
    fingerprint = build_alert_fingerprint(status, checks)
    previous = load_previous_fingerprint(state_file)
    should_alert, change_type, summary = summarize_change(fingerprint, previous)
    save_fingerprint(state_file, fingerprint)
    payload["should_alert"] = should_alert if args.alert_on_change else status in {"warn", "critical"}
    payload["change_type"] = change_type
    payload["summary"] = summary

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=True))
    else:
        for item in checks:
            print(
                f"[{item.status}] {item.name}: value={item.value} "
                f"threshold={item.threshold} details={item.details}"
            )
        print(f"overall_status={status}")

    if status == "critical":
        return 2
    if status == "warn":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

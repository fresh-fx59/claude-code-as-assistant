#!/usr/bin/env python3
"""Summarize CodexBar cost logs by model.

Handles mildly different JSON layouts from `codexbar cost --format json`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize CodexBar model usage cost")
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--input", help="Path to JSON file, or '-' for stdin")
    parser.add_argument("--mode", choices=("current", "all"), default="current")
    parser.add_argument("--model", help="Explicit model override for current mode")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--pretty", action="store_true")
    return parser.parse_args()


def _load_json(args: argparse.Namespace) -> Any:
    if args.input:
        if args.input == "-":
            return json.loads(sys.stdin.read())
        return json.loads(Path(args.input).read_text(encoding="utf-8"))

    cmd = ["codexbar", "cost", "--format", "json", "--provider", args.provider]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"codexbar failed ({proc.returncode}): {stderr}")
    return json.loads(proc.stdout)


def _as_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("data", "rows", "daily", "entries", "costByDay"):
        val = payload.get(key)
        if isinstance(val, list):
            return [row for row in val if isinstance(row, dict)]

    if all(not isinstance(v, (dict, list)) for v in payload.values()):
        return [payload]
    return []


def _parse_date(row: dict[str, Any]) -> datetime:
    raw = row.get("date") or row.get("day") or row.get("timestamp") or row.get("time")
    if isinstance(raw, str):
        for candidate in (raw, raw.replace("Z", "+00:00")):
            try:
                return datetime.fromisoformat(candidate)
            except ValueError:
                continue
    return datetime.min


def _to_cost(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    if isinstance(value, dict):
        for key in ("cost", "usd", "amount", "value", "total"):
            if key in value:
                return _to_cost(value[key])
    return 0.0


def _extract_breakdown(row: dict[str, Any]) -> dict[str, float]:
    raw = row.get("modelBreakdowns")
    if raw is None:
        raw = row.get("model_breakdowns")
    if raw is None:
        raw = row.get("models")

    out: dict[str, float] = {}

    if isinstance(raw, dict):
        for model, value in raw.items():
            cost = _to_cost(value)
            if cost > 0:
                out[str(model)] = out.get(str(model), 0.0) + cost
        return out

    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            model = item.get("model") or item.get("name") or item.get("id")
            if not model:
                continue
            cost = _to_cost(item.get("cost") or item.get("usd") or item.get("totalCost") or item)
            if cost > 0:
                out[str(model)] = out.get(str(model), 0.0) + cost
    return out


def _extract_models_used(row: dict[str, Any]) -> list[str]:
    raw = row.get("modelsUsed")
    if raw is None:
        raw = row.get("models_used")
    if isinstance(raw, list):
        return [str(x) for x in raw if isinstance(x, str) and x.strip()]
    return []


def _extract_row_total(row: dict[str, Any]) -> float:
    for key in ("totalCost", "total_cost", "cost", "usd"):
        if key in row:
            return _to_cost(row[key])
    return 0.0


def summarize_all(rows: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        for model, cost in _extract_breakdown(row).items():
            totals[model] += cost
    return dict(sorted(totals.items(), key=lambda kv: kv[1], reverse=True))


def summarize_current(rows: list[dict[str, Any]], forced_model: str | None = None) -> dict[str, float]:
    if forced_model:
        total = 0.0
        for row in rows:
            total += _extract_breakdown(row).get(forced_model, 0.0)
        return {forced_model: total}

    for row in sorted(rows, key=_parse_date, reverse=True):
        breakdown = _extract_breakdown(row)
        if breakdown:
            model, cost = max(breakdown.items(), key=lambda kv: kv[1])
            return {model: cost}

        models_used = _extract_models_used(row)
        if models_used:
            return {models_used[-1]: _extract_row_total(row)}

    return {}


def render_text(provider: str, mode: str, summary: dict[str, float]) -> str:
    if not summary:
        return f"No model usage data found for provider={provider}, mode={mode}."

    lines = [f"provider={provider} mode={mode}"]
    for model, cost in summary.items():
        lines.append(f"- {model}: ${cost:.6f}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    try:
        payload = _load_json(args)
    except Exception as exc:  # pragma: no cover
        print(f"Failed to load JSON: {exc}", file=sys.stderr)
        return 2

    rows = _as_rows(payload)
    if args.mode == "all":
        summary = summarize_all(rows)
    else:
        summary = summarize_current(rows, forced_model=args.model)

    if args.format == "json":
        print(json.dumps(summary, indent=2 if args.pretty else None, ensure_ascii=False))
    else:
        print(render_text(args.provider, args.mode, summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Gmail Gateway Canary Checklist

## Inputs
- Baseline send success rate (previous stable window).
- Canary send success rate (current cohort window).
- Duplicate-send anomaly rate (per 1000 sends).
- Sync lag in minutes (P95 or agreed canary statistic).

## Guardrail Command
```bash
python scripts/gmail_gateway_canary_guardrails.py \
  --baseline-success-rate 99.8 \
  --canary-success-rate 99.1 \
  --duplicate-rate-per-1000 0.2 \
  --sync-lag-minutes 6.0
```

## Pass Criteria
- Success-rate drop is within allowed threshold.
- Duplicate anomaly rate is within allowed threshold.
- Sync lag is within allowed threshold.

## Rollback Triggers
- Success drop exceeds threshold.
- Duplicate anomaly rate exceeds threshold.
- Sync lag exceeds threshold for sustained window.

## Evidence
- Save command JSON output.
- Attach Grafana/Loki links for canary window.
- Record decision in `docs/gmail-gateway/canary-evidence.md`.

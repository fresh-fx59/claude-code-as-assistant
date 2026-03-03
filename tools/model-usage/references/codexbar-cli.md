# CodexBar Cost CLI Notes

This helper expects JSON from:

```bash
codexbar cost --format json --provider codex
codexbar cost --format json --provider claude
```

## Supported input shapes

The summarizer accepts these common layouts:

- top-level list of day rows
- top-level object with `data`, `rows`, `daily`, `entries`, or `costByDay`

Per-row fields used when available:

- `date` / `day` / `timestamp` (to choose most recent row)
- `modelBreakdowns` (preferred per-model cost source)
- `modelsUsed` (fallback model name when breakdown missing)
- `totalCost` / `cost` / `usd` (fallback row total)

## Linux PATH quick fix

If `codexbar` is installed but not found:

```bash
export PATH="$(npm prefix -g)/bin:$PATH"
command -v codexbar
```

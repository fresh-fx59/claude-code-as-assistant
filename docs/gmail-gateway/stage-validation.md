# Gmail Gateway Stage Validation

## Purpose
Run real-tenant end-to-end checks against a staged `gmail-gateway` deployment (not mocked).

## Required Environment
- `GMAIL_GATEWAY_REAL_BASE_URL`
- `GMAIL_GATEWAY_REAL_ACCOUNT_ID`

Optional probes:
- `GMAIL_GATEWAY_REAL_MESSAGE_ID` (enables read probe)
- `GMAIL_GATEWAY_REAL_SEND_TO` (enables send probe)

## Commands
Run the stage tests:

```bash
pytest -q tests/gmail_gateway/test_real_tenant_integration.py
```

Run only read/send probes:

```bash
pytest -q tests/gmail_gateway/test_real_tenant_integration.py -k "read_probe or send_probe"
```

## Expected Outcome
- Account/status contract passes.
- Search contract passes.
- Optional read probe passes when a known message id is provided.
- Optional send probe passes when a recipient is configured.

## Refresh/Retry Verification
- Force token expiry in stage (or rotate test token) before running the send probe.
- Confirm send still succeeds and gateway logs include refresh+retry flow.
- Verify `/internal/metrics` counters increased for send path after the probe.

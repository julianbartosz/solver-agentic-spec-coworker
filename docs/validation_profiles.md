# Validation Profiles and Network Safety

This repository enforces deterministic test behavior via three validation profiles:

- **offline** (default):
  - pytest-socket blocks all sockets except explicit `@pytest.mark.allow_hosts` (used for Prism localhost).
  - pytest-recording defaults to `record_mode=none` (replay-only). Any missing cassette is a hard failure.
  - Live tests (`integration_live`) are skipped.
- **record** (explicit opt-in):
  - Requires `ALLOW_RECORD=1`.
  - Forces `record_mode=once` (or `new_episodes` if explicitly set) to refresh cassettes.
  - Sockets remain blocked except localhost; only cassette-targeted hosts should be contacted.
- **live** (explicit opt-in):
  - Requires `ALLOW_LIVE=1` and `LIVE_HOST_ALLOWLIST`.
  - Runs only tests marked `integration_live`; all others are skipped.

## Recording cassettes

```bash
ALLOW_RECORD=1 VALIDATION_PROFILE=record \
  python -m pytest tests/contract -v --record-mode=once
```

### CI hooks

- Default CI runs the `offline` profile with coverage + generated-module gates.
- Manual `workflow_dispatch` inputs enable opt-in jobs:
  - `run_record: true` → `ALLOW_RECORD=1 VALIDATION_PROFILE=record` runs `pytest tests/contract -v --record-mode=once` (installs Prism CLI for mock server).
  - `run_live: true` → `ALLOW_LIVE=1 VALIDATION_PROFILE=live` runs `pytest -m integration_live` and requires `live_host_allowlist` input (comma-separated) to satisfy socket gating.

## Contract testing with Schemathesis

- Prefer `@schema.parametrize()` + `case.call_and_validate(base_url=...)` for live/record flows.
- For cassette replay, ensure `record_mode=none` and VCR cassettes exist; missing cassettes fail in offline.

## Coverage gates

- Global coverage fail-under: **60%** (CI) with `--cov=src`.
- Generated modules gate: `scripts/check_generated_coverage.py --require-matches --fail-under 60` (CI and default `make coverage`).

## Security gate (Bandit)

- Bandit is configured via `pyproject.toml` and invoked as:

```bash
bandit -c pyproject.toml -r src tests
```

- TOML support is ensured via `bandit[toml]` dependency.
- Severity/confidence thresholds: MEDIUM/MEDIUM.

## Network safety

- offline profile blocks network by default; Prism localhost allowed via `@pytest.mark.allow_hosts(["127.0.0.1", "localhost"])`.
- Missing cassettes in offline are hard failures (no silent skips).

## Sandbox contract validation (generated clients)

- Sandbox can opt into Schemathesis + Prism contract checks by setting `enable_contract_tests=True` and providing `contract_spec_path` in `SandboxConfig`.
- Prism runs on localhost only (default port 4010) to stay compatible with offline profile; Schemathesis is invoked with the copied spec and hard-coded checks (`status_code_conformance`, `content_type_conformance`).
- Prism CLI must be available on PATH (`npm install -g @stoplight/prism-cli`); absence is treated as a gate failure.

## Secrets

- Do not commit real secrets. Use `.env.example` for placeholders and keep `.env` gitignored. Rotate any leaked keys immediately.

# Production Spec Sweep (Dec 12, 2025)

This doc captures a production-themed evaluation sweep of Integration Co‑Worker against a set of “real” public OpenAPI specs, using:

- **Real LLM mode** (no mocks)
- **Postgres persistence** (spec_silver / integration_gold / kg + checkpoints)
- **Repo integration writes** into the UPlant realism repo

## Effective config (redacted)

- `USE_MOCK_LLM=false`
- `USE_SQLITE=false`
- DB: Postgres (`DATABASE_URL` set)
- LLM mode: Real (confirmed via `integration_coworker.cli status`)

## Target repo (realism)

- Repo root: `/Users/julianbartosz/git/schoolwork/UPlant-testing-solver-agentic-spec-coworker`
- Profile source: `.integration-coworker.yaml` (FastAPI / Python)
- Writes:
  - `src/custom_integrations/api_clients/<provider>.py`
  - `src/custom_integrations/workflows/<provider>_<task>.py`
  - `tests/custom_integrations/test_<provider>_<task>.py`
  - Updates `src/app/router.py`

## Spec set

We keep two spec locations in this repo:

- `specs/` — curated, checked-in reference specs.
- `data/spec_sweep/` — **downloaded snapshots** used for this run.

### Used in this sweep (`data/spec_sweep/`)

| Provider | Spec file | Notes |
|---|---|---|
| Stripe | `data/spec_sweep/stripe_api.json` | large (587 endpoints) |
| GitHub | `data/spec_sweep/github_api.json` | very large |
| Slack | `data/spec_sweep/slack_api.yaml` | JSON content; filename kept for continuity |
| Twilio | `data/spec_sweep/twilio_messaging_v1.yaml` | Messaging API |
| Petstore | `data/spec_sweep/petstore_v3.yaml` | from `swagger-api/swagger-petstore` |

### Zoom

**Missing due to 404**.

Attempted sources (all returned 404 on Dec 12, 2025):

- `https://raw.githubusercontent.com/zoom/api/master/openapi/zoom-api.yaml`
- `https://raw.githubusercontent.com/zoom/api/main/openapi/zoom-api.yaml`

We can re-run this sweep with Zoom once a stable, authoritative URL is provided.

## Results summary

All runs below were executed with `python -m integration_coworker.cli run` and completed successfully.

| Provider | Task slug (from report) | Run ID | Endpoints (from report) | Repo writes |
|---|---|---|---:|---|
| sweep_stripe | create_checkout_session | `run_a95ce0ff_1765569890` | 587 | 3 created, 1 updated |
| sweep_github | create_github_issue | `run_7e957ccd_1765570181` | (see log) | 3 created, 1 updated |
| sweep_slack | post_message_to_slack_channel | `run_f3fa49fb_1765570574` | (see log) | 3 created, 1 updated |
| sweep_twilio | send_sms_and_return_message_sid | `run_8001dbb2_1765570698` | (see log) | 3 created, 1 updated |
| sweep_petstore | create_petstore_pet | `run_3ee34e27_1765570889` | (see log) | 3 created, 1 updated |

## Knowledge graph persistence & cross-run learning (sweep2)

We re-ran the same production sweep with **new provider codes** (`sweep2_*`) specifically to verify that the knowledge graph is **learning across runs** (i.e., tables in schema `kg` accumulate new nodes/edges, rather than staying empty).

### Sweep2 run IDs

| Provider | Run ID |
|---|---|
| sweep2_stripe | `run_7b53e7dd_1765574272` |
| sweep2_github | `run_36d7200f_1765574493` |
| sweep2_slack | `run_c23403d8_1765574828` |
| sweep2_twilio | `run_e572330a_1765574932` |
| sweep2_petstore | `run_13767dc4_1765575090` |

### What we observed in Postgres after sweep2

- LangGraph checkpoints table `public.checkpoints`: **6357** total rows
  - Previously observed pre-sweep2 baseline: **6252** (Δ **+105**)
- Knowledge graph tables (schema `kg`):
  - `kg.nodes`: **621**
  - `kg.edges`: **620**
  - `kg.workflow_steps`: **25**
  - `kg.step_bindings`: **5**
  - `kg.confidence_history`: **0** (still empty)

### Learning attribution (by provider + run)

The `kg.nodes` table includes both `provider_code` and `source_run_id`, which lets us attribute what was learned.

- Nodes by provider:
  - `sweep2_github`: 264
  - `sweep2_stripe`: 217
  - `sweep2_slack`: 53
  - `sweep2_twilio`: 53
  - `sweep2_petstore`: 27

- Node types (top):
  - `entity`: 380
  - `endpoint`: 219

- Edge types (top):
  - `belongs_to_provider`: 604

This is enough evidence that:

1) **KG persistence is active** in the production Postgres backend.
2) **Each run contributes new nodes/edges**, tracked via `source_run_id`.

Open question: why `kg.confidence_history` remains empty even though nodes have a `confidence_score` field.

## Per-run notes

### Stripe (`sweep_stripe`)

- Primary endpoint selected: `POST /v1/checkout/sessions`
- Artifacts:
  - `src/custom_integrations/api_clients/sweep_stripe.py`
  - `src/custom_integrations/workflows/sweep_stripe_create_checkout_session.py`
  - `tests/custom_integrations/test_sweep_stripe_create_checkout_session.py`

### GitHub (`sweep_github`)

- Artifacts:
  - `src/custom_integrations/api_clients/sweep_github.py`
  - `src/custom_integrations/workflows/sweep_github_create_github_issue.py`
  - `tests/custom_integrations/test_sweep_github_create_github_issue.py`

### Slack (`sweep_slack`)

- Artifacts:
  - `src/custom_integrations/api_clients/sweep_slack.py`
  - `src/custom_integrations/workflows/sweep_slack_post_message_to_slack_channel.py`
  - `tests/custom_integrations/test_sweep_slack_post_message_to_slack_channel.py`

### Twilio (`sweep_twilio`)

- Artifacts:
  - `src/custom_integrations/api_clients/sweep_twilio.py`
  - `src/custom_integrations/workflows/sweep_twilio_send_sms_and_return_message_sid.py`
  - `tests/custom_integrations/test_sweep_twilio_send_sms_and_return_message_sid.py`

### Petstore (`sweep_petstore`)

- Artifacts:
  - `src/custom_integrations/api_clients/sweep_petstore.py`
  - `src/custom_integrations/workflows/sweep_petstore_create_petstore_pet.py`
  - `tests/custom_integrations/test_sweep_petstore_create_petstore_pet.py`

## Key issues observed (actionable)

1) **CLI/report “Persistence: Status: unknown”** even though persistence is working (runs wrote to Postgres, checkpoints exist). This should report actual backend + success.

2) **No spec chunks embedded** for Stripe in the report (`Chunks: 0`). For large specs, chunking/embedding should be non-zero or we should clearly explain why it’s skipped.

3) **DB connection cleanup warnings** (psycopg “rolling back returned connection” + wrapper GC warning). Indicates some code paths return pooled connections still in transaction or without a context manager.

4) **Spec URL brittleness** for sweep automation (Zoom, OAI examples). We should pin official sources and/or vendor the sweep copies under versioned `data/spec_sweep/<date>/`.

## Suggested next improvements

- Fix persistence status reporting in the report.
- Ensure spec chunking/embedding is consistently applied (or intentionally disabled with explicit reason).
- Audit Postgres connection lifecycle; enforce `with get_connection() as conn:` everywhere.
- Add a `scripts/run_production_spec_sweep.py` runner that:
  - downloads specs,
  - runs the CLI for each,
  - writes a markdown report automatically.

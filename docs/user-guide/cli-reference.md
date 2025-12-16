# CLI Reference

The Integration Co-Worker CLI provides commands for running integrations, managing the knowledge graph, and monitoring the system.

## Main Command

```bash
integration-coworker [OPTIONS] COMMAND [ARGS]...
```

## Core Commands

### `run`

Run an integration workflow.

```bash
integration-coworker run \
  --spec-ref <SPEC_PATH> \
  --task <TASK_DESCRIPTION> \
  [--provider <PROVIDER_CODE>] \
  [--repo-root <REPO_PATH>] \
  [--dry-run]
```

**Options:**

| Option | Description |
|--------|-------------|
| `-s, --spec-ref` | Path or URL to OpenAPI spec (required) |
| `-t, --task` | Task description in natural language (required) |
| `--provider` | Provider code (e.g., `stripe`, `twilio`) |
| `--repo-root` | Target repository for code integration |
| `--dry-run` | Don't persist results or write files |
| `--json-output` | Output results as JSON |

**Example:**

```bash
integration-coworker run \
  -s specs/stripe_api.json \
  -t "Create a payment checkout session" \
  --provider stripe \
  --dry-run
```

### `demo`

Run a demonstration workflow with sample data.

```bash
integration-coworker demo [--persist]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--persist` | Save results to database |

### `status`

Check system status and configuration.

```bash
integration-coworker status
```

### `health`

Run health checks.

```bash
integration-coworker health [--verbose] [--check-llm]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--verbose` | Show detailed output |
| `--check-llm` | Test LLM connectivity |

### `init-db`

Initialize the database schema.

```bash
integration-coworker init-db
```

## Knowledge Graph Commands

### `kg-dump`

Export the knowledge graph to JSON.

```bash
integration-coworker kg-dump --output <FILE>
```

### `kg-query`

Query the knowledge graph.

```bash
integration-coworker kg-query "<QUERY>"
```

### `kg-confidence`

Display confidence scores for templates and patterns.

```bash
integration-coworker kg-confidence
```

## Feedback Commands

### `feedback`

Submit feedback on a run.

```bash
integration-coworker feedback --run-id <RUN_ID> --rating <1-5> --comment "<TEXT>"
```

### `feedback-sync`

Synchronize feedback with the database.

```bash
integration-coworker feedback-sync
```

## Cache Commands

### `cache-stats`

Display LLM cache statistics.

```bash
integration-coworker cache-stats
```

**Output:**

```
LLM Cache Statistics
====================
Total Requests: 150
Cache Hits: 120
Cache Misses: 30
Hit Rate: 80.0%
Estimated Savings: $12.50
```

### `cache-clear`

Clear the LLM response cache.

```bash
integration-coworker cache-clear [--confirm]
```

## UI Command

### `ui`

Launch the Streamlit web interface.

```bash
integration-coworker ui [--port <PORT>]
```

**Options:**

| Option | Description | Default |
|--------|-------------|---------|
| `--port` | Port to run the UI on | `8501` |

## Resume Command

### `resume`

Resume a failed or interrupted run.

```bash
integration-coworker resume --run-id <RUN_ID>
```

## Environment Variables

Many CLI behaviors can be controlled via environment variables:

```bash
# Database
export DATABASE_URL="postgresql://..."
export USE_SQLITE=true

# LLM
export USE_MOCK_LLM=true
export LLM_CACHE_ENABLED=true

# Workflow
export PARALLEL_WORKFLOW=true
```

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | General error |
| `2` | Invalid arguments |
| `3` | Database error |
| `4` | LLM error |

---

[Back to Getting Started](../getting-started/configuration.md) | [Web UI →](web-ui.md)

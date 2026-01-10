# Recovery

Resume interrupted runs and handle failures gracefully.

## Overview

Integration Co-Worker provides checkpoint-based recovery for long-running workflows.

## Checkpoints

The workflow creates checkpoints at key stages:

| Checkpoint | After Node | Contains |
|------------|------------|----------|
| Silver | `persist_silver_checkpoint` | Endpoints, schemas, entities |
| Gold | `persist_gold_checkpoint` | Task, flow, code artifacts |
| Outcome | `persist_run_outcome` | Final status, metrics |

## Resuming Runs

### From CLI

```bash
# Resume a failed run
integration-coworker resume --run-id <RUN_ID>
```

### Programmatic

```python
from integration_coworker.api.entrypoint import resume_run

result = resume_run(run_id="abc-123")
```

## Finding Run IDs

### From CLI

```bash
# List recent runs
integration-coworker status --list-runs

# Get run details
integration-coworker status --run-id <RUN_ID>
```

### From Database

```sql
SELECT run_id, status, created_at 
FROM integration_runs 
ORDER BY created_at DESC 
LIMIT 10;
```

## Recovery Behavior

### What Gets Restored

- ✅ All persisted state (Silver, Gold layers)
- ✅ Run configuration and options
- ✅ Node completion status

### What Gets Re-executed

- ❌ Incomplete nodes re-run from the start
- ❌ Nodes after failure point re-run
- ❌ Validation and finalization nodes

## Error Handling

### Graceful Degradation

When errors occur:

1. Error is captured in `WorkflowState.errors`
2. `degraded_mode` flag is set
3. `degraded_reason` explains the issue
4. Workflow continues if possible

### Handle Error Node

The `handle_error` node processes failures:

```python
def handle_error(state: WorkflowState) -> WorkflowState:
    state.degraded_mode = True
    state.degraded_reason = state.errors[-1] if state.errors else "Unknown error"
    return state
```

### Report Generation

Reports are always generated, even after errors:

```markdown
## Run Status: PARTIAL

### Errors Encountered
- Validation failed: Missing endpoint binding for step 3

### Completed Successfully
- ✅ Spec ingestion
- ✅ Silver model extraction
- ✅ Task understanding
- ⚠️ Flow planning (partial)
- ❌ Code generation (skipped)
```

## Dry Run Recovery

Dry runs don't persist checkpoints, so they cannot be resumed:

```bash
# This won't work for dry runs
integration-coworker resume --run-id <DRY_RUN_ID>
# Error: No checkpoint found for run
```

## Idempotency

Runs are designed to be idempotent:

- Same inputs produce same outputs
- Re-running a completed workflow is safe
- SHA-256 hashes detect duplicate specs

## Best Practices

### Long-Running Workflows

For large specs:

```bash
# Enable persistence
integration-coworker run \
  -s large-spec.yaml \
  -t "Complex task" \
  --persist  # Enables checkpoints
```

### Monitoring Progress

```bash
# In another terminal
integration-coworker status --run-id <RUN_ID> --watch
```

### Handling Persistent Failures

If a run repeatedly fails:

1. Check the error message in the report
2. Verify inputs (spec, task description)
3. Check LLM connectivity
4. Try with `--verbose` for detailed logs

```bash
integration-coworker run \
  -s spec.yaml \
  -t "Task" \
  --verbose 2>&1 | tee debug.log
```

---

[Back to Parallel Execution](parallel-execution.md) | [Architecture →](../development/architecture.md)

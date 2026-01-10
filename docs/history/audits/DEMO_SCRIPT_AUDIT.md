# Demo Script Audit & Production Testing Improvements

**Date**: December 18, 2025
**Script**: `scripts/demo-final-showcase.sh`

## Executive Summary

The demo script has several issues that could cause inconsistent results, false positives/negatives, and mask real bugs. This document outlines the issues and recommended fixes.

---

## 🔴 Critical Issues

### 1. No LLM Cache Clearing Between Runs

**Problem**: The script doesn't clear the Redis LLM cache before running. This means:
- Subsequent runs may use stale cached responses from previous runs
- Bugs in prompt engineering may be masked by old cached responses
- Different prompts could get same responses if cache key collision occurs

**Evidence**:
```
Cache stats: hits=67, misses=77
Total Redis keys: 56 (all with TTL ~24h)
```

**Fix**:
```bash
# Add to demo script before runs
if [ "$SKIP_CLEANUP" != "true" ]; then
    info "Clearing LLM cache for clean demo run..."
    redis-cli FLUSHDB || $PYTHON_BIN -c "
from integration_coworker.llm.cache import get_llm_cache
cache = get_llm_cache()
if cache.is_available():
    import redis
    import os
    r = redis.from_url(os.environ.get('REDIS_URL', 'redis://localhost:6379'))
    r.flushdb()
    print('LLM cache cleared')
"
fi
```

### 2. No Database Reset for Clean State Testing

**Problem**: The script clears KG tables but NOT:
- `integration_gold.run_checkpoints` - May contain corrupted state from failed runs
- `spec_silver.*` tables - May have stale/corrupted spec data
- `public.checkpoints` (LangGraph) - May cause auto-resume to wrong state

**Evidence**:
```
Total checkpoints: 8
Distinct runs: 1  # Only 1 run despite multiple demo runs - stale data
Spec chunks: 7066
Endpoints: 585
```

**Fix**:
```bash
# Add full database reset option
if [ "$FULL_RESET" = "true" ]; then
    info "Full database reset (including gold/silver tables)..."
    $PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    # Clear in dependency order
    for table in [
        'integration_gold.run_checkpoints',
        'integration_gold.code_artifacts', 
        'integration_gold.policies',
        'integration_gold.workflow_templates',
        'public.checkpoints',
        'public.checkpoint_writes',
        'public.checkpoint_blobs',
    ]:
        cur.execute(f'TRUNCATE {table} CASCADE')
    conn.commit()
    print('Gold tables cleared')
"
fi
```

### 3. `--auto-resume` Flag May Mask Bugs

**Problem**: The `--auto-resume` flag causes the CLI to resume from last checkpoint instead of starting fresh. This can:
- Resume from corrupted state
- Skip nodes that failed previously
- Make it impossible to reproduce bugs

**Current Usage**:
```bash
$PYTHON_BIN -m integration_coworker.cli run \
    ...
    $( [ "${AUTO_RESUME_DEFAULT}" = "true" ] && echo "--auto-resume" )
```

**Fix**: For demo/testing, disable auto-resume by default:
```bash
AUTO_RESUME_DEFAULT=${AUTO_RESUME_DEFAULT:-false}  # Changed from true
```

---

## 🟠 Important Issues

### 4. Spec Caching May Cause Stale Data Issues

**Problem**: The demo script references `spec_silver.source_refs` for caching proof (Step 6), but the actual table is `spec_silver.spec_documents`. This indicates the script is out of sync with the actual schema.

**Evidence**: Script line 753 references non-existent table
```bash
# Check spec_silver.source_refs for cached entries  # WRONG TABLE
```

**Fix**: Update script to use correct table names.

### 5. No Validation of Clean Environment State

**Problem**: The script doesn't verify the environment is in a known-good state before running.

**Fix**: Add pre-flight checks:
```bash
step "Step 0.0: Pre-flight Environment Validation"

# Check for zombie processes
ZOMBIE_PROCS=$(pgrep -f "integration_coworker" | grep -v $$ || true)
if [ -n "$ZOMBIE_PROCS" ]; then
    warn "Found running integration_coworker processes: $ZOMBIE_PROCS"
    warn "These may interfere with demo. Consider: pkill -f integration_coworker"
fi

# Check database connection pool
$PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    cur.execute('SELECT count(*) FROM pg_stat_activity WHERE state = \\'active\\'')
    active = cur.fetchone()[0]
    if active > 10:
        print(f'WARNING: {active} active DB connections - possible connection leak')
"

# Check disk space for logs
LOG_SPACE=$(df -h "$LOG_DIR" | tail -1 | awk '{print $5}' | tr -d '%')
if [ "$LOG_SPACE" -gt 90 ]; then
    warn "Log directory disk usage at ${LOG_SPACE}% - consider cleanup"
fi
```

### 6. No Spec Hash Verification

**Problem**: The script doesn't verify that spec files haven't been modified since last run. If a spec file is edited, the cache may return stale parsed data.

**Fix**: Add spec file hash tracking:
```bash
# Generate spec hashes
SPEC_HASHES_FILE="${LOG_DIR}/spec_hashes_${TIMESTAMP}.txt"
for spec in "$SPECS_DIR"/*.json "$SPECS_DIR"/*.yaml; do
    if [ -f "$spec" ]; then
        sha256sum "$spec" >> "$SPEC_HASHES_FILE"
    fi
done

# Compare with last run
LAST_HASHES=$(ls -t "${LOG_DIR}"/spec_hashes_*.txt 2>/dev/null | head -2 | tail -1)
if [ -n "$LAST_HASHES" ] && [ -f "$LAST_HASHES" ]; then
    if ! diff -q "$SPEC_HASHES_FILE" "$LAST_HASHES" >/dev/null 2>&1; then
        warn "Spec files have changed since last run!"
        diff "$LAST_HASHES" "$SPEC_HASHES_FILE" || true
    fi
fi
```

---

## 🟡 Minor Issues

### 7. Hardcoded Paths

**Problem**: The script has hardcoded paths:
```bash
TARGET_REPO="/Users/julianbartosz/git/schoolwork/UPlant/testing-solver-agentic-spec-coworker"
```

**Fix**: Make configurable via environment variable:
```bash
TARGET_REPO="${TARGET_REPO:-$(mktemp -d)}"
```

### 8. No Timeout for Individual Runs

**Problem**: If a single spec run hangs, the entire demo hangs.

**Fix**: Add per-run timeout:
```bash
RUN_TIMEOUT=${RUN_TIMEOUT:-300}  # 5 minutes default

timeout $RUN_TIMEOUT $PYTHON_BIN -m integration_coworker.cli run \
    --spec-ref "${SPECS_DIR}/${SPEC_FILE}" \
    ...
```

### 9. Missing Error Aggregation

**Problem**: Errors are logged but not aggregated for summary. Hard to see overall health.

**Fix**: Track errors and report at end:
```bash
# At start
ERRORS_FILE="${LOG_DIR}/errors_${TIMESTAMP}.txt"
touch "$ERRORS_FILE"

# In run loop, when error occurs
echo "${SPEC_FILE}:${EXIT_CODE}:${error_message}" >> "$ERRORS_FILE"

# At end
step "Error Summary"
if [ -s "$ERRORS_FILE" ]; then
    warn "Errors occurred during demo:"
    cat "$ERRORS_FILE" | while read line; do
        log "  ❌ $line"
    done
else
    success "No errors recorded"
fi
```

---

## 📋 Recommended Demo Profiles

### Profile 1: `--fresh` (Clean State Testing)
```bash
./scripts/demo-final-showcase.sh --fresh
# - Clears ALL database tables
# - Flushes Redis/LLM cache
# - Disables auto-resume
# - Full spec processing from scratch
```

### Profile 2: `--incremental` (Default)
```bash
./scripts/demo-final-showcase.sh
# - Clears only KG tables
# - Preserves LLM cache (faster)
# - Uses spec caching
# - Good for iteration
```

### Profile 3: `--production` (Production Validation)
```bash
./scripts/demo-final-showcase.sh --production
# - Fresh state
# - Production profile enabled
# - All validation gates enabled
# - Strict timeouts
# - Error = failure (no continue on error)
```

---

## 🔧 Implementation Priority

1. **HIGH**: Add `--fresh` flag with cache/DB clearing (prevents stale data bugs)
2. **HIGH**: Fix auto-resume default to false for demo
3. **MEDIUM**: Add pre-flight environment validation
4. **MEDIUM**: Fix incorrect table references in script
5. **LOW**: Add spec hash verification
6. **LOW**: Make paths configurable

---

## Appendix: Current Database State Analysis

```
=== Run Checkpoints ===
Total checkpoints: 8
Distinct runs: 1 (stale - should be more)

=== KG Data ===
Nodes: 7 (only standard patterns)
Edges: 0

=== Spec Silver ===
Spec documents: 1
Spec chunks: 7066
Endpoints: 585

=== LLM Cache ===
Keys: 56
TTL: ~24h average
Hit rate: 46% (67 hits / 144 total)
```

This state shows the database has accumulated data from previous runs that isn't being cleared, which could cause the `'str' object has no attribute 'get'` error if corrupted checkpoint data is being resumed.

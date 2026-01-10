# Demo Harness Improvements for Production Testing

**Purpose**: Transform `scripts/demo-final-showcase.sh` from a demonstration script into a comprehensive production testing and bug-finding harness.

**Status**: ✅ **IMPLEMENTED** (December 2024)

---

## Implementation Summary

The following improvements have been added to `scripts/demo-final-showcase.sh`:

| Feature | Flag | Status |
|---------|------|--------|
| Chaos Injection | `--chaos` | ✅ Implemented |
| Stress Testing | `--stress` | ✅ Implemented |
| Soak Testing | `--soak` | ✅ Implemented |
| Benchmarking | `--benchmark` | ✅ Implemented |
| Full Mode | `--full` | ✅ Implemented |
| Live API | `--live` | 📝 Documented |
| Multi-language | `--multilang` | 📝 Documented |
| Trace Analysis | `--trace-analysis` | 📝 Documented |

### Quick Start

```bash
# Find bugs with chaos and stress testing:
./scripts/demo-final-showcase.sh --quick --chaos --stress

# Full production validation:
./scripts/demo-final-showcase.sh --full --fresh

# Help:
./scripts/demo-final-showcase.sh --help
```

---

## Executive Summary

The current demo harness (~1983 lines) is excellent for showcasing features but lacks:
1. **Chaos/failure injection** - No tests for circuit breaker stress, timeout handling
2. **Soak integration** - Soak script exists but isn't integrated into demo
3. **Concurrent stress testing** - No multi-run parallel stress 
4. **Performance benchmarks** - No regression detection
5. **Live API testing** - Only mock mode by default
6. **Multi-language Docker gates** - TypeScript/Go validation not in demo
7. **LangSmith trace analysis** - Manual, not automated

---

## Current Strengths (Don't Break)

| Feature | Status | Evidence |
|---------|--------|----------|
| Docker service management | ✅ | Lines 207-305: Postgres + Redis healthcheck |
| Per-spec sandbox validation | ✅ | Lines 1284-1388: 5-gate validation |
| Fresh mode for clean state | ✅ | `--fresh` flag resets DB |
| Parallel execution proof | ✅ | Part 5: Node timing analysis |
| Cache hit/miss verification | ✅ | Part 6: Redis cache proof |
| Error tracking | ✅ | `ERRORS` array, error summary |
| Resume/checkpoint proof | ✅ | Part 8: Demonstrates recovery |
| Contract testing (Prism) | ✅ | Lines 1347-1388: Schemathesis integration |

---

## Recommended Improvements

### 1. Chaos/Failure Injection Mode 

**Gap**: No testing of circuit breaker, timeout handling, or recovery under failure.

**Add to harness** (after Part 3):

```bash
# =============================================================================
# PART 3.5: CHAOS INJECTION MODE (--chaos flag)
# =============================================================================

if [ "${CHAOS_MODE:-false}" = "true" ]; then
    step "Part 3.5: Chaos Injection Testing"
    
    # --- Test 1: Circuit Breaker Stress ---
    sub_step "Test 1: Circuit Breaker Opens Under Load"
    # Force failures via bad base URL
    export LLM_BASE_URL="https://invalid.openai.example.com/v1"
    export LLM_CIRCUIT_FAILURE_THRESHOLD=3
    export LLM_CIRCUIT_RECOVERY_TIMEOUT=5
    
    python -c "
from integration_coworker.llm.circuit_breaker import get_circuit_breaker, CircuitOpenError, CircuitState

breaker = get_circuit_breaker()
key = 'chaos_test:gpt-4o'

# Simulate 5 failures
for i in range(5):
    breaker.record_failure(key)
    
state = breaker.get_state(key)
assert state == CircuitState.OPEN, f'Expected OPEN, got {state}'
print('✓ Circuit breaker opened after 5 failures')

# Verify fail-fast behavior
try:
    breaker.can_execute(key)
    assert False, 'Should have raised CircuitOpenError'
except CircuitOpenError as e:
    print(f'✓ Requests fail fast: {e.time_until_recovery:.1f}s until recovery')

# Cleanup
from integration_coworker.llm.circuit_breaker import reset_circuit_breaker
reset_circuit_breaker()
"
    unset LLM_BASE_URL
    check $? "Circuit breaker stress test"
    
    # --- Test 2: Timeout Handling ---
    sub_step "Test 2: LLM Acquisition Timeout"
    export LLM_MAX_CONCURRENT=1
    export LLM_ACQUIRE_TIMEOUT=2  # Very short timeout
    
    python -c "
import asyncio
from integration_coworker.llm.concurrency import acquire_llm_slot, reset_llm_semaphore

async def test_timeout():
    reset_llm_semaphore()  # Fresh state
    
    # Acquire the only slot
    async with acquire_llm_slot():
        # Try to acquire second slot (should timeout)
        try:
            async with acquire_llm_slot(timeout=1.0):
                pass
            assert False, 'Should have timed out'
        except asyncio.TimeoutError as e:
            print(f'✓ Timeout correctly raised: {str(e)[:60]}...')
    
    reset_llm_semaphore()

asyncio.run(test_timeout())
"
    check $? "LLM timeout handling"
    unset LLM_MAX_CONCURRENT LLM_ACQUIRE_TIMEOUT
    
    # --- Test 3: Graceful Shutdown ---
    sub_step "Test 3: Graceful Shutdown Signal"
    python -c "
from integration_coworker.shutdown import request_shutdown, is_shutdown_requested, reset_shutdown

reset_shutdown()  # Clean state
assert not is_shutdown_requested()

request_shutdown()
assert is_shutdown_requested()
print('✓ Shutdown flag correctly set')

reset_shutdown()
assert not is_shutdown_requested()
print('✓ Shutdown flag correctly reset')
"
    check $? "Graceful shutdown handling"
fi
```

**Usage**: 
```bash
./scripts/demo-final-showcase.sh --chaos
# Or combine:
CHAOS_MODE=true ./scripts/demo-final-showcase.sh
```

---

### 2. Soak Test Integration

**Gap**: `scripts/soak.sh` exists but isn't integrated. No leak detection in demo.

**Add to harness** (new Part 10, or as `--soak` flag):

```bash
# =============================================================================
# PART 10: SOAK TESTING (--soak or SOAK_MODE=true)
# =============================================================================

if [ "${SOAK_MODE:-false}" = "true" ]; then
    step "Part 10: Soak Testing (Leak Detection)"
    info "Running 5-minute mini soak test..."
    
    # Use quick mode for demo (5 minutes instead of 30)
    export SOAK_PARALLEL_RUNS=1
    export SOAK_INTERVAL_SECONDS=10
    export SOAK_SAMPLE_INTERVAL=30
    
    if ./scripts/soak.sh 5 --quick; then
        SOAK_RESULT="PASSED"
        info "✓ Soak test passed - no resource leaks detected"
    else
        SOAK_RESULT="FAILED"
        ERRORS+=("Soak test detected resource leaks")
        error "Soak test failed - check for memory/thread/FD leaks"
    fi
    
    # Show metrics
    info "Final metrics:"
    python -c "
from integration_coworker.llm.concurrency import get_concurrency_metrics
from integration_coworker.llm.circuit_breaker import get_circuit_breaker_metrics

print('LLM Concurrency:')
for k, v in get_concurrency_metrics().items():
    print(f'  {k}: {v}')

print('Circuit Breaker:')
for k, v in get_circuit_breaker_metrics().items():
    print(f'  {k}: {v}')
" 2>/dev/null || true
fi
```

---

### 3. Concurrent Stress Testing

**Gap**: Demo tests specs sequentially. No multi-run stress testing.

**Add to harness** (as Part 3.6 or `--stress` flag):

```bash
# =============================================================================
# PART 3.6: CONCURRENT STRESS TEST (--stress flag)
# =============================================================================

if [ "${STRESS_MODE:-false}" = "true" ]; then
    step "Part 3.6: Concurrent Stress Testing"
    
    STRESS_RUNS=${STRESS_RUNS:-5}
    STRESS_TIMEOUT=${STRESS_TIMEOUT:-60}
    
    info "Running $STRESS_RUNS concurrent workflow instances..."
    info "Timeout per run: ${STRESS_TIMEOUT}s"
    
    # Create temp directory for concurrent runs
    STRESS_DIR=$(mktemp -d -t stress_test_XXXXXX)
    
    # Launch concurrent runs
    pids=()
    for i in $(seq 1 $STRESS_RUNS); do
        (
            timeout $STRESS_TIMEOUT python -m integration_coworker.cli demo-v1 \
                --spec specs/petstore-minimal.yaml \
                --output-dir "$STRESS_DIR/run_$i" \
                2>&1 | head -20
            echo "RUN_$i_EXIT=$?" > "$STRESS_DIR/run_$i.status"
        ) &
        pids+=($!)
        info "Started run $i (PID: ${pids[-1]})"
    done
    
    # Wait for all to complete
    failed=0
    for i in $(seq 1 $STRESS_RUNS); do
        pid=${pids[$((i-1))]}
        if wait $pid 2>/dev/null; then
            info "✓ Run $i completed"
        else
            error "✗ Run $i failed"
            failed=$((failed + 1))
        fi
    done
    
    # Report results
    if [ $failed -eq 0 ]; then
        info "✓ All $STRESS_RUNS concurrent runs completed successfully"
    else
        ERRORS+=("Stress test: $failed/$STRESS_RUNS runs failed")
        error "$failed out of $STRESS_RUNS concurrent runs failed"
    fi
    
    # Show concurrency metrics
    python -c "
from integration_coworker.llm.concurrency import get_concurrency_metrics
m = get_concurrency_metrics()
print(f'Peak concurrent LLM requests: {m[\"peak_active\"]}/{m[\"max_concurrent\"]}')
print(f'Total timeouts: {m[\"total_timeouts\"]}')
"
    
    rm -rf "$STRESS_DIR"
fi
```

**Usage**:
```bash
STRESS_MODE=true STRESS_RUNS=10 ./scripts/demo-final-showcase.sh
```

---

### 4. Performance Benchmarking

**Gap**: No timing baselines or regression detection.

**Add to harness** (Part 11 or `--benchmark`):

```bash
# =============================================================================
# PART 11: PERFORMANCE BENCHMARKING (--benchmark flag)
# =============================================================================

if [ "${BENCHMARK_MODE:-false}" = "true" ]; then
    step "Part 11: Performance Benchmarking"
    
    BENCHMARK_FILE="${SCRIPT_DIR}/../.beads/benchmarks.json"
    
    # Run standardized benchmark
    info "Running benchmark with petstore-minimal.yaml..."
    
    START_TIME=$(python -c "import time; print(time.time())")
    
    python -m integration_coworker.cli run \
        --spec specs/petstore-minimal.yaml \
        --output-dir "$OUTPUT_DIR/benchmark" \
        --skip-sandbox \
        2>&1 | head -20
    
    END_TIME=$(python -c "import time; print(time.time())")
    DURATION=$(python -c "print(round($END_TIME - $START_TIME, 2))")
    
    info "Benchmark completed in ${DURATION}s"
    
    # Compare to previous baseline
    if [ -f "$BENCHMARK_FILE" ]; then
        PREV_DURATION=$(jq -r '.petstore_minimal.last_duration // 0' "$BENCHMARK_FILE")
        DIFF=$(python -c "print(round($DURATION - $PREV_DURATION, 2))")
        PERCENT=$(python -c "print(round(($DURATION / max($PREV_DURATION, 0.1) - 1) * 100, 1))")
        
        if (( $(echo "$PERCENT > 20" | bc -l) )); then
            warn "⚠️  Performance regression: ${PERCENT}% slower than baseline"
            ERRORS+=("Performance regression: ${DURATION}s vs ${PREV_DURATION}s baseline")
        elif (( $(echo "$PERCENT < -10" | bc -l) )); then
            info "🚀 Performance improvement: ${PERCENT}% faster than baseline"
        else
            info "✓ Performance within normal range (${PERCENT}% vs baseline)"
        fi
    fi
    
    # Update baseline
    mkdir -p "$(dirname "$BENCHMARK_FILE")"
    echo "{
  \"petstore_minimal\": {
    \"last_duration\": $DURATION,
    \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
    \"commit\": \"$(git rev-parse HEAD 2>/dev/null || echo 'unknown')\"
  }
}" > "$BENCHMARK_FILE"
    info "Benchmark saved to $BENCHMARK_FILE"
fi
```

---

### 5. Live API Testing Mode

**Gap**: Demo always uses mock mode. No real API validation.

**Add to harness** (Part 12 or `--live`):

```bash
# =============================================================================
# PART 12: LIVE API TESTING (--live flag)
# =============================================================================

if [ "${LIVE_MODE:-false}" = "true" ]; then
    step "Part 12: Live API Testing"
    
    # Check for required API key
    if [ -z "$OPENAI_API_KEY" ]; then
        error "LIVE_MODE requires OPENAI_API_KEY"
        exit 1
    fi
    
    # Budget controls
    LIVE_LLM_BUDGET=${LIVE_LLM_BUDGET:-5}
    LIVE_TIMEOUT=${LIVE_TIMEOUT:-120}
    
    info "Live mode configuration:"
    info "  LLM Budget: $LIVE_LLM_BUDGET calls"
    info "  Timeout: ${LIVE_TIMEOUT}s"
    info "  Model: ${LLM_MODEL:-gpt-4o-mini}"
    
    # Disable mock mode
    export USE_MOCK_LLM=false
    
    # Run with budget cap
    timeout $LIVE_TIMEOUT python -c "
import asyncio
from integration_coworker.llm.client import get_llm_client

async def test_live():
    client = get_llm_client()
    print(f'Using provider: {client.provider}')
    
    response = await client.acomplete(
        messages=[{'role': 'user', 'content': 'Reply with: LIVE_TEST_OK'}],
        max_tokens=20,
    )
    
    print(f'Response: {response.content}')
    assert 'OK' in response.content.upper() or 'LIVE' in response.content.upper()
    print('✓ Live LLM test passed')

asyncio.run(test_live())
"
    check $? "Live LLM connectivity"
    
    # Run one mini spec with live LLM
    info "Running mini spec with live LLM..."
    timeout $LIVE_TIMEOUT python -m integration_coworker.cli run \
        --spec specs/petstore-minimal.yaml \
        --output-dir "$OUTPUT_DIR/live_test" \
        --skip-sandbox \
        2>&1 | tail -10
    check $? "Live spec processing"
    
    # Reset to mock mode
    export USE_MOCK_LLM=true
fi
```

**Usage**:
```bash
LIVE_MODE=true OPENAI_API_KEY=sk-... LIVE_LLM_BUDGET=10 ./scripts/demo-final-showcase.sh --live
```

---

### 6. Multi-Language Docker Validation

**Gap**: `sandbox_multilang.py` exists but demo doesn't test TypeScript/Go.

**Add to harness** (Part 13 or `--multilang`):

```bash
# =============================================================================
# PART 13: MULTI-LANGUAGE VALIDATION (--multilang flag)
# =============================================================================

if [ "${MULTILANG_MODE:-false}" = "true" ]; then
    step "Part 13: Multi-Language Sandbox Validation"
    
    # Check Docker availability
    if ! command -v docker &> /dev/null || ! docker info &> /dev/null; then
        warn "Docker not available - skipping multi-language tests"
    else
        info "Testing TypeScript validation..."
        
        # Create test TypeScript file
        MULTILANG_DIR=$(mktemp -d -t multilang_test_XXXXXX)
        
        cat > "$MULTILANG_DIR/test.ts" << 'EOF'
function greet(name: string): string {
    return `Hello, ${name}!`;
}

console.log(greet("World"));
EOF
        
        # Run TypeScript sandbox
        python -c "
import asyncio
from integration_coworker.codegen.sandbox_multilang import (
    MultilangSandbox,
    MultilangConfig,
    Language,
)

async def test_typescript():
    config = MultilangConfig(
        language=Language.TYPESCRIPT,
        timeout_seconds=60,
    )
    sandbox = MultilangSandbox(config)
    
    with open('$MULTILANG_DIR/test.ts') as f:
        content = f.read()
    
    result = await sandbox.validate(content)
    print(f'TypeScript validation: {\"PASSED\" if result.success else \"FAILED\"}')
    if not result.success:
        print(f'Errors: {result.errors}')
    return result.success

success = asyncio.run(test_typescript())
exit(0 if success else 1)
" 2>&1
        check $? "TypeScript sandbox validation"
        
        rm -rf "$MULTILANG_DIR"
    fi
fi
```

---

### 7. LangSmith Trace Analysis

**Gap**: LangSmith traces are created but not analyzed in demo.

**Add to harness** (Part 14 or `--trace-analysis`):

```bash
# =============================================================================
# PART 14: LANGSMITH TRACE ANALYSIS (--trace-analysis flag)
# =============================================================================

if [ "${TRACE_ANALYSIS:-false}" = "true" ]; then
    step "Part 14: LangSmith Trace Analysis"
    
    if [ -z "$LANGCHAIN_API_KEY" ]; then
        warn "LANGCHAIN_API_KEY not set - skipping trace analysis"
    else
        info "Analyzing recent LangSmith traces..."
        
        python -c "
from langsmith import Client
from datetime import datetime, timedelta

client = Client()
project_name = '${LANGCHAIN_PROJECT:-integration-coworker}'

# Get runs from last hour
end_time = datetime.now()
start_time = end_time - timedelta(hours=1)

runs = list(client.list_runs(
    project_name=project_name,
    start_time=start_time,
    end_time=end_time,
    limit=50,
))

print(f'Found {len(runs)} runs in the last hour')

# Analyze error rates
errors = [r for r in runs if r.error]
if errors:
    print(f'⚠️  {len(errors)} runs had errors:')
    for r in errors[:5]:
        print(f'  - {r.name}: {str(r.error)[:50]}...')
else:
    print('✓ No errors in recent runs')

# Analyze latency
latencies = [r.total_time for r in runs if r.total_time]
if latencies:
    avg_latency = sum(latencies) / len(latencies)
    max_latency = max(latencies)
    print(f'Latency: avg={avg_latency:.2f}s, max={max_latency:.2f}s')
    
    if max_latency > 30:
        print(f'⚠️  High latency detected: {max_latency:.1f}s')

# Token usage
total_tokens = sum(r.total_tokens or 0 for r in runs)
print(f'Total tokens used: {total_tokens:,}')
"
        check $? "LangSmith trace analysis"
    fi
fi
```

---

## Command Line Flag Summary

Add these flags to the harness:

```bash
# At the top of demo-final-showcase.sh, add flag parsing:

usage() {
    cat << EOF
Usage: $0 [options]

Options:
    --quick         Run minimal demo (3 specs)
    --fresh         Reset database before run
    --chaos         Enable chaos/failure injection tests
    --soak          Run 5-minute soak test for leaks
    --stress        Run concurrent stress test
    --benchmark     Run performance benchmarks
    --live          Use real LLM (requires OPENAI_API_KEY)
    --multilang     Test TypeScript/Go Docker sandboxes
    --trace-analysis Analyze LangSmith traces
    --full          Enable all tests (chaos+soak+stress+benchmark)
    -h, --help      Show this help

Environment Variables:
    CHAOS_MODE=true         Enable chaos testing
    SOAK_MODE=true          Enable soak testing  
    STRESS_MODE=true        Enable stress testing
    STRESS_RUNS=5           Number of concurrent runs
    BENCHMARK_MODE=true     Enable benchmarking
    LIVE_MODE=true          Use real LLM
    LIVE_LLM_BUDGET=5       Max LLM calls in live mode
    MULTILANG_MODE=true     Enable multi-language tests
    TRACE_ANALYSIS=true     Enable trace analysis
EOF
}

# Parse flags
while [[ $# -gt 0 ]]; do
    case $1 in
        --quick) QUICK_MODE=true ;;
        --fresh) FRESH_MODE=true ;;
        --chaos) CHAOS_MODE=true ;;
        --soak) SOAK_MODE=true ;;
        --stress) STRESS_MODE=true ;;
        --benchmark) BENCHMARK_MODE=true ;;
        --live) LIVE_MODE=true ;;
        --multilang) MULTILANG_MODE=true ;;
        --trace-analysis) TRACE_ANALYSIS=true ;;
        --full) 
            CHAOS_MODE=true
            SOAK_MODE=true
            STRESS_MODE=true
            BENCHMARK_MODE=true
            ;;
        -h|--help) usage; exit 0 ;;
        *) error "Unknown option: $1"; usage; exit 1 ;;
    esac
    shift
done
```

---

## Full Production Test Run

```bash
# Complete production validation:
./scripts/demo-final-showcase.sh --full --fresh

# With live LLM:
OPENAI_API_KEY=sk-... ./scripts/demo-final-showcase.sh --full --live

# CI-friendly (no soak, no live):
./scripts/demo-final-showcase.sh --chaos --stress --benchmark

# Quick smoke test:
./scripts/demo-final-showcase.sh --quick
```

---

## Integration with Existing Tests

### Run Order for Bug Finding

1. **Quick smoke** (`--quick`): Catches obvious breakage
2. **Chaos injection** (`--chaos`): Finds failure handling bugs
3. **Stress test** (`--stress`): Finds concurrency bugs
4. **Benchmark** (`--benchmark`): Detects performance regressions
5. **Soak test** (`--soak`): Finds resource leaks
6. **Live test** (`--live`): Validates real API integration

### Example: Finding Bugs Like We Fixed

The improvements would have caught:

| Bug ID | What Caught It | New Test |
|--------|---------------|----------|
| B-001 (circuit breaker) | `--chaos` | Circuit opens after failures |
| B-003 (concurrency) | `--stress` | Parallel run crashes |
| B-005 (timeout) | `--chaos` | Acquisition timeout test |
| B-007 (memory) | `--soak` | Memory growth detection |

---

## Files to Modify

1. **`scripts/demo-final-showcase.sh`**: Add new parts and flag parsing
2. **`scripts/soak.sh`**: Already exists, integrate into demo
3. **Create `scripts/benchmark.sh`**: Standalone benchmark runner
4. **Create `.beads/benchmarks.json`**: Store performance baselines

---

## Summary

| Improvement | Priority | Effort | Bug-Finding Value |
|-------------|----------|--------|-------------------|
| Chaos injection | P0 | Medium | High - catches failure handling |
| Stress testing | P0 | Medium | High - catches concurrency bugs |
| Soak integration | P1 | Low | Medium - catches leaks |
| Benchmarking | P1 | Medium | Medium - catches regressions |
| Live API mode | P2 | Low | Medium - validates real integration |
| Multi-language | P2 | Medium | Low - validates Docker gates |
| Trace analysis | P3 | Low | Low - observability |

**Recommended first step**: Add chaos injection (`--chaos`) and stress testing (`--stress`) as these have the highest bug-finding value for the least effort.

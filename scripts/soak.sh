#!/usr/bin/env bash
# =============================================================================
# Soak Test Script (Production Hardening S-1)
# =============================================================================
#
# Runs the integration workflow for an extended period to detect:
# - Thread leaks (thread count should remain stable)
# - File descriptor leaks (FD count should remain stable)
# - Memory leaks (RSS should remain bounded)
# - Deadlocks (all runs should complete within timeout)
#
# Usage:
#   ./scripts/soak.sh              # Run for 30 minutes (default)
#   ./scripts/soak.sh 60           # Run for 60 minutes
#   ./scripts/soak.sh 5 --quick    # Quick 5-minute check
#
# Environment:
#   USE_MOCK_LLM=true              # Use mock LLM (default)
#   USE_SQLITE=true                # Use SQLite (default)
#   SOAK_PARALLEL_RUNS=2           # Parallel workflow runs (default: 2)
#   SOAK_INTERVAL_SECONDS=30       # Seconds between runs (default: 30)
#   SOAK_SAMPLE_INTERVAL=60        # Seconds between metric samples (default: 60)
#
# Exit codes:
#   0 - All checks passed
#   1 - Resource leak detected
#   2 - Timeout or deadlock detected
#   3 - Configuration error
#
# =============================================================================

set -eo pipefail

# Default configuration
DURATION_MINUTES="${1:-30}"
QUICK_MODE="${2:-}"
PARALLEL_RUNS="${SOAK_PARALLEL_RUNS:-2}"
RUN_INTERVAL="${SOAK_INTERVAL_SECONDS:-30}"
SAMPLE_INTERVAL="${SOAK_SAMPLE_INTERVAL:-60}"

# Environment defaults for soak tests
export USE_MOCK_LLM="${USE_MOCK_LLM:-true}"
export USE_SQLITE="${USE_SQLITE:-true}"
export VALIDATION_PROFILE="${VALIDATION_PROFILE:-offline}"

# Thresholds for leak detection
THREAD_THRESHOLD=50        # Max threads allowed
FD_THRESHOLD=200           # Max file descriptors allowed
MEMORY_GROWTH_MB=100       # Max memory growth allowed (MB)

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Metrics storage (as space-separated strings for bash 3 compatibility)
THREAD_SAMPLES=""
FD_SAMPLES=""
MEMORY_SAMPLES=""
SAMPLE_COUNT=0
BASELINE_THREADS=0
BASELINE_FDS=0
BASELINE_MEMORY_KB=0

# Process tracking
SOAK_PID=$$
WORKFLOW_PIDS=""

# =============================================================================
# Helper Functions
# =============================================================================

log_info() {
    echo -e "${GREEN}[INFO]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $(date '+%Y-%m-%d %H:%M:%S') $*"
}

get_thread_count() {
    # Count threads for all Python processes in this session
    ps -M $(pgrep -P $SOAK_PID python 2>/dev/null || echo "$$") 2>/dev/null | wc -l | tr -d ' '
}

get_fd_count() {
    # Count open file descriptors for Python processes
    local total=0
    for pid in $(pgrep -P $SOAK_PID python 2>/dev/null || echo ""); do
        if [ -d "/proc/$pid/fd" ]; then
            # Linux
            total=$((total + $(ls /proc/$pid/fd 2>/dev/null | wc -l)))
        else
            # macOS - use lsof
            total=$((total + $(lsof -p $pid 2>/dev/null | wc -l)))
        fi
    done
    echo $total
}

get_memory_kb() {
    # Get resident set size in KB for Python processes
    local total=0
    for pid in $(pgrep -P $SOAK_PID python 2>/dev/null || echo ""); do
        # Use ps to get RSS in KB
        local rss=$(ps -o rss= -p $pid 2>/dev/null | tr -d ' ')
        if [ -n "$rss" ]; then
            total=$((total + rss))
        fi
    done
    echo $total
}

sample_metrics() {
    local threads=$(get_thread_count)
    local fds=$(get_fd_count)
    local memory_kb=$(get_memory_kb)
    
    THREAD_SAMPLES="$THREAD_SAMPLES $threads"
    FD_SAMPLES="$FD_SAMPLES $fds"
    MEMORY_SAMPLES="$MEMORY_SAMPLES $memory_kb"
    SAMPLE_COUNT=$((SAMPLE_COUNT + 1))
    
    local memory_mb=$((memory_kb / 1024))
    log_info "Sample $SAMPLE_COUNT: threads=$threads, fds=$fds, memory=${memory_mb}MB"
}

run_workflow() {
    # Run a single workflow iteration with timeout
    local run_id=$1
    local timeout_seconds=300  # 5 minute timeout per run
    
    log_info "Starting workflow run #$run_id"
    
    # Run pytest with a specific test that exercises the workflow
    timeout $timeout_seconds python -m pytest \
        tests/ \
        -m "not (slow or integration_live or e2e or docker or postgres)" \
        -k "test_" \
        --timeout=120 \
        -x \
        -q \
        --tb=no \
        2>&1 | head -5 || {
            local exit_code=$?
            if [ $exit_code -eq 124 ]; then
                log_error "Workflow run #$run_id timed out after ${timeout_seconds}s"
                return 2
            else
                log_warn "Workflow run #$run_id failed with exit code $exit_code"
                return 1
            fi
        }
    
    log_info "Workflow run #$run_id completed"
    return 0
}

check_thresholds() {
    local failed=0
    
    # Skip checks if no samples
    if [ -z "$THREAD_SAMPLES" ] || [ "$SAMPLE_COUNT" -lt 2 ]; then
        return 0
    fi
    
    # Check thread count
    local max_threads=$(echo $THREAD_SAMPLES | tr ' ' '\n' | sort -rn | head -1)
    if [ -n "$max_threads" ] && [ "$max_threads" -gt "$THREAD_THRESHOLD" ]; then
        log_error "Thread threshold exceeded: $max_threads > $THREAD_THRESHOLD"
        failed=1
    fi
    
    # Check FD count
    local max_fds=$(echo $FD_SAMPLES | tr ' ' '\n' | sort -rn | head -1)
    if [ -n "$max_fds" ] && [ "$max_fds" -gt "$FD_THRESHOLD" ]; then
        log_error "FD threshold exceeded: $max_fds > $FD_THRESHOLD"
        failed=1
    fi
    
    # Check memory growth
    local first_memory=$(echo $MEMORY_SAMPLES | awk '{print $1}')
    local last_memory=$(echo $MEMORY_SAMPLES | awk '{print $NF}')
    
    if [ -n "$first_memory" ] && [ -n "$last_memory" ]; then
        local growth_kb=$((last_memory - first_memory))
        local growth_mb=$((growth_kb / 1024))
        
        if [ "$growth_mb" -gt "$MEMORY_GROWTH_MB" ]; then
            log_error "Memory growth exceeded: ${growth_mb}MB > ${MEMORY_GROWTH_MB}MB"
            failed=1
        fi
    fi
    
    return $failed
}

print_summary() {
    echo ""
    echo "============================================="
    echo "             SOAK TEST SUMMARY"
    echo "============================================="
    echo "Duration: $DURATION_MINUTES minutes"
    echo "Samples collected: $SAMPLE_COUNT"
    echo ""
    
    if [ -n "$THREAD_SAMPLES" ]; then
        echo "Thread Count:"
        echo "  Baseline: $BASELINE_THREADS"
        echo "  Min: $(echo $THREAD_SAMPLES | tr ' ' '\n' | sort -n | head -1)"
        echo "  Max: $(echo $THREAD_SAMPLES | tr ' ' '\n' | sort -rn | head -1)"
        echo "  Final: $(echo $THREAD_SAMPLES | awk '{print $NF}')"
        echo ""
        echo "File Descriptors:"
        echo "  Baseline: $BASELINE_FDS"
        echo "  Min: $(echo $FD_SAMPLES | tr ' ' '\n' | sort -n | head -1)"
        echo "  Max: $(echo $FD_SAMPLES | tr ' ' '\n' | sort -rn | head -1)"
        echo "  Final: $(echo $FD_SAMPLES | awk '{print $NF}')"
        echo ""
        local first_mem=$(($(echo $MEMORY_SAMPLES | awk '{print $1}') / 1024))
        local last_mem=$(($(echo $MEMORY_SAMPLES | awk '{print $NF}') / 1024))
        local growth=$((last_mem - first_mem))
        echo "Memory (MB):"
        echo "  Baseline: ${first_mem}MB"
        echo "  Final: ${last_mem}MB"
        echo "  Growth: ${growth}MB"
    else
        echo "(No samples collected)"
    fi
    echo "============================================="
}

cleanup() {
    log_info "Cleaning up..."
    # Kill any child processes
    if [ -n "$WORKFLOW_PIDS" ]; then
        for pid in $WORKFLOW_PIDS; do
            kill $pid 2>/dev/null || true
        done
    fi
    wait 2>/dev/null || true
}

trap cleanup EXIT

# =============================================================================
# Main
# =============================================================================

main() {
    log_info "Starting soak test for $DURATION_MINUTES minutes"
    log_info "Configuration:"
    log_info "  USE_MOCK_LLM=$USE_MOCK_LLM"
    log_info "  USE_SQLITE=$USE_SQLITE"
    log_info "  PARALLEL_RUNS=$PARALLEL_RUNS"
    log_info "  RUN_INTERVAL=${RUN_INTERVAL}s"
    log_info "  SAMPLE_INTERVAL=${SAMPLE_INTERVAL}s"
    
    # Take baseline measurements
    log_info "Taking baseline measurements..."
    BASELINE_THREADS=$(get_thread_count)
    BASELINE_FDS=$(get_fd_count)
    BASELINE_MEMORY_KB=$(get_memory_kb)
    log_info "Baseline: threads=$BASELINE_THREADS, fds=$BASELINE_FDS, memory=$((BASELINE_MEMORY_KB/1024))MB"
    
    # Initial sample
    sample_metrics
    
    local end_time=$(($(date +%s) + DURATION_MINUTES * 60))
    local run_count=0
    local last_sample_time=$(date +%s)
    local failed_runs=0
    local timeout_runs=0
    
    while [ $(date +%s) -lt $end_time ]; do
        run_count=$((run_count + 1))
        
        # Run workflow (in foreground for now - can parallelize later)
        if ! run_workflow $run_count; then
            local exit_code=$?
            if [ $exit_code -eq 2 ]; then
                timeout_runs=$((timeout_runs + 1))
            else
                failed_runs=$((failed_runs + 1))
            fi
        fi
        
        # Sample metrics periodically
        local now=$(date +%s)
        if [ $((now - last_sample_time)) -ge $SAMPLE_INTERVAL ]; then
            sample_metrics
            last_sample_time=$now
        fi
        
        # Check thresholds during run (fail fast)
        if ! check_thresholds; then
            log_error "Threshold exceeded during soak test"
            print_summary
            exit 1
        fi
        
        # Sleep between runs
        log_info "Sleeping ${RUN_INTERVAL}s before next run..."
        sleep $RUN_INTERVAL
    done
    
    # Final sample
    sample_metrics
    
    # Print summary
    print_summary
    
    # Final threshold check
    if ! check_thresholds; then
        log_error "Soak test FAILED: Resource thresholds exceeded"
        exit 1
    fi
    
    # Check for timeouts
    if [ $timeout_runs -gt 0 ]; then
        log_error "Soak test FAILED: $timeout_runs runs timed out (potential deadlock)"
        exit 2
    fi
    
    log_info "Soak test PASSED"
    log_info "  Total runs: $run_count"
    log_info "  Failed runs: $failed_runs"
    log_info "  Timeout runs: $timeout_runs"
    
    exit 0
}

main "$@"

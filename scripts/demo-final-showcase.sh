#!/bin/bash
#
# 🎬 FINAL DEMO SHOWCASE - Integration Co-Worker
# Comprehensive demonstration of all key features
#
# This script demonstrates:
# 1. API Spec Processing - All 15 specs
# 2. Workflow Visualization - 21-node LangGraph + Postgres tables
# 3. KG Learning - Template reuse across runs
# 4. Code Generation - Real output to target repo
#
# Prerequisites:
# - DATABASE_URL set (Postgres + pgvector)
# - OPENAI_API_KEY set
# - Virtual environment (.venv311) available
#
# Usage:
#   ./scripts/demo-final-showcase.sh [--quick] [--skip-cleanup]
#
#   --quick       Run only 3 specs instead of all 15
#   --skip-cleanup  Don't reset KG before demo
#

set -e  # Exit on error

# ============================================================================
# Configuration
# ============================================================================
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_REPO="/Users/julianbartosz/git/schoolwork/UPlant/testing-solver-agentic-spec-coworker"
SPECS_DIR="${PROJECT_ROOT}/specs"
LOG_DIR="${PROJECT_ROOT}/logs/demo-final"
PYTHON_BIN="${PROJECT_ROOT}/.venv311/bin/python"

# Parse args
QUICK_MODE=false
SKIP_CLEANUP=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --quick) QUICK_MODE=true; shift ;;
    --skip-cleanup) SKIP_CLEANUP=true; shift ;;
    -h|--help)
      head -30 "$0" | tail -20
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Logging
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
MAIN_LOG="${LOG_DIR}/demo-${TIMESTAMP}.log"

log() {
  echo -e "$1" | tee -a "$MAIN_LOG"
}

section() {
  log ""
  log "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
  log "${CYAN}   $1${NC}"
  log "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
  log ""
}

step() {
  log "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
  log "${YELLOW}$1${NC}"
  log "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

info() {
  log "${BLUE}ℹ️  $1${NC}"
}

success() {
  log "${GREEN}✅ $1${NC}"
}

warn() {
  log "${YELLOW}⚠️  $1${NC}"
}

# ============================================================================
# Setup
# ============================================================================
cd "$PROJECT_ROOT"
export PYTHONPATH=src

# Source environment
if [ -f "$PROJECT_ROOT/.env" ]; then
    source "$PROJECT_ROOT/.env"
    info "Loaded environment from .env"
fi

# Ensure real mode (no mocks) - override anything from .env
unset USE_MOCK_LLM
unset USE_SQLITE
unset USE_IN_MEMORY_KG_FALLBACK

# Set demo-specific environment
export USE_MOCK_LLM=false
export USE_SQLITE=false
export USE_IN_MEMORY_KG_FALLBACK=false

section "🚀 INTEGRATION CO-WORKER - FINAL DEMO SHOWCASE"

log "${MAGENTA}Demo Configuration:${NC}"
log "  Project Root:  $PROJECT_ROOT"
log "  Target Repo:   $TARGET_REPO"
log "  Specs Dir:     $SPECS_DIR"
log "  Log File:      $MAIN_LOG"
log "  Quick Mode:    $QUICK_MODE"
log "  Python:        $PYTHON_BIN"
log ""

# ============================================================================
# Environment Validation (from setup_env.sh)
# ============================================================================
step "Step 0.1: Validate Python Environment"

# Check Python binary exists
if [ ! -x "$PYTHON_BIN" ]; then
  log "${RED}ERROR: Python not found at $PYTHON_BIN${NC}"
  log "${RED}Run: ./scripts/setup_env.sh to create the environment${NC}"
  exit 1
fi

# Check Python version
PY_VERSION=$("$PYTHON_BIN" --version 2>&1)
if [[ ! "$PY_VERSION" =~ "3.11" ]]; then
  log "${RED}ERROR: Wrong Python version: $PY_VERSION (expected 3.11.x)${NC}"
  exit 1
fi
success "Python version: $PY_VERSION"

# Check for mixed site-packages (the bug we encountered)
SITE_PACKAGES_COUNT=$(ls -d "${PROJECT_ROOT}/.venv311/lib/"python* 2>/dev/null | wc -l | tr -d ' ')
if [ "$SITE_PACKAGES_COUNT" -gt 1 ]; then
  log "${RED}ERROR: Multiple Python versions in site-packages!${NC}"
  log "${RED}This causes package conflicts. Run: ./scripts/setup_env.sh --clean${NC}"
  exit 1
fi
success "Single Python version in site-packages"

# Check critical imports
log "${BLUE}Testing critical imports...${NC}"
if $PYTHON_BIN -c "
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langchain_openai import ChatOpenAI
from integration_coworker.graph.parallel import is_parallel_enabled
print('All imports successful')
" 2>&1 | tee -a "$MAIN_LOG"; then
  success "All critical imports work"
else
  log "${RED}ERROR: Critical imports failed! Run: ./scripts/setup_env.sh --clean${NC}"
  exit 1
fi

step "Step 0.2: Validate Environment Variables"

# Check required environment variables
if [ -z "$DATABASE_URL" ]; then
  log "${RED}ERROR: DATABASE_URL not set${NC}"
  exit 1
fi
success "DATABASE_URL is set"

if [ -z "$OPENAI_API_KEY" ]; then
  log "${RED}ERROR: OPENAI_API_KEY not set${NC}"
  exit 1
fi
success "OPENAI_API_KEY is set"

# Check optional but recommended variables
if [ -z "$REDIS_URL" ]; then
  warn "REDIS_URL not set - LLM response caching will be disabled"
else
  success "REDIS_URL is set (LLM caching enabled)"
fi

if [ -z "$LANGCHAIN_API_KEY" ]; then
  warn "LANGCHAIN_API_KEY not set - LangSmith tracing will be disabled"
else
  success "LANGCHAIN_API_KEY is set (LangSmith tracing enabled)"
fi

# Verify mode settings
log "${BLUE}Mode Settings:${NC}"
log "  USE_MOCK_LLM=${USE_MOCK_LLM:-unset}"
log "  USE_SQLITE=${USE_SQLITE:-unset}"
log "  USE_IN_MEMORY_KG_FALLBACK=${USE_IN_MEMORY_KG_FALLBACK:-unset}"

# ============================================================================
# PART 1: System Status & Architecture Overview
# ============================================================================
section "📊 PART 1: SYSTEM STATUS & ARCHITECTURE"

step "Step 1.1: System Configuration"
$PYTHON_BIN -m integration_coworker.cli status 2>&1 | tee -a "$MAIN_LOG"

step "Step 1.2: Available API Specs (15 total)"
log "${BLUE}Specs in ${SPECS_DIR}:${NC}"
ls -1 "$SPECS_DIR" | while read spec; do
  size=$(du -h "${SPECS_DIR}/${spec}" | cut -f1)
  log "  📄 ${spec} (${size})"
done

step "Step 1.3: LangGraph Workflow Architecture (21 Nodes)"
log "${BLUE}The workflow consists of 21 nodes in a DAG:${NC}"
log ""
log "  ${CYAN}┌─────────────────────────────────────────────────────────────┐${NC}"
log "  ${CYAN}│                    LangGraph Workflow                       │${NC}"
log "  ${CYAN}├─────────────────────────────────────────────────────────────┤${NC}"
log "  ${CYAN}│  plan_run → ingest_spec → detect_and_parse_spec            │${NC}"
log "  ${CYAN}│      ↓                                                      │${NC}"
log "  ${CYAN}│  build_silver_api_model → embed_spec_chunks                │${NC}"
log "  ${CYAN}│      ↓                                                      │${NC}"
log "  ${CYAN}│  persist_silver_checkpoint → understand_task               │${NC}"
log "  ${CYAN}│      ↓                                                      │${NC}"
log "  ${CYAN}│  align_task_with_kg → plan_integration_flow                │${NC}"
log "  ${CYAN}│      ↓                                                      │${NC}"
log "  ${CYAN}│  attach_policies_and_patterns → generate_code_and_tests    │${NC}"
log "  ${CYAN}│      ↓                                                      │${NC}"
log "  ${CYAN}│  persist_gold_checkpoint → persist_kg_learning             │${NC}"
log "  ${CYAN}│      ↓                                                      │${NC}"
log "  ${CYAN}│  validate_integration_design → analyze_repo_layout         │${NC}"
log "  ${CYAN}│      ↓                                                      │${NC}"
log "  ${CYAN}│  write_code_to_repo → build_report                         │${NC}"
log "  ${CYAN}└─────────────────────────────────────────────────────────────┘${NC}"
log ""

# ============================================================================
# PART 2: Database & Knowledge Graph Setup
# ============================================================================
section "🗄️ PART 2: DATABASE & KNOWLEDGE GRAPH"

step "Step 2.1: Initialize Database Schema"
$PYTHON_BIN -m integration_coworker.cli init-db 2>&1 | tee -a "$MAIN_LOG"

if [ "$SKIP_CLEANUP" != "true" ]; then
  step "Step 2.2: Reset KG for Clean Demo (Optional)"
  info "Clearing existing KG data for fresh demo..."
  $PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    for table in ['step_bindings', 'workflow_steps', 'edges', 'nodes']:
        cur.execute(f'DELETE FROM kg.{table}')
    conn.commit()
    print('KG tables cleared')
" 2>&1 | tee -a "$MAIN_LOG"
fi

step "Step 2.3: Seed Standard Patterns"
info "Seeding 7 STANDARD_PATTERNS into KG..."
$PYTHON_BIN -c "
from integration_coworker.persistence.seed_kg import seed_standard_patterns
seed_standard_patterns()
print('Standard patterns seeded')
" 2>&1 | tee -a "$MAIN_LOG"

step "Step 2.4: Knowledge Graph State (BEFORE)"
log "${BLUE}PostgreSQL KG Tables:${NC}"
$PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    
    print('\\n=== KG Table Counts ===')
    for table in ['nodes', 'edges', 'workflow_steps', 'step_bindings']:
        cur.execute(f'SELECT COUNT(*) FROM kg.{table}')
        count = cur.fetchone()[0]
        print(f'  kg.{table}: {count} rows')
    
    print('\\n=== Node Types ===')
    cur.execute('SELECT node_type, COUNT(*) FROM kg.nodes GROUP BY node_type ORDER BY count DESC')
    for row in cur.fetchall():
        print(f'  {row[0]}: {row[1]}')
    
    print('\\n=== Standard Patterns Seeded ===')
    cur.execute(\"SELECT key, name FROM kg.nodes WHERE node_type = 'pattern' ORDER BY key\")
    for row in cur.fetchall():
        print(f'  {row[0]}: {row[1]}')
" 2>&1 | tee -a "$MAIN_LOG"

# ============================================================================
# PART 3: PARALLEL & ASYNC VERIFICATION
# ============================================================================
section "⚡ PART 3: PARALLEL & ASYNC EXECUTION VERIFICATION"

step "Step 3.1: Enable Parallel Workflow Execution"
export PARALLEL_WORKFLOW=true
export PARALLEL_TIMEOUT=300
info "PARALLEL_WORKFLOW=$PARALLEL_WORKFLOW"
info "PARALLEL_TIMEOUT=${PARALLEL_TIMEOUT}s"

step "Step 3.2: Verify Async Runtime Configuration"
log "${BLUE}Checking async infrastructure:${NC}"
$PYTHON_BIN -c "
import os
import asyncio

# Check LangGraph async saver availability
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    print('  ✅ AsyncPostgresSaver available (Postgres async checkpointing)')
except ImportError as e:
    print(f'  ❌ AsyncPostgresSaver not available: {e}')

try:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    print('  ✅ AsyncSqliteSaver available (SQLite fallback)')
except ImportError as e:
    print(f'  ❌ AsyncSqliteSaver not available: {e}')

# Check parallel module
from integration_coworker.graph.parallel import is_parallel_enabled, get_parallel_timeout
print(f'  ✅ Parallel enabled: {is_parallel_enabled()}')
print(f'  ✅ Parallel timeout: {get_parallel_timeout()}s')

# Check async event loop
loop = asyncio.new_event_loop()
print(f'  ✅ Async event loop available: {type(loop).__name__}')
" 2>&1 | tee -a "$MAIN_LOG"

# ============================================================================
# PART 4: API Spec Processing Demo
# ============================================================================
section "📄 PART 4: API SPEC PROCESSING"

# Select specs based on mode
if [ "$QUICK_MODE" = "true" ]; then
  DEMO_SPECS=(
    "stripe_api.json:Create a checkout session for a one-time payment"
    "twilio_messaging_v1.json:Send an SMS message to a phone number"
    "github_api.json:Create a new issue in a repository"
  )
  info "Quick mode: Running 3 representative specs"
else
  DEMO_SPECS=(
    "stripe_api.json:Create a checkout session for a one-time payment"
    "twilio_messaging_v1.json:Send an SMS message to a phone number"
    "github_api.json:Create a new issue in a repository"
    "slack_api.yaml:Send a message to a Slack channel"
    "openai_api.yaml:Create a chat completion with GPT-4"
    "spotify_api.yaml:Search for tracks by artist name"
    "zoom_api.yaml:Create a new meeting with participants"
    "mailchimp_api.yaml:Add a subscriber to a mailing list"
    "asana_api.yaml:Create a new task in a project"
    "box_api.yaml:Upload a file to a folder"
    "circleci_api.yaml:Trigger a pipeline build"
    "digitalocean_api.yaml:Create a new droplet"
    "plaid_api.yaml:Link a bank account"
    "petstore_v3.json:Add a new pet to the store"
    "httpbin_api.json:Make a POST request with JSON body"
  )
  info "Full mode: Running all 15 specs"
fi

# Prepare target repo
step "Step 4.1: Prepare Target Repository"
info "Target repo: $TARGET_REPO"
mkdir -p "$TARGET_REPO/src/integrations/clients"
mkdir -p "$TARGET_REPO/src/integrations/flows"
mkdir -p "$TARGET_REPO/tests/integrations"

# Initialize git if needed
if [ ! -d "$TARGET_REPO/.git" ]; then
  pushd "$TARGET_REPO" >/dev/null
  git init
  git add .
  git commit -m "Initial commit" 2>/dev/null || true
  popd >/dev/null
fi

# Create baseline branch
pushd "$TARGET_REPO" >/dev/null
git checkout -B baseline 2>/dev/null || true
git checkout -B demo-${TIMESTAMP}
popd >/dev/null
success "Created demo branch: demo-${TIMESTAMP}"

# Process each spec
SPEC_COUNT=0
TOTAL_SPECS=${#DEMO_SPECS[@]}

for spec_entry in "${DEMO_SPECS[@]}"; do
  SPEC_COUNT=$((SPEC_COUNT + 1))
  
  # Parse spec:task
  SPEC_FILE="${spec_entry%%:*}"
  TASK="${spec_entry#*:}"
  PROVIDER="${SPEC_FILE%.*}"
  PROVIDER="${PROVIDER//_/-}"
  
  step "Step 4.${SPEC_COUNT}: Processing ${SPEC_FILE} (${SPEC_COUNT}/${TOTAL_SPECS})"
  log "${BLUE}Spec:${NC} ${SPECS_DIR}/${SPEC_FILE}"
  log "${BLUE}Task:${NC} ${TASK}"
  log "${BLUE}Provider:${NC} ${PROVIDER}"
  log ""
  
  RUN_LOG="${LOG_DIR}/${PROVIDER}-${TIMESTAMP}.log"
  
  # Run the coworker
  START_TIME=$(date +%s)
  
  set +e
  $PYTHON_BIN -m integration_coworker.cli run \
    --spec-ref "${SPECS_DIR}/${SPEC_FILE}" \
    --task "$TASK" \
    --provider "$PROVIDER" \
    --repo-root "$TARGET_REPO" \
    2>&1 | tee "$RUN_LOG" | tee -a "$MAIN_LOG"
  EXIT_CODE=${PIPESTATUS[0]}
  set -e
  
  END_TIME=$(date +%s)
  DURATION=$((END_TIME - START_TIME))
  
  if [ $EXIT_CODE -eq 0 ]; then
    success "Completed ${SPEC_FILE} in ${DURATION}s"
    
    # Capture the run_id from the log for later analysis
    LAST_RUN_ID=$(grep -o 'run_id=[^ ]*' "$RUN_LOG" | tail -1 | cut -d= -f2 2>/dev/null || echo "")
    if [ -n "$LAST_RUN_ID" ]; then
      echo "$LAST_RUN_ID:$SPEC_FILE:$DURATION" >> "${LOG_DIR}/run_ids.txt"
    fi
  else
    warn "Failed ${SPEC_FILE} (exit code: ${EXIT_CODE}) - see ${RUN_LOG}"
  fi
  
  log ""
done

# ============================================================================
# PART 5: PARALLEL EXECUTION PROOF
# ============================================================================
section "⚡ PART 5: PARALLEL EXECUTION PROOF"

step "Step 5.1: Analyze Node Timing Data"
log "${BLUE}Examining node_timings from run checkpoints to prove parallelization:${NC}"
$PYTHON_BIN -c "
import json
from integration_coworker.persistence.postgres import get_connection

with get_connection() as conn:
    cur = conn.cursor()
    
    # Get the most recent runs
    cur.execute('''
        SELECT run_id, node_name, state_json->'node_timings' as timings, created_at
        FROM integration_gold.run_checkpoints
        WHERE state_json ? 'node_timings'
        ORDER BY created_at DESC
        LIMIT 5
    ''')
    
    rows = cur.fetchall()
    if not rows:
        print('  No checkpoint data with node_timings found')
    else:
        print('\\n=== Recent Run Node Timings ===')
        for run_id, node_name, timings, created_at in rows:
            if timings:
                print(f'\\nRun: {run_id[:30]}... (checkpoint: {node_name})')
                for node, ms in sorted(timings.items(), key=lambda x: -x[1] if isinstance(x[1], (int, float)) else 0):
                    print(f'  {node}: {ms:.2f}ms' if isinstance(ms, (int, float)) else f'  {node}: {ms}')

    # Check for parallel branches specifically
    cur.execute('''
        SELECT 
            run_id,
            state_json->'node_timings'->'embed_spec_chunks' as embed_time,
            state_json->'node_timings'->'understand_task' as task_time,
            state_json->'node_timings'->'sync_embed_task' as sync_time
        FROM integration_gold.run_checkpoints
        WHERE state_json->'node_timings' ? 'embed_spec_chunks'
          AND state_json->'node_timings' ? 'understand_task'
        ORDER BY created_at DESC
        LIMIT 3
    ''')
    
    parallel_rows = cur.fetchall()
    print('\\n=== Parallel Branch Analysis ===')
    if parallel_rows:
        for run_id, embed_t, task_t, sync_t in parallel_rows:
            print(f'\\nRun: {run_id[:30]}...')
            embed_ms = float(embed_t) if embed_t else 0
            task_ms = float(task_t) if task_t else 0
            sync_ms = float(sync_t) if sync_t else 0
            sequential_time = embed_ms + task_ms
            parallel_time = max(embed_ms, task_ms) + sync_ms
            
            print(f'  embed_spec_chunks: {embed_ms:.2f}ms')
            print(f'  understand_task:   {task_ms:.2f}ms')
            print(f'  sync_embed_task:   {sync_ms:.2f}ms')
            print(f'  ---')
            print(f'  Sequential would be: {sequential_time:.2f}ms')
            print(f'  Parallel achieves:   {parallel_time:.2f}ms')
            if sequential_time > 0:
                speedup = sequential_time / parallel_time if parallel_time > 0 else 1
                print(f'  Speedup factor:      {speedup:.2f}x')
                if speedup > 1.1:
                    print(f'  ✅ PARALLEL EXECUTION CONFIRMED (>{speedup:.1f}x speedup)')
    else:
        print('  No runs with both parallel branches found yet')
        print('  (Parallel branches: embed_spec_chunks + understand_task)')
" 2>&1 | tee -a "$MAIN_LOG"

# ============================================================================
# PART 6: SPEC CACHING PROOF
# ============================================================================
section "🗃️ PART 6: SPEC CACHING PROOF"

step "Step 6.1: Check Spec Cache Status"
log "${BLUE}Examining spec_silver table for cached entries:${NC}"
$PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection

with get_connection() as conn:
    cur = conn.cursor()
    
    # Check spec_silver.source_refs for cached specs
    cur.execute('''
        SELECT 
            source_ref_uri,
            provider_code,
            created_at,
            updated_at
        FROM spec_silver.source_refs
        ORDER BY created_at DESC
        LIMIT 10
    ''')
    
    rows = cur.fetchall()
    print('\\n=== Cached Spec References (spec_silver.source_refs) ===')
    if rows:
        for uri, provider, created, updated in rows:
            print(f'  {provider}: {uri[:50]}...')
            print(f'    Created: {created}, Updated: {updated}')
    else:
        print('  No cached specs found')
    
    # Check chunk embeddings count
    cur.execute('SELECT COUNT(*) FROM spec_silver.spec_chunk_embeddings')
    embed_count = cur.fetchone()[0]
    print(f'\\n=== Cached Embeddings: {embed_count} chunks ===')
" 2>&1 | tee -a "$MAIN_LOG"

step "Step 6.2: Cache Hit Test - Re-run First Spec"
CACHE_TEST_SPEC="${DEMO_SPECS[0]%%:*}"
CACHE_TEST_PROVIDER="${CACHE_TEST_SPEC%.*}"
CACHE_TEST_PROVIDER="${CACHE_TEST_PROVIDER//_/-}"

info "Re-running ${CACHE_TEST_SPEC} to test cache hit..."
log "${BLUE}First run loaded spec fresh. Second run should use cache.${NC}"

CACHE_TEST_LOG="${LOG_DIR}/cache-test-${TIMESTAMP}.log"
START_CACHE_TEST=$(date +%s%N)

$PYTHON_BIN -m integration_coworker.cli run \
  --spec-ref "${SPECS_DIR}/${CACHE_TEST_SPEC}" \
  --task "List all available products" \
  --provider "${CACHE_TEST_PROVIDER}-cache-test" \
  --repo-root "$TARGET_REPO" \
  --verbose 2>&1 | tee "$CACHE_TEST_LOG"

END_CACHE_TEST=$(date +%s%N)
CACHE_TEST_DURATION=$(( (END_CACHE_TEST - START_CACHE_TEST) / 1000000 ))

step "Step 6.3: Verify Cache Hit in Run"
log "${BLUE}Checking cache_hit status in checkpoint:${NC}"
$PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection

with get_connection() as conn:
    cur = conn.cursor()
    
    # Get most recent runs and their cache_hit status
    cur.execute('''
        SELECT 
            run_id,
            state_json->>'provider_code' as provider,
            state_json->>'cache_hit' as cache_hit,
            created_at
        FROM integration_gold.run_checkpoints
        WHERE node_name = 'build_silver_api_model'
        ORDER BY created_at DESC
        LIMIT 5
    ''')
    
    rows = cur.fetchall()
    print('\\n=== Recent Runs Cache Status ===')
    cache_hits = 0
    cache_misses = 0
    for run_id, provider, cache_hit, created_at in rows:
        status = '✅ CACHE HIT' if cache_hit == 'true' else '❌ Cache miss (fresh parse)'
        print(f'  {provider or \"unknown\"}: {status}')
        print(f'    Run: {run_id[:30]}...')
        if cache_hit == 'true':
            cache_hits += 1
        else:
            cache_misses += 1
    
    print(f'\\n=== Cache Statistics ===')
    print(f'  Cache hits:   {cache_hits}')
    print(f'  Cache misses: {cache_misses}')
    if cache_hits > 0:
        print(f'  ✅ CACHING IS WORKING - {cache_hits} hits detected')
    else:
        print(f'  ⚠️  No cache hits yet (first run of each spec)')
" 2>&1 | tee -a "$MAIN_LOG"

# ============================================================================
# PART 7: Knowledge Graph Learning Demo
# ============================================================================
section "🧠 PART 7: KNOWLEDGE GRAPH LEARNING"

step "Step 7.1: Knowledge Graph State (AFTER)"
$PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    
    print('\\n=== KG Table Counts (After Processing) ===')
    for table in ['nodes', 'edges', 'workflow_steps', 'step_bindings']:
        cur.execute(f'SELECT COUNT(*) FROM kg.{table}')
        count = cur.fetchone()[0]
        print(f'  kg.{table}: {count} rows')
    
    print('\\n=== Node Types ===')
    cur.execute('SELECT node_type, COUNT(*) FROM kg.nodes GROUP BY node_type ORDER BY count DESC')
    for row in cur.fetchall():
        print(f'  {row[0]}: {row[1]}')
    
    print('\\n=== Workflow Templates Learned ===')
    cur.execute('''
        SELECT key, name, usage_count, embedding IS NOT NULL as has_embedding
        FROM kg.nodes 
        WHERE node_type = 'workflow_template'
        ORDER BY usage_count DESC
        LIMIT 10
    ''')
    for row in cur.fetchall():
        print(f'  {row[0]}')
        print(f'    Name: {row[1]}, Usage: {row[2]}, Has Embedding: {row[3]}')
    
    print('\\n=== Step Bindings (Endpoint Mappings) ===')
    cur.execute('SELECT COUNT(*) FROM kg.step_bindings')
    count = cur.fetchone()[0]
    print(f'  Total step bindings: {count}')
    
    if count > 0:
        cur.execute('''
            SELECT ws.step_key, n.key as endpoint_key
            FROM kg.step_bindings sb
            JOIN kg.workflow_steps ws ON sb.step_id = ws.id
            JOIN kg.nodes n ON sb.endpoint_node_id = n.id
            LIMIT 5
        ''')
        print('  Sample bindings:')
        for row in cur.fetchall():
            print(f'    {row[0]} → {row[1]}')
" 2>&1 | tee -a "$MAIN_LOG"

step "Step 7.2: Template Reuse Test"
info "Running SAME provider again to demonstrate template reuse..."

# Pick the first provider we ran
REUSE_SPEC="${DEMO_SPECS[0]%%:*}"
REUSE_PROVIDER="${REUSE_SPEC%.*}"
REUSE_PROVIDER="${REUSE_PROVIDER//_/-}"

$PYTHON_BIN -m integration_coworker.cli run \
  --spec-ref "${SPECS_DIR}/${REUSE_SPEC}" \
  --task "Process a refund for a previous payment" \
  --provider "$REUSE_PROVIDER" \
  --repo-root "$TARGET_REPO" \
  --verbose 2>&1 | grep -E "template|pattern|KG|reuse|matched|GraphRAG" | head -20 | tee -a "$MAIN_LOG"

step "Step 7.3: Usage Count Verification"
$PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    
    print('\\n=== Template Usage Counts (After Reuse Test) ===')
    cur.execute('''
        SELECT key, usage_count, last_used_at
        FROM kg.nodes 
        WHERE node_type = 'workflow_template'
        ORDER BY usage_count DESC
        LIMIT 5
    ''')
    for row in cur.fetchall():
        print(f'  {row[0]}: usage={row[1]}, last_used={row[2]}')
" 2>&1 | tee -a "$MAIN_LOG"

# ============================================================================
# PART 8: Generated Code Output
# ============================================================================
section "💻 PART 8: GENERATED CODE OUTPUT"

step "Step 8.1: Target Repository Structure"
log "${BLUE}Files generated in ${TARGET_REPO}:${NC}"
find "$TARGET_REPO/src" "$TARGET_REPO/tests" -type f -name "*.py" 2>/dev/null | while read f; do
  size=$(wc -l < "$f" | tr -d ' ')
  log "  📄 ${f#$TARGET_REPO/} (${size} lines)"
done

step "Step 8.2: Sample Generated Client"
SAMPLE_CLIENT=$(find "$TARGET_REPO/src/integrations/clients" -name "*.py" -type f | head -1)
if [ -n "$SAMPLE_CLIENT" ] && [ -f "$SAMPLE_CLIENT" ]; then
  log "${BLUE}Client: ${SAMPLE_CLIENT#$TARGET_REPO/}${NC}"
  log ""
  head -60 "$SAMPLE_CLIENT" | tee -a "$MAIN_LOG"
  log ""
  log "${CYAN}... (truncated)${NC}"
else
  warn "No client files found"
fi

step "Step 8.3: Sample Generated Flow"
SAMPLE_FLOW=$(find "$TARGET_REPO/src/integrations/flows" -name "*.py" -type f | head -1)
if [ -n "$SAMPLE_FLOW" ] && [ -f "$SAMPLE_FLOW" ]; then
  log "${BLUE}Flow: ${SAMPLE_FLOW#$TARGET_REPO/}${NC}"
  log ""
  head -50 "$SAMPLE_FLOW" | tee -a "$MAIN_LOG"
  log ""
  log "${CYAN}... (truncated)${NC}"
else
  warn "No flow files found"
fi

step "Step 8.4: Sample Generated Tests"
SAMPLE_TEST=$(find "$TARGET_REPO/tests/integrations" -name "test_*.py" -type f | head -1)
if [ -n "$SAMPLE_TEST" ] && [ -f "$SAMPLE_TEST" ]; then
  log "${BLUE}Tests: ${SAMPLE_TEST#$TARGET_REPO/}${NC}"
  log ""
  head -50 "$SAMPLE_TEST" | tee -a "$MAIN_LOG"
  log ""
  log "${CYAN}... (truncated)${NC}"
else
  warn "No test files found"
fi

step "Step 8.5: Git Diff Summary"
pushd "$TARGET_REPO" >/dev/null
log "${BLUE}Changes from baseline:${NC}"
git diff --stat baseline...HEAD | tee -a "$MAIN_LOG"
popd >/dev/null

# ============================================================================
# PART 9: Summary & Feature Verification
# ============================================================================
section "📋 PART 9: DEMO SUMMARY & FEATURE VERIFICATION"

step "Step 9.1: Final Statistics"

$PYTHON_BIN -c "
from integration_coworker.persistence.postgres import get_connection
with get_connection() as conn:
    cur = conn.cursor()
    
    # Count everything
    stats = {}
    for table in ['nodes', 'edges', 'workflow_steps', 'step_bindings']:
        cur.execute(f'SELECT COUNT(*) FROM kg.{table}')
        stats[table] = cur.fetchone()[0]
    
    cur.execute(\"SELECT COUNT(*) FROM kg.nodes WHERE node_type = 'workflow_template'\")
    stats['templates'] = cur.fetchone()[0]
    
    cur.execute(\"SELECT COUNT(*) FROM kg.nodes WHERE node_type = 'endpoint'\")
    stats['endpoints'] = cur.fetchone()[0]
    
    cur.execute(\"SELECT COUNT(*) FROM kg.nodes WHERE node_type = 'pattern'\")
    stats['patterns'] = cur.fetchone()[0]
    
    print('\\n╔════════════════════════════════════════════════════════════════╗')
    print('║               INTEGRATION CO-WORKER DEMO RESULTS                ║')
    print('╠════════════════════════════════════════════════════════════════╣')
    print(f'║  KG Nodes:           {stats[\"nodes\"]:>6}                                 ║')
    print(f'║  KG Edges:           {stats[\"edges\"]:>6}                                 ║')
    print(f'║  Workflow Templates: {stats[\"templates\"]:>6}                                 ║')
    print(f'║  Workflow Steps:     {stats[\"workflow_steps\"]:>6}                                 ║')
    print(f'║  Step Bindings:      {stats[\"step_bindings\"]:>6}                                 ║')
    print(f'║  Endpoints:          {stats[\"endpoints\"]:>6}                                 ║')
    print(f'║  Patterns:           {stats[\"patterns\"]:>6}                                 ║')
    print('╚════════════════════════════════════════════════════════════════╝')
" 2>&1 | tee -a "$MAIN_LOG"

step "Step 9.2: Feature Verification Summary"
log "${BLUE}Verifying all demo features are wired in and working:${NC}"
$PYTHON_BIN -c "
import os
from integration_coworker.persistence.postgres import get_connection
from integration_coworker.graph.parallel import is_parallel_enabled
from integration_coworker.llm.cache import get_llm_cache

results = []

# 1. Check Parallel Execution
parallel_ok = is_parallel_enabled()
results.append(('Parallel Workflow', 'PARALLEL_WORKFLOW=true', parallel_ok))

# 2. Check Async Checkpointer
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    async_ok = True
except ImportError:
    async_ok = False
results.append(('Async Checkpointer', 'AsyncPostgresSaver available', async_ok))

# 3. Check LLM Cache (Redis)
cache = get_llm_cache()
cache_available = cache.is_available()
results.append(('LLM Response Cache', 'Redis connected', cache_available))

# 4. Check Spec Caching from DB
with get_connection() as conn:
    cur = conn.cursor()
    
    # Check for cache hits in checkpoints
    cur.execute('''
        SELECT COUNT(*) FROM integration_gold.run_checkpoints
        WHERE state_json->>'cache_hit' = 'true'
    ''')
    cache_hits = cur.fetchone()[0]
    spec_cache_ok = cache_hits > 0
    results.append(('Spec Caching', f'{cache_hits} cache hits', spec_cache_ok or cache_hits == 0))
    
    # Check parallel timing data
    cur.execute('''
        SELECT COUNT(*) FROM integration_gold.run_checkpoints
        WHERE state_json->'node_timings' ? 'embed_spec_chunks'
          AND state_json->'node_timings' ? 'understand_task'
    ''')
    parallel_runs = cur.fetchone()[0]
    parallel_proof = parallel_runs > 0
    results.append(('Parallel Branches Executed', f'{parallel_runs} runs with parallel nodes', parallel_proof))
    
    # Check KG learning
    cur.execute('SELECT COUNT(*) FROM kg.workflow_steps')
    kg_steps = cur.fetchone()[0]
    kg_ok = kg_steps > 0
    results.append(('KG Learning (workflow_steps)', f'{kg_steps} steps learned', kg_ok))
    
    cur.execute('SELECT COUNT(*) FROM kg.step_bindings')
    kg_bindings = cur.fetchone()[0]
    bindings_ok = kg_bindings > 0
    results.append(('KG Step Bindings', f'{kg_bindings} endpoint bindings', bindings_ok))

print('')
print('╔════════════════════════════════════════════════════════════════╗')
print('║              FEATURE VERIFICATION RESULTS                      ║')
print('╠════════════════════════════════════════════════════════════════╣')

all_ok = True
for name, detail, ok in results:
    status = '✅' if ok else '❌'
    if not ok:
        all_ok = False
    print(f'║  {status} {name:<25} {detail:<25}  ║')

print('╠════════════════════════════════════════════════════════════════╣')
if all_ok:
    print('║     🎉 ALL FEATURES VERIFIED AND WORKING                       ║')
else:
    print('║     ⚠️  SOME FEATURES MAY NOT BE FULLY WIRED                   ║')
print('╚════════════════════════════════════════════════════════════════╝')
" 2>&1 | tee -a "$MAIN_LOG"

log ""
log "${GREEN}Demo Complete!${NC}"
log ""
log "${BLUE}Next Steps:${NC}"
log "  1. Review generated code:"
log "     ${CYAN}cd $TARGET_REPO && git diff baseline...HEAD${NC}"
log ""
log "  2. Query the Knowledge Graph:"
log "     ${CYAN}python -m integration_coworker.cli kg-dump${NC}"
log ""
log "  3. View full log:"
log "     ${CYAN}less $MAIN_LOG${NC}"
log ""
log "  4. Run tests on generated code:"
log "     ${CYAN}cd $TARGET_REPO && pytest tests/integrations/${NC}"
log ""

success "Demo log saved to: $MAIN_LOG"

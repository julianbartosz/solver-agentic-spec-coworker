#!/bin/bash
#
# 🎬 COMPREHENSIVE DEMO SHOWCASE
# Agentic Integration Designer & Code Generator
#
# This script demonstrates:
# 1. KG learning with persistence
# 2. GraphRAG retrieval on subsequent runs
# 3. Multi-spec workflow planning
# 4. Real public OpenAPI specs (Stripe, GitHub, Petstore)
# 5. Code artifact generation with full output
# 6. Writing to a target repository
#
# Prerequisites:
# - DATABASE_URL set (Postgres + pgvector)
# - OPENAI_API_KEY set
# - Virtual environment activated
#

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}   🚀 AGENTIC INTEGRATION DESIGNER - COMPREHENSIVE DEMO${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""

# Setup
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
export PYTHONPATH=src

# Source .env file to get correct API keys
if [ -f "$PROJECT_ROOT/.env" ]; then
    source "$PROJECT_ROOT/.env"
    echo -e "${BLUE}📄 Loaded environment from .env${NC}"
fi

# Ensure fallback is disabled (use real KG)
unset USE_IN_MEMORY_KG_FALLBACK
unset USE_MOCK_LLM
unset USE_SQLITE

# Create target repo for demo
TARGET_REPO="/tmp/integration-demo-repo"
rm -rf "$TARGET_REPO"
mkdir -p "$TARGET_REPO/src/integrations/clients"
mkdir -p "$TARGET_REPO/src/integrations/flows"
mkdir -p "$TARGET_REPO/tests/integrations"
echo "# Demo Integration Repo" > "$TARGET_REPO/README.md"

echo -e "${BLUE}📁 Created target repo at: ${TARGET_REPO}${NC}"
echo ""

# ============================================================================
# STEP 0: Status Check
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 0: Configuration Status${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
python -m integration_coworker.cli status
echo ""

# ============================================================================
# STEP 1: Initialize Database
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 1: Initialize Database Schema${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
python -m integration_coworker.cli init-db
echo ""

# ============================================================================
# STEP 2: Check KG Before (should be empty or minimal)
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 2: Knowledge Graph State (BEFORE)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}KG Nodes by type:${NC}"
psql "$DATABASE_URL" -c "SELECT node_type, COUNT(*) FROM kg.nodes GROUP BY node_type ORDER BY node_type" 2>/dev/null || echo "(No nodes yet)"
echo ""
echo -e "${BLUE}KG Edges by relation:${NC}"
psql "$DATABASE_URL" -c "SELECT relation_type, COUNT(*) FROM kg.edges GROUP BY relation_type ORDER BY relation_type" 2>/dev/null || echo "(No edges yet)"
echo ""

# ============================================================================
# STEP 3: First Run - Mock Payments (KG Learning)
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 3: First Run - Mock Payments API (KG Learning)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Running: demo --persist${NC}"
echo ""
python -m integration_coworker.cli demo --persist
echo ""

# ============================================================================
# STEP 4: Check KG After First Run
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 4: Knowledge Graph State (AFTER RUN 1)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}KG Nodes by type:${NC}"
psql "$DATABASE_URL" -c "SELECT node_type, COUNT(*) FROM kg.nodes GROUP BY node_type ORDER BY node_type"
echo ""
echo -e "${BLUE}KG Edges by relation:${NC}"
psql "$DATABASE_URL" -c "SELECT relation_type, COUNT(*) FROM kg.edges GROUP BY relation_type ORDER BY relation_type"
echo ""
echo -e "${BLUE}Workflow templates learned:${NC}"
psql "$DATABASE_URL" -c "SELECT key, name FROM kg.nodes WHERE node_type = 'workflow_template' LIMIT 5"
echo ""

# ============================================================================
# STEP 5: Second Run - Similar Task (GraphRAG Retrieval)
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 5: Second Run - Similar Task (GraphRAG Retrieval)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Running with similar task to test KG template reuse...${NC}"
echo ""
python -m integration_coworker.cli run \
  -s tests/fixtures/mock_payments_openapi.yaml \
  -t "Create a subscription checkout session with metadata" \
  --repo-root "$TARGET_REPO"
echo ""

# ============================================================================
# STEP 6: Multi-Spec Run (Payments + Notifications)
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 6: Multi-Spec Run (Payments + Notifications)${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Running with TWO specs for cross-API workflow...${NC}"
echo ""
python -m integration_coworker.cli run \
  -s tests/fixtures/mock_payments_openapi.yaml \
  -s tests/fixtures/mock_notifications_openapi.yaml \
  -t "Create checkout session and send confirmation notification to customer" \
  --repo-root "$TARGET_REPO" \
  -p "payments_notifications"
echo ""

# ============================================================================
# STEP 7: Real Public Spec - Petstore (Quick)
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 7: Real Public Spec - Swagger Petstore${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}URL: https://petstore3.swagger.io/api/v3/openapi.json${NC}"
echo ""
python -m integration_coworker.cli run \
  -s "https://petstore3.swagger.io/api/v3/openapi.json" \
  -t "Add a new pet to the store and update its status to sold" \
  --repo-root "$TARGET_REPO"
echo ""

# ============================================================================
# STEP 8: Check KG Growth
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 8: Knowledge Graph Growth Summary${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}KG Nodes by type (FINAL):${NC}"
psql "$DATABASE_URL" -c "SELECT node_type, COUNT(*) as count FROM kg.nodes GROUP BY node_type ORDER BY count DESC"
echo ""
echo -e "${BLUE}KG Edges by relation (FINAL):${NC}"
psql "$DATABASE_URL" -c "SELECT relation_type, COUNT(*) as count FROM kg.edges GROUP BY relation_type ORDER BY count DESC"
echo ""
echo -e "${BLUE}All workflow templates learned:${NC}"
psql "$DATABASE_URL" -c "SELECT provider_code, key, name FROM kg.nodes WHERE node_type = 'workflow_template' ORDER BY provider_code"
echo ""
echo -e "${BLUE}Workflow steps count:${NC}"
psql "$DATABASE_URL" -c "SELECT COUNT(*) as total_steps FROM kg.workflow_steps"
echo ""

# ============================================================================
# STEP 9: Show Generated Code in Target Repo
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 9: Generated Code Artifacts in Target Repo${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}Files created in ${TARGET_REPO}:${NC}"
find "$TARGET_REPO" -name "*.py" -type f | head -20
echo ""

echo -e "${BLUE}━━━ Sample Client Code ━━━${NC}"
FIRST_CLIENT=$(find "$TARGET_REPO/src/integrations/clients" -name "*.py" -type f | head -1)
if [ -n "$FIRST_CLIENT" ]; then
  echo -e "${GREEN}File: $FIRST_CLIENT${NC}"
  head -60 "$FIRST_CLIENT"
fi
echo ""

echo -e "${BLUE}━━━ Sample Flow Code ━━━${NC}"
FIRST_FLOW=$(find "$TARGET_REPO/src/integrations/flows" -name "*.py" -type f | head -1)
if [ -n "$FIRST_FLOW" ]; then
  echo -e "${GREEN}File: $FIRST_FLOW${NC}"
  head -60 "$FIRST_FLOW"
fi
echo ""

echo -e "${BLUE}━━━ Sample Test Code ━━━${NC}"
FIRST_TEST=$(find "$TARGET_REPO/tests/integrations" -name "*.py" -type f | head -1)
if [ -n "$FIRST_TEST" ]; then
  echo -e "${GREEN}File: $FIRST_TEST${NC}"
  head -60 "$FIRST_TEST"
fi
echo ""

# ============================================================================
# STEP 10: Code Artifacts in Database
# ============================================================================
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${YELLOW}STEP 10: Persisted Code Artifacts in Database${NC}"
echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
psql "$DATABASE_URL" -c "SELECT artifact_type, module_name, rel_path FROM integration_gold.code_artifacts ORDER BY artifact_type, module_name" 2>/dev/null || echo "(Table may not exist yet)"
echo ""

# ============================================================================
# FINAL SUMMARY
# ============================================================================
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}   ✅ DEMO COMPLETE${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "${GREEN}What was demonstrated:${NC}"
echo "  1. ✓ KG learning from first run (demo --persist)"
echo "  2. ✓ GraphRAG retrieval on second run (similar task)"
echo "  3. ✓ Multi-spec workflow (payments + notifications)"
echo "  4. ✓ Real public spec (Petstore API)"
echo "  5. ✓ Code generation (client, flow, test)"
echo "  6. ✓ Repo integration (wrote to $TARGET_REPO)"
echo "  7. ✓ KG growth tracking (nodes, edges, templates)"
echo ""
echo -e "${BLUE}Target repo location: ${TARGET_REPO}${NC}"
echo -e "${BLUE}To explore: ls -la ${TARGET_REPO}/src/integrations/${NC}"
echo ""

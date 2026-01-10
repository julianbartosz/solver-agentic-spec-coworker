#!/bin/bash
#
# CI Hardening Gates for Discovery Module
#
# These gates enforce architectural constraints that protect against
# security regressions in the discovery module.
#
# Run this in CI to block merges that bypass hardened HTTP.
#
# Gates:
#   1-10: HTTP hardening (http_client.py invariants)
#   11-13: Test-based verification
#   14-16: Slice 4 web search module constraints
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "=== Discovery Module Hardening Gates ==="
echo ""

# Gate 1: No direct httpx imports outside http_client.py and web_search modules
# (web_search_providers.py is allowed because it needs POST for Tavily/SerpApi API)
echo -n "Gate 1: No direct httpx imports in discovery/... "
HTTPX_VIOLATIONS=$(grep -rn "import httpx" "${PROJECT_ROOT}/src/integration_coworker/discovery/" \
    --include="*.py" \
    | grep -v "http_client.py" \
    | grep -v "web_search.py" \
    | grep -v "web_search_providers.py" \
    | grep -v "__pycache__" || true)

if [ -n "${HTTPX_VIOLATIONS}" ]; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "Found direct httpx imports outside http_client.py:"
    echo "${HTTPX_VIOLATIONS}"
    echo ""
    echo "All HTTP fetches in discovery/ must go through hardened_fetch() in http_client.py"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 2: No httpx.AsyncClient or httpx.Client instantiation outside http_client.py and web_search modules
echo -n "Gate 2: No httpx.Client instantiation in discovery/... "
CLIENT_VIOLATIONS=$(grep -rn "httpx\.\(Async\)\?Client" "${PROJECT_ROOT}/src/integration_coworker/discovery/" \
    --include="*.py" \
    | grep -v "http_client.py" \
    | grep -v "web_search.py" \
    | grep -v "web_search_providers.py" \
    | grep -v "__pycache__" || true)

if [ -n "${CLIENT_VIOLATIONS}" ]; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "Found direct httpx client instantiation outside http_client.py:"
    echo "${CLIENT_VIOLATIONS}"
    echo ""
    echo "Use hardened_fetch() instead of creating clients directly."
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 3: http_client.py has trust_env=False
echo -n "Gate 3: trust_env=False in http_client.py... "
if ! grep -q "trust_env=False" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "http_client.py must have trust_env=False to block proxy env leakage"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 4: http_client.py has follow_redirects=False
echo -n "Gate 4: follow_redirects=False (manual loop)... "
if ! grep -q "follow_redirects=False" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "http_client.py must have follow_redirects=False for manual redirect loop"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 5: http_client.py has explicit timeout configuration
echo -n "Gate 5: Explicit timeout configuration... "
if ! grep -q "httpx.Timeout" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "http_client.py must use explicit httpx.Timeout, not default timeouts"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 6: http_client.py has explicit limits configuration
echo -n "Gate 6: Explicit limits configuration... "
if ! grep -q "httpx.Limits" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "http_client.py must use explicit httpx.Limits, not default limits"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 7: http_client.py uses streaming for byte cap enforcement
echo -n "Gate 7: Streaming byte cap (aiter_bytes)... "
if ! grep -q "aiter_bytes" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "http_client.py must use aiter_bytes() for streaming byte cap enforcement"
    echo "Do NOT use response.text which materializes the full body first"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 8: http_client.py has loop detection
echo -n "Gate 8: Redirect loop detection... "
if ! grep -q "seen_urls" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "http_client.py must have redirect loop detection (seen_urls set)"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 9: http_client.py blocks dangerous schemes
echo -n "Gate 9: Dangerous scheme blocking... "
if ! grep -q "ALLOWED_SCHEMES" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "http_client.py must have ALLOWED_SCHEMES to block file://, gopher://, etc."
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 10: http_client.py has total timeout budget
echo -n "Gate 10: Total timeout budget (INVARIANT 6)... "
if ! grep -q "DEFAULT_TOTAL_TIMEOUT" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "http_client.py must have DEFAULT_TOTAL_TIMEOUT for total budget across redirects"
    exit 1
fi
if ! grep -q "total_timeout_seconds" "${PROJECT_ROOT}/src/integration_coworker/discovery/http_client.py"; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "hardened_fetch must have total_timeout_seconds parameter"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 11: No response.text or response.content (INVARIANT 3)
# This is now handled by AST-based gate which is more robust
echo -n "Gate 11: AST-based response.text/content check... "
if ! python "${SCRIPT_DIR}/ast_security_gate.py" > /tmp/ast_gate_output.txt 2>&1; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    cat /tmp/ast_gate_output.txt
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 12: Run hardening acceptance tests (string-based gates)
echo -n "Gate 12: Hardening acceptance tests... "
if ! python -m pytest "${PROJECT_ROOT}/tests/discovery/test_hardening.py" -q --tb=no 2>/dev/null; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "Hardening acceptance tests failed. Run:"
    echo "  pytest tests/discovery/test_hardening.py -v"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 13: SEMANTIC GATE - Run behavior-proving invariant tests
# These tests PROVE the invariants work, not just that strings exist
echo -n "Gate 13: Invariant behavior tests (semantic)... "
INVARIANT_TEST_PATTERN="TestInvariant1 or TestInvariant2 or TestInvariant3 or TestInvariant5 or TestInvariant6 or TestInvariant8 or TestBehaviorInvariant4 or TestBehaviorInvariant6 or TestBehaviorInvariant9 or TestBehaviorInvariant10"
if ! python -m pytest "${PROJECT_ROOT}/tests/discovery/test_hardening.py" -k "${INVARIANT_TEST_PATTERN}" -q --tb=short 2>&1; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "Invariant behavior tests failed. These prove the security contract:"
    echo "  pytest tests/discovery/test_hardening.py -k '${INVARIANT_TEST_PATTERN}' -v"
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# =============================================================================
# Slice 4: Web Search Module Gates (14-17)
# =============================================================================

# Gate 14: web_search_providers.py has trust_env=False (same as http_client.py)
echo -n "Gate 14: trust_env=False in web_search_providers.py... "
WEB_SEARCH_PROVIDERS_FILE="${PROJECT_ROOT}/src/integration_coworker/discovery/web_search_providers.py"
WEB_SEARCH_CONFIG_FILE="${PROJECT_ROOT}/src/integration_coworker/discovery/web_search_config.py"
WEB_SEARCH_FILE="${PROJECT_ROOT}/src/integration_coworker/discovery/web_search.py"
if [ -f "${WEB_SEARCH_PROVIDERS_FILE}" ]; then
    if ! grep -q "trust_env=False" "${WEB_SEARCH_PROVIDERS_FILE}"; then
        echo -e "${RED}FAILED${NC}"
        echo ""
        echo "web_search_providers.py must have trust_env=False to block proxy env leakage"
        exit 1
    fi
    echo -e "${GREEN}PASSED${NC}"
elif [ -f "${WEB_SEARCH_FILE}" ]; then
    # Legacy: check web_search.py if providers not split out yet
    if ! grep -q "trust_env=False" "${WEB_SEARCH_FILE}"; then
        echo -e "${RED}FAILED${NC}"
        echo ""
        echo "web_search.py must have trust_env=False to block proxy env leakage"
        exit 1
    fi
    echo -e "${GREEN}PASSED${NC}"
else
    echo -e "${YELLOW}SKIPPED (web_search_providers.py not found)${NC}"
fi

# Gate 15: Web search is disabled by default
echo -n "Gate 15: Web search disabled by default... "
if [ -f "${WEB_SEARCH_CONFIG_FILE}" ]; then
    # Check that DISCOVERY_WEB_SEARCH_ENABLED defaults to "false"
    if grep -q 'DISCOVERY_WEB_SEARCH_ENABLED.*"false"' "${WEB_SEARCH_CONFIG_FILE}" || \
       grep -q 'enabled.*=.*False' "${WEB_SEARCH_CONFIG_FILE}"; then
        echo -e "${GREEN}PASSED${NC}"
    else
        echo -e "${RED}FAILED${NC}"
        echo ""
        echo "Web search must be disabled by default (DISCOVERY_WEB_SEARCH_ENABLED)"
        exit 1
    fi
elif [ -f "${WEB_SEARCH_FILE}" ]; then
    # Legacy: check web_search.py if config not split out yet
    if ! grep -q 'enabled.*=.*False' "${WEB_SEARCH_FILE}" && \
       ! grep -q "default.*false" "${WEB_SEARCH_FILE}" && \
       ! grep -q 'DISCOVERY_WEB_SEARCH_ENABLED.*false' "${WEB_SEARCH_FILE}"; then
        if ! grep -q '\.lower() in ("1", "true", "yes")' "${WEB_SEARCH_FILE}"; then
            echo -e "${RED}FAILED${NC}"
            echo ""
            echo "Web search must be disabled by default (DISCOVERY_WEB_SEARCH_ENABLED)"
            exit 1
        fi
    fi
    echo -e "${GREEN}PASSED${NC}"
else
    echo -e "${YELLOW}SKIPPED (web_search_config.py not found)${NC}"
fi

# Gate 16: No 'requests' library imports in discovery module
echo -n "Gate 16: No 'requests' library in discovery/... "
REQUESTS_VIOLATIONS=$(grep -rn "^import requests\|^from requests" "${PROJECT_ROOT}/src/integration_coworker/discovery/" \
    --include="*.py" \
    | grep -v "__pycache__" || true)

if [ -n "${REQUESTS_VIOLATIONS}" ]; then
    echo -e "${RED}FAILED${NC}"
    echo ""
    echo "Found 'requests' library imports in discovery/:"
    echo "${REQUESTS_VIOLATIONS}"
    echo ""
    echo "Use httpx (via hardened_fetch) instead of requests."
    exit 1
fi
echo -e "${GREEN}PASSED${NC}"

# Gate 17: Web search tests exist and pass without network
echo -n "Gate 17: Web search tests (offline)... "
WEB_SEARCH_TEST_FILE="${PROJECT_ROOT}/tests/discovery/test_web_search.py"
if [ -f "${WEB_SEARCH_TEST_FILE}" ]; then
    if ! python -m pytest "${WEB_SEARCH_TEST_FILE}" -q --tb=no 2>/dev/null; then
        echo -e "${RED}FAILED${NC}"
        echo ""
        echo "Web search tests failed. Run:"
        echo "  pytest tests/discovery/test_web_search.py -v"
        exit 1
    fi
    echo -e "${GREEN}PASSED${NC}"
else
    echo -e "${YELLOW}SKIPPED (test_web_search.py not found)${NC}"
fi

# Gate 18: SerpApi requires explicit opt-in (DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI)
# This is a legal risk mitigation gate - SerpApi scrapes Google results which
# Google considers a ToS violation. Operators must explicitly opt-in.
echo -n "Gate 18: SerpApi requires explicit opt-in flag... "
if [ -f "${WEB_SEARCH_CONFIG_FILE}" ]; then
    # Check that allow_serpapi field exists and defaults to False
    if grep -q 'allow_serpapi.*=.*field' "${WEB_SEARCH_CONFIG_FILE}" && \
       grep -q 'DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI.*"false"' "${WEB_SEARCH_CONFIG_FILE}"; then
        echo -e "${GREEN}PASSED${NC}"
    else
        echo -e "${RED}FAILED${NC}"
        echo ""
        echo "web_search_config.py must have allow_serpapi field that defaults to False"
        echo "SerpApi requires explicit opt-in via DISCOVERY_WEB_SEARCH_ALLOW_SERPAPI=true"
        exit 1
    fi
else
    echo -e "${YELLOW}SKIPPED (web_search_config.py not found)${NC}"
fi

# Gate 19: Web search behavior tests pass (hermetic cache tests)
echo -n "Gate 19: Web search behavior tests (hermetic)... "
WEB_SEARCH_BEHAVIOR_TEST_FILE="${PROJECT_ROOT}/tests/discovery/test_web_search_behavior.py"
if [ -f "${WEB_SEARCH_BEHAVIOR_TEST_FILE}" ]; then
    if ! python -m pytest "${WEB_SEARCH_BEHAVIOR_TEST_FILE}" -q --tb=no 2>/dev/null; then
        echo -e "${RED}FAILED${NC}"
        echo ""
        echo "Web search behavior tests failed. Run:"
        echo "  pytest tests/discovery/test_web_search_behavior.py -v"
        exit 1
    fi
    echo -e "${GREEN}PASSED${NC}"
else
    echo -e "${YELLOW}SKIPPED (test_web_search_behavior.py not found)${NC}"
fi

echo ""
echo -e "${GREEN}=== All hardening gates passed (19 gates) ===${NC}"

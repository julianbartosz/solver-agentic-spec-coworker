# Live Fire Demo Guide

> **Duration**: 10 minutes  
> **Goal**: Prove the coworker modifies a real repo to implement a missing external API call, with tests proving it works.

---

## Prerequisites

### Environment
```bash
# Activate virtual environment
source .venv311/bin/activate

# Verify environment
./scripts/setup_env.sh --check
```

### Required Tools
- Docker (for WireMock stub server)
- Python 3.11+
- Git

---

## Demo Setup (5 minutes before)

### 1. Clone demo repo
```bash
# Use the included test fixture or a dedicated demo repo
export DEMO_REPO=/tmp/demo-repo
cp -r tests/fixtures/repos/fastapi_service $DEMO_REPO
cd $DEMO_REPO
```

### 2. Start stub server (WireMock)
```bash
# From project root
docker-compose -f docker-compose.yml up -d wiremock

# Verify it's running
curl http://localhost:8080/__admin/mappings
```

### 3. Configure stub endpoint
```bash
# Add a mock payments endpoint
curl -X POST http://localhost:8080/__admin/mappings \
  -H "Content-Type: application/json" \
  -d '{
    "request": {
      "method": "POST",
      "urlPath": "/v1/checkout/sessions"
    },
    "response": {
      "status": 200,
      "jsonBody": {"id": "cs_test_123", "status": "open"},
      "headers": {"Content-Type": "application/json"}
    }
  }'
```

### 4. Verify no prior requests
```bash
# Should return empty items array
curl http://localhost:8080/__admin/requests | jq '.requests | length'
# Expected: 0
```

---

## Demo Script

### Step 1: Show the "before" state (1 min)

**Talking point**: "This repo has no integration with the payments API."

```bash
# Show there's no payments client
ls -la $DEMO_REPO/app/integrations/ 2>/dev/null || echo "No integrations directory"

# Show tests would fail (no client exists)
cd $DEMO_REPO
pytest tests/ -v --tb=short 2>&1 | head -20
```

**Show WireMock received zero requests:**
```bash
curl -s http://localhost:8080/__admin/requests | jq '.requests | length'
# Shows: 0
```

### Step 2: Run the coworker (3 min)

**Talking point**: "Now we run the coworker with a task description."

```bash
cd /path/to/solver-agentic-spec-coworker

PYTHONPATH=src python -m integration_coworker.cli run \
  --spec-ref tests/fixtures/mock_payments_openapi.yaml \
  --task "Create checkout session" \
  --repo-root $DEMO_REPO \
  --dry-run
```

**What to highlight:**
- Spec ingestion (endpoints detected)
- Task understanding (constraints identified)
- Flow planning (workflow nodes)
- Code generation (artifacts created)

### Step 3: Apply changes (1 min)

**Talking point**: "In dry-run we saw what would happen. Now we apply."

```bash
# Run without --dry-run to actually write files
PYTHONPATH=src python -m integration_coworker.cli run \
  --spec-ref tests/fixtures/mock_payments_openapi.yaml \
  --task "Create checkout session" \
  --repo-root $DEMO_REPO
```

### Step 4: Show the "after" state (2 min)

**Show generated files:**
```bash
# Client code
cat $DEMO_REPO/app/integrations/clients/mock_payments.py | head -50

# Flow code
cat $DEMO_REPO/app/integrations/flows/mock_payments_checkout.py | head -50

# Test code
cat $DEMO_REPO/tests/integrations/test_mock_payments_checkout.py | head -50
```

**Show the diff:**
```bash
cd $DEMO_REPO
git diff --stat
git diff app/integrations/
```

### Step 5: Run tests and show request (2 min)

**Run the generated tests:**
```bash
cd $DEMO_REPO
pytest tests/integrations/test_mock_payments_checkout.py -v
```

**Show WireMock received the request:**
```bash
curl -s http://localhost:8080/__admin/requests | jq '.requests[0]'
```

**Expected output:**
```json
{
  "request": {
    "method": "POST",
    "url": "/v1/checkout/sessions",
    "headers": {
      "Authorization": "Bearer test_api_key"
    }
  }
}
```

### Step 6: HITL approval step (1 min)

**Talking point**: "The coworker generates code, but humans approve before merge."

```bash
# Show the generated code for review
cd $DEMO_REPO
git add -A
git diff --cached

# In real workflow, this becomes a PR for review
```

---

## Failure Modes (Optional Demos)

### Bad API Key
```bash
# Configure WireMock to return 401
curl -X POST http://localhost:8080/__admin/mappings \
  -d '{"request":{"method":"POST","urlPath":"/v1/checkout/sessions"},"response":{"status":401}}'

# Run and show retry/error handling
```

### Timeout
```bash
# Configure WireMock with delay
curl -X POST http://localhost:8080/__admin/mappings \
  -d '{"request":{"method":"POST","urlPath":"/v1/checkout/sessions"},"response":{"fixedDelayMilliseconds":35000,"status":200}}'
```

### Schema Mismatch
- Modify the OpenAPI spec to have different field names
- Show validation errors in generated code

---

## Cleanup

```bash
# Stop WireMock
docker-compose down

# Remove demo repo
rm -rf $DEMO_REPO
```

---

## Key Points to Emphasize

1. **Before/After proof**: Zero requests → One successful request
2. **Spec-driven**: Generated code matches OpenAPI contract
3. **Testable**: Generated tests actually run and pass
4. **HITL**: Human reviews and approves all changes
5. **Observable**: LangSmith traces show reasoning

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| WireMock not responding | Check `docker ps`, restart container |
| Tests fail with import error | Activate venv, check PYTHONPATH |
| No files generated | Check `--repo-root` path, verify write permissions |
| LLM timeout | Check API keys, try `USE_MOCK_LLM=true` for demo |

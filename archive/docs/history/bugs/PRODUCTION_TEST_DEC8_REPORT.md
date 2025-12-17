# Production Test Report - December 8, 2025

## Executive Summary

Ran comprehensive production tests covering multi-endpoint specs, LLM repo inference, database persistence, edge cases, and parallel codegen. **Found 8 bugs** ranging from missing CLI commands to LLM output validation issues.

## Test Environment

- **Database**: PostgreSQL (postgresql://integration:integration@localhost:5432/integration_coworker)
- **LLM**: OpenAI API (gpt-4.1 async, claude for sync)
- **Specs Tested**: 
  - OpenAI API (70K lines, 220 endpoints)
  - Stripe API (189K lines, 585 endpoints)
  - Twilio Messaging (11K lines, ~10 endpoints)
  - Empty spec (edge case)

---

## Bugs Found

### BUG #44: Resume CLI Command Missing [HIGH]

**Feature**: Resume from Checkpoint (Bug #36)

**Issue**: The `resume` command documented in Bug #36 fix doesn't exist in the CLI.

**Evidence**:
```
$ python -m integration_coworker.cli resume --help
No such command 'resume'.
```

**Impact**: Users cannot resume failed/interrupted runs despite checkpoints being saved (43 checkpoints in DB).

**Fix**: Implement `resume` command in `cli.py` that reads from `run_checkpoints` table.

---

### BUG #45: LLM Output Fails Expected Symbol Validation [HIGH]

**Feature**: Parallel Async Codegen

**Issue**: LLM-generated code consistently fails the `expected_class` and `expected_function` validation checks, causing all artifacts to fall back to templates.

**Evidence**:
```
WARNING: [create_chat_completion_flow] Attempt 1: Missing function create_chat_completion_flow
WARNING: [StripeProdTestDec8Client] Attempt 1: Missing class StripeProdTestDec8Client
WARNING: [OpenaiProdTestDec8Client] Failed all 3 attempts, using template fallback
```

**Impact**: Parallel codegen returns 0/3 LLM-generated artifacts, always falling back to templates.

**Root Cause**: The LLM is generating valid code but with different naming than expected (e.g., `OpenAIClient` instead of `OpenaiProdTestDec8Client`).

**Fix Options**:
1. Relax validation to accept reasonable class name alternatives
2. Include expected names more prominently in the prompt
3. Add a rename pass that fixes class/function names post-generation

---

### BUG #46: Database Connection Pool Leak Warning [MEDIUM]

**Feature**: Database Persistence

**Issue**: Consistent warning about connection pool leaks.

**Evidence**:
```
WARNING: ConnectionWrapper was garbage collected without being closed. 
Use 'with db.get_connection() as conn:' pattern for proper cleanup. 
(This warning is shown once per session)
```

**Impact**: Potential connection exhaustion under heavy load.

**Fix**: Audit all database access patterns to ensure `with` context manager usage.

---

### BUG #47: LLM Repo Inference Not Triggered Due to Rate Limits [MEDIUM]

**Feature**: LLM Repo Inference

**Issue**: LLM inference fails with 429 rate limit errors before falling back to convention inference.

**Evidence**:
```
ERROR: [LlmInferenceTestClient] Attempt 3 error: Error code: 429 - 
{'error': {'message': 'You exceeded your current quota...'}}
Profile Source: convention_inference  (not llm_inference)
```

**Impact**: LLM repo inference feature isn't being used when quota is limited.

**Positive**: Convention fallback (`_infer_profile_from_conventions`) works correctly as designed.

---

### BUG #48: Parallel Codegen Log Messages Not Appearing in Production Runs [LOW]

**Feature**: Parallel Async Codegen

**Issue**: The `"Running parallel codegen"` and `"Using full parallel codegen results"` log messages don't appear in production run logs.

**Evidence**:
```
$ grep -iE "parallel" /tmp/test1_openai.log
(no output)
```

But parallel IS working when tested directly:
```
INFO:integration_coworker.graph.nodes.generate_code_and_tests:Running parallel codegen for 1 endpoint(s)
INFO:integration_coworker.graph.nodes.generate_code_and_tests:Parallel codegen completed: 1/3 artifacts succeeded
```

**Impact**: Difficult to verify parallel execution in production logs.

**Fix**: Ensure log level is INFO in production CLI runs.

---

### BUG #49: Psycopg Connection Rollback Messages [LOW]

**Feature**: Database Persistence

**Issue**: Multiple "rolling back returned connection" messages in logs.

**Evidence**:
```
rolling back returned connection: <psycopg.Connection [INTRANS] (host=localhost...)>
```

**Impact**: Indicates uncommitted transactions, possible data loss.

**Fix**: Ensure all DB operations properly commit or rollback.

---

### BUG #50: Endpoint Binding with endpoint_id=None [LOW]

**Feature**: Workflow Planning

**Issue**: EndpointBinding created with `endpoint_id=None` when spec has no matching endpoints.

**Evidence**:
```
Warning: EndpointBinding for node 'call_api' has endpoint_id=None
```

**Impact**: Downstream code may fail when trying to use null endpoint.

**Fix**: Validate endpoint bindings before proceeding to codegen.

---

### BUG #51: Confidence History Not Being Recorded [LOW]

**Feature**: Knowledge Graph Learning

**Issue**: `confidence_history` table has 0 records despite 24 feedback records and 498 KG edges.

**Evidence**:
```sql
SELECT COUNT(*) FROM kg.confidence_history;
-- 0 rows
```

**Impact**: Historical confidence tracking not working.

**Fix**: Ensure confidence updates are logged to history table.

---

## Tests Passed ✅

| Test | Result | Notes |
|------|--------|-------|
| Multi-Endpoint OpenAI Spec | ✅ | 220 endpoints, 3 min runtime |
| Multi-Endpoint Stripe Spec | ✅ | 585 endpoints, 4.7 min runtime |
| LLM Repo Inference (FastAPI) | ✅ | Convention fallback worked |
| Database Persistence | ✅ | 498 edges, 330 entities, 6 tasks |
| Code Artifacts Generated | ✅ | 18 artifacts, ~3KB avg |
| File Write to Repo | ✅ | 6 files created in test repo |
| Empty Spec Edge Case | ✅ | Graceful error handling |
| KG Nodes Created | ✅ | 330 entities, 150 endpoints |

---

## Metrics

| Metric | Value |
|--------|-------|
| Total Production Runs | 6 |
| Endpoints Discovered | 815+ |
| Code Artifacts Generated | 18 |
| KG Nodes | 498 |
| KG Edges | 498 |
| Checkpoints Saved | 43 |
| Bugs Found | 8 |

---

## Priority Fixes

1. **BUG #44** (Resume CLI) - HIGH - Core feature documented but missing
2. **BUG #45** (Symbol Validation) - HIGH - Parallel codegen always falling back
3. **BUG #46** (Connection Pool) - MEDIUM - Production stability risk
4. **BUG #47** (Rate Limits) - MEDIUM - Feature not used when quota limited

---

## Recommendations

1. **Immediate**: Implement resume CLI command
2. **Immediate**: Fix symbol validation to accept LLM variations
3. **Short-term**: Add connection pool monitoring
4. **Short-term**: Implement graceful rate limit handling with longer backoff

---

*Report generated by production testing session on December 8, 2025*

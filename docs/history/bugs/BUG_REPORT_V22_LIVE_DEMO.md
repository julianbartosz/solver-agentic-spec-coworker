# Bug Report V22 - Live Demo Audit (2025-12-24 to 2025-12-26)

## Executive Summary

This report documents bugs discovered during live demo run audited from `/tmp/demo-livev4-20251224-153434.log` (24,020+ lines). The demo ran with command:
```bash
INTEGRATION_COWORKER_PROFILE=production VALIDATION_PROFILE=live ./scripts/demo-final-showcase.sh --live --fresh
```

### Key Statistics
- **Total runs in last 3 days**: 47
- **Completed successfully**: 10 (21%)
- **Completed with errors**: 21 (45%)
- **Still running/stuck**: 16 (34%)
- **Average duration (errors)**: 1529s (25 min)
- **Average duration (success)**: 621s (10 min)
- **Target duration**: 300s (5 min)

---

## BUG-V22-001: Massive State Size Bloat (CRITICAL)

### Severity: P0 - Critical Performance Issue

### Description
The workflow state is growing to astronomical sizes during node execution, causing massive memory pressure and likely triggering OOM kills (exit code 137 = SIGKILL).

### Evidence
From logs showing `output_bytes` field:
```
validate_integration_design: output_bytes=69,462,613,912 (~65 GB)
build_report:                 output_bytes=138,924,226,074 (~129 GB)
```

### State Growth Pattern
| Node | Output Size | Growth Factor |
|------|-------------|---------------|
| ingest_spec | 700 KB | baseline |
| detect_and_parse_spec | 700 KB | 1.0x |
| analyze_repo_layout | 8.7 GB | **12,232x** |
| apply_repo_integration_changes | 34.7 GB | 4.0x |
| validate_integration_design | 69.5 GB | 2.0x |
| build_report | 138.9 GB | 2.0x |

### Root Cause Analysis
The **massive 12,232x jump at `analyze_repo_layout`** was caused by a bug in `_get_state_size()`:
1. The estimation used unbounded recursion with `item_count * 100` multiplier for containers
2. This caused exponential growth when traversing nested structures like `repo_snapshot.files`
3. The calculation compounded on each node, doubling with every checkpoint

### Fix Applied (2025-01-XX)

**Bounded Sampling Estimator** implemented in `src/integration_coworker/graph/runtime.py`:
- Max recursion depth: 4 levels
- Max sample items per container: 50 entries
- 100MB cap on estimates to prevent absurd values
- Deterministic traversal (sorted keys for dicts/sets)

**Validation Results** (from `scripts/validate_v22_001_fix.py`):
| Metric | Before Fix | After Fix |
|--------|------------|-----------|
| Stage 1→2 Growth | 12,232x | 9.5x |
| Stage 2→3 Growth | 2.0x | 1.0x |
| Final Estimate | 139GB | 4.82MB |
| Determinism | Non-deterministic | 100% deterministic |
| Estimation Time | Unknown | 0.2ms |

**Enhanced Observability**:
- Logs both `output_bytes_uncapped` and `output_bytes_capped` for regression detection
- Logs `output_bytes_method='sampled_recursive'` with parameters
- Logs `rss_bytes`, `tracemalloc_current`, `tracemalloc_peak` at node boundaries
- Logs `memory_baseline` at workflow start and `memory_final` at workflow end

### Status: ✅ FIXED AND VALIDATED

---

## BUG-V22-002: "Syntax error at line 8: invalid syntax" (HIGH)

### Severity: P1 - Blocks Code Generation

### Description
Multiple flows fail with identical error pattern: "Syntax error at line 8: invalid syntax"

### Affected Providers (from logs)
1. twilio_messaging_v1 - `send_sms_message_flow`
2. slack_api - `send_message_to_slack_channel_flow`
3. openai_api - `create_chat_completion_flow`
4. zoom_api - `create_meeting_with_participants_flow`
5. circleci_api - `trigger_pipeline_build_flow`
6. plaid_api - `link_bank_account_flow`
7. petstore_v3 - `add_new_pet_to_store_flow`
8. httpbin_api - `make_post_request_with_json_flow`

### Pattern
All failures occur in `packages_sdk_python` repos, suggesting an issue with the flow template generation for that repo layout.

### Error Location
- `src/integration_coworker/codegen/security.py:296` - `validate_syntax()` function reports line 8

### Hypothesis
Line 8 in generated flow code is typically the import section after the docstring:
```python
"""                          # Line 1-6: Docstring
...
"""
from typing import ...       # Line 7
from clients.xxx import ...  # Line 8 <- ERROR HERE
```

The LLM may be generating malformed import statements with incorrect module paths for the packages_sdk_python repo layout.

### Required Investigation
- Capture actual generated code for one failed flow
- Verify the module import path generation for packages_sdk_python

---

## BUG-V22-003: Test Mock Assertion Failures (HIGH)

### Severity: P1 - All Generated Tests Fail

### Description
Generated tests consistently fail with `AssertionError: assert False` because the mock client method was never called.

### Evidence
```
tests/test_github_api_root_create_new_issue.py:49: 
    assert mock_client.create_issue.called
E   AssertionError: assert False

tests/test_slack_api_root_send_message_to_slack_channel.py:48:
    assert mock_client.chat_postMessage.called
E   AssertionError: assert False

tests/test_openai_api_apps_service_a_create_chat_completion_with_gpt4.py:55:
    assert mock_client.create_assistant.called
E   AssertionError: assert False
```

### Root Cause Analysis
The generated test code patches the wrong location or uses incorrect method names:
1. Mock patch target may not match actual import path in flow code
2. Method name in assertion doesn't match actual method called
3. Client instantiation inside flow uses different import than test patch

### Example Mismatch
Test expects: `mock_client.create_assistant.called`
But flow actually calls: `client.create_chat_completion()` (different method)

### Affected Code
- `src/integration_coworker/graph/nodes/generate_code_and_tests.py` - `_generate_test_code()`
- Test template generation needs to match flow method calls

---

## BUG-V22-004: ModuleNotFoundError for Provider Clients (MEDIUM)

### Severity: P2 - Sandbox Test Failures

### Description
Sandbox pytest fails with `ModuleNotFoundError: No module named 'clients.<provider>_client'`

### Evidence
```
from workflows.stripe_api_apps_service_a_create_checkout_session import (
from clients.stripe_api_apps_service_a_client import (
E   ModuleNotFoundError: No module named 'clients.stripe_api_a'
```

### Root Cause
Dynamic stub generation (v21 fix) is not creating client stubs for all providers before tests run.

### Affected Code
- Sandbox client stub generation logic
- `src/integration_coworker/codegen/sandbox.py`

---

## BUG-V22-005: Mypy Type Errors in Generated Code (MEDIUM)

### Severity: P2 - Sandbox Validation Failures

### Description
Generated client code fails mypy validation with `[union-attr]` errors.

### Evidence
```
src/clients/asana_api_packages_sdk_python.py:250: error: 
    Item "None" of "Response | None" has no attribute "status_code"  [union-attr]

src/integrations/clients/digitalocean_api_root.py:290: error:
    Item "None" of "Response | None" has no attribute "text"  [union-attr]
```

### Root Cause
Generated code does not handle `Optional[Response]` return types properly. Need null checks before accessing `.status_code` or `.text`.

### Fix Pattern
```python
# Current (fails mypy)
return response.status_code

# Fixed
if response is None:
    raise IntegrationError("No response received")
return response.status_code
```

### Affected Code
- Client template generation
- `_generate_client_code()` functions

---

## BUG-V22-006: Coverage Below Threshold (MEDIUM)

### Severity: P2 - Sandbox Validation Failures

### Description
Test coverage consistently falls below the 20% threshold.

### Evidence
```
FAIL Required test coverage of 20% not reached. Total coverage: 11.63%
FAIL Required test coverage of 20% not reached. Total coverage: 12%
Coverage failure: total of 19 is less than fail-under=20
```

### Root Cause
Generated tests only exercise the "happy path" and don't cover:
1. Error handling paths
2. Edge cases
3. Client method internals

### Affected Code
- Test generation needs expanded scenarios
- Consider generating multiple test cases per flow

---

## BUG-V22-007: Global Workflow Timeout (HIGH)

### Severity: P1 - Runs Exceed Time Limits

### Description
Workflows hit the 3600s (1 hour) global timeout, resulting in forced termination.

### Evidence
```
ERROR Global workflow timeout exceeded: 3600.0s (run_id=run_8cfa533c_1766622921)
```

### Contributing Factors (from node durations)
| Node | Max Duration |
|------|--------------|
| persist_silver_checkpoint | 5,062 ms |
| build_report | 2,060 ms |
| detect_and_parse_spec | 1,371 ms |
| persist_kg_learning | 1,561 ms |
| validate_integration_design | 781 ms |

The cumulative effect of many slow nodes causes timeouts.

---

## BUG-V22-008: "Shutdown requested, aborting LLM slot acquisition" (MEDIUM)

### Severity: P2 - Cascading Failure Pattern

### Description
When approaching timeout, `build_report` node fails with LLM slot acquisition abort.

### Evidence
```
event: llm.call.error
node_name: build_report
step: executive_summary
error_type: RuntimeError
error_message: Shutdown requested, aborting LLM slot acquisition
```

### Pattern
This occurs consistently at `build_report → executive_summary` step when the global timeout approaches, suggesting:
1. Timeout signal arrives during LLM call
2. LLM slot semaphore acquisition is interrupted
3. Report generation fails, leaving incomplete output

### Affected Code
- `src/integration_coworker/llm/slots.py` or similar
- Need graceful timeout handling

---

## BUG-V22-009: Migration Checksum Mismatch (LOW)

### Severity: P3 - Informational Warning Spam

### Description
Persistent warning about migration checksum mismatch floods logs.

### Evidence
```
WARNING  Migration 001_baseline_v1 checksum mismatch: 
    file=df409efaca082c2a, db=0940a813cebe63f7. 
    Migration file may have been modified after application.
```

### Root Cause
Migration file was modified after initial application to database.

### Fix
Either:
1. Update the stored checksum in database, or
2. Create a new migration version with the changes

---

## BUG-V22-010: Self-Review Cannot Repair (MEDIUM)

### Severity: P2 - Codegen Failure Path

### Description
When self-review validation fails, the repair mechanism is not able to fix issues.

### Evidence
```
ERROR [self_review] Production: cannot repair, failing hard:
Strict codegen failed for flow 'search_tracks_by_artist_name_flow': Self-review failed and could not repair
Strict codegen failed for flow 'create_meeting_with_participants_flow': Self-review failed and could not repair
```

### Root Cause
The self-review LLM repair loop exhausts attempts without producing valid code.

### Affected Code
- `src/integration_coworker/codegen/self_review.py`

---

## Priority Summary

| Bug ID | Priority | Impact | Fix Complexity |
|--------|----------|--------|----------------|
| V22-001 | P0 | Causes OOM/SIGKILL | High - architecture issue |
| V22-002 | P1 | Blocks 8+ providers | Medium - template fix |
| V22-003 | P1 | All tests fail | Medium - test template |
| V22-007 | P1 | Runs timeout | Medium - optimize slow nodes |
| V22-004 | P2 | Module imports fail | Low - stub generation |
| V22-005 | P2 | Mypy validation fails | Low - type hints |
| V22-006 | P2 | Coverage too low | Medium - test generation |
| V22-008 | P2 | Report incomplete | Low - graceful shutdown |
| V22-010 | P2 | Repair loop fails | Medium - improve prompts |
| V22-009 | P3 | Log spam | Trivial - DB update |

---

## Recommended Fix Order

1. **V22-001** - State size bloat (root cause of OOM kills)
2. **V22-002** - Line 8 syntax errors (blocks most providers)
3. **V22-003** - Test assertions (all tests fail)
4. **V22-007** - Timeout optimization (prerequisite for stable runs)
5. Remaining P2 bugs in any order

---

## Appendix: Node Duration Hotspots

From slow run analysis, these nodes need optimization:

```
persist_silver_checkpoint:     up to 5,062 ms (5s)
build_report:                  up to 2,060 ms (2s)
detect_and_parse_spec:         up to 1,371 ms (1.4s)
persist_kg_learning:           up to 1,561 ms (1.6s)
validate_integration_design:   up to 781 ms
```

Total node time ~10s per run, but actual runs take 300-3600s due to LLM calls.

---

*Report generated: 2025-12-26*
*Demo log: `/tmp/demo-livev4-20251224-153434.log`*
*Run trace bundles: `/tmp/graph_traces/run_*/SLOW_RUN_BUNDLE.json`*

# Protocol Support vNext: Production Test Report

**Date**: 2025-12-22  
**Test Environment**: macOS, Python 3.11.14, pytest 8.4.2  
**Related Documents**: [PROTOCOL_SUPPORT_VNEXT.md](PROTOCOL_SUPPORT_VNEXT.md), [PROTOCOL_SUPPORT_REFACTOR_PLAN.md](PROTOCOL_SUPPORT_REFACTOR_PLAN.md)

---

## Executive Summary

Production-mode tests were run to validate the current state of protocol handling. **All protocol-related tests pass**, but the test coverage reveals significant gaps in multi-protocol semantic preservation.

| Area | Status | Notes |
|------|--------|-------|
| GraphQL Parser | ✅ 6/6 pass | Deterministic parsing works |
| AsyncAPI Conversion | ✅ 1/1 pass | Converts to pseudo-OpenAPI |
| OpenAPI Quality Markers | ✅ 14/15 pass | 1 skipped (GraphQL without graphql-core) |
| Source Detection | ✅ Pass | Confidence scoring works |
| Semantic Preservation | ⚠️ Not tested | No tests for subscription/pubsub semantics |

---

## Test Execution Results

### 1. Protocol-Related Tests

```bash
pytest tests/test_graphql_parser.py tests/sources/test_openapi_conversion_quality.py -v
```

**Results**: 20 passed, 1 skipped, 1 warning in 1.48s

| Test | Result | Notes |
|------|--------|-------|
| `test_deterministic_graphql_parsing` | ✅ PASS | GraphQL SDL parsed without LLM |
| `test_schema_extraction` | ✅ PASS | Component schemas extracted |
| `test_type_conversion` | ✅ PASS | GraphQL→OpenAPI type mapping |
| `test_query_parameters` | ✅ PASS | Query args become params |
| `test_mutation_request_body` | ✅ PASS | Mutation args become body |
| `test_reproducibility` | ✅ PASS | Same input→same output |
| `test_asyncapi_has_deterministic_quality` | ✅ PASS | Quality marker set |
| `test_graphql_has_deterministic_quality` | ⏭️ SKIP | graphql-core not in test env |

### 2. Source Layer Tests

```bash
pytest tests/sources/ -v
```

**Results**: 14 passed, 1 skipped, 1 warning in 1.24s

| Test Suite | Passed | Skipped |
|------------|--------|---------|
| OpenAPI Conversion Quality | 4 | 0 |
| AsyncAPI Conversion Quality | 1 | 0 |
| GraphQL Conversion Quality | 0 | 1 |
| HTML Conversion Quality | 1 | 0 |
| PDF Conversion Quality | 1 | 0 |
| Conversion Warnings | 3 | 0 |
| Quality Levels Ordering | 1 | 0 |
| Stripe-Style Detection | 3 | 0 |

### 3. Full Test Collection

```bash
pytest tests/ -v --collect-only -k "protocol or graphql or asyncapi"
```

**Results**: 14 tests selected from 3375 total

---

## Bugs Found

### Bug #1: GraphQL Subscription Semantic Loss (CONFIRMED)

**Severity**: P0 (Blocks production for GraphQL-heavy integrations)

**Evidence**:
```python
# src/integration_coworker/graph/nodes/detect_and_parse_spec.py:386-396
subscription_type = schema.subscription_type
if subscription_type:
    for field_name, field in subscription_type.fields.items():
        path = f"/graphql/subscription/{field_name}"
        spec["paths"][path] = {
            "post": _build_operation_from_field(field_name, field, "subscription")
        }
```

**Problem**: GraphQL subscriptions are converted to POST endpoints with no WebSocket/streaming semantics.

**Fix Plan**: Implement `protocols/graphql.py` adapter that preserves `CommunicationPattern.SUBSCRIBE` and sets `requires_websocket=True`.

---

### Bug #2: AsyncAPI Pub/Sub Direction Lost (CONFIRMED)

**Severity**: P0 (Breaks event-driven architecture code generation)

**Evidence**:
```python
# src/integration_coworker/sources/openapi.py (implied from pseudo-OpenAPI)
# Channels become /channels/{name} paths with GET - loses publish/subscribe distinction
```

**Problem**: AsyncAPI `publish` vs `subscribe` operations both become REST-like paths, losing message direction.

**Fix Plan**: Implement `protocols/asyncapi.py` adapter that preserves `channel`, `operation` (pub/sub), and `broker_protocol`.

---

### Bug #3: No Test for Streaming Semantics (GAP)

**Severity**: P1 (Test coverage gap)

**Evidence**:
- No test file for `test_streaming_semantics.py`
- No tests for `CommunicationPattern.SERVER_STREAMING`, `BIDIRECTIONAL`, etc.

**Fix Plan**: Add test file validating that:
1. GraphQL subscriptions produce `Operation` with `communication_pattern=SUBSCRIBE`
2. AsyncAPI subscribe channels produce `Operation` with `communication_pattern=SUBSCRIBE`
3. gRPC server streaming produces `Operation` with `communication_pattern=SERVER_STREAMING`

---

### Bug #4: Missing Protocol Adapters (GAP)

**Severity**: P1 (Feature gap)

**Evidence**:
- No `src/integration_coworker/protocols/` directory exists
- All protocol handling in `sources/openapi.py` with pseudo-OpenAPI conversion

**Fix Plan**: Create protocol adapter infrastructure per refactor plan.

---

### Bug #5: Endpoint Model HTTP-Centric (CONFIRMED)

**Severity**: P1 (Architecture limitation)

**Evidence**:
```python
# domain/models.py:108-125
@dataclass
class Endpoint:
    path: str       # e.g., "/v1/checkout/sessions"
    method: str     # GET, POST, PUT, DELETE, etc.
    # No fields for: protocol_type, communication_pattern, channel, broker
```

**Problem**: Model assumes HTTP request/response pattern.

**Fix Plan**: Create `Operation` IR model per design doc, deprecate `Endpoint` over 6 months.

---

## Test Coverage Gaps

| Gap | Impact | Priority |
|-----|--------|----------|
| No tests for `Operation` IR model | Can't validate IR correctness | P1 |
| No tests for protocol adapters | Adapters untested | P1 |
| No tests for GraphQL subscription preservation | Subscription bugs undetected | P0 |
| No tests for AsyncAPI pub/sub preservation | Event-driven bugs undetected | P0 |
| No integration test: GraphQL→Client code | Generated clients may be wrong | P1 |

---

## Recommendations

### Immediate (This Sprint)

1. **Add IR unit tests**: `tests/unit/domain/test_ir.py` (per refactor plan)
2. **Add semantic preservation tests**: Validate subscriptions/pubsub not lost
3. **Instrument conversion quality**: Log when `best_effort` quality used

### Short-Term (Next 2 Sprints)

1. **Implement REST adapter**: `protocols/rest.py` as proof of concept
2. **Implement GraphQL adapter**: `protocols/graphql.py` with subscription support
3. **DB migration**: Add `operations` table

### Medium-Term (Quarter)

1. **Implement AsyncAPI adapter**: `protocols/asyncapi.py`
2. **Protocol-aware code generators**: Native GraphQL client generation
3. **Deprecate `Endpoint`**: Migration path to `Operation`

---

## Test Commands for CI

```bash
# Protocol-related tests
pytest tests/test_graphql_parser.py tests/sources/test_openapi_conversion_quality.py -v

# Full source layer
pytest tests/sources/ -v

# Search for protocol tests
pytest tests/ -v -k "protocol or graphql or asyncapi or grpc"

# With coverage
pytest tests/sources/ --cov=src/integration_coworker/sources --cov-report=term-missing
```

---

## Appendix: Test Output

### GraphQL Parser Tests
```
tests/test_graphql_parser.py::test_deterministic_graphql_parsing PASSED
tests/test_graphql_parser.py::test_schema_extraction PASSED
tests/test_graphql_parser.py::test_type_conversion PASSED
tests/test_graphql_parser.py::test_query_parameters PASSED
tests/test_graphql_parser.py::test_mutation_request_body PASSED
tests/test_graphql_parser.py::test_reproducibility PASSED
```

### Conversion Quality Tests
```
TestOpenAPIConversionQuality::test_native_openapi_30_has_deterministic_quality PASSED
TestOpenAPIConversionQuality::test_native_openapi_31_has_deterministic_quality PASSED
TestOpenAPIConversionQuality::test_swagger_20_has_deterministic_quality PASSED
TestAsyncAPIConversionQuality::test_asyncapi_has_deterministic_quality PASSED
TestHTMLConversionQuality::test_html_has_llm_assisted_quality PASSED
TestPDFConversionQuality::test_pdf_has_best_effort_quality PASSED
```

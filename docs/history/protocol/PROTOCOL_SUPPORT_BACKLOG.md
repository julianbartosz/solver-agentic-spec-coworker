# Protocol Support vNext: Ranked Backlog

**Date**: 2025-12-22  
**Decision**: Option B (Protocol-Native IR with Adapter Plugins)  
**Related Documents**: [Design Doc](PROTOCOL_SUPPORT_VNEXT.md) | [Refactor Plan](PROTOCOL_SUPPORT_REFACTOR_PLAN.md) | [Test Report](PROTOCOL_SUPPORT_TEST_REPORT.md)

---

## Priority Legend

| Priority | Meaning | SLA |
|----------|---------|-----|
| **P0** | Blocks production, data loss, security | Fix immediately |
| **P1** | High ROI, major feature, important bug | This sprint |
| **P2** | Nice-to-have, polish, optimization | Next sprint |
| **P3** | Backlog, future ideas | When capacity |
| **🚫 Detrimental** | Avoid, negative ROI, technical debt | Do NOT implement |

---

## P0: Critical (Fix Immediately)

### P0-1: GraphQL Subscription Semantic Loss

**Problem**: GraphQL subscriptions converted to POST endpoints, losing WebSocket/streaming semantics.

**Impact**: 
- Generated clients won't work for subscription-based APIs
- AI cannot reason about real-time data flows
- Breaks GraphQL-first integrations (GitHub, Shopify, etc.)

**Fix**: Implement `protocols/graphql.py` adapter preserving subscription semantics.

**Effort**: 3 days  
**Owner**: TBD  
**Tracking**: Create issue `PROT-001`

---

### P0-2: AsyncAPI Pub/Sub Direction Lost

**Problem**: AsyncAPI `publish` vs `subscribe` both become REST paths.

**Impact**:
- Cannot generate correct event producers vs consumers
- Message broker semantics (Kafka, RabbitMQ) lost
- Event-driven architecture code generation broken

**Fix**: Implement `protocols/asyncapi.py` adapter preserving channel direction.

**Effort**: 3 days  
**Owner**: TBD  
**Tracking**: Create issue `PROT-002`

---

### P0-3: Create IR Foundation

**Problem**: No protocol-agnostic data model exists.

**Impact**:
- Cannot implement adapters without IR
- Endpoint model too rigid for multi-protocol
- Blocks all other protocol work

**Fix**: Create `domain/ir.py` with `Operation`, `ProtocolType`, `CommunicationPattern`.

**Effort**: 2 days  
**Owner**: TBD  
**Tracking**: Create issue `PROT-003`

---

## P1: High ROI (This Sprint)

### P1-1: REST Adapter (Proof of Concept)

**Problem**: No adapter exists; all logic in `sources/openapi.py`.

**Impact**:
- Cannot validate adapter pattern works
- Blocks GraphQL/AsyncAPI adapters

**Fix**: Create `protocols/rest.py` converting existing Endpoint→Operation.

**Effort**: 2 days  
**Dependencies**: P0-3  
**Tracking**: Create issue `PROT-004`

---

### P1-2: Add IR Unit Tests

**Problem**: No tests for Operation model or adapters.

**Impact**:
- Regressions undetected
- Cannot validate correctness

**Fix**: Create `tests/unit/domain/test_ir.py` per refactor plan.

**Effort**: 1 day  
**Dependencies**: P0-3  
**Tracking**: Create issue `PROT-005`

---

### P1-3: Add Semantic Preservation Tests

**Problem**: No tests validate subscription/pubsub semantics preserved.

**Impact**:
- P0 bugs went undetected
- No regression protection

**Fix**: Create `tests/integration/test_protocol_semantics.py`:
- GraphQL subscriptions → `CommunicationPattern.SUBSCRIBE`
- AsyncAPI subscribe → `CommunicationPattern.SUBSCRIBE`
- AsyncAPI publish → `CommunicationPattern.PUBLISH`

**Effort**: 1 day  
**Dependencies**: P0-1, P0-2  
**Tracking**: Create issue `PROT-006`

---

### P1-4: DB Migration for Operations Table

**Problem**: No persistence for `Operation` model.

**Impact**:
- Cannot cache/persist protocol-agnostic operations
- Breaks Silver→Gold pipeline for non-REST

**Fix**: Create `migrations/002_add_operations_table.sql` per refactor plan.

**Effort**: 1 day  
**Dependencies**: P0-3  
**Tracking**: Create issue `PROT-007`

---

### P1-5: Dual Output in build_silver_api_model

**Problem**: Only emits `Endpoint`, not `Operation`.

**Impact**:
- New IR model unused
- Breaks migration path

**Fix**: Modify `build_silver_api_model.py` to emit both `state.endpoints` (legacy) and `state.operations` (new).

**Effort**: 1 day  
**Dependencies**: P0-3, P1-1  
**Tracking**: Create issue `PROT-008`

---

## P2: Nice-to-Have (Next Sprint)

### P2-1: GraphQL-Native Code Generator

**Problem**: Generated GraphQL clients are REST-like.

**Impact**:
- Poor developer experience
- Manual fixes required for subscriptions

**Fix**: Create `codegen/generators/graphql.py` producing native GraphQL client code.

**Effort**: 3 days  
**Dependencies**: P0-1, P1-2  
**Tracking**: Create issue `PROT-009`

---

### P2-2: AsyncAPI Event Consumer Generator

**Problem**: No code generator for pub/sub patterns.

**Impact**:
- Cannot generate Kafka/RabbitMQ consumers
- Event-driven architecture manual

**Fix**: Create `codegen/generators/asyncapi.py`.

**Effort**: 3 days  
**Dependencies**: P0-2  
**Tracking**: Create issue `PROT-010`

---

### P2-3: Protocol Detection Confidence Tuning

**Problem**: GraphQL/AsyncAPI detection uses heuristics.

**Impact**:
- False positives possible
- Edge cases misclassified

**Fix**: Add configurable confidence thresholds per protocol.

**Effort**: 1 day  
**Tracking**: Create issue `PROT-011`

---

### P2-4: Conversion Quality Dashboard

**Problem**: No visibility into conversion quality at runtime.

**Impact**:
- Operators unaware of semantic loss
- Hard to debug LLM fallbacks

**Fix**: Add metrics endpoint showing `conversion_quality` distribution.

**Effort**: 2 days  
**Tracking**: Create issue `PROT-012`

---

### P2-5: Deprecation Warnings for Endpoint

**Problem**: Migration path unclear.

**Impact**:
- Breaking change surprises users

**Fix**: Add deprecation warnings to `Endpoint.__init__` per refactor plan.

**Effort**: 0.5 days  
**Dependencies**: P0-3  
**Tracking**: Create issue `PROT-013`

---

## P3: Backlog (Future)

### P3-1: gRPC Support

**Problem**: gRPC/Protobuf not supported.

**Impact**: Cannot integrate gRPC services.

**Fix**: Implement `protocols/grpc.py` adapter.

**Effort**: 1 week  
**Tracking**: Create issue `PROT-014`

---

### P3-2: SOAP/WSDL Support

**Problem**: Legacy SOAP services unsupported.

**Impact**: Enterprise integrations blocked.

**Fix**: Implement `protocols/soap.py` adapter.

**Effort**: 2 weeks  
**Tracking**: Create issue `PROT-015`

---

### P3-3: WebSocket Native Support

**Problem**: No first-class WebSocket protocol support.

**Impact**: Real-time APIs like Slack Events API limited.

**Fix**: Implement `protocols/websocket.py` adapter.

**Effort**: 1 week  
**Tracking**: Create issue `PROT-016`

---

### P3-4: Protocol Auto-Detection Improvement

**Problem**: Manual protocol hints sometimes needed.

**Impact**: UX friction.

**Fix**: ML-based protocol classifier.

**Effort**: 2 weeks  
**Tracking**: Create issue `PROT-017`

---

## 🚫 Detrimental (Do NOT Implement)

### 🚫 D-1: Extend Endpoint with Union Fields

**Why Detrimental**:
- Creates nullable field sprawl (N protocols × M fields)
- Business logic scattered with `if protocol ==` checks
- Doesn't scale to new protocols
- Scored 2.15/5 in decision rubric (lowest)

**Alternative**: Use Option B (Protocol-Native IR).

---

### 🚫 D-2: Full REST Transcoding with x-extensions

**Why Detrimental**:
- Doesn't solve semantic loss (REST model can't represent streaming)
- `x-*` extensions are unofficial, tooling varies
- Code generation becomes heuristic-heavy
- Scored 2.65/5 in decision rubric

**Alternative**: Use Option B (Protocol-Native IR).

---

### 🚫 D-3: Remove Endpoint Before Migration Complete

**Why Detrimental**:
- Breaks all existing workflows immediately
- No backward compatibility
- Forces big-bang migration

**Alternative**: 6-month deprecation period with dual output.

---

### 🚫 D-4: Protocol-Specific Code in Prompts

**Why Detrimental**:
- Prompt spaghetti ("if protocol is graphql, add this context...")
- Hard to test
- Prompts become unreadable

**Alternative**: Protocol-aware code generators with clean separation.

---

## Sprint Planning Summary

### Sprint N (Current)

| Item | Effort | Owner |
|------|--------|-------|
| P0-3: Create IR Foundation | 2d | TBD |
| P0-1: GraphQL Adapter | 3d | TBD |
| P0-2: AsyncAPI Adapter | 3d | TBD |
| P1-1: REST Adapter | 2d | TBD |
| **Total** | **10d** | |

### Sprint N+1

| Item | Effort | Owner |
|------|--------|-------|
| P1-2: IR Unit Tests | 1d | TBD |
| P1-3: Semantic Tests | 1d | TBD |
| P1-4: DB Migration | 1d | TBD |
| P1-5: Dual Output | 1d | TBD |
| P2-1: GraphQL Generator | 3d | TBD |
| **Total** | **7d** | |

### Sprint N+2

| Item | Effort | Owner |
|------|--------|-------|
| P2-2: AsyncAPI Generator | 3d | TBD |
| P2-3: Detection Tuning | 1d | TBD |
| P2-4: Quality Dashboard | 2d | TBD |
| P2-5: Deprecation Warnings | 0.5d | TBD |
| **Total** | **6.5d** | |

---

## Metrics to Track

| Metric | Target | Current |
|--------|--------|---------|
| Protocol tests passing | 100% | 95% (1 skip) |
| GraphQL subscription preserved | Yes | No |
| AsyncAPI pub/sub preserved | Yes | No |
| Operation model adoption | 100% | 0% |
| Endpoint deprecation warnings | Enabled | Not yet |
| gRPC support | Partial | None |

---

## Stakeholder Sign-Off

| Stakeholder | Approval | Date |
|-------------|----------|------|
| Tech Lead | ⬜ | |
| Product | ⬜ | |
| QA | ⬜ | |

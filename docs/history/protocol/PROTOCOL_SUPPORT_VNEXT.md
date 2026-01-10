# Protocol Support vNext Design Document

**Status**: Draft  
**Author**: AI Assistant  
**Date**: 2025-12-22  
**ADR Ref**: Will produce ADR-0011-multi-protocol-architecture.md upon decision

---

## Executive Summary

The Integration Coworker system is currently HTTP/OpenAPI-centric. GraphQL and AsyncAPI specs are converted to "pseudo-OpenAPI" representations that lose native semantics (subscriptions, pub/sub channels, streaming). gRPC, SOAP, and WebSocket protocols are entirely unsupported.

This document analyzes the current state, debates three architecture options, selects the recommended approach, and provides a concrete implementation plan.

**Decision**: Adopt **Option B: Protocol-Native IR with Adapter Plugins** with staged migration.

---

## CRITICAL CORRECTION (2025-12-22)

### Native Parsing vs. Lossy Conversion

The Phase 1 implementation **MUST** preserve native semantics by parsing from the original spec format:

| Protocol | WRONG (lossy) | CORRECT (native) |
|----------|---------------|------------------|
| GraphQL | Extract from pseudo-OpenAPI `/graphql/subscription/*` | Parse SDL directly via `graphql-core`, preserve `subscription_type` |
| AsyncAPI | Extract from LLM-converted pseudo-OpenAPI | Parse YAML/JSON channels directly, preserve `publish`/`subscribe` operations |

**Why this matters**:
- GraphQL subscriptions need **WebSocket transport** metadata (protocol: `graphql-transport-ws`)
- AsyncAPI pub/sub direction is **semantic** - converting to GET/POST loses the intent
- The IR must capture `CommunicationPattern.SUBSCRIBE` vs `CommunicationPattern.PUBLISH`

**Implementation constraint**: Adapters in `protocols/graphql.py` and `protocols/asyncapi.py` MUST call the native parsers (`graphql-core`, direct YAML parsing) — NOT extract from already-converted pseudo-OpenAPI.

### Reality Check Findings

Per repo audit on 2025-12-22:
1. **No Alembic** — repo uses `CREATE TABLE IF NOT EXISTS` pattern (inline DDL)
2. **No `spec_silver.*` persistence** — Silver entities are in-memory dataclasses only
3. **Conclusion**: Phase 1 requires NO database migrations

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Current State Analysis](#2-current-state-analysis)
3. [Protocol Taxonomy](#3-protocol-taxonomy)
4. [Architecture Options](#4-architecture-options)
5. [Decision Rubric](#5-decision-rubric)
6. [Recommendation](#6-recommendation)
7. [Implementation Plan](#7-implementation-plan)
8. [Migration Strategy](#8-migration-strategy)
9. [Test Strategy](#9-test-strategy)
10. [Risk Analysis](#10-risk-analysis)

---

## 1. Problem Statement

### 1.1 Core Issue

The system's data model is **HTTP-request/response centric**:

```python
# domain/models.py - Current Endpoint model
@dataclass
class Endpoint:
    path: str       # HTTP path like "/v1/users/{id}"
    method: str     # HTTP method: GET, POST, PUT, DELETE
    ...
```

This model **cannot represent**:

| Protocol | Semantic | Current Behavior | Loss |
|----------|----------|------------------|------|
| GraphQL | Single `/graphql` endpoint with operations | Exploded to `/graphql/query/{op}` paths | Query batching, introspection |
| GraphQL | Subscriptions (WebSocket streams) | Converted to POST endpoints | Real-time semantics |
| AsyncAPI | Publish/Subscribe channels | Channels → `/channels/{name}` GET | Direction semantics |
| AsyncAPI | Multiple message brokers | Lost entirely | Broker metadata |
| gRPC | Streaming (unary/client/server/bidirectional) | Unsupported | All semantics |
| SOAP/WSDL | Operations with WS-* standards | Unsupported | All semantics |
| WebSocket | Bidirectional message frames | Unsupported | All semantics |

### 1.2 Impact

1. **Code Generation Quality**: Generated clients for GraphQL are REST-like, missing native SDK patterns
2. **Semantic Fidelity**: AI cannot reason about subscriptions, streaming, or event-driven patterns
3. **Protocol Evolution**: Adding new protocols requires invasive changes to core models
4. **Test Coverage**: No tests for protocol-specific behaviors (streaming, reconnection, etc.)

### 1.3 Evidence from Codebase

**detect_and_parse_spec.py** (lines 386-396):
```python
# Extract Subscription operations (POST with streaming hint)
subscription_type = schema.subscription_type
if subscription_type:
    for field_name, field in subscription_type.fields.items():
        path = f"/graphql/subscription/{field_name}"
        spec["paths"][path] = {
            "post": _build_operation_from_field(field_name, field, "subscription")
        }
```
→ GraphQL subscriptions become POST endpoints, losing streaming semantics entirely.

**openapi.py** `_asyncapi_to_pseudo_openapi()`:
```python
# AsyncAPI channels become REST paths
for channel_name, channel in channels.items():
    path = f"/channels/{channel_name}"
    # Loses: publish vs subscribe distinction, broker binding info
```

---

## 2. Current State Analysis

### 2.1 Source Plugin Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    SpecSource Protocol                          │
│  sources/base.py                                                │
│  ┌──────────────────┐    ┌────────────────────────────────┐    │
│  │ detect(content)  │ →  │ Returns confidence 0.0-1.0    │    │
│  │ parse(content)   │ →  │ Returns ParsedSpec             │    │
│  └──────────────────┘    └────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    OpenAPISource (Primary)                      │
│  sources/openapi.py                                             │
│  - Handles OpenAPI 2.0/3.0/3.1                                  │
│  - Handles Swagger 2.0                                          │
│  - Converts GraphQL → pseudo-OpenAPI                           │
│  - Converts AsyncAPI → pseudo-OpenAPI                           │
│  - Converts HTML/PDF → pseudo-OpenAPI (via LLM)                 │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│              build_silver_api_model.py                          │
│  - Extracts: Endpoint, EndpointParameter, Schema, Entity       │
│  - ALL paths assumed to be HTTP request/response                │
│  - No concept of streams, channels, or bidirectional comms     │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                    codegen/prompts.py                           │
│  - Generates HTTP client code (httpx/fetch/axios)               │
│  - Assumes request/response pattern                             │
│  - No support for streaming, subscriptions, WebSocket           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Key Data Structures

**SourceType Enum** (base.py):
```python
class SourceType(Enum):
    API = "api"       # OpenAPI, Swagger, GraphQL, etc.
    FILE = "file"     # CSV, Excel, fixed-width
    MESSAGE = "message"  # Future: Avro, Protobuf (UNUSED)
    UNKNOWN = "unknown"
```
→ `MESSAGE` type exists but is **never used** - this is a hook for future protocol support.

**ParsedSpec** (base.py):
```python
@dataclass
class ParsedSpec:
    source_type: SourceType
    source_uri: str
    data: dict        # Always OpenAPI-like structure
    metadata: dict    # _parsed_from, _conversion_quality
    errors: list
    warnings: list
    confidence: float
```
→ `data` is always coerced to OpenAPI-like dict, even for non-OpenAPI sources.

**Endpoint** (domain/models.py):
```python
@dataclass
class Endpoint:
    id: Optional[int]
    source_system_id: Optional[int]
    spec_document_id: Optional[int]
    path: str           # e.g., "/v1/checkout/sessions"
    method: str         # GET, POST, PUT, DELETE, etc.
    operation_id: Optional[str]
    summary: Optional[str]
    description: Optional[str]
    request_schema_id: Optional[int]
    response_schema_id: Optional[int]
    auth_required: bool = True
    pagination_style: Optional[str] = None
    rate_limit_bucket: Optional[str] = None
```
→ Purely HTTP-centric. No fields for:
- Protocol type (REST, GraphQL, gRPC, etc.)
- Communication pattern (unary, streaming, bidirectional)
- Message broker / channel info
- Subscription semantics

### 2.3 Conversion Quality Tracking

The system does track conversion fidelity via `_conversion_quality`:

| Value | Meaning | Usage |
|-------|---------|-------|
| `"deterministic"` | Native OpenAPI, no transformation | OpenAPI specs |
| `"llm_assisted"` | LLM used for extraction | HTML, PDF sources |
| `"best_effort"` | Significant semantic loss | GraphQL, AsyncAPI |

This is a good foundation but insufficient for protocol-aware code generation.

---

## 3. Protocol Taxonomy

### 3.1 Protocol Categories

| Category | Protocols | Communication Pattern | Key Semantics |
|----------|-----------|----------------------|---------------|
| **REST-like** | OpenAPI, Swagger | Request/Response | HTTP methods, status codes, headers |
| **RPC** | gRPC, JSON-RPC, XML-RPC | Request/Response (may stream) | Procedures, streaming modes |
| **Event-Driven** | AsyncAPI, CloudEvents | Pub/Sub | Channels, messages, brokers |
| **Query** | GraphQL | Single endpoint, typed queries | Operations, types, subscriptions |
| **Legacy** | SOAP/WSDL | Request/Response | WS-* standards, WSDL operations |
| **Realtime** | WebSocket, SSE | Bidirectional/Server-push | Frames, events, reconnection |

### 3.2 Semantic Dimensions

Each protocol has unique semantics that must be preserved:

```
┌───────────────────────────────────────────────────────────────────┐
│ Dimension          │ REST │ GraphQL │ gRPC │ AsyncAPI │ WebSocket │
├───────────────────────────────────────────────────────────────────┤
│ Request/Response   │ ✓    │ ✓       │ ✓    │ ✗        │ △         │
│ Server Streaming   │ ✗    │ ✓ (sub) │ ✓    │ ✓        │ ✓         │
│ Client Streaming   │ ✗    │ ✗       │ ✓    │ ✓        │ ✓         │
│ Bidirectional      │ ✗    │ ✗       │ ✓    │ ✓        │ ✓         │
│ Typed Schema       │ ✓    │ ✓✓      │ ✓✓   │ ✓        │ △         │
│ Introspection      │ △    │ ✓       │ ✓    │ ✗        │ ✗         │
│ Broker Metadata    │ ✗    │ ✗       │ ✗    │ ✓        │ ✗         │
└───────────────────────────────────────────────────────────────────┘
Legend: ✓ = native support, △ = partial, ✗ = not applicable
```

---

## 4. Architecture Options

### Option A: Extend Endpoint with Union Fields (Minimal Change)

**Concept**: Keep `Endpoint` as the universal entity, add optional protocol-specific fields.

```python
@dataclass
class Endpoint:
    # Existing fields...
    path: str
    method: str
    
    # NEW: Protocol discriminator
    protocol_type: str = "rest"  # "rest", "graphql", "grpc", "asyncapi", "websocket"
    
    # NEW: GraphQL-specific
    graphql_operation_type: Optional[str] = None  # "query", "mutation", "subscription"
    graphql_field_name: Optional[str] = None
    
    # NEW: gRPC-specific  
    grpc_service: Optional[str] = None
    grpc_method: Optional[str] = None
    grpc_streaming_mode: Optional[str] = None  # "unary", "server", "client", "bidi"
    
    # NEW: AsyncAPI-specific
    channel_name: Optional[str] = None
    channel_operation: Optional[str] = None  # "publish", "subscribe"
    broker_protocol: Optional[str] = None  # "kafka", "amqp", "mqtt"
    
    # NEW: WebSocket-specific
    ws_message_types: Optional[list] = None
```

**Pros**:
- Minimal migration (additive changes only)
- DB schema easy to extend (add nullable columns)
- Existing code continues to work

**Cons**:
- Model becomes bloated with nullable fields
- Business logic scattered with `if protocol_type == "graphql"` checks
- Doesn't scale: each new protocol adds more fields
- Code generation prompts become protocol-switch spaghetti

**Estimated Effort**: 2 weeks  
**Long-term Maintainability**: ⭐⭐ (Poor)

---

### Option B: Protocol-Native IR with Adapter Plugins (Recommended)

**Concept**: Introduce a Protocol-agnostic Intermediate Representation (IR), with protocol-specific adapters that convert to/from IR.

```
┌────────────────────────────────────────────────────────────────────┐
│                      Protocol Sources                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │ OpenAPI  │ │ GraphQL  │ │ gRPC     │ │AsyncAPI  │ │WebSocket │ │
│  │ Source   │ │ Source   │ │ Source   │ │ Source   │ │ Source   │ │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘ │
│       │            │            │            │            │        │
│       ▼            ▼            ▼            ▼            ▼        │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │               Protocol Adapter Layer                        │   │
│  │  Converts each protocol's AST to unified IR                 │   │
│  └────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│                    Unified IR (domain/ir.py)                       │
│                                                                    │
│  @dataclass                                                        │
│  class Operation:                                                  │
│      """Protocol-agnostic operation representation."""             │
│      id: Optional[int]                                             │
│      name: str                                                     │
│      protocol: ProtocolType  # REST, GRAPHQL, GRPC, ASYNCAPI, WS   │
│      communication_pattern: CommunicationPattern  # UNARY, STREAM..│
│      input_schema: Optional[Schema]                                │
│      output_schema: Optional[Schema]                               │
│      metadata: ProtocolMetadata  # Protocol-specific (discriminated)│
│                                                                    │
│  @dataclass                                                        │
│  class RestMetadata(ProtocolMetadata):                             │
│      path: str                                                     │
│      method: str                                                   │
│      headers: Dict[str, str]                                       │
│                                                                    │
│  @dataclass                                                        │
│  class GraphQLMetadata(ProtocolMetadata):                          │
│      operation_type: str  # query, mutation, subscription          │
│      field_path: str                                               │
│      variables: Dict[str, TypeRef]                                 │
│                                                                    │
│  @dataclass                                                        │
│  class GrpcMetadata(ProtocolMetadata):                             │
│      service: str                                                  │
│      method: str                                                   │
│      streaming_mode: StreamingMode                                 │
│                                                                    │
│  @dataclass                                                        │
│  class AsyncAPIMetadata(ProtocolMetadata):                         │
│      channel: str                                                  │
│      operation: str  # publish, subscribe                          │
│      broker: BrokerConfig                                          │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│                    Code Generation Layer                           │
│                                                                    │
│  ┌──────────────────────────────────────────────────────────────┐ │
│  │ Protocol-Aware Code Generators                               │ │
│  │ - RESTClientGenerator (existing behavior)                    │ │
│  │ - GraphQLClientGenerator (native queries, subscriptions)     │ │
│  │ - GrpcClientGenerator (native stubs)                         │ │
│  │ - AsyncAPIConsumerGenerator (pub/sub handlers)               │ │
│  └──────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────┘
```

**New Models** (domain/ir.py):

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional, Dict, Any, Union

class ProtocolType(str, Enum):
    REST = "rest"
    GRAPHQL = "graphql"
    GRPC = "grpc"
    ASYNCAPI = "asyncapi"
    WEBSOCKET = "websocket"
    SOAP = "soap"

class CommunicationPattern(str, Enum):
    UNARY = "unary"              # Request → Response
    SERVER_STREAMING = "server"  # Request → Stream of Responses
    CLIENT_STREAMING = "client"  # Stream of Requests → Response
    BIDIRECTIONAL = "bidi"       # Stream ↔ Stream
    PUBLISH = "publish"          # Fire and forget
    SUBSCRIBE = "subscribe"      # Receive events

@dataclass
class ProtocolMetadata:
    """Base class for protocol-specific metadata."""
    pass

@dataclass
class RestMetadata(ProtocolMetadata):
    path: str
    method: str
    headers: Dict[str, str] = None
    query_params: Dict[str, Any] = None

@dataclass
class GraphQLMetadata(ProtocolMetadata):
    operation_type: str  # "query", "mutation", "subscription"
    field_name: str
    field_path: str = None  # For nested selections
    variables: Dict[str, Any] = None

@dataclass
class GrpcMetadata(ProtocolMetadata):
    package: str
    service: str
    method: str
    streaming_mode: CommunicationPattern = CommunicationPattern.UNARY

@dataclass 
class AsyncAPIMetadata(ProtocolMetadata):
    channel: str
    operation: str  # "publish" or "subscribe"
    broker_protocol: str = None  # "kafka", "amqp", "mqtt", etc.
    message_name: str = None

@dataclass
class Operation:
    """
    Protocol-agnostic operation representation.
    
    This is the core IR entity that replaces Endpoint as the
    universal representation of an API operation.
    """
    id: Optional[int]
    name: str
    summary: Optional[str]
    description: Optional[str]
    protocol: ProtocolType
    communication_pattern: CommunicationPattern
    input_schema_ref: Optional[str] = None
    output_schema_ref: Optional[str] = None
    auth_required: bool = True
    metadata: Union[RestMetadata, GraphQLMetadata, GrpcMetadata, AsyncAPIMetadata] = None
    
    # Traceability (from API-002)
    source_uri: Optional[str] = None
    source_system_id: Optional[int] = None
    spec_document_id: Optional[int] = None
```

**Pros**:
- Clean separation of concerns
- Protocol-specific logic isolated in adapters
- New protocols require new adapter, not core model changes
- Code generation can be protocol-aware (native GraphQL clients, gRPC stubs)
- Scales indefinitely

**Cons**:
- Larger initial investment
- Migration complexity (Endpoint → Operation)
- DB schema migration required

**Estimated Effort**: 4-6 weeks  
**Long-term Maintainability**: ⭐⭐⭐⭐⭐ (Excellent)

---

### Option C: Full Transcoding to REST (Status Quo+)

**Concept**: Keep everything REST-based, but improve the transcoding fidelity with richer annotations.

```python
# Enhanced pseudo-OpenAPI output
{
    "paths": {
        "/graphql/subscription/onMessage": {
            "post": {
                "x-original-protocol": "graphql",
                "x-operation-type": "subscription",
                "x-requires-websocket": true,
                "x-stream-response": true,
                ...
            }
        }
    }
}
```

Code generation reads `x-*` extensions and generates appropriate code.

**Pros**:
- Minimal model changes
- Leverages existing OpenAPI tooling
- Progressive enhancement

**Cons**:
- Doesn't solve the core problem (REST model can't represent non-REST semantics)
- `x-*` extensions are unofficial, tooling varies
- Code generation becomes heuristic-heavy ("if x-requires-websocket, generate WS client")
- Still loses introspection, broker metadata, etc.

**Estimated Effort**: 2-3 weeks  
**Long-term Maintainability**: ⭐⭐⭐ (Medium)

---

## 5. Decision Rubric

| Criterion | Weight | Option A (Union Fields) | Option B (IR + Adapters) | Option C (Transcoding) |
|-----------|--------|-------------------------|--------------------------|------------------------|
| **Semantic Fidelity** | 25% | 2/5 (fields exist but scattered) | 5/5 (native per protocol) | 2/5 (annotations lossy) |
| **Code Gen Quality** | 25% | 2/5 (still HTTP-centric prompts) | 5/5 (protocol-native generators) | 3/5 (heuristic-based) |
| **Migration Cost** | 15% | 4/5 (additive changes) | 2/5 (larger refactor) | 4/5 (minimal changes) |
| **Scalability** | 20% | 1/5 (N protocols = N*M fields) | 5/5 (O(N) adapters) | 2/5 (annotations accumulate) |
| **Maintainability** | 15% | 2/5 (nullable field sprawl) | 5/5 (clean separation) | 3/5 (x-extension sprawl) |
| **Weighted Score** | 100% | **2.15/5** | **4.45/5** | **2.65/5** |

**Winner: Option B (Protocol-Native IR with Adapter Plugins)**

---

## 6. Recommendation

**Adopt Option B: Protocol-Native IR with Adapter Plugins** with the following staged rollout:

### Phase 1: Foundation (Weeks 1-2)
- Create `domain/ir.py` with `Operation`, `ProtocolType`, `CommunicationPattern`, and metadata classes
- Create adapter interface `protocols/base.py`
- Implement `protocols/rest.py` adapter that converts existing `Endpoint` to `Operation`

### Phase 2: GraphQL Native (Weeks 3-4)
- Implement `protocols/graphql.py` adapter
- Preserve subscription semantics (don't convert to POST)
- Add GraphQL-specific code generator

### Phase 3: AsyncAPI Native (Week 5)
- Implement `protocols/asyncapi.py` adapter
- Preserve pub/sub channel semantics
- Add event-driven code generator

### Phase 4: gRPC Support (Week 6+)
- Implement `protocols/grpc.py` adapter
- Preserve streaming modes
- Add gRPC stub generator

---

## 7. Implementation Plan

### 7.1 File-by-File Changes

See separate document: **PROTOCOL_SUPPORT_REFACTOR_PLAN.md**

### 7.2 New Files

| File | Purpose |
|------|---------|
| `src/integration_coworker/domain/ir.py` | Protocol-agnostic IR models |
| `src/integration_coworker/protocols/__init__.py` | Protocol adapter package |
| `src/integration_coworker/protocols/base.py` | Adapter protocol/interface |
| `src/integration_coworker/protocols/rest.py` | REST/OpenAPI adapter |
| `src/integration_coworker/protocols/graphql.py` | GraphQL adapter |
| `src/integration_coworker/protocols/asyncapi.py` | AsyncAPI adapter |
| `src/integration_coworker/protocols/grpc.py` | gRPC adapter (future) |
| `src/integration_coworker/codegen/generators/__init__.py` | Protocol-aware generators |
| `src/integration_coworker/codegen/generators/rest.py` | REST client generator |
| `src/integration_coworker/codegen/generators/graphql.py` | GraphQL client generator |
| `migrations/002_add_operations_table.sql` | DB migration for Operation |

### 7.3 Modified Files

| File | Changes |
|------|---------|
| `domain/models.py` | Add `protocol_type` field to `Endpoint` (deprecated), import IR types |
| `sources/base.py` | Add `protocol_hint` to `ParsedSpec` |
| `sources/openapi.py` | Update to emit protocol metadata |
| `graph/nodes/detect_and_parse_spec.py` | Route to protocol-specific adapters |
| `graph/nodes/build_silver_api_model.py` | Emit `Operation` instead of (or alongside) `Endpoint` |
| `codegen/prompts.py` | Protocol-aware prompt builders |
| `codegen/context.py` | Add protocol to `CodegenContext` |

---

## 8. Migration Strategy

### 8.1 Backward Compatibility

**Requirement**: Existing REST/OpenAPI workflows must continue to work unchanged.

**Strategy**:
1. `Endpoint` remains in `domain/models.py` (marked deprecated for v2.0 removal)
2. `build_silver_api_model.py` emits BOTH `Endpoint` (for compatibility) and `Operation` (for new code)
3. Code generators check for `Operation` first, fall back to `Endpoint`
4. After 6-month deprecation period, remove `Endpoint`

### 8.2 Database Migration

```sql
-- migrations/002_add_operations_table.sql

-- New operations table (Silver layer)
CREATE TABLE spec_silver.operations (
    id SERIAL PRIMARY KEY,
    source_system_id INTEGER REFERENCES spec_silver.source_systems(id),
    spec_document_id INTEGER REFERENCES spec_silver.spec_documents(id),
    name VARCHAR(255) NOT NULL,
    summary TEXT,
    description TEXT,
    protocol VARCHAR(50) NOT NULL DEFAULT 'rest',
    communication_pattern VARCHAR(50) NOT NULL DEFAULT 'unary',
    input_schema_ref VARCHAR(255),
    output_schema_ref VARCHAR(255),
    auth_required BOOLEAN DEFAULT TRUE,
    metadata JSONB,  -- Protocol-specific metadata
    source_uri VARCHAR(2048),
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_operations_protocol ON spec_silver.operations(protocol);
CREATE INDEX idx_operations_source_system ON spec_silver.operations(source_system_id);

-- Migrate existing endpoints to operations
INSERT INTO spec_silver.operations (
    source_system_id, spec_document_id, name, summary, description,
    protocol, communication_pattern, input_schema_ref, output_schema_ref,
    auth_required, metadata, source_uri
)
SELECT 
    source_system_id, spec_document_id, operation_id, summary, description,
    'rest', 'unary', 
    CASE WHEN request_schema_id IS NOT NULL THEN '#/components/schemas/' || request_schema_id END,
    CASE WHEN response_schema_id IS NOT NULL THEN '#/components/schemas/' || response_schema_id END,
    auth_required,
    jsonb_build_object('path', path, 'method', method),
    NULL
FROM spec_silver.endpoints;
```

### 8.3 Feature Flags

```python
# config.py
FEATURE_FLAGS = {
    "use_operation_model": False,  # Default off, flip per-tenant
    "graphql_native_codegen": False,
    "asyncapi_native_codegen": False,
}
```

---

## 9. Test Strategy

### 9.1 Unit Tests

| Test File | Coverage |
|-----------|----------|
| `tests/unit/domain/test_ir.py` | IR model validation, serialization |
| `tests/unit/protocols/test_rest_adapter.py` | REST → Operation conversion |
| `tests/unit/protocols/test_graphql_adapter.py` | GraphQL → Operation conversion |
| `tests/unit/protocols/test_asyncapi_adapter.py` | AsyncAPI → Operation conversion |

### 9.2 Integration Tests

| Test | Validates |
|------|-----------|
| `test_graphql_subscription_preserved` | GraphQL subscription semantics not lost |
| `test_asyncapi_pubsub_preserved` | Pub/sub direction preserved |
| `test_endpoint_to_operation_migration` | Existing Endpoints migrate correctly |
| `test_codegen_protocol_dispatch` | Correct generator invoked per protocol |

### 9.3 E2E Tests

| Test | Validates |
|------|-----------|
| `test_e2e_graphql_spec_to_client` | Full pipeline: GraphQL SDL → Native client code |
| `test_e2e_asyncapi_spec_to_consumer` | Full pipeline: AsyncAPI → Event consumer code |
| `test_e2e_backward_compat` | Existing OpenAPI specs produce identical output |

---

## 10. Risk Analysis

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Migration breaks existing workflows | Medium | High | Feature flags, parallel models, extensive tests |
| Performance regression from IR layer | Low | Medium | IR is thin wrapper, benchmark before/after |
| GraphQL subscription codegen quality | Medium | Medium | Start with query/mutation only, subscriptions v2 |
| DB migration fails on large datasets | Low | High | Test on prod-size dataset copy first |
| Scope creep (add all protocols immediately) | High | Medium | Strict phasing: REST → GraphQL → AsyncAPI |

---

## Appendices

### A. Related Documents

- [ADR-0006: Medallion Data Architecture](decisions/adr-0006-medallion-data-architecture.md)
- [ADR-0003: Template-First Code Generation](decisions/adr-0003-template-first-code-generation.md)
- [FILE_INTEGRATION_V1_PLAN.md](FILE_INTEGRATION_V1_PLAN.md) - Similar pattern for file vs API specs

### B. References

- [OpenAPI Specification 3.1](https://spec.openapis.org/oas/v3.1.0)
- [GraphQL Specification](https://spec.graphql.org/)
- [AsyncAPI Specification](https://www.asyncapi.com/docs/reference/specification/v2.6.0)
- [gRPC Concepts](https://grpc.io/docs/what-is-grpc/core-concepts/)

### C. Glossary

| Term | Definition |
|------|------------|
| **IR** | Intermediate Representation - protocol-agnostic data model |
| **Adapter** | Component that converts protocol-specific AST to IR |
| **Pseudo-OpenAPI** | OpenAPI-like structure derived from non-OpenAPI source |
| **Semantic Loss** | Information lost during protocol conversion |
| **Communication Pattern** | How messages flow (unary, streaming, pub/sub) |

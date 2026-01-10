# ADR-0005: Policy Injection Strategy — Current State and Target Architecture

| Metadata       | Value                                                  |
|----------------|--------------------------------------------------------|
| **Status**     | Accepted (with planned evolution)                      |
| **Date**       | 2025-12-01                                             |
| **Deciders**   | Integration Coworker Team                              |
| **Supersedes** | —                                                      |
| **Related**    | ADR-0003 (Template-First Code Generation)              |

---

## Context

Every production API integration requires **non-functional resilience patterns**:

| Policy | Purpose |
|--------|---------|
| **Authentication** | Bearer tokens, API keys, OAuth2, Basic auth |
| **Retry** | Exponential backoff, jitter, retryable status codes |
| **Rate Limiting** | Token bucket throttling, burst control |
| **Logging** | Request/response capture, sensitive field redaction |
| **Idempotency** | Unique keys for safe retries of mutating operations |

These patterns must be:
1. **Consistently applied** across all generated clients
2. **Configurable** per API (auth type, retry limits, rate caps)
3. **Inferred** from OpenAPI specs when possible (securitySchemes, x-rate-limit)
4. **Maintainable** as policies evolve (security patches, behavior changes)

We evaluated multiple approaches and made a pragmatic v1 choice that differs from the optimal long-term architecture.

---

## Decision

### Current Implementation: Code Templates

For v1, we implemented **Policy Injection via Code Templates**:

- Each policy type has a template function that generates Python code snippets
- Snippets are injected into generated client code at specific injection points
- Generated code is standalone—no runtime library dependency for policies

### Target Architecture: Runtime Middleware

The optimal long-term architecture is **Runtime Middleware with Thin Generated Wiring**:

- Policy logic lives in a shared runtime library
- Generated clients configure and compose policy objects
- Bug fixes and improvements propagate automatically to all clients

### Migration Path

We will evolve from Code Templates to Runtime Middleware incrementally, without breaking existing generated code.

---

## Current Implementation Details

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│               attach_policies_and_patterns                      │
│                    (Policy Inference)                           │
├─────────────────────────────────────────────────────────────────┤
│  Reads: OpenAPI securitySchemes, x-rate-limit extensions       │
│  Writes: state.policies[] with type and config                 │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│               codegen/policy_templates.py                       │
│                    (Template Functions)                         │
├─────────────────────────────────────────────────────────────────┤
│  get_auth_template(config) → PolicyCodeSnippet                 │
│  get_retry_template(config) → PolicyCodeSnippet                │
│  get_rate_limit_template(config) → PolicyCodeSnippet           │
│  get_logging_template(config) → PolicyCodeSnippet              │
│  get_idempotency_template(config) → PolicyCodeSnippet          │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│               inject_policies_into_client_code                  │
│                    (String-Based Injection)                     │
├─────────────────────────────────────────────────────────────────┤
│  Injection points:                                              │
│  ├─ After imports → policy imports                             │
│  ├─ End of __init__ → setup_code (instance variables)          │
│  ├─ Before request → pre_request_code (headers, rate limit)    │
│  ├─ After response → post_request_code (logging)               │
│  └─ End of class → wrapper_code (helper methods)               │
└─────────────────────────────────────────────────────────────────┘
```

### PolicyCodeSnippet Structure

```python
@dataclass
class PolicyCodeSnippet:
    imports: List[str]       # ["import os", "import time"]
    setup_code: str          # Code for __init__ (instance variables)
    pre_request_code: str    # Code before each HTTP request
    post_request_code: str   # Code after each HTTP response
    wrapper_code: str        # Helper methods, decorators
```

### Example: Generated Client with Inline Policies

```python
# Current output: ~300 lines per client
class StripeClient:
    def __init__(self, api_key: str, base_url: str = "https://api.stripe.com"):
        self.client = IntegrationHttpClient(base_url=base_url)
        
        # === AUTH POLICY (inline) ===
        self._auth_token = os.environ.get("STRIPE_API_KEY", "")
        if not self._auth_token:
            raise ValueError("Missing STRIPE_API_KEY")
        
        # === RETRY POLICY (inline) ===
        self._max_retries = 3
        self._initial_delay_ms = 100
        self._retryable_status_codes = [429, 500, 502, 503, 504]
        
        # === RATE LIMIT POLICY (inline) ===
        self._rate_limit = 10
        self._tokens = 20
        self._last_refill = time.time()
        self._rate_limit_lock = threading.Lock()
        
        # === LOGGING POLICY (inline) ===
        self._logger = logging.getLogger(self.__class__.__name__)
        self._redact_fields = ["Authorization", "api_key"]
        
        # === IDEMPOTENCY POLICY (inline) ===
        self._idempotency_header = "Idempotency-Key"
    
    def create_payment_intent(self, payload: dict) -> dict:
        headers = {}
        
        # Auth (from template)
        headers["Authorization"] = f"Bearer {self._auth_token}"
        
        # Rate limit (from template)
        self._acquire_rate_limit_token()
        
        # Idempotency (from template)
        headers["Idempotency-Key"] = str(uuid.uuid4())
        
        # Logging (from template)
        self._log_request("POST", "/v1/payment_intents", headers, {}, payload)
        
        response = self.client.request("POST", "/v1/payment_intents", json=payload, headers=headers)
        
        # Logging (from template)
        self._log_response(response)
        
        return response.json()
    
    # === 100+ lines of helper methods from templates ===
    def _acquire_rate_limit_token(self): ...
    def _with_retry(self, operation): ...
    def _log_request(self, ...): ...
    def _log_response(self, ...): ...
    def _redact_sensitive(self, ...): ...
```

### Why This Was Chosen for v1

| Reason | Explanation |
|--------|-------------|
| **Fast to implement** | Template strings are simpler than designing a runtime library |
| **Immediate visibility** | Developers can read the generated code to understand behavior |
| **No runtime dependency** | Generated code works without installing additional packages |
| **Incremental development** | Could add one policy type at a time |

---

## Target Architecture: Runtime Middleware

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│               Policy Configuration                              │
│          (YAML file or inferred from OpenAPI spec)             │
├─────────────────────────────────────────────────────────────────┤
│  # config/stripe.policies.yaml                                  │
│  auth:                                                          │
│    type: bearer                                                 │
│    env_var: STRIPE_API_KEY                                      │
│  retry:                                                         │
│    strategy: exponential                                        │
│    max_attempts: 3                                              │
│    retryable_codes: [429, 500, 502, 503, 504]                  │
│  rate_limit:                                                    │
│    requests_per_second: 10                                      │
│    burst_size: 20                                               │
│  logging:                                                       │
│    log_request: true                                            │
│    log_response: true                                           │
│    redact_fields: [Authorization, api_key]                     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│               Generated Client (Thin Wiring)                    │
│                       (~50 lines)                               │
├─────────────────────────────────────────────────────────────────┤
│  from integration_coworker.runtime import (                    │
│      IntegrationClient,                                         │
│      BearerAuth,                                                │
│      ExponentialRetry,                                          │
│      TokenBucketRateLimiter,                                    │
│      RequestLogger,                                             │
│  )                                                              │
│                                                                 │
│  class StripeClient(IntegrationClient):                         │
│      def __init__(self):                                        │
│          super().__init__(                                      │
│              base_url="https://api.stripe.com",                │
│              auth=BearerAuth(env_var="STRIPE_API_KEY"),        │
│              retry=ExponentialRetry(max_attempts=3),           │
│              rate_limiter=TokenBucketRateLimiter(rps=10),      │
│              logger=RequestLogger(redact=["Authorization"]),   │
│          )                                                      │
│                                                                 │
│      def create_payment_intent(self, amount, currency):         │
│          return self.post("/v1/payment_intents", json={...})   │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│               Runtime Library (Shared)                          │
│                       (~500 lines)                              │
├─────────────────────────────────────────────────────────────────┤
│  integration_coworker/runtime/                                  │
│  ├── client.py        # IntegrationClient base class            │
│  ├── auth.py          # BearerAuth, ApiKeyAuth, OAuth2Auth      │
│  ├── retry.py         # ExponentialRetry, LinearRetry           │
│  ├── rate_limit.py    # TokenBucketRateLimiter (thread-safe)   │
│  ├── logging.py       # RequestLogger with redaction            │
│  └── idempotency.py   # IdempotencyKeyGenerator                 │
└─────────────────────────────────────────────────────────────────┘
```

### Example: Generated Client with Runtime Middleware

```python
# Target output: ~50 lines per client
from integration_coworker.runtime import (
    IntegrationClient,
    BearerAuth,
    ExponentialRetry,
    TokenBucketRateLimiter,
    RequestLogger,
    IdempotencyKeyGenerator,
)


class StripeClient(IntegrationClient):
    """Stripe API Client - Auto-generated by Integration Coworker."""
    
    def __init__(self):
        super().__init__(
            base_url="https://api.stripe.com",
            auth=BearerAuth(env_var="STRIPE_API_KEY"),
            retry=ExponentialRetry(
                max_attempts=3,
                retryable_codes=[429, 500, 502, 503, 504],
            ),
            rate_limiter=TokenBucketRateLimiter(
                requests_per_second=10,
                burst_size=20,
            ),
            logger=RequestLogger(
                redact_fields=["Authorization", "api_key"],
            ),
            idempotency=IdempotencyKeyGenerator(
                header_name="Idempotency-Key",
            ),
        )
    
    def create_payment_intent(self, amount: int, currency: str) -> dict:
        """Create a PaymentIntent."""
        return self.post("/v1/payment_intents", json={
            "amount": amount,
            "currency": currency,
        })
    
    def get_payment_intent(self, payment_intent_id: str) -> dict:
        """Retrieve a PaymentIntent by ID."""
        return self.get(f"/v1/payment_intents/{payment_intent_id}")
```

### Runtime Library Implementation

```python
# integration_coworker/runtime/client.py

class IntegrationClient:
    """Base class for all generated API clients."""
    
    def __init__(
        self,
        base_url: str,
        auth: BaseAuth,
        retry: BaseRetry = None,
        rate_limiter: BaseRateLimiter = None,
        logger: BaseLogger = None,
        idempotency: BaseIdempotency = None,
    ):
        self.base_url = base_url
        self.auth = auth
        self.retry = retry or NoRetry()
        self.rate_limiter = rate_limiter or NoRateLimiter()
        self.logger = logger or NoLogger()
        self.idempotency = idempotency or NoIdempotency()
        self._http = httpx.Client()
    
    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Execute request with all policies applied."""
        # Rate limiting
        self.rate_limiter.acquire()
        
        # Build headers
        headers = kwargs.pop("headers", {})
        headers.update(self.auth.get_headers())
        
        # Idempotency for mutating requests
        if method.upper() in ("POST", "PUT", "PATCH"):
            headers.update(self.idempotency.get_headers(kwargs.get("json", {})))
        
        # Logging
        self.logger.log_request(method, path, headers, kwargs)
        
        # Execute with retry
        def execute():
            return self._http.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                **kwargs,
            )
        
        response = self.retry.execute(execute)
        
        # Logging
        self.logger.log_response(response)
        
        return response
    
    def get(self, path: str, **kwargs) -> dict:
        return self.request("GET", path, **kwargs).json()
    
    def post(self, path: str, **kwargs) -> dict:
        return self.request("POST", path, **kwargs).json()
```

---

## Comparison

| Metric | Code Templates (Current) | Runtime Middleware (Target) |
|--------|--------------------------|----------------------------|
| **Lines per client** | ~300 | ~50 |
| **Lines in shared code** | ~50 | ~500 |
| **Total (10 clients)** | 3,050 | 1,000 |
| **Total (100 clients)** | 30,050 | 5,500 |
| **Bug fix propagation** | Regenerate all clients | Update library once |
| **Policy behavior change** | Regenerate affected clients | Update library once |
| **Testing required** | Each generated client | Library + integration tests |
| **Runtime dependency** | None (standalone) | `integration_coworker.runtime` |
| **State sharing** | Per-client (no sharing) | Global (shared rate limiters) |
| **Debuggability** | Read generated code | Step through library |

---

## Migration Path

### Phase 1: Introduce Runtime Library (Non-Breaking)

**Goal**: Create the runtime library without changing existing code generation.

```
src/integration_coworker/runtime/
├── __init__.py
├── client.py          # IntegrationClient base
├── auth.py            # Auth policy implementations
├── retry.py           # Retry policy implementations
├── rate_limit.py      # Rate limit implementations
├── logging.py         # Logging implementations
├── idempotency.py     # Idempotency implementations
└── exceptions.py      # Policy-related exceptions
```

**Deliverables**:
- Fully tested runtime library
- Documentation for each policy class
- No changes to existing code generation

### Phase 2: Dual-Mode Code Generation

**Goal**: Support both inline templates and runtime wiring.

```python
# New option in IntegrationOptions:
class IntegrationOptions:
    policy_mode: Literal["inline", "runtime"] = "inline"  # Default to current behavior
```

When `policy_mode="runtime"`:
- Generate thin client that imports from runtime library
- Generate policy configuration (YAML or Python dict)
- Skip inline template injection

**Deliverables**:
- `generate_code_and_tests` supports both modes
- New clients can opt into runtime mode
- Existing clients continue to work

### Phase 3: Runtime as Default

**Goal**: Make runtime middleware the default, deprecate inline templates.

```python
class IntegrationOptions:
    policy_mode: Literal["inline", "runtime"] = "runtime"  # New default
```

**Deliverables**:
- Documentation updated to recommend runtime mode
- Deprecation warnings for `policy_mode="inline"`
- Migration guide for existing clients

### Phase 4: Remove Inline Templates

**Goal**: Simplify codebase by removing template injection.

**Deliverables**:
- Remove `policy_templates.py`
- Remove `inject_policies_into_client_code`
- All generated clients use runtime library

---

## Consequences

### Current Implementation (Code Templates)

**Positive**:
- ✅ Visible: policy code is inline and readable
- ✅ Standalone: generated code has no runtime dependency
- ✅ Implemented: already working in production

**Negative**:
- ❌ Code bloat: ~200 lines of boilerplate per client
- ❌ Maintenance burden: template changes require regeneration
- ❌ No state sharing: each client has its own rate limiter
- ❌ Fragile injection: string-based parsing of generated code

### Target Architecture (Runtime Middleware)

**Positive**:
- ✅ Maintainable: single library to update
- ✅ Compact: ~50 lines per generated client
- ✅ State sharing: global rate limiters, token caches
- ✅ Testable: test library once, not each client

**Negative**:
- ❌ Runtime dependency: clients must install runtime package
- ❌ Less visible: policy logic in library, not inline
- ❌ Migration effort: existing clients need regeneration

---

## Implementation References

### Current Implementation

| Component | File |
|-----------|------|
| Template functions | `codegen/policy_templates.py` |
| Code injection | `codegen/policy_templates.py::inject_policies_into_client_code` |
| Policy inference | `graph/nodes/attach_policies_and_patterns.py` |
| Tests | `tests/test_policy_templates.py` |

### Target Implementation (To Be Created)

| Component | File |
|-----------|------|
| Base client | `runtime/client.py` |
| Auth policies | `runtime/auth.py` |
| Retry policies | `runtime/retry.py` |
| Rate limiting | `runtime/rate_limit.py` |
| Logging | `runtime/logging.py` |
| Idempotency | `runtime/idempotency.py` |

---

## Decision Rationale

### Why Code Templates for v1?

1. **Speed to market**: Template strings were faster to implement than a full runtime library
2. **Reduced scope**: Avoided designing policy interfaces upfront
3. **Immediate results**: Generated working code quickly for demos

### Why Runtime Middleware for v2?

1. **Scalability**: O(1) maintenance vs O(n) client regeneration
2. **Reliability**: Single well-tested library vs N copies of template code
3. **Features**: State sharing (global rate limiters) requires shared runtime

### Why Not Runtime Middleware from the Start?

In hindsight, Runtime Middleware would have been the better initial choice. The "visibility" and "standalone code" arguments for Code Templates are weaker than they appeared:

- **Visibility**: Developers skip over boilerplate; clean client code is more readable
- **Standalone**: The runtime library is a small, well-tested dependency—not a burden

This ADR documents the decision honestly: Code Templates was a pragmatic v1 choice, not the optimal architecture.

---

## Open Questions

1. **Should the runtime library be a separate package?**
   - Option A: Part of `integration_coworker` (current plan)
   - Option B: Separate `integration-runtime` package for lighter dependency

2. **How do we handle version compatibility?**
   - Generated clients may expect specific runtime library versions
   - Need versioning strategy for runtime library

3. **Should we support plugin policies?**
   - Allow users to implement custom `BaseAuth`, `BaseRetry`, etc.
   - Increases flexibility but adds complexity

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-01 | Integration Coworker Team | Initial decision documenting current state and target |

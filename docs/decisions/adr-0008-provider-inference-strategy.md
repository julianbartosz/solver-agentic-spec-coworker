# ADR-0008: Provider Inference Strategy

| Metadata       | Value                                                     |
|----------------|-----------------------------------------------------------|
| **Status**     | Accepted                                                  |
| **Date**       | 2025-12-01                                                |
| **Deciders**   | Integration Coworker Team                                 |
| **Supersedes** | —                                                         |
| **Related**    | ADR-0004 (Hybrid GraphRAG Scoring), design-doc Section 4.2 |

---

## Context

The Integration Coworker generates integration code for arbitrary OpenAPI specifications. A critical early step is determining the **provider code** — a normalized identifier (e.g., `"stripe"`, `"hubspot"`, `"acme_widget"`) that:

1. **Indexes the Knowledge Graph** — Templates are keyed by `(provider_code, task_slug)`
2. **Drives code generation** — Client class names (`StripeClient`), config modules (`stripe_config.py`)
3. **Enables cross-provider pattern matching** — Fallback when no provider-specific templates exist
4. **Links to source_systems** — Silver layer records associate parsed data with a provider

### The Problem

How should the system determine `provider_code` when given only a spec reference (URL or file path) and optionally the parsed spec content?

```python
# User invocation — no explicit provider given
integration_coworker run \
  --spec-ref https://api.acme.com/openapi.yaml \
  --task "Create a widget order"

# What should provider_code be?
```

### Requirements

| Requirement | Description |
|-------------|-------------|
| **R1. Cold-start capable** | Must handle completely unknown APIs without prior registration |
| **R2. Deterministic** | Same input always produces same provider_code |
| **R3. Zero-config** | No mandatory provider database or registry |
| **R4. Accurate for known APIs** | Well-known providers (Stripe, HubSpot) should resolve correctly |
| **R5. Graceful degradation** | Always return a usable value, never fail |
| **R6. Fast** | Sub-millisecond, no network calls in critical path |
| **R7. Override-capable** | User can explicitly set provider if inference is wrong |

---

## Decision

We adopt a **Cascading Heuristic Inference Strategy** that applies multiple extraction methods in priority order, returning the first successful match.

### Priority Cascade (v1)

```
┌─────────────────────────────────────────────────────────────────┐
│                    Provider Inference Cascade                   │
│                  (First match wins, always succeeds)            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. User Override                                               │
│     └── options.override_provider_code                          │
│         "stripe" → "stripe" ✓                                   │
│                                                                 │
│  2. Server URL Domain Extraction                                │
│     └── spec.servers[0].url                                     │
│         "https://api.stripe.com/v1" → "stripe" ✓                │
│                                                                 │
│  3. Info Title Slugification                                    │
│     └── spec.info.title                                         │
│         "Acme Widget API v2.0" → "acme_widget" ✓                │
│                                                                 │
│  4. Spec URL Domain Extraction                                  │
│     └── spec_ref (if URL)                                       │
│         "https://docs.hubspot.com/api.yaml" → "hubspot" ✓       │
│                                                                 │
│  5. Filepath Stem Extraction                                    │
│     └── Path(spec_ref).stem                                     │
│         "tests/fixtures/mock_payments_openapi.yaml"             │
│         → "mock_payments" ✓                                     │
│                                                                 │
│  6. Fallback                                                    │
│     └── "unknown" (guaranteed return)                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Priority Cascade (v2 — Planned)

v2 adds an `x-provider-code` OpenAPI extension as the highest-priority source after user override:

```yaml
# In the OpenAPI spec
openapi: 3.0.0
info:
  title: Acme Widget API
  x-provider-code: acme_widgets  # ← Spec author's canonical answer
```

```
┌─────────────────────────────────────────────────────────────────┐
│              Provider Inference Cascade (v2)                    │
├─────────────────────────────────────────────────────────────────┤
│  1. User Override          options.override_provider_code       │
│  2. Extension (NEW)        spec.info.x-provider-code            │
│  3. Server URL Domain      spec.servers[0].url                  │
│  4. Info Title Slug        spec.info.title                      │
│  5. Spec URL Domain        spec_ref (if URL)                    │
│  6. Filepath Stem          Path(spec_ref).stem                  │
│  7. Fallback               "unknown"                            │
└─────────────────────────────────────────────────────────────────┘
```

### Implementation

Located in `src/integration_coworker/graph/nodes/plan_run.py`:

```python
def infer_provider_code(
    spec_ref: str,
    parsed_spec: Optional[dict] = None,
) -> str:
    """
    Infer provider_code from spec content and/or spec reference.
    
    M5 Architecture: Dynamic inference, no hardcoded provider list.
    
    Priority order:
    1. spec.servers[0].url domain (most reliable for real APIs)
    2. spec.info.title (good for descriptive specs)
    3. Filepath/URL of spec itself (fallback)
    
    Returns:
        Inferred provider_code string (never None)
    """
    # Strategy 1: Infer from spec servers URL
    if parsed_spec:
        servers = parsed_spec.get("servers", [])
        if servers and isinstance(servers, list):
            server_url = servers[0].get("url", "") if isinstance(servers[0], dict) else ""
            provider = _infer_provider_from_url(server_url)
            if provider:
                return provider

    # Strategy 2: Infer from spec info.title
    if parsed_spec:
        title = parsed_spec.get("info", {}).get("title", "")
        provider = _infer_provider_from_title(title)
        if provider:
            return provider

    # Strategy 3: Infer from spec URL/path
    if "://" in spec_ref:
        provider = _infer_provider_from_url(spec_ref)
        if provider:
            return provider

    # Strategy 4: Infer from filepath
    return _infer_provider_from_filepath(spec_ref)
```

### Helper Functions

#### URL Domain Extraction

```python
def _infer_provider_from_url(url: str) -> Optional[str]:
    """
    Extract provider code from URL domain.
    
    Examples:
    - "https://api.stripe.com/v1" → "stripe"
    - "https://api.hubspot.com" → "hubspot"
    - "https://my-api.acme.io" → "acme"
    """
    domain = url.split("://")[1].split("/")[0].lower()
    domain = domain.replace("api.", "").replace("www.", "")
    parts = domain.split(".")
    filtered = [p for p in parts if p not in ("com", "io", "org", "net", "dev", "co", "app")]
    return filtered[0] if filtered else None
```

#### Title Slugification

```python
def _infer_provider_from_title(title: str) -> Optional[str]:
    """
    Convert spec title to snake_case provider code.
    
    Examples:
    - "Stripe API" → "stripe"
    - "Acme Widget API v2.0" → "acme_widget"
    - "Mock Payments Service" → "mock_payments"
    """
    title = title.lower()
    for suffix in [" api", " service", " v1", " v2", " v3"]:
        title = title.replace(suffix, "")
    slug = re.sub(r'[^a-z0-9]+', '_', title.strip()).strip('_')
    return slug[:30] if slug else None
```

#### Filepath Extraction

```python
def _infer_provider_from_filepath(filepath: str) -> str:
    """
    Extract provider code from file path stem.
    
    Examples:
    - "tests/fixtures/mock_payments_openapi.yaml" → "mock_payments"
    - "/path/to/stripe_api.json" → "stripe"
    """
    stem = Path(filepath).stem.lower()
    for suffix in ["_openapi", "-openapi", "_api", "-api", "_spec", "-spec"]:
        stem = stem.replace(suffix, "")
    slug = re.sub(r'[^a-z0-9]+', '_', stem).strip('_')
    return slug if slug else "unknown"
```

---

## Alternatives Considered

### 1. Static Provider Registry

Pre-define all known providers in a configuration file:

```python
PROVIDER_REGISTRY = {
    "stripe": {"domains": ["stripe.com", "api.stripe.com"], "auth": "bearer"},
    "hubspot": {"domains": ["hubspot.com", "api.hubspot.com"], "auth": "api_key"},
    # ... must add each provider manually
}
```

| Aspect | Assessment |
|--------|------------|
| ✅ Pros | Explicit, predictable, supports provider-specific metadata |
| ❌ Cons | Maintenance burden, can't handle unknown APIs, violates R1/R3 |
| **Verdict** | Too rigid for "any OpenAPI spec" use case |

### 2. LLM-Based Inference

Use a language model to infer the provider from spec content:

```python
def infer_provider_llm(spec: dict) -> str:
    prompt = f"Given this OpenAPI spec info: {spec['info']}, what provider is this? Reply with one word."
    return call_llm(prompt).strip().lower()
```

| Aspect | Assessment |
|--------|------------|
| ✅ Pros | Handles ambiguous cases, human-like reasoning |
| ❌ Cons | Non-deterministic, slow (500ms+), expensive, hallucination risk |
| **Verdict** | Overkill for a deterministic derivation problem; violates R2/R6 |

### 3. OpenAPI Extension Only

Require spec authors to include `x-provider-code`:

```yaml
info:
  title: Acme Widget API
  x-provider-code: acme_widgets
```

| Aspect | Assessment |
|--------|------------|
| ✅ Pros | Canonical answer from spec author |
| ❌ Cons | Public specs won't have this, requires spec modification |
| **Verdict** | Good as priority-2 check (v2), not a complete solution |

### 4. Domain Fingerprint Database

Maintain a database of known API domains:

```python
DOMAIN_FINGERPRINTS = {
    "stripe.com": "stripe",
    "api.hubspot.com": "hubspot",
    "graph.microsoft.com": "microsoft_graph",
}
```

| Aspect | Assessment |
|--------|------------|
| ✅ Pros | Fast, accurate for known APIs |
| ❌ Cons | Same maintenance burden as registry, doesn't help unknown APIs |
| **Verdict** | Could augment current approach as an optimization |

### 5. Semantic Embedding + Nearest Neighbor

Embed spec metadata and find similar known providers:

```python
def infer_provider_semantic(spec: dict) -> str:
    spec_embedding = embed(json.dumps(spec['info']))
    nearest = vector_db.query(spec_embedding, collection="known_providers")
    return nearest[0].provider_code if similarity > 0.85 else slugify(spec['info']['title'])
```

| Aspect | Assessment |
|--------|------------|
| ✅ Pros | Could generalize across similar APIs |
| ❌ Cons | Requires seed database, cold-start problem, overkill for derivation |
| **Verdict** | Interesting for cross-provider pattern matching, not for inference |

---

## Comparison Matrix

| Criterion | Cascading Heuristic | Static Registry | LLM | Extension-Only | Embedding |
|-----------|---------------------|-----------------|-----|----------------|-----------|
| **R1. Cold-start** | ✅ Excellent | ❌ None | ⚠️ Unpredictable | ❌ None | ⚠️ Needs seeding |
| **R2. Deterministic** | ✅ Yes | ✅ Yes | ❌ No | ✅ Yes | ✅ Yes |
| **R3. Zero-config** | ✅ Yes | ❌ No | ✅ Yes | ❌ Requires spec edit | ⚠️ Needs DB |
| **R4. Known accuracy** | ⚠️ Good | ✅ Perfect | ⚠️ Good | ✅ Perfect | ⚠️ Good |
| **R5. Graceful degradation** | ✅ Always returns | ❌ May fail | ⚠️ May hallucinate | ❌ May fail | ⚠️ May fail |
| **R6. Fast** | ✅ <1ms | ✅ <1ms | ❌ 500ms+ | ✅ <1ms | ⚠️ 10-50ms |
| **R7. Override** | ✅ Supported | ✅ Supported | ✅ Supported | ✅ Supported | ✅ Supported |
| **Fit for v1** | ✅ **Excellent** | ⚠️ Medium | ❌ Poor | ⚠️ Medium | ❌ Poor |

---

## Consequences

### Positive

1. **Zero-config operation** — Works with any OpenAPI spec out of the box
2. **Deterministic behavior** — Same input always yields same `provider_code`
3. **Fast execution** — No network calls, sub-millisecond inference
4. **Graceful degradation** — Always returns a usable value (worst case: slugified title)
5. **Simple implementation** — ~100 LOC, easy to understand and maintain
6. **Override escape hatch** — Users can explicitly set `--provider` if inference is wrong

### Negative

1. **Disambiguation limitations** — "Acme API" and "Acme Payments API" both become `"acme"`
2. **Multi-tenant spec hosting** — Specs on SwaggerHub/GitHub lose domain signal
3. **Provider renaming** — If company rebrands, old specs may infer wrong code
4. **No semantic understanding** — Can't infer that "PayPal Checkout" relates to "payments"

### Neutral

1. **Title quality dependency** — Inference quality correlates with spec metadata quality
2. **Normalization rules** — Must document slugification rules for predictability

---

## Scalability Assessment

### Scales Well For

| Scenario | Why It Works |
|----------|--------------|
| New unknown providers | Slugifies title, always returns something |
| Self-hosted / private APIs | No registry dependency |
| Offline operation | No network calls |
| High-volume runs | O(1) inference, no DB lookups |

### Does Not Scale For

| Scenario | Limitation |
|----------|------------|
| Disambiguation of similar names | Multiple APIs → same provider_code |
| Provider ecosystem tracking | No way to know "all Stripe-related specs" |
| Very large multi-provider orgs | Same company, many APIs → need explicit codes |

---

## Migration Path to v2

### Phase 1: Add x-provider-code Extension (v2)

```python
def infer_provider_code_v2(spec_ref: str, parsed_spec: dict) -> str:
    # Priority 1: User override (unchanged)
    
    # Priority 2: NEW — x-provider-code extension
    if parsed_spec:
        ext = parsed_spec.get("info", {}).get("x-provider-code")
        if ext and isinstance(ext, str):
            return normalize(ext)
    
    # Priority 3+: Existing cascade (unchanged)
```

### Phase 2: Domain Fingerprint Cache (v2+)

Add high-confidence domain mappings as an optional priority:

```python
KNOWN_DOMAINS = {
    "api.stripe.com": "stripe",
    "api.hubspot.com": "hubspot",
    "graph.microsoft.com": "microsoft_graph",
}

# Insert between extension and server URL extraction
```

### Phase 3: Provider Alias Support (v3)

Allow multiple names to resolve to same canonical provider:

```python
PROVIDER_ALIASES = {
    "paypal_checkout": "paypal",
    "paypal_payments": "paypal",
    "braintree": "paypal",  # Owned by PayPal
}
```

---

## Implementation References

### Source Files

| File | Description |
|------|-------------|
| `src/integration_coworker/graph/nodes/plan_run.py` | Main inference implementation |
| `src/integration_coworker/api/types.py` | `IntegrationOptions.override_provider_code` |
| `src/integration_coworker/graph/state.py` | `WorkflowState.provider_code` field |

### Integration Points

| Component | How provider_code Is Used |
|-----------|---------------------------|
| `plan_run` | Sets `state.provider_code` |
| `build_silver_api_model` | Uses for `source_systems.provider_code` |
| `align_task_with_kg` | Queries KG by provider_code |
| `generate_code_and_tests` | Derives class names, file paths |
| `persist_silver_checkpoint` | Stores in database |

### Tests

| Test File | Coverage |
|-----------|----------|
| `tests/test_plan_run.py` | Inference priority cascade |
| `tests/test_cross_provider_patterns.py` | Pattern fallback when provider unknown |
| `tests/test_end_to_end_integration.py` | Full workflow with various providers |

---

## Design Doc References

- **Section 4.2**: "Provider identification uses cascading heuristics"
- **Appendix C.1.1**: "plan_run node determines provider_code before parsing"

---

## Related ADRs

- **ADR-0004**: [Hybrid GraphRAG Scoring](adr-0004-hybrid-graphrag-scoring-strategy.md) — Uses provider_code for KG filtering
- **ADR-0006**: [Medallion Data Architecture](adr-0006-medallion-data-architecture.md) — provider_code stored in source_systems

---

## Open Questions (v2)

1. **Should x-provider-code be a registered OpenAPI extension?**
   - Could submit to OpenAPI Initiative for standardization
   - Currently treated as vendor extension (`x-` prefix)

2. **Should we validate provider_code format?**
   - Currently accepts any slugified string
   - Could enforce pattern: `^[a-z][a-z0-9_]{2,29}$`

3. **Should inference be re-run after parsing?**
   - Currently runs in `plan_run` (before parse) and could refine in `detect_and_parse_spec`
   - Trade-off: complexity vs. accuracy

---

## Notes

This decision prioritizes **cold-start capability and zero-config operation** over perfect accuracy for known providers. The cascading heuristic approach aligns with the project's goal of working with "any OpenAPI spec" without requiring a provider database.

The v2 enhancement (`x-provider-code` extension) provides an escape hatch for spec authors who want deterministic provider identification, while maintaining backward compatibility with specs that lack the extension.

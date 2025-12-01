# ADR-0003: Template-First Code Generation Strategy

| Metadata       | Value                                                  |
|----------------|--------------------------------------------------------|
| **Status**     | Accepted                                               |
| **Date**       | 2025-12-01                                             |
| **Deciders**   | Integration Coworker Team                              |
| **Supersedes** | —                                                      |
| **Related**    | ADR-0001 (Initial Architecture), ADR-0004 (Hybrid GraphRAG Scoring) |

---

## Context

The Integration Coworker must generate production-ready code artifacts (API clients, workflow functions, tests) from an API specification and task description. This presents several challenges:

1. **Structural Consistency**: Generated code must follow predictable patterns (auth handling, error paths, logging) regardless of which API is being integrated.

2. **Policy Compliance**: Every integration must incorporate baseline policies—authentication, retry logic, rate limiting, logging with redaction, idempotency—without the developer manually wiring each one.

3. **Reproducibility**: Running the same task twice should produce the same code structure. Non-deterministic LLM output violates this requirement.

4. **Knowledge Accumulation**: Patterns learned from one integration should benefit future integrations with similar APIs.

5. **Explainability**: Developers must understand *why* certain code was generated and *how* to modify it.

We evaluated multiple approaches to code generation and needed to select one that balances flexibility, predictability, and maintainability.

---

## Decision

We adopt a **Template-First Code Generation Strategy** where:

1. **Workflow templates** define the structure of integration flows (sequence of steps, step types, dependencies)
2. **Templates are retrieved** from the Knowledge Graph via Hybrid GraphRAG scoring
3. **Templates are instantiated** with spec-specific details (endpoint paths, schemas, field names)
4. **LLM refines** the skeletal code within the template structure (not generating from scratch)
5. **Templates are persisted** back to the KG for future reuse

### Core Principle

> **Templates provide structure; LLM provides polish.**

The LLM operates as a *refinement engine* on template-generated skeletons, not as a *generative engine* producing code from scratch.

---

## Architecture

### Workflow Template Structure

Each template defines a directed acyclic graph (DAG) of steps:

```python
@dataclass
class WorkflowTemplate:
    template_id: str           # Unique identifier (e.g., "stripe_payment_intent_v1")
    name: str                  # Human-readable name
    description: str           # What this template accomplishes
    steps: List[WorkflowStep]  # Ordered sequence of steps

@dataclass  
class WorkflowStep:
    key: str           # Unique within template (e.g., "validate_input")
    type: StepType     # One of: start, validation, api_call, transform, decision, end
    label: str         # Display label
    description: str   # What this step does
    config: dict       # Step-specific configuration
```

### Step Type Vocabulary

| Step Type | Purpose | Code Generation Effect |
|-----------|---------|------------------------|
| `start` | Entry point marker | Function signature, docstring, input parsing |
| `validation` | Input validation | Type assertions, required field checks, format validation |
| `api_call` | HTTP request execution | `client.request()` with endpoint binding, headers, auth |
| `transform` | Response processing | Field extraction, data normalization, type conversion |
| `decision` | Conditional branching | if/else logic, error routing, retry decisions |
| `end` | Exit point marker | Return statement, cleanup, logging |

### Template Retrieval Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                    1. align_task_with_kg                        │
│           Input: task_description, provider_code                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Source 1: KG Templates (GraphRAG)                       │   │
│  │ • Query kg.nodes WHERE type='workflow_template'         │   │
│  │ • Filter by provider_code                               │   │
│  │ • Score via Hybrid GraphRAG (40/40/20)                  │   │
│  │ • Return top-5 candidates                               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                          ↓ (empty?)                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Source 2: Legacy Templates (USE_LEGACY_TEMPLATES=1)     │   │
│  │ • In-memory dict: _LEGACY_WORKFLOW_TEMPLATES            │   │
│  │ • Keyed by (provider_code, task_slug)                   │   │
│  │ • DEPRECATED: For backwards compatibility only          │   │
│  └─────────────────────────────────────────────────────────┘   │
│                          ↓ (empty?)                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Source 3: HTTP Method Inference (M5 Default)            │   │
│  │ • _infer_workflow_from_endpoint(endpoint, task)         │   │
│  │ • POST → validate_input → create → transform → end      │   │
│  │ • GET+path_param → validate_id → fetch → handle_404     │   │
│  │ • GET → build_query → list → paginate                   │   │
│  │ • PUT/PATCH → validate → update → transform             │   │
│  │ • DELETE → validate_id → delete → confirm               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  Output: workflow_nodes[], workflow_edges[]                     │
└─────────────────────────────────────────────────────────────────┘
```

### Code Generation Pipeline

```
┌─────────────────────────────────────────────────────────────────┐
│                  generate_code_and_tests                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  For each artifact_type in [client, flow, test]:               │
│                                                                 │
│  1. TEMPLATE SKELETON                                           │
│     ├─ Derive names from spec (client_class, method_name, etc) │
│     ├─ Get layout directories from RepoProfile                 │
│     └─ Generate skeleton with structure from workflow steps    │
│                                                                 │
│  2. POLICY INJECTION                                            │
│     ├─ get_auth_template(policy.config) → auth code snippet    │
│     ├─ get_retry_template(policy.config) → retry decorator     │
│     ├─ get_rate_limit_template(policy.config) → throttle code  │
│     └─ inject_policies_into_client_code(skeleton, policies)    │
│                                                                 │
│  3. LLM REFINEMENT                                              │
│     ├─ _refine_with_llm(skeleton, state, artifact_type)        │
│     ├─ Validate output (AST parse, expected class/function)    │
│     └─ Fallback to skeleton if LLM output invalid              │
│                                                                 │
│  4. OUTPUT                                                      │
│     └─ CodeArtifact(content=refined_code, rel_path=...)        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Template Learning Cycle

```
┌─────────────────────────────────────────────────────────────────┐
│                   persist_kg_learning                           │
│                (runs after successful generation)               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. CREATE TEMPLATE NODE                                        │
│     • key: "template.{provider}.{task_slug}"                   │
│     • type: "workflow_template"                                 │
│     • properties: {steps, node_count, edge_count}              │
│                                                                 │
│  2. COMPUTE EMBEDDING                                           │
│     • text: "Workflow template for {task_slug}: {description}" │
│     • embedding: 1536-dim vector (text-embedding-3-small)      │
│                                                                 │
│  3. CREATE WORKFLOW STEPS                                       │
│     • For each workflow_node → kg.workflow_steps row           │
│     • Columns: step_key, step_type, position, label, config    │
│                                                                 │
│  4. CREATE EDGES                                                │
│     • template → provider (BELONGS_TO_PROVIDER)                │
│     • template → task (COMPOSED_OF)                            │
│     • template → endpoints (USES_ENDPOINT)                     │
│     • template → entities (PRODUCES_ENTITY, CONSUMES_ENTITY)   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Alternatives Considered

### Option A: Pure LLM Generation

**Approach**: Provide task + spec to LLM, ask for complete code output.

```
Prompt: "Generate a Python client for Stripe that creates payment intents.
        Here is the OpenAPI spec: {...}"
Output: Complete client.py, flow.py, test.py
```

| Pros | Cons |
|------|------|
| Maximum flexibility—handles any API | Non-deterministic: different output each run |
| No template maintenance | No policy guarantees—may forget auth, retry |
| Works immediately for new providers | Hard to validate correctness |
| Natural language customization | No knowledge accumulation |
| | Expensive: full generation per request |

**Verdict**: Rejected. Policy compliance and reproducibility are requirements. LLM variability is unacceptable for production codegen.

### Option B: Schema-Driven Codegen

**Approach**: Mechanically generate code from OpenAPI schema, no LLM involved.

| Pros | Cons |
|------|------|
| Perfectly deterministic | No semantic understanding |
| Fast (no LLM calls) | Can't handle task nuances |
| Works for any OpenAPI spec | Generates everything, not task-specific code |
| Well-understood (Swagger Codegen) | No workflow structure, just client stubs |
| | Limited customization options |

**Verdict**: Rejected. Task-specific workflows require semantic understanding. Schema-driven tools generate generic clients, not integration flows.

### Option C: DSL + Compiler

**Approach**: Define integrations in a domain-specific language, compile to code.

```yaml
# integration.dsl
workflow: create_payment_intent
provider: stripe
steps:
  - validate: {amount: required, currency: enum[usd,eur]}
  - call: POST /v1/payment_intents
  - transform: extract(id, client_secret, status)
policies:
  - auth: bearer
  - retry: exponential_backoff
```

| Pros | Cons |
|------|------|
| Very predictable | Requires learning DSL syntax |
| Explicit policy declaration | DSL maintenance overhead |
| Tooling-friendly (linting, validation) | Less flexible than templates |
| Version-controlled definitions | Cold-start: must write DSL first |

**Verdict**: Considered for v2. DSL approach requires upfront investment in language design. Template-First is a stepping stone that could evolve into DSL.

### Option D: Template-First with LLM Refinement (Selected)

**Approach**: Templates provide structure; LLM fills in details.

| Pros | Cons |
|------|------|
| Predictable structure | Template bootstrap problem |
| Policy injection points defined | Step type vocabulary is limited |
| LLM handles nuance within structure | Template proliferation risk |
| Knowledge accumulates in KG | Requires GraphRAG infrastructure |
| Graceful degradation (skeleton if LLM fails) | Complex fallback hierarchy |

**Verdict**: Selected. Best balance of predictability, flexibility, and knowledge accumulation.

---

## Consequences

### Positive

1. **Structural Consistency**
   - All integrations follow same pattern: start → validate → call → transform → end
   - Developers know where to find auth logic, error handling, etc.

2. **Policy Compliance by Default**
   ```python
   # Policy injection happens automatically:
   inject_policies_into_client_code(skeleton, [
       auth_policy,      # → adds auth header setup
       retry_policy,     # → adds @retry decorator
       rate_limit_policy # → adds throttle logic
   ])
   ```

3. **Reproducible Output**
   - Template selection is deterministic (given same KG state)
   - LLM refinement is validation-gated (falls back to skeleton if invalid)

4. **Knowledge Graph Benefits**
   - Templates are queryable: "What templates exist for Stripe?"
   - Edges enable reasoning: "What endpoints does this template use?"
   - Embeddings enable similarity: "Find templates like this one"

5. **Graceful Degradation**
   - Mock LLM mode produces valid skeleton code
   - Missing templates trigger HTTP method inference
   - Invalid LLM output falls back to template skeleton

### Negative

1. **Bootstrap Problem**
   - First run for new provider has no templates
   - Falls back to HTTP method inference (generic patterns)
   - Quality improves after `persist_kg_learning` runs

2. **Step Type Limitations**
   ```python
   # Current vocabulary:
   STEP_TYPES = ["start", "validation", "api_call", "transform", "decision", "end"]
   
   # Not supported:
   # - loop (iterate over collection)
   # - parallel (concurrent API calls)
   # - sub_workflow (nested workflow invocation)
   ```

3. **Linear Flow Assumption**
   - Templates assume mostly linear sequences
   - Complex branching requires multiple `decision` nodes
   - No native support for retry-with-different-params patterns

4. **Template Maintenance**
   - Each provider × operation may need a template
   - Risk of template sprawl without proper governance
   - Need patterns for template versioning and deprecation

---

## Implementation Details

### Template Definition (Legacy In-Memory)

```python
# src/integration_coworker/graph/nodes/align_task_with_kg.py

_LEGACY_WORKFLOW_TEMPLATES = {
    ("stripe", "create_payment_intent"): {
        "template_id": "stripe_payment_intent_v1",
        "name": "Stripe Create Payment Intent",
        "description": "Standard flow for creating a Stripe PaymentIntent",
        "steps": [
            {"key": "start", "type": "start", "label": "Start"},
            {"key": "validate_input", "type": "validation", "label": "Validate Input",
             "description": "Validate amount, currency, and payment method types"},
            {"key": "call_create_intent", "type": "api_call", "label": "Create PaymentIntent",
             "description": "POST to /v1/payment_intents"},
            {"key": "transform_response", "type": "transform", "label": "Transform Response",
             "description": "Extract id, client_secret, and status"},
            {"key": "end", "type": "end", "label": "Return Result"},
        ],
    },
    # ... more templates
}
```

### HTTP Method Inference (Dynamic Fallback)

```python
# src/integration_coworker/graph/nodes/align_task_with_kg.py

def _infer_workflow_from_endpoint(endpoint: Endpoint, task_description: str) -> List[dict]:
    """
    Infer workflow steps from endpoint structure (M5: dynamic, no hardcoding).
    
    Pattern mapping:
    - POST with request body → validate_input → create_resource → return_created
    - GET with path params → validate_id → fetch_resource → return_or_404
    - GET without params → build_query → list_resources → paginate_response
    - PUT/PATCH → validate_input → fetch_existing → update_resource
    - DELETE → validate_id → delete_resource → confirm_deleted
    """
    steps = [{"key": "start", "type": "start", "label": "Start"}]
    
    method = (endpoint.method or "GET").upper()
    has_path_params = "{" in endpoint.path
    
    if method == "POST":
        steps.extend([
            {"key": "validate_input", "type": "validation", ...},
            {"key": "call_create", "type": "api_call", ...},
            {"key": "transform_response", "type": "transform", ...},
        ])
    elif method == "GET" and has_path_params:
        steps.extend([
            {"key": "validate_id", "type": "validation", ...},
            {"key": "call_get", "type": "api_call", ...},
            {"key": "handle_not_found", "type": "transform", ...},
        ])
    # ... other methods
    
    steps.append({"key": "end", "type": "end", "label": "End"})
    return steps
```

### Policy Injection

```python
# src/integration_coworker/codegen/policy_templates.py

def inject_policies_into_client_code(
    skeleton_code: str,
    policies: List[Policy],
) -> str:
    """Inject policy code snippets into generated client code."""
    
    for policy in policies:
        if policy.policy_type == "auth":
            snippet = get_auth_template(policy.config)
        elif policy.policy_type == "retry":
            snippet = get_retry_template(policy.config)
        elif policy.policy_type == "rate_limit":
            snippet = get_rate_limit_template(policy.config)
        # ...
        
        skeleton_code = _insert_snippet(skeleton_code, snippet)
    
    return skeleton_code
```

### LLM Refinement with Validation

```python
# src/integration_coworker/graph/nodes/generate_code_and_tests.py

def _refine_with_llm(
    template_code: str,
    state: WorkflowState,
    artifact_type: Literal["client", "flow", "test"],
    expected_class: Optional[str] = None,
    expected_function: Optional[str] = None,
) -> str:
    """Refine template code with LLM, with validation fallback."""
    
    client = get_llm_client_for_node("generate_code_and_tests")
    prompt = _build_code_generation_prompt(template_code, state, artifact_type)
    
    try:
        refined = client.complete(prompt)
        
        # Validate: must parse as valid Python
        ast.parse(refined)
        
        # Validate: expected class/function must exist
        if expected_class and expected_class not in refined:
            raise ValueError(f"Missing expected class: {expected_class}")
        if expected_function and expected_function not in refined:
            raise ValueError(f"Missing expected function: {expected_function}")
        
        return refined
        
    except Exception as e:
        logger.warning(f"LLM refinement failed, using template: {e}")
        return template_code  # Fallback to skeleton
```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `USE_LEGACY_TEMPLATES` | `0` | Enable legacy in-memory templates (deprecated) |
| `USE_IN_MEMORY_KG_FALLBACK` | `0` | Enable in-memory KG for testing |
| `USE_MOCK_LLM` | `0` | Use mock LLM (skeleton-only output) |

### Fallback Hierarchy

```
1. KG Templates (GraphRAG)         ← Production path
   ↓ (empty)
2. Legacy Templates (if enabled)   ← Backwards compatibility
   ↓ (empty)
3. In-Memory KG (if enabled)       ← Testing only
   ↓ (empty)
4. HTTP Method Inference           ← Dynamic fallback (M5 default)
```

---

## Test Coverage

| Test File | Coverage |
|-----------|----------|
| `test_align_task_with_kg.py` | Template matching, fallback behavior |
| `test_graphrag_integration.py` | KG learning cycle, template retrieval |
| `test_codegen_validation.py` | LLM refinement validation |
| `test_policy_inference.py` | Policy injection |
| `test_m4_generated_code_execution.py` | End-to-end code execution |

Run with:
```bash
USE_SQLITE=true USE_MOCK_LLM=true python -m pytest tests/test_align_task_with_kg.py -v
```

---

## Future Evolution

### Phase 1: Pattern-Level Templates (Planned)

Provider-agnostic templates for common CRUD operations:

```python
PATTERN_TEMPLATES = {
    "pattern.crud_create": {...},   # Any POST /resource
    "pattern.crud_read": {...},     # Any GET /resource/{id}
    "pattern.crud_list": {...},     # Any GET /resource
    "pattern.crud_update": {...},   # Any PUT/PATCH /resource/{id}
    "pattern.crud_delete": {...},   # Any DELETE /resource/{id}
}

# Provider templates inherit from patterns:
# stripe.create_payment_intent IMPLEMENTS pattern.crud_create
```

### Phase 2: Extended Step Types (Considered)

```python
# New step types for complex flows:
EXTENDED_STEP_TYPES = [
    "loop",          # Iterate over collection
    "parallel",      # Concurrent execution
    "sub_workflow",  # Nested workflow call
    "wait",          # Async/webhook waiting
    "cache_check",   # Cache lookup before API call
]
```

### Phase 3: DSL Migration (Long-term)

If template proliferation becomes problematic, consider migrating to explicit DSL:

```yaml
# .integration-coworker/workflows/create_payment.yaml
workflow: create_payment_intent
provider: stripe
version: 1
steps:
  - id: validate
    type: validation
    schema: PaymentIntentInput
  - id: create
    type: api_call
    endpoint: POST /v1/payment_intents
    mapping:
      amount: $.input.amount
      currency: $.input.currency
  - id: respond
    type: transform
    extract: [id, client_secret, status]
policies:
  auth: bearer
  retry: {strategy: exponential, max_attempts: 3}
```

---

## Implementation References

| Component | File | Key Function/Class |
|-----------|------|-------------------|
| Template retrieval | `graph/nodes/align_task_with_kg.py` | `_query_kg_templates()` |
| HTTP method inference | `graph/nodes/align_task_with_kg.py` | `_infer_workflow_from_endpoint()` |
| Legacy templates | `graph/nodes/align_task_with_kg.py` | `_LEGACY_WORKFLOW_TEMPLATES` |
| Code generation | `graph/nodes/generate_code_and_tests.py` | `generate_code_and_tests()` |
| LLM refinement | `graph/nodes/generate_code_and_tests.py` | `_refine_with_llm()` |
| Policy injection | `codegen/policy_templates.py` | `inject_policies_into_client_code()` |
| Template persistence | `graph/nodes/persist_kg_learning.py` | `persist_kg_learning()` |
| Domain models | `domain/models.py` | `WorkflowTemplate`, `IntegrationFlowNode` |

---

## Decision Outcome

**Accepted**. The Template-First approach provides the optimal balance of:

- **Predictability**: Same task → same code structure
- **Policy Compliance**: Injection points are architecturally defined
- **Knowledge Accumulation**: Templates persist to KG for reuse
- **Graceful Degradation**: Valid output even without LLM or templates
- **Explainability**: Workflow steps visible in plan output

The step type vocabulary and fallback hierarchy are documented. Evolution paths to pattern-level templates and DSL are identified for future scalability.

---

## Changelog

| Date | Author | Change |
|------|--------|--------|
| 2025-12-01 | Integration Coworker Team | Initial decision |

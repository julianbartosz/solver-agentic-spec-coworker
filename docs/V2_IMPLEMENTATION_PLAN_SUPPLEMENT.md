# V2 Implementation Plan Supplement

**Status**: Draft
**Date**: 2024-10-24
**Parent**: [V2_IMPLEMENTATION_PLAN.md](./V2_IMPLEMENTATION_PLAN.md)
**Context**: [V2_DOCUMENTATION_CORRECTION.md](./V2_DOCUMENTATION_CORRECTION.md)

## 1. Overview

This document provides the detailed technical specifications for the four critical gaps identified in the V2 Documentation Correction audit. These components are required to bring the codebase into full alignment with the V2 architecture goals.

**Scope:**
1.  **SEC-004**: Content Policy Enforcement (Semantic Safety)
2.  **KG-002/004**: Knowledge Graph Auto-Seeding
3.  **API-002**: Multi-Spec Source Reference Handling
4.  **LLM-003**: Unified LLM Mode Configuration

---

## 2. SEC-004: Content Policy Enforcement

**Problem**: The current `codegen/security.py` performs AST-based syntax checks (detecting `subprocess`, `eval`, etc.) but lacks *semantic* validation. It cannot detect "hallucinated" API endpoints, insecure credential handling (e.g., API keys in URLs), or logic that violates business rules.

**Goal**: Implement a `ContentPolicy` layer that validates generated code against the Silver API model and security best practices.

### 2.1 Implementation Specification

**New File**: `src/integration_coworker/llm/content_policy.py`

```python
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional
from agentic_integration.models_silver import SilverEndpoint

class PolicyViolationType(Enum):
    HALLUCINATED_ENDPOINT = "hallucinated_endpoint"
    INSECURE_CREDENTIAL_USAGE = "insecure_credential_usage"
    DATA_LEAKAGE = "data_leakage"
    BANNED_PATTERN = "banned_pattern"

@dataclass
class PolicyViolation:
    type: PolicyViolationType
    message: str
    line_number: Optional[int] = None
    severity: str = "high"  # high, medium, low

class ContentPolicyEnforcer:
    def __init__(self, valid_endpoints: List[SilverEndpoint]):
        self.valid_paths = {e.path for e in valid_endpoints}
        self.valid_methods = {e.method.upper() for e in valid_endpoints}

    def validate_code(self, code: str) -> List[PolicyViolation]:
        violations = []
        violations.extend(self._check_hallucinated_endpoints(code))
        violations.extend(self._check_credential_patterns(code))
        return violations

    def _check_hallucinated_endpoints(self, code: str) -> List[PolicyViolation]:
        # Regex or AST walk to find string literals looking like paths
        # Compare against self.valid_paths
        # Return violations if unknown path found in requests.* call
        pass

    def _check_credential_patterns(self, code: str) -> List[PolicyViolation]:
        # Check for "api_key=" in query params
        # Check for hardcoded secrets
        pass
```

**Refactoring Target**: `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

*   **Change**: Inject `ContentPolicyEnforcer` into the generation loop.
*   **Logic**:
    1.  Retrieve `SilverEndpoint` list from `state.silver_api`.
    2.  Instantiate `ContentPolicyEnforcer`.
    3.  After LLM generation and before `security.validate_code_security` (AST check), run `policy.validate_code`.
    4.  If violations found, feed back to LLM for self-correction (up to N retries).

### 2.2 Alternatives Analysis

| Approach | Pros | Cons | Decision |
| :--- | :--- | :--- | :--- |
| **A. Regex Heuristics** | Fast, deterministic, no token cost. | Brittle, high false positives/negatives. | **Hybrid (Selected)**: Use Regex for credentials, AST for structure, LLM for complex logic. |
| **B. LLM-as-Judge** | Can understand context ("is this PII?"). | Slow, expensive, non-deterministic. | Use only for complex policy checks (e.g. "is this logic sound?"). |
| **C. AST Only** | Robust parsing. | Hard to detect semantic intent (e.g. is this string a URL?). | Use for structural validation (imports, calls). |

**Selected Path**: Hybrid. Implement `ContentPolicyEnforcer` using AST to find HTTP calls, then validate the URL strings against the Silver model.

---

## 3. KG-002/004: Knowledge Graph Auto-Seeding

**Problem**: The `integration_gold` database starts empty. The system has no prior knowledge of common API patterns (e.g., "OAuth flow", "Pagination"), forcing the LLM to reinvent them every time.

**Goal**: Pre-populate the `integration_gold` tables (`Task`, `WorkflowTemplate`, `Node`, `Edge`) with high-quality, curated patterns upon database initialization.

### 3.1 Implementation Specification

**New File**: `src/integration_coworker/persistence/seed_kg.py`

```python
from sqlalchemy.orm import Session
from agentic_integration.db import IntegrationTask, WorkflowTemplate

def get_seed_templates() -> List[WorkflowTemplate]:
    return [
        WorkflowTemplate(
            name="Standard OAuth2 Authorization Code Flow",
            description="Secure flow for user delegation...",
            nodes=[...],
            edges=[...]
        ),
        WorkflowTemplate(
            name="Cursor-based Pagination",
            description="Iterate through pages using next_cursor...",
            nodes=[...],
            edges=[...]
        )
    ]

def seed_knowledge_graph(session: Session):
    templates = get_seed_templates()
    existing = session.query(WorkflowTemplate).count()
    if existing > 0:
        print("KG already seeded, skipping.")
        return
    
    for t in templates:
        session.add(t)
    session.commit()
    print(f"Seeded {len(templates)} KG templates.")
```

**Refactoring Target**: `src/integration_coworker/cli.py`

*   **Change**: Update the `init-db` command.
*   **Logic**:
    ```python
    @app.command()
    def init_db(recreate: bool = False):
        """Initialize database and seed Knowledge Graph."""
        init_db_schema(recreate=recreate)
        with get_session() as session:
            seed_knowledge_graph(session)
    ```

### 3.2 Alternatives Analysis

| Approach | Pros | Cons | Decision |
| :--- | :--- | :--- | :--- |
| **A. JSON/YAML Files** | Decoupled data from code. Easy to edit. | Requires loader logic, schema validation at runtime. | **Rejected** for V2 (complexity). |
| **B. Python Code (Hardcoded)** | Type-safe, easy to refactor, no parsing. | "Magic data" in code. | **Selected**: Simplest for V2. Move to JSON in V3. |
| **C. Dynamic Fetch** | Always up to date. | External dependency, network risk. | **Rejected**. |

---

## 4. API-002: Multi-Spec Source Reference Handling

**Problem**: The `WorkflowState` has a `source_refs` field, but `ingest_spec.py` ignores it, and `build_silver_api_model.py` does not link parsed entities back to their specific source. This makes multi-provider integrations (e.g., "Sync GitHub Issues to Jira") impossible to model correctly.

**Goal**: Ensure every Silver entity (Endpoint, Schema) is traceable to a specific `SourceRef` (file or URL).

### 4.1 Implementation Specification

**Refactoring Target 1**: `src/integration_coworker/graph/nodes/ingest_spec.py`

*   **Logic**:
    1.  Iterate `state.spec_refs` (list of paths/URLs).
    2.  For each `ref`, create a `SourceRef` object (id, uri, type).
    3.  Store these in `state.source_refs`.
    4.  Pass `source_ref.id` downstream along with the raw content.

**Refactoring Target 2**: `src/integration_coworker/graph/nodes/build_silver_api_model.py`

*   **Change**: Update `_extract_from_spec` signature.
*   **Logic**:
    ```python
    def build_silver_api_model(state: WorkflowState) -> WorkflowState:
        silver_model = SilverAPIModel()
        
        # Iterate over INGESTED docs, which should be paired with SourceRefs
        for doc, source_ref in zip(state.spec_documents, state.source_refs):
            partial_model = parse_openapi(doc, source_id=source_ref.id)
            silver_model.merge(partial_model)
            
        return state.update(silver_api=silver_model)
    ```

### 4.2 Alternatives Analysis

| Approach | Pros | Cons | Decision |
| :--- | :--- | :--- | :--- |
| **A. Merge at Ingestion** | Single "Mega-Spec" passed to parser. | Naming collisions, loss of provenance. | **Rejected**. |
| **B. Merge in Silver** | Keep specs distinct during parse, merge in domain model. | Requires robust merging logic (deduplication). | **Selected**: Preserves traceability. |

---

## 5. LLM-003: Unified LLM Mode Configuration

**Problem**: The codebase mixes `settings.llm.use_mock` (boolean) with various ad-hoc checks. This prevents advanced modes like "Record/Replay" (for regression testing) or "Hybrid" (Mock for easy tasks, Real for hard ones).

**Goal**: Centralize LLM behavior control into a single Enum.

### 5.1 Implementation Specification

**New File**: `src/integration_coworker/config/llm_mode.py`

```python
from enum import Enum

class LLMMode(str, Enum):
    REAL = "real"       # Call OpenAI/Anthropic
    MOCK = "mock"       # Return static strings
    RECORD = "record"   # Call Real, save to disk
    REPLAY = "replay"   # Read from disk, fail if missing
```

**Refactoring Target 1**: `src/integration_coworker/config/__init__.py`

*   **Change**: Replace `use_mock: bool` with `mode: LLMMode`.
*   **Migration**: Map `USE_MOCK_LLM=true` env var to `LLMMode.MOCK`.

**Refactoring Target 2**: `src/integration_coworker/llm/client.py`

*   **Change**:
    ```python
    def get_completion(prompt: str, ...):
        mode = settings.llm.mode
        
        if mode == LLMMode.MOCK:
            return _get_mock_completion(prompt)
        elif mode == LLMMode.REPLAY:
            return _get_replayed_completion(prompt)
        
        response = _call_provider(prompt)
        
        if mode == LLMMode.RECORD:
            _save_interaction(prompt, response)
            
        return response
    ```

### 5.2 Alternatives Analysis

| Approach | Pros | Cons | Decision |
| :--- | :--- | :--- | :--- |
| **A. Boolean Flags** | Simple (`use_mock`, `use_cache`). | Combinatorial explosion (`use_mock=True` + `record=True`??). | **Rejected**. |
| **B. Strategy Pattern** | Cleanest OO design (`LLMProvider` interface). | More boilerplate to wire up. | **Selected (via Enum)**: Enum drives the strategy selection in the factory/client. |

---

## 6. Execution Order

1.  **LLM-003**: Foundation. Safe to change first.
2.  **KG-002**: Independent. Can be done anytime.
3.  **API-002**: Affects core workflow data structures. High risk. Do carefully.
4.  **SEC-004**: Depends on stable Silver model (post-API-002). Do last.

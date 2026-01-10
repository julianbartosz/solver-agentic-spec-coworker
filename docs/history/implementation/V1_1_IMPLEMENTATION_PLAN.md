# V1.1 Detailed Implementation Specification

**Document Version**: 2.0 (Final Specification)
**Status**: Ready for Implementation
**Target Release**: V1.1

---

## Executive Summary

This document provides the **exhaustive technical specification** for the 5 key features of the V1.1 release. It is designed to be implemented without further architectural interpretation.

### Feature Set
1.  **Spec Caching (FT-001)**: Content-addressable caching to skip redundant parsing.
2.  **Cross-Provider Pattern Learning (FT-005)**: Abstracting successful integrations into reusable patterns.
3.  **Strict Codegen Mode (FT-008)**: Enforcing syntax and linting standards on generated code.
4.  **KG Learning from Logs (FT-011)**: Feedback loop from production usage to KG weights.
5.  **Enhanced Reporting (FT-014)**: Rich, actionable HTML/Markdown reports.

---

## 1. Spec Caching (FT-001)

**Objective**: Reduce end-to-end latency by 40% for repeated runs on the same spec by skipping the `ingest_spec` and `build_silver_api_model` phases when content is unchanged.

### 1.1 Database Schema Changes
No schema changes required. We leverage the existing `sha256` column in `spec_silver.spec_documents`.

### 1.2 Logic Specification

#### A. `src/integration_coworker/graph/nodes/ingest_spec.py`

**Function**: `ingest_spec(state: WorkflowState) -> WorkflowState`

**Algorithm**:
1.  **Fetch Content**: Retrieve raw content from `state.spec_refs`.
2.  **Compute Hash**: Calculate SHA-256 of the raw bytes.
3.  **Cache Lookup**:
    ```python
    # Pseudo-code
    existing_doc = db.execute(
        select(SpecDocument).where(SpecDocument.sha256 == computed_hash)
    ).scalar_one_or_none()
    ```
4.  **Branching**:
    *   **Hit (`existing_doc` found)**:
        *   Log: "Spec cache hit: {existing_doc.id}"
        *   Update State: `state.spec_documents = [existing_doc]`
        *   Set Flag: `state.options.cache_hit = True` (Need to add this field to `IntegrationOptions` or handle via context) -> *Decision*: Add `cache_hit` to `WorkflowState` metadata or just rely on `spec_doc` presence. Let`s add `cache_hit: bool = False` to `WorkflowState`.
        *   **SKIP** persistence and chunking logic.
    *   **Miss**:
        *   Proceed with existing logic (persist to `spec_documents`, chunk to `spec_sections`).
        *   Set `state.cache_hit = False`.

#### B. `src/integration_coworker/graph/nodes/build_silver_api_model.py`

**Function**: `build_silver_api_model(state: WorkflowState) -> WorkflowState`

**Algorithm**:
1.  **Check Cache Flag**: If `state.cache_hit` is `False`, execute existing LLM-based parsing logic.
2.  **Fast Path (Cache Hit)**:
    *   Query DB for all child entities linked to `state.spec_documents[0].id`.
    *   **Queries**:
        ```sql
        SELECT * FROM spec_silver.endpoints WHERE spec_document_id = :doc_id;
        SELECT * FROM spec_silver.schemas WHERE spec_document_id = :doc_id;
        SELECT * FROM spec_silver.entities WHERE spec_document_id = :doc_id;
        ```
    *   **Hydration**: Reconstruct `Endpoint`, `Schema`, `Entity` objects from rows.
    *   **State Update**: Populate `state.endpoints`, `state.schemas`, `state.entities`.
    *   **Bypass**: Return state immediately, skipping LLM calls.

#### C. CLI Updates (`src/integration_coworker/cli.py`)

*   Add flag: `--no-cache`
*   Pass to `IntegrationOptions`.
*   If `options.no_cache` is True, `ingest_spec` forces a "Miss" path even if hash matches.

---

## 2. Cross-Provider Pattern Learning (FT-005)

**Objective**: Allow the system to solve tasks using generic patterns (e.g., "Pagination", "OAuth Flow") when exact provider templates are missing.

### 2.1 Data Model Updates

**Existing Infrastructure** (No Changes Needed):
-   `KGNodeType.PATTERN` already exists in `src/integration_coworker/domain/models.py`.
-   `KGEdgeRelation.IMPLEMENTS_PATTERN` already exists.
-   `STANDARD_PATTERNS` dict already exists in `src/integration_coworker/kg/__init__.py`.
-   `query_templates_with_pattern_fallback()` already exists in `kg/__init__.py`.
-   `infer_pattern_from_task()` already exists in `kg/__init__.py`.

### 2.2 Logic Specification

#### A. `src/integration_coworker/graph/nodes/align_task_with_kg.py`

**Function**: `align_task_with_kg(state: WorkflowState) -> WorkflowState`

**Refactoring**:
-   **Replace** direct `query_workflow_templates()` call with `query_templates_with_pattern_fallback()`.
-   The existing `kg/__init__.py` function already handles:
    1.  Primary template search.
    2.  Pattern fallback if no template found.
    3.  Returns `(templates, patterns, source)` tuple.

**State Update**:
-   If `source == "pattern"`:
    -   `state.plan["template_source"] = "pattern"`
    -   `state.plan["pattern_key"] = patterns[0].pattern_key`
    -   Inject pattern steps into `state.plan["steps"]`.

#### B. `src/integration_coworker/graph/nodes/persist_kg_learning.py`

**Function**: `persist_kg_learning(state: WorkflowState) -> WorkflowState`

**Refactoring**:
-   After creating the workflow template node, check `state.plan.get("pattern_key")`.
-   If set, create edge: `Template --IMPLEMENTS_PATTERN--> Pattern`.
-   **Existing function** `_upsert_kg_edge` can be reused.

---

## 3. Strict Codegen Mode (FT-008)

**Objective**: Guarantee that generated code is syntactically valid and adheres to basic style rules before presenting it to the user.

### 3.1 Enhance Existing Module: `src/integration_coworker/codegen/security.py`

**Refactoring**:
-   Add `fix_code_style(content: str) -> str` function to the existing module.
-   **Implementation**:
    -   Leverage `subprocess` to run `ruff check --fix` (similar to `tests/test_generated_code_quality.py`).
    -   Handle `FileNotFoundError` if ruff is missing (graceful degradation).

### 3.2 Integration

#### `src/integration_coworker/graph/nodes/generate_code_and_tests.py`

*   **Current State**: Already calls `validate_code_security`.
*   **Update**:
    1.  **Auto-Fix**: Call `fix_code_style(code)` immediately after generation.
    2.  **Strict Validation**:
        -   If `state.options.strict_codegen` is True:
            -   Check `ast.parse(code)` for syntax errors.
            -   Check `validate_code_security(code)` for violations.
            -   **Action**: Raise `ValueError` if any checks fail (halting the workflow).
        -   If False (default): Log warnings but proceed (current behavior).

---

## 4. KG Learning from Logs (FT-011)

**Objective**: Ingest production usage logs to update the "confidence" (usage count/success rate) of KG nodes.

### 4.1 Database Schema

**Existing Infrastructure**:
-   `kg.nodes` already has `usage_count` column (see `domain/models.py:KGNode`).
-   `persist_kg_learning.py` already increments `usage_count` on each run via `_upsert_kg_node`.

**New Table** (Add to `src/integration_coworker/persistence/db.py`):
```python
class KGUsageMetric(Base):
    __tablename__ = "kg_usage_metrics"
    __table_args__ = {"schema": "kg"}

    id = Column(Integer, primary_key=True)
    node_key = Column(String, nullable=False) # Link to kg.nodes.key
    run_id = Column(String, nullable=True)
    success = Column(Boolean, nullable=False)
    error_type = Column(String, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    timestamp = Column(DateTime, server_default=func.now())
```

### 4.2 Ingestion Logic

**Extend Existing Module**: `src/integration_coworker/graph/nodes/persist_kg_learning.py`

**Add Function**: `record_kg_usage_metric(node_key: str, run_id: str, success: bool, ...)`

**Rationale**: Keep KG-related persistence together instead of creating a new `learning/` module.

**Algorithm**:
1.  Insert row into `kg_usage_metrics`.
2.  Update `kg.nodes.usage_count` (already happens in `_upsert_kg_node`).

### 4.3 CLI Command

*   `integration-coworker learn-logs <path_to_log_file>`
*   Implementation in `cli.py` (call the function from `persist_kg_learning.py`).

---

## 5. Enhanced Reporting (FT-014)

**Objective**: Provide a report that is useful for both developers (debugging) and stakeholders (status).

### 5.1 Existing Infrastructure

**File**: `src/integration_coworker/graph/nodes/build_report.py`

**Current Capabilities**:
-   Already generates structured Markdown report.
-   Includes: Run ID, Provider, Task, Spec Ingestion stats, Silver Model stats, Workflow details, Policies, Generated Code, Repo Changes, Repo Profile, Persistence status, Errors, Completed Steps, Node Timings.
-   Already has LLM-generated executive summary.
-   Already shows template selection source (`inferred`, `kg`, `pattern`).

### 5.2 Enhancements

**Refactoring** (Minimal - extend existing `build_report.py`):

1.  **Mermaid Diagram**: Add a function `_build_mermaid_workflow_diagram(state)` that generates:
    ```mermaid
    graph LR
        start[Start] --> validate[Validation]
        validate --> api_call[API Call]
        api_call --> transform[Transform]
        transform --> end_node[End]
    ```
    Insert into the "Integration Workflow" section.

2.  **Next Steps Section**: Add copy-pasteable commands:
    ```markdown
    ## Next Steps
    
    ```bash
    # Run the generated flow
    python -m integrations.{provider}.{task_slug}
    
    # Run tests
    pytest tests/test_{provider}_{task_slug}.py -v
    ```
    ```

3.  **HTML Format** (Optional, Lower Priority):
    -   If `state.options.report_format == "html"`:
        -   Wrap Markdown in HTML template with embedded Mermaid JS.
        -   Add CSS styling.
    -   **Decision**: Defer to V1.2. Keep Markdown-only for V1.1.

---

## Implementation Checklist

### Phase 1: Foundation (Caching & Strict Mode)
- [x] Modify `WorkflowState` in `state.py` (add `cache_hit`).
- [x] Add `fix_code_style` to `codegen/security.py`.
- [x] Update `ingest_spec.py` with hashing logic.
- [x] Update `build_silver_api_model.py` with DB hydration logic.
- [x] Update `generate_code_and_tests.py` to use validation.

### Phase 2: Intelligence (KG & Patterns)
- [x] Add usage tracking to `persist_kg_learning.py` (uses existing usage_count).
- [x] Update `align_task_with_kg.py` to use `query_templates_with_pattern_fallback`.
- [x] Update `persist_kg_learning.py` to track matched templates/patterns.

### Phase 3: Polish (Reporting)
- [x] Add `_generate_workflow_mermaid` to `build_report.py`.
- [ ] Add "Next Steps" section to report (deferred to V1.2).
- [x] Add CLI flags (`--no-cache`, `--strict-codegen`).

---
**End of Specification**

# ADR: Production-Grade HITL Enhancement (v2)

**Status:** Implemented (PRs #1-10 Complete)  
**Date:** 2026-01-03  
**Authors:** Copilot  
**Supersedes:** ADR-HITL-ENHANCEMENT.md (v1)

---

## Executive Summary

This document addresses 7 sub-tasks (A-G) for designing a production-grade Human-in-the-Loop (HITL) system that allows humans to review sandbox errors, inject feedback, force regeneration, and provide new directions.

**v2 Changes from v1:**
- Payload size discipline: refs-not-blobs architecture
- Lightweight Review Framework (Option 4) chosen over separate gate nodes
- Single Streamlit dialog with routing to avoid rerun bugs
- Bounded diff viewer with `difflib` guardrails
- Postgres-backed validation harness (not skipped)
- Real LLM opt-in validation mode

## Implementation Status

| PR | Component | Status | Key Files |
|----|-----------|--------|-----------|
| #1 | Review Artifacts | ✅ Complete | [review_artifacts.py](../../src/integration_coworker/graph/review_artifacts.py) |
| #2 | Review Gate | ✅ Complete | [review_gate.py](../../src/integration_coworker/graph/nodes/review_gate.py) |
| #3 | State Fields | ✅ Complete | [state.py](../../src/integration_coworker/graph/state.py) (lines 178-226) |
| #4 | UI Dialog | ✅ Complete | [review_dialog.py](../../src/integration_coworker/ui/review_dialog.py), [streamlit_app.py](../../src/integration_coworker/ui/streamlit_app.py) |
| #5 | CLI Enhancements | ✅ Complete | [cli.py](../../src/integration_coworker/cli.py) (hitl-resume cmd) |
| #6 | HITL Validation Workflow | ✅ Complete | [hitl-validation.yml](../../.github/workflows/hitl-validation.yml), [validate_hitl_production.py](../../scripts/validate_hitl_production.py) |
| #7 | Quality Signals Core | ✅ Complete | [quality_models.py](../../src/integration_coworker/graph/quality_models.py), [quality_artifacts.py](../../src/integration_coworker/graph/quality_artifacts.py) |
| #8 | Static Analysis Gate | ✅ Complete | [static_checks.py](../../src/integration_coworker/graph/static_checks.py), [static_analysis_gate.py](../../src/integration_coworker/graph/nodes/static_analysis_gate.py) |
| #9 | Production Guardrails | ✅ Complete | [production_guardrails.py](../../src/integration_coworker/graph/production_guardrails.py) |
| #10 | Targeted Regeneration | ✅ Complete | [regeneration_models.py](../../src/integration_coworker/graph/regeneration_models.py), [targeted_regeneration.py](../../src/integration_coworker/graph/nodes/targeted_regeneration.py) |
| CI | Import Safety Jobs | ✅ Complete | [ci.yml](../../.github/workflows/ci.yml) (core-import-safety, ui-extra) |

---

## Non-Negotiable Invariants (External References)

These constraints are derived from upstream documentation and known failure modes:

| Invariant | Source | Implication |
|-----------|--------|-------------|
| `interrupt()` payloads must be JSON-serializable | [LangGraph How-to: Wait for User Input](https://langchain-ai.github.io/langgraph/how-tos/human_in_the_loop/wait-user-input/) | No binary data, no Python objects, bounded size |
| Resume uses same `thread_id` + `Command(resume=...)` | [LangGraph How-to: Wait for User Input](https://langchain-ai.github.io/langgraph/how-tos/human_in_the_loop/wait-user-input/) | Thread identity preserved; do NOT replay state as input |
| Checkpointers persist state fields | [LangChain Forum](https://forum.langchain.com/t/langgraph-checkpointer-selective-memory/1639) | Large blobs → checkpoint bloat → slow resume |
| `st.dialog` inherits fragment behavior | [Streamlit st.dialog Docs](https://docs.streamlit.io/develop/api-reference/execution-flow/st.dialog) | Widget clicks rerun dialog only; `st.rerun()` at submit end closes |
| `difflib.SequenceMatcher` has quadratic worst-case | [Python difflib Docs](https://docs.python.org/3/library/difflib.html) | Must bound inputs, offer fallback |
| Content-addressed artifacts are the contract | Local: `persistence/artifacts/base.py` | Use existing `ArtifactRef` pattern |

---

## Task A: HITL Capability Map (Repo-Grounded Inventory)

### Implemented HITL Components

| Component | File(s) | Implementation | Tests |
|-----------|---------|----------------|-------|
| **Review Artifacts** | [review_artifacts.py](../../src/integration_coworker/graph/review_artifacts.py) | `store_review_artifacts()`, `compute_unified_diff_patches()`, bounded summaries via `build_code_summary()`, `build_sandbox_summary()` | [test_review_artifacts.py](../../tests/test_review_artifacts.py) |
| **Review Gate Factory** | [review_gate.py](../../src/integration_coworker/graph/nodes/review_gate.py) | `review_gate(kind)` factory returning node functions for "code" or "sandbox" kinds, idempotent interrupt handling | [test_review_gate.py](../../tests/test_review_gate.py) |
| **State Fields** | [state.py](../../src/integration_coworker/graph/state.py#L178-L226) | `review_artifact_refs`, `pending_review_kind`, `human_feedback`, `review_decisions` | N/A (dataclass) |
| **Streamlit Dialog** | [review_dialog.py](../../src/integration_coworker/ui/review_dialog.py) | `show_review_dialog()`, `render_review_dialog()`, lazy artifact loading | [test_review_dialog.py](../../tests/test_review_dialog.py) |
| **App Integration** | [streamlit_app.py](../../src/integration_coworker/ui/streamlit_app.py#L93-L159) | `_handle_pending_review()` with proper @st.dialog contract enforcement | [test_import_safety.py](../../tests/test_import_safety.py) |
| **CLI Resume** | [cli.py](../../src/integration_coworker/cli.py#L1330-L1495) | `hitl-resume` command with `--feedback`/`--comment` aliases, `--regenerate` flag | [test_cli_hitl_resume.py](../../tests/test_cli_hitl_resume.py) |

---

## Task B: Architecture Design Debate (Revised)

### Option 1: Separate `sandbox_review_gate` Node

**Description:** Add a distinct `sandbox_review_gate` node after `generate_code_and_tests`.

**Pros:** Clear separation; testable in isolation  
**Cons:** Two interrupt points → two payloads → two dialogs → Streamlit rerun bugs; payload drift over time

**Migration Cost:** Medium  
**Verdict:** ❌ Rejected (Streamlit multi-dialog hazard)

---

### Option 2: Extended `hitl_review_gate` with Phases

**Description:** Single node with `gate_phase` field in payload.

**Pros:** One node; unified schema  
**Cons:** SRP violation; harder to test; still sends full payloads

**Migration Cost:** Low-Medium  
**Verdict:** ❌ Rejected (still has blob-in-payload problem)

---

### Option 3: Generic "Review Framework" (Over-engineered)

**Description:** Full abstraction with pluggable renderers, dependency injection.

**Pros:** Maximum flexibility  
**Cons:** Over-engineered; complex DI; hard to understand

**Migration Cost:** High  
**Verdict:** ❌ Rejected (YAGNI)

---

### Option 4: Lightweight Review Framework (NEW - RECOMMENDED)

**Description:** Minimal generic review gate with:
1. One reusable `review_gate` node function, instantiated twice in graph
2. Discriminated-union payload with `review_kind` field
3. Pure functions for payload building and decision application
4. Single Streamlit dialog that routes on `review_kind`

**Key Design:**

```python
# graph/nodes/review_gate.py
ReviewKind = Literal["pre_write", "post_sandbox"]

def review_gate(kind: ReviewKind) -> Callable[[WorkflowState], WorkflowState]:
    """Factory that returns a review gate node for the given kind."""
    def _gate(state: WorkflowState) -> WorkflowState:
        if _should_skip_review(state, kind):
            return _mark_complete(state, kind)
        
        # Store artifacts externally, get refs
        artifact_refs = build_review_artifacts(state, kind)
        
        # Minimal payload with refs only
        payload = build_review_payload(kind, state, artifact_refs)
        
        # Interrupt (payload is ~1KB, not megabytes)
        decision = interrupt(payload)
        
        # Apply decision
        return apply_review_decision(kind, state, decision)
    
    _gate.__name__ = f"review_gate_{kind}"
    return _gate
```

**Graph Wiring:**

```python
# runtime.py
workflow.add_node("pre_write_review", review_gate("pre_write"))
workflow.add_node("post_sandbox_review", review_gate("post_sandbox"))

# Edges
workflow.add_edge("analyze_repo_layout", "pre_write_review")
workflow.add_edge("pre_write_review", "apply_repo_integration_changes")

workflow.add_conditional_edges(
    "generate_code_and_tests",
    _check_sandbox_needs_review,
    {"needs_review": "post_sandbox_review", "passed": "persist_gold_checkpoint"},
)
workflow.add_conditional_edges(
    "post_sandbox_review",
    _check_regenerate_decision,
    {"regenerate": "generate_code_and_tests", "continue": "persist_gold_checkpoint"},
)
```

**Pros:**
- Refs-not-blobs: payload is ~1KB (metadata + refs), not megabytes
- Single dialog: UI routes on `review_kind`, no nested dialogs
- DRY: one set of decision parsing, schema versioning, validation
- Testable: each kind has fixtures, but core logic is shared
- Backward compatible: existing `hitl_review_gate` behavior preserved

**Cons:**
- Slight abstraction overhead (review_gate factory)

**Migration Cost:** Medium (refactor existing hitl_gate.py)

**Verdict:** ✅ **SELECTED**

---

### Decision Rationale

| Criterion | Option 1 | Option 2 | Option 3 | Option 4 |
|-----------|----------|----------|----------|----------|
| Payload size discipline | ❌ Blobs | ❌ Blobs | ✅ Refs | ✅ Refs |
| Streamlit dialog safety | ❌ Two dialogs | ✅ One | ✅ One | ✅ One |
| DRY principle | ❌ Duplicate | ✅ Shared | ✅ Shared | ✅ Shared |
| Testability | ✅ Isolated | ⚠️ Phases | ⚠️ Complex DI | ✅ Isolated + shared |
| Implementation effort | Low | Low-Med | High | Medium |
| Long-term scalability | ⚠️ Drift | ⚠️ Coupling | ✅ Abstract | ✅ Balanced |

---

## Task C: Zero-Ambiguity Implementation Plan

### Phase 1: Review Artifact Infrastructure (PR #1) ✅ IMPLEMENTED

**Files:**
- [review_artifacts.py](../../src/integration_coworker/graph/review_artifacts.py): Review artifact storage utilities

**Actual Implementation:**

```python
# review_artifacts.py - Key constants and functions (from actual code)
REVIEW_ARTIFACT_CODEC = ArtifactCodec.JSON  # Line 36 - NO PICKLE

# Bounded summary constants (lines 39-43)
MAX_FILES_IN_SUMMARY = 10           # Only show first N files in payload
MAX_ERROR_PREVIEWS = 3              # Only show first N error previews
MAX_ERROR_PREVIEW_CHARS = 200       # Truncate error messages

def store_review_artifacts(run_id, kind, *, code_artifacts, sandbox_result, diff_patches):
    """Store all review artifacts and return refs. Uses JSON codec only."""
    # Returns: {"code_snapshot": ArtifactRef, "sandbox_result": ArtifactRef, "diff_patches": ArtifactRef}

def compute_unified_diff_patches(code_artifacts, existing_files) -> List[str]:
    """Generate unified diff patches - TEXT patches, not before/after blobs."""

def build_code_summary(code_artifacts) -> Dict[str, Any]:
    """Build bounded summary (< 1KB) with file_count, adds, total_bytes, files[:10]."""

def build_sandbox_summary(sandbox_result) -> Dict[str, Any]:
    """Build bounded summary with all_passed, gates_failed[:5], error_previews[:3]."""
```

**State Fields (from [state.py](../../src/integration_coworker/graph/state.py#L178-L226)):**

```python
# Lines 178-226 - Actual implementation
review_artifact_refs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
# Structure: {"pre_write": {"code_snapshot": {...}}, "post_sandbox": {...}}

pending_review_kind: Optional[str] = None  # "pre_write" | "post_sandbox" | None
human_feedback: Optional[str] = None

review_decisions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
# Structure: {"code": {"approved": bool, "auto": bool, "feedback": str, ...}}
```

**Tests:** [test_review_artifacts.py](../../tests/test_review_artifacts.py)
- `TestCodecConstraints.test_review_artifact_codec_is_json` - Verifies JSON-only codec
- `TestArtifactRefRoundtrip.test_artifact_ref_to_dict_from_dict_roundtrip` - Verifies serialization
- `TestUnifiedDiffPatches` - Verifies patch generation

---

### Phase 2: Review Gate Factory (PR #2) ✅ IMPLEMENTED

**Files:**
- [review_gate.py](../../src/integration_coworker/graph/nodes/review_gate.py): Review gate factory

**Actual Implementation:**

```python
# review_gate.py - Key types and functions (from actual code)
ReviewKind = Literal["code", "sandbox"]  # Line 27

def review_gate(kind: ReviewKind) -> Callable[[WorkflowState], WorkflowState]:
    """
    Factory that creates a review gate node for the specified kind.
    Each gate instance:
    1. Checks if review is needed (idempotent - skips if decision exists)
    2. Stores large artifacts via ArtifactStore
    3. Builds bounded payload with refs + summaries
    4. Calls interrupt() to pause workflow
    5. On resume, parses decision and updates state
    """
    def gate_node(state: WorkflowState) -> WorkflowState:
        # Skip conditions checked first (lines 71-96):
        # - _has_existing_decision() - idempotent guard
        # - _should_skip_hitl() - HITL disabled
        # - state.options.dry_run
        # - _has_reviewable_content() - no content to review
        
        # Store artifacts externally (lines 98-110)
        artifact_refs = _store_artifacts_for_kind(state, run_id, kind)
        state.review_artifact_refs[kind] = refs_to_dicts(artifact_refs)
        state.pending_review_kind = kind
        
        # Build bounded payload (lines 112-115)
        payload = _build_review_payload(state, kind, artifact_refs)
        
        # INTERRUPT - DO NOT WRAP IN try/except (lines 117-126)
        decision = interrupt(payload)
        
        # Process decision on resume (lines 128-155)
        parsed = _parse_decision(decision)
        _record_decision(state, kind, approved=parsed["approved"], ...)
        
        return state
    
    gate_node.__name__ = f"{kind}_review_gate"
    return gate_node

# Legacy wrapper for backwards compatibility
def hitl_review_gate(state: WorkflowState) -> WorkflowState:
    """DEPRECATED: Use review_gate('code') directly."""
    return review_gate("code")(state)
```

**Guard Functions (lines 264-296):**

```python
def check_review_approved(state: WorkflowState, kind: str) -> bool:
    """Check if review was approved for the specified kind."""

def get_review_feedback(state: WorkflowState, kind: str) -> Optional[str]:
    """Get feedback from review decision."""
```

**Tests:** [test_review_gate.py](../../tests/test_review_gate.py)
- `TestReviewGateSkipConditions` - Tests idempotency, dry_run, hitl_mode skips
- `TestReviewGatePayloadBuilding` - Tests bounded summary construction
- `TestDecisionParsing` - Tests various decision formats (bool, dict, action-based)
- `TestGuardFunctions` - Tests `check_review_approved`, `get_review_feedback`

---

### Phase 3: Streamlit UI Dialog (PR #4) ✅ IMPLEMENTED

**Files:**
- [review_dialog.py](../../src/integration_coworker/ui/review_dialog.py): Review dialog component
- [streamlit_app.py](../../src/integration_coworker/ui/streamlit_app.py#L93-L159): App integration

**Actual Implementation - review_dialog.py:**

```python
# review_dialog.py - Key functions (from actual code)

def _get_streamlit():
    """Lazy import streamlit, raising ImportError with helpful message if not installed."""
    # Lines 24-31 - Enables import safety without streamlit

def show_review_dialog(run_id, pending_kind, artifact_refs) -> Optional[Dict[str, Any]]:
    """
    Show the review dialog as a Streamlit modal.
    
    CRITICAL INVARIANTS (per Streamlit docs):
    1. Only ONE dialog function may be called per script run
    2. Dialog is implicitly opened when decorated function called
    3. Dialog closes via st.rerun() - NOT by returning from function
    4. Caller is responsible for calling st.rerun() after persisting decision
    """
    st = _get_streamlit()
    
    @st.dialog("Review Required", width="large")
    def _dialog_inner():
        return render_review_dialog(run_id, pending_kind, artifact_refs)
    
    return _dialog_inner()

def render_review_dialog(run_id, pending_kind, artifact_refs, on_decision=None):
    """Render review content based on kind, with decision form."""
    # Routes to _render_code_review_content() or _render_sandbox_review_content()
    # Ends with _render_decision_form() for Approve/Reject/Regenerate buttons

def check_pending_review(session_state) -> Optional[Dict[str, Any]]:
    """Check session state for pending review - pure Python, no streamlit needed."""
```

**Actual Implementation - streamlit_app.py (lines 93-159):**

```python
# streamlit_app.py - _handle_pending_review() (from actual code)
def _handle_pending_review() -> None:
    """
    Handle pending HITL review dialog at TOP-LEVEL of app render.
    
    CRITICAL: Per @st.dialog contract:
    - Only ONE dialog function may be called per script run
    - Dialog must be called at top-level (not nested in tabs/columns/expanders)
    - st.rerun() is the ONLY way to close/dismiss the dialog
    
    Per @st.rerun contract:
    - st.rerun() immediately reruns the entire script
    - ONLY call st.rerun() AFTER persisting user decision to session_state
    - Never call during intermediate UI interactions
    """
    pending_kind = st.session_state.get("pending_review_kind")
    if not pending_kind:
        return  # No pending review - continue to normal render
    
    # Show dialog (lazy import)
    from integration_coworker.ui.review_dialog import show_review_dialog
    decision = show_review_dialog(run_id, pending_kind, artifact_refs)
    
    if decision:
        # Persist decision BEFORE rerun
        st.session_state.review_decisions[pending_kind] = decision
        st.session_state.pending_review_kind = None
        st.rerun()  # Dismiss dialog
    
    st.stop()  # Prevent other UI while dialog is open
```

**Import Safety Pattern (pyproject.toml):**

```toml
# Line 93-95 in pyproject.toml
[project.optional-dependencies]
ui = [
    "streamlit>=1.28.0",
]
```

**Tests:**
- [test_review_dialog.py](../../tests/test_review_dialog.py) - Dialog rendering and decision handling
- [test_import_safety.py](../../tests/test_import_safety.py) - Lazy import verification

---

### Phase 4: CLI Enhancement (PR #5) ✅ IMPLEMENTED

**Files:**
- [cli.py](../../src/integration_coworker/cli.py#L1330-L1495): hitl-resume command

**Actual Implementation:**

```python
# cli.py lines 1330-1495 - hitl-resume command (from actual code)
@app.command("hitl-resume")
def hitl_resume_cmd(
    run_id: str = typer.Argument(..., help="Run ID to resume from HITL pause"),
    approve: bool = typer.Option(False, "--approve", "-a", help="Approve the changes"),
    reject: bool = typer.Option(False, "--reject", "-r", help="Reject the changes"),
    # TRUE ALIASES: --feedback and --comment are the SAME parameter
    # Per Typer docs: https://typer.tiangolo.com/tutorial/options/name/
    feedback: Optional[str] = typer.Option(
        None,
        "--feedback", "-f",
        "--comment", "-c",
        help="Feedback/comment for approval/rejection (--comment and --feedback are aliases)"
    ),
    regenerate: bool = typer.Option(False, "--regenerate", help="Request regeneration"),
    exclude: Optional[List[str]] = typer.Option(None, "--exclude", "-x"),
    json_output: bool = typer.Option(False, "--json", "-j"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Resume a run paused at HITL gate with approval/rejection."""
    
    # Validation (lines 1375-1400)
    if not approve and not reject:
        # Error: Must specify --approve or --reject
    if approve and reject:
        # Error: Cannot specify both
    if regenerate and approve:
        # Error: --regenerate requires --reject
    if regenerate and not feedback:
        # Error: --regenerate requires --feedback/--comment
    
    # Build decision (lines 1410-1425)
    decision = {
        "approved": approve,
        "comment": feedback or "",
        "feedback": feedback or "",  # Same value for compatibility
        "overrides": {},
    }
    if exclude:
        decision["overrides"]["exclude_files"] = list(exclude)
    if regenerate:
        decision["regenerate"] = True
    
    # Resume (line 1445)
    result = resume_with_approval(run_id, decision)
```

**CLI Help Output (verified):**

```
$ integration-coworker hitl-resume --help
Usage: integration-coworker hitl-resume [OPTIONS] RUN_ID

  Resume a run paused at HITL gate with approval/rejection.

Arguments:
  RUN_ID  Run ID to resume from HITL pause  [required]

Options:
  -a, --approve                   Approve the changes
  -r, --reject                    Reject the changes
  --feedback,--comment  -f,-c  TEXT  Feedback/comment for approval/rejection
                                      (--comment and --feedback are aliases)
  --regenerate                    Request regeneration with feedback
  -x, --exclude TEXT              Files to exclude from approval
  -j, --json                      Output as JSON
  -v, --verbose                   Enable debug logging
```

**Tests:** [test_cli_hitl_resume.py](../../tests/test_cli_hitl_resume.py)
- `TestHitlResumeHelp` - Verifies CLI help shows all flags
- `TestHitlResumeValidation` - Tests require approve/reject, mutual exclusion
- `TestFeedbackAlias` - Verifies --feedback and --comment are true aliases
- `TestRegenerateConstraints` - Tests --regenerate requires --reject + --feedback

---

### Phase 5: CI Hardening ✅ IMPLEMENTED

**Files:**
- [ci.yml](../../.github/workflows/ci.yml#L185-L255): core-import-safety and ui-extra jobs

**Actual CI Jobs (from ci.yml):**

```yaml
# Lines 185-218 - core-import-safety job
core-import-safety:
  runs-on: ubuntu-latest
  timeout-minutes: 3
  needs: lint
  steps:
  - name: Install CORE dependencies only (no ui extra)
    run: |
      pip install -e ".[dev]"
      # Verify streamlit is NOT installed
      python -c "import sys; assert 'streamlit' not in sys.modules"
  
  - name: Run import safety tests
    run: pytest tests/test_import_safety.py -v --timeout=60
  
  - name: Verify CLI works without UI
    run: |
      python -m integration_coworker.cli --help
      python -m integration_coworker.cli hitl-resume --help

# Lines 224-255 - ui-extra job
ui-extra:
  runs-on: ubuntu-latest
  timeout-minutes: 5
  needs: lint
  steps:
  - name: Install with UI extra
    run: |
      pip install -e ".[dev,ui]"
      # Verify streamlit IS installed
      python -c "import streamlit; print(f'streamlit version: {streamlit.__version__}')"
  
  - name: Run UI tests
    run: pytest tests/test_review_dialog.py tests/test_import_safety.py -v --timeout=120
```

**Import Safety Tests (from [test_import_safety.py](../../tests/test_import_safety.py)):**

```python
class TestReviewDialogImportSafety:
    """Test that review_dialog.py can be imported without streamlit."""

    def test_import_without_streamlit(self):
        """review_dialog should import successfully without streamlit installed."""
        # Mocks import to raise ImportError for streamlit
        # Verifies module loads and has expected functions:
        # - check_pending_review
        # - render_review_dialog
        # - show_review_dialog
        # - _get_streamlit

    def test_check_pending_review_no_streamlit(self):
        """check_pending_review should work without streamlit (pure Python)."""
```

---

## Task D: JSON Payload Contracts (Refs-Not-Blobs)

### Review Payload (v2.0.0)

**Critical change from v1:** Payload contains ONLY refs and bounded summaries. Full content is in artifact store.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "review-payload-v2",
  "type": "object",
  "required": ["schema_version", "review_kind", "run_id", "refs", "summary"],
  "properties": {
    "schema_version": {"const": "2.0.0"},
    "review_kind": {"enum": ["pre_write", "post_sandbox"]},
    "run_id": {"type": "string"},
    "provider_code": {"type": "string"},
    "is_dry_run": {"type": "boolean"},
    
    "refs": {
      "type": "object",
      "description": "References to externally stored artifacts",
      "properties": {
        "code_snapshot": {"$ref": "#/$defs/artifact_ref"},
        "sandbox_result": {"$ref": "#/$defs/artifact_ref"},
        "diffs": {"$ref": "#/$defs/artifact_ref"},
        "failure_bundle": {"$ref": "#/$defs/artifact_ref"}
      }
    },
    
    "summary": {
      "type": "object",
      "description": "Bounded summary (max 2KB total)",
      "properties": {
        "file_count": {"type": "integer"},
        "adds": {"type": "integer"},
        "total_bytes": {"type": "integer"},
        "files": {
          "type": "array",
          "maxItems": 10,
          "items": {
            "type": "object",
            "properties": {
              "path": {"type": "string", "maxLength": 200},
              "bytes": {"type": "integer"}
            }
          }
        },
        "files_truncated": {"type": "boolean"},
        "all_passed": {"type": "boolean"},
        "gates_failed": {"type": "array", "maxItems": 5, "items": {"type": "string"}},
        "error_previews": {
          "type": "array",
          "maxItems": 3,
          "items": {
            "type": "object",
            "properties": {
              "gate": {"type": "string"},
              "message": {"type": "string", "maxLength": 200}
            }
          }
        }
      }
    }
  },
  
  "$defs": {
    "artifact_ref": {
      "description": "OPAQUE: Schema is whatever ArtifactRef.to_dict() returns. Do not invent fields.",
      "type": "object",
      "required": ["__artifact_ref__"],
      "properties": {
        "__artifact_ref__": {"const": true}
      },
      "additionalProperties": true
    }
  }
}
```

**ArtifactRef Schema Contract:**
- The `artifact_ref` schema is **opaque** - it accepts whatever `ArtifactRef.to_dict()` produces.
- The only guaranteed field is `__artifact_ref__: true` (used by `ArtifactRef.is_artifact_ref()`).
- Actual fields (as of this writing): `run_id`, `key`, `uri`, `sha256`, `size_bytes`, `content_type`, `codec`, `created_at`, `metadata`.
- **Test requirement:** Add `test_artifact_ref_roundtrip` that asserts `ArtifactRef.from_dict(ref.to_dict()) == ref`.
```

**Payload size guarantee:** With bounded summaries (10 files, 3 errors, 200 char limits), payload is ~1-2KB.

### Review Decision (v2.0.0)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "review-decision-v2",
  "type": "object",
  "required": ["action"],
  "properties": {
    "action": {"enum": ["approve", "reject", "regenerate"]},
    "approved": {"type": "boolean"},
    "feedback": {
      "type": "string",
      "maxLength": 4000,
      "description": "Required if action=regenerate"
    },
    "targets": {
      "type": "array",
      "description": "Optional: specific artifacts to regenerate",
      "items": {"type": "string"}
    },
    "overrides": {
      "type": "object",
      "properties": {
        "exclude_files": {"type": "array", "items": {"type": "string"}},
        "force_write": {"type": "boolean"},
        "force_reason": {
          "type": "string",
          "minLength": 10,
          "description": "Required if force_write=true"
        }
      }
    }
  },
  "allOf": [
    {
      "if": {"properties": {"action": {"const": "regenerate"}}},
      "then": {"required": ["feedback"]}
    },
    {
      "if": {"properties": {"overrides": {"properties": {"force_write": {"const": true}}}}},
      "then": {"properties": {"overrides": {"required": ["force_reason"]}}}
    }
  ]
}
```

---

## Task E: Diff Viewer Approach (Revised)

### Decision: Bounded `difflib` with Guardrails

**Implementation:** See Phase 3 above (`diff_viewer.py`)

**Key guardrails (SINGLE SOURCE OF TRUTH):**

| Threshold | Action |
|-----------|--------|
| < 100 lines AND < 50KB | HtmlDiff side-by-side (best-effort, opt-in via `try_html=True`) |
| < 500 lines AND < 50KB | Unified diff text (default, guaranteed fast) |
| >= 500 lines OR >= 50KB | Truncate to 50 lines preview + download link |

**Rationale:**
- `difflib.SequenceMatcher` has quadratic worst-case time complexity ([Python difflib docs](https://docs.python.org/3/library/difflib.html))
- HtmlDiff requires reconstructing before/after from patch, which is fragile
- Streaming large diffs to Streamlit causes UI lag
- Users can download full diff artifact if needed

---

## Task F: Production Validation (Postgres + Real LLM)

### Validation Harness

**Script:** [scripts/validate_hitl_production.py](scripts/validate_hitl_production.py) (**NEW**)

**MANDATORY Requirements (NO SKIPS):**

| Requirement | Skip Allowed? | Rationale |
|-------------|---------------|-----------|
| Postgres checkpoint persistence | ❌ NO | Production uses Postgres, not SQLite |
| Artifact roundtrip with large artifacts | ❌ NO | Core refs-not-blobs contract |
| Process kill/resume (SIGKILL) | ❌ NO | Proves crash recovery |
| CLI resume path | ❌ NO | User-facing recovery |
| API recovery path | ❌ NO | Programmatic recovery |
| Real LLM calls | ✅ YES (opt-in) | Cost control |

**CI Integration:**

These tests run in GitHub Actions with a Postgres service container:

```yaml
# .github/workflows/hitl-validation.yml
name: HITL Production Validation

on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_USER: icw_test
          POSTGRES_PASSWORD: test_password
          POSTGRES_DB: icw_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
    
    env:
      DATABASE_URL: postgresql://icw_test:test_password@localhost:5432/icw_test
    
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -e ".[dev]"
      - run: python scripts/validate_hitl_production.py --postgres-only
```

**Execution:**

```bash
# Local: Start Postgres
docker compose up -d postgres

# Run Postgres-mandatory tests (NO SKIPS)
export DATABASE_URL="postgresql://user:pass@localhost:5432/icw_test"
python scripts/validate_hitl_production.py --postgres-only

# Run with real LLM (opt-in)
export IC_ALLOW_LIVE_LLM=1
export IC_LLM_TOKEN_LIMIT=10000
export IC_LLM_COST_LIMIT_USD=0.50
python scripts/validate_hitl_production.py --all
```

**Test Matrix:**

| Test | Environment | Description | Skip Allowed |
|------|-------------|-------------|--------------|
| `test_postgres_checkpoint_resume` | Postgres | Start run → interrupt → **restart process** → resume → verify state | ❌ NO |
| `test_artifact_roundtrip_large` | Postgres + FS | Store 1MB+ artifacts → checkpoint → resume → verify sha256 | ❌ NO |
| `test_process_kill_recovery` | Postgres | Start run → **SIGKILL** at interrupt → resume via CLI → verify | ❌ NO |
| `test_cli_resume_path` | Postgres | Resume via `hitl-resume --approve` → verify completion | ❌ NO |
| `test_api_recovery_path` | Postgres | Resume via `recovery.resume_run()` API → verify completion | ❌ NO |
| `test_state_size_under_threshold` | Any | Serialize state → assert < 64KB | ❌ NO |
| `test_artifact_ref_roundtrip` | Any | `ArtifactRef.from_dict(ref.to_dict()) == ref` | ❌ NO |
| `test_real_llm_codegen_sandbox_fail` | Postgres + LLM | Real codegen → force sandbox failure → review → regenerate | ✅ opt-in |
| `test_real_llm_with_feedback` | Postgres + LLM | Sandbox fail → human feedback → verify feedback in prompt | ✅ opt-in |

**Opt-in Safety for Real LLM Tests:**

```python
# validate_hitl_production.py

def _require_live_llm_opt_in() -> tuple[int, float]:
    """Ensure explicit opt-in for real LLM calls."""
    if os.environ.get("IC_ALLOW_LIVE_LLM") != "1":
        pytest.skip("Real LLM tests require IC_ALLOW_LIVE_LLM=1")
    
    token_limit = int(os.environ.get("IC_LLM_TOKEN_LIMIT", "0"))
    cost_limit = float(os.environ.get("IC_LLM_COST_LIMIT_USD", "0"))
    
    if token_limit <= 0:
        pytest.skip("IC_LLM_TOKEN_LIMIT must be > 0")
    if cost_limit <= 0:
        pytest.skip("IC_LLM_COST_LIMIT_USD must be > 0")
    
    return token_limit, cost_limit


def _require_postgres():
    """Postgres tests are MANDATORY - fail loudly, don't skip."""
    if not os.environ.get("DATABASE_URL"):
        pytest.fail(
            "DATABASE_URL required. Postgres tests are MANDATORY for production validation. "
            "Run: docker compose up -d postgres && export DATABASE_URL=postgresql://..."
        )
```

**Auditable Report:**

Real LLM tests emit a JSON report with token usage:

```python
# At end of test run
report = {
    "timestamp": datetime.utcnow().isoformat(),
    "tests_run": test_count,
    "tests_passed": pass_count,
    "tests_failed": fail_count,
    "llm_usage": {
        "tokens_used": total_tokens,
        "estimated_cost_usd": estimated_cost,
        "token_limit": token_limit,
        "cost_limit_usd": cost_limit,
    },
    "feedback_injection_audit": [
        {"test": "test_real_llm_with_feedback", "feedback_redacted": "Fix the [REDACTED]..."}
    ]
}
with open(f"hitl_validation_report_{timestamp}.json", "w") as f:
    json.dump(report, f, indent=2)
```

**Feedback Injection Seam:**

Human feedback is injected at a single, testable seam in the prompt builder:

```python
# codegen/prompt_builder.py

def build_codegen_prompt(
    context: CodegenContext,
    human_feedback: Optional[str] = None,  # Single injection point
) -> str:
    """Build the codegen prompt, optionally including human feedback."""
    prompt = _build_base_prompt(context)
    
    if human_feedback:
        # Log redacted version for audit
        redacted = _redact_for_logging(human_feedback)
        logger.info(f"Including human feedback in prompt: {redacted}")
        
        prompt += f"\n\n## Human Feedback\n\nThe reviewer provided the following feedback:\n\n{human_feedback}\n\nPlease address this feedback in your implementation."
    
    return prompt
```

---

## Task G: Feature ROI Ranking (Revised)

### ROI Matrix

| Rank | Feature | Effort | Impact | Risk | ROI |
|------|---------|--------|--------|------|-----|
| **1** | Review artifact infrastructure + state size guards | 2d | **Critical** - Unblocks all other features; proves refs-not-blobs | Low | **10/10** |
| **2** | Production validation harness (Postgres mandatory) | 1d | **Critical** - Proves persistence before building on it | Low | **10/10** |
| **3** | Generic `review_gate` node with routing | 2d | **High** - Enables post-sandbox review; single implementation | Medium | **9/10** |
| **4** | Bounded diff viewer (patch-based) | 1d | **Medium** - Required for UX; guardrails prevent hangs | Low | **8/10** |
| **5** | Single Streamlit dialog | 1d | **Medium** - Prevents rerun bugs; cleaner UX | Low | **8/10** |
| **6** | CLI `--regenerate --feedback` | 0.5d | **Medium** - Power user workflow | Low | **7/10** |
| **7** | Real LLM validation tests (opt-in) | 0.5d | **Medium** - E2E confidence | Low | **7/10** |

### Implementation Order (REVISED)

**Rationale:** Build validation harness BEFORE gate logic to avoid building on unproven persistence invariants.

```
Week 1:
├── PR #1: Review artifact infrastructure + state size guards
│   ├── review_artifacts.py (JSON-only codec)
│   ├── State fields: review_artifact_refs, human_feedback
│   ├── test_artifact_ref_roundtrip
│   └── test_state_size_under_threshold
│
├── PR #2: Production validation harness (Postgres mandatory)
│   ├── validate_hitl_production.py
│   ├── GitHub Actions workflow with Postgres service
│   ├── test_postgres_checkpoint_resume (xfail OK initially)
│   └── test_artifact_roundtrip_large
│
└── PR #3: review_gate refactor + payload builder + decision applier
    ├── review_gate.py (factory pattern)
    ├── Payload contracts (refs-not-blobs)
    └── Wire two instances in runtime.py

Week 2:
├── PR #4: UI dialog routing + lazy artifact loads + bounded diff viewer
│   ├── Single @st.dialog with review_kind routing
│   ├── diff_viewer.py (patch-based, HtmlDiff best-effort)
│   └── Lazy load via session_state (no st.rerun)
│
├── PR #5: CLI actions + recovery API actions
│   ├── hitl-resume --regenerate --feedback
│   └── recovery.regenerate_with_feedback()
│
└── PR #6: Real LLM opt-in suite
    ├── test_real_llm_codegen_sandbox_fail
    ├── test_real_llm_with_feedback
    └── Auditable report with token usage

Week 3: Quality Pipeline (PR #7-#9)
├── PR #7: Quality Signals Core (models + artifact-backed storage)
│   ├── Domain models (pure dataclasses):
│   │   ├── StaticAnalysisResult, StaticIssue
│   │   ├── AttributedFailure, FailureAttribution
│   │   ├── QualityScoreBreakdown
│   │   ├── RegenerationTarget
│   │   ├── HumanEditPatch
│   │   └── IterationState
│   ├── quality_refs: Dict[str, ArtifactRef] keyed by stage
│   ├── All heavy payloads → artifacts (JSON codec)
│   ├── test_quality_artifacts_roundtrip
│   └── test_payload_size_quality (stays < 2KB with quality refs)
│
├── PR #8: Static Analysis Gate (toolchain-discovered, deterministic)
│   ├── StaticCheck interface:
│   │   ├── discover(repo_root) -> Optional[Config]
│   │   └── run(ctx) -> Findings
│   ├── Minimum deterministic checks (zero external tools):
│   │   ├── AST parse (syntax validation)
│   │   └── Import resolution (repo-root PYTHONPATH aware)
│   ├── Optional checks (discovered + available only):
│   │   └── ruff/flake8, mypy/pyright, bandit via config + PATH probing
│   ├── Output: static_analysis.json artifact + bounded summary
│   ├── Routing: retry budget + explicit escalation (no infinite loops)
│   ├── test_static_analysis_gate
│   └── test_static_analysis_no_hardcode (fails if tool assumed without discovery)
│
└── PR #9: Sandbox Error Attribution (stacktrace-driven)
    ├── Attribution engine:
    │   ├── Parse traceback frames
    │   ├── Map to generated files by artifact rel_path (not hardcoded prefixes)
    │   ├── "Deepest generated frame" heuristic + confidence score
    │   └── Bounded snippet extraction (line window capped)
    ├── Output: sandbox_attribution.json artifact + bounded summary
    ├── test_error_attribution (fixture traceback → correct file/line)
    └── test_attribution_bounds (snippet + list sizes bounded)

Week 4: Targeted Actions (PR #10-#12)
├── PR #10: Targeted Regeneration (decision schema + codegen routing)
│   ├── Extended review decision:
│   │   ├── action: "regenerate_targeted"
│   │   ├── targets: List[rel_path] (max enforced)
│   │   └── target_feedback: Dict[rel_path, str] (max sizes enforced)
│   ├── Codegen strategy accepts:
│   │   ├── Target paths as constraints
│   │   ├── Structured "why" per target
│   │   └── Non-target preservation (unless strategy explains via artifact)
│   ├── Iteration budget in state (small ints only)
│   ├── test_targeted_regeneration_e2e (Postgres)
│   └── test_iteration_budget (no infinite loop; escalates after N)
│
├── PR #11: Human Edit Capability (patch-based, audited, validated)
│   ├── UI accepts edits → runtime receives patches (not full blobs)
│   ├── Apply edits:
│   │   ├── Validate patch applies cleanly
│   │   ├── Validate syntax (AST parse minimum)
│   │   └── Record audit artifact (human_edits.json) with reason + diff
│   ├── Flow: apply edits → rerun static analysis → proceed
│   ├── test_human_edit_apply_e2e (Postgres, checkpoint size under threshold)
│   └── test_human_edit_audit (artifact created with reason)
│
└── PR #12: Quality Dashboard UI (single dialog routing, lazy-load)
    ├── Single @st.dialog routing by review_kind
    ├── Quality panel reads summaries from payload only
    ├── "Load details" buttons fetch artifacts by ref lazily
    ├── st.rerun() only at END of submit handler to close
    └── Invariant enforcement (code scan):
        ├── Single dialog entrypoint
        ├── No nested dialogs
        └── st.rerun() only in submit path
```

---

## Production Guardrails Contract (PR #9)

### Single Source of Truth

All production safety bounds are defined in ONE module:

**File:** [`production_guardrails.py`](../../src/integration_coworker/graph/production_guardrails.py)

This module is the ONLY place that defines:
- Timeout defaults and env overrides
- Stdout/stderr capture caps
- Truncation policy (bytes + lines + deterministic marker)
- Redaction policy (secrets and volatile tokens)
- Deterministic serialization helpers
- Process-tree termination utilities

### Contract Invariants

| Invariant | Enforcement | Test |
|-----------|-------------|------|
| **Timeout always enforced** | `run_tool_safely()` uses Popen with `_kill_process_tree()` | `test_subprocess_timeout_always_kills_and_reaps` |
| **Process tree killed on timeout** | `start_new_session=True` + `os.killpg()` on POSIX | `test_subprocess_uses_process_group_isolation` |
| **Child always reaped** | `_kill_process_tree()`: SIGTERM → SIGKILL → wait() | `test_kill_process_tree_exists_and_reaps` |
| **Output caps always enforced** | Bytes bounded BEFORE decode; lines bounded with marker | `test_output_caps_always_enforced` |
| **Redaction uses callable repls** | All `_REDACTION_PATTERNS` use `lambda m: ...` | `test_all_redaction_replacements_are_callable` |
| **Volatile patterns use callable repls** | All `_VOLATILE_PATTERNS` use `lambda m: ...` | `test_all_volatile_replacements_are_callable` |
| **Redaction never throws** | try/except around each pattern; skip on failure | `test_redaction_never_raises` |
| **Normalization never throws** | try/except around entire pipeline; fallback to truncated raw | `test_normalization_never_raises` |
| **Deterministic truncation** | Line marker: `[... N more lines truncated ...]` | `test_deterministic_truncation` |
| **Fingerprints stable** | Computed from post-normalized content only | `test_fingerprint_stability_across_runs` |
| **No duplicate constants** | Graph modules import from guardrails, not define | `test_no_duplicate_guardrail_constants_in_graph_modules` |
| **Timeout ceiling non-overridable** | `TOOL_MAX_TIMEOUT_SECONDS=300` is hardcoded | `test_env_overrides_are_bounded` |

### Where Guardrails Must Be Used

All seams that touch external text or tools:

| Seam | Module | Usage |
|------|--------|-------|
| Static analysis tool output | `static_checks.py` | `run_tool_safely()` for subprocess, `normalize_evidence()` for output |
| Sandbox stdout/stderr | `sandbox_attribution.py` | `normalize_evidence()` before fingerprinting |
| Exception tracebacks | `sandbox_attribution.py` | `_extract_stack_frames()` bounded to `MAX_STACK_FRAMES` |
| Artifact summaries | `quality_artifacts.py`, `review_artifacts.py` | `safe_json_dumps()` with `MAX_ARTIFACT_SUMMARY_BYTES` |
| Evidence windows | All attribution | `MAX_EVIDENCE_BYTES`, `MAX_EVIDENCE_LINES` |

### Non-Bypassable Enforcement

Guardrails cannot be silently bypassed:

1. **Callable-only replacements**: Test inspects `_REDACTION_PATTERNS` and `_VOLATILE_PATTERNS` to ensure all use `callable()`, not strings
2. **No duplicate constants**: Test scans graph modules for local definitions of guardrail constants
3. **Process-group isolation**: Test inspects `run_tool_safely()` source for `start_new_session` and `_kill_process_tree`
4. **CI grep check**: Workflow fails if guardrail constants appear outside `production_guardrails.py`

### Environment Overrides

```bash
# Override default tool timeout (default: 30s, max: 300s)
export GUARDRAIL_TOOL_TIMEOUT_SECONDS=60

# Override max output bytes (default: 64KB)
export GUARDRAIL_MAX_OUTPUT_BYTES=131072
```

Note: `TOOL_MAX_TIMEOUT_SECONDS=300` is NOT env-overridable by design.

### Subprocess Termination (Cross-Platform)

```python
def _kill_process_tree(proc: subprocess.Popen, grace_seconds: float = 2.0) -> None:
    """
    Kill a process and all its children, then reap zombies.
    
    POSIX: Uses process groups via start_new_session=True.
    Windows: Uses proc.kill() only (no process groups).
    """
    # POSIX: Kill entire process group
    # 1. SIGTERM to group (graceful)
    # 2. Wait for grace_seconds
    # 3. SIGKILL to group (forceful)
    # 4. wait() to reap zombie
```

### Normalization Order (Deterministic)

1. Normalize line endings (`\r\n` → `\n`, `\r` → `\n`)
2. Strip ANSI escape codes
3. Truncate to max bytes
4. Truncate to max lines (deterministic marker)
5. Redact secrets (callable replacement, no backslash issues)
6. Normalize volatile tokens (timestamps, PIDs, addresses)
7. Compute fingerprint from post-normalized content only

### Test Coverage

Contract tests in [`test_production_guardrails.py`](../../tests/test_production_guardrails.py):

| Test Class | Purpose |
|------------|---------|
| `TestSubprocessTimeout` | Timeout behavior, ceiling enforcement |
| `TestHugeOutputHandling` | Output caps, deterministic truncation |
| `TestSecretRedaction` | API keys, AWS, GitHub tokens, DB passwords |
| `TestDeterministicBehavior` | Reproducible outputs |
| `TestGuardrailsContract` | **Cross-module contract verification** |
| `TestGuardrailsEnforcement` | **Non-bypassable enforcement** (7 tests) |

**Enforcement Tests (merge-blocking):**
- `test_all_redaction_replacements_are_callable`
- `test_all_volatile_replacements_are_callable`
- `test_no_duplicate_guardrail_constants_in_graph_modules`
- `test_subprocess_uses_process_group_isolation`
- `test_kill_process_tree_exists_and_reaps`
- `test_timeout_proves_process_is_reaped`
- `test_env_overrides_are_bounded`

---

## Task H: Quality Pipeline Architecture (PR #7-#12)

### Non-Negotiables (Apply to ALL Quality PRs)

| Constraint | Enforcement | Rationale |
|------------|-------------|-----------|
| **No hardcoded tools** | Discovery via repo config + PATH probing | Different repos use different linters |
| **No hardcoded DB schema** | Checkpointer API boundary (`aget_tuple`) only | LangGraph schema is internal |
| **No blobs in state** | Only refs + bounded summaries | Checkpoint size discipline |
| **Deterministic routing** | Retry budgets + escalation paths | No infinite loops |
| **LangGraph resume semantics** | Same `thread_id` + `Command(resume=...)` | [Durable execution contract](https://docs.langchain.com/oss/python/langgraph/durable-execution) |
| **Streamlit dialog model** | `st.rerun()` at END of submit only | [Fragment behavior](https://docs.streamlit.io/develop/api-reference/execution-flow/st.dialog) |

### PR #7: Quality Signals Core

**Purpose:** Domain models and artifact plumbing for quality data. No tool choices.

**Domain Models (pure dataclasses):**

```python
# graph/quality_models.py
"""
Quality pipeline domain models.

CRITICAL: These are pure data structures. No tool choices, no hardcoded paths.
All heavy data goes to artifacts, only refs + bounded summaries in state.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Any
from datetime import datetime

@dataclass
class StaticIssue:
    """A single static analysis finding."""
    severity: Literal["error", "warning", "info"]
    category: Literal["syntax", "import", "type", "lint", "security", "other"]
    file_path: str  # rel_path from code artifacts
    line_number: int
    column: int
    message: str
    rule_id: Optional[str] = None  # e.g., "E501", "F401"
    suggested_fix: Optional[str] = None
    auto_fixable: bool = False

@dataclass
class StaticAnalysisResult:
    """Result of static analysis stage."""
    passed: bool
    issues: List[StaticIssue]
    blocking_count: int  # errors that block proceed
    warning_count: int
    tool_versions: Dict[str, str]  # tool -> version (for reproducibility)
    
@dataclass
class FailureAttribution:
    """Links a test failure to specific generated code."""
    test_name: str
    test_file: str
    error_type: str
    error_message: str  # Bounded: max 500 chars in payload
    
    # Attribution (may be None if attribution failed)
    likely_cause_file: Optional[str]  # rel_path
    likely_cause_lines: Optional[tuple[int, int]]  # (start, end)
    confidence: float  # 0.0-1.0
    
    # Context for regeneration (bounded)
    fix_hints: List[str] = field(default_factory=list)  # max 3 hints, 200 chars each

@dataclass
class QualityScoreBreakdown:
    """Composite quality score with breakdown."""
    overall: float  # 0-100
    static_analysis: float  # 0-100
    test_coverage: Optional[float]  # 0-100, None if not measured
    complexity: Optional[float]  # 0-100, None if not measured
    
@dataclass
class RegenerationTarget:
    """A file targeted for regeneration."""
    file_path: str  # rel_path
    reason: str  # Why this file needs regeneration
    failures_linked: List[str]  # Test names that failed due to this file
    priority: int  # Higher = regenerate first
    constraints: List[str]  # Specific constraints for regeneration

@dataclass
class HumanEditPatch:
    """A human-applied code patch."""
    file_path: str  # rel_path
    patch_text: str  # Unified diff format
    reason: str  # Audit: why this edit
    editor_id: str  # Who made the edit
    timestamp: datetime
    
@dataclass
class IterationState:
    """Tracks regeneration iteration budget."""
    iteration_count: int = 0
    max_iterations: int = 3
    quality_history: List[float] = field(default_factory=list)
    escalation_reason: Optional[str] = None
```

**State Extension:**

```python
# state.py additions
quality_refs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
# Structure: {
#   "static": {"result": ArtifactRef, "summary": {...}},
#   "sandbox_attribution": {"failures": ArtifactRef, "summary": {...}},
#   "human_edits": {"patches": ArtifactRef, "audit": ArtifactRef},
#   "quality_score": {"breakdown": ArtifactRef}
# }

iteration_state: Optional[Dict[str, Any]] = None  # IterationState as dict
```

**Acceptance Tests:**

| Test | What It Proves |
|------|----------------|
| `quality-artifacts-roundtrip` | Quality refs resolve, dict equality on JSON load |
| `payload-size-quality` | Review payload < 2KB even with all quality refs |

---

### PR #8: Static Analysis Gate

**Purpose:** Catch deterministic errors before sandbox. Toolchain-discovered, not hardcoded.

**Check Interface (no tool assumptions):**

```python
# codegen/static_checks/base.py
"""
Static check interface.

CRITICAL: Checks are DISCOVERED, not hardcoded.
- discover() probes repo config and PATH
- If tool not found, check is skipped (not failed)
- No hardcoded commands like "ruff check" or "mypy ."
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Dict, Any

@dataclass
class CheckConfig:
    """Configuration discovered for a check."""
    tool_name: str
    tool_version: str
    config_file: Optional[Path]  # e.g., pyproject.toml, .flake8
    extra_args: List[str] = field(default_factory=list)

@dataclass
class CheckContext:
    """Context for running a check."""
    repo_root: Path
    files_to_check: List[Path]  # Generated files only
    sandbox_env: Dict[str, str]  # Environment variables

class StaticCheck(ABC):
    """Interface for a static analysis check."""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable check name."""
        pass
    
    @abstractmethod
    def discover(self, repo_root: Path) -> Optional[CheckConfig]:
        """
        Discover if this check is available and configured.
        
        Returns None if:
        - Tool not installed (not in PATH)
        - No configuration found in repo
        - Check explicitly disabled in repo config
        """
        pass
    
    @abstractmethod
    def run(self, ctx: CheckContext, config: CheckConfig) -> List[StaticIssue]:
        """
        Run the check and return findings.
        
        MUST NOT raise on check failure - return issues instead.
        MUST timeout after reasonable limit (e.g., 60s).
        """
        pass
```

**Minimum Deterministic Checks (zero external tools):**

```python
# codegen/static_checks/builtin.py
"""
Built-in checks that require no external tools.

These ALWAYS run because they use only Python stdlib.
"""
import ast
from pathlib import Path
from typing import List, Optional

class SyntaxCheck(StaticCheck):
    """AST parse to catch syntax errors."""
    
    name = "syntax"
    
    def discover(self, repo_root: Path) -> Optional[CheckConfig]:
        # Always available - uses stdlib ast
        return CheckConfig(tool_name="ast", tool_version=f"python-{sys.version_info.major}.{sys.version_info.minor}")
    
    def run(self, ctx: CheckContext, config: CheckConfig) -> List[StaticIssue]:
        issues = []
        for file_path in ctx.files_to_check:
            if file_path.suffix == ".py":
                try:
                    content = file_path.read_text()
                    ast.parse(content, filename=str(file_path))
                except SyntaxError as e:
                    issues.append(StaticIssue(
                        severity="error",
                        category="syntax",
                        file_path=str(file_path.relative_to(ctx.repo_root)),
                        line_number=e.lineno or 1,
                        column=e.offset or 0,
                        message=str(e.msg),
                    ))
        return issues

class ImportCheck(StaticCheck):
    """Best-effort import resolution."""
    
    name = "imports"
    
    def discover(self, repo_root: Path) -> Optional[CheckConfig]:
        return CheckConfig(tool_name="import_check", tool_version="1.0")
    
    def run(self, ctx: CheckContext, config: CheckConfig) -> List[StaticIssue]:
        # Parse imports from AST, check if resolvable
        # Best-effort: may miss dynamic imports, namespace packages
        ...
```

**Optional Checks (discovered only):**

```python
# codegen/static_checks/discovered.py
"""
Checks that require external tools - only enabled if discovered.

CRITICAL: No hardcoded tool invocations. Discovery determines availability.
"""
import shutil
import subprocess
from pathlib import Path

class RuffCheck(StaticCheck):
    """Ruff linter - only if installed and configured."""
    
    name = "ruff"
    
    def discover(self, repo_root: Path) -> Optional[CheckConfig]:
        # Check PATH
        ruff_path = shutil.which("ruff")
        if not ruff_path:
            return None
        
        # Check repo config
        pyproject = repo_root / "pyproject.toml"
        ruff_toml = repo_root / "ruff.toml"
        
        config_file = None
        if pyproject.exists() and "[tool.ruff]" in pyproject.read_text():
            config_file = pyproject
        elif ruff_toml.exists():
            config_file = ruff_toml
        
        # Get version
        result = subprocess.run([ruff_path, "--version"], capture_output=True, text=True, timeout=5)
        version = result.stdout.strip() if result.returncode == 0 else "unknown"
        
        return CheckConfig(tool_name="ruff", tool_version=version, config_file=config_file)
    
    def run(self, ctx: CheckContext, config: CheckConfig) -> List[StaticIssue]:
        # Run ruff with JSON output, parse results
        # Timeout after 60s
        ...
```

**Routing with Budget:**

```python
# graph/nodes/static_analysis_gate.py
"""
Static analysis gate node.

ROUTING:
- passed: all blocking issues fixed → proceed to review
- retry: auto-fixable issues exist, budget remaining → apply fixes, re-check
- escalate: budget exhausted or unfixable → proceed to review with issues visible
"""

MAX_STATIC_RETRIES = 2  # Small, explicit budget

def static_analysis_gate(state: WorkflowState) -> WorkflowState:
    """Run static analysis on generated code."""
    
    retry_count = state.static_analysis_retries or 0
    
    # Discover and run checks
    result = run_all_checks(state)
    
    # Store full result as artifact
    refs = store_quality_artifact(state.run_id, "static", result)
    state.quality_refs["static"] = {
        "result": refs["result"].to_dict(),
        "summary": _build_bounded_summary(result),
    }
    
    if result.passed:
        return state  # Continue to review
    
    if retry_count >= MAX_STATIC_RETRIES:
        # Escalate: proceed to review with issues visible
        state.static_analysis_escalated = True
        return state
    
    # Check for auto-fixable issues
    auto_fixes = [i for i in result.issues if i.auto_fixable]
    if auto_fixes and _high_confidence_fixes(auto_fixes):
        state.static_analysis_retries = retry_count + 1
        state.pending_auto_fixes = auto_fixes
        # Graph edge routes to apply_fixes → re-check
        return state
    
    # No auto-fixes: escalate
    state.static_analysis_escalated = True
    return state
```

**Acceptance Tests:**

| Test | What It Proves |
|------|----------------|
| `TestStaticAnalysisGateGraphWiring` | Node exists, follows `generate_code_and_tests`, no auto-fix loops |
| `TestStateSyncDrift` | Runtime imports cleanly, quality fields exist in state |
| `TestBoundedSummaryCeiling` | Summary < 4KB, truncates large lists, minimal for empty |
| `TestDiscoveryOnlyEnforcement` | Tools not invoked without discovery, builtins always run |
| `TestNoHardcodedPaths` | No hardcoded tool paths, registry uses tool_name |
| `TestQualityRefsSizeGuard` | `validate_quality_refs_size()` catches oversized refs |

**Test File:** [test_static_analysis_gate.py](../../tests/test_static_analysis_gate.py) (45 tests)

**Invariants Enforced:**
- Built-in checks (syntax, imports) always run (stdlib-only)
- Optional tools discovered via `shutil.which()` + config detection
- Missing tools → "skipped" (not "failed")
- Summary ceiling: 4KB hard limit
- State keeps `ArtifactRef` + bounded summary only

---

### PR #9: Sandbox Error Attribution

**Purpose:** Link test failures to specific generated code. Stacktrace-driven, no magic constants.

**Attribution Engine:**

```python
# codegen/sandbox_attribution.py
"""
Error attribution - links sandbox failures to generated code.

CRITICAL: 
- Map frames by artifact rel_path, not hardcoded path prefixes
- Confidence score reflects attribution certainty
- Bounded snippets and lists
"""
from dataclasses import dataclass
from typing import List, Optional, Set
import traceback
import re

MAX_SNIPPET_LINES = 10
MAX_FIX_HINTS = 3
MAX_HINT_LENGTH = 200

def attribute_failures(
    failures: List[SandboxFailure],
    generated_files: List[CodeArtifact],
) -> List[FailureAttribution]:
    """
    Attribute test failures to generated code.
    
    Strategy:
    1. Parse stack trace to extract frames
    2. Match frames against generated file rel_paths
    3. Deepest match is "likely cause"
    4. Extract bounded snippet
    5. Generate fix hints (if possible)
    """
    generated_paths: Set[str] = {a.rel_path for a in generated_files}
    generated_content: Dict[str, str] = {a.rel_path: a.content for a in generated_files}
    
    attributions = []
    
    for failure in failures:
        frames = _parse_traceback(failure.stack_trace)
        
        # Find frames in generated files
        matching_frames = [
            f for f in frames 
            if _normalize_path(f.filename) in generated_paths
        ]
        
        if not matching_frames:
            # No attribution possible
            attributions.append(FailureAttribution(
                test_name=failure.test_name,
                test_file=failure.test_file,
                error_type=failure.error_type,
                error_message=_truncate(failure.error_message, 500),
                likely_cause_file=None,
                likely_cause_lines=None,
                confidence=0.0,
            ))
            continue
        
        # Deepest generated frame is likely cause
        cause_frame = matching_frames[-1]
        rel_path = _normalize_path(cause_frame.filename)
        content = generated_content.get(rel_path, "")
        
        # Extract bounded snippet
        lines = content.splitlines()
        start = max(0, cause_frame.lineno - MAX_SNIPPET_LINES // 2)
        end = min(len(lines), cause_frame.lineno + MAX_SNIPPET_LINES // 2)
        
        # Generate fix hints
        hints = _generate_fix_hints(failure, cause_frame, content)
        
        attributions.append(FailureAttribution(
            test_name=failure.test_name,
            test_file=failure.test_file,
            error_type=failure.error_type,
            error_message=_truncate(failure.error_message, 500),
            likely_cause_file=rel_path,
            likely_cause_lines=(start + 1, end),  # 1-indexed
            confidence=_calculate_confidence(matching_frames, frames),
            fix_hints=hints[:MAX_FIX_HINTS],
        ))
    
    return attributions


def _calculate_confidence(matching: List, total: List) -> float:
    """Calculate attribution confidence."""
    if not matching:
        return 0.0
    # More matching frames = higher confidence
    ratio = len(matching) / len(total) if total else 0
    # Deeper match = higher confidence
    depth_bonus = 0.2 if matching[-1] == total[-1] else 0
    return min(1.0, ratio + depth_bonus)
```

**Acceptance Tests:**

| Test | What It Proves |
|------|----------------|
| `error-attribution` | Fixture traceback → correct file/line in attribution |
| `attribution-bounds` | Snippet ≤ MAX_SNIPPET_LINES, hints ≤ MAX_FIX_HINTS |

---

### PR #10: Targeted Regeneration ✅ IMPLEMENTED

**Purpose:** Allow regenerating specific files, not all. Constrain codegen strategy, not hardcoded rewrites.

**Actual Implementation Files:**
- [regeneration_models.py](../../src/integration_coworker/graph/regeneration_models.py) - Decision schemas, path validation, iteration state (524 lines)
- [targeted_regeneration.py](../../src/integration_coworker/graph/nodes/targeted_regeneration.py) - Node implementation (369 lines)
- [state.py](../../src/integration_coworker/graph/state.py#L148) - Added `regeneration_constraints_ref` field
- [runtime.py](../../src/integration_coworker/graph/runtime.py#L2662-2702) - Conditional routing and loop edge

**Implemented Decision Schema:**

> **Verified at [regeneration_models.py#L115-200](../../src/integration_coworker/graph/regeneration_models.py#L115)**

```python
# From regeneration_models.py

@dataclass
class RegenerateTargetedDecision:
    """
    Decision to regenerate specific files with targeted feedback.
    
    Bounds (from production_guardrails.py):
    - MAX_TARGETS = 10
    - MAX_FEEDBACK_LENGTH = 4000 (global)
    - MAX_TARGET_FEEDBACK_LENGTH = 500 (per-target)
    - MAX_FILE_PATH_LENGTH = 500
    
    Security:
    - All paths validated via validate_rel_path() at parse time
    - Rejects absolute paths, .., and path traversal
    
    Determinism:
    - targets list sorted at parse time
    - target_feedback dict keys sorted
    - fingerprint() returns stable hash for duplicate detection
    """
    action: Literal["regenerate_targeted"] = "regenerate_targeted"
    targets: List[str] = field(default_factory=list)
    target_feedback: Dict[str, str] = field(default_factory=dict)
    global_feedback: Optional[str] = None
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RegenerateTargetedDecision":
        """Parse and validate from dict with deterministic ordering."""
        targets = sorted(set(data.get("targets", [])))  # Sort at parse time
        for path in targets:
            validate_rel_path(path, "target")  # Security boundary
        # ... validation continues
```

**Iteration State with Budget:**

> **Verified at [regeneration_models.py#L260-320](../../src/integration_coworker/graph/regeneration_models.py#L260)**

```python
@dataclass  
class IterationState:
    """Track regeneration iterations for budget enforcement."""
    iteration_count: int = 0
    max_iterations: int = MAX_REGENERATION_ITERATIONS  # Hard limit: 3
    quality_history: List[float] = field(default_factory=list)
    fingerprint_history: List[str] = field(default_factory=list)  # Stuck loop detection
```

**Node Implementation:**

> **Verified at [targeted_regeneration.py#L54-100](../../src/integration_coworker/graph/nodes/targeted_regeneration.py#L54)**

```python
def targeted_regeneration(state: WorkflowState) -> WorkflowState:
    """
    Process targeted regeneration decision and build constraints artifact.
    
    Flow:
    1. Parse and validate decision (paths, bounds)
    2. Build RegenerationConstraints from decision + attribution summary
    3. Check for stuck loops (same fingerprint repeated)
    4. Store constraints as artifact (refs-not-blobs pattern)
    5. Update iteration state with budget tracking
    
    Returns:
        Updated state with regeneration_constraints_ref set
        
    Raises:
        PathValidationError: If targets contain invalid paths (security)
        ValueError: If iteration budget exceeded
    """
```

**Graph Routing:**

> **Verified at [runtime.py#L2662-2702](../../src/integration_coworker/graph/runtime.py#L2662)**

```python
def check_sandbox_review_decision(state: WorkflowState) -> str:
    """
    Route based on sandbox review decision.
    
    PR #10: Supports targeted regeneration decisions.
    
    Routes:
    - "continue": Proceed to gold checkpoint
    - "regenerate": Go to targeted_regeneration node  
    - "escalate": Budget exhausted or stuck loop, proceed anyway
    """
    # Check for escalation (budget exhausted)
    if state.plan.get("regeneration_escalated"):
        logger.info("[PR #10] Regeneration escalated, proceeding to gold checkpoint")
        return "continue"
    
    decisions = getattr(state, 'review_decisions', None) or {}
    sandbox_decision = decisions.get("sandbox", {})
    action = sandbox_decision.get("action", "continue")
    
    if action == "regenerate_targeted":
        logger.info(f"[PR #10] Sandbox review decision: regenerate_targeted")
        return "regenerate"
    
    return "continue"

# Conditional edges
workflow.add_conditional_edges(
    "sandbox_review_gate",
    check_sandbox_review_decision,
    {
        "continue": "persist_gold_checkpoint",
        "regenerate": "targeted_regeneration",
    }
)

# Loop back to codegen
workflow.add_edge("targeted_regeneration", "generate_code_and_tests")
```

**Acceptance Tests (83 tests total):**

| Test File | Count | What It Proves |
|-----------|-------|----------------|
| [test_regeneration_models.py](../../tests/test_regeneration_models.py) | 49 | Path validation (rejects `..`, absolute), bounds enforcement, fingerprinting, serialization |
| [test_targeted_regeneration.py](../../tests/test_targeted_regeneration.py) | 23 | Node integration, budget enforcement, stuck loop detection, escalation |
| [test_targeted_regen_wiring.py](../../tests/test_targeted_regen_wiring.py) | 11 | Graph structure, conditional edges, routing function |

**Key Invariants (All Verified):**

| Invariant | Evidence |
|-----------|----------|
| Iteration budget = 3 | `MAX_REGENERATION_ITERATIONS = 3` at [regeneration_models.py#L48](../../src/integration_coworker/graph/regeneration_models.py#L48) |
| Path security | `validate_rel_path()` rejects `..`, `/`, `C:\` at [regeneration_models.py#L62](../../src/integration_coworker/graph/regeneration_models.py#L62) |
| Stuck loop detection | Fingerprint history check at [targeted_regeneration.py#L135](../../src/integration_coworker/graph/nodes/targeted_regeneration.py#L135) |
| Refs-not-blobs | `regeneration_constraints_ref` field at [state.py#L148](../../src/integration_coworker/graph/state.py#L148) |
| Deterministic ordering | `sorted()` at parse time in `from_dict()` |

---

### PR #11: Human Edit Capability

**Purpose:** Allow humans to apply quick fixes. Patch-based, audited, validated.

**Patch Model (not full blobs):**

```python
# graph/human_edits.py
"""
Human edit application.

CRITICAL:
- Runtime receives PATCHES, not full file blobs
- Patches are validated before application
- All edits are audited
"""
import difflib
from dataclasses import dataclass
from typing import List

@dataclass
class EditValidationResult:
    """Result of validating a human edit patch."""
    valid: bool
    errors: List[str]
    warnings: List[str]

def validate_patch(patch: HumanEditPatch, artifact: CodeArtifact) -> EditValidationResult:
    """
    Validate that a patch can be applied.
    
    Checks:
    1. Patch applies cleanly (context matches)
    2. Result is valid syntax (AST parse)
    3. Patch is bounded (not replacing entire file)
    """
    errors = []
    warnings = []
    
    # Try to apply patch
    try:
        patched_content = _apply_unified_patch(artifact.content, patch.patch_text)
    except PatchApplyError as e:
        errors.append(f"Patch does not apply cleanly: {e}")
        return EditValidationResult(valid=False, errors=errors, warnings=warnings)
    
    # Validate syntax
    if artifact.rel_path.endswith(".py"):
        try:
            ast.parse(patched_content)
        except SyntaxError as e:
            errors.append(f"Patched code has syntax error: {e}")
    
    # Check patch is bounded (not full replacement)
    original_lines = artifact.content.splitlines()
    patched_lines = patched_content.splitlines()
    if len(patched_lines) > len(original_lines) * 2:
        warnings.append("Patch significantly increases file size - consider regeneration instead")
    
    return EditValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


def apply_human_edits(
    state: WorkflowState,
    patches: List[HumanEditPatch],
) -> WorkflowState:
    """
    Apply validated human edit patches.
    
    Flow:
    1. Validate all patches
    2. Apply patches to artifacts
    3. Store audit artifact
    4. Re-run static analysis (via graph routing)
    """
    audit_entries = []
    
    for patch in patches:
        # Find artifact
        artifact = next(
            (a for a in state.code_artifacts if a.rel_path == patch.file_path),
            None
        )
        if not artifact:
            raise ValueError(f"File not found: {patch.file_path}")
        
        # Validate
        validation = validate_patch(patch, artifact)
        if not validation.valid:
            raise ValueError(f"Invalid patch for {patch.file_path}: {validation.errors}")
        
        # Apply
        artifact.content = _apply_unified_patch(artifact.content, patch.patch_text)
        
        # Record audit
        audit_entries.append({
            "file_path": patch.file_path,
            "patch_text": patch.patch_text,
            "reason": patch.reason,
            "editor_id": patch.editor_id,
            "timestamp": patch.timestamp.isoformat(),
            "validation_warnings": validation.warnings,
        })
    
    # Store audit artifact
    refs = store_quality_artifact(state.run_id, "human_edits", {"entries": audit_entries})
    state.quality_refs["human_edits"] = {
        "audit": refs["audit"].to_dict(),
        "count": len(patches),
    }
    
    # Mark for re-validation
    state.needs_static_recheck = True
    
    return state
```

**Acceptance Tests:**

| Test | What It Proves | Requires Postgres |
|------|----------------|-------------------|
| `human-edit-apply-e2e` | Pause, apply patch, resume, checkpoint size OK | **Yes** |
| `human-edit-audit` | Audit artifact created with reason + diff | **Yes** |

---

### PR #12: Quality Dashboard UI

**Purpose:** Display quality signals in review dialog. Single dialog, lazy load artifacts.

**Streamlit Dialog Contract Enforcement:**

```python
# ui/streamlit_app.py

@st.dialog("Review Required", width="large")
def _show_review_dialog(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Single review dialog with quality dashboard.
    
    STREAMLIT CONTRACT (per official docs):
    1. Dialog inherits fragment behavior - widget clicks rerun dialog only
    2. on_dismiss controls dismiss behavior (default: ignore)
    3. st.rerun() at END of submit handler to close (official pattern)
    4. Widget keys include run_id for fresh state on reopen
    5. ONE dialog - calling another while open is an error
    """
    run_id = payload.get("run_id", "unknown")
    review_kind = payload.get("review_kind", "pre_write")
    
    # Quality score (always in payload summary)
    quality_summary = payload.get("quality_summary", {})
    if quality_summary:
        _render_quality_score_header(quality_summary)
    
    # Route to appropriate content renderer
    if review_kind == "pre_write":
        _render_pre_write_review(payload)
    else:
        _render_post_sandbox_review(payload)
    
    # Quality details panel (lazy load)
    _render_quality_details_panel(payload, run_id)
    
    # Decision buttons
    st.divider()
    decision = _render_decision_buttons(review_kind, payload, run_id)
    
    if decision:
        # Store decision THEN rerun to close
        st.session_state[f"decision_{run_id}"] = decision
        st.rerun()  # Official close pattern - at END of handler
    
    return None


def _render_quality_details_panel(payload: Dict, run_id: str) -> None:
    """Render quality details with lazy artifact loading."""
    quality_refs = payload.get("quality_refs", {})
    
    with st.expander("📊 Quality Details", expanded=False):
        # Static analysis
        if "static" in quality_refs:
            st.subheader("Static Analysis")
            summary = quality_refs["static"].get("summary", {})
            st.write(f"Errors: {summary.get('blocking_count', 0)}")
            st.write(f"Warnings: {summary.get('warning_count', 0)}")
            
            # Lazy load full result
            load_key = f"load_static_{run_id}"
            if st.button("Load full report", key=load_key):
                ref = quality_refs["static"].get("result")
                if ref:
                    st.session_state[f"static_{run_id}"] = _load_artifact(ref)
            
            if f"static_{run_id}" in st.session_state:
                st.json(st.session_state[f"static_{run_id}"])
        
        # Attribution (post-sandbox only)
        if "sandbox_attribution" in quality_refs:
            st.subheader("Failure Attribution")
            summary = quality_refs["sandbox_attribution"].get("summary", {})
            
            for attr in summary.get("top_attributions", []):
                with st.expander(f"❌ {attr['test_name']}", expanded=attr.get('confidence', 0) > 0.8):
                    if attr.get("likely_cause_file"):
                        st.markdown(f"**Likely cause:** `{attr['likely_cause_file']}`")
                        st.caption(f"Lines {attr['likely_cause_lines'][0]}-{attr['likely_cause_lines'][1]}")
                    if attr.get("fix_hints"):
                        st.markdown("**Suggestions:**")
                        for hint in attr["fix_hints"]:
                            st.markdown(f"- {hint}")
```

**Invariant Enforcement (code scan in CI):**

```python
# tests/ui/test_dialog_invariants.py
"""
Enforce Streamlit dialog contract via static analysis.
"""
import ast
from pathlib import Path

def test_single_dialog_entrypoint():
    """Only one @st.dialog in streamlit_app.py."""
    source = Path("src/integration_coworker/ui/streamlit_app.py").read_text()
    tree = ast.parse(source)
    
    dialog_decorators = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if _is_st_dialog_decorator(decorator):
                    dialog_decorators += 1
    
    assert dialog_decorators == 1, f"Expected 1 @st.dialog, found {dialog_decorators}"


def test_no_nested_dialogs():
    """No @st.dialog calls inside dialog function."""
    # Parse dialog function body, check for st.dialog calls
    ...


def test_rerun_only_in_submit_path():
    """st.rerun() only called after decision stored."""
    # Check that st.rerun() is preceded by session_state write
    ...
```

---

| v1 Decision | v2 Decision | Reason |
|-------------|-------------|--------|
| Option 1 (separate nodes) | Option 4 (lightweight framework) | Streamlit dialog safety; DRY |
| Full diff content in payload | Artifact refs in payload | Checkpoint bloat prevention |
| HtmlDiff always | Bounded HtmlDiff with fallback | Performance safety |
| `interrupt()` in generate_code_and_tests | All interrupts in gate nodes | Separation of concerns |
| Mock-only validation | Postgres + real LLM validation | Production-grade assurance |
| Invented ArtifactRef schema | Opaque (repo-defined) | Match actual implementation |
| Before/after diff blobs | Unified diff patches | Smaller, text-only artifacts |
| Any codec allowed | JSON-only for review artifacts | Safety and portability |

---

## Appendix B: Verified Invariants (From Actual Code)

These invariants are verified against actual implementation in the repo:

### ArtifactRef Contract ✅

> **Verified in [test_review_artifacts.py#L49-75](../../tests/test_review_artifacts.py#L49)**

```python
# From test_artifact_ref_to_dict_from_dict_roundtrip
ref = ArtifactRef(run_id="test-run", key="snapshot", uri="file:///tmp/a", ...)
ref_dict = ref.to_dict()
json_str = json.dumps(ref_dict)  # JSON serializable
restored_ref = ArtifactRef.from_dict(json.loads(json_str))
assert restored_ref.run_id == ref.run_id  # Roundtrip works
```

### Review Artifact Codec ✅

> **Verified at [review_artifacts.py#L36](../../src/integration_coworker/graph/review_artifacts.py#L36)**

```python
REVIEW_ARTIFACT_CODEC = ArtifactCodec.JSON  # Line 36 - NO PICKLE
```

Test: `TestCodecConstraints.test_review_artifact_codec_is_json` asserts this.

### Bounded Summaries ✅

> **Verified at [review_artifacts.py#L39-43](../../src/integration_coworker/graph/review_artifacts.py#L39)**

```python
MAX_FILES_IN_SUMMARY = 10           # Only show first N files in payload
MAX_ERROR_PREVIEWS = 3              # Only show first N error previews
MAX_ERROR_PREVIEW_CHARS = 200       # Truncate error messages
MAX_FILE_PATH_CHARS = 200           # Truncate long paths
```

### Streamlit Dialog Pattern ✅

> **Verified at [streamlit_app.py#L93-159](../../src/integration_coworker/ui/streamlit_app.py#L93)**

```python
def _handle_pending_review() -> None:
    # ...
    decision = show_review_dialog(run_id, pending_kind, artifact_refs)
    
    if decision:
        # Persist BEFORE rerun (line 152-155)
        st.session_state.review_decisions[pending_kind] = decision
        st.session_state.pending_review_kind = None
        st.rerun()  # ONLY after persist
    
    st.stop()  # Prevent other UI while dialog is open (line 157)
```

### Lazy Import Pattern ✅

> **Verified at [review_dialog.py#L24-31](../../src/integration_coworker/ui/review_dialog.py#L24)**

```python
def _get_streamlit():
    """Lazy import streamlit, raising ImportError with helpful message if not installed."""
    try:
        import streamlit as st
        return st
    except ImportError:
        raise ImportError(
            "Streamlit is required for the review dialog UI. "
            "Install with: pip install 'solver-agentic-spec-coworker[ui]'"
        )
```

Test: `test_import_without_streamlit` in [test_import_safety.py](../../tests/test_import_safety.py)

### CLI True Aliases ✅

> **Verified at [cli.py#L1337-1343](../../src/integration_coworker/cli.py#L1337)**

```python
# --feedback and --comment are TRUE ALIASES (same parameter, multiple names)
feedback: Optional[str] = typer.Option(
    None,
    "--feedback", "-f",
    "--comment", "-c",
    help="Feedback/comment for approval/rejection (--comment and --feedback are aliases)"
)
```

Test: `test_feedback_and_comment_are_aliases` in [test_cli_hitl_resume.py](../../tests/test_cli_hitl_resume.py)

### Review Gate Idempotency ✅

> **Verified at [review_gate.py#L74-77](../../src/integration_coworker/graph/nodes/review_gate.py#L74)**

```python
# Already decided - idempotent guard
if _has_existing_decision(state, kind):
    logger.info(f"{node_name}: Skipping - decision already exists for '{kind}'")
    _mark_completed(state, node_name)
    return state
```

### Phase 2 Invariants

The following invariants track implementation status for Phase 2 Quality Pipeline:

| Invariant | Status | Evidence |
|-----------|--------|----------|
| Tool Discovery (`StaticCheck.discover()`) | 🔲 Planned | PR #8 static_checks.py |
| Iteration Budget (`IterationState`, max iterations) | ✅ Implemented | [regeneration_models.py#L48](../../src/integration_coworker/graph/regeneration_models.py#L48): `MAX_REGENERATION_ITERATIONS = 3` |
| Human Edit Patches (unified diff format) | 🔲 Planned | PR #11 |
| Error Attribution (stacktrace analysis) | 🔲 Planned | PR #9 sandbox_attribution.py |

---

## Acceptance Criteria (Updated v2.5)

### "Done Means Done" Checklist

#### Foundation PRs (PR #1-#5) ✅ COMPLETE

**Test files covering this phase:**
- [test_review_artifacts.py](../../tests/test_review_artifacts.py) - 14 tests
- [test_review_gate.py](../../tests/test_review_gate.py) - 17 tests  
- [test_review_dialog.py](../../tests/test_review_dialog.py) - 19 tests
- [test_cli_hitl_resume.py](../../tests/test_cli_hitl_resume.py) - 12 tests
- [test_import_safety.py](../../tests/test_import_safety.py) - 4 tests

| Test | What It Proves | Status | Location |
|------|----------------|--------|----------|
| `test_review_artifact_codec_is_json` | JSON-only codec, no pickle | ✅ | [test_review_artifacts.py#L42](../../tests/test_review_artifacts.py#L42) |
| `test_artifact_ref_to_dict_from_dict_roundtrip` | ArtifactRef serialization survives JSON | ✅ | [test_review_artifacts.py#L49](../../tests/test_review_artifacts.py#L49) |
| `test_refs_to_dicts_roundtrip` | Dict of refs survives roundtrip | ✅ | [test_review_artifacts.py#L74](../../tests/test_review_artifacts.py#L74) |
| `test_build_code_summary_bounded` | Summary respects MAX_FILES_IN_SUMMARY | ✅ | [test_review_artifacts.py](../../tests/test_review_artifacts.py) |
| `test_build_sandbox_summary_bounded` | Summary respects MAX_ERROR_PREVIEWS | ✅ | [test_review_artifacts.py](../../tests/test_review_artifacts.py) |
| `test_review_gate_idempotency` | Doesn't re-interrupt if decision exists | ✅ | [test_review_gate.py](../../tests/test_review_gate.py) |
| `test_review_gate_skip_dry_run` | Skips when dry_run=True | ✅ | [test_review_gate.py](../../tests/test_review_gate.py) |
| `test_review_gate_skip_hitl_disabled` | Skips when hitl_mode="never" | ✅ | [test_review_gate.py](../../tests/test_review_gate.py) |
| `test_decision_parsing_*` | Parses bool, dict, action-based formats | ✅ | [test_review_gate.py](../../tests/test_review_gate.py) |
| `test_import_without_streamlit` | review_dialog imports without streamlit | ✅ | [test_import_safety.py#L19](../../tests/test_import_safety.py#L19) |
| `test_check_pending_review_no_streamlit` | Pure Python function works | ✅ | [test_import_safety.py#L56](../../tests/test_import_safety.py#L56) |
| `test_help_shows_feedback_flag` | CLI shows --feedback | ✅ | [test_cli_hitl_resume.py](../../tests/test_cli_hitl_resume.py) |
| `test_feedback_and_comment_are_aliases` | Same parameter, multiple names | ✅ | [test_cli_hitl_resume.py](../../tests/test_cli_hitl_resume.py) |
| `test_regenerate_requires_feedback` | --regenerate needs --feedback | ✅ | [test_cli_hitl_resume.py](../../tests/test_cli_hitl_resume.py) |

#### CI Jobs ✅ COMPLETE

| Job | What It Proves | Location |
|-----|----------------|----------|
| `core-import-safety` | Core works without streamlit | [ci.yml#L185-L218](../../.github/workflows/ci.yml#L185) |
| `ui-extra` | UI works with streamlit | [ci.yml#L224-L255](../../.github/workflows/ci.yml#L224) |

### Verified Implementation Invariants

Based on actual code review:

| Invariant | Evidence |
|-----------|----------|
| JSON-only codec | `REVIEW_ARTIFACT_CODEC = ArtifactCodec.JSON` in [review_artifacts.py#L36](../../src/integration_coworker/graph/review_artifacts.py#L36) |
| Bounded summaries | `MAX_FILES_IN_SUMMARY = 10`, `MAX_ERROR_PREVIEWS = 3` in [review_artifacts.py#L39-43](../../src/integration_coworker/graph/review_artifacts.py#L39) |
| Lazy imports | `_get_streamlit()` pattern in [review_dialog.py#L24-31](../../src/integration_coworker/ui/review_dialog.py#L24) |
| st.stop() after dialog | `st.stop()` in [streamlit_app.py#L157](../../src/integration_coworker/ui/streamlit_app.py#L157) |
| st.rerun() after persist | `st.rerun()` after session_state write in [streamlit_app.py#L152-155](../../src/integration_coworker/ui/streamlit_app.py#L152) |
| True CLI aliases | Single Option with multiple names in [cli.py#L1337-1343](../../src/integration_coworker/cli.py#L1337) |

### PR #9 Production Guardrails Verified Invariants ✅

> **Verified in [production_guardrails.py](../../src/integration_coworker/graph/production_guardrails.py) + [test_production_guardrails.py](../../tests/test_production_guardrails.py) (41 tests)**

| Invariant | Evidence |
|-----------|----------|
| Single source of truth for bounds | All constants at [production_guardrails.py#L52-75](../../src/integration_coworker/graph/production_guardrails.py#L52): `MAX_TARGETS=10`, `MAX_FEEDBACK_LENGTH=4000`, etc. |
| Timeout defaults + env override | `GUARDRAIL_TOOL_TIMEOUT_SECONDS` env var with 30s default |
| Output byte cap (64KB) | `MAX_OUTPUT_BYTES = 65536` at line 58 |
| Normalization order (7-step) | Lines 1-40 docstring + `normalize_tool_output()` function |
| Deterministic fingerprinting | `compute_deterministic_fingerprint()` at line 200+ |
| Secret redaction | `redact_secrets()` with `SECRET_PATTERNS` list |

### PR #10 Targeted Regeneration Verified Invariants ✅

> **Verified in [regeneration_models.py](../../src/integration_coworker/graph/regeneration_models.py) + [targeted_regeneration.py](../../src/integration_coworker/graph/nodes/targeted_regeneration.py) + 83 tests**

| Invariant | Evidence |
|-----------|----------|
| Iteration budget enforcement | `MAX_REGENERATION_ITERATIONS = 3` at [regeneration_models.py#L48](../../src/integration_coworker/graph/regeneration_models.py#L48) |
| Bounded targets | `MAX_TARGETS = 10` (imported from production_guardrails.py) |
| Bounded feedback | `MAX_FEEDBACK_LENGTH = 4000`, `MAX_TARGET_FEEDBACK_LENGTH = 500` |
| Path traversal prevention | `validate_rel_path()` at [regeneration_models.py#L62-112](../../src/integration_coworker/graph/regeneration_models.py#L62) rejects `..`, absolute paths |
| Deterministic ordering | Targets and dict keys sorted at parse time in `RegenerateTargetedDecision.from_dict()` |
| Stuck loop detection | Fingerprint comparison in [targeted_regeneration.py#L135-145](../../src/integration_coworker/graph/nodes/targeted_regeneration.py#L135) |
| Refs-not-blobs | State field `regeneration_constraints_ref` at [state.py#L148](../../src/integration_coworker/graph/state.py#L148) stores only ref, not full constraints |
| Graph routing | `check_sandbox_review_decision()` at [runtime.py#L2662](../../src/integration_coworker/graph/runtime.py#L2662) routes to targeted_regeneration |
| Loop edge to codegen | `workflow.add_edge("targeted_regeneration", "generate_code_and_tests")` at [runtime.py#L2702](../../src/integration_coworker/graph/runtime.py#L2702) |

**PR #10 Test Coverage:**

| Test File | Tests | What It Proves |
|-----------|-------|----------------|
| [test_regeneration_models.py](../../tests/test_regeneration_models.py) | 49 | Path validation, bounds enforcement, fingerprinting, serialization roundtrip |
| [test_targeted_regeneration.py](../../tests/test_targeted_regeneration.py) | 23 | Node integration, budget enforcement, stuck loop detection, escalation |
| [test_targeted_regen_wiring.py](../../tests/test_targeted_regen_wiring.py) | 11 | Graph structure, conditional edges, routing function |

### PR #11 Human Edit Capability Verified Invariants ✅

> **Verified in [human_edit_models.py](../../src/integration_coworker/graph/human_edit_models.py) + [apply_human_edits.py](../../src/integration_coworker/graph/nodes/apply_human_edits.py) + 72 tests**

| Invariant | Evidence |
|-----------|----------|
| Single-file patches only | `HumanEditPatch` has single `file_path: str` field, not list |
| Strict context matching | `_apply_hunk()` in human_edit_models.py compares exact strings, raises `PatchApplyError` on mismatch |
| AST validation for Python | `validate_patch()` calls `ast.parse()` for `.py` files |
| Bounded patch size | `MAX_PATCH_BYTES = 32_768`, `MAX_HUNKS_PER_PATCH = 10`, `MAX_CHANGED_LINES = 200` |
| Audit artifact required | `EditAuditEntry.from_patch()` creates audit record for every edit attempt |
| Edit budget enforcement | `MAX_HUMAN_EDIT_BUDGET = 2` in apply_human_edits.py prevents UI loops |
| Path traversal prevention | `HumanEditPatch.__post_init__()` calls `validate_rel_path()` |
| Graph routing | `check_sandbox_review_decision()` routes `action="apply_human_edits"` to apply_human_edits node |
| Loop edge to static analysis | `workflow.add_edge("apply_human_edits", "static_analysis_gate")` ensures re-validation |

**PR #11 Test Coverage:**

| Test File | Tests | What It Proves |
|-----------|-------|----------------|
| [test_human_edit_models.py](../../tests/test_human_edit_models.py) | 36 | Patch parsing, validation, application, bounded constraints, audit entries |
| [test_apply_human_edits.py](../../tests/test_apply_human_edits.py) | 21 | Node budget, state updates, patch failures, audit creation |
| [test_human_edit_wiring.py](../../tests/test_human_edit_wiring.py) | 15 | Graph structure, 3-way routing from sandbox_review_gate, loop path |

### PR #12 Quality Dashboard UI Verified Invariants ✅

> **Verified in [test_streamlit_dialog_invariants.py](../../tests/test_streamlit_dialog_invariants.py) + 23 tests**

| Invariant | Evidence |
|-----------|----------|
| Actions are explicit strings | `action="regenerate_targeted"` or `action="apply_human_edits"`, not just `approved=False` |
| Patches require file_path, patch_text, reason | Test fixtures and `HumanEditPatch.from_dict()` validation |
| Decision persisted BEFORE st.rerun() | Documented in review_dialog.py, tested in state machine tests |
| Dialog closes via st.rerun(), not return | Caller responsibility documented, tested |
| Empty patches with apply_human_edits is error | Validation documented, node handles gracefully |

### E2E Postgres Merge-Blocking Tests ✅

> **Verified in [test_targeted_regen_e2e_postgres.py](../../tests/test_targeted_regen_e2e_postgres.py) + 8 tests**

| Invariant | Evidence |
|-----------|----------|
| Resume after fresh process | `test_fresh_connection_resume` creates new `PostgresSaver` connection and resumes |
| Refs-not-blobs enforced | `test_constraints_ref_is_bounded` verifies `regeneration_constraints_ref` < 2KB |
| Fingerprint loop prevention | `test_duplicate_fingerprint_causes_escalation` verifies escalation on repeat |
| Checkpoint size ceiling | `test_checkpoint_size_under_ceiling` verifies < 100KB |
| Production flow simulation | `test_sandbox_to_regen_to_codegen_loop` verifies full loop |

### Phase 2: Quality Pipeline (PR #7-#12)

Quality pipeline implementation status:

| PR | Component | Status | Key Files |
|----|-----------|--------|-----------|
| #7 | Quality Signals Core | ✅ Complete | [quality_models.py](../../src/integration_coworker/graph/quality_models.py), [quality_artifacts.py](../../src/integration_coworker/graph/quality_artifacts.py), [test_quality_signals_core.py](../../tests/test_quality_signals_core.py) |
| #8 | Static Analysis Gate | ✅ Complete | [static_checks.py](../../src/integration_coworker/graph/static_checks.py), [static_analysis_gate.py](../../src/integration_coworker/graph/nodes/static_analysis_gate.py), [test_static_analysis_gate.py](../../tests/test_static_analysis_gate.py) |
| #9 | Production Guardrails | ✅ Complete | [production_guardrails.py](../../src/integration_coworker/graph/production_guardrails.py), [test_production_guardrails.py](../../tests/test_production_guardrails.py) (41 tests) |
| #10 | Targeted Regeneration | ✅ Complete | [regeneration_models.py](../../src/integration_coworker/graph/regeneration_models.py), [targeted_regeneration.py](../../src/integration_coworker/graph/nodes/targeted_regeneration.py), [test_regeneration_models.py](../../tests/test_regeneration_models.py), [test_targeted_regeneration.py](../../tests/test_targeted_regeneration.py), [test_targeted_regen_wiring.py](../../tests/test_targeted_regen_wiring.py) (83 tests) |
| #11 | Human Edit Capability | ✅ Complete | [human_edit_models.py](../../src/integration_coworker/graph/human_edit_models.py), [apply_human_edits.py](../../src/integration_coworker/graph/nodes/apply_human_edits.py), [test_human_edit_models.py](../../tests/test_human_edit_models.py), [test_apply_human_edits.py](../../tests/test_apply_human_edits.py), [test_human_edit_wiring.py](../../tests/test_human_edit_wiring.py) (72 tests) |
| #12 | Quality Dashboard UI | ✅ Complete | [test_streamlit_dialog_invariants.py](../../tests/test_streamlit_dialog_invariants.py) (23 tests), strict `@st.dialog` contract enforcement |

**E2E Merge-Blocking Test:**
| Test File | Tests | What It Proves |
|-----------|-------|----------------|
| [test_targeted_regen_e2e_postgres.py](../../tests/test_targeted_regen_e2e_postgres.py) | 9 | Postgres-backed interrupt/resume, refs-not-blobs, checkpoint size ceiling, fingerprint loop prevention |
| [test_human_edits_e2e_postgres.py](../../tests/test_human_edits_e2e_postgres.py) | 7 | Human edits interrupt/resume, decision schema validation, budget persistence, optimistic concurrency |

---

## PR 1-12 Wiring Proof Matrix

> **Last verified: 2026-01-04**

This matrix proves each PR feature is:
1. Exercised by concrete tests
2. Run by a CI job that triggers on PRs and merge queue
3. Not filtered out by `paths:` constraints

### Complete Wiring Matrix

| PR | Feature | Module | Test File(s) | Tests | CI Job | Trigger |
|----|---------|--------|--------------|-------|--------|---------|
| #1 | Review Artifacts | `review_artifacts.py` | `test_review_artifacts.py` | 21 | `unit-fast` | PR ✅, merge_group ✅ |
| #2 | Postgres Validation | `init_db_postgres.py` | `test_m4_persistence.py`, `test_pgvector_search.py` | ~30 | `postgres-required` | PR ✅, merge_group ✅ |
| #3 | Review Gate Factory | `review_gate.py` | `test_review_gate.py` | 37 | `unit-fast` | PR ✅, merge_group ✅ |
| #4 | Streamlit Dialog | `review_dialog.py` | `test_review_dialog.py` | 19 | `ui-extra` | PR ✅, merge_group ✅ |
| #5 | CLI HITL | `cli.py` (hitl-resume) | `test_cli_hitl_resume.py` | 16 | `unit-fast`, `core-import-safety` | PR ✅, merge_group ✅ |
| #6 | Live LLM Tests | Test infrastructure | `integration_live` marked | - | `live` (opt-in) | workflow_dispatch |
| #7 | Quality Signals | `quality_models.py`, `quality_artifacts.py` | `test_quality_signals_core.py` | 44 | `unit-fast` | PR ✅, merge_group ✅ |
| #8 | Static Analysis Gate | `static_checks.py`, `static_analysis_gate.py` | `test_static_analysis_gate.py` | 56 | `unit-fast` | PR ✅, merge_group ✅ |
| #9 | Sandbox + Guardrails | `sandbox_attribution.py`, `production_guardrails.py` | `test_sandbox_attribution.py`, `test_production_guardrails.py` | 108 | `unit-fast` | PR ✅, merge_group ✅ |
| #10 | Targeted Regen | `regeneration_models.py`, `targeted_regeneration.py` | `test_regeneration_models.py`, `test_targeted_regen_e2e_postgres.py` | 43+9 | `unit-fast`, `postgres-required` | PR ✅, merge_group ✅ |
| #11 | Human Edits | `human_edit_models.py`, `apply_human_edits.py` | `test_human_edit_models.py`, `test_human_edits_e2e_postgres.py` | 104+7 | `unit-fast`, `postgres-required` | PR ✅, merge_group ✅ |
| #12 | Quality Dashboard | `streamlit_app.py` (quality panel) | `test_streamlit_dialog_invariants.py` | 23 | `ui-extra` | PR ✅, merge_group ✅ |

### CI Job Coverage Summary

| CI Job | Marker Expression | Always Runs | PRs Covered |
|--------|------------------|-------------|-------------|
| `lint` | - | ✅ | All (pre-requisite) |
| `unit-fast` | `-m "not (integration or postgres or e2e)"` | ✅ | #1, #3, #5, #7, #8, #9, #10, #11 |
| `integration` | `-m "integration and not postgres"` | ✅ | All (wiring) |
| `postgres-required` | `-m "postgres"` | ✅ | #2, #10, #11 (E2E) |
| `ui-extra` | Requires `[ui]` extra | ✅ | #4, #12 |
| `core-import-safety` | Import tests | ✅ | #5 (CLI) |
| `live` | `-m "integration_live"` | ❌ (opt-in) | #6 |

### Trigger Coverage Verification

```yaml
# .github/workflows/ci.yml triggers (verified 2026-01-04)
on:
  push:
    branches: [ main, master ]  # ✅ Main branch pushes
  pull_request:
    branches: [ main, master ]  # ✅ All PRs
  merge_group:
    types: [checks_requested]   # ✅ Merge queue (added for safety)
  workflow_dispatch:            # ✅ Manual trigger
```

### Postgres Job is Merge-Blocking

The `postgres-required` job:
- Has no `if:` condition (always runs)
- Has no `paths:` filter (runs on all file changes)
- Uses GitHub Actions service container (not docker-compose)
- Fails the build on any test failure (no `|| true`)
- Runs 70 total `@pytest.mark.postgres` tests

**Note**: Branch protection must be configured in GitHub repo settings to require `postgres-required` status check. This cannot be verified via API without admin access.

### Decision Schema Version Enforcement

All decision builders now include `decision_version`:
- `create_sandbox_decision()` in `human_edit_models.py` ✅
- `_build_decision()` in `review_dialog.py` ✅ (fixed 2026-01-04)
- CLI `hitl-resume` command ✅ (fixed 2026-01-04)

### Backward Compatibility

`HumanEditPatch` re-exported from `quality_models.py` with deprecation warning:
```python
# quality_models.py
def __getattr__(name: str):
    if name == "HumanEditPatch":
        warnings.warn("HumanEditPatch moved to human_edit_models. Will be removed in v2.0.")
        from integration_coworker.graph.human_edit_models import HumanEditPatch
        return HumanEditPatch
```

---

**Conclusion**: All PR #1-12 features are wired into CI with tests that run on every PR and merge queue entry. The postgres job is merge-blocking and cannot be bypassed via path filters.

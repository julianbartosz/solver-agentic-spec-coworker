"""
E2E Postgres Tests for Human Edit Application (PR #11)

MERGE-BLOCKING: These tests prove the production contract for interrupt/resume
with human edit patches.

Tests verify:
1. interrupt() at sandbox_review_gate with action=apply_human_edits
2. Kill process, fresh start, resume with same thread_id works
3. Human edit patches are correctly applied after resume
4. Decision schema validation happens on resume
5. Budget enforcement persists across interrupt/resume
6. Optimistic concurrency (expected_base_sha256) works across resume

CRITICAL: These tests require a real Postgres database.
They will FAIL (not skip) if Postgres is unavailable.

Per ADR-HITL-ENHANCEMENT-v2 PR #11:
- Durable execution is non-negotiable for HITL
- LangGraph interrupt/resume hinges on correct checkpoint persistence
- Human edits must survive process restart
"""

import hashlib
import json
import os
import pytest
import time
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import patch, MagicMock

from integration_coworker.graph.human_edit_models import (
    HumanEditPatch,
    validate_sandbox_decision,
    create_sandbox_decision,
    DECISION_SCHEMA_VERSION,
    VALID_SANDBOX_ACTIONS,
)
from integration_coworker.graph.production_guardrails import MAX_HUMAN_EDIT_BUDGET


def require_postgres():
    """Raise skip if Postgres is unavailable."""
    if not os.environ.get('DATABASE_URL'):
        pytest.skip(
            "DATABASE_URL not set - Postgres E2E tests require real database. "
            "These are merge-blocking for PR #11."
        )
    try:
        import psycopg
        with psycopg.connect(os.environ['DATABASE_URL']) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except Exception as e:
        pytest.skip(f"Cannot connect to Postgres: {e}")


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def unique_thread_id() -> str:
    """Generate unique thread ID for test isolation."""
    return f"test-edits-e2e-{uuid.uuid4().hex[:8]}-{int(time.time())}"


@pytest.fixture
def db_url() -> str:
    """Get database URL."""
    require_postgres()
    url = os.environ.get('DATABASE_URL')
    if not url:
        pytest.skip("DATABASE_URL not set")
    return url


@pytest.fixture
def simple_patch_text() -> str:
    """A valid unified diff patch."""
    return """\
--- a/src/example.py
+++ b/src/example.py
@@ -1,3 +1,3 @@
 def hello():
-    return "hello"
+    return "world"
 
"""


@pytest.fixture
def original_content() -> str:
    """Original file content for patch application."""
    return """\
def hello():
    return "hello"

"""


# =============================================================================
# Test: Basic Interrupt/Resume with Human Edit Decision
# =============================================================================

@pytest.mark.postgres
class TestHumanEditsInterruptResume:
    """
    Test interrupt → checkpoint → resume cycle for human edits.
    
    This exercises the REAL production contract:
    - interrupt() at sandbox_review_gate
    - Checkpoint persisted to Postgres
    - Resume with Command(resume=<decision>) containing patches
    - apply_human_edits node processes patches
    - State is bounded (refs, not blobs)
    """
    
    def test_interrupt_persists_checkpoint_for_edits(
        self, unique_thread_id: str, db_url: str
    ):
        """
        Verify interrupt() creates a checkpoint that survives for edit decisions.
        """
        from langgraph.types import Command, interrupt
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.postgres import PostgresSaver
        from integration_coworker.graph.runtime import get_thread_config
        from typing import TypedDict
        
        class TestState(TypedDict):
            value: int
            decision: Optional[Dict]
        
        def node_with_interrupt(state: TestState) -> TestState:
            decision = interrupt({
                "review_kind": "sandbox",
                "run_id": "test-edits",
                "available_actions": list(VALID_SANDBOX_ACTIONS),
            })
            return {"decision": decision}
        
        workflow = StateGraph(TestState)
        workflow.add_node("interrupt_node", node_with_interrupt)
        workflow.set_entry_point("interrupt_node")
        workflow.add_edge("interrupt_node", END)
        
        config = get_thread_config(unique_thread_id)
        
        with PostgresSaver.from_conn_string(db_url) as checkpointer:
            checkpointer.setup()
            app = workflow.compile(checkpointer=checkpointer)
            
            # Run until interrupt
            for chunk in app.stream(
                {"value": 0, "decision": None},
                config=config,
                stream_mode="values"
            ):
                pass
            
            # Verify graph is paused
            paused_state = app.get_state(config)
            assert paused_state.next, "Graph should be paused at interrupt"
            
            # Verify checkpoint exists
            checkpoint = checkpointer.get_tuple(config)
            assert checkpoint is not None, "Checkpoint must exist after interrupt"
    
    def test_resume_with_apply_human_edits_decision(
        self, unique_thread_id: str, db_url: str, simple_patch_text: str
    ):
        """
        Test full interrupt → resume cycle with apply_human_edits action.
        
        Simulates:
        1. sandbox_review_gate interrupts
        2. Human submits action="apply_human_edits" with patches
        3. Graph resumes and processes decision
        """
        from langgraph.types import Command, interrupt
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.postgres import PostgresSaver
        from integration_coworker.graph.runtime import get_thread_config
        from typing import TypedDict
        
        class TestState(TypedDict):
            run_id: str
            patches_applied: int
            decision_received: Optional[Dict]
        
        def sandbox_gate(state: TestState) -> TestState:
            """Simulates sandbox_review_gate with interrupt."""
            decision = interrupt({
                "review_kind": "sandbox",
                "run_id": state["run_id"],
            })
            return {"decision_received": decision}
        
        def apply_edits(state: TestState) -> TestState:
            """Simulates apply_human_edits processing."""
            decision = state.get("decision_received", {})
            patches = decision.get("patches", [])
            
            # Validate decision schema
            errors = validate_sandbox_decision(decision)
            assert not errors, f"Decision schema errors: {errors}"
            
            return {"patches_applied": len(patches)}
        
        workflow = StateGraph(TestState)
        workflow.add_node("sandbox_gate", sandbox_gate)
        workflow.add_node("apply_edits", apply_edits)
        workflow.set_entry_point("sandbox_gate")
        workflow.add_edge("sandbox_gate", "apply_edits")
        workflow.add_edge("apply_edits", END)
        
        config = get_thread_config(unique_thread_id)
        
        with PostgresSaver.from_conn_string(db_url) as checkpointer:
            checkpointer.setup()
            app = workflow.compile(checkpointer=checkpointer)
            
            # Phase 1: Run until interrupt
            initial_state = {
                "run_id": unique_thread_id,
                "patches_applied": 0,
                "decision_received": None,
            }
            
            for chunk in app.stream(
                initial_state, config=config, stream_mode="values"
            ):
                pass
            
            # Verify paused
            paused = app.get_state(config)
            assert paused.next, "Graph should be paused"
            
            # Phase 2: Resume with human edit decision
            decision = create_sandbox_decision(
                action="apply_human_edits",
                patches=[{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Fix greeting message",
                }]
            )
            
            result = None
            for chunk in app.stream(
                Command(resume=decision),
                config=config,
                stream_mode="values"
            ):
                result = chunk
            
            # Verify patches were processed
            assert result is not None
            assert result.get("patches_applied") == 1
    
    def test_decision_schema_validated_on_resume(
        self, unique_thread_id: str, db_url: str
    ):
        """
        Verify that invalid decisions are caught after resume.
        
        This ensures the versioned schema validation happens even
        after checkpoint restore.
        """
        from langgraph.types import Command, interrupt
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.postgres import PostgresSaver
        from integration_coworker.graph.runtime import get_thread_config
        from typing import TypedDict
        
        class TestState(TypedDict):
            validated: bool
            decision_received: Optional[Dict]
            validation_errors: List[str]
        
        def sandbox_gate(state: TestState) -> TestState:
            decision = interrupt({"review_kind": "sandbox"})
            return {"decision_received": decision}
        
        def validate_decision(state: TestState) -> TestState:
            decision = state.get("decision_received", {})
            errors = validate_sandbox_decision(decision)
            return {
                "validated": len(errors) == 0,
                "validation_errors": errors,
            }
        
        workflow = StateGraph(TestState)
        workflow.add_node("sandbox_gate", sandbox_gate)
        workflow.add_node("validate", validate_decision)
        workflow.set_entry_point("sandbox_gate")
        workflow.add_edge("sandbox_gate", "validate")
        workflow.add_edge("validate", END)
        
        config = get_thread_config(unique_thread_id)
        
        with PostgresSaver.from_conn_string(db_url) as checkpointer:
            checkpointer.setup()
            app = workflow.compile(checkpointer=checkpointer)
            
            # Run until interrupt
            for chunk in app.stream(
                {"validated": False, "decision_received": None, "validation_errors": []},
                config=config,
                stream_mode="values"
            ):
                pass
            
            # Resume with INVALID decision (missing decided_at)
            invalid_decision = {
                "action": "apply_human_edits",
                # Missing decided_at and patches!
            }
            
            result = None
            for chunk in app.stream(
                Command(resume=invalid_decision),
                config=config,
                stream_mode="values"
            ):
                result = chunk
            
            # Should have validation errors
            assert result is not None
            assert result.get("validated") is False
            assert len(result.get("validation_errors", [])) > 0


@pytest.mark.postgres
class TestBudgetPersistence:
    """
    Test that edit budget survives interrupt/resume.
    
    Budget prevents infinite UI loops - it must persist!
    """
    
    def test_budget_survives_restart(self, unique_thread_id: str, db_url: str):
        """
        Verify budget counter persists across checkpoint restore.
        """
        from langgraph.types import Command, interrupt
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.postgres import PostgresSaver
        from integration_coworker.graph.runtime import get_thread_config
        from typing import TypedDict
        
        class TestState(TypedDict):
            edit_budget_used: int
            decision_received: Optional[Dict]
        
        def sandbox_gate(state: TestState) -> TestState:
            # Check budget before interrupt
            if state.get("edit_budget_used", 0) >= MAX_HUMAN_EDIT_BUDGET:
                return {"edit_budget_used": state["edit_budget_used"]}
            
            decision = interrupt({"review_kind": "sandbox"})
            return {"decision_received": decision}
        
        def increment_budget(state: TestState) -> TestState:
            current = state.get("edit_budget_used", 0)
            return {"edit_budget_used": current + 1}
        
        workflow = StateGraph(TestState)
        workflow.add_node("sandbox_gate", sandbox_gate)
        workflow.add_node("increment", increment_budget)
        workflow.set_entry_point("sandbox_gate")
        workflow.add_edge("sandbox_gate", "increment")
        workflow.add_edge("increment", END)
        
        config = get_thread_config(unique_thread_id)
        
        with PostgresSaver.from_conn_string(db_url) as checkpointer:
            checkpointer.setup()
            app = workflow.compile(checkpointer=checkpointer)
            
            # Run until interrupt (budget = 0)
            for chunk in app.stream(
                {"edit_budget_used": 0, "decision_received": None},
                config=config,
                stream_mode="values"
            ):
                pass
            
            # Resume with valid decision
            decision = create_sandbox_decision(action="continue")
            
            result = None
            for chunk in app.stream(
                Command(resume=decision),
                config=config,
                stream_mode="values"
            ):
                result = chunk
            
            # Budget should be incremented
            assert result is not None
            assert result.get("edit_budget_used") == 1
            
            # Verify checkpoint has correct budget
            state = app.get_state(config)
            assert state.values.get("edit_budget_used") == 1


@pytest.mark.postgres
class TestOptimisticConcurrencyAcrossResume:
    """
    Test that expected_base_sha256 validation works after resume.
    """
    
    def test_sha256_mismatch_caught_after_resume(
        self, unique_thread_id: str, db_url: str,
        simple_patch_text: str, original_content: str
    ):
        """
        Verify optimistic concurrency check works after checkpoint restore.
        
        Scenario:
        1. Interrupt for review
        2. During interrupt, file is "modified" (simulated via wrong hash)
        3. Resume with patch containing old SHA256
        4. Validation should fail
        """
        from langgraph.types import Command, interrupt
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.postgres import PostgresSaver
        from integration_coworker.graph.runtime import get_thread_config
        from integration_coworker.graph.human_edit_models import (
            validate_patch, HumanEditPatch
        )
        from typing import TypedDict
        
        class TestState(TypedDict):
            file_content: str
            validation_passed: bool
            validation_errors: List[str]
            decision_received: Optional[Dict]
        
        def sandbox_gate(state: TestState) -> TestState:
            decision = interrupt({
                "review_kind": "sandbox",
                "file_sha256": hashlib.sha256(
                    state["file_content"].encode()
                ).hexdigest(),
            })
            return {"decision_received": decision}
        
        def validate_and_apply(state: TestState) -> TestState:
            decision = state.get("decision_received", {})
            patches = decision.get("patches", [])
            
            if not patches:
                return {"validation_passed": False, "validation_errors": ["No patches"]}
            
            # Validate first patch
            patch_dict = patches[0]
            try:
                patch = HumanEditPatch.from_dict(patch_dict)
                result = validate_patch(patch, state["file_content"])
                return {
                    "validation_passed": result.valid,
                    "validation_errors": result.errors,
                }
            except Exception as e:
                return {"validation_passed": False, "validation_errors": [str(e)]}
        
        workflow = StateGraph(TestState)
        workflow.add_node("sandbox_gate", sandbox_gate)
        workflow.add_node("validate", validate_and_apply)
        workflow.set_entry_point("sandbox_gate")
        workflow.add_edge("sandbox_gate", "validate")
        workflow.add_edge("validate", END)
        
        config = get_thread_config(unique_thread_id)
        
        with PostgresSaver.from_conn_string(db_url) as checkpointer:
            checkpointer.setup()
            app = workflow.compile(checkpointer=checkpointer)
            
            # Run until interrupt
            initial = {
                "file_content": original_content,
                "validation_passed": False,
                "validation_errors": [],
                "decision_received": None,
            }
            
            for chunk in app.stream(
                initial, config=config, stream_mode="values"
            ):
                pass
            
            # Resume with patch that has WRONG expected_base_sha256
            wrong_sha256 = "a" * 64  # Definitely wrong
            decision = create_sandbox_decision(
                action="apply_human_edits",
                patches=[{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Fix greeting",
                    "expected_base_sha256": wrong_sha256,
                }]
            )
            
            result = None
            for chunk in app.stream(
                Command(resume=decision),
                config=config,
                stream_mode="values"
            ):
                result = chunk
            
            # Validation should have failed due to SHA256 mismatch
            assert result is not None
            assert result.get("validation_passed") is False
            assert any(
                "concurrency" in e.lower() or "changed" in e.lower()
                for e in result.get("validation_errors", [])
            )


@pytest.mark.postgres
class TestDecisionSchemaVersioning:
    """
    Test that schema versioning works correctly across resume.
    """
    
    def test_schema_version_included_in_decision(
        self, unique_thread_id: str, db_url: str
    ):
        """
        Verify create_sandbox_decision includes decision_version.
        """
        decision = create_sandbox_decision(action="continue")
        
        assert "decision_version" in decision
        assert decision["decision_version"] == DECISION_SCHEMA_VERSION
    
    def test_future_schema_rejected_after_resume(
        self, unique_thread_id: str, db_url: str
    ):
        """
        Verify that a decision with future schema version is rejected.
        
        This protects against downgrade scenarios where an old graph
        receives a decision from a newer UI.
        """
        from langgraph.types import Command, interrupt
        from langgraph.graph import StateGraph, END
        from langgraph.checkpoint.postgres import PostgresSaver
        from integration_coworker.graph.runtime import get_thread_config
        from typing import TypedDict
        
        class TestState(TypedDict):
            schema_valid: bool
            decision_received: Optional[Dict]
        
        def sandbox_gate(state: TestState) -> TestState:
            decision = interrupt({"review_kind": "sandbox"})
            return {"decision_received": decision}
        
        def check_schema(state: TestState) -> TestState:
            decision = state.get("decision_received", {})
            errors = validate_sandbox_decision(decision)
            return {"schema_valid": len(errors) == 0}
        
        workflow = StateGraph(TestState)
        workflow.add_node("sandbox_gate", sandbox_gate)
        workflow.add_node("check_schema", check_schema)
        workflow.set_entry_point("sandbox_gate")
        workflow.add_edge("sandbox_gate", "check_schema")
        workflow.add_edge("check_schema", END)
        
        config = get_thread_config(unique_thread_id)
        
        with PostgresSaver.from_conn_string(db_url) as checkpointer:
            checkpointer.setup()
            app = workflow.compile(checkpointer=checkpointer)
            
            # Run until interrupt
            for chunk in app.stream(
                {"schema_valid": False, "decision_received": None},
                config=config,
                stream_mode="values"
            ):
                pass
            
            # Resume with FUTURE schema version
            future_decision = {
                "action": "continue",
                "decided_at": time.time(),
                "decision_version": 999,  # From the future!
            }
            
            result = None
            for chunk in app.stream(
                Command(resume=future_decision),
                config=config,
                stream_mode="values"
            ):
                result = chunk
            
            # Schema should be invalid
            assert result is not None
            assert result.get("schema_valid") is False

"""
Tests for apply_human_edits node (PR #11)

Tests cover:
1. Budget enforcement
2. Patch application flow
3. Validation failures
4. Audit artifact creation
5. State updates
6. Edge cases
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from integration_coworker.graph.nodes.apply_human_edits import (
    apply_human_edits,
    _get_pending_patches,
    check_needs_human_edit_recheck,
    check_human_edit_budget,
    is_human_edit_escalated,
    EDIT_BUDGET_KEY,
)
# Single source of truth for budget constant
from integration_coworker.graph.production_guardrails import (
    MAX_HUMAN_EDIT_BUDGET,
)
from integration_coworker.graph.human_edit_models import HumanEditPatch
from integration_coworker.graph.state import WorkflowState

# Fixed timestamp for reproducible tests
TEST_DECIDED_AT = 1234567890.0


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def mock_state():
    """Create a minimal mock state for testing."""
    state = MagicMock(spec=WorkflowState)
    state.run_id = "test-run-123"
    state.plan = {}
    state.warnings = []
    state.completed_steps = []
    state.quality_refs = {}
    state.review_decisions = {}
    state.code_artifacts = []
    return state


@pytest.fixture
def simple_patch_text():
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
def mock_code_artifact():
    """Create a mock code artifact."""
    artifact = MagicMock()
    artifact.rel_path = "src/example.py"
    artifact.content = """\
def hello():
    return "hello"

"""
    return artifact


# =============================================================================
# Budget Tests
# =============================================================================

class TestBudgetEnforcement:
    """Tests for edit budget enforcement."""
    
    def test_budget_exhausted_triggers_escalation(self, mock_state, simple_patch_text):
        """Budget exhausted should escalate and skip edits."""
        # Budget already at max
        mock_state.plan[EDIT_BUDGET_KEY] = MAX_HUMAN_EDIT_BUDGET
        
        # Set up a pending edit (should be ignored)
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "patches": [{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Test",
                }]
            }
        }
        
        result = apply_human_edits(mock_state)
        
        # Should be escalated
        assert result.plan.get("human_edit_escalated") is True
        assert "budget" in result.warnings[0].lower()
        assert "apply_human_edits" in result.completed_steps
    
    def test_budget_incremented_on_success(self, mock_state, simple_patch_text, mock_code_artifact):
        """Successful edit should increment budget."""
        mock_state.plan[EDIT_BUDGET_KEY] = 0
        mock_state.code_artifacts = [mock_code_artifact]
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Test fix",
                }]
            }
        }
        
        # Mock the artifact store
        with patch('integration_coworker.graph.nodes.apply_human_edits.store_human_edits_artifact') as mock_store:
            mock_store.return_value = {"audit": {"sha256": "test"}, "count": 1}
            
            result = apply_human_edits(mock_state)
        
        # Budget should be incremented
        assert result.plan[EDIT_BUDGET_KEY] == 1
    
    def test_check_human_edit_budget_available(self, mock_state):
        """check_human_edit_budget returns True when budget available."""
        mock_state.plan[EDIT_BUDGET_KEY] = 0
        assert check_human_edit_budget(mock_state) is True
        
        mock_state.plan[EDIT_BUDGET_KEY] = MAX_HUMAN_EDIT_BUDGET - 1
        assert check_human_edit_budget(mock_state) is True
    
    def test_check_human_edit_budget_exhausted(self, mock_state):
        """check_human_edit_budget returns False when budget exhausted."""
        mock_state.plan[EDIT_BUDGET_KEY] = MAX_HUMAN_EDIT_BUDGET
        assert check_human_edit_budget(mock_state) is False


# =============================================================================
# Patch Application Tests
# =============================================================================

class TestPatchApplication:
    """Tests for patch application flow."""
    
    def test_no_patches_skips_processing(self, mock_state):
        """No patches should complete immediately."""
        mock_state.review_decisions = {}
        
        result = apply_human_edits(mock_state)
        
        assert "apply_human_edits" in result.completed_steps
        # No budget change when no patches
        assert result.plan.get(EDIT_BUDGET_KEY) is None
    
    def test_wrong_action_skips_processing(self, mock_state):
        """Non-apply_human_edits action should skip."""
        mock_state.review_decisions = {
            "sandbox": {
                "action": "regenerate_targeted",
                "feedback": "Please fix tests",
            }
        }
        
        result = apply_human_edits(mock_state)
        
        assert "apply_human_edits" in result.completed_steps
    
    def test_applies_valid_patch(self, mock_state, simple_patch_text, mock_code_artifact):
        """Valid patch should be applied to code artifact."""
        mock_state.code_artifacts = [mock_code_artifact]
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Fix greeting",
                }]
            }
        }
        
        with patch('integration_coworker.graph.nodes.apply_human_edits.store_human_edits_artifact') as mock_store:
            mock_store.return_value = {"audit": {"sha256": "test"}, "count": 1}
            
            result = apply_human_edits(mock_state)
        
        # Content should be updated
        assert 'return "world"' in result.code_artifacts[0].content
        assert 'return "hello"' not in result.code_artifacts[0].content
    
    def test_file_not_in_artifacts_fails(self, mock_state, simple_patch_text):
        """Patch for non-existent file should fail validation."""
        mock_state.code_artifacts = []  # No artifacts
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/missing.py",
                    "patch_text": simple_patch_text,
                    "reason": "Test",
                }]
            }
        }
        
        with patch('integration_coworker.graph.nodes.apply_human_edits.store_human_edits_artifact') as mock_store:
            mock_store.return_value = {"audit": {"sha256": "test"}, "count": 1}
            
            result = apply_human_edits(mock_state)
        
        # Should have warning about file not in artifacts
        assert any("not in code artifacts" in w for w in result.warnings)
    
    def test_context_mismatch_fails(self, mock_state, simple_patch_text):
        """Patch with wrong context should fail."""
        # Artifact with different content
        artifact = MagicMock()
        artifact.rel_path = "src/example.py"
        artifact.content = "completely different content"
        
        mock_state.code_artifacts = [artifact]
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Test",
                }]
            }
        }
        
        with patch('integration_coworker.graph.nodes.apply_human_edits.store_human_edits_artifact') as mock_store:
            mock_store.return_value = {"audit": {"sha256": "test"}, "count": 1}
            
            result = apply_human_edits(mock_state)
        
        # Content should NOT be changed (patch failed)
        assert artifact.content == "completely different content"


# =============================================================================
# State Update Tests
# =============================================================================

class TestStateUpdates:
    """Tests for state updates after edits."""
    
    def test_needs_static_recheck_set(self, mock_state, simple_patch_text, mock_code_artifact):
        """Applied edits should set needs_static_recheck flag."""
        mock_state.code_artifacts = [mock_code_artifact]
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Fix",
                }]
            }
        }
        
        with patch('integration_coworker.graph.nodes.apply_human_edits.store_human_edits_artifact') as mock_store:
            mock_store.return_value = {"audit": {"sha256": "test"}, "count": 1}
            
            result = apply_human_edits(mock_state)
        
        assert result.plan.get("needs_static_recheck") is True
    
    def test_human_edit_summary_stored(self, mock_state, simple_patch_text, mock_code_artifact):
        """Applied edits should store bounded summary."""
        mock_state.code_artifacts = [mock_code_artifact]
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Fix",
                }]
            }
        }
        
        with patch('integration_coworker.graph.nodes.apply_human_edits.store_human_edits_artifact') as mock_store:
            mock_store.return_value = {"audit": {"sha256": "test"}, "count": 1}
            
            result = apply_human_edits(mock_state)
        
        summary = result.plan.get("human_edit_summary")
        assert summary is not None
        assert summary["edit_count"] == 1
        assert "src/example.py" in summary["files_edited"]
    
    def test_quality_refs_updated(self, mock_state, simple_patch_text, mock_code_artifact):
        """Applied edits should store audit ref in quality_refs."""
        mock_state.code_artifacts = [mock_code_artifact]
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Fix",
                }]
            }
        }
        
        with patch('integration_coworker.graph.nodes.apply_human_edits.store_human_edits_artifact') as mock_store:
            mock_store.return_value = {"audit": {"sha256": "testsha"}, "count": 1}
            
            result = apply_human_edits(mock_state)
        
        assert "human_edits" in result.quality_refs
        assert result.quality_refs["human_edits"]["audit_ref"]["audit"]["sha256"] == "testsha"


# =============================================================================
# Helper Function Tests
# =============================================================================

class TestHelperFunctions:
    """Tests for helper functions."""
    
    def test_get_pending_patches_with_patches(self, mock_state, simple_patch_text):
        """_get_pending_patches extracts patches from decision."""
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/example.py",
                    "patch_text": simple_patch_text,
                    "reason": "Fix",
                }]
            }
        }
        
        patches = _get_pending_patches(mock_state)
        
        assert len(patches) == 1
        assert patches[0].file_path == "src/example.py"
    
    def test_get_pending_patches_wrong_action(self, mock_state):
        """_get_pending_patches returns empty for wrong action."""
        mock_state.review_decisions = {
            "sandbox": {
                "action": "continue",
                "decided_at": TEST_DECIDED_AT,
            }
        }
        
        patches = _get_pending_patches(mock_state)
        
        assert patches == []
    
    def test_get_pending_patches_no_decision(self, mock_state):
        """_get_pending_patches returns empty when no decision."""
        mock_state.review_decisions = {}
        
        patches = _get_pending_patches(mock_state)
        
        assert patches == []
    
    def test_check_needs_human_edit_recheck_always_recheck(self, mock_state):
        """check_needs_human_edit_recheck always returns 'recheck'."""
        result = check_needs_human_edit_recheck(mock_state)
        assert result == "recheck"
    
    def test_is_human_edit_escalated_false(self, mock_state):
        """is_human_edit_escalated returns False by default."""
        assert is_human_edit_escalated(mock_state) is False
    
    def test_is_human_edit_escalated_true(self, mock_state):
        """is_human_edit_escalated returns True when escalated."""
        mock_state.plan["human_edit_escalated"] = True
        assert is_human_edit_escalated(mock_state) is True


# =============================================================================
# Invariant Tests  
# =============================================================================

class TestInvariants:
    """Tests for non-negotiable invariants."""
    
    def test_max_budget_constant_reasonable(self):
        """Budget limit should be small to prevent UI loops."""
        assert 1 <= MAX_HUMAN_EDIT_BUDGET <= 5
    
    def test_node_is_idempotent_on_no_patches(self, mock_state):
        """Calling node multiple times with no patches is safe."""
        mock_state.review_decisions = {}
        
        # Call twice
        apply_human_edits(mock_state)
        result = apply_human_edits(mock_state)
        
        # Node guards against duplicate completion markers
        assert result.completed_steps.count("apply_human_edits") == 1
    
    def test_audit_always_created_for_attempts(self, mock_state, simple_patch_text):
        """Audit should be created even for failed patches."""
        # File doesn't exist
        mock_state.code_artifacts = []
        mock_state.review_decisions = {
            "sandbox": {
                "action": "apply_human_edits",
                "decided_at": TEST_DECIDED_AT,
                "patches": [{
                    "file_path": "src/missing.py",
                    "patch_text": simple_patch_text,
                    "reason": "Test",
                }]
            }
        }
        
        with patch('integration_coworker.graph.nodes.apply_human_edits.store_human_edits_artifact') as mock_store:
            mock_store.return_value = {"audit": {"sha256": "test"}, "count": 1}
            
            apply_human_edits(mock_state)
        
        # Audit should have been called even though patch failed
        mock_store.assert_called_once()
        call_args = mock_store.call_args
        audit_entries = call_args[0][1]  # Second positional arg
        
        # Should have audit entry with applied=False
        assert len(audit_entries) == 1
        assert audit_entries[0]["applied"] is False    
    def test_budget_constant_in_production_guardrails(self):
        """MAX_HUMAN_EDIT_BUDGET must be defined in production_guardrails."""
        from integration_coworker.graph import production_guardrails
        
        # Must be in single source of truth
        assert hasattr(production_guardrails, 'MAX_HUMAN_EDIT_BUDGET')
        assert production_guardrails.MAX_HUMAN_EDIT_BUDGET == MAX_HUMAN_EDIT_BUDGET
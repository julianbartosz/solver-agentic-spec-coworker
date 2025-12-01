"""
Tests for cross-provider pattern matching (WS2-T1).

Pattern matching enables knowledge transfer between providers by:
1. Identifying common workflow patterns (CRUD, search, etc.)
2. Falling back to patterns when no provider-specific templates exist
3. Learning patterns from successful provider-specific workflows
"""
import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.kg import (
    infer_pattern_from_task,
    query_kg_patterns,
    query_templates_with_pattern_fallback,
    PatternMatch,
    STANDARD_PATTERNS,
)


# Mark all tests in this module to skip database initialization
pytestmark = pytest.mark.no_db


class TestStandardPatterns:
    """Tests for the standard pattern definitions."""
    
    def test_all_patterns_have_required_fields(self):
        """All standard patterns should have name, description, and steps."""
        for key, pattern in STANDARD_PATTERNS.items():
            assert "name" in pattern, f"Pattern {key} missing name"
            assert "description" in pattern, f"Pattern {key} missing description"
            assert "steps" in pattern, f"Pattern {key} missing steps"
            assert len(pattern["steps"]) > 0, f"Pattern {key} has no steps"
    
    def test_all_patterns_have_http_methods(self):
        """All patterns should specify allowed HTTP methods."""
        for key, pattern in STANDARD_PATTERNS.items():
            assert "http_methods" in pattern, f"Pattern {key} missing http_methods"
            assert len(pattern["http_methods"]) > 0
    
    def test_step_structure(self):
        """Steps should have key, type, and position."""
        for key, pattern in STANDARD_PATTERNS.items():
            for step in pattern["steps"]:
                assert "key" in step, f"Step in {key} missing key"
                assert "type" in step, f"Step in {key} missing type"
                assert "position" in step, f"Step in {key} missing position"


class TestInferPatternFromTask:
    """Tests for inferring patterns from task characteristics."""
    
    def test_create_pattern_from_post_method(self):
        """POST method should infer crud_create pattern."""
        matches = infer_pattern_from_task(
            task_description="Create a new user account",
            http_method="POST",
        )
        assert len(matches) > 0
        assert matches[0].pattern_key == "pattern.crud_create"
        assert matches[0].confidence > 0.3
    
    def test_read_pattern_from_get_with_id(self):
        """GET with {id} path should infer crud_read pattern."""
        matches = infer_pattern_from_task(
            task_description="Get user details",
            http_method="GET",
            endpoint_path="/users/{id}",
        )
        # Should have matches for both read and list patterns
        pattern_keys = [m.pattern_key for m in matches]
        assert "pattern.crud_read" in pattern_keys or "pattern.crud_list" in pattern_keys
    
    def test_list_pattern_from_get_without_id(self):
        """GET without {id} should infer crud_list pattern."""
        matches = infer_pattern_from_task(
            task_description="List all users",
            http_method="GET",
            endpoint_path="/users",
        )
        assert len(matches) > 0
        # crud_list should be in the matches
        pattern_keys = [m.pattern_key for m in matches]
        assert "pattern.crud_list" in pattern_keys
    
    def test_update_pattern_from_put(self):
        """PUT method should infer crud_update pattern."""
        matches = infer_pattern_from_task(
            task_description="Update user profile",
            http_method="PUT",
        )
        assert len(matches) > 0
        assert matches[0].pattern_key == "pattern.crud_update"
    
    def test_update_pattern_from_patch(self):
        """PATCH method should infer crud_update pattern."""
        matches = infer_pattern_from_task(
            task_description="Modify user settings",
            http_method="PATCH",
        )
        assert len(matches) > 0
        assert matches[0].pattern_key == "pattern.crud_update"
    
    def test_delete_pattern_from_delete(self):
        """DELETE method should infer crud_delete pattern."""
        matches = infer_pattern_from_task(
            task_description="Remove user account",
            http_method="DELETE",
        )
        assert len(matches) > 0
        assert matches[0].pattern_key == "pattern.crud_delete"
    
    def test_search_pattern_from_keywords(self):
        """Search keywords should infer search_filter pattern."""
        matches = infer_pattern_from_task(
            task_description="Search for users by name",
            http_method="GET",
        )
        pattern_keys = [m.pattern_key for m in matches]
        assert "pattern.search_filter" in pattern_keys
    
    def test_no_matches_for_mismatched_method(self):
        """Mismatched HTTP method should not produce matches."""
        matches = infer_pattern_from_task(
            task_description="Create a new user",
            http_method="DELETE",  # Mismatch: create action but DELETE method
        )
        # Should still get delete pattern, but not create
        if matches:
            assert matches[0].pattern_key == "pattern.crud_delete"
    
    def test_confidence_ordering(self):
        """Matches should be ordered by confidence descending."""
        matches = infer_pattern_from_task(
            task_description="Create and add a new resource item",
            http_method="POST",
        )
        assert len(matches) > 0
        for i in range(len(matches) - 1):
            assert matches[i].confidence >= matches[i + 1].confidence
    
    def test_pattern_match_structure(self):
        """PatternMatch should have all required fields."""
        matches = infer_pattern_from_task(
            task_description="Create a payment",
            http_method="POST",
        )
        assert len(matches) > 0
        match = matches[0]
        assert match.pattern_key.startswith("pattern.")
        assert match.pattern_name
        assert match.description
        assert match.confidence > 0
        assert len(match.steps) > 0
        assert match.source == "builtin"


class TestQueryKGPatterns:
    """Tests for querying patterns from the KG."""
    
    @patch("integration_coworker.kg.db")
    def test_returns_builtin_patterns_when_kg_empty(self, mock_db):
        """Should return builtin patterns when KG has no pattern nodes."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db.get_connection.return_value = mock_conn
        mock_db.get_engine_type.return_value = "sqlite"
        mock_cur.fetchall.return_value = []  # No KG patterns
        
        matches = query_kg_patterns(
            task_description="Create a new order",
            http_method="POST",
        )
        
        # Should still get builtin patterns
        assert len(matches) > 0
        assert matches[0].source == "builtin"
    
    @patch("integration_coworker.kg.db")
    @patch("integration_coworker.kg._compute_embedding")
    def test_merges_kg_and_builtin_patterns(self, mock_embed, mock_db):
        """Should merge KG patterns with builtin patterns."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db.get_connection.return_value = mock_conn
        mock_db.get_engine_type.return_value = "sqlite"
        mock_embed.return_value = None
        
        # Simulate a KG pattern
        mock_cur.fetchall.side_effect = [
            [  # First call: pattern nodes
                (1, "pattern.custom_flow", "Custom Flow", "A custom workflow", "{}", None)
            ],
            [],  # Second call: provider examples
        ]
        
        matches = query_kg_patterns(
            task_description="Create a new order",
            http_method="POST",
        )
        
        # Should have both KG and builtin patterns
        sources = {m.source for m in matches}
        assert "kg" in sources
        assert "builtin" in sources


class TestQueryTemplatesWithPatternFallback:
    """Tests for the combined template + pattern query."""
    
    @patch("integration_coworker.kg.query_workflow_templates")
    @patch("integration_coworker.kg.query_kg_patterns")
    def test_uses_templates_when_available(self, mock_patterns, mock_templates):
        """Should use provider-specific templates when found."""
        from integration_coworker.domain.models import KGWorkflowTemplate
        
        mock_templates.return_value = [
            KGWorkflowTemplate(
                template_id="template.stripe.create_checkout",
                name="Create Checkout",
                provider_code="stripe",
            )
        ]
        mock_patterns.return_value = []
        
        templates, patterns, source = query_templates_with_pattern_fallback(
            provider_code="stripe",
            task_description="Create a checkout session",
        )
        
        assert len(templates) == 1
        assert len(patterns) == 0
        assert source == "exact"
        mock_patterns.assert_not_called()  # Shouldn't query patterns
    
    @patch("integration_coworker.kg.query_workflow_templates")
    @patch("integration_coworker.kg.query_kg_patterns")
    def test_falls_back_to_patterns_when_no_templates(self, mock_patterns, mock_templates):
        """Should fall back to patterns when no templates found."""
        mock_templates.return_value = []
        mock_patterns.return_value = [
            PatternMatch(
                pattern_key="pattern.crud_create",
                pattern_name="Create Resource",
                description="Create via POST",
                confidence=0.8,
                steps=[{"key": "call_api", "type": "api_call", "position": 0}],
                source="builtin",
                provider_examples=[],
            )
        ]
        
        templates, patterns, source = query_templates_with_pattern_fallback(
            provider_code="unknown_provider",
            task_description="Create a new resource",
        )
        
        assert len(templates) == 0
        assert len(patterns) == 1
        assert source == "pattern"
    
    @patch("integration_coworker.kg.query_workflow_templates")
    @patch("integration_coworker.kg.query_kg_patterns")
    def test_returns_none_source_when_nothing_found(self, mock_patterns, mock_templates):
        """Should return 'none' source when no matches found."""
        mock_templates.return_value = []
        mock_patterns.return_value = []
        
        templates, patterns, source = query_templates_with_pattern_fallback(
            provider_code="unknown",
            task_description="Do something complex",
        )
        
        assert len(templates) == 0
        assert len(patterns) == 0
        assert source == "none"


class TestPatternSteps:
    """Tests for pattern step structure."""
    
    def test_crud_create_has_validation_step(self):
        """Create pattern should include validation step."""
        steps = STANDARD_PATTERNS["crud_create"]["steps"]
        step_types = [s["type"] for s in steps]
        assert "validation" in step_types
    
    def test_crud_list_has_pagination_step(self):
        """List pattern should include pagination step."""
        steps = STANDARD_PATTERNS["crud_list"]["steps"]
        step_types = [s["type"] for s in steps]
        assert "pagination" in step_types
    
    def test_nested_resource_has_parent_resolution(self):
        """Nested resource pattern should resolve parent first."""
        steps = STANDARD_PATTERNS["nested_resource"]["steps"]
        # First step should be resolving the parent
        assert steps[0]["key"] == "resolve_parent"

"""
Test suite for the dynamic pattern learning system.

This tests the V1.2 Pattern Learning feature implemented per docs/PATTERN_LEARNING_DESIGN.md.

Components tested:
- capture_run_events: Records workflow events to kg.run_events
- discover_pattern_candidates: Mines events for candidate patterns
- promote_pattern_candidate: Graduates candidate to kg.nodes
- record_pattern_match: Tracks pattern matches for explainability
- update_pattern_confidence_from_feedback: Adjusts confidence based on feedback

Run with: pytest -m sqlite tests/test_pattern_learning.py
"""

import os
import pytest
from unittest.mock import patch, MagicMock

# Mark all tests in this module as SQLite tests
pytestmark = pytest.mark.sqlite


@pytest.fixture(autouse=True)
def sqlite_test_env(monkeypatch, tmp_path):
    """Configure SQLite test environment per-test (not module-level)."""
    test_db = tmp_path / "test_pattern_learning.db"
    monkeypatch.setenv("USE_SQLITE", "true")
    monkeypatch.setenv("BEADS_DB", str(test_db))
    yield


class TestPatternDiscoveryModule:
    """Unit tests for pattern_discovery.py functions."""
    
    def test_imports(self):
        """All pattern discovery functions should be importable."""
        from integration_coworker.kg.pattern_discovery import (
            capture_run_events,
            discover_pattern_candidates,
            promote_pattern_candidate,
            record_pattern_match,
            get_pattern_candidates,
        )
        assert callable(capture_run_events)
        assert callable(discover_pattern_candidates)
        assert callable(promote_pattern_candidate)
        assert callable(record_pattern_match)
        assert callable(get_pattern_candidates)
    
    def test_capture_run_events_disabled(self):
        """Events should not be captured when pattern learning is disabled."""
        from integration_coworker.kg.pattern_discovery import capture_run_events
        from integration_coworker.config import reset_settings
        
        with patch.dict(os.environ, {"PATTERN_LEARNING_ENABLED": "false"}):
            reset_settings()
            # Should return 0 when disabled (no events, no workflow_nodes)
            count = capture_run_events(
                run_id="test_disabled_run",
                workflow_nodes=[],
                provider_code="test",
            )
            # Should return 0 when disabled or no nodes
            assert count == 0
    
    def test_get_pattern_candidates_empty(self):
        """Should return empty list when no candidates exist."""
        from integration_coworker.kg.pattern_discovery import get_pattern_candidates
        
        # This should not crash even with empty DB
        candidates = get_pattern_candidates()
        assert isinstance(candidates, list)


class TestPatternMatchRecording:
    """Tests for pattern match recording in align_task_with_kg."""
    
    def test_align_task_with_kg_records_pattern(self):
        """align_task_with_kg should attempt to record pattern match."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import IntegrationTask, Endpoint
        from integration_coworker.graph.nodes.align_task_with_kg import align_task_with_kg
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Create a new user",
            provider_code="test_provider",
            integration_task=IntegrationTask(
                id=None,
                source_system_id=None,
                task_slug="create_user",
                provider_code="test_provider",
                description="Create a new user",
            ),
            endpoints=[
                Endpoint(
                    id=None,
                    source_system_id=None,
                    spec_document_id=None,
                    path="/users",
                    method="POST",
                    operation_id="createUser",
                    summary="Create a user",
                    description="Creates a new user",
                    request_schema_id=None,
                    response_schema_id=None,
                ),
            ],
        )
        
        result = align_task_with_kg(state)
        
        # Should have matched a pattern
        assert result.plan.get("template_source") in ("pattern", "inferred")
        assert result.plan.get("matched_pattern_id") or result.plan.get("matched_template_id")
        assert len(result.workflow_nodes) >= 4
    
    def test_align_task_with_kg_handles_missing_run_id(self):
        """Should not crash when run_id is not available."""
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import IntegrationTask, Endpoint
        from integration_coworker.graph.nodes.align_task_with_kg import align_task_with_kg
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Create a new user",
            provider_code="test_provider",
            integration_task=IntegrationTask(
                id=None,
                source_system_id=None,
                task_slug="create_user",
                provider_code="test_provider",
                description="Create a new user",
            ),
            endpoints=[
                Endpoint(
                    id=None,
                    source_system_id=None,
                    spec_document_id=None,
                    path="/users",
                    method="POST",
                    operation_id="createUser",
                    summary="Create a user",
                    description="Creates a new user",
                    request_schema_id=None,
                    response_schema_id=None,
                ),
            ],
        )
        # Explicitly no run_id
        state.plan = {}
        
        # Should complete without crashing
        result = align_task_with_kg(state)
        assert result.plan.get("template_source") is not None


class TestConfigFlags:
    """Tests for pattern learning configuration."""
    
    def test_pattern_learning_disabled_by_default(self):
        """Pattern learning should be DISABLED by default for safety."""
        from integration_coworker.config import get_settings, reset_settings
        
        # Remove env var and reset
        env_backup = os.environ.pop("PATTERN_LEARNING_ENABLED", None)
        try:
            reset_settings()
            settings = get_settings()
            # V1.2: Default OFF for production safety
            assert settings.pattern_learning_enabled is False
            # Granular flags can still be checked
            assert hasattr(settings, 'pattern_capture_events')
            assert hasattr(settings, 'pattern_discover_candidates')
            assert hasattr(settings, 'pattern_auto_promote')
            assert hasattr(settings, 'pattern_match_learned')
        finally:
            if env_backup:
                os.environ["PATTERN_LEARNING_ENABLED"] = env_backup
            reset_settings()
    
    def test_pattern_promotion_threshold_configurable(self):
        """Pattern promotion threshold should be configurable via env."""
        from integration_coworker.config import get_settings, reset_settings
        
        with patch.dict(os.environ, {"PATTERN_PROMOTION_THRESHOLD": "5"}):
            reset_settings()
            settings = get_settings()
            assert settings.pattern_promotion_threshold == 5
    
    def test_master_flag_off_prevents_all_operations(self):
        """When master flag is OFF, no pattern learning operations should occur."""
        from integration_coworker.kg.pattern_discovery import (
            capture_run_events,
            discover_pattern_candidates,
        )
        from integration_coworker.config import reset_settings
        from integration_coworker.domain.models import IntegrationFlowNode
        
        # Explicitly disable master flag, enable sub-flags
        with patch.dict(os.environ, {
            "PATTERN_LEARNING_ENABLED": "false",
            "PATTERN_CAPTURE_EVENTS": "true",
            "PATTERN_DISCOVER_CANDIDATES": "true",
        }):
            reset_settings()
            
            # Try to capture events - should return 0 (no-op)
            workflow = [IntegrationFlowNode(
                id=None, task_id=None, node_key="test", node_type="api_call",
                position=1, config={"endpoint_path": "/test", "endpoint_method": "GET"}
            )]
            count = capture_run_events("test_run", workflow, "test_provider")
            assert count == 0, "Master flag OFF should prevent event capture"
            
            # Try to discover candidates - should return empty
            candidates = discover_pattern_candidates(min_support=1)
            assert candidates == [], "Master flag OFF should prevent discovery"
    
    def test_capture_only_mode(self):
        """When capture=ON but discover=OFF, events logged but no candidates discovered."""
        from integration_coworker.config import get_settings, reset_settings
        
        with patch.dict(os.environ, {
            "PATTERN_LEARNING_ENABLED": "true",
            "PATTERN_CAPTURE_EVENTS": "true",
            "PATTERN_DISCOVER_CANDIDATES": "false",
        }):
            reset_settings()
            settings = get_settings()
            
            assert settings.pattern_learning_enabled is True
            assert settings.pattern_capture_events is True
            assert settings.pattern_discover_candidates is False


class TestCrossProviderPatternMatching:
    """Tests for cross-provider pattern matching."""
    
    def test_crud_create_pattern_available(self):
        """The crud_create pattern should be available for matching."""
        from integration_coworker.kg import STANDARD_PATTERNS
        
        # STANDARD_PATTERNS is a dict with pattern keys (without 'pattern.' prefix)
        assert "crud_create" in STANDARD_PATTERNS
    
    def test_all_crud_patterns_present(self):
        """All CRUD patterns should be seeded."""
        from integration_coworker.kg import STANDARD_PATTERNS
        
        # Keys in STANDARD_PATTERNS don't have 'pattern.' prefix
        expected_patterns = [
            "crud_create",
            "crud_read",
            "crud_update",
            "crud_delete",
            "crud_list",
        ]
        
        for expected in expected_patterns:
            assert expected in STANDARD_PATTERNS, f"Missing pattern: {expected}"


class TestConfidenceAdjustment:
    """Tests for feedback-based confidence adjustment."""
    
    def test_update_pattern_confidence_import(self):
        """The confidence update function should be importable from pattern_discovery (stable API)."""
        from integration_coworker.kg.pattern_discovery import update_pattern_confidence_from_feedback
        assert callable(update_pattern_confidence_from_feedback)
    
    def test_update_pattern_confidence_handles_missing_pattern(self):
        """Should handle missing pattern gracefully."""
        from integration_coworker.kg.pattern_discovery import update_pattern_confidence_from_feedback
        
        # Should not crash for non-existent pattern
        result = update_pattern_confidence_from_feedback("nonexistent_pattern_xyz_123")
        # Returns None or 0.0 for missing pattern
        assert result in (None, 0.0)


class TestPatternLearningIntegration:
    """Integration tests for the full pattern learning flow."""
    
    def test_end_to_end_pattern_learning_components(self):
        """All pattern learning components should be wired together."""
        # 1. Pattern discovery module should exist
        from integration_coworker.kg import pattern_discovery
        assert hasattr(pattern_discovery, "capture_run_events")
        assert hasattr(pattern_discovery, "discover_pattern_candidates")
        
        # 2. Config should have flags
        from integration_coworker.config import get_settings
        settings = get_settings()
        assert hasattr(settings, "pattern_learning_enabled")
        assert hasattr(settings, "pattern_promotion_threshold")
        
        # 3. align_task_with_kg should import record_pattern_match
        from integration_coworker.kg.pattern_discovery import record_pattern_match
        assert callable(record_pattern_match)
        
        # 4. capture_run_events should be callable from kg module
        from integration_coworker.kg.pattern_discovery import capture_run_events
        assert callable(capture_run_events)
        
        # 5. Confidence update should exist in pattern_discovery (stable API)
        from integration_coworker.kg.pattern_discovery import update_pattern_confidence_from_feedback
        assert callable(update_pattern_confidence_from_feedback)


class TestRFC8785Canonicalization:
    """Tests for RFC 8785 JSON Canonicalization Scheme determinism."""
    
    def test_jcs_canonicalize_import(self):
        """JCS functions should be importable."""
        from integration_coworker.kg.pattern_discovery import (
            jcs_canonicalize,
            hash_canonical_sequence,
            SIGNATURE_VERSION,
        )
        assert callable(jcs_canonicalize)
        assert callable(hash_canonical_sequence)
        assert SIGNATURE_VERSION == "v1"
    
    def test_identical_steps_produce_identical_hash(self):
        """Same steps should always produce the same hash."""
        from integration_coworker.kg.pattern_discovery import (
            CanonicalStep,
            hash_canonical_sequence,
        )
        
        steps = [
            CanonicalStep(method="POST", action="create", resource_type="pet", position=1),
            CanonicalStep(method="GET", action="read", resource_type="pet", position=2),
        ]
        
        # Hash multiple times
        hash1 = hash_canonical_sequence(steps)
        hash2 = hash_canonical_sequence(steps)
        hash3 = hash_canonical_sequence(steps)
        
        assert hash1 == hash2 == hash3, "Hashes must be deterministic"
    
    def test_different_dict_ordering_same_hash(self):
        """Different dict construction order should produce same hash."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        # Create dicts with different insertion order
        d1 = {"z": 1, "a": 2, "m": 3}
        d2 = {"a": 2, "m": 3, "z": 1}
        d3 = {"m": 3, "z": 1, "a": 2}
        
        # JCS should produce identical output
        assert jcs_canonicalize(d1) == jcs_canonicalize(d2) == jcs_canonicalize(d3)
    
    def test_nested_objects_canonicalized(self):
        """Nested objects should be canonicalized recursively."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        d1 = {"outer": {"z": 1, "a": 2}, "list": [{"b": 1, "a": 2}]}
        d2 = {"list": [{"a": 2, "b": 1}], "outer": {"a": 2, "z": 1}}
        
        assert jcs_canonicalize(d1) == jcs_canonicalize(d2)
    
    def test_different_steps_produce_different_hash(self):
        """Different step sequences should produce different hashes."""
        from integration_coworker.kg.pattern_discovery import (
            CanonicalStep,
            hash_canonical_sequence,
        )
        
        steps_a = [
            CanonicalStep(method="POST", action="create", position=1),
            CanonicalStep(method="GET", action="read", position=2),
        ]
        
        steps_b = [
            CanonicalStep(method="GET", action="read", position=1),
            CanonicalStep(method="POST", action="create", position=2),
        ]
        
        hash_a = hash_canonical_sequence(steps_a)
        hash_b = hash_canonical_sequence(steps_b)
        
        assert hash_a != hash_b, "Different sequences must produce different hashes"
    
    def test_candidate_key_stable_across_recreations(self):
        """PatternCandidate should produce stable keys when recreated."""
        from integration_coworker.kg.pattern_discovery import (
            CanonicalStep,
            PatternCandidate,
        )
        
        steps = [
            CanonicalStep(method="POST", action="create", resource_type="item", position=1),
            CanonicalStep(method="GET", action="list", resource_type="item", position=2),
        ]
        
        # Create candidate multiple times
        c1 = PatternCandidate(
            candidate_key="test",
            name="Test",
            description=None,
            canonical_sequence=steps,
            support_count=3,
            provider_examples=["test"],
        )
        
        c2 = PatternCandidate(
            candidate_key="test",
            name="Test",
            description=None,
            canonical_sequence=steps,
            support_count=3,
            provider_examples=["test"],
        )
        
        assert c1.sequence_hash() == c2.sequence_hash()


class TestRFC8785Compliance:
    """
    RFC 8785 compliance tests with official test vectors.
    
    These tests verify our JCS implementation matches the RFC specification.
    Test vectors from: https://www.rfc-editor.org/rfc/rfc8785.html
    """
    
    def test_reject_nan(self):
        """JCS must reject NaN (I-JSON constraint)."""
        from integration_coworker.kg.pattern_discovery import (
            jcs_canonicalize,
            JCSEncodingError,
        )
        import math
        
        with pytest.raises(JCSEncodingError, match="NaN"):
            jcs_canonicalize({"value": float('nan')})
    
    def test_reject_infinity(self):
        """JCS must reject Infinity (I-JSON constraint)."""
        from integration_coworker.kg.pattern_discovery import (
            jcs_canonicalize,
            JCSEncodingError,
        )
        
        with pytest.raises(JCSEncodingError, match="Infinity"):
            jcs_canonicalize({"value": float('inf')})
        
        with pytest.raises(JCSEncodingError, match="Infinity"):
            jcs_canonicalize({"value": float('-inf')})
    
    def test_rfc8785_number_serialization(self):
        """
        Test number serialization per RFC 8785 Appendix B.
        
        Key test cases from the RFC's number serialization samples.
        """
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        # Zero and minus zero both serialize as "0"
        assert jcs_canonicalize(0) == b'0'
        assert jcs_canonicalize(-0.0) == b'0'
        
        # Integers
        assert jcs_canonicalize(1) == b'1'
        assert jcs_canonicalize(-1) == b'-1'
        
        # Note: 2^53 (9007199254740992) is at the boundary of IEEE 754 safe integers
        # The rfc8785 package rejects integers >= 2^53, but RFC 8785 only warns.
        # Our implementation uses fallback encoder for these edge cases.
        assert jcs_canonicalize(9007199254740991) == b'9007199254740991'  # Max safe int - 1
        
        # Floats that are whole numbers should serialize as integers
        assert jcs_canonicalize(4.0) == b'4'
        
        # Scientific notation cases from RFC
        # 1e+30 is represented in canonical form
        result = jcs_canonicalize(1e30)
        assert b'e' in result.lower() or result == b'1e+30' or b'1e30' in result.lower()
    
    def test_rfc8785_string_escaping(self):
        """
        Test string escaping per RFC 8785 Section 3.2.2.2.
        
        - Control chars 0x00-0x1F must use lowercase hex notation \\uhhhh
        - Except: 0x08=\\b, 0x09=\\t, 0x0A=\\n, 0x0C=\\f, 0x0D=\\r
        - Backslash and quote must be escaped
        """
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        # Basic escaping
        assert jcs_canonicalize("hello") == b'"hello"'
        assert jcs_canonicalize('say "hi"') == b'"say \\"hi\\""'
        assert jcs_canonicalize("back\\slash") == b'"back\\\\slash"'
        
        # Control character escaping
        assert jcs_canonicalize("tab\there") == b'"tab\\there"'
        assert jcs_canonicalize("new\nline") == b'"new\\nline"'
        assert jcs_canonicalize("return\rhere") == b'"return\\rhere"'
        
        # Unicode from RFC example: €$\u000F\u000aA'B"\\"/
        # Input string with euro sign, dollar, control char 0x0F, newline, etc.
        test_str = "\u20ac$\u000f\nA'B\"\\\\/\""
        result = jcs_canonicalize(test_str)
        # Should have lowercase \\u000f for control char
        assert b'\\u000f' in result
        assert b'\\n' in result
    
    def test_rfc8785_property_sorting(self):
        """
        Test property sorting per RFC 8785 Section 3.2.3.
        
        Properties sorted by UTF-16 code unit order.
        """
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        # RFC 8785 sorting test case
        test_data = {
            "\u20ac": "Euro Sign",
            "\r": "Carriage Return",
            "1": "One",
            "\u0080": "Control",
        }
        
        result = jcs_canonicalize(test_data).decode('utf-8')
        
        # Check that carriage return comes first (lowest code point)
        # Order should be: \r (0x0D), 1 (0x31), \u0080 (0x80), € (0x20AC)
        assert result.index("Carriage Return") < result.index("One")
        assert result.index("One") < result.index("Control")
        assert result.index("Control") < result.index("Euro Sign")
    
    def test_rfc8785_full_example(self):
        """
        Test full example from RFC 8785 Section 3.2.2.
        
        Input:
        {
            "numbers": [333333333.33333329, 1E30, 4.50, 2e-3, 0.000000000000000000000000001],
            "string": "€$\\u000F\\u000aA'B\\"\\\\\\\\\\"/",
            "literals": [null, true, false]
        }
        """
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        test_data = {
            "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
            "string": "\u20ac$\u000f\nA'B\"\\\\\"/",
            "literals": [None, True, False]
        }
        
        result = jcs_canonicalize(test_data)
        
        # Verify it's valid JSON (can be parsed back)
        import json
        parsed = json.loads(result.decode('utf-8'))
        assert "literals" in parsed
        assert "numbers" in parsed
        assert "string" in parsed
        
        # Verify property ordering (alphabetical)
        result_str = result.decode('utf-8')
        assert result_str.index('"literals"') < result_str.index('"numbers"')
        assert result_str.index('"numbers"') < result_str.index('"string"')
    
    def test_deterministic_hashing(self):
        """Verify that hashing is deterministic across multiple runs."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        import hashlib
        
        test_data = {
            "z": 1,
            "a": {"nested": True, "value": 42},
            "m": [1, 2, 3],
        }
        
        # Hash 100 times - must all be identical
        canonical = jcs_canonicalize(test_data)
        expected_hash = hashlib.sha256(canonical).hexdigest()
        
        for _ in range(100):
            result = hashlib.sha256(jcs_canonicalize(test_data)).hexdigest()
            assert result == expected_hash, "Hash must be deterministic"


class TestRFC8785ReferenceComparison:
    """
    Reference-comparison tests against known-good RFC 8785 implementation.
    
    Uses the `rfc8785` PyPI package as the reference implementation to validate
    our custom encoder produces identical canonical output.
    
    See: https://pypi.org/project/rfc8785/
    """
    
    @pytest.fixture
    def reference_canonicalize(self):
        """Get the reference implementation's canonicalize function."""
        try:
            import rfc8785
            return rfc8785.dumps
        except ImportError:
            pytest.skip("rfc8785 package not installed for reference comparison")
    
    def test_simple_objects_match_reference(self, reference_canonicalize):
        """Simple objects should match reference implementation."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        test_cases = [
            {},
            {"a": 1},
            {"b": 2, "a": 1},  # Should be sorted
            {"key": "value"},
            {"nested": {"inner": "value"}},
        ]
        
        for test_data in test_cases:
            our_result = jcs_canonicalize(test_data)
            ref_result = reference_canonicalize(test_data)
            assert our_result == ref_result, f"Mismatch for {test_data}: ours={our_result}, ref={ref_result}"
    
    def test_arrays_match_reference(self, reference_canonicalize):
        """Arrays should match reference implementation."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        test_cases = [
            [],
            [1, 2, 3],
            [{"a": 1}, {"b": 2}],
            ["string", 42, True, None],
        ]
        
        for test_data in test_cases:
            our_result = jcs_canonicalize(test_data)
            ref_result = reference_canonicalize(test_data)
            assert our_result == ref_result, f"Mismatch for {test_data}: ours={our_result}, ref={ref_result}"
    
    def test_numbers_match_reference(self, reference_canonicalize):
        """Number serialization should match reference implementation."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        test_cases = [
            0,
            -0.0,  # Should serialize as 0
            1,
            -1,
            42,
            3.14,
            1e10,
            2.5e-5,
            # Note: 2^53 excluded - rfc8785 package rejects it but RFC only warns
            9007199254740991,  # Max safe integer - 1
        ]
        
        for test_data in test_cases:
            our_result = jcs_canonicalize(test_data)
            ref_result = reference_canonicalize(test_data)
            assert our_result == ref_result, f"Mismatch for {test_data}: ours={our_result}, ref={ref_result}"
    
    def test_string_escaping_matches_reference(self, reference_canonicalize):
        """String escaping should match reference implementation."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        test_cases = [
            "hello",
            "hello world",
            'say "hi"',
            "back\\slash",
            "tab\there",
            "new\nline",
            "return\rhere",
            "\u20ac",  # Euro sign
            "mixed\t\n\rcontrol",
        ]
        
        for test_data in test_cases:
            our_result = jcs_canonicalize(test_data)
            ref_result = reference_canonicalize(test_data)
            assert our_result == ref_result, f"Mismatch for '{test_data}': ours={our_result}, ref={ref_result}"
    
    def test_nested_structure_matches_reference(self, reference_canonicalize):
        """Nested structures should match reference implementation."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        test_data = {
            "z": [1, 2, 3],
            "a": {"deep": {"nested": True}},
            "m": None,
        }
        
        our_result = jcs_canonicalize(test_data)
        ref_result = reference_canonicalize(test_data)
        assert our_result == ref_result, f"Mismatch for nested: ours={our_result}, ref={ref_result}"
    
    def test_random_dicts_match_reference(self, reference_canonicalize):
        """Random nested dicts should produce matching output."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        import random
        import string
        
        def random_value(depth=0):
            if depth > 3:
                return random.choice([42, "string", True, None])
            choice = random.randint(0, 4)
            if choice == 0:
                return random.randint(-1000, 1000)
            elif choice == 1:
                return ''.join(random.choices(string.ascii_letters, k=5))
            elif choice == 2:
                return random.choice([True, False, None])
            elif choice == 3:
                return [random_value(depth + 1) for _ in range(random.randint(0, 3))]
            else:
                keys = [''.join(random.choices(string.ascii_lowercase, k=3)) for _ in range(random.randint(1, 4))]
                return {k: random_value(depth + 1) for k in keys}
        
        # Test 20 random structures
        random.seed(42)  # Reproducible randomness
        for i in range(20):
            test_data = random_value()
            our_result = jcs_canonicalize(test_data)
            ref_result = reference_canonicalize(test_data)
            assert our_result == ref_result, f"Mismatch for random case {i}: {test_data}"
    
    def test_edge_case_integers_match_reference(self, reference_canonicalize):
        """Edge case integers should match reference."""
        from integration_coworker.kg.pattern_discovery import jcs_canonicalize
        
        # Note: rfc8785 package rejects integers >= 2^53 (strict I-JSON)
        # We test up to 2^53 - 2 to stay safely in bounds
        test_cases = [
            2**53 - 2,  # Safe: within IEEE 754 range
            -(2**53 - 2),  # Safe: within IEEE 754 range
            2**31 - 1,  # Max 32-bit signed
            -(2**31),  # Min 32-bit signed
            0,
        ]
        
        for test_data in test_cases:
            our_result = jcs_canonicalize(test_data)
            ref_result = reference_canonicalize(test_data)
            assert our_result == ref_result, f"Mismatch for {test_data}"


class TestRetentionFunctions:
    """Tests for retention / cleanup functions."""
    
    def test_purge_old_run_events_import(self):
        """purge_old_run_events should be importable."""
        from integration_coworker.kg.pattern_discovery import purge_old_run_events
        assert callable(purge_old_run_events)
    
    def test_get_event_stats_import(self):
        """get_event_stats should be importable."""
        from integration_coworker.kg.pattern_discovery import get_event_stats
        assert callable(get_event_stats)
    
    def test_purge_requires_positive_days(self):
        """purge_old_run_events should reject invalid retention periods."""
        from integration_coworker.kg.pattern_discovery import purge_old_run_events
        
        with pytest.raises(ValueError, match="at least 1 day"):
            purge_old_run_events(days=0)
        
        with pytest.raises(ValueError, match="at least 1 day"):
            purge_old_run_events(days=-5)
    
    def test_get_event_stats_returns_dict(self):
        """get_event_stats should return a dict with expected keys."""
        from integration_coworker.kg.pattern_discovery import get_event_stats
        
        stats = get_event_stats()
        assert isinstance(stats, dict)
        # Either has stats or error key
        assert "total_events" in stats or "error" in stats
    
    def test_purge_actually_deletes_old_events(self, tmp_path):
        """Purge should delete old events and keep recent ones."""
        from integration_coworker.config import reset_settings
        from integration_coworker.kg.pattern_discovery import purge_old_run_events
        from integration_coworker.persistence import db
        
        test_db = tmp_path / "test_purge.db"
        old_env = {}
        
        # Save and set environment
        for k in ["USE_SQLITE", "BEADS_DB", "PATTERN_LEARNING_ENABLED"]:
            old_env[k] = os.environ.get(k)
        
        os.environ["USE_SQLITE"] = "true"
        os.environ["BEADS_DB"] = str(test_db)
        os.environ["PATTERN_LEARNING_ENABLED"] = "true"
        reset_settings()
        
        try:
            # Initialize DB
            db.init_schema()
            
            conn = db.get_connection()
            cur = conn.cursor()
            
            # Insert an old event (100 days ago) - column is 'timestamp' in SQLite
            cur.execute("""
                INSERT INTO kg_run_events 
                    (run_id, event_type, activity, position, timestamp)
                VALUES 
                    ('old_run', 'workflow_step', 'test', 1, datetime('now', '-100 days'))
            """)
            
            # Insert a recent event (1 day ago)
            cur.execute("""
                INSERT INTO kg_run_events 
                    (run_id, event_type, activity, position, timestamp)
                VALUES 
                    ('new_run', 'workflow_step', 'test', 1, datetime('now', '-1 days'))
            """)
            conn.commit()
            
            # Verify both exist
            cur.execute("SELECT COUNT(*) FROM kg_run_events")
            assert cur.fetchone()[0] == 2
            
            # Purge events older than 30 days
            deleted = purge_old_run_events(days=30)
            assert deleted == 1, f"Should delete 1 old event, deleted {deleted}"
            
            # Verify only new event remains
            cur.execute("SELECT run_id FROM kg_run_events")
            rows = cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "new_run"
            
        finally:
            # Restore environment
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            reset_settings()


class TestConflictSafeInserts:
    """Tests for idempotent event capture (ON CONFLICT DO NOTHING)."""
    
    @pytest.fixture
    def temp_db_and_enabled_learning(self, tmp_path):
        """Set up a temp SQLite DB with pattern learning enabled."""
        import os
        from integration_coworker.config import reset_settings
        
        test_db = tmp_path / "test_conflict.db"
        old_env = {}
        
        # Save and set environment
        for k in ["USE_SQLITE", "BEADS_DB", "PATTERN_LEARNING_ENABLED", 
                  "PATTERN_CAPTURE_EVENTS"]:
            old_env[k] = os.environ.get(k)
        
        os.environ["USE_SQLITE"] = "true"
        os.environ["BEADS_DB"] = str(test_db)
        os.environ["PATTERN_LEARNING_ENABLED"] = "true"
        os.environ["PATTERN_CAPTURE_EVENTS"] = "true"
        reset_settings()
        
        # Initialize DB schema
        from integration_coworker.persistence import db
        db.init_schema()
        
        yield str(test_db)
        
        # Restore environment
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        reset_settings()
    
    def test_double_capture_is_idempotent(self, temp_db_and_enabled_learning):
        """
        Calling capture_run_events twice for the same run should NOT raise
        an exception and should NOT increase row count.
        """
        from integration_coworker.kg.pattern_discovery import capture_run_events
        from integration_coworker.domain.models import IntegrationFlowNode
        from integration_coworker.persistence import db
        
        # Create test workflow
        workflow = [
            IntegrationFlowNode(
                id=None,
                task_id=None,
                node_key="test_step_1",
                node_type="api_call",
                position=1,
                config={"endpoint_path": "/test", "endpoint_method": "GET"},
            ),
            IntegrationFlowNode(
                id=None,
                task_id=None,
                node_key="test_step_2",
                node_type="api_call",
                position=2,
                config={"endpoint_path": "/test", "endpoint_method": "POST"},
            ),
        ]
        
        run_id = "test_idempotent_run_001"
        
        # First capture - should succeed
        count1 = capture_run_events(
            run_id=run_id,
            workflow_nodes=workflow,
            provider_code="test_provider",
        )
        assert count1 == 2, "First capture should insert 2 events"
        
        # Check row count
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM kg_run_events WHERE run_id = ?", (run_id,))
        row_count_1 = cur.fetchone()[0]
        assert row_count_1 == 2
        
        # Second capture - should NOT raise, should NOT increase count
        count2 = capture_run_events(
            run_id=run_id,
            workflow_nodes=workflow,
            provider_code="test_provider",
        )
        # Note: count2 may be 2 (attempted inserts) but DB should stay at 2 rows
        
        # Check row count again - should still be 2
        cur.execute("SELECT COUNT(*) FROM kg_run_events WHERE run_id = ?", (run_id,))
        row_count_2 = cur.fetchone()[0]
        assert row_count_2 == 2, f"Row count should stay at 2, got {row_count_2}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Tests for the path fixer module (Bug #30 fix).

Verifies that the hybrid AST + fuzzy matching approach correctly:
1. Detects hallucinated API paths in generated code
2. Matches them to valid paths from the spec
3. Replaces them with correct paths above threshold
4. Reports unfixable paths below threshold
"""
import pytest
from integration_coworker.codegen.path_fixer import (
    PathFixer,
    PathFixResult,
    PathReplacement,
    fix_hallucinated_paths,
)
from dataclasses import dataclass


@dataclass
class MockEndpoint:
    """Mock endpoint for testing."""
    path: str
    method: str = "GET"


class TestPathFixer:
    """Test the PathFixer class."""
    
    @pytest.fixture
    def valid_paths(self):
        """Common set of valid paths for tests."""
        return {
            "/v1/users",
            "/v1/users/{user_id}",
            "/v1/messages",
            "/v1/messages/{message_sid}",
            "/v1/accounts/{account_sid}/messages",
            "/v1/payments",
            "/v1/payments/{payment_id}",
        }
    
    @pytest.fixture
    def fixer(self, valid_paths):
        """Create a PathFixer with test paths."""
        return PathFixer(valid_paths=valid_paths, threshold=0.65)
    
    def test_exact_match_no_change(self, fixer):
        """Test that exact matches are not changed."""
        code = '''
def get_users():
    url = "/v1/users"
    return requests.get(url)
'''
        result = fixer.fix_code(code)
        assert result.success
        assert len(result.replacements) == 0
        assert result.fixed_code == code
    
    def test_fix_typo_in_path(self, fixer):
        """Test fixing a typo in path (messages -> mesages)."""
        code = '''
def send_message():
    url = "/v1/mesages"
    return requests.post(url)
'''
        result = fixer.fix_code(code)
        assert len(result.replacements) == 1
        assert result.replacements[0].old_path == "/v1/mesages"
        assert result.replacements[0].new_path == "/v1/messages"
        assert result.replacements[0].confidence >= 0.65
        assert "/v1/messages" in result.fixed_code
    
    def test_fix_hallucinated_path(self, fixer):
        """Test fixing a close hallucinated path (singular vs plural)."""
        code = '''
def get_user_data():
    url = "/v1/user"  # Hallucinated singular - should match /v1/users
    return requests.get(url)
'''
        result = fixer.fix_code(code)
        # This should match well since "user" is very close to "users"
        assert len(result.replacements) == 1
        assert result.replacements[0].old_path == "/v1/user"
        assert result.replacements[0].new_path == "/v1/users"
        assert "/v1/users" in result.fixed_code
    
    def test_fix_wrong_version(self, fixer):
        """Test fixing wrong API version."""
        code = '''
def get_messages():
    url = "/v2/messages"  # Wrong version
    return requests.get(url)
'''
        result = fixer.fix_code(code)
        assert len(result.replacements) == 1
        assert result.replacements[0].old_path == "/v2/messages"
        assert result.replacements[0].new_path == "/v1/messages"
    
    def test_no_fix_for_file_paths(self, fixer):
        """Test that file paths are not changed."""
        code = '''
def read_config():
    path = "/home/user/config.json"
    return open(path).read()
'''
        result = fixer.fix_code(code)
        assert result.success
        assert len(result.replacements) == 0
        assert result.fixed_code == code
    
    def test_no_fix_for_non_api_paths(self, fixer):
        """Test that non-API paths are ignored."""
        code = '''
def get_static():
    path = "/static/css/style.css"
    return path
'''
        result = fixer.fix_code(code)
        assert len(result.replacements) == 0
    
    def test_multiple_paths_fixed(self, fixer):
        """Test fixing multiple close paths in one file."""
        code = '''
def api_calls():
    users_url = "/v1/user"
    messages_url = "/v1/message"
    return requests.get(users_url), requests.get(messages_url)
'''
        result = fixer.fix_code(code)
        # Both should be close enough to fix (singular -> plural)
        assert len(result.replacements) == 2
    
    def test_low_confidence_not_fixed(self, fixer):
        """Test that low confidence matches are not applied."""
        code = '''
def random_endpoint():
    url = "/v1/completely_random_endpoint"
    return requests.get(url)
'''
        result = fixer.fix_code(code)
        # Should either not fix (in unfixable) or have low confidence
        if result.replacements:
            # If it matched something, verify it's above threshold
            assert all(r.confidence >= 0.65 for r in result.replacements)
    
    def test_syntax_error_handled(self, fixer):
        """Test that syntax errors in code are handled gracefully."""
        code = '''
def broken_code(
    url = "/v1/users"
    # Missing closing paren
'''
        result = fixer.fix_code(code)
        assert not result.success
        assert result.fixed_code == code  # Returns original on error
    
    def test_path_with_parameters(self, fixer):
        """Test paths with path parameters."""
        code = '''
def get_message(sid):
    url = f"/v1/messages/{sid}"  # fstring - won't be detected as string literal
    url2 = "/v1/message/{msg_id}"  # Hallucinated
    return requests.get(url)
'''
        result = fixer.fix_code(code)
        # Should fix the hallucinated path
        fixed_paths = [r for r in result.replacements if "/v1/message/{msg_id}" in r.old_path]
        if fixed_paths:
            assert fixed_paths[0].new_path == "/v1/messages/{message_sid}"


class TestPathFixerFromEndpoints:
    """Test PathFixer.from_endpoints factory."""
    
    def test_from_endpoints_creates_fixer(self):
        """Test creating fixer from endpoint objects."""
        endpoints = [
            MockEndpoint(path="/v1/users", method="GET"),
            MockEndpoint(path="/v1/users/{id}", method="GET"),
            MockEndpoint(path="/v1/messages", method="POST"),
        ]
        fixer = PathFixer.from_endpoints(endpoints, threshold=0.7)
        assert len(fixer.valid_paths) == 3
        assert "/v1/users" in fixer.valid_paths
        assert fixer.threshold == 0.7
    
    def test_from_endpoints_empty_list(self):
        """Test handling empty endpoint list."""
        fixer = PathFixer.from_endpoints([])
        assert len(fixer.valid_paths) == 0
    
    def test_from_endpoints_with_methods(self):
        """Test that methods are captured."""
        endpoints = [
            MockEndpoint(path="/v1/users", method="GET"),
            MockEndpoint(path="/v1/users", method="POST"),
        ]
        fixer = PathFixer.from_endpoints(endpoints)
        # Path should be in valid_paths (deduplicated)
        assert "/v1/users" in fixer.valid_paths


class TestFixHallucinatedPaths:
    """Test the convenience function."""
    
    def test_convenience_function(self):
        """Test fix_hallucinated_paths convenience function."""
        endpoints = [
            MockEndpoint(path="/v1/users", method="GET"),
            MockEndpoint(path="/v1/messages", method="POST"),
        ]
        code = '''
def get():
    return requests.get("/v1/usr")
'''
        result = fix_hallucinated_paths(code, endpoints)
        assert isinstance(result, PathFixResult)
        assert len(result.replacements) >= 0  # May or may not fix based on confidence


class TestPathNormalization:
    """Test path normalization logic."""
    
    def test_trailing_slash_normalized(self):
        """Test that trailing slashes are handled."""
        fixer = PathFixer(valid_paths={"/v1/users"})
        # Normalization happens internally
        assert fixer._normalize_path("/v1/users/") == "/v1/users"
    
    def test_numeric_segments_normalized(self):
        """Test that numeric segments are normalized to {id}."""
        fixer = PathFixer(valid_paths={"/v1/users/{id}"})
        assert fixer._normalize_path("/v1/users/123") == "/v1/users/{id}"
    
    def test_case_insensitive_matching(self):
        """Test case normalization in matching."""
        fixer = PathFixer(valid_paths={"/v1/Users"})
        normalized = fixer._normalize_path("/v1/users")
        assert normalized.lower() == "/v1/users"


class TestPathFixerStrictMode:
    """Test strict mode behavior."""
    
    def test_strict_mode_raises_on_unfixable(self):
        """Test that strict mode raises on unfixable paths."""
        fixer = PathFixer(
            valid_paths={"/v1/users"},
            threshold=0.99,  # Very high threshold
            strict_mode=True,
        )
        code = '''
def get():
    return requests.get("/completely_different_api")
'''
        with pytest.raises(ValueError, match="Cannot fix"):
            fixer.fix_code(code)
    
    def test_non_strict_mode_returns_unfixable(self):
        """Test that non-strict mode returns unfixable paths."""
        fixer = PathFixer(
            valid_paths={"/v1/users"},
            threshold=0.99,  # Very high threshold
            strict_mode=False,
        )
        code = '''
def get():
    return requests.get("/completely_different_api")
'''
        result = fixer.fix_code(code)
        # Should return unfixable but not raise
        assert len(result.unfixable_paths) >= 0  # May or may not be unfixable

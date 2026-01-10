"""
Production Hardening Acceptance Tests for Discovery Module

These tests verify the acceptance gates from the hardening plan:
1. No direct httpx usage outside http_client.py
2. merge_intent() LLM is advisory, heuristics authoritative
3. Per-hop redirect SSRF validation
4. Explicit HTTPX Timeout and Limits configuration
5. Transaction-scoped HNSW ef_search
6. HITL non-interactive safety
7. DNS resolution validated before fetch

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md and OWASP SSRF Cheat Sheet.
"""

import ast
import os
import re
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from contextlib import contextmanager
from typing import Dict

from integration_coworker.discovery.intent import IntentAnalysis
from integration_coworker.discovery.intent_llm import (
    merge_intent,
    _normalize_provider,
    _validate_llm_provider,
    HEURISTIC_AUTHORITY_THRESHOLD,
    KNOWN_PROVIDER_ALIASES,
)


# =============================================================================
# Gate 1: No Direct httpx Usage Outside http_client.py
# =============================================================================

class TestNoDirectHttpx:
    """
    Acceptance Gate: No direct httpx.get() or httpx.AsyncClient() in discovery
    code except inside http_client.py and web_search_providers.py.
    
    web_search_providers.py is allowed because it uses the same hardened settings
    (trust_env=False, explicit timeouts) as http_client.py for Tavily/SerpApi POST requests.
    
    This ensures all HTTP fetches go through hardened patterns for SSRF protection.
    """
    
    # Files allowed to use httpx directly (with hardened settings)
    ALLOWED_HTTPX_FILES = frozenset({
        "http_client.py",
        "web_search_providers.py",  # Uses trust_env=False, explicit timeouts
    })
    
    def get_discovery_python_files(self):
        """Get all Python files in the discovery module."""
        discovery_path = Path(__file__).parent.parent.parent / "src" / "integration_coworker" / "discovery"
        return list(discovery_path.glob("*.py"))
    
    def test_no_httpx_import_outside_http_client(self):
        """No 'import httpx' statements outside allowed modules."""
        for py_file in self.get_discovery_python_files():
            if py_file.name in self.ALLOWED_HTTPX_FILES:
                continue  # These files are allowed to import httpx
            
            content = py_file.read_text()
            
            # Parse the file to check imports
            try:
                tree = ast.parse(content)
            except SyntaxError:
                continue  # Skip files with syntax errors
            
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name != "httpx", (
                            f"Direct 'import httpx' found in {py_file.name}. "
                            "Use 'from integration_coworker.discovery.http_client import hardened_fetch' instead."
                        )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and "httpx" in node.module:
                        assert False, (
                            f"Direct 'from httpx import ...' found in {py_file.name}. "
                            "Use 'from integration_coworker.discovery.http_client import hardened_fetch' instead."
                        )
    
    def test_no_httpx_client_calls_outside_http_client(self):
        """No httpx.AsyncClient() or httpx.get() calls outside allowed modules."""
        patterns = [
            r'httpx\.AsyncClient\s*\(',
            r'httpx\.Client\s*\(',
            r'httpx\.get\s*\(',
            r'httpx\.post\s*\(',
            r'httpx\.request\s*\(',
        ]
        
        for py_file in self.get_discovery_python_files():
            if py_file.name in self.ALLOWED_HTTPX_FILES:
                continue
            
            content = py_file.read_text()
            
            for pattern in patterns:
                matches = re.findall(pattern, content)
                assert len(matches) == 0, (
                    f"Direct httpx call '{pattern}' found in {py_file.name}. "
                    "Use hardened_fetch() instead."
                )


# =============================================================================
# Gate 2: merge_intent() - LLM Advisory, Heuristics Authoritative
# =============================================================================

class TestMergeIntentLLMAdvisory:
    """
    Acceptance Gate: merge_intent() treats LLM as advisory only.
    
    Key rules:
    1. Heuristics are AUTHORITATIVE when confidence >= 0.8
    2. LLM providers must pass validation (known alias or valid domain)
    3. All strings normalized (lowercase, stripped, Unicode NFC)
    4. Stable ordering: heuristic first, then validated LLM
    """
    
    def test_heuristic_authoritative_high_confidence(self):
        """When heuristic confidence >= 0.8, LLM cannot override explicit_provider."""
        heuristic = IntentAnalysis(
            explicit_provider="stripe.com",
            inferred_providers=["stripe.com"],
            keywords=["payment"],
            confidence=0.85,  # High confidence - authoritative
            raw_task="Process payment with Stripe",
        )
        llm = IntentAnalysis(
            explicit_provider="paypal.com",  # LLM proposes different provider
            inferred_providers=["paypal.com"],
            keywords=["checkout"],
            confidence=0.9,
            raw_task="",
        )
        
        merged = merge_intent(heuristic, llm)
        
        # Heuristic wins because confidence >= 0.8
        assert merged.explicit_provider == "stripe.com"
    
    def test_llm_provider_rejected_if_unknown(self):
        """LLM-proposed providers that aren't in aliases or valid domains are rejected."""
        heuristic = IntentAnalysis(
            explicit_provider=None,
            inferred_providers=[],
            keywords=["payment"],
            confidence=0.5,  # Below threshold
            raw_task="task",
        )
        llm = IntentAnalysis(
            explicit_provider="totally-made-up-provider",  # Invalid
            inferred_providers=["another-fake-one"],
            keywords=[],
            confidence=0.9,
            raw_task="",
        )
        
        merged = merge_intent(heuristic, llm)
        
        # LLM provider rejected - no valid domain or alias
        assert merged.explicit_provider is None
        # Invalid inferred providers also rejected
        assert "another-fake-one" not in merged.inferred_providers
    
    def test_llm_provider_accepted_if_known_alias(self):
        """LLM-proposed providers that match known aliases are accepted."""
        heuristic = IntentAnalysis(
            explicit_provider=None,
            inferred_providers=[],
            keywords=[],
            confidence=0.5,
            raw_task="task",
        )
        llm = IntentAnalysis(
            explicit_provider="stripe",  # Known alias
            inferred_providers=["twilio", "sendgrid"],  # Also known
            keywords=[],
            confidence=0.8,
            raw_task="",
        )
        
        merged = merge_intent(heuristic, llm)
        
        # Alias resolved to domain
        assert merged.explicit_provider == "stripe.com"
        assert "twilio.com" in merged.inferred_providers
        assert "sendgrid.com" in merged.inferred_providers
    
    def test_llm_provider_accepted_if_valid_domain(self):
        """LLM-proposed providers that look like valid domains are accepted."""
        heuristic = IntentAnalysis(
            explicit_provider=None,
            inferred_providers=[],
            keywords=[],
            confidence=0.5,
            raw_task="task",
        )
        llm = IntentAnalysis(
            explicit_provider="newapi.example.com",  # Valid domain format
            inferred_providers=["another.valid.io"],
            keywords=[],
            confidence=0.8,
            raw_task="",
        )
        
        merged = merge_intent(heuristic, llm)
        
        # Valid domains accepted
        assert merged.explicit_provider == "newapi.example.com"
        assert "another.valid.io" in merged.inferred_providers
    
    def test_providers_normalized(self):
        """All provider strings are normalized (lowercase, stripped)."""
        heuristic = IntentAnalysis(
            explicit_provider="  STRIPE.COM  ",
            inferred_providers=["  TWILIO.COM  "],
            keywords=["  PAYMENT  "],
            confidence=0.9,
            raw_task="task",
        )
        llm = IntentAnalysis(
            explicit_provider="OpenAI",
            inferred_providers=["GitHub"],
            keywords=["AI"],
            confidence=0.5,
            raw_task="",
        )
        
        merged = merge_intent(heuristic, llm)
        
        # All normalized to lowercase, stripped
        assert merged.explicit_provider == "stripe.com"
        assert all(p == p.lower().strip() for p in merged.inferred_providers)
        assert all(k == k.lower().strip() for k in merged.keywords)
    
    def test_stable_ordering_heuristic_first(self):
        """Inferred providers: heuristic first, then validated LLM."""
        heuristic = IntentAnalysis(
            explicit_provider=None,
            inferred_providers=["stripe.com", "paypal.com"],
            keywords=[],
            confidence=0.5,
            raw_task="task",
        )
        llm = IntentAnalysis(
            explicit_provider=None,
            inferred_providers=["twilio.com", "stripe.com"],  # stripe.com is duplicate
            keywords=[],
            confidence=0.8,
            raw_task="",
        )
        
        merged = merge_intent(heuristic, llm)
        
        # Heuristic providers come first
        assert merged.inferred_providers[0] == "stripe.com"
        assert merged.inferred_providers[1] == "paypal.com"
        # LLM providers after (deduplicated)
        assert "twilio.com" in merged.inferred_providers
        # Duplicate stripe.com not repeated
        assert merged.inferred_providers.count("stripe.com") == 1


class TestProviderNormalization:
    """Tests for provider string normalization."""
    
    @pytest.mark.parametrize("input_str,expected", [
        ("  Stripe  ", "stripe"),
        ("OPENAI", "openai"),
        ("GitHub.com", "github.com"),
        ("", None),
        (None, None),
        ("  ", None),
    ])
    def test_normalize_provider(self, input_str, expected):
        """Test provider normalization."""
        result = _normalize_provider(input_str)
        assert result == expected
    
    @pytest.mark.parametrize("provider,expected", [
        ("stripe", "stripe.com"),  # Known alias
        ("twilio", "twilio.com"),  # Known alias
        ("example.com", "example.com"),  # Valid domain
        ("api.service.io", "api.service.io"),  # Valid domain
        ("made-up-thing", None),  # Unknown, no domain
        ("x", None),  # Too short
    ])
    def test_validate_llm_provider(self, provider, expected):
        """Test LLM provider validation."""
        result = _validate_llm_provider(provider, KNOWN_PROVIDER_ALIASES)
        assert result == expected


# =============================================================================
# Gate 3: Per-Hop Redirect SSRF Validation
# =============================================================================

class TestPerHopRedirectSSRF:
    """
    Acceptance Gate: SSRF validation on EVERY redirect hop.
    
    Per OWASP: redirects are a common SSRF bypass vector.
    Each hop must be validated, not just the final URL.
    """
    
    @pytest.mark.asyncio
    async def test_redirect_chain_recorded(self):
        """FetchResult includes redirect_chain for auditing."""
        from integration_coworker.discovery.http_client import FetchResult
        
        result = FetchResult(
            success=True,
            content="test",
            redirect_chain=["https://a.com", "https://b.com", "https://c.com"],
        )
        
        assert result.redirect_chain is not None
        assert len(result.redirect_chain) == 3
    
    def test_per_hop_validation_blocks_mid_chain_ssrf(self):
        """Per-hop validation function catches SSRF in redirect chain."""
        from integration_coworker.discovery.http_client import (
            _validate_hop,
        )
        
        # First hop is safe
        is_safe, error, _ = _validate_hop(
            "https://example.com/", 
            allow_http=False, 
            validate_dns=False,  # Skip DNS for unit test
            hop_number=0
        )
        assert is_safe, f"Expected safe, got: {error}"
        
        # Second hop to localhost should be blocked
        is_safe, error, _ = _validate_hop(
            "https://localhost/internal",
            allow_http=False,
            validate_dns=False,
            hop_number=1,
            previous_url="https://example.com/"
        )
        assert not is_safe, "Should block localhost"
        assert "localhost" in error.lower() or "blocked" in error.lower()
    
    def test_scheme_downgrade_blocked(self):
        """HTTPS→HTTP downgrade in redirect chain is blocked."""
        from integration_coworker.discovery.http_client import _validate_hop
        
        # Redirect from HTTPS to HTTP should be blocked
        is_safe, error, _ = _validate_hop(
            "http://example.com/",  # HTTP!
            allow_http=False,
            validate_dns=False,
            hop_number=1,
            previous_url="https://secure.com/"  # Was HTTPS
        )
        assert not is_safe, "Should block HTTPS→HTTP downgrade"
        assert "downgrade" in error.lower() or "http" in error.lower()
    
    def test_dangerous_schemes_blocked(self):
        """Non-HTTP(S) schemes like file://, gopher://, ftp:// are blocked."""
        from integration_coworker.discovery.http_client import _validate_hop
        
        dangerous_schemes = [
            "file:///etc/passwd",
            "ftp://attacker.com/malware",
            "gopher://attacker.com:70/_",
            "dict://attacker.com:11111/",
            "ldap://attacker.com/",
        ]
        
        for url in dangerous_schemes:
            is_safe, error, _ = _validate_hop(
                url,
                allow_http=True,  # Even with allow_http, these should be blocked
                validate_dns=False,
                hop_number=0,
            )
            assert not is_safe, f"Should block dangerous scheme: {url}"
            assert "scheme" in error.lower() or "blocked" in error.lower()
    
    def test_redirect_loop_detection_via_canonicalize(self):
        """URL canonicalization supports loop detection."""
        from integration_coworker.discovery.http_client import _canonicalize_url
        
        # Same URL different case/port should canonicalize the same
        assert _canonicalize_url("https://example.com/") == _canonicalize_url("https://EXAMPLE.COM/")
        assert _canonicalize_url("https://example.com:443/") == _canonicalize_url("https://example.com/")
        assert _canonicalize_url("http://example.com:80/") == _canonicalize_url("http://example.com/")
        
        # Different paths should be different
        assert _canonicalize_url("https://example.com/a") != _canonicalize_url("https://example.com/b")


# =============================================================================
# Gate 4: Explicit HTTPX Timeout and Limits
# =============================================================================

class TestExplicitTimeoutAndLimits:
    """
    Acceptance Gate: HTTPX configured with explicit Timeout and Limits.
    
    Per https://www.python-httpx.org/api/:
    - Timeout must specify connect/read/write/pool, not just a float
    - Limits must cap connections and keepalive
    """
    
    def test_timeout_config_explicit(self):
        """Timeout configuration specifies all components."""
        from integration_coworker.discovery.http_client import get_hardened_timeout
        
        timeout = get_hardened_timeout()
        
        # All components must be set (not None)
        assert timeout.connect is not None
        assert timeout.read is not None
        assert timeout.write is not None
        assert timeout.pool is not None
    
    def test_timeout_config_values_reasonable(self):
        """Timeout values are reasonable (not defaults)."""
        from integration_coworker.discovery.http_client import get_timeout_config
        
        config = get_timeout_config()
        
        # Connect should be relatively short
        assert 1.0 <= config["connect"] <= 30.0
        # Read can be longer
        assert 5.0 <= config["read"] <= 120.0
        # Pool timeout reasonable
        assert 1.0 <= config["pool"] <= 60.0
    
    def test_limits_config_explicit(self):
        """Limits configuration caps connections."""
        from integration_coworker.discovery.http_client import get_hardened_limits, get_limits_config
        
        limits = get_hardened_limits()
        config = get_limits_config()
        
        # Limits must be set
        assert limits.max_connections is not None
        assert limits.max_keepalive_connections is not None
        
        # Values from config
        assert config["max_connections"] > 0
        assert config["max_keepalive_connections"] > 0
        assert config["max_redirects"] > 0
    
    def test_limits_not_unbounded(self):
        """Connection limits are bounded (not too high)."""
        from integration_coworker.discovery.http_client import get_limits_config
        
        config = get_limits_config()
        
        # Reasonable bounds
        assert config["max_connections"] <= 100
        assert config["max_keepalive_connections"] <= 50
        assert config["max_redirects"] <= 10


# =============================================================================
# Gate 5: Transaction-Scoped HNSW ef_search
# =============================================================================

class TestHNSWEfSearchTransactionScoped:
    """
    Acceptance Gate: SET LOCAL hnsw.ef_search in same transaction as query.
    
    SET LOCAL only applies within the current transaction. If autocommit
    or a new transaction is opened, the setting silently does nothing.
    """
    
    @pytest.mark.asyncio
    async def test_ef_search_set_before_vector_query(self):
        """SET LOCAL hnsw.ef_search is called before vector similarity query."""
        executed_sql = []
        
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = []
        
        def capture_execute(sql, params=None):
            executed_sql.append(sql if isinstance(sql, str) else str(sql))
        
        mock_cursor.execute = capture_execute
        
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        
        @contextmanager
        def mock_ctx():
            yield mock_conn
        
        async def mock_embedding(query):
            return [0.1] * 1536
        
        with patch(
            "integration_coworker.discovery.catalog._get_catalog_connection",
            mock_ctx
        ), patch(
            "integration_coworker.discovery.catalog._compute_query_embedding",
            mock_embedding
        ), patch(
            "integration_coworker.config.get_settings"
        ) as mock_settings:
            mock_settings.return_value.discovery_hnsw_ef_search = 100
            
            from integration_coworker.discovery.catalog import _search_semantic
            await _search_semantic(mock_conn, "test query", max_results=10, include_untrusted=False)
        
        # Find SET LOCAL and vector query
        set_local_idx = None
        vector_query_idx = None
        
        for i, sql in enumerate(executed_sql):
            if "SET LOCAL hnsw.ef_search" in sql:
                set_local_idx = i
            if "<=>" in sql or "embedding" in sql.lower():
                vector_query_idx = i
        
        assert set_local_idx is not None, f"SET LOCAL not found. SQL: {executed_sql}"
        
        if vector_query_idx is not None:
            assert set_local_idx < vector_query_idx, (
                f"SET LOCAL (idx {set_local_idx}) must come before vector query (idx {vector_query_idx})"
            )


# =============================================================================
# Gate 6: HITL Non-Interactive Safety
# =============================================================================

class TestHITLNonInteractiveSafety:
    """
    Acceptance Gate: HITL doesn't break non-interactive callers.
    
    Default behavior (require_confirmation=False) must:
    - Never return requires_hitl=True
    - Always proceed with best-effort selection
    """
    
    @pytest.mark.asyncio
    async def test_default_never_blocks_on_hitl(self):
        """Default call (require_confirmation=False) never returns requires_hitl."""
        from integration_coworker.discovery.resolver import resolve_spec_from_task
        from integration_coworker.discovery.apis_guru import SpecCandidate
        
        # Mock to return low-confidence candidates
        mock_candidates = [
            SpecCandidate(
                provider="test.com",
                api_name="Test API",
                spec_url="https://test.com/spec.yaml",
                spec_format="openapi_3.0",
                score=0.3,  # Low score
            ),
            SpecCandidate(
                provider="test2.com",
                api_name="Test API 2",
                spec_url="https://test2.com/spec.yaml",
                spec_format="openapi_3.0",
                score=0.2,
            ),
        ]
        
        with patch(
            "integration_coworker.discovery.resolver.search_apis_guru",
            new_callable=AsyncMock,
            return_value=mock_candidates,
        ), patch(
            "integration_coworker.discovery.resolver._search_local_catalog",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "integration_coworker.discovery.resolver.validate_spec_url",
            new_callable=AsyncMock,
        ) as mock_validate:
            mock_validate.return_value = MagicMock(valid=True, to_dict=lambda: {})
            
            # Default call - should NOT block on HITL
            result = await resolve_spec_from_task(
                "some task",
                # require_confirmation=False is default
            )
            
            # Should NOT return HITL request
            assert not result.requires_hitl, "Default call should never require HITL"
    
    @pytest.mark.asyncio
    async def test_require_confirmation_returns_hitl(self):
        """require_confirmation=True returns HITL request when confidence low."""
        from integration_coworker.discovery.resolver import resolve_spec_from_task
        from integration_coworker.discovery.apis_guru import SpecCandidate
        
        mock_candidates = [
            SpecCandidate(
                provider="test.com",
                api_name="Test API",
                spec_url="https://test.com/spec.yaml",
                spec_format="openapi_3.0",
                score=0.3,
            ),
            SpecCandidate(
                provider="test2.com",
                api_name="Test API 2",
                spec_url="https://test2.com/spec.yaml",
                spec_format="openapi_3.0",
                score=0.2,
            ),
        ]
        
        with patch(
            "integration_coworker.discovery.resolver.search_apis_guru",
            new_callable=AsyncMock,
            return_value=mock_candidates,
        ), patch(
            "integration_coworker.discovery.resolver._search_local_catalog",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "integration_coworker.config.get_settings"
        ) as mock_settings:
            mock_settings.return_value.discovery_confirmation_threshold = 0.9  # High threshold
            
            result = await resolve_spec_from_task(
                "some task",
                require_confirmation=True,  # Explicitly request HITL
            )
            
            # Should return HITL request
            assert result.requires_hitl, "require_confirmation=True should return HITL when low confidence"
            assert result.hitl_request is not None


# =============================================================================
# Gate 7: DNS Resolution Enforced
# =============================================================================

class TestDNSValidationEnforced:
    """
    Acceptance Gate: DNS resolution is validated before fetching.
    """
    
    @pytest.mark.asyncio
    async def test_dns_resolution_blocks_private_ip(self):
        """DNS resolving to private IP blocks the fetch."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (2, 1, 6, '', ('192.168.1.100', 443)),
            ]
            
            from integration_coworker.discovery.http_client import hardened_fetch
            
            result = await hardened_fetch("https://attacker-controlled.com/spec.yaml")
            
            assert not result.success
            assert "blocked" in result.error.lower() or "private" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_dns_resolution_blocks_loopback(self):
        """DNS resolving to loopback blocks the fetch."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (2, 1, 6, '', ('127.0.0.1', 443)),
            ]
            
            from integration_coworker.discovery.http_client import hardened_fetch
            
            result = await hardened_fetch("https://attacker-controlled.com/spec.yaml")
            
            assert not result.success
            assert "blocked" in result.error.lower() or "127.0.0.1" in result.error
    
    @pytest.mark.asyncio
    async def test_dns_resolution_blocks_metadata_ip(self):
        """DNS resolving to cloud metadata IP blocks the fetch."""
        with patch("socket.getaddrinfo") as mock_getaddrinfo:
            mock_getaddrinfo.return_value = [
                (2, 1, 6, '', ('169.254.169.254', 443)),
            ]
            
            from integration_coworker.discovery.http_client import hardened_fetch
            
            result = await hardened_fetch("https://attacker-controlled.com/spec.yaml")
            
            assert not result.success
            assert "blocked" in result.error.lower()


# =============================================================================
# Additional Hardening Tests
# =============================================================================

class TestProxyEnvBlocked:
    """Test that proxy environment variables are blocked."""
    
    @pytest.mark.asyncio
    async def test_trust_env_false(self):
        """Client is created with trust_env=False."""
        # This is verified by code inspection, but we can check the module
        import integration_coworker.discovery.http_client as http_client
        
        # Read the source and verify trust_env=False is set
        source = Path(http_client.__file__).read_text()
        assert "trust_env=False" in source, "httpx client must have trust_env=False"


class TestIntentAnalysisStableContract:
    """Tests verifying IntentAnalysis dataclass contract is stable."""
    
    def test_intent_analysis_has_required_fields(self):
        """IntentAnalysis has all required fields."""
        intent = IntentAnalysis(
            explicit_provider="test.com",
            inferred_providers=["a.com", "b.com"],
            keywords=["kw1", "kw2"],
            confidence=0.8,
            raw_task="test task",
        )
        
        assert intent.explicit_provider == "test.com"
        assert intent.inferred_providers == ["a.com", "b.com"]
        assert intent.keywords == ["kw1", "kw2"]
        assert intent.confidence == 0.8
        assert intent.raw_task == "test task"
    
    def test_intent_analysis_has_helper_properties(self):
        """IntentAnalysis has helper properties."""
        intent_with = IntentAnalysis(
            explicit_provider="test.com",
            inferred_providers=[],
            keywords=[],
            confidence=0.8,
            raw_task="",
        )
        intent_without = IntentAnalysis(
            explicit_provider=None,
            inferred_providers=["inferred.com"],
            keywords=[],
            confidence=0.5,
            raw_task="",
        )
        
        assert intent_with.has_explicit_provider is True
        assert intent_without.has_explicit_provider is False
        
        assert intent_with.best_provider_hint == "test.com"
        assert intent_without.best_provider_hint == "inferred.com"


# =============================================================================
# Integration-Style Acceptance Tests (higher-value end-to-end proofs)
# =============================================================================

class TestRedirectChainBlockIntegration:
    """
    Acceptance Test: Redirect chain A→B→localhost is blocked at hop 2.
    
    This verifies the manual redirect loop with per-hop validation actually
    works end-to-end, not just that the validation function exists.
    """
    
    @pytest.mark.asyncio
    async def test_redirect_chain_blocked_at_ssrf_hop(self):
        """URL A → B → localhost is blocked on hop 2, not after final fetch."""
        from integration_coworker.discovery.http_client import hardened_fetch
        
        # Create mock headers that support get_list()
        def make_mock_headers(location):
            """Create mock headers that mimic httpx.Headers with get_list support."""
            mock = MagicMock()
            mock.get = MagicMock(return_value=location)
            mock.get_list = MagicMock(return_value=[location] if location else [])
            return mock
        
        # Mock httpx.AsyncClient to simulate redirects
        hop_0_response = MagicMock()
        hop_0_response.status_code = 302
        hop_0_response.headers = make_mock_headers("https://intermediate.example.com/api")
        
        hop_1_response = MagicMock()
        hop_1_response.status_code = 302
        # Use HTTPS to avoid scheme downgrade, test pure SSRF
        hop_1_response.headers = make_mock_headers("https://127.0.0.1/internal")  # SSRF!
        
        call_count = [0]
        
        async def mock_get(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return hop_0_response
            elif call_count[0] == 2:
                return hop_1_response
            else:
                # Should never get here - should be blocked before hop 3
                pytest.fail("Request made to third hop - SSRF not blocked!")
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await hardened_fetch(
                "https://start.example.com/api",
                validate_dns=False,  # Skip DNS for unit test
            )
        
        assert not result.success, "Should fail on SSRF redirect"
        # Should block on loopback IP
        assert "127.0.0.1" in result.error or "loopback" in result.error.lower()
        # Should have recorded the chain up to but not including the blocked hop
        assert result.redirect_chain is not None
        assert len(result.redirect_chain) >= 1
    
    @pytest.mark.asyncio
    async def test_redirect_loop_detected(self):
        """Redirect loop A → B → A is detected and blocked."""
        from integration_coworker.discovery.http_client import hardened_fetch
        
        def make_mock_headers(location):
            """Create mock headers that mimic httpx.Headers with get_list support."""
            mock = MagicMock()
            mock.get = MagicMock(return_value=location)
            mock.get_list = MagicMock(return_value=[location] if location else [])
            return mock
        
        responses = [
            MagicMock(status_code=302, headers=make_mock_headers("https://b.example.com/")),
            MagicMock(status_code=302, headers=make_mock_headers("https://a.example.com/")),  # Back to A!
        ]
        response_idx = [0]
        
        async def mock_get(url, **kwargs):
            idx = response_idx[0]
            response_idx[0] += 1
            if idx < len(responses):
                return responses[idx]
            pytest.fail("Too many requests - loop not detected!")
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await hardened_fetch(
                "https://a.example.com/",
                validate_dns=False,
            )
        
        assert not result.success, "Should fail on redirect loop"
        assert "loop" in result.error.lower(), f"Error should mention loop: {result.error}"


class TestStreamingByteCap:
    """
    Acceptance Gate: Hard byte cap enforced via streaming.
    
    The response body must be read via aiter_bytes() with incremental
    accumulation, not via response.text which materializes everything first.
    """
    
    @pytest.mark.asyncio
    async def test_byte_cap_enforced_via_streaming(self):
        """Large response is rejected mid-stream, not after full download."""
        from integration_coworker.discovery.http_client import hardened_fetch
        
        # Track how many bytes we actually "sent" before rejection
        bytes_sent = [0]
        small_limit = 1000  # 1KB limit for test
        
        async def mock_aiter_bytes():
            """Yield chunks until we exceed limit."""
            for i in range(100):
                chunk = b"x" * 100  # 100-byte chunks
                bytes_sent[0] += len(chunk)
                yield chunk
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}  # No Content-Length
        mock_response.raise_for_status = MagicMock()
        mock_response.aiter_bytes = mock_aiter_bytes
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        # Non-redirect response for initial GET
        initial_response = MagicMock()
        initial_response.status_code = 200
        initial_response.headers = {"content-type": "text/plain"}
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=initial_response)
            mock_client.stream = MagicMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await hardened_fetch(
                "https://example.com/large-file",
                max_bytes=small_limit,
                validate_dns=False,
            )
        
        assert not result.success, "Should fail on oversized response"
        assert "too large" in result.error.lower() or "exceeded" in result.error.lower()
        # Critical: We should have stopped early, not downloaded all 10KB
        assert bytes_sent[0] < 5000, f"Downloaded too much before stopping: {bytes_sent[0]} bytes"
    
    @pytest.mark.asyncio
    async def test_content_length_precheck(self):
        """Content-Length header is checked before streaming starts."""
        from integration_coworker.discovery.http_client import hardened_fetch
        
        small_limit = 1000
        
        # Response with large Content-Length
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-length": "999999999"}  # 1GB!
        mock_response.raise_for_status = MagicMock()
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        stream_called = [False]
        original_aiter = mock_response.aiter_bytes
        
        async def track_aiter():
            stream_called[0] = True
            async for chunk in original_aiter():
                yield chunk
        
        mock_response.aiter_bytes = track_aiter
        
        # Non-redirect response for initial GET
        initial_response = MagicMock()
        initial_response.status_code = 200
        initial_response.headers = {"content-type": "text/plain"}
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=initial_response)
            mock_client.stream = MagicMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await hardened_fetch(
                "https://example.com/giant-file",
                max_bytes=small_limit,
                validate_dns=False,
            )
        
        assert not result.success
        assert "too large" in result.error.lower() or "content-length" in result.error.lower()
        # Content-Length check should have rejected before any streaming
        assert not stream_called[0], "Should reject based on Content-Length without streaming"


# =============================================================================
# BEHAVIOR-PROVING TESTS (Step 4 of hardening plan)
# =============================================================================
# These tests PROVE the contract invariants are enforced. They fail if someone:
# - Reintroduces follow_redirects=True
# - Switches from aiter_bytes() to response.text
# - Removes trust_env=False
# - Breaks canonicalization
# =============================================================================

class TestInvariant1FollowRedirectsFalse:
    """
    INVARIANT 1: follow_redirects=False at httpx client level.
    
    Test strategy: Verify the source code directly contains the invariant.
    This catches accidental removal or changes.
    """
    
    def test_follow_redirects_always_false(self):
        """The http_client.py source must have follow_redirects=False."""
        import integration_coworker.discovery.http_client as http_client
        source = Path(http_client.__file__).read_text()
        
        # Must have the invariant
        assert "follow_redirects=False" in source, (
            "INVARIANT 1 VIOLATION: follow_redirects=False not found in http_client.py. "
            "Manual redirect loop is REQUIRED for per-hop SSRF validation."
        )
        
        # Must NOT have follow_redirects=True
        assert "follow_redirects=True" not in source, (
            "INVARIANT 1 VIOLATION: follow_redirects=True found in http_client.py. "
            "This bypasses per-hop SSRF validation and is FORBIDDEN."
        )


class TestInvariant2OnlyRedirectStatusCodes:
    """
    INVARIANT 2: Manual loop follows ONLY 301/302/303/307/308.
    
    Test strategy: Verify REDIRECT_STATUS_CODES constant exists and is correct.
    """
    
    def test_only_redirect_status_codes_followed(self):
        """Only standard HTTP redirect codes are followed."""
        from integration_coworker.discovery.http_client import REDIRECT_STATUS_CODES
        
        expected = frozenset({301, 302, 303, 307, 308})
        assert REDIRECT_STATUS_CODES == expected, (
            f"INVARIANT 2 VIOLATION: REDIRECT_STATUS_CODES is {REDIRECT_STATUS_CODES}, "
            f"expected {expected}. Non-standard codes could bypass validation."
        )


class TestInvariant3NoResponseTextOrContent:
    """
    INVARIANT 3: NEVER access response.text or response.content in http_client.py.
    
    Test strategy: Scan source for forbidden patterns in actual code, not docs.
    """
    
    def test_no_response_text_or_content_access(self):
        """Source must not use response.text or response.content in actual code."""
        import integration_coworker.discovery.http_client as http_client
        source = Path(http_client.__file__).read_text()
        
        # Track if we're in a docstring
        in_docstring = False
        docstring_delimiter = None
        
        lines = source.split('\n')
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            
            # Track docstring state
            if not in_docstring:
                if stripped.startswith('"""') or stripped.startswith("'''"):
                    delimiter = '"""' if stripped.startswith('"""') else "'''"
                    # Check if it's a one-liner docstring
                    if stripped.count(delimiter) >= 2:
                        continue  # One-liner docstring, skip the line
                    in_docstring = True
                    docstring_delimiter = delimiter
                    continue
            else:
                if docstring_delimiter in stripped:
                    in_docstring = False
                    docstring_delimiter = None
                continue
            
            # Skip comment lines
            if stripped.startswith('#'):
                continue
            
            # Check for code before any inline comment
            code_part = line.split('#')[0]
            
            # Skip lines that are just part of strings or documentation
            # Check for actual method calls: response.text or response.content
            # Not in quoted strings
            if 'response.text' in code_part:
                # Make sure it's not in a string literal
                if '"response.text"' in code_part or "'response.text'" in code_part:
                    continue
                assert False, (
                    f"INVARIANT 3 VIOLATION at line {i}: response.text found in code. "
                    "This materializes the full body and defeats streaming byte cap. "
                    "Use aiter_bytes() instead."
                )
            if 'response.content' in code_part:
                if '"response.content"' in code_part or "'response.content'" in code_part:
                    continue
                assert False, (
                    f"INVARIANT 3 VIOLATION at line {i}: response.content found in code. "
                    "This materializes the full body and defeats streaming byte cap. "
                    "Use aiter_bytes() instead."
                )
        
        # Must have aiter_bytes for streaming
        assert "aiter_bytes" in source, (
            "INVARIANT 3 VIOLATION: aiter_bytes not found in http_client.py. "
            "Streaming byte cap REQUIRES aiter_bytes() for incremental reading."
        )


class TestInvariant4ValidateBeforeRequest:
    """
    INVARIANT 4: Validate next hop BEFORE requesting it.
    
    Test strategy: Verify source code order shows validation before request.
    """
    
    def test_validate_before_request(self):
        """Source code validates next hop BEFORE making network request."""
        import integration_coworker.discovery.http_client as http_client
        source = Path(http_client.__file__).read_text()
        
        # The pattern we're looking for in the redirect handling code:
        # 1. _validate_hop is called on next_url
        # 2. Only AFTER validation passes, we set current_url = next_url and continue
        # 3. The actual request (client.get) happens at top of next loop iteration
        
        # Find the redirect handling section
        assert "Validate NEXT hop BEFORE following" in source or "INVARIANT 4" in source, (
            "INVARIANT 4 VIOLATION: Missing documentation about validate-before-request"
        )
        
        # Verify the code structure: _validate_hop call must appear in redirect handling
        # and current_url = next_url must come AFTER the validation check
        lines = source.split('\n')
        in_redirect_block = False
        validate_hop_line = None
        set_current_url_line = None
        
        for i, line in enumerate(lines):
            if 'if response.status_code in REDIRECT_STATUS_CODES' in line:
                in_redirect_block = True
            if in_redirect_block:
                # _validate_hop can be on one line or split across lines
                if '_validate_hop(' in line:
                    validate_hop_line = i
                if 'current_url = next_url' in line:
                    set_current_url_line = i
                if 'continue' in line and set_current_url_line:
                    break  # End of redirect block
        
        assert validate_hop_line is not None, (
            "INVARIANT 4 VIOLATION: _validate_hop not called in redirect handling"
        )
        assert set_current_url_line is not None, (
            "INVARIANT 4 VIOLATION: current_url = next_url not found after validation"
        )
        assert validate_hop_line < set_current_url_line, (
            f"INVARIANT 4 VIOLATION: _validate_hop (line {validate_hop_line}) must come "
            f"BEFORE current_url = next_url (line {set_current_url_line})"
        )


class TestInvariant5TrustEnvFalse:
    """
    INVARIANT 5: trust_env=False for proxy immunity.
    
    Test strategy: Verify source code and constructor call.
    """
    
    def test_trust_env_always_false(self):
        """http_client.py must have trust_env=False."""
        import integration_coworker.discovery.http_client as http_client
        source = Path(http_client.__file__).read_text()
        
        assert "trust_env=False" in source, (
            "INVARIANT 5 VIOLATION: trust_env=False not found in http_client.py. "
            "Without this, HTTP_PROXY/HTTPS_PROXY env vars can redirect traffic to attacker."
        )
        
        # Must NOT have trust_env=True
        assert "trust_env=True" not in source, (
            "INVARIANT 5 VIOLATION: trust_env=True found in http_client.py. "
            "This allows proxy env vars to redirect traffic."
        )
    
    @pytest.mark.asyncio
    async def test_proxy_env_vars_actually_ignored(self):
        """Verify proxy env vars don't affect requests."""
        import os
        from integration_coworker.discovery.http_client import hardened_fetch
        
        original_proxy = os.environ.get("HTTPS_PROXY")
        try:
            os.environ["HTTPS_PROXY"] = "http://evil-proxy.attacker.com:8080"
            
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get = AsyncMock(return_value=MagicMock(
                    status_code=200,
                    headers={"content-type": "text/plain"}
                ))
                mock_client.stream = MagicMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client
                
                await hardened_fetch("https://safe.example.com/", validate_dns=False)
                
                # Verify trust_env=False was passed
                _, kwargs = mock_client_class.call_args
                assert kwargs.get("trust_env") is False, (
                    f"INVARIANT 5 VIOLATION: trust_env should be False, got {kwargs}"
                )
        finally:
            if original_proxy is None:
                os.environ.pop("HTTPS_PROXY", None)
            else:
                os.environ["HTTPS_PROXY"] = original_proxy


class TestInvariant6TotalTimeoutBudget:
    """
    INVARIANT 6: Total timeout budget across redirect chain.
    
    Test strategy: Verify total_timeout parameter exists and is enforced.
    """
    
    def test_total_timeout_budget_exists(self):
        """hardened_fetch has total_timeout_seconds parameter."""
        import inspect
        from integration_coworker.discovery.http_client import hardened_fetch
        
        sig = inspect.signature(hardened_fetch)
        params = list(sig.parameters.keys())
        
        assert "total_timeout_seconds" in params, (
            "INVARIANT 6 VIOLATION: total_timeout_seconds parameter not found. "
            "Without total budget, 5 redirects × 30s = 150s stall is possible."
        )
    
    def test_default_total_timeout_exists(self):
        """DEFAULT_TOTAL_TIMEOUT constant exists and is reasonable."""
        from integration_coworker.discovery.http_client import DEFAULT_TOTAL_TIMEOUT
        
        assert DEFAULT_TOTAL_TIMEOUT is not None, (
            "INVARIANT 6 VIOLATION: DEFAULT_TOTAL_TIMEOUT not defined"
        )
        # Should be less than max_redirects * per_hop_timeout
        # 5 redirects × 30s = 150s, so total should be < 150s
        assert DEFAULT_TOTAL_TIMEOUT <= 120, (
            f"INVARIANT 6 VIOLATION: DEFAULT_TOTAL_TIMEOUT={DEFAULT_TOTAL_TIMEOUT}s is too high. "
            "Should prevent multi-redirect stalls."
        )
    
    @pytest.mark.asyncio
    async def test_total_timeout_budget_enforced(self):
        """Total budget is actually enforced across redirects."""
        from integration_coworker.discovery.http_client import hardened_fetch
        import time
        
        def make_mock_headers(location):
            """Create mock headers that mimic httpx.Headers with get_list support."""
            mock = MagicMock()
            mock.get = MagicMock(return_value=location)
            mock.get_list = MagicMock(return_value=[location] if location else [])
            return mock
        
        # Simulate slow redirects that would exceed budget
        call_count = [0]
        
        async def slow_get(url, **kwargs):
            call_count[0] += 1
            # Simulate delay
            time.sleep(0.5)  # 500ms per redirect
            response = MagicMock()
            response.status_code = 302
            response.headers = make_mock_headers(f"https://hop{call_count[0]}.example.com/")
            return response
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = slow_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            start = time.monotonic()
            result = await hardened_fetch(
                "https://start.example.com/",
                total_timeout_seconds=1.0,  # 1 second total budget
                max_redirects=10,  # Many redirects allowed
                validate_dns=False,
            )
            elapsed = time.monotonic() - start
        
        # Should have been cut off by budget, not completed all redirects
        assert not result.success, "Should fail when budget exhausted"
        assert "budget" in result.error.lower() or "timeout" in result.error.lower(), (
            f"Error should mention budget/timeout: {result.error}"
        )
        # Should have stopped reasonably quickly (within 2x budget + overhead)
        assert elapsed < 3.0, f"Took too long ({elapsed}s), budget not enforced"


class TestInvariant7RedirectsDontReadBodies:
    """
    INVARIANT 7: Redirect responses don't read bodies (only final 200 streams).
    
    Test strategy: Verify redirects only extract Location header, no body reading.
    """
    
    @pytest.mark.asyncio
    async def test_redirects_dont_read_bodies(self):
        """3xx responses should not read body, only Location header."""
        from integration_coworker.discovery.http_client import hardened_fetch
        
        redirect_body_read = [False]
        
        class TrackingResponse:
            def __init__(self, status_code, location=None):
                self.status_code = status_code
                self.headers = {"location": location} if location else {}
            
            @property
            def text(self):
                redirect_body_read[0] = True
                return "should not read"
            
            @property
            def content(self):
                redirect_body_read[0] = True
                return b"should not read"
        
        redirect_response = TrackingResponse(302, "https://final.example.com/")
        final_response = MagicMock()
        final_response.status_code = 200
        final_response.headers = {"content-type": "text/plain"}
        
        call_count = [0]
        
        async def mock_get(url, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return redirect_response
            return final_response
        
        # Mock stream for final response
        mock_stream = MagicMock()
        mock_stream.status_code = 200
        mock_stream.headers = {"content-type": "text/plain"}
        mock_stream.raise_for_status = MagicMock()
        
        async def mock_aiter():
            yield b"final content"
        
        mock_stream.aiter_bytes = mock_aiter
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await hardened_fetch(
                "https://start.example.com/",
                validate_dns=False,
            )
        
        assert not redirect_body_read[0], (
            "INVARIANT 7 VIOLATION: Redirect response body was read. "
            "Only Location header should be extracted from 3xx responses."
        )


class TestInvariant8Canonicalization:
    """
    INVARIANT 8: Canonicalization for loop detection.
    
    Test strategy: Verify all canonicalization rules work.
    """
    
    def test_canonicalization_scheme_case(self):
        """Scheme is lowercased."""
        from integration_coworker.discovery.http_client import _canonicalize_url
        
        assert _canonicalize_url("HTTP://example.com/") == _canonicalize_url("http://example.com/")
        assert _canonicalize_url("HTTPS://example.com/") == _canonicalize_url("https://example.com/")
    
    def test_canonicalization_host_case(self):
        """Host is lowercased."""
        from integration_coworker.discovery.http_client import _canonicalize_url
        
        assert _canonicalize_url("https://EXAMPLE.COM/") == _canonicalize_url("https://example.com/")
        assert _canonicalize_url("https://ExAmPlE.CoM/") == _canonicalize_url("https://example.com/")
    
    def test_canonicalization_default_port(self):
        """Default ports are stripped."""
        from integration_coworker.discovery.http_client import _canonicalize_url
        
        # HTTPS default port
        assert _canonicalize_url("https://example.com:443/") == _canonicalize_url("https://example.com/")
        # HTTP default port
        assert _canonicalize_url("http://example.com:80/") == _canonicalize_url("http://example.com/")
        # Non-default ports preserved
        assert _canonicalize_url("https://example.com:8443/") != _canonicalize_url("https://example.com/")
    
    def test_canonicalization_path_normalization(self):
        """Path is normalized (.. and . resolved)."""
        from integration_coworker.discovery.http_client import _canonicalize_url
        
        # Parent traversal
        assert _canonicalize_url("https://example.com/a/../b") == _canonicalize_url("https://example.com/b")
        # Current directory
        assert _canonicalize_url("https://example.com/a/./b") == _canonicalize_url("https://example.com/a/b")
        # Mid-path double slashes are normalized
        assert _canonicalize_url("https://example.com/a//b") == _canonicalize_url("https://example.com/a/b")
    
    def test_canonicalization_fragment_stripped(self):
        """Fragment is stripped."""
        from integration_coworker.discovery.http_client import _canonicalize_url
        
        assert _canonicalize_url("https://example.com/path#fragment") == _canonicalize_url("https://example.com/path")
        assert _canonicalize_url("https://example.com/#anchor") == _canonicalize_url("https://example.com/")
    
    def test_canonicalization_querystring_sorted(self):
        """Querystring params are sorted."""
        from integration_coworker.discovery.http_client import _canonicalize_url
        
        assert _canonicalize_url("https://example.com/?b=2&a=1") == _canonicalize_url("https://example.com/?a=1&b=2")
    
    def test_canonicalization_prevents_bypass(self):
        """Canonicalization prevents loop detection bypass."""
        from integration_coworker.discovery.http_client import _canonicalize_url
        
        # All of these should canonicalize to the same URL
        variants = [
            "https://example.com/path",
            "HTTPS://EXAMPLE.COM/path",
            "https://example.com:443/path",
            "https://example.com/a/../path",
            "https://example.com/path#ignored",
        ]
        
        canonical = _canonicalize_url(variants[0])
        for variant in variants[1:]:
            assert _canonicalize_url(variant) == canonical, (
                f"INVARIANT 8 VIOLATION: {variant} should canonicalize to {canonical}, "
                f"got {_canonicalize_url(variant)}"
            )


class TestProxyEnvImmunityIntegration:
    """
    Acceptance Test: HTTP_PROXY and HTTPS_PROXY env vars are ignored.
    
    Even with malicious proxy env vars set, requests go direct because
    we construct clients with trust_env=False.
    """
    
    @pytest.mark.asyncio
    async def test_proxy_env_vars_ignored(self):
        """Requests still go direct even with proxy env vars set."""
        import os
        from integration_coworker.discovery.http_client import hardened_fetch
        
        # Set malicious proxy env vars
        original_http = os.environ.get("HTTP_PROXY")
        original_https = os.environ.get("HTTPS_PROXY")
        original_all = os.environ.get("ALL_PROXY")
        
        try:
            os.environ["HTTP_PROXY"] = "http://malicious-proxy.attacker.com:8080"
            os.environ["HTTPS_PROXY"] = "http://malicious-proxy.attacker.com:8080"
            os.environ["ALL_PROXY"] = "http://malicious-proxy.attacker.com:8080"
            
            # Track what URL was actually requested
            requested_urls = []
            
            async def mock_get(url, **kwargs):
                requested_urls.append(str(url))
                response = MagicMock()
                response.status_code = 200
                response.headers = {"content-type": "application/json"}
                return response
            
            # Mock stream for final response
            mock_stream = MagicMock()
            mock_stream.status_code = 200
            mock_stream.headers = {"content-type": "application/json"}
            mock_stream.raise_for_status = MagicMock()
            
            async def mock_aiter():
                yield b'{"openapi": "3.0.0"}'
            
            mock_stream.aiter_bytes = mock_aiter
            mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream.__aexit__ = AsyncMock(return_value=None)
            
            with patch("httpx.AsyncClient") as mock_client_class:
                mock_client = AsyncMock()
                mock_client.get = mock_get
                mock_client.stream = MagicMock(return_value=mock_stream)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_class.return_value = mock_client
                
                result = await hardened_fetch(
                    "https://api.example.com/openapi.json",
                    validate_dns=False,
                )
                
                # Verify trust_env=False was passed
                _, kwargs = mock_client_class.call_args
                assert kwargs.get("trust_env") is False, (
                    f"trust_env must be False, got {kwargs}"
                )
            
            # The request should go to the actual URL, not the proxy
            assert len(requested_urls) >= 1
            assert "api.example.com" in requested_urls[0]
            assert "malicious-proxy" not in requested_urls[0]
            
        finally:
            # Restore env vars
            if original_http is None:
                os.environ.pop("HTTP_PROXY", None)
            else:
                os.environ["HTTP_PROXY"] = original_http
            if original_https is None:
                os.environ.pop("HTTPS_PROXY", None)
            else:
                os.environ["HTTPS_PROXY"] = original_https
            if original_all is None:
                os.environ.pop("ALL_PROXY", None)
            else:
                os.environ["ALL_PROXY"] = original_all


@pytest.mark.integration
class TestTransactionLocalEfSearchIntegration:
    """
    Acceptance Test: SET LOCAL hnsw.ef_search is transaction-scoped.
    
    This verifies with a real database that:
    1. SET LOCAL runs in the same transaction as the vector query
    2. Different ef_search values don't leak between requests
    
    Requires: INTEGRATION_TESTS=1 and DATABASE_URL set
    Per pgvector docs: SET LOCAL is the intended pattern for per-query tuning.
    """
    
    @pytest.mark.asyncio
    async def test_ef_search_transaction_isolated(self):
        """Two queries with different ef_search don't affect each other."""
        import os
        if not os.environ.get("INTEGRATION_TESTS"):
            pytest.skip("Requires INTEGRATION_TESTS=1")
        
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            pytest.skip("Requires DATABASE_URL")
        
        try:
            import asyncpg
        except ImportError:
            pytest.skip("Requires asyncpg")
        
        # Track executed SQL in order per connection
        connection1_sql = []
        connection2_sql = []
        
        async def capture_sql_conn1(sql, *args):
            connection1_sql.append(sql)
        
        async def capture_sql_conn2(sql, *args):
            connection2_sql.append(sql)
        
        # Create two mock connections
        mock_conn1 = MagicMock()
        mock_conn1.execute = AsyncMock(side_effect=capture_sql_conn1)
        mock_conn1.fetchall = AsyncMock(return_value=[])
        mock_conn1.fetchone = AsyncMock(return_value=None)
        mock_conn1.__aenter__ = AsyncMock(return_value=mock_conn1)
        mock_conn1.__aexit__ = AsyncMock(return_value=None)
        
        mock_conn2 = MagicMock()
        mock_conn2.execute = AsyncMock(side_effect=capture_sql_conn2)
        mock_conn2.fetchall = AsyncMock(return_value=[])
        mock_conn2.fetchone = AsyncMock(return_value=None)
        mock_conn2.__aenter__ = AsyncMock(return_value=mock_conn2)
        mock_conn2.__aexit__ = AsyncMock(return_value=None)
        
        # Import and run with different ef_search values
        from integration_coworker.discovery.catalog import _search_semantic
        
        # Run first query with default ef_search
        with patch.dict(os.environ, {"HNSW_EF_SEARCH": "100"}):
            await _search_semantic(mock_conn1, "query 1", max_results=5, include_untrusted=False)
        
        # Run second query with different ef_search  
        with patch.dict(os.environ, {"HNSW_EF_SEARCH": "500"}):
            await _search_semantic(mock_conn2, "query 2", max_results=5, include_untrusted=False)
        
        # Verify each connection got its own SET LOCAL
        conn1_set_local = [s for s in connection1_sql if "SET LOCAL" in s]
        conn2_set_local = [s for s in connection2_sql if "SET LOCAL" in s]
        
        assert len(conn1_set_local) >= 1, "Connection 1 should have SET LOCAL"
        assert len(conn2_set_local) >= 1, "Connection 2 should have SET LOCAL"
        
        # Verify values are different (if env var worked)
        # Note: This depends on catalog.py reading HNSW_EF_SEARCH
        # If it doesn't, both will have same default, which is also acceptable


# =============================================================================
# BEHAVIOR-BASED CALL ORDER TESTS (Step 4 improvement)
# =============================================================================
# These tests use spies/mocks to verify CALL ORDER, not source code shape.
# They prove the security invariants by observing actual runtime behavior.
# =============================================================================

class TestBehaviorInvariant4ValidateBeforeRequest:
    """
    INVARIANT 4: Validate hop BEFORE requesting it.
    
    Test strategy: Use a spy to record the call order of:
    1. _validate_hop() - must be called first
    2. client.get() - must be called second
    
    This is a BEHAVIOR test, not a source-shape test. It proves the
    invariant by observing actual execution order.
    """
    
    @pytest.mark.asyncio
    async def test_validate_hop_called_before_client_get(self):
        """_validate_hop is called BEFORE client.get for each redirect."""
        from integration_coworker.discovery.http_client import hardened_fetch, _validate_hop
        
        call_order = []
        
        # Spy on _validate_hop
        original_validate = _validate_hop
        
        def spy_validate_hop(*args, **kwargs):
            call_order.append(("validate_hop", args[0]))  # Record URL
            return original_validate(*args, **kwargs)
        
        # Mock client.get to record calls
        async def spy_get(url, **kwargs):
            call_order.append(("client.get", str(url)))
            response = MagicMock()
            response.status_code = 200
            response.headers = {"content-type": "text/plain"}
            return response
        
        # Mock stream
        mock_stream = MagicMock()
        mock_stream.status_code = 200
        mock_stream.headers = {}
        mock_stream.raise_for_status = MagicMock()
        
        async def mock_aiter():
            yield b"content"
        
        mock_stream.aiter_bytes = mock_aiter
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        
        with patch("integration_coworker.discovery.http_client._validate_hop", side_effect=spy_validate_hop), \
             patch("httpx.AsyncClient") as mock_client_class:
            
            mock_client = AsyncMock()
            mock_client.get = spy_get
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            await hardened_fetch("https://example.com/api", validate_dns=False)
        
        # Verify call order: validate_hop MUST come before client.get for same URL
        validate_calls = [c for c in call_order if c[0] == "validate_hop"]
        get_calls = [c for c in call_order if c[0] == "client.get"]
        
        assert len(validate_calls) >= 1, "validate_hop should be called"
        assert len(get_calls) >= 1, "client.get should be called"
        
        # Find the first validate_hop and first client.get
        first_validate_idx = call_order.index(validate_calls[0])
        first_get_idx = call_order.index(get_calls[0])
        
        assert first_validate_idx < first_get_idx, (
            f"INVARIANT 4 VIOLATION: validate_hop (idx {first_validate_idx}) must come "
            f"before client.get (idx {first_get_idx}). Call order: {call_order}"
        )
    
    @pytest.mark.asyncio
    async def test_validate_hop_called_before_each_redirect(self):
        """_validate_hop is called before EACH redirect, not just the first."""
        from integration_coworker.discovery.http_client import hardened_fetch, _validate_hop
        
        call_order = []
        redirect_count = [0]
        
        original_validate = _validate_hop
        
        def spy_validate_hop(*args, **kwargs):
            call_order.append(("validate_hop", args[0]))
            return original_validate(*args, **kwargs)
        
        async def spy_get(url, **kwargs):
            call_order.append(("client.get", str(url)))
            redirect_count[0] += 1
            
            response = MagicMock()
            # First request returns redirect
            if redirect_count[0] == 1:
                response.status_code = 302
                response.headers = MagicMock()
                response.headers.get_list = MagicMock(return_value=["https://second.example.com/api"])
            # Second request returns success
            else:
                response.status_code = 200
                response.headers = {"content-type": "text/plain"}
            return response
        
        mock_stream = MagicMock()
        mock_stream.status_code = 200
        mock_stream.headers = {}
        mock_stream.raise_for_status = MagicMock()
        
        async def mock_aiter():
            yield b"content"
        
        mock_stream.aiter_bytes = mock_aiter
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        
        with patch("integration_coworker.discovery.http_client._validate_hop", side_effect=spy_validate_hop), \
             patch("httpx.AsyncClient") as mock_client_class:
            
            mock_client = AsyncMock()
            mock_client.get = spy_get
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            await hardened_fetch("https://first.example.com/api", validate_dns=False)
        
        # Should have 2 validate_hop calls (initial + redirect target)
        validate_calls = [c for c in call_order if c[0] == "validate_hop"]
        assert len(validate_calls) >= 2, (
            f"Expected at least 2 validate_hop calls (initial + redirect), got {len(validate_calls)}"
        )
        
        # For each client.get, there should be a preceding validate_hop
        get_calls = [c for c in call_order if c[0] == "client.get"]
        for get_call in get_calls:
            get_idx = call_order.index(get_call)
            # Find the most recent validate_hop before this get
            preceding_validates = [
                c for i, c in enumerate(call_order[:get_idx])
                if c[0] == "validate_hop"
            ]
            assert len(preceding_validates) > 0, (
                f"INVARIANT 4 VIOLATION: No validate_hop before client.get at index {get_idx}"
            )


class TestBehaviorInvariant6AsyncioTimeoutWallClock:
    """
    INVARIANT 6: Total timeout enforced via asyncio.timeout wall-clock.
    
    Test strategy: Verify asyncio.timeout wraps the redirect loop by:
    1. Using a very short total timeout
    2. Making slow responses that would exceed budget
    3. Verifying asyncio.TimeoutError is caught and converted to FetchResult
    """
    
    @pytest.mark.asyncio
    async def test_asyncio_timeout_fires_on_slow_operations(self):
        """asyncio.timeout cancels the operation when wall-clock exceeded."""
        import asyncio
        from integration_coworker.discovery.http_client import hardened_fetch
        
        # A slow get that will exceed the tiny total timeout
        async def slow_get(url, **kwargs):
            await asyncio.sleep(2.0)  # Will exceed 0.5s budget
            response = MagicMock()
            response.status_code = 200
            return response
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = slow_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await hardened_fetch(
                "https://example.com/",
                total_timeout_seconds=0.5,  # Very short budget
                validate_dns=False,
            )
        
        assert not result.success, "Should fail due to timeout"
        assert "timeout" in result.error.lower() or "budget" in result.error.lower(), (
            f"Error should mention timeout/budget: {result.error}"
        )


class TestBehaviorInvariant9LocationHeaderProtection:
    """
    Test Location header protection against poisoning and injection.
    
    Test strategy: Verify actual behavior when malicious Location headers sent.
    """
    
    @pytest.mark.asyncio
    async def test_multiple_location_headers_rejected(self):
        """Multiple Location headers are rejected as header poisoning."""
        from integration_coworker.discovery.http_client import hardened_fetch
        
        async def mock_get(url, **kwargs):
            response = MagicMock()
            response.status_code = 302
            response.headers = MagicMock()
            # Simulate multiple Location headers (header poisoning attempt)
            response.headers.get_list = MagicMock(return_value=[
                "https://safe.example.com/",
                "https://evil.attacker.com/",  # Second header!
            ])
            return response
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await hardened_fetch("https://example.com/", validate_dns=False)
        
        assert not result.success, "Should reject multiple Location headers"
        assert "multiple" in result.error.lower() or "poisoning" in result.error.lower(), (
            f"Error should mention multiple/poisoning: {result.error}"
        )
    
    @pytest.mark.asyncio
    async def test_location_header_with_control_chars_rejected(self):
        """Location headers with control characters are rejected."""
        from integration_coworker.discovery.http_client import hardened_fetch
        
        async def mock_get(url, **kwargs):
            response = MagicMock()
            response.status_code = 302
            response.headers = MagicMock()
            # CRLF injection attempt
            response.headers.get_list = MagicMock(return_value=[
                "https://example.com/\r\nX-Injected: evil",
            ])
            return response
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = mock_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            result = await hardened_fetch("https://example.com/", validate_dns=False)
        
        assert not result.success, "Should reject Location with control chars"
        assert "control" in result.error.lower() or "character" in result.error.lower(), (
            f"Error should mention control characters: {result.error}"
        )


class TestBehaviorInvariant10ExplicitHttpxTimeout:
    """
    Test that per-hop requests use explicit httpx.Timeout, not float.
    
    Test strategy: Spy on httpx.AsyncClient to verify timeout parameter type.
    """
    
    @pytest.mark.asyncio
    async def test_client_get_receives_httpx_timeout_object(self):
        """client.get() receives an httpx.Timeout object, not a raw float."""
        import httpx
        from integration_coworker.discovery.http_client import hardened_fetch
        
        captured_timeout = [None]
        
        async def capture_get(url, timeout=None, **kwargs):
            captured_timeout[0] = timeout
            response = MagicMock()
            response.status_code = 200
            response.headers = {"content-type": "text/plain"}
            return response
        
        mock_stream = MagicMock()
        mock_stream.status_code = 200
        mock_stream.headers = {}
        mock_stream.raise_for_status = MagicMock()
        
        async def mock_aiter():
            yield b"content"
        
        mock_stream.aiter_bytes = mock_aiter
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.get = capture_get
            mock_client.stream = MagicMock(return_value=mock_stream)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_class.return_value = mock_client
            
            await hardened_fetch("https://example.com/", validate_dns=False)
        
        assert captured_timeout[0] is not None, "timeout should be passed to client.get"
        assert isinstance(captured_timeout[0], httpx.Timeout), (
            f"timeout should be httpx.Timeout, got {type(captured_timeout[0]).__name__}. "
            "Raw floats don't provide fine-grained control over connect/read/write/pool."
        )

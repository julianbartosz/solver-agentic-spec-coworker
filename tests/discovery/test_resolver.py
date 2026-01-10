"""
E2E tests for spec auto-discovery flow.

Tests the full discovery pipeline from task description to spec URL.
Live HTTP tests are guarded with @pytest.mark.integration.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from integration_coworker.discovery import (
    resolve_spec_from_task,
    DiscoveryResult,
)


class TestResolveSpecFromTaskMocked:
    """Tests with mocked HTTP calls."""
    
    @pytest.mark.asyncio
    async def test_resolve_with_explicit_provider(self):
        """Test resolving when provider is explicitly mentioned."""
        # Mock APIs.guru response
        mock_directory = {
            "stripe.com": {
                "preferred": "v1",
                "versions": {
                    "v1": {
                        "openapiUrl": "https://example.com/stripe.yaml",
                        "info": {
                            "title": "Stripe API",
                            "description": "Payment processing",
                        }
                    }
                }
            }
        }
        
        # Mock validation response
        mock_validation = MagicMock()
        mock_validation.valid = True
        mock_validation.title = "Stripe API"
        mock_validation.spec_format = MagicMock()
        mock_validation.spec_format.value = "openapi_3.0"
        
        with patch("integration_coworker.discovery.resolver.search_apis_guru") as mock_search:
            with patch("integration_coworker.discovery.resolver.validate_spec_url") as mock_validate:
                # Setup mocks
                from integration_coworker.discovery.apis_guru import SpecCandidate
                mock_search.return_value = [
                    SpecCandidate(
                        provider="stripe.com",
                        api_name="Stripe API",
                        spec_url="https://example.com/stripe.yaml",
                        score=0.9,
                    )
                ]
                mock_validate.return_value = mock_validation
                
                result = await resolve_spec_from_task("Process payment with Stripe")
        
        assert result.success is True
        assert result.spec_url == "https://example.com/stripe.yaml"
        assert result.provider == "stripe.com"
        assert result.confidence > 0.5
    
    @pytest.mark.asyncio
    async def test_resolve_no_matches(self):
        """Test resolving when no APIs match."""
        with patch("integration_coworker.discovery.resolver.search_apis_guru") as mock_search:
            mock_search.return_value = []
            
            result = await resolve_spec_from_task("Use nonexistent API xyz")
        
        assert result.success is False
        assert result.error is not None
        assert "No matching" in result.error or "not found" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_resolve_validation_failure(self):
        """Test handling when spec validation fails."""
        mock_validation = MagicMock()
        mock_validation.valid = False
        mock_validation.error = "Invalid spec structure"
        
        with patch("integration_coworker.discovery.resolver.search_apis_guru") as mock_search:
            with patch("integration_coworker.discovery.resolver.validate_spec_url") as mock_validate:
                from integration_coworker.discovery.apis_guru import SpecCandidate
                mock_search.return_value = [
                    SpecCandidate(
                        provider="test.com",
                        api_name="Test API",
                        spec_url="https://example.com/invalid.yaml",
                        score=0.8,
                    )
                ]
                mock_validate.return_value = mock_validation
                
                result = await resolve_spec_from_task("Use Test API")
        
        assert result.success is False
        assert "validation" in result.error.lower() or "failed" in result.error.lower()
    
    @pytest.mark.asyncio
    async def test_result_contains_candidates(self):
        """Test that result includes candidate list (with HITL callback for low confidence)."""
        mock_validation = MagicMock()
        mock_validation.valid = True
        mock_validation.title = "Test API"
        mock_validation.spec_format = MagicMock()
        mock_validation.spec_format.value = "openapi_3.0"
        
        with patch("integration_coworker.discovery.resolver.search_apis_guru") as mock_search:
            with patch("integration_coworker.discovery.resolver.validate_spec_url") as mock_validate:
                from integration_coworker.discovery.apis_guru import SpecCandidate
                mock_search.return_value = [
                    SpecCandidate(provider="test1.com", api_name="Test 1", spec_url="https://a.com/spec.yaml", score=0.9),
                    SpecCandidate(provider="test2.com", api_name="Test 2", spec_url="https://b.com/spec.yaml", score=0.7),
                ]
                mock_validate.return_value = mock_validation
                
                # Provide HITL callback that selects first candidate
                # This is needed because "Use Test API" has low intent confidence
                # which triggers HITL when confidence < threshold
                hitl_callback = lambda req: 0
                
                result = await resolve_spec_from_task("Use Test API", hitl_callback=hitl_callback)
        
        assert result.success is True
        assert len(result.candidates) >= 1
    
    @pytest.mark.asyncio
    async def test_empty_task_fails(self):
        """Test that empty task returns low confidence."""
        result = await resolve_spec_from_task("")
        
        assert result.success is False
        assert result.confidence < 0.2
    
    @pytest.mark.asyncio
    async def test_hitl_triggered_on_low_confidence(self):
        """Test that HITL is triggered when confidence is below threshold and require_confirmation=True."""
        with patch("integration_coworker.discovery.resolver.search_apis_guru") as mock_search:
            from integration_coworker.discovery.apis_guru import SpecCandidate
            mock_search.return_value = [
                SpecCandidate(provider="test1.com", api_name="Test 1", spec_url="https://a.com/spec.yaml", score=0.9),
                SpecCandidate(provider="test2.com", api_name="Test 2", spec_url="https://b.com/spec.yaml", score=0.7),
            ]
            
            # Low confidence task with require_confirmation=True (explicit HITL request)
            result = await resolve_spec_from_task("Use some API", require_confirmation=True)
        
        # Should require HITL since confidence is low, require_confirmation=True, and no callback provided
        assert result.requires_hitl is True
        assert result.hitl_request is not None
        assert len(result.hitl_request.candidates) >= 1
        assert "confidence" in result.hitl_request.reason.lower()
    
    @pytest.mark.asyncio
    async def test_hitl_deterministic_ordering(self):
        """Test that HITL candidates are ordered deterministically."""
        with patch("integration_coworker.discovery.resolver.search_apis_guru") as mock_search:
            from integration_coworker.discovery.apis_guru import SpecCandidate
            # Provide candidates in non-deterministic order
            mock_search.return_value = [
                SpecCandidate(provider="zebra.com", api_name="Zebra API", spec_url="https://z.com/spec.yaml", score=0.8),
                SpecCandidate(provider="alpha.com", api_name="Alpha API", spec_url="https://a.com/spec.yaml", score=0.8),
            ]
            
            # With require_confirmation=True to get HITL result
            result = await resolve_spec_from_task("Use some API", require_confirmation=True)
        
        # With same score, should be sorted alphabetically by provider
        assert result.requires_hitl is True
        candidates = result.hitl_request.candidates
        assert candidates[0].provider == "alpha.com"  # Alphabetically first
        assert candidates[1].provider == "zebra.com"


@pytest.mark.integration
@pytest.mark.asyncio
class TestResolveSpecFromTaskLive:
    """Live integration tests (hit real APIs.guru)."""
    
    async def test_resolve_stripe(self):
        """Test resolving Stripe payment task."""
        result = await resolve_spec_from_task("Process payment with Stripe")
        
        # Stripe should be in APIs.guru
        if result.success:
            assert "stripe" in result.provider.lower()
            assert result.spec_url is not None
            assert result.confidence > 0.5
        else:
            # APIs.guru might not have Stripe - skip
            pytest.skip("Stripe not found in APIs.guru")
    
    async def test_resolve_github(self):
        """Test resolving GitHub API task."""
        result = await resolve_spec_from_task("Create a GitHub issue")
        
        if result.success:
            assert "github" in result.provider.lower()
            assert result.spec_url is not None
        else:
            pytest.skip("GitHub not found in APIs.guru")
    
    async def test_resolve_sms(self):
        """Test resolving SMS task (implicit provider)."""
        result = await resolve_spec_from_task("Send SMS notification to customer")
        
        # Should infer Twilio or similar
        if result.success:
            # Any SMS provider is acceptable
            assert result.spec_url is not None
            assert result.confidence > 0.3

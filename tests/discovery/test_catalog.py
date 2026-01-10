"""
Tests for Local Spec Catalog (Slice 2)

Tests the local-first discovery catalog without requiring a database connection.
Uses mocks to verify search behavior and fallback logic.
"""

import pytest
from contextlib import contextmanager
from unittest.mock import patch, MagicMock, AsyncMock
from dataclasses import dataclass

from integration_coworker.discovery.catalog import (
    CatalogMatch,
    _build_search_terms,
    _apply_quality_boost,
    CURATED_BOOST,
    PREMIUM_BOOST,
)


class TestCatalogMatch:
    """Tests for CatalogMatch dataclass."""
    
    def test_to_spec_candidate(self):
        """CatalogMatch converts to SpecCandidate correctly."""
        match = CatalogMatch(
            provider_id=1,
            provider_key="stripe.com:stripe",
            domain="stripe.com",
            display_name="Stripe API",
            spec_url="https://example.com/stripe.yaml",
            spec_format="openapi_3.0",
            similarity=0.95,
            score=0.95,
            is_curated=True,
            quality_tier="premium",
        )
        
        candidate = match.to_spec_candidate()
        
        assert candidate.provider == "stripe.com"
        assert candidate.api_name == "Stripe API"
        assert candidate.spec_url == "https://example.com/stripe.yaml"
        assert candidate.spec_format == "openapi_3.0"
        assert candidate.score == 0.95


class TestSearchTerms:
    """Tests for search term building."""
    
    def test_build_with_provider_hint(self):
        """Provider hint is included in search terms."""
        terms = _build_search_terms(
            query="process payment",
            provider_hint="stripe",
            keywords=None,
        )
        assert "stripe" in terms
    
    def test_build_with_keywords(self):
        """Keywords are included in search terms."""
        terms = _build_search_terms(
            query="process payment",
            provider_hint=None,
            keywords=["payment", "checkout"],
        )
        assert "payment" in terms
        assert "checkout" in terms
    
    def test_build_extracts_query_words(self):
        """Meaningful words from query are extracted."""
        terms = _build_search_terms(
            query="integrate with stripe for payment processing",
            provider_hint=None,
            keywords=None,
        )
        # Should extract meaningful words, not stop words
        assert "stripe" in terms
        assert "payment" in terms
        # Stop words should be excluded
        assert " with " not in terms
    
    def test_build_limits_keywords(self):
        """Keywords are limited to prevent query explosion."""
        many_keywords = [f"keyword{i}" for i in range(20)]
        terms = _build_search_terms(
            query="test",
            provider_hint=None,
            keywords=many_keywords,
        )
        # Should not include all 20 keywords
        keyword_count = sum(1 for k in many_keywords if k in terms)
        assert keyword_count <= 5
    
    def test_build_deduplicates(self):
        """Duplicate terms are removed."""
        terms = _build_search_terms(
            query="stripe stripe stripe",
            provider_hint="stripe",
            keywords=["stripe"],
        )
        # Should only have one "stripe" (deduped by set)
        assert terms.count("stripe") == 1


class TestQualityBoost:
    """Tests for quality-based score boosting."""
    
    def test_curated_boost(self):
        """Curated entries get a score boost."""
        match = CatalogMatch(
            provider_id=1,
            provider_key="test:test",
            domain="test.com",
            display_name="Test",
            spec_url="https://test.com/spec",
            spec_format="openapi_3.0",
            similarity=0.5,
            score=0.5,
            is_curated=True,
            quality_tier="standard",
        )
        
        boosted = _apply_quality_boost(match)
        assert boosted == min(1.0, 0.5 * CURATED_BOOST)
    
    def test_premium_boost(self):
        """Premium tier entries get a score boost."""
        match = CatalogMatch(
            provider_id=1,
            provider_key="test:test",
            domain="test.com",
            display_name="Test",
            spec_url="https://test.com/spec",
            spec_format="openapi_3.0",
            similarity=0.5,
            score=0.5,
            is_curated=False,
            quality_tier="premium",
        )
        
        boosted = _apply_quality_boost(match)
        assert boosted == min(1.0, 0.5 * PREMIUM_BOOST)
    
    def test_standard_no_boost(self):
        """Standard tier entries don't get boosted."""
        match = CatalogMatch(
            provider_id=1,
            provider_key="test:test",
            domain="test.com",
            display_name="Test",
            spec_url="https://test.com/spec",
            spec_format="openapi_3.0",
            similarity=0.5,
            score=0.5,
            is_curated=False,
            quality_tier="standard",
        )
        
        boosted = _apply_quality_boost(match)
        assert boosted == 0.5
    
    def test_boost_capped_at_one(self):
        """Boosted score is capped at 1.0."""
        match = CatalogMatch(
            provider_id=1,
            provider_key="test:test",
            domain="test.com",
            display_name="Test",
            spec_url="https://test.com/spec",
            spec_format="openapi_3.0",
            similarity=0.9,  # High base score
            score=0.9,
            is_curated=True,
            quality_tier="premium",
        )
        
        boosted = _apply_quality_boost(match)
        assert boosted == 1.0


class TestCatalogAvailability:
    """Tests for catalog availability checks."""
    
    def test_catalog_unavailable_no_connection(self):
        """is_catalog_available returns False without DB."""
        @contextmanager
        def mock_no_conn():
            yield None
            
        with patch(
            "integration_coworker.discovery.catalog._get_catalog_connection",
            mock_no_conn
        ):
            from integration_coworker.discovery.catalog import is_catalog_available
            assert is_catalog_available() is False
    
    def test_catalog_unavailable_empty(self):
        """is_catalog_available returns False with empty catalog."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)  # Empty catalog
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        
        @contextmanager
        def mock_ctx():
            yield mock_conn
        
        with patch(
            "integration_coworker.discovery.catalog._get_catalog_connection",
            mock_ctx
        ):
            from integration_coworker.discovery.catalog import is_catalog_available
            assert is_catalog_available() is False


class TestSearchLocalCatalog:
    """Tests for search_local_catalog function."""
    
    @pytest.mark.asyncio
    async def test_returns_empty_without_db(self):
        """Returns empty list when database is unavailable."""
        @contextmanager
        def mock_no_conn():
            yield None
            
        with patch(
            "integration_coworker.discovery.catalog._get_catalog_connection",
            mock_no_conn
        ):
            from integration_coworker.discovery.catalog import search_local_catalog
            results = await search_local_catalog("stripe payment")
            assert results == []
    
    @pytest.mark.asyncio
    async def test_search_with_mock_db(self):
        """Search returns results from mocked database."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        
        # Mock exact match query
        mock_cursor.fetchall.side_effect = [
            # First call: exact match
            [(1, "stripe.com:stripe", "stripe.com", "Stripe API", True, "premium",
              "https://example.com/stripe.yaml", "openapi_3.0")],
            # Second call: alias match
            [],
            # Third call: full-text search
            [],
        ]
        
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        
        @contextmanager
        def mock_ctx():
            yield mock_conn
        
        with patch(
            "integration_coworker.discovery.catalog._get_catalog_connection",
            mock_ctx
        ), patch(
            "integration_coworker.discovery.catalog._search_semantic",
            new_callable=AsyncMock,
            return_value=[]
        ):
            from integration_coworker.discovery.catalog import search_local_catalog
            results = await search_local_catalog(
                query="stripe payment",
                provider_hint="stripe",
            )
            
            assert len(results) >= 1
            assert results[0].domain == "stripe.com"


class TestGetProviderByDomain:
    """Tests for direct domain lookup."""
    
    def test_returns_none_without_db(self):
        """Returns None when database is unavailable."""
        @contextmanager
        def mock_no_conn():
            yield None
            
        with patch(
            "integration_coworker.discovery.catalog._get_catalog_connection",
            mock_no_conn
        ):
            from integration_coworker.discovery.catalog import get_provider_by_domain
            result = get_provider_by_domain("stripe.com")
            assert result is None
    
    def test_returns_match_with_mock_db(self):
        """Returns match from mocked database."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            1, "stripe.com:stripe", "stripe.com", "Stripe API",
            True, "premium", "https://example.com/stripe.yaml", "openapi_3.0"
        )
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        
        @contextmanager
        def mock_ctx():
            yield mock_conn
        
        with patch(
            "integration_coworker.discovery.catalog._get_catalog_connection",
            mock_ctx
        ):
            from integration_coworker.discovery.catalog import get_provider_by_domain
            result = get_provider_by_domain("stripe.com")
            
            assert result is not None
            assert result.domain == "stripe.com"
            assert result.display_name == "Stripe API"

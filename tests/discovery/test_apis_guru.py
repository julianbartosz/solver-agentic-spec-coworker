"""
Unit tests for discovery/apis_guru.py - APIs.guru Client

Tests the ranking logic using fixture data (no HTTP calls).
Live HTTP tests are guarded with @pytest.mark.integration.
"""

import pytest
from typing import Dict, Any

from integration_coworker.discovery.apis_guru import (
    SpecCandidate,
    rank_candidates,
    _normalize_provider_name,
    _calculate_match_score,
)


# Sample fixture data mimicking APIs.guru response structure
SAMPLE_DIRECTORY: Dict[str, Any] = {
    "stripe.com": {
        "preferred": "v1",
        "versions": {
            "v1": {
                "openapiUrl": "https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.yaml",
                "info": {
                    "title": "Stripe API",
                    "description": "The Stripe API provides a complete payments platform.",
                }
            }
        }
    },
    "openai.com": {
        "preferred": "v1",
        "versions": {
            "v1": {
                "openapiUrl": "https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml",
                "info": {
                    "title": "OpenAI API",
                    "description": "APIs for GPT, DALL-E, Whisper, and embeddings.",
                }
            }
        }
    },
    "twilio.com": {
        "preferred": "2010-04-01",
        "versions": {
            "2010-04-01": {
                "openapiUrl": "https://raw.githubusercontent.com/twilio/twilio-oai/main/spec/yaml/twilio_api_v2010.yaml",
                "info": {
                    "title": "Twilio API",
                    "description": "SMS, voice, and messaging APIs.",
                }
            }
        }
    },
    "sendgrid.com": {
        "preferred": "v3",
        "versions": {
            "v3": {
                "openapiUrl": "https://raw.githubusercontent.com/sendgrid/sendgrid-oai/main/oai.yaml",
                "info": {
                    "title": "SendGrid API",
                    "description": "Email delivery and marketing APIs.",
                }
            }
        }
    },
    "github.com": {
        "preferred": "v3",
        "versions": {
            "v3": {
                "openapiUrl": "https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.yaml",
                "info": {
                    "title": "GitHub REST API",
                    "description": "GitHub REST API for repos, issues, PRs, and more.",
                }
            }
        }
    },
}


class TestNormalizeProviderName:
    """Tests for provider name normalization."""
    
    @pytest.mark.parametrize("name,expected", [
        ("stripe.com", "stripe"),
        ("openai.com", "openai"),
        ("api.stripe.com", "stripe"),
        ("www.github.com", "github"),
        ("STRIPE.COM", "stripe"),
        ("apis.guru", "guru"),
    ])
    def test_normalization(self, name, expected):
        """Test provider name normalization."""
        assert _normalize_provider_name(name) == expected


class TestRankCandidates:
    """Tests for candidate ranking (pure function, no HTTP)."""
    
    def test_exact_provider_match(self):
        """Exact provider name should rank highest."""
        candidates = rank_candidates(
            SAMPLE_DIRECTORY,
            provider_hint="stripe",
            keywords=[],
            max_results=5,
        )
        
        assert len(candidates) > 0
        assert candidates[0].provider == "stripe.com"
        assert candidates[0].score > 0.5
    
    def test_keyword_matching(self):
        """Keywords should influence ranking."""
        candidates = rank_candidates(
            SAMPLE_DIRECTORY,
            provider_hint=None,
            keywords=["payment", "checkout"],
            max_results=5,
        )
        
        # Stripe should rank high due to "payments" in description
        stripe_candidates = [c for c in candidates if c.provider == "stripe.com"]
        assert len(stripe_candidates) > 0
    
    def test_combined_provider_and_keywords(self):
        """Provider hint + keywords should boost confidence."""
        candidates = rank_candidates(
            SAMPLE_DIRECTORY,
            provider_hint="openai",
            keywords=["gpt", "embeddings"],
            max_results=5,
        )
        
        assert len(candidates) > 0
        assert candidates[0].provider == "openai.com"
        # Should have high score due to both matches
        assert candidates[0].score > 0.6
    
    def test_no_matches_returns_empty(self):
        """No matching criteria should return few or no candidates."""
        candidates = rank_candidates(
            SAMPLE_DIRECTORY,
            provider_hint="nonexistent_provider_xyz",
            keywords=["completely_random_keyword_abc"],
            max_results=5,
        )
        
        # May return some low-score candidates or empty
        # All returned candidates should have low scores
        for c in candidates:
            assert c.score < 0.5
    
    def test_max_results_honored(self):
        """Should return at most max_results candidates."""
        candidates = rank_candidates(
            SAMPLE_DIRECTORY,
            provider_hint=None,
            keywords=["api"],  # Matches many
            max_results=2,
        )
        
        assert len(candidates) <= 2
    
    def test_sorted_by_score_descending(self):
        """Candidates should be sorted by score (highest first)."""
        candidates = rank_candidates(
            SAMPLE_DIRECTORY,
            provider_hint=None,
            keywords=["api", "rest"],
            max_results=10,
        )
        
        if len(candidates) > 1:
            for i in range(len(candidates) - 1):
                assert candidates[i].score >= candidates[i + 1].score
    
    def test_spec_url_populated(self):
        """All returned candidates should have spec_url."""
        candidates = rank_candidates(
            SAMPLE_DIRECTORY,
            provider_hint="stripe",
            keywords=[],
            max_results=5,
        )
        
        for c in candidates:
            assert c.spec_url is not None
            assert c.spec_url.startswith("http")
    
    def test_candidate_fields_populated(self):
        """Candidate should have all expected fields."""
        candidates = rank_candidates(
            SAMPLE_DIRECTORY,
            provider_hint="stripe",
            keywords=[],
            max_results=1,
        )
        
        assert len(candidates) == 1
        c = candidates[0]
        
        assert c.provider == "stripe.com"
        assert c.api_name == "Stripe API"
        assert "stripe" in c.spec_url.lower()
        assert c.spec_format in ("openapi_3", "swagger_2")
        assert len(c.description) > 0
        assert c.score > 0


class TestSpecCandidate:
    """Tests for SpecCandidate dataclass."""
    
    def test_to_dict(self):
        """Test serialization to dict."""
        candidate = SpecCandidate(
            provider="stripe.com",
            api_name="Stripe API",
            spec_url="https://example.com/spec.yaml",
            spec_format="openapi_3",
            description="Payment API",
            score=0.85,
            version="v1",
        )
        
        d = candidate.to_dict()
        
        assert d["provider"] == "stripe.com"
        assert d["api_name"] == "Stripe API"
        assert d["spec_url"] == "https://example.com/spec.yaml"
        assert d["score"] == 0.85


@pytest.mark.integration
@pytest.mark.asyncio
class TestApisGuruLive:
    """Live integration tests that hit APIs.guru (guarded)."""
    
    async def test_fetch_directory(self):
        """Test fetching the live APIs.guru directory."""
        from integration_coworker.discovery.apis_guru import fetch_apis_guru_directory
        
        directory = await fetch_apis_guru_directory(force_refresh=True)
        
        # Should have many entries
        assert len(directory) > 100
        
        # Should have known providers
        assert "stripe.com" in directory
        assert "github.com" in directory
    
    async def test_search_stripe(self):
        """Test searching for Stripe."""
        from integration_coworker.discovery.apis_guru import search_apis_guru
        
        candidates = await search_apis_guru(
            provider_hint="stripe",
            keywords=["payment"],
            max_results=5,
        )
        
        assert len(candidates) > 0
        assert candidates[0].provider == "stripe.com"
        assert candidates[0].spec_url is not None
    
    async def test_search_openai(self):
        """Test searching for OpenAI."""
        from integration_coworker.discovery.apis_guru import search_apis_guru
        
        candidates = await search_apis_guru(
            provider_hint="openai",
            keywords=["gpt", "ai"],
            max_results=5,
        )
        
        # OpenAI may or may not be in APIs.guru
        # If found, should rank high
        if candidates:
            openai_candidates = [c for c in candidates if "openai" in c.provider.lower()]
            if openai_candidates:
                assert openai_candidates[0].score > 0.5

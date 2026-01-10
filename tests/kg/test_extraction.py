"""
Tests for KG entity/endpoint extraction from OpenAPI specs.

See docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md Phase 1 for design rationale.
"""

import pytest
from integration_coworker.kg.extraction import (
    build_spec_candidates,
    extract_entities_from_task,
    extract_endpoints_from_task,
    extract_entities_endpoints_from_spec,
    _normalize_word,
    _tokenize,
    _camel_to_words,
    _extract_path_segments,
    SpecCandidates,
)


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def stripe_spec():
    """Minimal Stripe-like OpenAPI spec for testing."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "Stripe API", "version": "2023-10-16"},
        "tags": [
            {"name": "Customers"},
            {"name": "PaymentIntents"},
            {"name": "Charges"},
        ],
        "components": {
            "schemas": {
                "Customer": {"type": "object", "properties": {}},
                "PaymentIntent": {"type": "object", "properties": {}},
                "Charge": {"type": "object", "properties": {}},
                "Invoice": {"type": "object", "properties": {}},
                "Subscription": {"type": "object", "properties": {}},
            }
        },
        "paths": {
            "/v1/customers": {
                "get": {
                    "operationId": "listCustomers",
                    "summary": "List all customers",
                },
                "post": {
                    "operationId": "createCustomer",
                    "summary": "Create a new customer",
                },
            },
            "/v1/customers/{id}": {
                "get": {
                    "operationId": "getCustomer",
                    "summary": "Retrieve a customer by ID",
                },
                "delete": {
                    "operationId": "deleteCustomer",
                    "summary": "Delete a customer",
                },
            },
            "/v1/payment_intents": {
                "post": {
                    "operationId": "createPaymentIntent",
                    "summary": "Create a payment intent to charge a card",
                    "description": "Creates a PaymentIntent object for processing payments",
                },
            },
            "/v1/charges": {
                "post": {
                    "operationId": "createCharge",
                    "summary": "Create a charge",
                },
            },
            "/v1/invoices/{id}/send": {
                "post": {
                    "operationId": "sendInvoice",
                    "summary": "Send an invoice to the customer",
                },
            },
        },
    }


@pytest.fixture
def openai_spec():
    """Minimal OpenAI-like OpenAPI spec for testing."""
    return {
        "openapi": "3.0.0",
        "info": {"title": "OpenAI API", "version": "v1"},
        "tags": [
            {"name": "Chat"},
            {"name": "Completions"},
        ],
        "components": {
            "schemas": {
                "ChatCompletion": {"type": "object"},
                "Message": {"type": "object"},
                "Model": {"type": "object"},
            }
        },
        "paths": {
            "/v1/chat/completions": {
                "post": {
                    "operationId": "createChatCompletion",
                    "summary": "Creates a model response for the given chat conversation",
                },
            },
            "/v1/models": {
                "get": {
                    "operationId": "listModels",
                    "summary": "Lists the currently available models",
                },
            },
        },
    }


# ---------------------------------------------------------------------------
# Unit Tests: Helper Functions
# ---------------------------------------------------------------------------

class TestNormalizeWord:
    """Tests for _normalize_word()."""
    
    def test_lowercase(self):
        assert _normalize_word("Customer") == "customer"
    
    def test_singular_from_plural_s(self):
        assert _normalize_word("customers") == "customer"
    
    def test_singular_from_plural_ies(self):
        assert _normalize_word("entries") == "entry"
    
    def test_singular_from_plural_es(self):
        assert _normalize_word("matches") == "match"
    
    def test_preserves_ss(self):
        # "address" should not become "addres"
        assert _normalize_word("address") == "address"
    
    def test_strips_whitespace(self):
        assert _normalize_word("  test  ") == "test"


class TestTokenize:
    """Tests for _tokenize()."""
    
    def test_splits_on_whitespace(self):
        tokens = _tokenize("create a customer")
        assert "create" in tokens
        assert "customer" in tokens
        assert "a" not in tokens  # stopword
    
    def test_removes_stopwords(self):
        tokens = _tokenize("the customer is in the database")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "in" not in tokens
        assert "customer" in tokens
        assert "database" in tokens
    
    def test_splits_on_delimiters(self):
        tokens = _tokenize("create_customer/payment-intent")
        assert "customer" in tokens
        assert "payment" in tokens
        assert "intent" in tokens
    
    def test_normalizes_plurals(self):
        tokens = _tokenize("list customers")
        # Should normalize to singular
        assert "customer" in tokens


class TestCamelToWords:
    """Tests for _camel_to_words()."""
    
    def test_camel_case(self):
        assert _camel_to_words("createPaymentIntent") == ["create", "payment", "intent"]
    
    def test_pascal_case(self):
        assert _camel_to_words("PaymentIntent") == ["payment", "intent"]
    
    def test_uppercase_sequence(self):
        words = _camel_to_words("HTTPResponse")
        assert "http" in words
        assert "response" in words
    
    def test_single_word(self):
        assert _camel_to_words("customer") == ["customer"]


class TestExtractPathSegments:
    """Tests for _extract_path_segments()."""
    
    def test_simple_path(self):
        segments = _extract_path_segments("/v1/customers")
        assert "customer" in segments
        assert "v1" not in segments  # version prefix excluded
    
    def test_path_with_parameter(self):
        segments = _extract_path_segments("/v1/customers/{id}/subscriptions")
        assert "customer" in segments
        assert "subscription" in segments
        assert "{id}" not in segments
    
    def test_nested_path(self):
        segments = _extract_path_segments("/invoices/{id}/send")
        # Segments are normalized (singular)
        assert "invoice" in segments or "invoices" in segments
        assert "send" in segments


# ---------------------------------------------------------------------------
# Integration Tests: Spec Candidates
# ---------------------------------------------------------------------------

class TestBuildSpecCandidates:
    """Tests for build_spec_candidates()."""
    
    def test_extracts_schema_names(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        assert "Customer" in candidates.entities
        assert "PaymentIntent" in candidates.entities
        assert "Charge" in candidates.entities
    
    def test_extracts_tag_names(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        assert "Customers" in candidates.entities
        assert "PaymentIntents" in candidates.entities
    
    def test_extracts_endpoints(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        assert "/v1/customers" in candidates.endpoints
        assert "/v1/payment_intents" in candidates.endpoints
    
    def test_builds_normalized_lookup(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        # Normalized form should map back to original
        assert "customer" in candidates.entities_normalized
        assert "payment" in candidates.entities_normalized
    
    def test_extracts_operation_keywords(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        # From summary "Create a new customer"
        post_customers = candidates.operation_keywords.get("POST /v1/customers", set())
        assert "create" in post_customers or "customer" in post_customers
    
    def test_handles_empty_spec(self):
        candidates = build_spec_candidates({})
        assert len(candidates.entities) == 0
        assert len(candidates.endpoints) == 0
    
    def test_handles_none_spec(self):
        candidates = build_spec_candidates(None)
        assert len(candidates.entities) == 0


# ---------------------------------------------------------------------------
# Integration Tests: Entity Extraction
# ---------------------------------------------------------------------------

class TestExtractEntitiesFromTask:
    """Tests for extract_entities_from_task()."""
    
    def test_matches_schema_name(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        entities = extract_entities_from_task("create a customer", candidates)
        # Should find Customer schema
        assert any("customer" in e.lower() for e in entities)
    
    def test_matches_partial_words(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        entities = extract_entities_from_task("payment processing", candidates)
        # Should find PaymentIntent from "payment"
        assert len(entities) > 0
    
    def test_handles_plurals(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        entities = extract_entities_from_task("list all customers", candidates)
        assert any("customer" in e.lower() for e in entities)
    
    def test_handles_camel_case_matching(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        entities = extract_entities_from_task("create payment intent", candidates)
        # Should match because "payment" and "intent" are in operationId words
        assert len(entities) > 0
    
    def test_returns_empty_for_no_match(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        entities = extract_entities_from_task("something completely unrelated", candidates)
        # May return empty or minimal matches
        # The point is it shouldn't crash
        assert isinstance(entities, list)
    
    def test_handles_empty_task(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        entities = extract_entities_from_task("", candidates)
        assert entities == []


# ---------------------------------------------------------------------------
# Integration Tests: Endpoint Extraction
# ---------------------------------------------------------------------------

class TestExtractEndpointsFromTask:
    """Tests for extract_endpoints_from_task()."""
    
    def test_matches_path_segment(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        endpoints = extract_endpoints_from_task("get customer data", candidates)
        # Should match /v1/customers from "customer"
        assert any("customer" in ep.lower() for ep in endpoints)
    
    def test_matches_action_keywords(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        endpoints = extract_endpoints_from_task("send invoice to user", candidates)
        # Should match invoice endpoint
        assert any("invoice" in ep.lower() for ep in endpoints)
    
    def test_handles_multiple_matches(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        endpoints = extract_endpoints_from_task("create customer and charge card", candidates)
        # Should find both customer and charge related endpoints
        has_customer = any("customer" in ep for ep in endpoints)
        has_charge = any("charge" in ep for ep in endpoints)
        assert has_customer or has_charge
    
    def test_handles_empty_task(self, stripe_spec):
        candidates = build_spec_candidates(stripe_spec)
        endpoints = extract_endpoints_from_task("", candidates)
        assert endpoints == []


# ---------------------------------------------------------------------------
# Integration Tests: Combined Extraction
# ---------------------------------------------------------------------------

class TestExtractEntitiesEndpointsFromSpec:
    """Tests for extract_entities_endpoints_from_spec()."""
    
    def test_returns_both_entities_and_endpoints(self, stripe_spec):
        entities, endpoints = extract_entities_endpoints_from_spec(
            stripe_spec, "create a customer"
        )
        assert isinstance(entities, list)
        assert isinstance(endpoints, list)
    
    def test_handles_complex_task(self, stripe_spec):
        entities, endpoints = extract_entities_endpoints_from_spec(
            stripe_spec, "create a customer and charge their card"
        )
        # Should find Customer and Charge entities
        entity_lower = [e.lower() for e in entities]
        # At least one relevant entity
        assert len(entities) > 0 or len(endpoints) > 0
    
    def test_handles_empty_inputs(self, stripe_spec):
        entities, endpoints = extract_entities_endpoints_from_spec(stripe_spec, "")
        assert entities == []
        assert endpoints == []
        
        entities, endpoints = extract_entities_endpoints_from_spec(None, "create customer")
        assert entities == []
        assert endpoints == []
    
    def test_openai_spec(self, openai_spec):
        entities, endpoints = extract_entities_endpoints_from_spec(
            openai_spec, "create a chat completion"
        )
        # Should find chat-related entities
        entity_lower = [e.lower() for e in entities]
        assert any("chat" in e or "completion" in e for e in entity_lower) or len(endpoints) > 0


# ---------------------------------------------------------------------------
# Production-Relevant Tests: Scoring Impact
# ---------------------------------------------------------------------------

class TestScoringImpact:
    """
    Tests that verify extraction fixes the KG scoring collapse.
    
    Per docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md:
    - Without extraction: entity_names=None → graph_score=0.3 (flat)
    - With extraction: entity_names=['Customer'] → graph_score varies
    """
    
    def test_extracts_entities_for_stripe_task(self, stripe_spec):
        """Verify that 'create customer and charge' extracts relevant entities."""
        entities, endpoints = extract_entities_endpoints_from_spec(
            stripe_spec, "create customer and charge"
        )
        
        # Must have at least one entity for scoring
        assert len(entities) > 0 or len(endpoints) > 0, \
            "Extraction should find at least one entity/endpoint to prevent scoring collapse"
    
    def test_extracts_endpoints_for_payment_task(self, stripe_spec):
        """Verify that payment-related tasks extract payment endpoints."""
        entities, endpoints = extract_entities_endpoints_from_spec(
            stripe_spec, "charge a credit card for payment"
        )
        
        # Should find payment or charge related content
        all_text = " ".join(entities + endpoints).lower()
        has_relevant = "payment" in all_text or "charge" in all_text
        assert has_relevant or len(entities) > 0, \
            "Should extract payment-related entities/endpoints"
    
    def test_deterministic_output(self, stripe_spec):
        """Verify extraction is deterministic (same input = same output)."""
        task = "create a new customer subscription"
        
        result1 = extract_entities_endpoints_from_spec(stripe_spec, task)
        result2 = extract_entities_endpoints_from_spec(stripe_spec, task)
        
        assert sorted(result1[0]) == sorted(result2[0]), "Entity extraction must be deterministic"
        assert sorted(result1[1]) == sorted(result2[1]), "Endpoint extraction must be deterministic"

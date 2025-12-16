"""
Test provider namespacing in multi-spec scenarios (Fix MULTI-001).

This ensures that endpoints from different providers with the same path
don't collide when stored and looked up.
"""
import pytest

from integration_coworker.graph.nodes.persist_results import get_qualified_endpoint_key


class TestProviderNamespacing:
    """Test qualified endpoint key generation."""
    
    def test_qualified_key_includes_provider(self):
        """Endpoint keys should include provider namespace."""
        key = get_qualified_endpoint_key("POST", "/payments", "createPayment", "stripe")
        
        assert len(key) == 4
        assert key[0] == "stripe"  # provider
        assert key[1] == "POST"    # method
        assert key[2] == "/payments"  # path
        assert key[3] == "createPayment"  # operation_id
    
    def test_provider_is_normalized_to_lowercase(self):
        """Provider codes should be normalized to lowercase."""
        key1 = get_qualified_endpoint_key("GET", "/users", None, "Stripe")
        key2 = get_qualified_endpoint_key("GET", "/users", None, "STRIPE")
        key3 = get_qualified_endpoint_key("GET", "/users", None, "stripe")
        
        # All should produce the same provider namespace
        assert key1[0] == "stripe"
        assert key2[0] == "stripe"
        assert key3[0] == "stripe"
        assert key1 == key2 == key3
    
    def test_method_is_normalized_to_uppercase(self):
        """HTTP methods should be normalized to uppercase."""
        key1 = get_qualified_endpoint_key("post", "/payments", None, "stripe")
        key2 = get_qualified_endpoint_key("POST", "/payments", None, "stripe")
        
        assert key1[1] == "POST"
        assert key2[1] == "POST"
        assert key1 == key2
    
    def test_missing_provider_defaults_to_default(self):
        """Missing provider should default to 'default'."""
        key1 = get_qualified_endpoint_key("GET", "/health", None, None)
        key2 = get_qualified_endpoint_key("GET", "/health", None, "")
        
        assert key1[0] == "default"
        assert key2[0] == "default"
    
    def test_same_path_different_providers_are_distinct(self):
        """Two providers with the same path should have distinct keys."""
        stripe_key = get_qualified_endpoint_key("POST", "/payments", "createPayment", "stripe")
        paypal_key = get_qualified_endpoint_key("POST", "/payments", "createPayment", "paypal")
        
        # Keys should be different
        assert stripe_key != paypal_key
        
        # But paths and operation_ids are the same
        assert stripe_key[2] == paypal_key[2] == "/payments"
        assert stripe_key[3] == paypal_key[3] == "createPayment"
    
    def test_key_uniqueness_in_dict(self):
        """Keys should work correctly as dict keys without collision."""
        endpoint_map = {}
        
        # Add stripe endpoint
        stripe_key = get_qualified_endpoint_key("POST", "/v1/charges", "createCharge", "stripe")
        endpoint_map[stripe_key] = {"id": 1, "provider": "stripe"}
        
        # Add paypal endpoint with same path
        paypal_key = get_qualified_endpoint_key("POST", "/v1/charges", "createCharge", "paypal")
        endpoint_map[paypal_key] = {"id": 2, "provider": "paypal"}
        
        # Both should exist in the map
        assert len(endpoint_map) == 2
        assert endpoint_map[stripe_key]["provider"] == "stripe"
        assert endpoint_map[paypal_key]["provider"] == "paypal"
    
    def test_with_none_operation_id(self):
        """Keys should work when operation_id is None."""
        key1 = get_qualified_endpoint_key("GET", "/users", None, "github")
        key2 = get_qualified_endpoint_key("GET", "/users", None, "gitlab")
        
        # Should still be distinct by provider
        assert key1 != key2
        assert key1[3] is None
        assert key2[3] is None


class TestMultiSpecEndpointResolution:
    """Integration tests for multi-spec endpoint resolution."""
    
    def test_endpoint_lookup_respects_provider(self):
        """Endpoint lookup should filter by provider."""
        # Simulate endpoint_ids_by_key from persist_results
        endpoint_ids_by_key = {
            ("stripe", "POST", "/payments", "createPayment"): 101,
            ("paypal", "POST", "/payments", "createPayment"): 202,
            ("stripe", "GET", "/payments/{id}", "getPayment"): 102,
        }
        
        # Look up stripe payment endpoint
        target_provider = "stripe"
        target_method = "POST"
        target_path = "/payments"
        target_op = "createPayment"
        
        found_id = None
        for key, ep_id in endpoint_ids_by_key.items():
            ep_provider, ep_method, ep_path, ep_op_id = key
            if ep_provider != target_provider.lower():
                continue
            if ep_op_id == target_op:
                found_id = ep_id
                break
        
        # Should find stripe's endpoint, not paypal's
        assert found_id == 101
    
    def test_endpoint_lookup_without_provider_matches_first(self):
        """Without provider filter, should still find an endpoint (backwards compat)."""
        endpoint_ids_by_key = {
            ("stripe", "POST", "/payments", "createPayment"): 101,
            ("paypal", "POST", "/payments", "createPayment"): 202,
        }
        
        # Old-style lookup without provider filtering
        target_method = "POST"
        target_path = "/payments"
        
        found_id = None
        for key, ep_id in endpoint_ids_by_key.items():
            ep_provider, ep_method, ep_path, ep_op_id = key
            if ep_method == target_method and ep_path == target_path:
                found_id = ep_id
                break
        
        # Should find one of them (order not guaranteed, but should find something)
        assert found_id in [101, 202]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

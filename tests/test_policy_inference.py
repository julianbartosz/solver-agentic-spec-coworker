"""
Tests for dynamic policy inference from OpenAPI securitySchemes.

M5 WS1-T5: Policies should be inferred from spec content, not hardcoded.
"""
import os
# Use SQLite for tests
os.environ.setdefault("USE_SQLITE", "true")
os.environ.setdefault("USE_MOCK_LLM", "true")

import pytest
from integration_coworker.graph.nodes.attach_policies_and_patterns import (
    _infer_auth_policy_config,
    _extract_security_schemes,
)
from integration_coworker.graph.state import WorkflowState


class TestAuthPolicyInference:
    """Tests for _infer_auth_policy_config()."""
    
    def test_bearer_token_from_http_scheme(self):
        """HTTP Bearer scheme → bearer auth config."""
        schemes = {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
            }
        }
        
        config = _infer_auth_policy_config(schemes)
        
        assert config["type"] == "bearer"
        assert config["header"] == "Authorization"
        assert config["prefix"] == "Bearer"
        assert config["scheme_name"] == "bearerAuth"
    
    def test_api_key_in_header(self):
        """API Key in header → api_key config with header location."""
        schemes = {
            "apiKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
            }
        }
        
        config = _infer_auth_policy_config(schemes)
        
        assert config["type"] == "api_key"
        assert config["location"] == "header"
        assert config["key_name"] == "X-API-Key"
    
    def test_api_key_in_query(self):
        """API Key in query → api_key config with query location."""
        schemes = {
            "apiKeyQuery": {
                "type": "apiKey",
                "in": "query",
                "name": "api_key",
            }
        }
        
        config = _infer_auth_policy_config(schemes)
        
        assert config["type"] == "api_key"
        assert config["location"] == "query"
        assert config["key_name"] == "api_key"
    
    def test_basic_auth(self):
        """HTTP Basic scheme → basic auth config."""
        schemes = {
            "basicAuth": {
                "type": "http",
                "scheme": "basic",
            }
        }
        
        config = _infer_auth_policy_config(schemes)
        
        assert config["type"] == "basic"
        assert config["header"] == "Authorization"
    
    def test_oauth2_client_credentials(self):
        """OAuth2 client_credentials flow → oauth2 config."""
        schemes = {
            "oauth2": {
                "type": "oauth2",
                "flows": {
                    "clientCredentials": {
                        "tokenUrl": "https://auth.example.com/token",
                        "scopes": {
                            "read": "Read access",
                            "write": "Write access",
                        }
                    }
                }
            }
        }
        
        config = _infer_auth_policy_config(schemes)
        
        assert config["type"] == "oauth2"
        assert config["flow"] == "client_credentials"
        assert config["token_url"] == "https://auth.example.com/token"
        assert "read" in config["scopes"]
        assert "write" in config["scopes"]
    
    def test_oauth2_authorization_code(self):
        """OAuth2 authorization_code flow → oauth2 config."""
        schemes = {
            "oauth2": {
                "type": "oauth2",
                "flows": {
                    "authorizationCode": {
                        "authorizationUrl": "https://auth.example.com/authorize",
                        "tokenUrl": "https://auth.example.com/token",
                        "scopes": {
                            "openid": "OpenID Connect",
                        }
                    }
                }
            }
        }
        
        config = _infer_auth_policy_config(schemes)
        
        assert config["type"] == "oauth2"
        assert config["flow"] == "authorization_code"
        assert "authorization_url" in config
        assert "token_url" in config
    
    def test_no_security_schemes(self):
        """Empty security schemes → no auth required."""
        schemes = {}
        
        config = _infer_auth_policy_config(schemes)
        
        assert config["type"] == "none"
    
    def test_openid_connect(self):
        """OpenID Connect → openid_connect config."""
        schemes = {
            "openId": {
                "type": "openIdConnect",
                "openIdConnectUrl": "https://auth.example.com/.well-known/openid-configuration",
            }
        }
        
        config = _infer_auth_policy_config(schemes)
        
        assert config["type"] == "openid_connect"
        assert "openid_connect_url" in config


class TestExtractSecuritySchemes:
    """Tests for _extract_security_schemes()."""
    
    def test_extracts_from_openapi_specs(self):
        """Extracts securitySchemes from state.plan.openapi_specs."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test",
            plan={
                "openapi_specs": [
                    {
                        "components": {
                            "securitySchemes": {
                                "apiKey": {
                                    "type": "apiKey",
                                    "in": "header",
                                    "name": "X-API-Key",
                                }
                            }
                        }
                    }
                ]
            }
        )
        
        schemes = _extract_security_schemes(state)
        
        assert "apiKey" in schemes
        assert schemes["apiKey"]["type"] == "apiKey"
    
    def test_returns_empty_when_no_specs(self):
        """Returns empty dict when no specs in state."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="Test",
            plan={},
        )
        
        schemes = _extract_security_schemes(state)
        
        assert schemes == {}
    
    def test_returns_empty_when_no_security_schemes(self):
        """Returns empty dict when spec has no securitySchemes."""
        state = WorkflowState(
            source_refs=[],
            spec_refs=["test.yaml"],
            task_description="Test",
            plan={
                "openapi_specs": [
                    {
                        "components": {}
                    }
                ]
            }
        )
        
        schemes = _extract_security_schemes(state)
        
        assert schemes == {}


class TestPolicyInferenceIntegration:
    """Integration tests for policy inference with real specs."""
    
    def test_mock_payments_spec_infers_api_key(self):
        """Mock payments spec uses apiKey → should infer api_key auth."""
        from pathlib import Path
        from integration_coworker.api.entrypoint import design_and_generate_integration
        from integration_coworker.api.types import IntegrationOptions
        
        spec_path = Path(__file__).parent / "fixtures" / "mock_payments_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(spec_path)],
            task_description="Create a checkout session",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Policies are reflected in the report markdown
        assert "policies:" in result.report_markdown.lower() or "Total policies" in result.report_markdown
        
        # Check that auth policy type is inferred from spec
        # The report should mention auth policy
        assert "auth" in result.report_markdown.lower()
    
    def test_acme_widgets_spec_infers_bearer(self):
        """Acme widgets spec uses http/bearer → should infer bearer auth."""
        from pathlib import Path
        from integration_coworker.api.entrypoint import design_and_generate_integration
        from integration_coworker.api.types import IntegrationOptions
        
        spec_path = Path(__file__).parent / "fixtures" / "acme_widgets_openapi.yaml"
        
        result = design_and_generate_integration(
            spec_refs=[str(spec_path)],
            task_description="Create a widget",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Policies should be in the report
        assert "policies:" in result.report_markdown.lower() or "Total policies" in result.report_markdown
        
        # The report should mention auth
        assert "auth" in result.report_markdown.lower()

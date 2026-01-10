"""Unit tests for _extract_required_headers function - V38-005."""

import pytest
from integration_coworker.graph.nodes.build_silver_api_model import _extract_required_headers


class TestExtractRequiredHeaders:
    """Test header extraction from OpenAPI operation data."""

    def test_curl_example_extraction(self):
        """Test Strategy 1: Extract headers from curl examples (most reliable)."""
        operation = {
            'x-oaiMeta': {
                'beta': True,
                'group': 'assistants',
                'examples': {
                    'request': {
                        'curl': '''curl https://api.openai.com/v1/assistants \\
                          -H "Content-Type: application/json" \\
                          -H "Authorization: Bearer $OPENAI_API_KEY" \\
                          -H "OpenAI-Beta: assistants=v2"'''
                    }
                }
            }
        }
        headers = _extract_required_headers(operation, '/assistants')
        assert headers == {'OpenAI-Beta': 'assistants=v2'}

    def test_group_inference_fallback(self):
        """Test Strategy 3: Infer header from beta flag + group name."""
        operation = {
            'x-oaiMeta': {
                'beta': True,
                'group': 'threads',
            }
        }
        headers = _extract_required_headers(operation, '/threads')
        assert headers == {'OpenAI-Beta': 'threads=v2'}

    def test_required_header_parameter(self):
        """Test Strategy 2: Extract from required header parameters."""
        operation = {
            'parameters': [
                {
                    'in': 'header',
                    'name': 'X-Custom-Header',
                    'required': True,
                    'schema': {'type': 'string', 'default': 'custom-value'}
                }
            ]
        }
        headers = _extract_required_headers(operation, '/api/v1/custom')
        assert headers == {'X-Custom-Header': 'custom-value'}

    def test_x_required_headers_extension(self):
        """Test Strategy 4: Use x-required-headers extension."""
        operation = {
            'x-required-headers': {
                'X-Api-Version': '2024-01'
            }
        }
        headers = _extract_required_headers(operation, '/api/resource')
        assert headers == {'X-Api-Version': '2024-01'}

    def test_no_special_headers(self):
        """Test normal operation with no special headers needed."""
        operation = {
            'operationId': 'listItems',
            'summary': 'List items'
        }
        headers = _extract_required_headers(operation, '/items')
        assert headers is None

    def test_curl_takes_priority_over_group_inference(self):
        """Test that curl extraction takes priority over group inference."""
        operation = {
            'x-oaiMeta': {
                'beta': True,
                'group': 'threads',  # Would produce 'threads=v2'
                'examples': {
                    'request': {
                        'curl': '-H "OpenAI-Beta: assistants=v2"'  # But curl says 'assistants=v2'
                    }
                }
            }
        }
        headers = _extract_required_headers(operation, '/threads/runs')
        # Curl should win
        assert headers == {'OpenAI-Beta': 'assistants=v2'}

    def test_beta_without_group_no_header(self):
        """Test beta=true without group doesn't produce a header."""
        operation = {
            'x-oaiMeta': {
                'beta': True,
                # No group specified
            }
        }
        headers = _extract_required_headers(operation, '/some/endpoint')
        assert headers is None

    def test_multiple_curl_headers_extracts_all_custom_headers(self):
        """Test that all custom headers (except Auth/Content-Type) are extracted from curl examples."""
        operation = {
            'x-oaiMeta': {
                'examples': {
                    'request': {
                        'curl': '''curl -H "Authorization: Bearer xxx" -H "OpenAI-Beta: assistants=v2" -H "X-Other: value"'''
                    }
                }
            }
        }
        headers = _extract_required_headers(operation, '/assistants')
        # All non-standard headers extracted (except Authorization which is handled separately)
        assert headers == {'OpenAI-Beta': 'assistants=v2', 'X-Other': 'value'}

    def test_assistants_group_produces_assistants_v2(self):
        """Test that assistants group produces correct header value."""
        operation = {
            'x-oaiMeta': {
                'beta': True,
                'group': 'assistants',
            }
        }
        headers = _extract_required_headers(operation, '/assistants/{assistant_id}')
        assert headers == {'OpenAI-Beta': 'assistants=v2'}

    def test_runs_group_produces_runs_v2(self):
        """Test vector store endpoints."""
        operation = {
            'x-oaiMeta': {
                'beta': True,
                'group': 'vector_stores',
            }
        }
        headers = _extract_required_headers(operation, '/vector_stores')
        assert headers == {'OpenAI-Beta': 'vector_stores=v2'}

"""
Tests for client_detector module (V39-007)

Tests the ability to detect existing API clients in target repositories
to enable reuse instead of duplicate generation.
"""
import tempfile
from pathlib import Path

import pytest

from integration_coworker.codegen.client_detector import (
    scan_repo_for_clients,
    check_for_existing_client,
    decide_client_strategy,
    ClientDetectionResult,
    ExistingClientInfo,
    ClientDecision,
    _file_path_to_module,
    _is_client_class,
    _detect_http_library,
    _match_provider,
)


class TestFilePathToModule:
    """Tests for _file_path_to_module helper."""
    
    def test_simple_path(self):
        """Test simple path conversion."""
        assert _file_path_to_module("clients/openai.py") == "clients.openai"
    
    def test_nested_path(self):
        """Test nested path conversion."""
        assert _file_path_to_module("src/integrations/clients/stripe.py") == "integrations.clients.stripe"
    
    def test_src_prefix_stripped(self):
        """Test that src/ prefix is handled."""
        assert _file_path_to_module("src/clients/github.py") == "clients.github"


class TestDetectHttpLibrary:
    """Tests for _detect_http_library helper."""
    
    def test_detects_httpx(self):
        """Should detect httpx library."""
        imports = {"httpx", "json", "typing"}
        assert _detect_http_library(imports) == "httpx"
    
    def test_detects_requests(self):
        """Should detect requests library."""
        imports = {"requests", "os"}
        assert _detect_http_library(imports) == "requests"
    
    def test_detects_aiohttp(self):
        """Should detect aiohttp library."""
        imports = {"aiohttp", "asyncio", "aiohttp.ClientSession"}
        assert _detect_http_library(imports) == "aiohttp"
    
    def test_returns_none_for_no_http(self):
        """Should return None when no HTTP library detected."""
        imports = {"json", "os", "pathlib"}
        assert _detect_http_library(imports) is None


class TestIsClientClass:
    """Tests for _is_client_class helper."""
    
    def test_recognizes_client_suffix(self):
        """Should recognize *Client class names."""
        class_info = {
            "name": "OpenAIClient",
            "bases": [],
            "methods": ["get", "post"],
            "async_methods": [],
        }
        imports = {"httpx"}
        is_client, reasons = _is_client_class(class_info, imports)
        assert is_client
        assert any("Client" in r for r in reasons)
    
    def test_recognizes_http_methods(self):
        """Should recognize HTTP method names."""
        class_info = {
            "name": "StripeAPI",
            "bases": [],
            "methods": ["get", "post", "delete", "_make_request"],
            "async_methods": [],
        }
        imports = {"requests"}
        is_client, reasons = _is_client_class(class_info, imports)
        assert is_client
        assert any("HTTP methods" in r for r in reasons)
    
    def test_rejects_non_client(self):
        """Should reject classes that don't look like clients."""
        class_info = {
            "name": "DataProcessor",
            "bases": [],
            "methods": ["process", "transform", "validate"],
            "async_methods": [],
        }
        imports = {"pandas", "numpy"}
        is_client, reasons = _is_client_class(class_info, imports)
        assert not is_client


class TestMatchProvider:
    """Tests for _match_provider helper."""
    
    def test_matches_by_class_name(self):
        """Should match when class name contains provider."""
        is_match, confidence, reasons = _match_provider(
            class_name="OpenAIClient",
            file_path="clients/openai.py",
            base_url="https://api.openai.com/v1",
            target_provider="openai",
        )
        assert is_match
        assert confidence >= 0.5
        assert any("Class name" in r for r in reasons)
    
    def test_matches_by_file_path(self):
        """Should match when file path contains provider."""
        is_match, confidence, reasons = _match_provider(
            class_name="APIClient",
            file_path="integrations/stripe_client.py",
            base_url=None,
            target_provider="stripe",
        )
        assert is_match
        assert confidence >= 0.3
    
    def test_no_match_for_different_provider(self):
        """Should not match different providers."""
        is_match, confidence, reasons = _match_provider(
            class_name="StripeClient",
            file_path="clients/stripe.py",
            base_url="https://api.stripe.com",
            target_provider="openai",
        )
        assert not is_match
        assert confidence < 0.3


class TestScanRepoForClients:
    """Tests for scan_repo_for_clients function."""
    
    def test_finds_simple_client(self, tmp_path: Path):
        """Should find a simple API client."""
        # Create a test repo structure
        clients_dir = tmp_path / "clients"
        clients_dir.mkdir()
        
        client_file = clients_dir / "openai_client.py"
        client_file.write_text('''
import httpx

class OpenAIClient:
    """OpenAI API client."""
    
    def __init__(self, api_key: str):
        self.base_url = "https://api.openai.com/v1"
        self.client = httpx.Client()
        self.api_key = api_key
    
    def get(self, endpoint: str):
        return self.client.get(f"{self.base_url}/{endpoint}")
    
    def post(self, endpoint: str, data: dict):
        return self.client.post(f"{self.base_url}/{endpoint}", json=data)
''')
        
        result = scan_repo_for_clients(
            repo_root=str(tmp_path),
            target_provider="openai",
        )
        
        assert result.clients_found >= 1
        assert len(result.matching_clients) >= 1
        assert result.best_match is not None
        assert "OpenAIClient" in result.best_match.class_name
    
    def test_finds_no_client_in_empty_repo(self, tmp_path: Path):
        """Should handle empty repo gracefully."""
        result = scan_repo_for_clients(
            repo_root=str(tmp_path),
            target_provider="stripe",
        )
        
        assert result.clients_found == 0
        assert len(result.matching_clients) == 0
        assert result.best_match is None
        assert not result.should_use_existing
    
    def test_excludes_venv_directories(self, tmp_path: Path):
        """Should exclude virtual environment directories."""
        # Create client in venv (should be ignored)
        venv_dir = tmp_path / ".venv" / "lib" / "site-packages" / "openai"
        venv_dir.mkdir(parents=True)
        (venv_dir / "client.py").write_text('''
import httpx
class OpenAIClient:
    def get(self): pass
''')
        
        result = scan_repo_for_clients(
            repo_root=str(tmp_path),
            target_provider="openai",
        )
        
        # Should not find the client in venv
        assert result.clients_found == 0
    
    def test_distinguishes_different_providers(self, tmp_path: Path):
        """Should correctly distinguish between different API providers."""
        clients_dir = tmp_path / "clients"
        clients_dir.mkdir()
        
        # Create Stripe client
        (clients_dir / "stripe_client.py").write_text('''
import requests

class StripeClient:
    def __init__(self):
        self.base_url = "https://api.stripe.com/v1"
    
    def post(self, endpoint, data):
        return requests.post(f"{self.base_url}/{endpoint}", json=data)
''')
        
        # Create OpenAI client
        (clients_dir / "openai_client.py").write_text('''
import httpx

class OpenAIClient:
    def __init__(self):
        self.base_url = "https://api.openai.com/v1"
    
    def post(self, endpoint, data):
        return httpx.post(f"{self.base_url}/{endpoint}", json=data)
''')
        
        # Search for OpenAI
        result = scan_repo_for_clients(
            repo_root=str(tmp_path),
            target_provider="openai",
        )
        
        assert result.best_match is not None
        assert "OpenAI" in result.best_match.class_name
        
        # Verify Stripe client went to other_clients
        assert len(result.other_clients) >= 1


class TestCheckForExistingClient:
    """Tests for check_for_existing_client convenience function."""
    
    def test_returns_client_info_when_found(self, tmp_path: Path):
        """Should return ExistingClientInfo when client found."""
        clients_dir = tmp_path / "src" / "clients"
        clients_dir.mkdir(parents=True)
        
        (clients_dir / "github_client.py").write_text('''
import httpx

class GitHubClient:
    """GitHub API client."""
    
    def __init__(self, token: str):
        self.base_url = "https://api.github.com"
        self.token = token
    
    def get(self, endpoint: str):
        pass
    
    def post(self, endpoint: str, data: dict):
        pass
''')
        
        result = check_for_existing_client(
            repo_root=str(tmp_path),
            provider_code="github",
        )
        
        assert result is not None
        assert result.class_name == "GitHubClient"
        assert "github_client" in result.file_path
    
    def test_returns_none_when_not_found(self, tmp_path: Path):
        """Should return None when no matching client found."""
        result = check_for_existing_client(
            repo_root=str(tmp_path),
            provider_code="nonexistent",
        )
        
        assert result is None


class TestDecideClientStrategy:
    """Tests for decide_client_strategy function."""
    
    def test_use_existing_when_found(self, tmp_path: Path):
        """Should decide to use existing client when found."""
        clients_dir = tmp_path / "integrations" / "clients"
        clients_dir.mkdir(parents=True)
        
        (clients_dir / "anthropic.py").write_text('''
import httpx

class AnthropicClient:
    """Anthropic Claude API client."""
    
    BASE_URL = "https://api.anthropic.com"
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = httpx.Client()
    
    def create_message(self, prompt: str):
        return self.client.post(
            f"{self.BASE_URL}/v1/messages",
            json={"prompt": prompt}
        )
''')
        
        decision = decide_client_strategy(
            repo_root=str(tmp_path),
            provider_code="anthropic",
        )
        
        assert decision.use_existing
        assert decision.client_class_name == "AnthropicClient"
        assert decision.import_statement is not None
        assert "AnthropicClient" in decision.import_statement
    
    def test_generate_new_when_not_found(self, tmp_path: Path):
        """Should decide to generate new client when none found."""
        decision = decide_client_strategy(
            repo_root=str(tmp_path),
            provider_code="stripe",
        )
        
        assert not decision.use_existing
        assert decision.existing_client is None
    
    def test_force_generate_flag(self, tmp_path: Path):
        """Should generate new client when force_generate=True."""
        clients_dir = tmp_path / "clients"
        clients_dir.mkdir()
        
        (clients_dir / "stripe_client.py").write_text('''
import requests

class StripeClient:
    def post(self, endpoint, data):
        pass
''')
        
        decision = decide_client_strategy(
            repo_root=str(tmp_path),
            provider_code="stripe",
            force_generate=True,
        )
        
        assert not decision.use_existing
        assert "force_generate" in decision.reasons[0].lower()
    
    def test_handles_missing_repo_root(self):
        """Should handle missing repo_root gracefully."""
        decision = decide_client_strategy(
            repo_root="",
            provider_code="openai",
        )
        
        assert not decision.use_existing


class TestClientDecisionBool:
    """Tests for ClientDecision __bool__ method."""
    
    def test_true_when_using_existing(self):
        """Should be truthy when using existing client."""
        decision = ClientDecision(
            use_existing=True,
            existing_client=ExistingClientInfo(
                file_path="clients/test.py",
                module_path="clients.test",
                class_name="TestClient",
            ),
        )
        assert bool(decision) is True
    
    def test_false_when_generating_new(self):
        """Should be falsy when generating new client."""
        decision = ClientDecision(use_existing=False)
        assert bool(decision) is False


class TestAsyncClientDetection:
    """Tests for detecting async API clients."""
    
    def test_detects_async_methods(self, tmp_path: Path):
        """Should detect async client methods."""
        clients_dir = tmp_path / "api"
        clients_dir.mkdir()
        
        (clients_dir / "async_client.py").write_text('''
import aiohttp

class AsyncOpenAIClient:
    """Async OpenAI API client."""
    
    def __init__(self):
        self.base_url = "https://api.openai.com/v1"
    
    async def get(self, endpoint: str):
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{self.base_url}/{endpoint}") as response:
                return await response.json()
    
    async def post(self, endpoint: str, data: dict):
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self.base_url}/{endpoint}", json=data) as response:
                return await response.json()
''')
        
        result = scan_repo_for_clients(
            repo_root=str(tmp_path),
            target_provider="openai",
        )
        
        assert result.clients_found >= 1
        assert result.best_match is not None
        assert len(result.best_match.async_methods) >= 2


class TestMultipleClientsInRepo:
    """Tests for repos with multiple clients."""
    
    def test_ranks_by_confidence(self, tmp_path: Path):
        """Should rank multiple clients by match confidence."""
        clients_dir = tmp_path / "services"
        clients_dir.mkdir()
        
        # Generic client (lower confidence)
        (clients_dir / "api.py").write_text('''
import httpx

class APIClient:
    def __init__(self):
        self.base_url = "https://api.example.com"
    
    def request(self, method, endpoint):
        pass
''')
        
        # Specific OpenAI client (higher confidence)
        (clients_dir / "openai_service.py").write_text('''
import httpx

class OpenAIService:
    def __init__(self):
        self.base_url = "https://api.openai.com/v1"
    
    def chat_completion(self, messages):
        pass
    
    def embeddings(self, input_text):
        pass
''')
        
        result = scan_repo_for_clients(
            repo_root=str(tmp_path),
            target_provider="openai",
        )
        
        # OpenAIService should be the best match due to name + URL
        assert result.best_match is not None
        assert "OpenAI" in result.best_match.class_name

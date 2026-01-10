"""
Integration tests for generate_code_and_tests.py multi-language sandbox dispatch.

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2:
- Verifies _run_multilang_sandbox_validation is invoked for TypeScript/Go
- Verifies _run_sandbox_validation is invoked for Python
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class MockCodeArtifact:
    """Mock CodeArtifact for testing."""
    id: Optional[str] = None
    task_id: Optional[str] = None
    artifact_type: str = "client"
    language: str = "typescript"
    module_name: str = "test_client"
    rel_path: str = "src/test_client.ts"
    content: str = "export class TestClient {}"


@dataclass
class MockProfile:
    """Mock CodegenProfile for testing."""
    name: str = "development"
    enable_sandbox_execution: bool = True
    enable_coverage: bool = False
    fail_on_no_tests: bool = False
    coverage_fail_under: int = 0
    enable_self_review: bool = False


@dataclass
class MockState:
    """Mock WorkflowState for testing."""
    code_artifacts: List[MockCodeArtifact] = None
    errors: List[str] = None
    repo_profile: Optional[MagicMock] = None
    sandbox_result: dict = None
    
    def __post_init__(self):
        self.code_artifacts = self.code_artifacts or []
        self.errors = self.errors or []


class TestMultiLangDispatch:
    """Tests for multi-language sandbox dispatch in generate_code_and_tests."""
    
    @pytest.mark.asyncio
    async def test_typescript_dispatches_to_multilang_sandbox(self):
        """TypeScript artifacts should use _run_multilang_sandbox_validation."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _run_multilang_sandbox_validation,
            _run_sandbox_validation,
        )
        
        artifacts = [
            MockCodeArtifact(language="typescript", rel_path="src/client.ts"),
        ]
        profile = MockProfile()
        state = MockState()
        
        # Mock the multi-lang sandbox execution
        with patch(
            "integration_coworker.graph.nodes.generate_code_and_tests.execute_multilang_sandbox"
        ) as mock_multilang:
            mock_gate = MagicMock()
            mock_gate.name = "tsc"
            mock_gate.passed = True
            mock_gate.duration_ms = 100
            mock_gate.output = "tsc passed"
            
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.gate_results = [mock_gate]
            mock_result.failed_gates = []
            mock_result.is_tier1_compliant = True
            mock_result.summary = "OK"
            mock_result.sandbox_dir = "/tmp/test"
            mock_multilang.return_value = mock_result
            
            success, result = await _run_multilang_sandbox_validation(
                code_artifacts=artifacts,
                target_language="typescript",
                profile=profile,
                state=state,
            )
            
            assert mock_multilang.called, "execute_multilang_sandbox should be called for TypeScript"
            assert success is True
    
    @pytest.mark.asyncio
    async def test_go_dispatches_to_multilang_sandbox(self):
        """Go artifacts should use _run_multilang_sandbox_validation."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _run_multilang_sandbox_validation,
        )
        
        artifacts = [
            MockCodeArtifact(language="go", rel_path="src/client.go"),
        ]
        profile = MockProfile()
        state = MockState()
        
        with patch(
            "integration_coworker.graph.nodes.generate_code_and_tests.execute_multilang_sandbox"
        ) as mock_multilang:
            mock_gate = MagicMock()
            mock_gate.name = "go_test"
            mock_gate.passed = True
            mock_gate.duration_ms = 100
            mock_gate.output = "PASS"
            
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.gate_results = [mock_gate]
            mock_result.failed_gates = []
            mock_result.is_tier1_compliant = True
            mock_result.summary = "OK"
            mock_result.sandbox_dir = "/tmp/test"
            mock_multilang.return_value = mock_result
            
            success, result = await _run_multilang_sandbox_validation(
                code_artifacts=artifacts,
                target_language="go",
                profile=profile,
                state=state,
            )
            
            assert mock_multilang.called, "execute_multilang_sandbox should be called for Go"
            assert success is True
    
    @pytest.mark.asyncio
    async def test_python_falls_back_to_regular_sandbox(self):
        """Python should fall back to _run_sandbox_validation."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _run_multilang_sandbox_validation,
        )
        
        artifacts = [
            MockCodeArtifact(language="python", rel_path="src/client.py"),
        ]
        profile = MockProfile()
        state = MockState()
        
        # Mock both sandboxes
        with patch(
            "integration_coworker.graph.nodes.generate_code_and_tests.execute_multilang_sandbox"
        ) as mock_multilang, patch(
            "integration_coworker.graph.nodes.generate_code_and_tests._run_sandbox_validation",
            new_callable=AsyncMock
        ) as mock_python:
            mock_python.return_value = (True, None)
            
            success, result = await _run_multilang_sandbox_validation(
                code_artifacts=artifacts,
                target_language="python",
                profile=profile,
                state=state,
            )
            
            assert mock_python.called, "_run_sandbox_validation should be called for Python"
            assert not mock_multilang.called, "execute_multilang_sandbox should NOT be called for Python"
    
    @pytest.mark.asyncio
    async def test_sandbox_disabled_returns_early(self):
        """When sandbox is disabled, should return (True, None) immediately."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _run_multilang_sandbox_validation,
        )
        
        artifacts = [
            MockCodeArtifact(language="typescript", rel_path="src/client.ts"),
        ]
        profile = MockProfile(enable_sandbox_execution=False)
        state = MockState()
        
        with patch(
            "integration_coworker.graph.nodes.generate_code_and_tests.execute_multilang_sandbox"
        ) as mock_multilang:
            success, result = await _run_multilang_sandbox_validation(
                code_artifacts=artifacts,
                target_language="typescript",
                profile=profile,
                state=state,
            )
            
            assert success is True
            assert result is None
            assert not mock_multilang.called


class TestExtensionMapping:
    """Tests for _get_extension_for_language helper."""
    
    def test_typescript_extension(self):
        """TypeScript should return .ts extension."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _get_extension_for_language,
        )
        from integration_coworker.codegen.sandbox_multilang import ArtifactLanguage
        
        assert _get_extension_for_language(ArtifactLanguage.TYPESCRIPT) == ".ts"
    
    def test_go_extension(self):
        """Go should return .go extension."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _get_extension_for_language,
        )
        from integration_coworker.codegen.sandbox_multilang import ArtifactLanguage
        
        assert _get_extension_for_language(ArtifactLanguage.GO) == ".go"
    
    def test_python_extension(self):
        """Python should return .py extension."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _get_extension_for_language,
        )
        from integration_coworker.codegen.sandbox_multilang import ArtifactLanguage
        
        assert _get_extension_for_language(ArtifactLanguage.PYTHON) == ".py"


class TestTierConfiguration:
    """Tests for Tier configuration in multi-lang sandbox.
    
    Tier selection logic:
    - Tier.PROD: Docker available AND Docker execution enabled
    - Tier.EXP: Docker not available (host execution fallback)
    """
    
    @pytest.mark.asyncio
    async def test_tier_prod_when_explicitly_configured(self):
        """When MULTILANG_TIER=prod and MULTILANG_USE_DOCKER=true, Tier.PROD is used.
        
        Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.4:
        - Tier is NEVER auto-selected from Docker availability
        - Tier is set explicitly via MULTILANG_TIER env var
        - Tier.PROD requires MULTILANG_USE_DOCKER=true (invariant)
        """
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _run_multilang_sandbox_validation,
        )
        from integration_coworker.config import reset_settings
        import os
        
        artifacts = [
            MockCodeArtifact(language="typescript", rel_path="src/client.ts"),
        ]
        profile = MockProfile(name="production")
        state = MockState()
        
        # Explicitly configure tier=prod and use_docker=true via env vars
        old_tier = os.environ.get("MULTILANG_TIER")
        old_docker = os.environ.get("MULTILANG_USE_DOCKER")
        os.environ["MULTILANG_TIER"] = "prod"
        os.environ["MULTILANG_USE_DOCKER"] = "true"
        reset_settings()  # Force reload of settings
        
        try:
            with patch(
                "integration_coworker.graph.nodes.generate_code_and_tests.execute_multilang_sandbox"
            ) as mock_multilang, patch(
                "integration_coworker.codegen.gates.docker_runner.is_docker_available",
                return_value=True,  # Docker must be available for Tier.PROD
            ):
                mock_gate = MagicMock()
                mock_gate.name = "tsc"
                mock_gate.passed = True
                mock_gate.duration_ms = 100
                mock_gate.output = "tsc passed"
                
                mock_result = MagicMock()
                mock_result.success = True
                mock_result.gate_results = [mock_gate]
                mock_result.failed_gates = []
                mock_result.is_tier1_compliant = True  # Docker enables Tier 1
                mock_result.summary = "OK"
                mock_result.sandbox_dir = "/tmp/test"
                mock_multilang.return_value = mock_result
                
                await _run_multilang_sandbox_validation(
                    code_artifacts=artifacts,
                    target_language="typescript",
                    profile=profile,
                    state=state,
                )
                
                # Check the config passed to execute_multilang_sandbox
                assert mock_multilang.called
                call_kwargs = mock_multilang.call_args[1]
                config = call_kwargs.get("config")
                assert config is not None
                
                from integration_coworker.codegen.gates import Tier
                assert config.tier == Tier.PROD, (
                    "When MULTILANG_TIER=prod, config should use Tier.PROD."
                )
                assert config.use_docker is True
        finally:
            # Restore original env vars
            if old_tier is not None:
                os.environ["MULTILANG_TIER"] = old_tier
            else:
                os.environ.pop("MULTILANG_TIER", None)
            if old_docker is not None:
                os.environ["MULTILANG_USE_DOCKER"] = old_docker
            else:
                os.environ.pop("MULTILANG_USE_DOCKER", None)
            reset_settings()
    
    @pytest.mark.asyncio
    async def test_tier_exp_when_docker_unavailable(self):
        """When Docker is unavailable, Tier.EXP should be used (fallback)."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _run_multilang_sandbox_validation,
        )
        
        artifacts = [
            MockCodeArtifact(language="go", rel_path="src/client.go"),
        ]
        profile = MockProfile(name="development")
        state = MockState()
        
        with patch(
            "integration_coworker.graph.nodes.generate_code_and_tests.execute_multilang_sandbox"
        ) as mock_multilang, patch(
            "integration_coworker.codegen.gates.docker_runner.is_docker_available",
            return_value=False,
        ):
            mock_gate = MagicMock()
            mock_gate.name = "go_test"
            mock_gate.passed = True
            mock_gate.duration_ms = 100
            mock_gate.output = "PASS"
            
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.gate_results = [mock_gate]
            mock_result.failed_gates = []
            mock_result.is_tier1_compliant = False  # No Docker = no Tier 1
            mock_result.summary = "OK"
            mock_result.sandbox_dir = "/tmp/test"
            mock_multilang.return_value = mock_result
            
            await _run_multilang_sandbox_validation(
                code_artifacts=artifacts,
                target_language="go",
                profile=profile,
                state=state,
            )
            
            # Check the config passed to execute_multilang_sandbox
            assert mock_multilang.called
            call_kwargs = mock_multilang.call_args[1]
            config = call_kwargs.get("config")
            assert config is not None
            
            from integration_coworker.codegen.gates import Tier
            assert config.tier == Tier.EXP, (
                "When Docker is unavailable, config should fallback to Tier.EXP."
            )
            assert config.use_docker is False


class TestStateObservability:
    """Tests for sandbox result storage in state."""
    
    @pytest.mark.asyncio
    async def test_state_stores_multilang_result(self):
        """Multi-lang result should be stored in state.sandbox_result."""
        from integration_coworker.graph.nodes.generate_code_and_tests import (
            _run_multilang_sandbox_validation,
        )
        
        artifacts = [
            MockCodeArtifact(language="typescript", rel_path="src/client.ts"),
        ]
        profile = MockProfile()
        state = MockState()
        
        with patch(
            "integration_coworker.graph.nodes.generate_code_and_tests.execute_multilang_sandbox"
        ) as mock_multilang:
            mock_gate = MagicMock()
            mock_gate.name = "tsc"
            mock_gate.passed = True
            mock_gate.duration_ms = 100
            mock_gate.output = "tsc passed"
            
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.gate_results = [mock_gate]
            mock_result.failed_gates = []
            mock_result.is_tier1_compliant = True
            mock_result.summary = "All gates passed"
            mock_result.sandbox_dir = "/tmp/test"
            mock_multilang.return_value = mock_result
            
            await _run_multilang_sandbox_validation(
                code_artifacts=artifacts,
                target_language="typescript",
                profile=profile,
                state=state,
            )
            
            # Verify state has sandbox_result
            assert state.sandbox_result is not None
            assert state.sandbox_result["success"] is True
            assert state.sandbox_result["language"] == "typescript"
            assert state.sandbox_result["tier1_compliant"] is True
            assert len(state.sandbox_result["gates"]) == 1
            assert state.sandbox_result["gates"][0]["name"] == "tsc"

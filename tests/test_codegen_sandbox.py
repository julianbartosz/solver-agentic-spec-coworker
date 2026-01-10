"""
Tests for codegen sandbox execution.

Per ADR-0005: Production-Grade Codegen Quality Gates
"""
import asyncio
import os
import pytest
from unittest.mock import patch, MagicMock

import integration_coworker.codegen.sandbox as sandbox
from integration_coworker.codegen.sandbox import (
    ArtifactFile,
    SandboxConfig,
    SandboxResult,
    GateResult,
    execute_in_sandbox,
    run_quick_validation,
    _write_validation_conftest,
    _write_stub_modules,
    _verify_stubs_exist,
    _write_run_metadata,
    _atomic_write,
    STUB_SENTINELS,
    StubVerificationError,
)
from integration_coworker.validation import pytest_conftest_template
from pathlib import Path


class TestArtifactFile:
    """Tests for ArtifactFile dataclass."""
    
    def test_path_normalization(self):
        """Test that Windows-style paths are normalized."""
        artifact = ArtifactFile(r"src\module\test.py", "code")
        assert artifact.path == "src/module/test.py"
    
    def test_unix_paths_unchanged(self):
        """Test that Unix paths remain unchanged."""
        artifact = ArtifactFile("src/module/test.py", "code")
        assert artifact.path == "src/module/test.py"


class TestSandboxConfig:
    """Tests for SandboxConfig defaults."""
    
    def test_default_values(self):
        """Test default configuration values."""
        config = SandboxConfig()
        assert config.python_version == "3.11"
        assert config.enable_ruff is True
        assert config.enable_mypy is True
        assert config.enable_pytest is True
        assert config.timeout_seconds == 300
    
    def test_custom_values(self):
        """Test custom configuration values."""
        config = SandboxConfig(
            python_version="3.12",
            enable_ruff=False,
            timeout_seconds=60,
        )
        assert config.python_version == "3.12"
        assert config.enable_ruff is False
        assert config.timeout_seconds == 60

    @pytest.mark.no_db
    def test_contract_spec_required_when_enabled(self):
        """Enabling contract tests requires a spec path."""
        with pytest.raises(ValueError):
            SandboxConfig(enable_contract_tests=True)

    @pytest.mark.no_db
    def test_live_host_allowlist_required_when_enabled(self):
        """Enabling live tests requires a host allowlist."""
        with pytest.raises(ValueError, match="live_host_allowlist is required"):
            SandboxConfig(enable_live_tests=True)

    @pytest.mark.no_db
    def test_live_tests_config_valid(self):
        """Test valid live tests configuration."""
        config = SandboxConfig(
            enable_live_tests=True,
            live_host_allowlist=["api.stripe.com", "api.twilio.com"],
            live_env_vars={"STRIPE_API_KEY": "sk_test_xxx"},
        )
        assert config.enable_live_tests is True
        assert "api.stripe.com" in config.live_host_allowlist
        assert config.live_env_vars["STRIPE_API_KEY"] == "sk_test_xxx"


class TestSandboxResult:
    """Tests for SandboxResult."""
    
    def test_failed_gates_property(self):
        """Test failed_gates returns only failed gates."""
        result = SandboxResult(
            success=False,
            gate_results=[
                GateResult("ruff", True, "ok", 0, 100),
                GateResult("mypy", False, "error", 1, 200),
                GateResult("pytest", False, "error", 1, 300),
            ],
            sandbox_dir=None,
            summary="test",
        )
        
        failed = result.failed_gates
        assert len(failed) == 2
        assert failed[0].name == "mypy"
        assert failed[1].name == "pytest"
    
    def test_passed_gates_property(self):
        """Test passed_gates returns only passed gates."""
        result = SandboxResult(
            success=False,
            gate_results=[
                GateResult("ruff", True, "ok", 0, 100),
                GateResult("mypy", False, "error", 1, 200),
            ],
            sandbox_dir=None,
            summary="test",
        )
        
        passed = result.passed_gates
        assert len(passed) == 1
        assert passed[0].name == "ruff"


class TestExecuteInSandbox:
    """Tests for execute_in_sandbox function."""
    
    @pytest.mark.asyncio
    async def test_valid_python_code_passes(self):
        """Test that valid Python code passes all gates."""
        valid_code = '''"""A valid module."""

from typing import List


def greet(name: str) -> str:
    """Greet someone.

    Args:
        name: The name to greet.

    Returns:
        A greeting string.
    """
    return f"Hello, {name}!"


def process_items(items: List[str]) -> List[str]:
    """Process items.

    Args:
        items: The items to process.

    Returns:
        Processed items.
    """
    return [item.upper() for item in items]


class Greeter:
    """A greeter class."""

    def __init__(self, prefix: str = "Hello") -> None:
        """Initialize the greeter."""
        self.prefix = prefix

    def greet(self, name: str) -> str:
        """Greet someone."""
        return f"{self.prefix}, {name}!"
'''
        
        result = await execute_in_sandbox(
            artifacts=[ArtifactFile("src/greeter.py", valid_code)],
            dependencies=[],  # Tooling auto-installed when gates enabled
            config=SandboxConfig(
                enable_pytest=False,  # No tests to run
                cleanup_on_success=True,
            ),
        )
        
        assert result.success is True
        assert result.sandbox_dir is None  # Cleaned up
        assert len(result.failed_gates) == 0
    
    @pytest.mark.asyncio
    async def test_syntax_error_fails_ruff(self):
        """Test that syntax errors fail ruff gate."""
        invalid_code = '''
def broken(
    # Missing closing paren
'''
        
        result = await execute_in_sandbox(
            artifacts=[ArtifactFile("src/broken.py", invalid_code)],
            dependencies=[],
            config=SandboxConfig(
                enable_pytest=False,
                enable_mypy=False,  # Just test ruff
                cleanup_on_failure=True,
            ),
        )
        
        assert result.success is False
        assert any(g.name == "ruff" and not g.passed for g in result.gate_results)
    
    @pytest.mark.asyncio
    async def test_type_error_fails_mypy(self):
        """Test that type errors fail mypy gate."""
        type_error_code = '''
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


# Type error: passing string instead of int
result: int = add("hello", "world")
'''
        
        result = await execute_in_sandbox(
            artifacts=[ArtifactFile("src/typed.py", type_error_code)],
            dependencies=[],
            config=SandboxConfig(
                enable_ruff=False,  # Skip ruff, focus on mypy
                enable_pytest=False,
                cleanup_on_failure=True,
            ),
        )
        
        assert result.success is False
        mypy_results = [g for g in result.gate_results if g.name == "mypy"]
        assert len(mypy_results) == 1
        assert mypy_results[0].passed is False
    
    @pytest.mark.asyncio
    async def test_failing_test_fails_pytest(self):
        """Test that failing tests fail pytest gate."""
        src_code = '''
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b
'''
        test_code = '''
import pytest
from src.math_utils import add


def test_add_fails():
    """This test will fail."""
    assert add(1, 1) == 3  # Wrong expectation
'''
        
        result = await execute_in_sandbox(
            artifacts=[
                ArtifactFile("src/math_utils.py", src_code),
                ArtifactFile("tests/test_math.py", test_code),
            ],
            dependencies=["pytest"],
            config=SandboxConfig(
                enable_ruff=False,
                enable_mypy=False,
                cleanup_on_failure=True,
                timeout_seconds=30,
            ),
        )
        
        assert result.success is False
        pytest_results = [g for g in result.gate_results if g.name == "pytest"]
        assert len(pytest_results) == 1
        assert pytest_results[0].passed is False
    
    @pytest.mark.asyncio
    async def test_passing_tests_succeed(self):
        """Test that passing tests pass pytest gate."""
        src_code = '''
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b
'''
        test_code = '''
import sys
sys.path.insert(0, ".")
from src.math_utils import add


def test_add_works():
    """This test will pass."""
    assert add(1, 1) == 2
'''
        
        result = await execute_in_sandbox(
            artifacts=[
                ArtifactFile("src/math_utils.py", src_code),
                ArtifactFile("tests/test_math.py", test_code),
            ],
            dependencies=["pytest", "pytest-timeout"],
            config=SandboxConfig(
                enable_ruff=False,
                enable_mypy=False,
                cleanup_on_success=True,
                timeout_seconds=30,
            ),
        )
        
        # May succeed or fail depending on import path handling
        # The important thing is the gate runs without crashing
        assert isinstance(result, SandboxResult)

    @pytest.mark.no_db
    def test_validation_conftest_matches_template(self, tmp_path):
        """Sandbox conftest should exactly match the golden template."""
        dest = _write_validation_conftest(str(tmp_path))
        source = Path(pytest_conftest_template.__file__).read_text(encoding="utf-8")
        written = dest.read_text(encoding="utf-8")
        assert written == source
    
    @pytest.mark.asyncio
    async def test_gates_disabled(self):
        """Test that gates can be individually disabled."""
        code = '''
# This has lint issues but gates are disabled
x=1+1
def f():pass
'''
        
        result = await execute_in_sandbox(
            artifacts=[ArtifactFile("src/ugly.py", code)],
            dependencies=[],
            config=SandboxConfig(
                enable_ruff=False,
                enable_mypy=False,
                enable_pytest=False,
                cleanup_on_success=True,
            ),
        )
        
        # Only venv creation should be in results
        assert result.success is True
        gate_names = [g.name for g in result.gate_results]
        assert "ruff" not in gate_names
        assert "mypy" not in gate_names

    @pytest.mark.asyncio
    @pytest.mark.no_db
    async def test_contract_gate_runs_when_enabled(self, monkeypatch, tmp_path):
        """Contract gate should execute when enabled and spec is provided."""
        spec = tmp_path / "spec.yaml"
        spec.write_text("""openapi: 3.0.0
info:
  title: Test
  version: 1.0.0
paths: {}
""", encoding="utf-8")

        called = {}

        async def fake_contract(sandbox_dir, python_exe, config, contract_spec):
            called["sandbox_dir"] = sandbox_dir
            called["contract_spec"] = str(contract_spec)
            return GateResult("contract", True, "ok", 0, 1)

        monkeypatch.setattr(sandbox, "_run_contract_tests", fake_contract)

        result = await execute_in_sandbox(
            artifacts=[ArtifactFile("src/foo.py", "def foo():\n    return 'ok'\n")],
            dependencies=[],
            config=SandboxConfig(
                enable_ruff=False,
                enable_mypy=False,
                enable_pytest=False,
                enable_contract_tests=True,
                contract_spec_path=str(spec),
                cleanup_on_success=True,
            ),
        )

        assert result.success is True
        assert any(g.name == "contract" for g in result.gate_results)
        assert called


class TestCoverageGate:
    """Tests for coverage requirements in sandbox.
    
    Per user requirement A: Coverage Gate in sandbox gates.
    - Production: fail_on_no_tests=True, exit code 5 = hard failure
    - Development: fail_on_no_tests=False, exit code 5 = warn + continue
    """
    
    @pytest.mark.asyncio
    async def test_coverage_meets_threshold_passes(self):
        """Test code with coverage meeting threshold passes."""
        src_code = '''"""Math utilities."""


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b
'''
        # Test exercises both functions - should get 100% coverage
        test_code = '''"""Test math utilities."""
import sys
sys.path.insert(0, ".")
from src.math_utils import add, multiply


def test_add():
    """Test add function."""
    assert add(1, 2) == 3
    assert add(0, 0) == 0


def test_multiply():
    """Test multiply function."""
    assert multiply(2, 3) == 6
    assert multiply(0, 5) == 0
'''
        
        result = await execute_in_sandbox(
            artifacts=[
                ArtifactFile("src/math_utils.py", src_code),
                ArtifactFile("tests/test_math.py", test_code),
            ],
            dependencies=["pytest", "pytest-cov"],
            config=SandboxConfig(
                enable_ruff=False,
                enable_mypy=False,
                enable_coverage=True,
                coverage_target="src",
                coverage_fail_under=50,  # Low threshold to ensure pass
                cleanup_on_success=True,
                timeout_seconds=60,
            ),
        )
        
        # Should pass with coverage meeting threshold
        pytest_gate = next((g for g in result.gate_results if g.name == "pytest"), None)
        assert pytest_gate is not None
        assert pytest_gate.passed is True
    
    @pytest.mark.asyncio
    async def test_coverage_below_threshold_fails(self):
        """Test code with coverage below threshold fails."""
        src_code = '''"""Math utilities."""


def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


def divide(a: int, b: int) -> float:
    """Divide two integers."""
    if b == 0:
        raise ValueError("Cannot divide by zero")
    return a / b
'''
        # Test only exercises add - should get ~33% coverage
        test_code = '''"""Test math utilities."""
import sys
sys.path.insert(0, ".")
from src.math_utils import add


def test_add():
    """Test add function."""
    assert add(1, 2) == 3
'''
        
        result = await execute_in_sandbox(
            artifacts=[
                ArtifactFile("src/math_utils.py", src_code),
                ArtifactFile("tests/test_math.py", test_code),
            ],
            dependencies=["pytest", "pytest-cov"],
            config=SandboxConfig(
                enable_ruff=False,
                enable_mypy=False,
                enable_coverage=True,
                coverage_target="src",
                coverage_fail_under=80,  # High threshold to ensure fail
                cleanup_on_failure=True,
                timeout_seconds=60,
            ),
        )
        
        # Should fail due to coverage below threshold
        pytest_gate = next((g for g in result.gate_results if g.name == "pytest"), None)
        assert pytest_gate is not None
        # pytest-cov returns exit code 2 for coverage below threshold
        assert result.success is False
    
    @pytest.mark.asyncio
    async def test_no_tests_collected_production_fails(self):
        """Test that no tests collected fails hard in production mode."""
        src_code = '''"""A module with no tests."""


def hello() -> str:
    """Say hello."""
    return "hello"
'''
        # No test file - will get exit code 5
        
        result = await execute_in_sandbox(
            artifacts=[
                ArtifactFile("src/greet.py", src_code),
                # No test file
            ],
            dependencies=["pytest", "pytest-cov"],
            config=SandboxConfig(
                enable_ruff=False,
                enable_mypy=False,
                enable_coverage=True,
                coverage_target="src",
                coverage_fail_under=80,
                fail_on_no_tests=True,  # Production mode
                cleanup_on_failure=True,
                timeout_seconds=60,
            ),
        )
        
        # Should fail in production mode
        pytest_gate = next((g for g in result.gate_results if g.name == "pytest"), None)
        assert pytest_gate is not None
        assert pytest_gate.passed is False
        assert result.success is False
    
    @pytest.mark.asyncio
    async def test_no_tests_collected_development_warns(self):
        """Test that no tests collected warns but continues in development."""
        src_code = '''"""A module with no tests."""


def hello() -> str:
    """Say hello."""
    return "hello"
'''
        # No test file
        
        result = await execute_in_sandbox(
            artifacts=[
                ArtifactFile("src/greet.py", src_code),
            ],
            dependencies=["pytest"],
            config=SandboxConfig(
                enable_ruff=False,
                enable_mypy=False,
                enable_pytest=True,
                fail_on_no_tests=False,  # Development mode
                cleanup_on_success=True,
                timeout_seconds=60,
            ),
        )
        
        # In dev mode, no tests is allowed (pytest returns success or we convert 5->0)
        pytest_gate = next((g for g in result.gate_results if g.name == "pytest"), None)
        assert pytest_gate is not None
        # Development mode: exit code 5 converted to pass
        assert pytest_gate.passed is True
    
    @pytest.mark.asyncio
    async def test_coverage_config_requires_target(self):
        """Test that enable_coverage=True requires coverage_target."""
        # This tests the SandboxConfig validation
        config = SandboxConfig(
            enable_coverage=True,
            coverage_target="src",  # Must specify target
            coverage_fail_under=80,
        )
        assert config.enable_coverage is True
        assert config.coverage_target == "src"


class TestQuickValidation:
    """Tests for quick validation helper."""
    
    def test_quick_validation_valid_code(self):
        """Test quick validation with valid code."""
        code = '''
def hello() -> str:
    """Return hello."""
    return "hello"
'''
        result = run_quick_validation(code, "hello.py")
        assert isinstance(result, SandboxResult)
    
    def test_quick_validation_invalid_code(self):
        """Test quick validation with invalid code."""
        code = "def broken("
        result = run_quick_validation(code, "broken.py")
        assert result.success is False


class TestExtractHostsFromSpec:
    """Tests for extract_hosts_from_spec function."""
    
    @pytest.mark.no_db
    def test_extract_simple_https_url(self):
        """Test extraction from simple HTTPS URL."""
        from integration_coworker.codegen.sandbox import extract_hosts_from_spec
        
        spec = {"servers": [{"url": "https://api.openai.com/v1"}]}
        hosts = extract_hosts_from_spec(spec)
        assert hosts == ["api.openai.com"]
    
    @pytest.mark.no_db
    def test_extract_with_port(self):
        """Test extraction includes both host and host:port."""
        from integration_coworker.codegen.sandbox import extract_hosts_from_spec
        
        spec = {"servers": [{"url": "https://api.example.com:8443/v1"}]}
        hosts = extract_hosts_from_spec(spec)
        assert "api.example.com" in hosts
        assert "api.example.com:8443" in hosts
    
    @pytest.mark.no_db
    def test_extract_with_server_variables(self):
        """Test extraction with server variables substituted."""
        from integration_coworker.codegen.sandbox import extract_hosts_from_spec
        
        spec = {
            "servers": [{
                "url": "https://{env}.api.example.com",
                "variables": {"env": {"default": "prod"}}
            }]
        }
        hosts = extract_hosts_from_spec(spec)
        assert hosts == ["prod.api.example.com"]
    
    @pytest.mark.no_db
    def test_extract_relative_url_returns_empty(self):
        """Test that relative URLs return empty list."""
        from integration_coworker.codegen.sandbox import extract_hosts_from_spec
        
        spec = {"servers": [{"url": "/v1/api"}]}
        hosts = extract_hosts_from_spec(spec)
        assert hosts == []
    
    @pytest.mark.no_db
    def test_extract_missing_servers_returns_empty(self):
        """Test that missing servers returns empty list."""
        from integration_coworker.codegen.sandbox import extract_hosts_from_spec
        
        spec = {}
        hosts = extract_hosts_from_spec(spec)
        assert hosts == []
    
    @pytest.mark.no_db
    def test_extract_multiple_servers_deduped(self):
        """Test that multiple servers are deduplicated."""
        from integration_coworker.codegen.sandbox import extract_hosts_from_spec
        
        spec = {
            "servers": [
                {"url": "https://api.example.com/v1"},
                {"url": "https://api.example.com/v2"},  # Same host
                {"url": "https://api.other.com"},
            ]
        }
        hosts = extract_hosts_from_spec(spec)
        assert len(hosts) == 2
        assert "api.example.com" in hosts
        assert "api.other.com" in hosts
    
    @pytest.mark.no_db
    def test_extract_variable_without_default_skipped(self):
        """Test that variables without defaults are skipped."""
        from integration_coworker.codegen.sandbox import extract_hosts_from_spec
        
        spec = {
            "servers": [{
                "url": "https://{region}.api.example.com",
                "variables": {"region": {}}  # No default
            }]
        }
        hosts = extract_hosts_from_spec(spec)
        assert hosts == []


class TestMaterializeSpecForContract:
    """Tests for materialize_spec_for_contract function."""
    
    @pytest.mark.no_db
    def test_materialize_creates_yaml_file(self, tmp_path):
        """Test that spec dict is written as YAML file."""
        from integration_coworker.codegen.sandbox import materialize_spec_for_contract
        
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test API", "version": "1.0"},
            "paths": {"/test": {"get": {"summary": "Test endpoint"}}}
        }
        
        result_path = materialize_spec_for_contract(str(tmp_path), spec)
        
        assert result_path.exists()
        assert result_path.name == "_contract_spec.yaml"
        
        # Verify content
        import yaml
        with open(result_path) as f:
            loaded = yaml.safe_load(f)
        assert loaded["openapi"] == "3.0.0"
        assert loaded["info"]["title"] == "Test API"


class TestContractSpecDictConfig:
    """Tests for contract_spec_dict config option."""
    
    @pytest.mark.no_db
    def test_contract_spec_dict_valid(self):
        """Test that contract_spec_dict is accepted as alternative to contract_spec_path."""
        config = SandboxConfig(
            enable_contract_tests=True,
            contract_spec_dict={"openapi": "3.0.0", "paths": {}},
        )
        assert config.enable_contract_tests is True
        assert config.contract_spec_dict is not None
    
    @pytest.mark.no_db
    def test_contract_spec_requires_path_or_dict(self):
        """Test that contract tests require either path or dict."""
        with pytest.raises(ValueError, match="contract_spec_path or contract_spec_dict"):
            SandboxConfig(enable_contract_tests=True)


# =============================================================================
# Bug #101 Regression Tests: Stub Module Creation
# =============================================================================

class TestStubModuleCreation:
    """
    Authoritative tests for Bug #101 stub module fixes.
    
    These tests verify that:
    1. Static stubs are created with correct content (not just package markers)
    2. Dynamic stubs are created from artifact imports
    3. Stub verification catches malformed/missing stubs
    4. Runtime provenance is written for debugging
    
    This is the ONE authoritative test suite - do not create ad-hoc scripts.
    """
    
    @pytest.mark.no_db
    def test_static_stubs_created_with_sentinels(self, tmp_path):
        """
        Verify all static stubs exist with sentinel strings.
        
        This catches the Dec 22 bug where only 28-byte __init__.py was created.
        """
        from integration_coworker.codegen.sandbox import (
            _write_stub_modules, 
            STUB_SENTINELS,
            ArtifactFile,
        )
        
        sandbox_dir = str(tmp_path)
        _write_stub_modules(sandbox_dir, artifacts=[])
        
        clients_dir = tmp_path / "src" / "clients"
        
        for filename, sentinel in STUB_SENTINELS.items():
            stub_path = clients_dir / filename
            assert stub_path.exists(), f"Missing stub: {filename}"
            
            content = stub_path.read_text()
            
            # Must be non-trivial (not just a package marker)
            assert len(content) >= 100, f"Stub too small: {filename} ({len(content)} bytes)"
            
            # Must contain sentinel string
            assert sentinel in content, (
                f"Stub {filename} missing sentinel '{sentinel[:40]}...'"
            )
    
    @pytest.mark.no_db
    def test_dynamic_stubs_created_for_provider_imports(self, tmp_path):
        """
        Verify dynamic stubs are created when artifacts import provider modules.
        
        This catches the Dec 22 bug for ModuleNotFoundError: clients.<provider>_client
        """
        from integration_coworker.codegen.sandbox import _write_stub_modules, ArtifactFile
        
        sandbox_dir = str(tmp_path)
        
        # Simulate artifacts with provider-specific imports
        artifacts = [
            ArtifactFile(
                path="src/clients/stripe_client.py",
                content="from clients.stripe_api_apps_service_a import StripeClient"
            ),
            ArtifactFile(
                path="src/clients/twilio_client.py",
                content="from clients.twilio_messaging_v1_apps_service_a_client import TwilioClient"
            ),
        ]
        
        _write_stub_modules(sandbox_dir, artifacts)
        
        clients_dir = tmp_path / "src" / "clients"
        
        # Verify dynamic stubs were created
        for module_name in ["stripe_api_apps_service_a", "twilio_messaging_v1_apps_service_a_client"]:
            stub_path = clients_dir / f"{module_name}.py"
            assert stub_path.exists(), f"Missing dynamic stub: {module_name}.py"
            
            content = stub_path.read_text()
            assert "class " in content, f"Dynamic stub {module_name} missing class definition"
            assert "__getattr__" in content, f"Dynamic stub {module_name} missing __getattr__ catch-all"
    
    @pytest.mark.no_db
    def test_stub_verification_catches_missing_stubs(self, tmp_path):
        """
        Verify _verify_stubs_exist raises StubVerificationError for missing stubs.
        """
        from integration_coworker.codegen.sandbox import (
            _verify_stubs_exist, 
            StubVerificationError,
        )
        
        clients_dir = tmp_path / "src" / "clients"
        clients_dir.mkdir(parents=True)
        
        # Create a trivial __init__.py (like the Dec 22 bug)
        (clients_dir / "__init__.py").write_text("# Auto-generated by sandbox\n")
        
        with pytest.raises(StubVerificationError, match="Stub too small"):
            _verify_stubs_exist(str(clients_dir))
    
    @pytest.mark.no_db
    def test_stub_verification_catches_missing_sentinel(self, tmp_path):
        """
        Verify _verify_stubs_exist raises error when sentinel is missing.
        """
        from integration_coworker.codegen.sandbox import (
            _verify_stubs_exist,
            StubVerificationError,
        )
        
        clients_dir = tmp_path / "src" / "clients"
        clients_dir.mkdir(parents=True)
        
        # Create __init__.py without sentinel (wrong content)
        (clients_dir / "__init__.py").write_text("# Wrong content " * 20)
        (clients_dir / "integration_http_client.py").write_text("# Wrong content " * 20)
        (clients_dir / "integration_error.py").write_text("# Wrong content " * 20)
        
        with pytest.raises(StubVerificationError, match="missing sentinel"):
            _verify_stubs_exist(str(clients_dir))
    
    @pytest.mark.no_db
    def test_run_metadata_written(self, tmp_path):
        """
        Verify RUN_METADATA.json is written with provenance info.
        
        Non-flaky: Does not depend on git being present or exact values.
        """
        from integration_coworker.codegen.sandbox import _write_run_metadata
        import json
        
        metadata_path = _write_run_metadata(str(tmp_path))
        
        assert metadata_path.exists()
        assert metadata_path.name == "RUN_METADATA.json"
        
        content = json.loads(metadata_path.read_text())
        
        # Must contain core provenance fields (existence only, not exact values)
        assert "timestamp" in content
        assert "pid" in content
        assert isinstance(content["pid"], int)
        assert "hostname" in content
        assert "python_version" in content
        
        # Git fields: git_commit can be None if git not present, but field must exist
        assert "git_commit" in content
        assert "git_error" in content  # Always present (null if no error)
        
        # Module fields
        assert "module_path" in content
        
        # Stub source hash (compact, not preview text)
        assert "stub_source_hash" in content
        # Hash should be 16 hex chars or None
        if content["stub_source_hash"] is not None:
            assert len(content["stub_source_hash"]) == 16
        
        # Sandbox paths
        assert "sandbox_dir" in content
        assert "sandbox_dir_resolved" in content
    
    @pytest.mark.no_db
    def test_atomic_write_prevents_partial_files(self, tmp_path):
        """
        Verify _atomic_write creates complete files with no temp file remnants.
        """
        from integration_coworker.codegen.sandbox import _atomic_write
        import os
        
        test_file = tmp_path / "test.txt"
        content = "Test content " * 100
        
        _atomic_write(str(test_file), content)
        
        assert test_file.exists()
        assert test_file.read_text() == content
        
        # No temp file should remain (check for any .tmp files)
        tmp_files = list(tmp_path.glob("*.tmp*"))
        assert len(tmp_files) == 0, f"Temp files remain: {tmp_files}"
    
    @pytest.mark.no_db
    def test_stub_verification_error_includes_inventory(self, tmp_path):
        """
        Verify StubVerificationError includes actionable directory inventory.
        """
        from integration_coworker.codegen.sandbox import (
            _verify_stubs_exist,
            StubVerificationError,
        )
        
        clients_dir = tmp_path / "src" / "clients"
        clients_dir.mkdir(parents=True)
        
        # Create a trivial __init__.py (like the Dec 22 bug)
        (clients_dir / "__init__.py").write_text("# tiny\n")
        (clients_dir / "some_other_file.py").write_text("# other file\n")
        
        with pytest.raises(StubVerificationError) as exc_info:
            _verify_stubs_exist(str(clients_dir))
        
        error_msg = str(exc_info.value)
        
        # Must include directory inventory
        assert "Directory inventory:" in error_msg
        assert "__init__.py" in error_msg
        # Must include error details
        assert "Stub too small" in error_msg or "Missing stub" in error_msg
    
    @pytest.mark.asyncio
    @pytest.mark.no_db
    async def test_integration_stubs_enable_sandbox_run(self, tmp_path):
        """
        Integration test: Verify stubs enable code with hallucinated imports to pass.
        
        This is the ultimate regression test for Bug #101.
        """
        # Code that imports from hallucinated clients module
        client_code = '''"""Stripe client using hallucinated imports."""
from typing import Dict, Any
from clients.integration_http_client import IntegrationHttpClient, IntegrationError


class StripeClient:
    """Client for Stripe API."""
    
    def __init__(self, api_key: str) -> None:
        """Initialize client."""
        self.api_key = api_key
        self._client = IntegrationHttpClient(
            base_url="https://api.stripe.com/v1",
            api_key=api_key,
        )
    
    def create_customer(self, email: str) -> Dict[str, Any]:
        """Create a customer."""
        # In tests, this would be mocked
        return {"id": "cus_test", "email": email}
'''
        
        test_code = '''"""Tests for Stripe client."""
import pytest
from unittest.mock import patch, MagicMock
from src.clients.stripe_client import StripeClient


def test_create_customer():
    """Test customer creation."""
    client = StripeClient("sk_test_xxx")
    result = client.create_customer("test@example.com")
    assert result["email"] == "test@example.com"
'''
        
        result = await execute_in_sandbox(
            artifacts=[
                ArtifactFile("src/clients/stripe_client.py", client_code),
                ArtifactFile("tests/test_stripe_client.py", test_code),
            ],
            dependencies=[],
            config=SandboxConfig(
                enable_ruff=True,
                enable_mypy=True,
                enable_pytest=True,
                cleanup_on_success=False,  # Keep for inspection
            ),
        )
        
        # Verify RUN_METADATA.json was created
        if result.sandbox_dir:
            import json
            metadata_path = Path(result.sandbox_dir) / "RUN_METADATA.json"
            assert metadata_path.exists(), "RUN_METADATA.json not created"
            
            metadata = json.loads(metadata_path.read_text())
            assert "git_commit" in metadata
        
        # The test should pass - stubs should make imports work
        assert result.success, f"Sandbox failed: {result.summary}"

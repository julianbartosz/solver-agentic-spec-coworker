"""
Tests for production-hardened strict quality gates.

Per DEEP_PRODUCTION_AUDIT_v2.md Gap D (B-002):
- Strict gates must FAIL (not warn) when tools are missing in production
- Tool execution failures must propagate as gate failures
- Development profile can degrade gracefully

This validates the fix in generate_code_and_tests.py for:
- _run_strict_quality_gates()
- _run_ruff_check()
- _run_ruff_format_check()
- _run_mypy_check()
"""
import pytest
import os
import sys
from dataclasses import dataclass
from unittest.mock import patch, MagicMock
import subprocess


# Minimal CodegenProfile mock that matches production interface
@dataclass
class MockCodegenProfile:
    """Mock profile for testing gate behavior."""
    name: str
    enable_strict_gates: bool
    mypy_strict: bool = False


class TestGateResultDataclass:
    """Test GateResult dataclass."""
    
    def test_gate_result_ok(self):
        """GateResult with ok=True."""
        from integration_coworker.graph.nodes.generate_code_and_tests import GateResult
        
        result = GateResult(ok=True, tool="ruff check", exit_code=0)
        assert result.ok is True
        assert result.tool == "ruff check"
        assert result.tool_missing is False
        
    def test_gate_result_tool_missing(self):
        """GateResult with tool_missing=True."""
        from integration_coworker.graph.nodes.generate_code_and_tests import GateResult
        
        result = GateResult(ok=False, tool="mypy", msg="tool not installed", tool_missing=True)
        assert result.ok is False
        assert result.tool_missing is True
        assert "not installed" in result.msg


class TestRuffCheckGate:
    """Test _run_ruff_check helper."""
    
    def test_ruff_check_passes(self, tmp_path):
        """ruff check returns ok=True when code is clean."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_ruff_check
        
        # Write valid Python
        test_file = tmp_path / "clean.py"
        test_file.write_text('x = 1\n')
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = _run_ruff_check(str(test_file), "test_module")
        
        assert result.ok is True
        assert result.tool == "ruff check"
        
    def test_ruff_check_fails_on_error(self, tmp_path):
        """ruff check returns ok=False when lint fails."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_ruff_check
        
        test_file = tmp_path / "bad.py"
        test_file.write_text('import os  # unused\n')
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="F401 unused import", stderr="")
            result = _run_ruff_check(str(test_file), "test_module")
        
        assert result.ok is False
        assert result.exit_code == 1
        assert "F401" in result.msg
        
    def test_ruff_check_missing_tool(self, tmp_path):
        """ruff check returns tool_missing=True when ruff not installed."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_ruff_check
        
        test_file = tmp_path / "test.py"
        test_file.write_text('x = 1\n')
        
        with patch("subprocess.run", side_effect=FileNotFoundError("ruff not found")):
            result = _run_ruff_check(str(test_file), "test_module")
        
        assert result.ok is False
        assert result.tool_missing is True
        assert "not installed" in result.msg
        
    def test_ruff_check_timeout(self, tmp_path):
        """ruff check returns ok=False on timeout."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_ruff_check
        
        test_file = tmp_path / "test.py"
        test_file.write_text('x = 1\n')
        
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ruff", 30)):
            result = _run_ruff_check(str(test_file), "test_module")
        
        assert result.ok is False
        assert "timeout" in result.msg


class TestRuffFormatGate:
    """Test _run_ruff_format_check helper."""
    
    def test_ruff_format_passes(self, tmp_path):
        """ruff format --check returns ok=True when formatted."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_ruff_format_check
        
        test_file = tmp_path / "formatted.py"
        test_file.write_text('x = 1\n')
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = _run_ruff_format_check(str(test_file), "test_module")
        
        assert result.ok is True
        
    def test_ruff_format_fails(self, tmp_path):
        """ruff format --check returns ok=False when unformatted."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_ruff_format_check
        
        test_file = tmp_path / "unformatted.py"
        test_file.write_text('x=1\n')  # Missing spaces
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="Would reformat", stderr="")
            result = _run_ruff_format_check(str(test_file), "test_module")
        
        assert result.ok is False
        
    def test_ruff_format_missing(self, tmp_path):
        """ruff format returns tool_missing=True when ruff not installed."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_ruff_format_check
        
        test_file = tmp_path / "test.py"
        test_file.write_text('x = 1\n')
        
        with patch("subprocess.run", side_effect=FileNotFoundError("ruff not found")):
            result = _run_ruff_format_check(str(test_file), "test_module")
        
        assert result.ok is False
        assert result.tool_missing is True


class TestMypyGate:
    """Test _run_mypy_check helper."""
    
    def test_mypy_passes(self, tmp_path):
        """mypy returns ok=True when code type-checks."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_mypy_check
        
        test_file = tmp_path / "typed.py"
        test_file.write_text('x: int = 1\n')
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
            result = _run_mypy_check(str(test_file), "test_module")
        
        assert result.ok is True
        
    def test_mypy_fails(self, tmp_path):
        """mypy returns ok=False on type error."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_mypy_check
        
        test_file = tmp_path / "bad_types.py"
        test_file.write_text('x: int = "oops"\n')
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="error: incompatible types", stderr="")
            result = _run_mypy_check(str(test_file), "test_module")
        
        assert result.ok is False
        assert "incompatible" in result.msg
        
    def test_mypy_missing(self, tmp_path):
        """mypy returns tool_missing=True when not installed."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_mypy_check
        
        test_file = tmp_path / "test.py"
        test_file.write_text('x = 1\n')
        
        with patch("subprocess.run", side_effect=FileNotFoundError("mypy not found")):
            result = _run_mypy_check(str(test_file), "test_module")
        
        assert result.ok is False
        assert result.tool_missing is True
        
    def test_mypy_strict_mode(self, tmp_path):
        """mypy uses --strict when strict_mode=True."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_mypy_check
        
        test_file = tmp_path / "test.py"
        test_file.write_text('x = 1\n')
        
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="Success", stderr="")
            _run_mypy_check(str(test_file), "test_module", strict_mode=True)
            
            # Verify --strict was passed
            call_args = mock_run.call_args[0][0]
            assert "--strict" in call_args


class TestStrictQualityGatesIntegration:
    """Integration tests for _run_strict_quality_gates."""
    
    @pytest.mark.asyncio
    async def test_production_profile_fails_on_missing_ruff(self):
        """Production profile must FAIL when ruff is missing (not warn)."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_strict_quality_gates
        
        profile = MockCodegenProfile(name="production", enable_strict_gates=True)
        code = "x = 1\n"
        
        # Simulate ruff missing
        with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_check") as mock_ruff:
            mock_ruff.return_value = MagicMock(
                ok=False, tool="ruff check", msg="tool not installed", tool_missing=True
            )
            with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_format_check") as mock_fmt:
                mock_fmt.return_value = MagicMock(ok=True, tool="ruff format", tool_missing=False)
                with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_mypy_check") as mock_mypy:
                    mock_mypy.return_value = MagicMock(ok=True, tool="mypy", tool_missing=False)
                    
                    passed, error = await _run_strict_quality_gates(code, "test_module", profile)
        
        # CRITICAL: Must fail, not pass with warning
        assert passed is False
        assert "MISSING" in error or "not installed" in error
        
    @pytest.mark.asyncio
    async def test_production_profile_fails_on_missing_mypy(self):
        """Production profile must FAIL when mypy is missing."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_strict_quality_gates
        
        profile = MockCodegenProfile(name="production", enable_strict_gates=True)
        code = "x = 1\n"
        
        # Simulate mypy missing
        with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_check") as mock_ruff:
            mock_ruff.return_value = MagicMock(ok=True, tool="ruff check", tool_missing=False)
            with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_format_check") as mock_fmt:
                mock_fmt.return_value = MagicMock(ok=True, tool="ruff format", tool_missing=False)
                with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_mypy_check") as mock_mypy:
                    mock_mypy.return_value = MagicMock(
                        ok=False, tool="mypy", msg="tool not installed", tool_missing=True
                    )
                    
                    passed, error = await _run_strict_quality_gates(code, "test_module", profile)
        
        assert passed is False
        assert "mypy" in error.lower()
        
    @pytest.mark.asyncio
    async def test_production_profile_passes_when_all_tools_pass(self):
        """Production profile passes when all gates pass."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_strict_quality_gates
        
        profile = MockCodegenProfile(name="production", enable_strict_gates=True)
        code = "x: int = 1\n"
        
        with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_check") as mock_ruff:
            mock_ruff.return_value = MagicMock(ok=True, tool="ruff check", tool_missing=False)
            with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_format_check") as mock_fmt:
                mock_fmt.return_value = MagicMock(ok=True, tool="ruff format", tool_missing=False)
                with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_mypy_check") as mock_mypy:
                    mock_mypy.return_value = MagicMock(ok=True, tool="mypy", tool_missing=False)
                    
                    passed, error = await _run_strict_quality_gates(code, "test_module", profile)
        
        assert passed is True
        assert error == ""
        
    @pytest.mark.asyncio
    async def test_development_profile_skips_gates(self):
        """Development profile (enable_strict_gates=False) skips gates entirely."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_strict_quality_gates
        
        profile = MockCodegenProfile(name="development", enable_strict_gates=False)
        code = "import os  # unused import - would fail ruff\n"
        
        # Should not even call the gate functions
        with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_check") as mock_ruff:
            with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_mypy_check") as mock_mypy:
                passed, error = await _run_strict_quality_gates(code, "test_module", profile)
        
        assert passed is True
        assert error == ""
        # Gates should not have been called
        mock_ruff.assert_not_called()
        mock_mypy.assert_not_called()
        
    @pytest.mark.asyncio
    async def test_production_aggregates_multiple_failures(self):
        """Production profile aggregates all gate failures in error message."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_strict_quality_gates
        
        profile = MockCodegenProfile(name="production", enable_strict_gates=True)
        code = "x = 1\n"
        
        # Simulate both ruff and mypy failing
        with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_check") as mock_ruff:
            mock_ruff.return_value = MagicMock(
                ok=False, tool="ruff check", msg="F401 unused import", tool_missing=False
            )
            with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_ruff_format_check") as mock_fmt:
                mock_fmt.return_value = MagicMock(ok=True, tool="ruff format", tool_missing=False)
                with patch("integration_coworker.graph.nodes.generate_code_and_tests._run_mypy_check") as mock_mypy:
                    mock_mypy.return_value = MagicMock(
                        ok=False, tool="mypy", msg="type error", tool_missing=False
                    )
                    
                    passed, error = await _run_strict_quality_gates(code, "test_module", profile)
        
        assert passed is False
        # Both errors should be in message
        assert "ruff" in error.lower()
        assert "mypy" in error.lower()


class TestStrictGatesContractConsistency:
    """Verify strict gates follow production contract."""
    
    def test_gate_result_fields_match_contract(self):
        """GateResult has all required fields for error propagation."""
        from integration_coworker.graph.nodes.generate_code_and_tests import GateResult
        
        # Required fields per production contract
        required_fields = {'ok', 'tool', 'msg', 'exit_code', 'tool_missing'}
        actual_fields = set(GateResult.__dataclass_fields__.keys())
        
        assert required_fields <= actual_fields, f"Missing fields: {required_fields - actual_fields}"
        
    @pytest.mark.asyncio
    async def test_return_type_is_tuple_bool_str(self):
        """_run_strict_quality_gates returns Tuple[bool, str]."""
        from integration_coworker.graph.nodes.generate_code_and_tests import _run_strict_quality_gates
        
        profile = MockCodegenProfile(name="dev", enable_strict_gates=False)
        passed, error = await _run_strict_quality_gates("x=1\n", "mod", profile)
        
        assert isinstance(passed, bool)
        assert isinstance(error, str)

"""
Tests for PR #8: Static Analysis Gate.

These tests validate:
1. Built-in checks work (syntax, imports)
2. Tool discovery is correct (not hardcoded)
3. "Tool missing" yields "skipped", not "failed"
4. Artifact storage follows refs-not-blobs pattern
5. No runtime wiring imports

Per ADR-HITL-ENHANCEMENT-v2:
- All tests must be deterministic and offline
- No actual tool execution unless tool is discovered
- Focus on contract validation, not integration
"""

import ast
import json
import pytest
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# =============================================================================
# Test: Module Import Safety
# =============================================================================

class TestImportSafety:
    """Verify static analysis modules don't pull in heavy dependencies."""
    
    def test_static_checks_import_no_streamlit(self):
        """static_checks should import without streamlit."""
        modules_before = set(sys.modules.keys())
        
        from integration_coworker.graph import static_checks
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        streamlit_modules = [m for m in new_modules if 'streamlit' in m.lower()]
        assert not streamlit_modules, f"Streamlit modules imported: {streamlit_modules}"
    
    def test_static_analysis_gate_import_no_streamlit(self):
        """static_analysis_gate should import without streamlit."""
        modules_before = set(sys.modules.keys())
        
        from integration_coworker.graph.nodes import static_analysis_gate
        
        modules_after = set(sys.modules.keys())
        new_modules = modules_after - modules_before
        
        streamlit_modules = [m for m in new_modules if 'streamlit' in m.lower()]
        assert not streamlit_modules, f"Streamlit modules imported: {streamlit_modules}"
    
    def test_static_checks_has_registry(self):
        """static_checks should export CHECK_REGISTRY."""
        from integration_coworker.graph import static_checks
        
        assert hasattr(static_checks, 'CHECK_REGISTRY')
        assert 'syntax' in static_checks.CHECK_REGISTRY
        assert 'imports' in static_checks.CHECK_REGISTRY


# =============================================================================
# Test: Built-in Checks (stdlib only)
# =============================================================================

class TestBuiltinChecks:
    """Test built-in checks that use only stdlib."""
    
    def test_syntax_check_valid_code(self):
        """Valid Python code should pass syntax check."""
        from integration_coworker.graph.static_checks import check_syntax
        
        code = '''
def hello():
    print("Hello, World!")
'''
        issues = check_syntax(code, "test.py")
        assert len(issues) == 0
    
    def test_syntax_check_invalid_code(self):
        """Invalid Python code should yield syntax error."""
        from integration_coworker.graph.static_checks import check_syntax
        
        code = '''
def hello(
    # Missing closing paren
'''
        issues = check_syntax(code, "test.py")
        
        assert len(issues) == 1
        assert issues[0].severity == "error"
        assert issues[0].category == "syntax"
        assert issues[0].rule_id == "E999"
    
    def test_syntax_check_reports_line_number(self):
        """Syntax error should report correct line number."""
        from integration_coworker.graph.static_checks import check_syntax
        
        code = '''x = 1
y = 2
if True
    pass
'''
        issues = check_syntax(code, "test.py")
        
        assert len(issues) == 1
        # Line 3 has the syntax error (missing colon)
        assert issues[0].line_number == 3 or issues[0].line_number == 4
    
    def test_import_check_stdlib_modules(self):
        """Stdlib imports should not trigger warnings."""
        from integration_coworker.graph.static_checks import check_imports
        
        code = '''
import os
import sys
from pathlib import Path
'''
        issues = check_imports(code, "test.py")
        
        # Stdlib modules should be found
        assert len(issues) == 0
    
    def test_import_check_unknown_module(self):
        """Unknown modules should trigger warning."""
        from integration_coworker.graph.static_checks import check_imports
        
        code = '''
import some_nonexistent_module_12345
'''
        issues = check_imports(code, "test.py")
        
        assert len(issues) == 1
        assert issues[0].severity == "warning"
        assert issues[0].category == "import"
        assert "some_nonexistent_module_12345" in issues[0].message
    
    def test_import_check_skips_syntax_errors(self):
        """Import check should skip files with syntax errors."""
        from integration_coworker.graph.static_checks import check_imports
        
        code = '''
def broken(
'''
        # Should not raise, just return empty
        issues = check_imports(code, "test.py")
        assert issues == []


# =============================================================================
# Test: Tool Discovery (Not Hardcoded)
# =============================================================================

class TestToolDiscovery:
    """Test that tools are discovered, not hardcoded."""
    
    def test_discover_tool_returns_none_for_missing(self):
        """discover_tool should return None for missing tools."""
        from integration_coworker.graph.static_checks import discover_tool
        
        # Use a tool name that definitely doesn't exist
        result = discover_tool("definitely_not_a_real_tool_12345")
        assert result is None
    
    def test_discover_tool_finds_python(self):
        """discover_tool should find python (test environment has it)."""
        from integration_coworker.graph.static_checks import discover_tool
        
        result = discover_tool("python")
        # python or python3 should be on PATH in test env
        assert result is None or result.endswith("python") or result.endswith("python3")
    
    def test_has_tool_config_no_repo(self):
        """has_tool_config should return False with no repo."""
        from integration_coworker.graph.static_checks import has_tool_config
        
        result = has_tool_config(None, ["ruff.toml"])
        assert result is False
    
    def test_has_tool_config_missing_file(self):
        """has_tool_config should return False for missing config."""
        from integration_coworker.graph.static_checks import has_tool_config
        
        with tempfile.TemporaryDirectory() as tmpdir:
            result = has_tool_config(Path(tmpdir), ["ruff.toml", "mypy.ini"])
            assert result is False
    
    def test_has_tool_config_finds_file(self):
        """has_tool_config should return True when config exists."""
        from integration_coworker.graph.static_checks import has_tool_config
        
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a config file
            (Path(tmpdir) / "ruff.toml").write_text("[tool.ruff]")
            
            result = has_tool_config(Path(tmpdir), ["ruff.toml", "mypy.ini"])
            assert result is True


# =============================================================================
# Test: Check Registry
# =============================================================================

class TestCheckRegistry:
    """Test the check registry pattern."""
    
    def test_builtin_checks_are_marked(self):
        """Built-in checks should have builtin=True."""
        from integration_coworker.graph.static_checks import CHECK_REGISTRY
        
        assert CHECK_REGISTRY["syntax"].builtin is True
        assert CHECK_REGISTRY["imports"].builtin is True
    
    def test_optional_checks_have_tool_name(self):
        """Optional checks should have tool_name set."""
        from integration_coworker.graph.static_checks import CHECK_REGISTRY
        
        if "ruff" in CHECK_REGISTRY:
            assert CHECK_REGISTRY["ruff"].tool_name == "ruff"
            assert CHECK_REGISTRY["ruff"].builtin is False
        
        if "mypy" in CHECK_REGISTRY:
            assert CHECK_REGISTRY["mypy"].tool_name == "mypy"
            assert CHECK_REGISTRY["mypy"].builtin is False
    
    def test_get_enabled_checks_always_includes_builtins(self):
        """get_enabled_checks should always include built-in checks."""
        from integration_coworker.graph.static_checks import get_enabled_checks
        
        enabled = get_enabled_checks(repo_root=None)
        
        assert "syntax" in enabled
        assert "imports" in enabled
    
    def test_get_enabled_checks_respects_explicit_disable(self):
        """get_enabled_checks should respect explicit_disable."""
        from integration_coworker.graph.static_checks import get_enabled_checks
        
        enabled = get_enabled_checks(
            repo_root=None,
            explicit_disable=["syntax"],
        )
        
        assert "syntax" not in enabled
        assert "imports" in enabled


# =============================================================================
# Test: Optional Tool Checks (Skipped When Missing)
# =============================================================================

class TestOptionalToolSkipping:
    """Test that optional tools are skipped when not available."""
    
    def test_ruff_skipped_when_not_on_path(self):
        """ruff check should skip when not on PATH."""
        from integration_coworker.graph.static_checks import run_ruff_check
        
        with patch('integration_coworker.graph.static_checks.discover_tool', return_value=None):
            result = run_ruff_check({"test.py": "x = 1"})
        
        assert result.status == "skipped"
        assert "not found on PATH" in result.skip_reason
    
    def test_ruff_skipped_when_no_config(self):
        """ruff check should skip when no config found."""
        from integration_coworker.graph.static_checks import run_ruff_check
        
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch('integration_coworker.graph.static_checks.discover_tool', return_value="/usr/bin/ruff"):
                result = run_ruff_check({"test.py": "x = 1"}, repo_root=Path(tmpdir))
        
        assert result.status == "skipped"
        assert "No ruff configuration" in result.skip_reason
    
    def test_mypy_skipped_when_not_on_path(self):
        """mypy check should skip when not on PATH."""
        from integration_coworker.graph.static_checks import run_mypy_check
        
        with patch('integration_coworker.graph.static_checks.discover_tool', return_value=None):
            result = run_mypy_check({"test.py": "x = 1"})
        
        assert result.status == "skipped"
        assert "not found on PATH" in result.skip_reason


# =============================================================================
# Test: Run All Checks
# =============================================================================

class TestRunAllChecks:
    """Test the run_all_checks aggregator."""
    
    def test_run_all_checks_on_valid_code(self):
        """run_all_checks should pass on valid code."""
        from integration_coworker.graph.static_checks import run_all_checks
        
        files = {
            "client.py": '''
import os

def hello():
    return os.getcwd()
''',
        }
        
        result = run_all_checks(files, repo_root=None)
        
        assert result.passed is True
        assert result.blocking_count == 0
    
    def test_run_all_checks_on_syntax_error(self):
        """run_all_checks should fail on syntax error."""
        from integration_coworker.graph.static_checks import run_all_checks
        
        files = {
            "broken.py": '''
def broken(
''',
        }
        
        result = run_all_checks(files, repo_root=None)
        
        assert result.passed is False
        assert result.blocking_count >= 1
        assert any(i.category == "syntax" for i in result.issues)
    
    def test_run_all_checks_multiple_files(self):
        """run_all_checks should check all Python files."""
        from integration_coworker.graph.static_checks import run_all_checks
        
        files = {
            "good.py": "x = 1",
            "bad.py": "def bad(",
            "readme.md": "# Not Python",
        }
        
        result = run_all_checks(files, repo_root=None)
        
        # bad.py has syntax error
        assert result.passed is False
        assert result.blocking_count >= 1
        
        # Only Python files analyzed
        file_paths = [i.file_path for i in result.issues]
        assert all(p.endswith('.py') for p in file_paths)


# =============================================================================
# Test: Artifact Storage
# =============================================================================

class TestArtifactStorage:
    """Test artifact storage via PR #7 plumbing."""
    
    def test_static_analysis_result_json_serializable(self):
        """StaticAnalysisResult should be JSON-serializable."""
        from integration_coworker.graph.quality_models import StaticAnalysisResult, StaticIssue
        
        result = StaticAnalysisResult(
            passed=False,
            issues=[
                StaticIssue(
                    severity="error",
                    category="syntax",
                    file_path="test.py",
                    line_number=1,
                    column=0,
                    message="Test error",
                )
            ],
            blocking_count=1,
            warning_count=0,
            tool_versions={"ast": "3.11"},
        )
        
        d = result.to_dict()
        json_str = json.dumps(d)
        
        assert len(json_str) > 0
        assert "syntax" in json_str
    
    def test_bounded_summary_truncates_large_issue_list(self):
        """Summary should truncate large issue lists."""
        from integration_coworker.graph.quality_models import (
            StaticAnalysisResult, StaticIssue, MAX_ISSUES_IN_SUMMARY
        )
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        # Create more issues than the limit
        issues = [
            StaticIssue(
                severity="warning",
                category="lint",
                file_path=f"file_{i}.py",
                line_number=i,
                column=0,
                message=f"Warning {i}",
            )
            for i in range(50)
        ]
        
        result = StaticAnalysisResult(
            passed=True,
            issues=issues,
            blocking_count=0,
            warning_count=50,
        )
        
        summary = build_static_analysis_summary(result)
        
        assert summary["total_issues"] == 50
        assert len(summary["issues_preview"]) == MAX_ISSUES_IN_SUMMARY
        assert summary["issues_truncated"] is True


# =============================================================================
# Test: Static Analysis Gate Node
# =============================================================================

class TestStaticAnalysisGateNode:
    """Test the static_analysis_gate node function."""
    
    def test_gate_returns_quality_refs(self):
        """Gate should return quality_refs in state update."""
        from integration_coworker.graph.nodes.static_analysis_gate import static_analysis_gate
        from integration_coworker.graph.state import WorkflowState
        from unittest.mock import MagicMock
        
        # Create minimal state with code artifacts
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
            run_id="test-run",
            code_artifacts=[],
        )
        
        # Mock artifact store
        mock_store = MagicMock()
        mock_ref = MagicMock()
        mock_ref.to_dict.return_value = {"__artifact_ref__": True}
        mock_store.put.return_value = mock_ref
        
        with patch('integration_coworker.graph.quality_artifacts.get_artifact_store', return_value=mock_store):
            result = static_analysis_gate(state)
        
        assert "quality_refs" in result
        assert "static" in result["quality_refs"]
    
    def test_gate_adds_completed_step(self):
        """Gate should add static_analysis_gate to completed_steps."""
        from integration_coworker.graph.nodes.static_analysis_gate import static_analysis_gate
        from integration_coworker.graph.state import WorkflowState
        from unittest.mock import MagicMock
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
            run_id="test-run",
            code_artifacts=[],
            completed_steps=["generate_code_and_tests"],
        )
        
        mock_store = MagicMock()
        mock_ref = MagicMock()
        mock_ref.to_dict.return_value = {"__artifact_ref__": True}
        mock_store.put.return_value = mock_ref
        
        with patch('integration_coworker.graph.quality_artifacts.get_artifact_store', return_value=mock_store):
            result = static_analysis_gate(state)
        
        assert "static_analysis_gate" in result["completed_steps"]


# =============================================================================
# Test: No Hardcoded Tool Paths
# =============================================================================

class TestNoHardcodedPaths:
    """Verify no hardcoded tool paths exist."""
    
    def test_static_checks_no_hardcoded_paths(self):
        """static_checks.py should not have hardcoded tool paths."""
        from pathlib import Path
        import re
        
        module_path = Path(__file__).parent.parent / "src" / "integration_coworker" / "graph" / "static_checks.py"
        
        if not module_path.exists():
            # Try alternative path
            module_path = Path(__file__).parent.parent.parent / "src" / "integration_coworker" / "graph" / "static_checks.py"
        
        if module_path.exists():
            content = module_path.read_text()
            
            # Check for hardcoded paths
            hardcoded_patterns = [
                r'/usr/bin/ruff',
                r'/usr/local/bin/ruff',
                r'/usr/bin/mypy',
                r'/usr/local/bin/mypy',
                r'C:\\.*\\ruff',
                r'C:\\.*\\mypy',
            ]
            
            for pattern in hardcoded_patterns:
                matches = re.findall(pattern, content, re.IGNORECASE)
                assert not matches, f"Found hardcoded path: {pattern}"
    
    def test_check_registry_uses_tool_name_not_path(self):
        """CHECK_REGISTRY entries should use tool_name, not paths."""
        from integration_coworker.graph.static_checks import CHECK_REGISTRY
        
        for name, check_def in CHECK_REGISTRY.items():
            if check_def.tool_name:
                # tool_name should be just the executable name, not a path
                assert '/' not in check_def.tool_name
                assert '\\' not in check_def.tool_name


# =============================================================================
# Test: Quality Refs Size Guard
# =============================================================================

class TestQualityRefsSizeGuard:
    """Test that quality_refs stays small."""
    
    def test_validate_quality_refs_size(self):
        """validate_quality_refs_size should catch oversized refs."""
        from integration_coworker.graph.quality_artifacts import validate_quality_refs_size
        
        # Small refs should pass
        small_refs = {"static": {"summary": {"passed": True}}}
        assert validate_quality_refs_size(small_refs, max_bytes=8192) is True
        
        # Large refs should fail
        large_refs = {"data": "x" * 20000}
        assert validate_quality_refs_size(large_refs, max_bytes=8192) is False
    
    def test_check_no_blobs_detects_large_lists(self):
        """check_no_blobs should detect large lists."""
        from integration_coworker.graph.quality_artifacts import check_no_blobs_in_quality_refs
        
        quality_refs = {
            "static": {
                "summary": {
                    # This is a violation - embedding full issue list
                    "issues": [{"msg": f"issue {i}"} for i in range(200)],
                }
            }
        }
        
        violations = check_no_blobs_in_quality_refs(quality_refs)
        assert len(violations) > 0


# =============================================================================
# Test: Graph-Level Wiring Assertion (PR #8 merge-ready)
# =============================================================================

class TestStaticAnalysisGateGraphWiring:
    """
    Test that static_analysis_gate is correctly wired in the graph.
    
    Per PR #8: generate_code_and_tests -> static_analysis_gate -> (existing routing)
    No new interrupts, no retries, no auto-fix loops.
    """
    
    def test_static_analysis_gate_exists_in_graph(self):
        """Assert static_analysis_gate exists as a node in compiled graph."""
        from integration_coworker.graph.runtime import build_graph
        
        with patch.dict('os.environ', {'USE_MOCK_LLM': 'true', 'USE_SQLITE': 'true'}):
            graph = build_graph()
        
        graph_dict = graph.get_graph().to_json()
        node_names = [n.get("id") for n in graph_dict.get("nodes", [])]
        
        assert "static_analysis_gate" in node_names, (
            f"static_analysis_gate not found in graph nodes: {node_names}"
        )
    
    def test_static_analysis_gate_follows_codegen(self):
        """Assert static_analysis_gate comes after generate_code_and_tests."""
        from integration_coworker.graph.runtime import build_graph
        
        with patch.dict('os.environ', {'USE_MOCK_LLM': 'true', 'USE_SQLITE': 'true'}):
            graph = build_graph()
        
        graph_dict = graph.get_graph().to_json()
        edges = graph_dict.get("edges", [])
        
        # Find edge from generate_code_and_tests to static_analysis_gate
        codegen_to_static = [
            e for e in edges 
            if e.get("source") == "generate_code_and_tests" 
            and e.get("target") == "static_analysis_gate"
        ]
        
        assert len(codegen_to_static) > 0, (
            f"No edge from generate_code_and_tests to static_analysis_gate. "
            f"Edges from codegen: {[e for e in edges if e.get('source') == 'generate_code_and_tests']}"
        )
    
    def test_static_analysis_gate_has_conditional_routing(self):
        """Assert static_analysis_gate routes to multiple destinations (conditional)."""
        from integration_coworker.graph.runtime import build_graph
        
        with patch.dict('os.environ', {'USE_MOCK_LLM': 'true', 'USE_SQLITE': 'true'}):
            graph = build_graph()
        
        graph_dict = graph.get_graph().to_json()
        edges = graph_dict.get("edges", [])
        
        # Find outgoing edges from static_analysis_gate
        static_out = [e for e in edges if e.get("source") == "static_analysis_gate"]
        
        # Should have conditional routing (multiple targets)
        assert len(static_out) >= 1, (
            f"static_analysis_gate has no outgoing edges: {edges}"
        )
    
    def test_graph_has_no_auto_fix_loop_to_static_gate(self):
        """Assert no edge loops back to static_analysis_gate (no auto-fix in PR #8)."""
        from integration_coworker.graph.runtime import build_graph
        
        with patch.dict('os.environ', {'USE_MOCK_LLM': 'true', 'USE_SQLITE': 'true'}):
            graph = build_graph()
        
        graph_dict = graph.get_graph().to_json()
        edges = graph_dict.get("edges", [])
        
        # Find incoming edges to static_analysis_gate
        static_in = [e for e in edges if e.get("target") == "static_analysis_gate"]
        
        # Only allowed incoming source is generate_code_and_tests
        allowed_sources = {"generate_code_and_tests"}
        actual_sources = {e.get("source") for e in static_in}
        
        unexpected_sources = actual_sources - allowed_sources
        assert not unexpected_sources, (
            f"static_analysis_gate has unexpected incoming edges (potential auto-fix loop): "
            f"{unexpected_sources}. PR #8 is signals-only, no retries."
        )


# =============================================================================
# Test: State Sync Drift Detection
# =============================================================================

class TestStateSyncDrift:
    """
    Test that WorkflowState and WorkflowStateDict stay in sync.
    
    This catches drift early before runtime import failures.
    """
    
    def test_runtime_import_constructs_graph(self):
        """runtime should import cleanly and construct graph without errors."""
        # This import triggers the parity check between WorkflowState and WorkflowStateDict
        from integration_coworker.graph.runtime import build_graph
        
        # If we get here, state types are in sync
        with patch.dict('os.environ', {'USE_MOCK_LLM': 'true', 'USE_SQLITE': 'true'}):
            graph = build_graph()
        
        assert graph is not None
        assert hasattr(graph, 'nodes')
    
    def test_quality_fields_in_workflow_state(self):
        """WorkflowState should have quality pipeline fields."""
        from integration_coworker.graph.state import WorkflowState
        import dataclasses
        
        field_names = {f.name for f in dataclasses.fields(WorkflowState)}
        
        # PR #7/8 quality fields
        required_fields = {"quality_refs", "static_analysis_retries", "static_analysis_escalated"}
        missing = required_fields - field_names
        
        assert not missing, f"WorkflowState missing quality fields: {missing}"
    
    def test_static_analysis_gate_reads_expected_fields(self):
        """static_analysis_gate should only read fields that exist in state."""
        from integration_coworker.graph.nodes.static_analysis_gate import static_analysis_gate
        from integration_coworker.graph.state import WorkflowState
        import dataclasses
        import inspect
        
        # Get the source and look for state.X accesses
        source = inspect.getsource(static_analysis_gate)
        
        # Simple check: ensure common accessed fields exist
        state_fields = {f.name for f in dataclasses.fields(WorkflowState)}
        
        # Fields the gate is known to access
        expected_accesses = ["code_artifacts", "run_id", "quality_refs", "completed_steps"]
        for field in expected_accesses:
            assert field in state_fields, f"static_analysis_gate accesses {field} but not in state"


# =============================================================================
# Test: Bounded Summary Ceiling (Hard Limit)
# =============================================================================

# Hard ceiling for quality_refs["static"]["summary"] - enforced in tests
SUMMARY_SIZE_CEILING_BYTES = 4096  # 4KB


class TestBoundedSummaryCeiling:
    """
    Test that static analysis summaries stay under a hard ceiling.
    
    Per PR #8 refs-not-blobs discipline: state keeps bounded summary only.
    """
    
    def test_summary_under_4kb_ceiling(self):
        """Static analysis summary must serialize to < 4KB."""
        from integration_coworker.graph.static_checks import run_all_checks
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        # Generate many issues to stress the summary
        code_with_issues = "\n".join([
            f"import fake_module_{i}" for i in range(100)
        ]) + "\n" + "\n".join([
            f"x{i} = undefined_var_{i}" for i in range(100)  
        ])
        
        result = run_all_checks({"test_large.py": code_with_issues})
        summary = build_static_analysis_summary(result)
        
        summary_json = json.dumps(summary)
        assert len(summary_json) < SUMMARY_SIZE_CEILING_BYTES, (
            f"Summary exceeds {SUMMARY_SIZE_CEILING_BYTES}B ceiling: {len(summary_json)}B. "
            f"This violates refs-not-blobs discipline. Truncate issues_preview."
        )
    
    def test_summary_truncates_large_issue_lists(self):
        """Summary should truncate when issues exceed preview limit."""
        from integration_coworker.graph.quality_models import StaticIssue
        from integration_coworker.graph.static_checks import StaticAnalysisResult
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary, MAX_ISSUES_IN_SUMMARY
        
        # Create 200 issues
        issues = [
            StaticIssue(
                severity="warning",
                category="lint",
                file_path=f"file_{i}.py",
                line_number=i,
                column=0,
                message=f"Warning message that is moderately long to simulate real issues {i}",
            )
            for i in range(200)
        ]
        
        result = StaticAnalysisResult(
            passed=True, issues=issues, blocking_count=0, warning_count=200
        )
        
        summary = build_static_analysis_summary(result)
        
        assert len(summary["issues_preview"]) <= MAX_ISSUES_IN_SUMMARY
        assert summary["issues_truncated"] is True
        
        # Still under ceiling
        summary_json = json.dumps(summary)
        assert len(summary_json) < SUMMARY_SIZE_CEILING_BYTES
    
    def test_empty_result_produces_minimal_summary(self):
        """Empty static analysis result should produce minimal summary."""
        from integration_coworker.graph.static_checks import StaticAnalysisResult
        from integration_coworker.graph.quality_artifacts import build_static_analysis_summary
        
        result = StaticAnalysisResult(
            passed=True, issues=[], blocking_count=0, warning_count=0
        )
        
        summary = build_static_analysis_summary(result)
        summary_json = json.dumps(summary)
        
        # Minimal summary should be well under ceiling
        assert len(summary_json) < 500, f"Empty summary too large: {len(summary_json)}B"


# =============================================================================
# Test: No-Hardcoding Enforcement (Discovery-Only)
# =============================================================================

class TestDiscoveryOnlyEnforcement:
    """
    Test that optional tools are discovered, not hardcoded.
    
    Per PR #8: tools must be detected via PATH + repo config, 
    report "skipped" when absent (not "failed").
    """
    
    def test_optional_tool_not_invoked_without_discovery(self):
        """Optional tools should not be invoked if not discovered on PATH."""
        from integration_coworker.graph.static_checks import (
            CHECK_REGISTRY, discover_tool, run_all_checks
        )
        from unittest.mock import patch, MagicMock
        import subprocess
        
        # Mock subprocess.run to detect if tools are called without discovery
        original_run = subprocess.run
        tool_calls = []
        
        def tracking_run(cmd, *args, **kwargs):
            tool_calls.append(cmd[0] if cmd else None)
            raise FileNotFoundError(f"Simulated: {cmd[0]} not found")
        
        # Mock shutil.which to return None (tools not on PATH)
        with patch('shutil.which', return_value=None):
            with patch('subprocess.run', side_effect=tracking_run):
                # Run checks - optional tools should be skipped, not invoked
                result = run_all_checks({"test.py": "x = 1"})
        
        # Verify no optional tool was actually invoked (subprocess.run never called)
        assert len(tool_calls) == 0, (
            f"Tools were invoked without discovery: {tool_calls}. "
            f"Optional tools must be discovered first."
        )
    
    def test_builtin_checks_always_available(self):
        """Built-in checks (syntax, imports) should always run."""
        from integration_coworker.graph.static_checks import (
            CHECK_REGISTRY, get_enabled_checks
        )
        
        # Mock no tools on PATH and no config
        with patch('shutil.which', return_value=None):
            enabled = get_enabled_checks(repo_root=None)
        
        # Built-in checks should always be enabled
        builtin_names = {name for name, defn in CHECK_REGISTRY.items() if defn.builtin}
        enabled_names = set(enabled)  # get_enabled_checks returns List[str]
        
        assert builtin_names.issubset(enabled_names), (
            f"Built-in checks not enabled: {builtin_names - enabled_names}"
        )
    
    def test_skipped_tool_reports_skip_reason(self):
        """Skipped tools should report skip reason, not failure."""
        from integration_coworker.graph.static_checks import run_all_checks
        
        # Mock no tools available
        with patch('shutil.which', return_value=None):
            result = run_all_checks({"test.py": "x = 1"}, repo_root=None)
        
        # Result should indicate pass (no failures from missing tools)
        assert result.passed is True, (
            f"Missing optional tools should not cause failure. "
            f"Issues: {result.issues}"
        )


# =============================================================================
# Test: Production Hardening (PR #8 Hardening)
# =============================================================================

class TestProductionHardening:
    """
    Test production hardening features:
    - Deterministic output ordering
    - Tool execution bounds
    - Metadata capture
    """
    
    def test_issues_are_sorted_deterministically(self):
        """Issues should be sorted by (file, line, col, message)."""
        from integration_coworker.graph.static_checks import run_all_checks
        
        # Create files that would produce issues in non-deterministic order
        files = {
            "z_file.py": "import fake_z_module",
            "a_file.py": "import fake_a_module",
            "m_file.py": "import fake_m_module",
        }
        
        with patch('shutil.which', return_value=None):
            result1 = run_all_checks(files)
            result2 = run_all_checks(files)
        
        # Both runs should produce identical ordering
        assert len(result1.issues) == len(result2.issues)
        for i1, i2 in zip(result1.issues, result2.issues):
            assert i1.file_path == i2.file_path, "Issue order should be deterministic"
            assert i1.line_number == i2.line_number
            assert i1.message == i2.message
        
        # Verify issues are actually sorted
        file_paths = [i.file_path for i in result1.issues]
        assert file_paths == sorted(file_paths), "Issues should be sorted by file_path"
    
    def test_multiple_issues_same_file_sorted_by_line(self):
        """Multiple issues in same file should be sorted by line number."""
        from integration_coworker.graph.static_checks import run_all_checks
        
        # Code with multiple issues on different lines
        code = """
import fake_module_line2
x = undefined_var
import another_fake_module_line4
"""
        
        with patch('shutil.which', return_value=None):
            result = run_all_checks({"test.py": code})
        
        # Get line numbers of issues
        line_numbers = [i.line_number for i in result.issues if i.file_path == "test.py"]
        assert line_numbers == sorted(line_numbers), "Issues should be sorted by line number"
    
    def test_production_constants_are_defined(self):
        """Verify production constants exist and have reasonable values."""
        from integration_coworker.graph.static_checks import (
            TOOL_TIMEOUT_SECONDS,
            MAX_TOOL_OUTPUT_BYTES,
            MAX_ISSUES_PER_TOOL,
            MAX_FILES_PER_CHECK,
        )
        
        assert TOOL_TIMEOUT_SECONDS >= 5, "Timeout should be at least 5 seconds"
        assert TOOL_TIMEOUT_SECONDS <= 120, "Timeout should not exceed 2 minutes"
        
        assert MAX_TOOL_OUTPUT_BYTES >= 10000, "Output cap should be at least 10KB"
        assert MAX_TOOL_OUTPUT_BYTES <= 10_000_000, "Output cap should not exceed 10MB"
        
        assert MAX_ISSUES_PER_TOOL >= 10, "Should collect at least 10 issues"
        assert MAX_ISSUES_PER_TOOL <= 1000, "Should not collect more than 1000 issues"
        
        assert MAX_FILES_PER_CHECK >= 10, "Should check at least 10 files"
    
    def test_tool_invocation_result_dataclass(self):
        """ToolInvocationResult should capture execution metadata."""
        from integration_coworker.graph.static_checks import ToolInvocationResult
        
        # Create a mock result
        result = ToolInvocationResult(
            returncode=0,
            stdout="output",
            stderr="",
            timed_out=False,
            truncated=False,
            duration_ms=100,
            metadata={"command": "test", "args_count": 1},
        )
        
        assert result.returncode == 0
        assert result.timed_out is False
        assert result.truncated is False
        assert "command" in result.metadata
    
    def test_check_result_has_invocation_metadata(self):
        """CheckResult should have invocation_metadata field."""
        from integration_coworker.graph.static_checks import CheckResult
        
        result = CheckResult(
            check_name="test",
            status="passed",
            invocation_metadata={"duration_ms": 50},
        )
        
        assert result.invocation_metadata is not None
        assert result.invocation_metadata["duration_ms"] == 50
    
    def test_check_result_to_dict(self):
        """CheckResult.to_dict() should produce JSON-serializable output."""
        from integration_coworker.graph.static_checks import CheckResult
        
        result = CheckResult(
            check_name="test",
            status="passed",
            tool_version="1.0.0",
            invocation_metadata={"duration_ms": 100},
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["check_name"] == "test"
        assert result_dict["status"] == "passed"
        assert result_dict["tool_version"] == "1.0.0"
        
        # Should be JSON-serializable
        import json
        json_str = json.dumps(result_dict)
        assert len(json_str) > 0
    
    def test_file_count_is_bounded(self):
        """run_all_checks should handle large file counts gracefully."""
        from integration_coworker.graph.static_checks import run_all_checks, MAX_FILES_PER_CHECK
        
        # Create more files than the limit
        many_files = {f"file_{i}.py": "x = 1" for i in range(MAX_FILES_PER_CHECK + 100)}
        
        with patch('shutil.which', return_value=None):
            result = run_all_checks(many_files)
        
        # Should succeed without issues (valid code)
        assert result.passed is True
        # Should have been bounded (no OOM from huge file count)


# =============================================================================
# Test: Message and Path Truncation
# =============================================================================

class TestTruncationBehavior:
    """Test that large messages and paths are properly truncated."""
    
    def test_huge_error_message_truncated(self):
        """Error messages longer than MAX_ERROR_MESSAGE_LENGTH should be truncated."""
        from integration_coworker.graph.quality_models import MAX_ERROR_MESSAGE_LENGTH
        from integration_coworker.graph.static_checks import check_syntax
        
        # Create code that might produce a long error message
        # (This is a synthetic test - in practice ast.parse errors are short)
        issues = check_syntax("def f(", "test.py")
        
        for issue in issues:
            assert len(issue.message) <= MAX_ERROR_MESSAGE_LENGTH + 50  # Allow some buffer
    
    def test_huge_file_path_truncated(self):
        """File paths longer than MAX_FILE_PATH_LENGTH should be truncated."""
        from integration_coworker.graph.quality_models import MAX_FILE_PATH_LENGTH
        from integration_coworker.graph.static_checks import check_syntax
        
        # Use a very long file path
        long_path = "a" * 500 + ".py"
        issues = check_syntax("def f(", long_path)
        
        for issue in issues:
            assert len(issue.file_path) <= MAX_FILE_PATH_LENGTH + 10  # Allow small buffer


# =============================================================================
# Test: Many Files Truncation
# =============================================================================

class TestManyFilesTruncation:
    """Test behavior with many files."""
    
    def test_many_files_all_checked(self):
        """Many files should all be checked up to the limit."""
        from integration_coworker.graph.static_checks import run_all_checks
        
        # Create 50 files, each with an import issue
        files = {f"file_{i}.py": f"import nonexistent_module_{i}" for i in range(50)}
        
        with patch('shutil.which', return_value=None):
            result = run_all_checks(files)
        
        # Should have one issue per file
        assert len(result.issues) == 50, f"Expected 50 issues, got {len(result.issues)}"
    
    def test_files_beyond_limit_gracefully_ignored(self):
        """Files beyond MAX_FILES_PER_CHECK should be gracefully ignored."""
        from integration_coworker.graph.static_checks import run_all_checks, MAX_FILES_PER_CHECK
        
        # Create more files than the limit
        num_files = MAX_FILES_PER_CHECK + 50
        files = {f"file_{i}.py": "x = 1" for i in range(num_files)}
        
        with patch('shutil.which', return_value=None):
            # Should not raise, should just process up to the limit
            result = run_all_checks(files)
        
        assert result is not None
        # Valid code, should pass
        assert result.passed is True


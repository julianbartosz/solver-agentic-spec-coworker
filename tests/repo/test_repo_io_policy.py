"""
Regression tests for Repo IO policy enforcement.

P1 Exit Criterion: Prove writes outside allowlist fail loudly and are audited.

Tests:
1. Writes outside allowlist raise PathPolicyViolation
2. Path traversal (.. escapes) are blocked
3. Denylist patterns are enforced
4. Symlink escapes are blocked
5. Size limits are enforced
6. All violations are audited
"""
import os
import pytest
import tempfile
from pathlib import Path
from unittest import mock

from integration_coworker.repo.io import (
    RepoIO,
    RepoIOConfig,
    PathPolicyViolation,
    SizeLimitExceeded,
    SymlinkPolicyViolation,
    repo_io_context,
    get_repo_io,
)


@pytest.fixture
def temp_repo():
    """Create a temporary repository structure for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_root = Path(tmpdir)
        
        # Create allowed directories
        (repo_root / "src").mkdir()
        (repo_root / "src" / "module").mkdir()
        (repo_root / "tests").mkdir()
        (repo_root / "docs").mkdir()
        
        # Create some files
        (repo_root / "src" / "main.py").write_text("# Main module")
        (repo_root / "src" / "module" / "helper.py").write_text("# Helper")
        (repo_root / "tests" / "test_main.py").write_text("# Tests")
        (repo_root / "README.md").write_text("# Project")
        
        # Create forbidden directories (outside allowlist)
        (repo_root / "secrets").mkdir()
        (repo_root / "secrets" / "api.key").write_text("SUPER_SECRET_KEY")
        (repo_root / ".git").mkdir()
        (repo_root / ".git" / "config").write_text("[core]")
        
        yield repo_root


@pytest.fixture
def restricted_config():
    """Create a restricted IO config with allowlist."""
    return RepoIOConfig(
        allowlist_roots=["src/", "tests/", "docs/"],
        max_write_size_bytes=1024,  # 1KB per file
        max_total_write_bytes=4096,  # 4KB total
        max_files_written=3,
        symlink_policy="deny",
    )


class TestAllowlistEnforcement:
    """Test that writes outside allowlist fail loudly."""
    
    def test_write_outside_allowlist_raises_policy_violation(self, temp_repo, restricted_config):
        """
        P1 REGRESSION: Write to path outside allowlist MUST raise PathPolicyViolation.
        
        This is the core security test - ensures generated code cannot write
        to arbitrary locations like secrets/ or .git/
        """
        io = RepoIO(temp_repo, "test-run-001", restricted_config)
        
        # Attempt to write to secrets/ (outside allowlist)
        with pytest.raises(PathPolicyViolation) as exc_info:
            io.write_text("secrets/new_secret.txt", "leaked data")
        
        # Verify exception details
        assert "allowlist" in str(exc_info.value).lower()
        # Path is resolved to absolute path
        assert "secrets" in exc_info.value.path
        assert "new_secret.txt" in exc_info.value.path
        assert exc_info.value.run_id == "test-run-001"
        
        # Verify violation was audited
        summary = io.get_audit_summary()
        assert summary.policy_violations >= 1
    
    def test_write_to_gitdir_blocked(self, temp_repo, restricted_config):
        """Writes to .git/ are always blocked by denylist."""
        io = RepoIO(temp_repo, "test-run-002", restricted_config)
        
        with pytest.raises(PathPolicyViolation) as exc_info:
            io.write_text(".git/hooks/pre-commit", "#!/bin/sh\nrm -rf /")
        
        # Should be blocked by denylist pattern, not just allowlist
        assert ".git" in str(exc_info.value) or "denylist" in str(exc_info.value).lower()
    
    def test_write_inside_allowlist_succeeds(self, temp_repo, restricted_config):
        """Writes to allowed paths should succeed."""
        io = RepoIO(temp_repo, "test-run-003", restricted_config)
        
        # Write to src/ (in allowlist)
        bytes_written = io.write_text("src/generated.py", "# Generated code")
        
        assert bytes_written > 0
        assert (temp_repo / "src" / "generated.py").exists()
        
        summary = io.get_audit_summary()
        assert summary.files_written == 1
        assert summary.policy_violations == 0
    
    def test_read_outside_allowlist_blocked(self, temp_repo, restricted_config):
        """Reads from outside allowlist are also blocked."""
        io = RepoIO(temp_repo, "test-run-004", restricted_config)
        
        with pytest.raises(PathPolicyViolation):
            io.read_text("secrets/api.key")
        
        summary = io.get_audit_summary()
        assert summary.policy_violations >= 1


class TestPathTraversalPrevention:
    """Test that path traversal attacks are blocked."""
    
    def test_dotdot_escape_blocked(self, temp_repo, restricted_config):
        """
        P1 REGRESSION: Path traversal with .. MUST be blocked.
        
        Prevents attacks like: src/../../../etc/passwd
        """
        io = RepoIO(temp_repo, "test-run-005", restricted_config)
        
        # Try to escape repo root using ..
        with pytest.raises(PathPolicyViolation) as exc_info:
            io.write_text("src/../../../etc/malicious", "pwned")
        
        assert "escapes" in str(exc_info.value).lower() or "root" in str(exc_info.value).lower()
        
        summary = io.get_audit_summary()
        assert summary.policy_violations >= 1
    
    def test_dotdot_within_repo_but_outside_allowlist(self, temp_repo, restricted_config):
        """Path traversal within repo but to non-allowed directory."""
        io = RepoIO(temp_repo, "test-run-006", restricted_config)
        
        # src/../secrets is still in repo, but outside allowlist
        with pytest.raises(PathPolicyViolation):
            io.write_text("src/../secrets/leaked.txt", "data")
    
    def test_absolute_path_escape_blocked(self, temp_repo, restricted_config):
        """Absolute paths outside repo are blocked."""
        io = RepoIO(temp_repo, "test-run-007", restricted_config)
        
        with pytest.raises(PathPolicyViolation):
            io.write_text("/etc/passwd", "root:x:0:0:")


class TestDenylistPatterns:
    """Test denylist pattern enforcement."""
    
    def test_node_modules_blocked(self, temp_repo):
        """node_modules/** is denied by default config."""
        config = RepoIOConfig()  # Default config
        io = RepoIO(temp_repo, "test-run-008", config)
        
        # Create node_modules for test
        (temp_repo / "node_modules").mkdir()
        (temp_repo / "node_modules" / "package").mkdir()
        
        with pytest.raises(PathPolicyViolation) as exc_info:
            io.write_text("node_modules/package/index.js", "malicious")
        
        assert "denylist" in str(exc_info.value).lower()
    
    def test_env_files_blocked(self, temp_repo):
        """*.env files are denied by default."""
        config = RepoIOConfig()
        io = RepoIO(temp_repo, "test-run-009", config)
        
        with pytest.raises(PathPolicyViolation):
            io.write_text(".env", "API_KEY=secret")
    
    def test_secrets_directory_blocked(self, temp_repo):
        """**/secrets/** is denied."""
        config = RepoIOConfig()
        io = RepoIO(temp_repo, "test-run-010", config)
        
        with pytest.raises(PathPolicyViolation):
            io.write_text("secrets/new.key", "secret data")


class TestSymlinkPolicy:
    """Test symlink policy enforcement."""
    
    def test_symlink_deny_policy(self, temp_repo):
        """With deny policy, symlinks raise SymlinkPolicyViolation."""
        config = RepoIOConfig(symlink_policy="deny", allowlist_roots=[])
        io = RepoIO(temp_repo, "test-run-011", config)
        
        # Create a symlink
        target = temp_repo / "src" / "main.py"
        link = temp_repo / "src" / "link_to_main.py"
        link.symlink_to(target)
        
        with pytest.raises(SymlinkPolicyViolation):
            io.read_text("src/link_to_main.py")
    
    def test_symlink_escape_blocked(self, temp_repo):
        """Symlinks pointing outside repo are blocked with follow_internal."""
        config = RepoIOConfig(symlink_policy="follow_internal", allowlist_roots=[])
        io = RepoIO(temp_repo, "test-run-012", config)
        
        # Create symlink pointing outside repo
        external_target = Path("/etc/passwd")
        if external_target.exists():
            link = temp_repo / "src" / "external_link"
            try:
                link.symlink_to(external_target)
                
                # Should raise either SymlinkPolicyViolation or PathPolicyViolation
                # depending on when the escape is detected
                with pytest.raises((SymlinkPolicyViolation, PathPolicyViolation)):
                    io.read_text("src/external_link")
            except OSError:
                pytest.skip("Cannot create symlink (permission denied)")
    
    def test_internal_symlink_allowed(self, temp_repo):
        """Symlinks within repo are allowed with follow_internal."""
        config = RepoIOConfig(symlink_policy="follow_internal", allowlist_roots=[])
        io = RepoIO(temp_repo, "test-run-013", config)
        
        # Create internal symlink
        target = temp_repo / "src" / "main.py"
        link = temp_repo / "src" / "link_to_main.py"
        link.symlink_to(target)
        
        content = io.read_text("src/link_to_main.py")
        assert content == "# Main module"


class TestSizeLimits:
    """Test size limit enforcement."""
    
    def test_per_file_size_limit(self, temp_repo, restricted_config):
        """
        P1 REGRESSION: Files exceeding per-file limit raise SizeLimitExceeded.
        """
        io = RepoIO(temp_repo, "test-run-014", restricted_config)
        
        # Config has max_write_size_bytes=1024
        large_content = "x" * 2000  # 2KB, exceeds 1KB limit
        
        with pytest.raises(SizeLimitExceeded) as exc_info:
            io.write_text("src/large.py", large_content)
        
        assert exc_info.value.limit_type == "max_write_size_bytes"
        assert exc_info.value.limit == 1024
    
    def test_total_write_limit(self, temp_repo, restricted_config):
        """Total write limit across all files."""
        # Use a custom config with larger per-file but smaller total
        config = RepoIOConfig(
            allowlist_roots=["src/", "tests/", "docs/"],
            max_write_size_bytes=2000,  # 2KB per file
            max_total_write_bytes=4000,  # 4KB total
            max_files_written=10,
        )
        io = RepoIO(temp_repo, "test-run-015", config)
        
        # Write multiple files that together exceed limit
        io.write_text("src/file1.py", "x" * 1000)  # 1KB
        io.write_text("src/file2.py", "x" * 1000)  # 2KB total
        io.write_text("src/file3.py", "x" * 1000)  # 3KB total
        
        # This should exceed 4KB limit
        with pytest.raises(SizeLimitExceeded) as exc_info:
            io.write_text("src/file4.py", "x" * 1500)  # Would be ~4.5KB total
        
        assert exc_info.value.limit_type == "max_total_write_bytes"
    
    def test_max_files_limit(self, temp_repo, restricted_config):
        """Max files written limit."""
        io = RepoIO(temp_repo, "test-run-016", restricted_config)
        
        # Config has max_files_written=3
        io.write_text("src/f1.py", "1")
        io.write_text("src/f2.py", "2")
        io.write_text("src/f3.py", "3")
        
        # 4th file should fail
        with pytest.raises(SizeLimitExceeded) as exc_info:
            io.write_text("src/f4.py", "4")
        
        assert exc_info.value.limit_type == "max_files_written"
    
    def test_read_size_limit(self, temp_repo):
        """Large file reads are blocked."""
        config = RepoIOConfig(max_read_size_bytes=100)
        io = RepoIO(temp_repo, "test-run-017", config)
        
        # Create large file
        (temp_repo / "src" / "large.txt").write_text("x" * 200)
        
        with pytest.raises(SizeLimitExceeded):
            io.read_text("src/large.txt")


class TestAuditLogging:
    """Test that all operations and violations are audited."""
    
    def test_successful_operations_audited(self, temp_repo, restricted_config):
        """Successful operations are recorded in audit."""
        io = RepoIO(temp_repo, "test-run-018", restricted_config)
        
        io.read_text("src/main.py")
        io.write_text("src/new.py", "# New file")
        io.exists("src/main.py")
        io.glob("src/*.py")
        
        summary = io.get_audit_summary()
        assert summary.operations_count >= 4
        assert summary.files_read >= 1
        assert summary.files_written >= 1
    
    def test_violations_audited(self, temp_repo, restricted_config):
        """Policy violations are recorded in audit."""
        io = RepoIO(temp_repo, "test-run-019", restricted_config)
        
        # Trigger multiple violations
        try:
            io.write_text("secrets/bad.txt", "data")
        except PathPolicyViolation:
            pass
        
        try:
            io.read_text(".git/config")
        except PathPolicyViolation:
            pass
        
        summary = io.get_audit_summary()
        assert summary.policy_violations >= 2
    
    def test_audit_records_contain_details(self, temp_repo, restricted_config):
        """Audit records contain operation details."""
        io = RepoIO(temp_repo, "test-run-020", restricted_config)
        
        io.write_text("src/test.py", "# Test")
        
        records = io.get_audit_records()
        assert len(records) >= 1
        
        write_record = next(r for r in records if r.operation == "write_text")
        assert write_record.success is True
        assert write_record.bytes_transferred > 0
        assert "src/test.py" in write_record.path


class TestContextManager:
    """Test context manager functionality."""
    
    def test_context_manager_sets_global(self, temp_repo, restricted_config):
        """Context manager sets and clears global context."""
        assert get_repo_io() is None
        
        with repo_io_context(temp_repo, "test-run-021", restricted_config) as io:
            assert get_repo_io() is io
            io.read_text("src/main.py")
        
        assert get_repo_io() is None
    
    def test_context_manager_logs_summary(self, temp_repo, restricted_config, caplog):
        """Context manager logs audit summary on exit."""
        import logging
        caplog.set_level(logging.INFO)
        
        with repo_io_context(temp_repo, "test-run-022", restricted_config) as io:
            io.read_text("src/main.py")
            io.write_text("src/out.py", "# Out")
        
        # Check summary was logged
        assert any("RepoIO summary" in record.message for record in caplog.records)


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_empty_allowlist_allows_all(self, temp_repo):
        """Empty allowlist means no allowlist restriction."""
        config = RepoIOConfig(allowlist_roots=[])  # No allowlist
        io = RepoIO(temp_repo, "test-run-023", config)
        
        # This would fail with allowlist, but passes without
        # (still blocked by denylist though)
        content = io.read_text("README.md")
        assert "# Project" in content
    
    def test_nested_allowlist_paths(self, temp_repo, restricted_config):
        """Deeply nested paths within allowlist work."""
        io = RepoIO(temp_repo, "test-run-024", restricted_config)
        
        (temp_repo / "src" / "module" / "sub").mkdir(parents=True)
        
        io.write_text("src/module/sub/deep.py", "# Deep")
        assert (temp_repo / "src" / "module" / "sub" / "deep.py").exists()
    
    def test_binary_operations(self, temp_repo, restricted_config):
        """Binary read/write operations work."""
        io = RepoIO(temp_repo, "test-run-025", restricted_config)
        
        binary_data = b"\x00\x01\x02\x03"
        io.write_bytes("src/data.bin", binary_data)
        
        read_back = io.read_bytes("src/data.bin")
        assert read_back == binary_data
    
    def test_is_file_is_dir_with_policy(self, temp_repo, restricted_config):
        """is_file and is_dir respect policy."""
        io = RepoIO(temp_repo, "test-run-026", restricted_config)
        
        # Allowed paths
        assert io.is_file("src/main.py") is True
        assert io.is_dir("src") is True
        
        # Denied paths return False (not exception)
        assert io.is_file("secrets/api.key") is False
        assert io.is_dir("secrets") is False
    
    def test_grep_respects_policy(self, temp_repo, restricted_config):
        """Grep operations respect path policy."""
        io = RepoIO(temp_repo, "test-run-027", restricted_config)
        
        # Search in allowed directory
        matches = io.grep("Main", path="src", file_pattern="*.py")
        assert len(matches) >= 1
        assert any("main.py" in m["file"] for m in matches)
        
        # Grep on root with allowlist should not be allowed directly
        # since "." is not in the allowlist. The grep would need to
        # search within allowed subdirectories.
        # Let's verify that searching secrets explicitly fails
        with pytest.raises(PathPolicyViolation):
            io.grep("SECRET", path="secrets", recursive=True)


class TestRegressionP1Criterion:
    """
    P1 Exit Criterion Test Suite:
    Prove writes outside allowlist fail loudly and are audited.
    """
    
    def test_p1_regression_full_scenario(self, temp_repo, restricted_config):
        """
        FULL P1 REGRESSION TEST:
        
        Scenario: A code generation node tries to write to multiple paths,
        some allowed, some denied. All violations must be caught and audited.
        """
        io = RepoIO(temp_repo, "p1-regression-final", restricted_config)
        
        # Track expected violations
        expected_violations = 0
        
        # 1. ALLOWED: Write to src/ (should succeed)
        io.write_text("src/client.py", "# Generated client")
        assert (temp_repo / "src" / "client.py").exists()
        
        # 2. DENIED: Write to secrets/ (should fail + audit)
        expected_violations += 1
        with pytest.raises(PathPolicyViolation):
            io.write_text("secrets/leaked.txt", "LEAKED!")
        
        # 3. DENIED: Write to .git/ (should fail + audit)
        expected_violations += 1
        with pytest.raises(PathPolicyViolation):
            io.write_text(".git/hooks/evil", "#!/bin/sh\nrm -rf /")
        
        # 4. DENIED: Path traversal escape (should fail + audit)
        expected_violations += 1
        with pytest.raises(PathPolicyViolation):
            io.write_text("src/../../etc/passwd", "pwned")
        
        # 5. ALLOWED: Write to tests/ (should succeed)
        io.write_text("tests/test_client.py", "# Generated test")
        assert (temp_repo / "tests" / "test_client.py").exists()
        
        # 6. DENIED: Write outside all allowlist roots
        expected_violations += 1
        with pytest.raises(PathPolicyViolation):
            io.write_text("config/settings.yaml", "bad: true")
        
        # VERIFY AUDIT
        summary = io.get_audit_summary()
        
        # Must have recorded all violations
        assert summary.policy_violations >= expected_violations, (
            f"Expected at least {expected_violations} violations, got {summary.policy_violations}"
        )
        
        # Must have recorded successful writes
        assert summary.files_written >= 2, "Should have 2 successful writes"
        
        # Audit must include run_id for traceability
        assert summary.run_id == "p1-regression-final"
        
        # Log the summary for manual verification
        print(f"\nP1 Regression Test Summary:")
        print(f"  Run ID: {summary.run_id}")
        print(f"  Files written: {summary.files_written}")
        print(f"  Bytes written: {summary.bytes_written}")
        print(f"  Policy violations: {summary.policy_violations}")
        print(f"  Total operations: {summary.operations_count}")


class TestGraphNodeEnforcement:
    """
    P1 Enforcement Test: Grep-based check that graph nodes use repo/io.py.
    
    Exit criterion: No graph node contains raw filesystem writes.
    
    Allowed patterns:
    - io.write_text(), io.write_bytes(), io.copy_file() 
    - Fallback else branches (for dry-run mode)
    - Import statements
    
    Forbidden patterns (outside else blocks):
    - .write_text(
    - .write_bytes(
    - open(..., 'w')
    - shutil.copy, shutil.move, shutil.rmtree
    - os.remove, os.unlink
    - Path(...).write_
    """
    
    def test_no_direct_writes_in_graph_nodes(self):
        """
        Scan all graph node files to ensure writes go through repo/io.py.
        
        This test enforces the P1 boundary - all file writes in graph nodes
        must use the RepoIO interface, with fallbacks only in else blocks.
        
        Exclusions:
        - Temporary file operations (tempfile module)
        - else: fallback blocks in allowed files
        - Import statements
        """
        import re
        from pathlib import Path
        
        # Find the graph nodes directory
        src_root = Path(__file__).parent.parent.parent / "src" / "integration_coworker"
        nodes_dir = src_root / "graph" / "nodes"
        
        assert nodes_dir.exists(), f"Nodes directory not found: {nodes_dir}"
        
        # Patterns that indicate direct filesystem writes TO REPO
        # These are FORBIDDEN outside of "else:" fallback blocks
        forbidden_patterns = [
            r'(?<!io\.)write_text\(',       # .write_text( not preceded by io.
            r'(?<!io\.)write_bytes\(',      # .write_bytes( not preceded by io.
            r'(?<!io\.)copy_file\(',        # .copy_file( not preceded by io.
            r'shutil\.copy[2]?\(',          # shutil.copy or shutil.copy2
            r'shutil\.move\(',              # shutil.move
            r'shutil\.rmtree\(',            # shutil.rmtree
            r'os\.remove\(',                # os.remove
            r'os\.unlink\(',                # os.unlink
            r'open\([^)]+["\']w',           # open(..., 'w' or "w"
        ]
        
        # Files that are explicitly allowed to have fallbacks
        # (they use `if io: ... else: ...` pattern)
        files_with_allowed_fallbacks = {
            "apply_repo_integration_changes.py",  # Has if io: / else: fallbacks
        }
        
        # Patterns that indicate temp file operations (not repo writes)
        # These are ALLOWED since they operate on temp directories, not the repo
        temp_file_patterns = [
            r'temp_path',           # Operating on tempfile-created path
            r'tmp_path',            # Alternative temp path variable
            r'tempfile\.',          # Direct tempfile module usage
            r'NamedTemporaryFile',  # tempfile.NamedTemporaryFile
        ]
        
        violations = []
        
        for py_file in nodes_dir.glob("*.py"):
            if py_file.name.startswith("__"):
                continue  # Skip __init__.py, __pycache__, etc.
            
            content = py_file.read_text()
            lines = content.split("\n")
            
            # Track if we're in an "else:" block (for fallback allowance)
            in_else_block = False
            else_indent = 0
            
            for line_num, line in enumerate(lines, 1):
                stripped = line.strip()
                current_indent = len(line) - len(line.lstrip())
                
                # Track else blocks for fallback pattern
                if stripped.startswith("else:"):
                    in_else_block = True
                    else_indent = current_indent
                elif in_else_block and current_indent <= else_indent and stripped:
                    in_else_block = False
                
                # Skip import lines
                if stripped.startswith("import ") or stripped.startswith("from "):
                    continue
                
                # Skip comments
                if stripped.startswith("#"):
                    continue
                
                # Skip temp file operations (not repo writes)
                is_temp_op = any(re.search(p, line) for p in temp_file_patterns)
                if is_temp_op:
                    continue
                
                # Check for forbidden patterns
                for pattern in forbidden_patterns:
                    matches = list(re.finditer(pattern, line))
                    for match in matches:
                        # Allow if in else block AND file is in allowed list
                        if in_else_block and py_file.name in files_with_allowed_fallbacks:
                            continue
                        
                        # Allow io.write_text, io.write_bytes, io.copy_file patterns
                        match_start = match.start()
                        prefix = line[max(0, match_start - 3):match_start]
                        if prefix.endswith("io."):
                            continue
                        
                        violations.append({
                            "file": py_file.name,
                            "line": line_num,
                            "pattern": pattern,
                            "content": line.strip()[:80],
                        })
        
        # Report violations
        if violations:
            msg = "\n\nP1 ENFORCEMENT FAILURE: Direct filesystem writes in graph nodes!\n"
            msg += "=" * 60 + "\n"
            for v in violations:
                msg += f"\n{v['file']}:{v['line']}\n"
                msg += f"  Pattern: {v['pattern']}\n"
                msg += f"  Content: {v['content']}\n"
            msg += "\n" + "=" * 60
            msg += "\nAll writes must go through repo/io.py (io.write_text, io.copy_file, etc.)"
            msg += "\nFallbacks in 'else:' blocks are allowed only in apply_repo_integration_changes.py"
            pytest.fail(msg)
    
    def test_repo_io_import_present_in_write_nodes(self):
        """
        Verify that nodes with write operations import from repo.io.
        """
        from pathlib import Path
        
        src_root = Path(__file__).parent.parent.parent / "src" / "integration_coworker"
        nodes_dir = src_root / "graph" / "nodes"
        
        # Nodes that perform write operations and MUST import RepoIO
        write_nodes = [
            "apply_repo_integration_changes.py",
            "analyze_repo_layout.py",  # Reads, but should use io pattern
        ]
        
        for node_name in write_nodes:
            node_file = nodes_dir / node_name
            if not node_file.exists():
                continue
                
            content = node_file.read_text()
            
            # Must import get_repo_io
            assert "from integration_coworker.repo.io import" in content, (
                f"{node_name} must import from repo.io"
            )
            assert "get_repo_io" in content, (
                f"{node_name} must import get_repo_io"
            )

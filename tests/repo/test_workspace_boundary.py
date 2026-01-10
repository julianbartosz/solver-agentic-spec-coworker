"""
Tests for G-02: Workspace boundary enforcement.

These tests verify that:
1. Monorepo workspace roots are correctly detected
2. Workspace selection heuristics work
3. RepoIO enforces write constraints to selected workspace
4. Cross-workspace writes are rejected

CRITICAL INVARIANTS:
- In monorepos, writes must be constrained to selected workspace
- Reads are allowed anywhere in the repo
- Cross-workspace writes raise WorkspaceBoundaryViolation
"""
import pytest
import tempfile
import json
from pathlib import Path

from integration_coworker.repo.detection import (
    detect_workspace_roots,
    select_workspace_root,
    detect_repo_profile,
)
from integration_coworker.repo.io import (
    RepoIO,
    WorkspaceBoundaryViolation,
)


class TestWorkspaceDetection:
    """Tests for detect_workspace_roots()."""
    
    def test_single_workspace_repo_returns_empty_list(self, tmp_path):
        """Single-workspace repos should return empty list."""
        # Create a simple Python project
        (tmp_path / "pyproject.toml").write_text("[project]\nname = 'myapp'")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("# main")
        
        roots = detect_workspace_roots(tmp_path)
        assert roots == []
    
    def test_nx_monorepo_detection(self, tmp_path):
        """Nx monorepo should detect apps/ and libs/ workspaces."""
        # Create nx.json marker
        (tmp_path / "nx.json").write_text("{}")
        
        # Create apps/ with packages
        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "api").mkdir()
        (apps_dir / "api" / "package.json").write_text('{"name": "@myorg/api"}')
        (apps_dir / "web").mkdir()
        (apps_dir / "web" / "package.json").write_text('{"name": "@myorg/web"}')
        
        # Create libs/ with packages
        libs_dir = tmp_path / "libs"
        libs_dir.mkdir()
        (libs_dir / "shared").mkdir()
        (libs_dir / "shared" / "package.json").write_text('{"name": "@myorg/shared"}')
        
        roots = detect_workspace_roots(tmp_path)
        
        assert len(roots) == 3
        assert "apps/api" in roots
        assert "apps/web" in roots
        assert "libs/shared" in roots
    
    def test_npm_workspaces_detection(self, tmp_path):
        """npm workspaces should detect packages from package.json."""
        # Create package.json with workspaces
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "monorepo",
            "workspaces": ["packages/*"]
        }))
        
        # Create packages
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        (packages_dir / "backend").mkdir()
        (packages_dir / "backend" / "package.json").write_text('{"name": "backend"}')
        (packages_dir / "frontend").mkdir()
        (packages_dir / "frontend" / "package.json").write_text('{"name": "frontend"}')
        
        roots = detect_workspace_roots(tmp_path)
        
        assert len(roots) == 2
        assert "packages/backend" in roots
        assert "packages/frontend" in roots
    
    def test_turborepo_detection(self, tmp_path):
        """Turborepo should detect apps/ and packages/ workspaces."""
        # Create turbo.json marker
        (tmp_path / "turbo.json").write_text("{}")
        
        # Create apps/
        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()
        (apps_dir / "docs").mkdir()
        (apps_dir / "docs" / "package.json").write_text('{"name": "docs"}')
        
        # Create packages/
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        (packages_dir / "ui").mkdir()
        (packages_dir / "ui" / "package.json").write_text('{"name": "ui"}')
        
        roots = detect_workspace_roots(tmp_path)
        
        assert len(roots) == 2
        assert "apps/docs" in roots
        assert "packages/ui" in roots
    
    def test_generic_services_directory(self, tmp_path):
        """Generic services/* structure should be detected."""
        services_dir = tmp_path / "services"
        services_dir.mkdir()
        
        # Create services with different markers
        (services_dir / "auth").mkdir()
        (services_dir / "auth" / "package.json").write_text('{"name": "auth"}')
        
        (services_dir / "payments").mkdir()
        (services_dir / "payments" / "pyproject.toml").write_text('[project]\nname = "payments"')
        
        roots = detect_workspace_roots(tmp_path)
        
        assert len(roots) == 2
        assert "services/auth" in roots
        assert "services/payments" in roots


class TestWorkspaceSelection:
    """Tests for select_workspace_root()."""
    
    def test_single_workspace_returns_it(self):
        """Single workspace should be selected."""
        roots = ["packages/api"]
        selected = select_workspace_root(roots)
        assert selected == "packages/api"
    
    def test_empty_list_returns_none(self):
        """Empty list should return None (single-workspace repo)."""
        selected = select_workspace_root([])
        assert selected is None
    
    def test_hint_matching(self):
        """Hint should match workspace containing the hint string."""
        roots = ["apps/web", "apps/api", "packages/shared"]
        
        selected = select_workspace_root(roots, hint="api")
        assert selected == "apps/api"
        
        selected = select_workspace_root(roots, hint="shared")
        assert selected == "packages/shared"
    
    def test_priority_pattern_api(self):
        """API-related workspaces should be prioritized."""
        roots = ["apps/admin", "apps/api", "packages/utils"]
        
        selected = select_workspace_root(roots)
        assert selected == "apps/api"
    
    def test_priority_pattern_app(self):
        """App-related workspaces should be prioritized."""
        roots = ["packages/config", "apps/main-app", "libs/shared"]
        
        selected = select_workspace_root(roots)
        assert selected == "apps/main-app"


class TestDetectedProfileWorkspaces:
    """Tests that detect_repo_profile() populates workspace fields."""
    
    def test_monorepo_populates_workspace_roots(self, tmp_path):
        """DetectedProfile should include workspace_roots for monorepos."""
        # Create npm workspaces monorepo
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "monorepo",
            "workspaces": ["packages/*"]
        }))
        
        packages_dir = tmp_path / "packages"
        packages_dir.mkdir()
        (packages_dir / "api").mkdir()
        (packages_dir / "api" / "package.json").write_text('{"name": "api"}')
        (packages_dir / "web").mkdir()
        (packages_dir / "web" / "package.json").write_text('{"name": "web"}')
        
        profile = detect_repo_profile(str(tmp_path))
        
        assert len(profile.workspace_roots) == 2
        assert profile.selected_workspace_root is not None
        assert "Monorepo detected" in " ".join(profile.evidence)


class TestRepoIOWorkspaceBoundary:
    """Tests for RepoIO workspace boundary enforcement."""
    
    def test_write_within_workspace_allowed(self, tmp_path):
        """Writes within workspace should succeed."""
        # Setup monorepo structure
        workspace = tmp_path / "apps" / "api"
        workspace.mkdir(parents=True)
        
        io = RepoIO(
            repo_root=tmp_path,
            run_id="test-001",
            workspace_root="apps/api"
        )
        
        # Write within workspace
        io.write_text("apps/api/src/main.ts", "console.log('hello');")
        
        assert (workspace / "src" / "main.ts").read_text() == "console.log('hello');"
    
    def test_write_outside_workspace_rejected(self, tmp_path):
        """Writes outside workspace should raise WorkspaceBoundaryViolation."""
        # Setup monorepo structure
        api_workspace = tmp_path / "apps" / "api"
        api_workspace.mkdir(parents=True)
        web_workspace = tmp_path / "apps" / "web"
        web_workspace.mkdir(parents=True)
        
        io = RepoIO(
            repo_root=tmp_path,
            run_id="test-002",
            workspace_root="apps/api"  # Selected workspace
        )
        
        # Attempt write to different workspace
        with pytest.raises(WorkspaceBoundaryViolation) as exc_info:
            io.write_text("apps/web/src/index.ts", "// This should fail")
        
        assert "apps/api" in str(exc_info.value)
        assert "apps/web/src/index.ts" in str(exc_info.value)
    
    def test_write_to_repo_root_rejected(self, tmp_path):
        """Writes to repo root (outside workspace) should be rejected."""
        workspace = tmp_path / "packages" / "backend"
        workspace.mkdir(parents=True)
        
        io = RepoIO(
            repo_root=tmp_path,
            run_id="test-003",
            workspace_root="packages/backend"
        )
        
        # Attempt write to repo root
        with pytest.raises(WorkspaceBoundaryViolation):
            io.write_text("README.md", "# Should fail")
    
    def test_read_outside_workspace_allowed(self, tmp_path):
        """Reads outside workspace should be allowed."""
        # Setup monorepo with data in another workspace
        (tmp_path / "apps" / "api").mkdir(parents=True)
        shared_dir = tmp_path / "packages" / "shared"
        shared_dir.mkdir(parents=True)
        (shared_dir / "config.json").write_text('{"key": "value"}')
        
        io = RepoIO(
            repo_root=tmp_path,
            run_id="test-004",
            workspace_root="apps/api"
        )
        
        # Read from different workspace should work
        content = io.read_text("packages/shared/config.json")
        assert '"key": "value"' in content
    
    def test_no_workspace_allows_all_writes(self, tmp_path):
        """Without workspace_root, all writes should be allowed."""
        io = RepoIO(
            repo_root=tmp_path,
            run_id="test-005",
            # No workspace_root - single workspace repo
        )
        
        # Write anywhere should work
        io.write_text("src/main.py", "# main")
        io.write_text("tests/test_main.py", "# tests")
        io.write_text("docs/README.md", "# docs")
        
        assert (tmp_path / "src" / "main.py").exists()
        assert (tmp_path / "tests" / "test_main.py").exists()
        assert (tmp_path / "docs" / "README.md").exists()


class TestRepoIOContextWorkspaceBoundary:
    """Tests for repo_io_context with workspace_root parameter.
    
    These tests verify that the context manager correctly propagates
    workspace_root to the underlying RepoIO instance.
    """
    
    def test_context_passes_workspace_root(self, tmp_path):
        """repo_io_context should pass workspace_root to RepoIO."""
        from integration_coworker.repo.io import repo_io_context, get_repo_io
        
        # Setup monorepo
        workspace = tmp_path / "apps" / "api"
        workspace.mkdir(parents=True)
        
        with repo_io_context(tmp_path, "test-ctx-001", workspace_root="apps/api") as io:
            # Verify workspace_root is set
            assert io.workspace_root == "apps/api"
            
            # Write within workspace should work
            io.write_text("apps/api/src/main.ts", "// works")
            assert (workspace / "src" / "main.ts").exists()
    
    def test_context_enforces_boundary_violation(self, tmp_path):
        """repo_io_context should enforce cross-workspace rejection."""
        from integration_coworker.repo.io import repo_io_context, WorkspaceBoundaryViolation
        
        # Setup monorepo
        (tmp_path / "apps" / "api").mkdir(parents=True)
        (tmp_path / "apps" / "web").mkdir(parents=True)
        
        with repo_io_context(tmp_path, "test-ctx-002", workspace_root="apps/api") as io:
            # Write to different workspace should raise
            with pytest.raises(WorkspaceBoundaryViolation) as exc_info:
                io.write_text("apps/web/src/file.ts", "// should fail")
            
            assert "apps/api" in str(exc_info.value)
    
    def test_context_sets_thread_local(self, tmp_path):
        """repo_io_context should set thread-local so get_repo_io() works."""
        from integration_coworker.repo.io import repo_io_context, get_repo_io
        
        workspace = tmp_path / "packages" / "core"
        workspace.mkdir(parents=True)
        
        # Outside context, get_repo_io() returns None
        assert get_repo_io() is None
        
        with repo_io_context(tmp_path, "test-ctx-003", workspace_root="packages/core"):
            # Inside context, get_repo_io() returns the context's RepoIO
            io = get_repo_io()
            assert io is not None
            assert io.workspace_root == "packages/core"
        
        # After context exits, get_repo_io() returns None again
        assert get_repo_io() is None


class TestE2EWorkspaceBoundaryWiring:
    """End-to-end tests for workspace boundary wiring through the system.
    
    These tests verify that workspace_root flows correctly:
    1. RepoProfile.workspace_root is set from detection
    2. Runtime wires it through to repo_io_context
    3. RepoIO enforces boundaries during graph execution
    
    This is the critical invariant from G-02: Monorepo Safety.
    """
    
    def test_repo_profile_workspace_root_flows_to_repoio(self, tmp_path):
        """RepoProfile.workspace_root should flow through to RepoIO.
        
        This tests the wiring we added in runtime.py commit c2ae842:
            with repo_io_context(
                repo_root,
                run_id,
                workspace_root=state.repo_profile.workspace_root  # <-- this line
            ):
        """
        from integration_coworker.repo.models import RepoProfile
        from integration_coworker.repo.io import repo_io_context, WorkspaceBoundaryViolation
        
        # Setup monorepo structure
        (tmp_path / "apps" / "api" / "src").mkdir(parents=True)
        (tmp_path / "apps" / "web" / "src").mkdir(parents=True)
        (tmp_path / "package.json").write_text('{"workspaces": ["apps/*"]}')
        
        # Create a RepoProfile like the runtime would
        profile = RepoProfile(
            name="test-monorepo",
            language="typescript",
            workspace_root="apps/api",  # Selected workspace
            is_monorepo=True,
        )
        
        # Simulate what runtime.py does
        with repo_io_context(
            tmp_path,
            "e2e-test-001",
            workspace_root=profile.workspace_root  # This is the critical wiring
        ) as io:
            # Verify workspace is enforced
            assert io.workspace_root == "apps/api"
            
            # Write within workspace should work
            io.write_text("apps/api/src/main.ts", "export const main = 1;")
            assert (tmp_path / "apps" / "api" / "src" / "main.ts").exists()
            
            # Write to different workspace should fail
            with pytest.raises(WorkspaceBoundaryViolation):
                io.write_text("apps/web/src/main.ts", "// cross-workspace")
            # Write within workspace should work
            io.write_text("apps/api/src/main.ts", "export const main = 1;")
            assert (tmp_path / "apps" / "api" / "src" / "main.ts").exists()
            
            # Write to different workspace should fail
            with pytest.raises(WorkspaceBoundaryViolation):
                io.write_text("apps/web/src/main.ts", "// cross-workspace")
    
    def test_detected_profile_workspace_wiring(self, tmp_path):
        """Full E2E: detect_repo_profile() -> repo_io_context() -> enforcement.
        
        This tests the complete flow from detection to enforcement,
        simulating what happens during an actual integration run.
        """
        from integration_coworker.repo.detection import detect_repo_profile
        from integration_coworker.repo.io import repo_io_context, WorkspaceBoundaryViolation
        
        # Create a realistic npm workspaces monorepo
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "my-monorepo",
            "private": True,
            "workspaces": ["packages/*"]
        }))
        
        # Create package directories with markers
        api_pkg = tmp_path / "packages" / "api"
        api_pkg.mkdir(parents=True)
        (api_pkg / "package.json").write_text('{"name": "@my/api"}')
        (api_pkg / "src").mkdir()
        
        shared_pkg = tmp_path / "packages" / "shared"
        shared_pkg.mkdir(parents=True)
        (shared_pkg / "package.json").write_text('{"name": "@my/shared"}')
        
        # Run detection - this is what the graph does
        profile = detect_repo_profile(str(tmp_path))
        
        # Verify detection worked - DetectedProfile uses selected_workspace_root
        assert len(profile.workspace_roots) == 2
        assert profile.selected_workspace_root is not None, "selected_workspace_root should be auto-selected"
        
        # Use the detected profile in repo_io_context
        with repo_io_context(
            str(tmp_path),  # DetectedProfile doesn't have repo_root, use original path
            "e2e-test-002",
            workspace_root=profile.selected_workspace_root
        ) as io:
            # Write within selected workspace
            workspace_path = profile.selected_workspace_root
            io.write_text(f"{workspace_path}/src/new_file.ts", "// generated")
            
            # Find the "other" workspace and try to write to it
            other_workspace = [w for w in profile.workspace_roots if w != profile.selected_workspace_root][0]
            
            with pytest.raises(WorkspaceBoundaryViolation) as exc_info:
                io.write_text(f"{other_workspace}/hack.ts", "// should fail")
            
            # Error should mention the boundary
            assert workspace_path in str(exc_info.value) or profile.selected_workspace_root in str(exc_info.value)
    
    def test_workspace_hint_integration(self, tmp_path):
        """Test that workspace hints correctly influence selection.
        
        When a hint like 'api' is provided, the workspace containing 'api'
        should be selected, and boundary enforcement should respect that.
        """
        from integration_coworker.repo.detection import detect_repo_profile, select_workspace_root
        from integration_coworker.repo.io import repo_io_context, WorkspaceBoundaryViolation
        
        # Create monorepo with multiple potential workspaces
        (tmp_path / "turbo.json").write_text("{}")
        
        (tmp_path / "apps" / "web").mkdir(parents=True)
        (tmp_path / "apps" / "web" / "package.json").write_text('{"name": "web"}')
        
        (tmp_path / "apps" / "api-server").mkdir(parents=True)
        (tmp_path / "apps" / "api-server" / "package.json").write_text('{"name": "api-server"}')
        
        # Detect monorepo, then use hint to select workspace
        profile = detect_repo_profile(str(tmp_path))
        
        # Use select_workspace_root with hint to choose api workspace
        selected = select_workspace_root(profile.workspace_roots, hint="api")
        
        # Should select the api-server workspace
        assert selected == "apps/api-server"
        
        # Boundary should be enforced for api-server
        with repo_io_context(
            str(tmp_path),
            "hint-test-001",
            workspace_root=selected
        ) as io:
            # OK: write to api-server
            io.write_text("apps/api-server/src/main.ts", "// api code")
            
            # FAIL: write to web
            with pytest.raises(WorkspaceBoundaryViolation):
                io.write_text("apps/web/src/app.ts", "// wrong workspace")

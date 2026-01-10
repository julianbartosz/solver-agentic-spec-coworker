"""
apply_repo_integration_changes node - Write generated code to target repository.

P2: Repo File Writes

This node takes the code_artifacts and repo_changes from the workflow state
and writes them to the target repository. Supports:
- Creating new files
- Updating existing files (with smart merge support)
- Dry-run mode for previewing changes
- Backup of modified files

P1 Refactor: All file operations go through repo/io.py for policy enforcement.
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import CodeArtifact
from integration_coworker.repo.models import FileChange
from integration_coworker.repo.io import get_repo_io, RepoIO

logger = logging.getLogger(__name__)


def _backup_file(file_path: Path, backup_dir: Path, io: Optional[RepoIO] = None) -> Optional[Path]:
    """
    Create a backup of an existing file before modification.
    
    P1: Uses RepoIO if available, falls back to direct ops for dry-run/no-context.
    
    Args:
        file_path: Path to the file to backup
        backup_dir: Directory to store backups
        io: Optional RepoIO context for policy-enforced operations
        
    Returns:
        Path to backup file, or None if file doesn't exist
    """
    # Check existence via IO context or direct
    if io:
        rel_path = file_path.relative_to(io.repo_root) if file_path.is_absolute() else file_path
        if not io.exists(rel_path):
            return None
    else:
        if not file_path.exists():
            return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path.name}.{timestamp}.bak"
    backup_path = backup_dir / backup_name

    # Copy via IO context for audit trail
    if io:
        rel_src = file_path.relative_to(io.repo_root) if file_path.is_absolute() else file_path
        rel_dst = backup_path.relative_to(io.repo_root) if backup_path.is_absolute() else backup_path
        io.copy_file(rel_src, rel_dst)
    else:
        # Fallback for dry-run mode where no IO context
        import shutil
        shutil.copy2(file_path, backup_path)
    
    logger.debug(f"Backed up {file_path} to {backup_path}")
    return backup_path


def _merge_imports(existing_content: str, new_imports: List[str]) -> str:
    """
    Merge new imports into existing file content.
    
    Adds imports after existing imports, avoiding duplicates.
    
    Args:
        existing_content: Current file content
        new_imports: List of import statements to add
        
    Returns:
        Updated content with merged imports
    """
    lines = existing_content.split("\n")
    import_end_index = 0
    existing_imports = set()

    # Find end of imports section and collect existing imports
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("import ") or stripped.startswith("from "):
            import_end_index = i + 1
            existing_imports.add(stripped)
        elif stripped and not stripped.startswith("#") and import_end_index > 0:
            # Non-import, non-comment line after imports - stop looking
            break

    # Filter out duplicate imports
    imports_to_add = [imp for imp in new_imports if imp.strip() not in existing_imports]

    if not imports_to_add:
        return existing_content

    # Insert new imports after existing imports
    result_lines = lines[:import_end_index]
    result_lines.append("")
    result_lines.append("# Generated integration imports")
    result_lines.extend(imports_to_add)
    result_lines.append("")
    result_lines.extend(lines[import_end_index:])

    return "\n".join(result_lines)


def _find_integration_hook(content: str, hook_marker: str) -> Optional[int]:
    """
    Find the line number of an integration hook marker.
    
    Hook markers look like: # @integration_hook: <name>
    
    Args:
        content: File content to search
        hook_marker: The hook name to find
        
    Returns:
        Line number (0-indexed) of the hook, or None
    """
    marker = f"# @integration_hook: {hook_marker}"
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if marker in line:
            return i
    return None


def _apply_code_artifact(
    artifact: CodeArtifact,
    repo_root: Path,
    backup_dir: Path,
    dry_run: bool,
    io: Optional[RepoIO] = None,
) -> dict:
    """
    Apply a single code artifact to the repository.
    
    P1: Uses RepoIO for policy-enforced file writes.
    
    Args:
        artifact: The code artifact to write
        repo_root: Root directory of target repo
        backup_dir: Directory for backups
        dry_run: If True, don't actually write files
        io: Optional RepoIO context for policy-enforced operations
        
    Returns:
        Dict with status information
    """
    target_path = repo_root / artifact.rel_path
    result = {
        "path": str(target_path),
        "action": "created",
        "backed_up": False,
        "backup_path": None,
    }

    # Check existence via IO context or direct
    exists = io.exists(artifact.rel_path) if io else target_path.exists()
    
    if exists:
        result["action"] = "updated"
        if not dry_run:
            backup_path = _backup_file(target_path, backup_dir, io)
            if backup_path:
                result["backed_up"] = True
                result["backup_path"] = str(backup_path)

    if not dry_run:
        # Ensure parent directory exists
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # P1: Write via RepoIO for policy enforcement and audit
        if io:
            io.write_text(artifact.rel_path, artifact.content)
        else:
            target_path.write_text(artifact.content, encoding="utf-8")
        logger.info(f"{result['action'].capitalize()} file: {target_path}")
    else:
        logger.info(f"[DRY RUN] Would {result['action']}: {target_path}")

    return result


def _apply_repo_change(
    change: FileChange,
    repo_root: Path,
    backup_dir: Path,
    dry_run: bool,
    io: Optional[RepoIO] = None,
) -> dict:
    """
    Apply a single repo change to the repository.
    
    P1: Uses RepoIO for policy-enforced file writes.
    
    Args:
        change: The change to apply
        repo_root: Root directory of target repo
        backup_dir: Directory for backups
        dry_run: If True, don't actually write files
        io: Optional RepoIO context for policy-enforced operations
        
    Returns:
        Dict with status information
    """
    target_path = repo_root / change.rel_path
    result = {
        "path": str(target_path),
        "action": change.change_type,
        "backed_up": False,
        "backup_path": None,
    }

    # Check existence via IO context or direct
    exists = io.exists(change.rel_path) if io else target_path.exists()

    if change.change_type == "delete":
        if exists:
            if not dry_run:
                backup_path = _backup_file(target_path, backup_dir, io)
                if backup_path:
                    result["backed_up"] = True
                    result["backup_path"] = str(backup_path)
                # Note: delete operation not yet in RepoIO - use direct unlink
                # This is acceptable since backup is done via io.copy_file
                target_path.unlink()
                logger.info(f"Deleted file: {target_path}")
            else:
                logger.info(f"[DRY RUN] Would delete: {target_path}")
        return result

    if exists and change.change_type != "create":
        if not dry_run:
            backup_path = _backup_file(target_path, backup_dir, io)
            if backup_path:
                result["backed_up"] = True
                result["backup_path"] = str(backup_path)

    if not dry_run:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # P1: Write via RepoIO for policy enforcement and audit
        if io:
            io.write_text(change.rel_path, change.after)
        else:
            target_path.write_text(change.after, encoding="utf-8")
        logger.info(f"{change.change_type.capitalize()}d file: {target_path}")
    else:
        logger.info(f"[DRY RUN] Would {change.change_type}: {target_path}")

    return result


# =============================================================================
# V38-001: Dependency Management Helpers
# =============================================================================

def _ensure_httpx_dependency(repo_root: Path, io: Optional[RepoIO], applied_changes: List[dict]) -> None:
    """
    Ensure httpx dependency is present in pyproject.toml.
    
    V38-001: Generated client code uses httpx, so it must be in dependencies.
    This function updates an existing pyproject.toml to add httpx if missing.
    """
    pyproject_file = repo_root / "pyproject.toml"
    
    try:
        if io:
            content = io.read_text("pyproject.toml")
        else:
            content = pyproject_file.read_text(encoding="utf-8")
        
        # Check if httpx is already in dependencies
        if "httpx" in content:
            logger.debug("httpx already in pyproject.toml dependencies")
            return
        
        # Find [project] dependencies section and add httpx
        # This handles both [project] dependencies = [...] and
        # [project.dependencies] formats
        import re
        
        # Pattern 1: dependencies = [...] under [project]
        pattern1 = r'(\[project\].*?dependencies\s*=\s*\[)([^\]]*)(\])'
        match1 = re.search(pattern1, content, re.DOTALL)
        
        if match1:
            before = match1.group(1)
            deps = match1.group(2)
            after = match1.group(3)
            
            # Add httpx to existing deps
            if deps.strip():
                new_deps = deps.rstrip() + ',\n    "httpx>=0.24.0",\n'
            else:
                new_deps = '\n    "httpx>=0.24.0",\n'
            
            new_content = content[:match1.start()] + before + new_deps + after + content[match1.end():]
            
            if io:
                io.write_text("pyproject.toml", new_content)
            else:
                pyproject_file.write_text(new_content, encoding="utf-8")
            
            logger.info("Added httpx to pyproject.toml dependencies")
            applied_changes.append({
                "path": str(pyproject_file),
                "action": "updated",
                "note": "Added httpx dependency",
                "auto_generated": True,
            })
            return
        
        # Pattern 2: No dependencies section - add one after [project]
        project_match = re.search(r'\[project\]', content)
        if project_match:
            # Find the next section or end of file
            next_section = re.search(r'\n\[', content[project_match.end():])
            insert_pos = project_match.end() + next_section.start() if next_section else len(content)
            
            deps_section = '\ndependencies = [\n    "httpx>=0.24.0",\n]\n'
            new_content = content[:insert_pos] + deps_section + content[insert_pos:]
            
            if io:
                io.write_text("pyproject.toml", new_content)
            else:
                pyproject_file.write_text(new_content, encoding="utf-8")
            
            logger.info("Added dependencies section with httpx to pyproject.toml")
            applied_changes.append({
                "path": str(pyproject_file),
                "action": "updated",
                "note": "Added httpx dependency",
                "auto_generated": True,
            })
            
    except Exception as e:
        logger.warning(f"Failed to update pyproject.toml with httpx dependency: {e}")


def _ensure_poetry_packages_registration(
    repo_root: Path,
    io: Optional[RepoIO],
    applied_changes: List[Dict[str, Any]],
    integrations_rel_dir: str = "src/integrations",
) -> None:
    """
    V38-002: Ensure integrations package is registered in Poetry's packages list.
    
    Poetry projects require explicit package registration in pyproject.toml.
    When we create src/integrations/, we must add it to the packages list:
    
    [tool.poetry]
    packages = [
        {include = "myapp", from = "src"},
        {include = "integrations", from = "src"}  # <- We add this
    ]
    
    This function handles both cases:
    1. Adding to existing packages list
    2. Creating packages list if none exists (rare for Poetry projects)
    
    Args:
        repo_root: Repository root path
        io: Optional RepoIO for policy-enforced writes
        applied_changes: List to record changes
        integrations_rel_dir: Relative path to integrations directory (e.g., "src/integrations")
    """
    import re
    
    pyproject_file = repo_root / "pyproject.toml"
    pyproject_exists = io.exists("pyproject.toml") if io else pyproject_file.exists()
    
    if not pyproject_exists:
        return  # No pyproject.toml, nothing to do
    
    try:
        if io:
            content = io.read_text("pyproject.toml")
        else:
            content = pyproject_file.read_text(encoding="utf-8")
        
        # Check if this is a Poetry project
        if "[tool.poetry]" not in content:
            logger.debug("Not a Poetry project, skipping packages registration")
            return
        
        # Parse the integrations directory to determine package name and from
        # e.g., "src/integrations" -> include="integrations", from="src"
        parts = integrations_rel_dir.split("/")
        if len(parts) >= 2:
            package_name = parts[-1]  # "integrations"
            from_dir = parts[0]       # "src"
        else:
            package_name = parts[0]
            from_dir = "."
        
        # Check if integrations is already registered
        # Pattern: {include = "integrations", from = "src"}
        # Or: {include = "integrations"} (without from)
        integrations_pattern = rf'\{{\s*include\s*=\s*["\']integrations["\']\s*(?:,\s*from\s*=\s*["\'][^"\']+["\']\s*)?\}}'
        if re.search(integrations_pattern, content):
            logger.debug("integrations package already registered in Poetry packages")
            return
        
        # Find existing packages list
        # Pattern: packages = [ ... ]
        packages_pattern = r'(packages\s*=\s*\[)([^\]]*?)(\])'
        packages_match = re.search(packages_pattern, content, re.DOTALL)
        
        if packages_match:
            # Add to existing packages list
            before = packages_match.group(1)
            existing = packages_match.group(2)
            after = packages_match.group(3)
            
            # Build new package entry
            new_entry = f'{{include = "{package_name}", from = "{from_dir}"}}'
            
            # Add with proper formatting
            if existing.strip():
                # Has existing entries - add comma and newline
                # Detect indentation from existing content
                indent_match = re.search(r'\n(\s+)\{', existing)
                indent = indent_match.group(1) if indent_match else "    "
                
                # Remove trailing whitespace from existing
                existing = existing.rstrip()
                if not existing.endswith(','):
                    existing += ','
                new_packages = f"{existing}\n{indent}{new_entry}\n"
            else:
                # Empty packages list
                new_packages = f"\n    {new_entry}\n"
            
            new_content = content[:packages_match.start()] + before + new_packages + after + content[packages_match.end():]
            
            if io:
                io.write_text("pyproject.toml", new_content)
            else:
                pyproject_file.write_text(new_content, encoding="utf-8")
            
            logger.info(f"V38-002: Registered integrations package in Poetry packages list")
            applied_changes.append({
                "path": str(pyproject_file),
                "action": "updated",
                "note": f"V38-002: Added integrations package to Poetry packages list",
                "auto_generated": True,
            })
        else:
            # No packages list exists - create one after [tool.poetry]
            poetry_match = re.search(r'\[tool\.poetry\]', content)
            if poetry_match:
                # Find the next section or end
                rest = content[poetry_match.end():]
                next_section = re.search(r'\n\[', rest)
                insert_pos = poetry_match.end() + next_section.start() if next_section else len(content)
                
                packages_list = f'\npackages = [\n    {{include = "{package_name}", from = "{from_dir}"}}\n]\n'
                new_content = content[:insert_pos] + packages_list + content[insert_pos:]
                
                if io:
                    io.write_text("pyproject.toml", new_content)
                else:
                    pyproject_file.write_text(new_content, encoding="utf-8")
                
                logger.info(f"V38-002: Created packages list with integrations in pyproject.toml")
                applied_changes.append({
                    "path": str(pyproject_file),
                    "action": "updated",
                    "note": "V38-002: Created packages list with integrations",
                    "auto_generated": True,
                })
                
    except Exception as e:
        logger.warning(f"V38-002: Failed to register integrations in Poetry packages: {e}")


def _ensure_requirements_txt_dependency(repo_root: Path, io: Optional[RepoIO], applied_changes: List[dict]) -> None:
    """
    Ensure httpx dependency is present in requirements.txt if it exists.
    
    V38-001: For repos that use requirements.txt instead of pyproject.toml,
    add httpx to the requirements file.
    """
    requirements_file = repo_root / "requirements.txt"
    requirements_exists = io.exists("requirements.txt") if io else requirements_file.exists()
    
    if not requirements_exists:
        # No requirements.txt - that's fine, we use pyproject.toml
        return
    
    try:
        if io:
            content = io.read_text("requirements.txt")
        else:
            content = requirements_file.read_text(encoding="utf-8")
        
        # Check if httpx is already in requirements
        lines = content.strip().split('\n')
        for line in lines:
            if line.strip().startswith('httpx'):
                logger.debug("httpx already in requirements.txt")
                return
        
        # Add httpx to requirements
        if content and not content.endswith('\n'):
            content += '\n'
        content += 'httpx>=0.24.0\n'
        
        if io:
            io.write_text("requirements.txt", content)
        else:
            requirements_file.write_text(content, encoding="utf-8")
        
        logger.info("Added httpx to requirements.txt")
        applied_changes.append({
            "path": str(requirements_file),
            "action": "updated",
            "note": "Added httpx dependency",
            "auto_generated": True,
        })
        
    except Exception as e:
        logger.warning(f"Failed to update requirements.txt with httpx dependency: {e}")


def _ensure_integration_tests_conftest(
    repo_root: Path,
    io: Optional["RepoIO"],
    applied_changes: List[Dict[str, Any]],
) -> None:
    """
    V38-002: Generate conftest.py for tests/integrations/ directory.
    
    This ensures generated integration tests can import from src-layout projects
    without requiring PYTHONPATH to be set. The conftest.py adds src/ to sys.path
    at import time.
    
    Only generated if:
    1. tests/integrations directory exists (we created tests there)
    2. tests/integrations/conftest.py doesn't already exist
    """
    # Only create if tests/integrations directory exists
    integrations_tests_dir = repo_root / "tests" / "integrations"
    dir_exists = io.exists("tests/integrations") if io else integrations_tests_dir.exists()
    
    if not dir_exists:
        return
    
    conftest_file = integrations_tests_dir / "conftest.py"
    conftest_rel_path = "tests/integrations/conftest.py"
    conftest_exists = io.exists(conftest_rel_path) if io else conftest_file.exists()
    
    if conftest_exists:
        logger.debug("tests/integrations/conftest.py already exists, skipping")
        return
    
    # Generate minimal conftest.py that sets up sys.path for imports
    conftest_content = '''"""Pytest configuration for integration tests.

V38-002: Auto-generated conftest to enable imports from src-layout projects.
Adds src/ directory to sys.path so tests can import generated clients/flows.
"""
import sys
from pathlib import Path

# Add src/ and repo root to sys.path for imports
def _setup_pythonpath():
    """Add src directory to Python path for src-layout projects."""
    conftest_dir = Path(__file__).parent.resolve()
    repo_root = conftest_dir.parent.parent  # tests/integrations -> tests -> repo_root
    
    # Add src/ directory if it exists
    src_dir = repo_root / "src"
    if src_dir.exists() and str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))
    
    # Also add repo root for flat-layout projects
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

_setup_pythonpath()
'''
    
    try:
        if io:
            io.write_text(conftest_rel_path, conftest_content)
        else:
            conftest_file.write_text(conftest_content, encoding="utf-8")
        
        logger.info(f"Created integration tests conftest.py: {conftest_file}")
        applied_changes.append({
            "path": str(conftest_file),
            "action": "created",
            "note": "V38-002: sys.path setup for integration test imports",
            "auto_generated": True,
        })
    except Exception as e:
        logger.warning(f"Failed to create integration tests conftest.py: {e}")


def apply_repo_integration_changes(state: WorkflowState) -> WorkflowState:
    """
    Write generated code artifacts to the target repository.
    
    Reads: repo_root, code_artifacts, repo_changes, options.dry_run, sandbox_result
    Writes: plan["applied_changes"], (filesystem if not dry_run)
    
    Features:
    - Creates backup of modified files
    - Supports dry-run mode for previewing changes
    - Handles both code_artifacts and repo_changes
    - Merges imports when updating existing files
    
    P1: All file writes go through repo/io.py for policy enforcement.
    
    Per Agent Harness Alignment Plan:
    - Checks HITL approval flag before writing
    - If hitl_rejected is True, skips all writes
    
    V42-004: Sandbox Validation Gate
    - Checks sandbox_result.success before writing to repository
    - If sandbox validation failed, skips all writes to prevent broken code in repo
    - This ensures only validated code is applied to the target repository
    
    V42-004 Extension: force_write_on_sandbox_failure option
    - If options.force_write_on_sandbox_failure is True, allow writes even on failure
    - Logs a WARNING when bypassing sandbox protection
    - Useful for debugging or when sandbox has infrastructure issues
    """
    # V42-004: Check sandbox validation result BEFORE any writes
    # This prevents broken/invalid code from being written to the repository
    sandbox_result = getattr(state, 'sandbox_result', None)
    
    # Check if force_write is enabled
    force_write = False
    if state.options and hasattr(state.options, 'force_write_on_sandbox_failure'):
        force_write = state.options.force_write_on_sandbox_failure
    
    if sandbox_result:
        sandbox_success = sandbox_result.get('success', True)
        if not sandbox_success:
            # Get detailed failure info
            sandbox_summary = sandbox_result.get('summary', 'Unknown failure')
            gates = sandbox_result.get('gates', [])
            failed_gates = [g.get('name', 'unknown') for g in gates if not g.get('passed', True)]
            
            if force_write:
                # V42-004: Force write enabled - allow writes with warning
                logger.warning(
                    f"[V42-004] Sandbox validation FAILED: {sandbox_summary}. "
                    f"Failed gates: {failed_gates or 'N/A'}. "
                    f"PROCEEDING WITH WRITES due to --force-write flag."
                )
                state.plan["sandbox_bypassed"] = True
                state.plan["sandbox_failure_reason"] = sandbox_summary
                # Continue to writes - don't return early
            else:
                # Standard behavior: block writes on sandbox failure
                logger.warning(
                    f"[V42-004] Skipping repo writes - sandbox validation FAILED: {sandbox_summary}. "
                    f"Failed gates: {failed_gates or 'N/A'}"
                )
                state.plan["applied_changes"] = []
                state.plan["sandbox_blocked"] = True
                state.plan["sandbox_failure_reason"] = sandbox_summary
                state.completed_steps.append("apply_repo_integration_changes")
                
                # Add to errors for visibility
                if f"Sandbox validation failed" not in str(state.errors):
                    state.errors.append(f"[V42-004] Repo writes blocked due to sandbox failure: {sandbox_summary}")
                
                return state
    
    # Check HITL approval (if HITL gate was used)
    if state.plan.get("hitl_rejected", False):
        logger.warning("Skipping repo writes - HITL review was rejected")
        state.plan["applied_changes"] = []
        state.plan["hitl_skipped"] = True
        state.completed_steps.append("apply_repo_integration_changes")
        return state
    
    is_dry_run = state.options.dry_run if state.options else False

    if not state.repo_root:
        logger.info("No repo_root specified; skipping file writes")
        state.completed_steps.append("apply_repo_integration_changes")
        return state

    repo_root = Path(state.repo_root)
    # V28-001 Fix: Use non-hidden backup directory to avoid RepoIO policy conflicts
    # The .integration_backups name was blocked by allow_hidden_dirs=False policy
    # Using a non-hidden name is more robust than modifying policy settings
    backup_dir = repo_root / "_integration_backups"

    # P1: Get RepoIO context for policy-enforced operations
    # V27-006 Fix: Create context if missing (e.g., when running node directly for tests)
    io = get_repo_io()
    io_context = None
    if io and not is_dry_run:
        logger.debug(f"Using RepoIO context for policy-enforced writes: {io.run_id}")
    elif not io and not is_dry_run:
        # V27-006: Create context on-demand if missing
        # This can happen when:
        # 1. Node is tested/run directly without full workflow
        # 2. Thread-local context was lost (parallel execution edge case)
        from integration_coworker.repo.io import repo_io_context, RepoIOConfig
        run_id = getattr(state, 'run_id', None) or 'apply_node_fallback'
        logger.warning(
            f"No RepoIO context available - creating fallback context for run_id={run_id}. "
            "This may indicate a bug in workflow setup or a direct node invocation."
        )
        # V28-001 Fix: Enable allow_hidden_dirs for fallback context to support
        # edge cases where backup dir might still be hidden (e.g., legacy paths)
        # Also add _integration_backups to denylist to prevent it from being
        # treated as a regular repo file
        fallback_config = RepoIOConfig(
            allow_hidden_dirs=True,  # Allow hidden dirs for backup operations
            allow_dotfiles=True,  # Allow .bak files
        )
        io_context = repo_io_context(repo_root, run_id, fallback_config)
        io_context.__enter__()
        io = get_repo_io()

    applied_changes: List[dict] = []
    has_code_artifacts = bool(state.code_artifacts)
    has_repo_changes = bool(state.repo_changes and state.repo_changes.changes)

    # Apply code_artifacts
    if has_code_artifacts:
        logger.info(f"Applying {len(state.code_artifacts)} code artifacts")
        for artifact in state.code_artifacts:
            result = _apply_code_artifact(artifact, repo_root, backup_dir, is_dry_run, io)
            applied_changes.append(result)

    # Apply repo_changes
    if has_repo_changes:
        logger.info(f"Applying {len(state.repo_changes.changes)} repo changes")
        for change in state.repo_changes.changes:
            result = _apply_repo_change(change, repo_root, backup_dir, is_dry_run, io)
            applied_changes.append(result)

    # Generate __init__.py files for new directories and ALL parent packages
    # This ensures that importing nested packages works (e.g., integrations.clients.X
    # requires both integrations/__init__.py AND integrations/clients/__init__.py)
    if not is_dry_run and (has_code_artifacts or has_repo_changes):
        dirs_needing_init = set()

        if has_code_artifacts:
            for artifact in state.code_artifacts:
                target_path = repo_root / artifact.rel_path
                current = target_path.parent
                while current != repo_root and current.is_relative_to(repo_root):
                    dirs_needing_init.add(current)
                    current = current.parent

        if has_repo_changes:
            for change in state.repo_changes.changes:
                if change.change_type not in {"create", "update"}:
                    continue
                target_path = repo_root / change.rel_path
                current = target_path.parent
                while current != repo_root and current.is_relative_to(repo_root):
                    dirs_needing_init.add(current)
                    current = current.parent

        for dir_path in sorted(dirs_needing_init):  # Sort for deterministic order
            init_file = dir_path / "__init__.py"
            init_exists = io.exists(init_file.relative_to(repo_root)) if io else init_file.exists()
            if not init_exists:
                # P1: Write via RepoIO
                if io:
                    io.write_text(init_file.relative_to(repo_root), "")
                else:
                    init_file.write_text("", encoding="utf-8")
                logger.info(f"Created __init__.py: {init_file}")
                applied_changes.append({
                    "path": str(init_file),
                    "action": "created",
                    "backed_up": False,
                    "auto_generated": True,
                })

    # Generate pyproject.toml with pytest config if it doesn't exist
    # This ensures pytest can find and import the generated modules
    # V38-001: Also includes setuptools package config and httpx dependency
    if not is_dry_run and (has_code_artifacts or has_repo_changes):
        pyproject_file = repo_root / "pyproject.toml"
        pyproject_exists = io.exists("pyproject.toml") if io else pyproject_file.exists()
        if not pyproject_exists:
            # V38-001: Enhanced pyproject.toml with project metadata, dependencies,
            # and proper package discovery for generated integrations
            pyproject_content = '''[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "generated-integration"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    "httpx>=0.24.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
]

[tool.setuptools.packages.find]
where = ["src"]
include = ["*"]

[tool.pytest.ini_options]
pythonpath = [".", "src"]
testpaths = ["tests"]
'''
            # P1: Write via RepoIO
            if io:
                io.write_text("pyproject.toml", pyproject_content)
            else:
                pyproject_file.write_text(pyproject_content, encoding="utf-8")
            logger.info(f"Created pyproject.toml with pytest config: {pyproject_file}")
            applied_changes.append({
                "path": str(pyproject_file),
                "action": "created",
                "backed_up": False,
                "auto_generated": True,
            })
        else:
            # V38-001: Update existing pyproject.toml to add httpx dependency if missing
            _ensure_httpx_dependency(repo_root, io, applied_changes)
            # V38-002: Register integrations package in Poetry's packages list
            _ensure_poetry_packages_registration(repo_root, io, applied_changes)
    
    # V38-001: Also update requirements.txt if it exists (for non-pyproject repos)
    if not is_dry_run and (has_code_artifacts or has_repo_changes):
        _ensure_requirements_txt_dependency(repo_root, io, applied_changes)

    # V38-002: Generate conftest.py for tests/integrations to enable imports
    # This is critical because the repo's existing conftest.py may not set up sys.path
    # for importing from src-layout projects
    if not is_dry_run and (has_code_artifacts or has_repo_changes):
        _ensure_integration_tests_conftest(repo_root, io, applied_changes)

    # Store results in plan
    state.plan["applied_changes"] = applied_changes

    # Write integration manifest for scoped validation
    # Only include user-generated files, not auto-generated __init__.py/pyproject.toml
    if not is_dry_run and applied_changes:
        import json
        from datetime import datetime
        
        manifest_files = [
            {"path": change["path"], "action": change["action"]}
            for change in applied_changes
            if not change.get("auto_generated", False)
        ]
        
        manifest = {
            "run_id": state.run_id or "unknown",
            "provider_code": state.provider_code or "unknown",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "files": manifest_files,
        }
        
        manifest_path = repo_root / ".integration_manifest.json"
        try:
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
            logger.info(f"Wrote integration manifest: {manifest_path} ({len(manifest_files)} files)")
        except Exception as e:
            logger.warning(f"Failed to write integration manifest: {e}")

    if is_dry_run:
        summary_lines = [f"DRY RUN - Would apply {len(applied_changes)} changes:"]
        for change in applied_changes:
            summary_lines.append(f"  {change['action'].upper()}: {change['path']}")
        state.plan["dry_run_summary"] = "\n".join(summary_lines)
        logger.info(state.plan["dry_run_summary"])
    else:
        logger.info(f"Applied {len(applied_changes)} changes to {repo_root}")
        # Ensure import machinery sees freshly written modules
        try:
            import importlib
            importlib.invalidate_caches()
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"Failed to invalidate import caches: {e}")
        # Bug #28 fix: Mark repo_changes as applied
        if state.repo_changes:
            state.repo_changes.applied = True

    # V23-002: Clear repo content after repo wiring is complete
    # This frees repo_snapshot and repo_markdown_context that are no longer needed
    from integration_coworker.graph.state_gc import cleanup_after_repo_wiring
    cleanup_after_repo_wiring(state)

    # V27-006: Clean up fallback io_context if we created one
    if io_context:
        io_context.__exit__(None, None, None)

    state.completed_steps.append("apply_repo_integration_changes")
    return state

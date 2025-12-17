"""
apply_repo_integration_changes node - Write generated code to target repository.

P2: Repo File Writes

This node takes the code_artifacts and repo_changes from the workflow state
and writes them to the target repository. Supports:
- Creating new files
- Updating existing files (with smart merge support)
- Dry-run mode for previewing changes
- Backup of modified files
"""
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import CodeArtifact
from integration_coworker.repo.models import FileChange

logger = logging.getLogger(__name__)


def _backup_file(file_path: Path, backup_dir: Path) -> Optional[Path]:
    """
    Create a backup of an existing file before modification.
    
    Args:
        file_path: Path to the file to backup
        backup_dir: Directory to store backups
        
    Returns:
        Path to backup file, or None if file doesn't exist
    """
    if not file_path.exists():
        return None

    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"{file_path.name}.{timestamp}.bak"
    backup_path = backup_dir / backup_name

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
) -> dict:
    """
    Apply a single code artifact to the repository.
    
    Args:
        artifact: The code artifact to write
        repo_root: Root directory of target repo
        backup_dir: Directory for backups
        dry_run: If True, don't actually write files
        
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

    if target_path.exists():
        result["action"] = "updated"
        if not dry_run:
            backup_path = _backup_file(target_path, backup_dir)
            if backup_path:
                result["backed_up"] = True
                result["backup_path"] = str(backup_path)

    if not dry_run:
        # Ensure parent directory exists
        target_path.parent.mkdir(parents=True, exist_ok=True)
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
) -> dict:
    """
    Apply a single repo change to the repository.
    
    Args:
        change: The change to apply
        repo_root: Root directory of target repo
        backup_dir: Directory for backups
        dry_run: If True, don't actually write files
        
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

    if change.change_type == "delete":
        if target_path.exists():
            if not dry_run:
                backup_path = _backup_file(target_path, backup_dir)
                if backup_path:
                    result["backed_up"] = True
                    result["backup_path"] = str(backup_path)
                target_path.unlink()
                logger.info(f"Deleted file: {target_path}")
            else:
                logger.info(f"[DRY RUN] Would delete: {target_path}")
        return result

    if target_path.exists() and change.change_type != "create":
        if not dry_run:
            backup_path = _backup_file(target_path, backup_dir)
            if backup_path:
                result["backed_up"] = True
                result["backup_path"] = str(backup_path)

    if not dry_run:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(change.after, encoding="utf-8")
        logger.info(f"{change.change_type.capitalize()}d file: {target_path}")
    else:
        logger.info(f"[DRY RUN] Would {change.change_type}: {target_path}")

    return result


def apply_repo_integration_changes(state: WorkflowState) -> WorkflowState:
    """
    Write generated code artifacts to the target repository.
    
    Reads: repo_root, code_artifacts, repo_changes, options.dry_run
    Writes: plan["applied_changes"], (filesystem if not dry_run)
    
    Features:
    - Creates backup of modified files
    - Supports dry-run mode for previewing changes
    - Handles both code_artifacts and repo_changes
    - Merges imports when updating existing files
    """
    is_dry_run = state.options.dry_run if state.options else False

    if not state.repo_root:
        logger.info("No repo_root specified; skipping file writes")
        state.completed_steps.append("apply_repo_integration_changes")
        return state

    repo_root = Path(state.repo_root)
    backup_dir = repo_root / ".integration_backups"

    applied_changes: List[dict] = []

    # Apply code_artifacts
    if state.code_artifacts:
        logger.info(f"Applying {len(state.code_artifacts)} code artifacts")
        for artifact in state.code_artifacts:
            result = _apply_code_artifact(artifact, repo_root, backup_dir, is_dry_run)
            applied_changes.append(result)

    # Apply repo_changes
    if state.repo_changes and state.repo_changes.changes:
        logger.info(f"Applying {len(state.repo_changes.changes)} repo changes")
        for change in state.repo_changes.changes:
            result = _apply_repo_change(change, repo_root, backup_dir, is_dry_run)
            applied_changes.append(result)

    # Generate __init__.py files for new directories and ALL parent packages
    # This ensures that importing nested packages works (e.g., integrations.clients.X
    # requires both integrations/__init__.py AND integrations/clients/__init__.py)
    if not is_dry_run and state.code_artifacts:
        dirs_needing_init = set()
        for artifact in state.code_artifacts:
            target_path = repo_root / artifact.rel_path
            # Walk up from the parent directory to repo_root, collecting all dirs
            current = target_path.parent
            while current != repo_root and current.is_relative_to(repo_root):
                dirs_needing_init.add(current)
                current = current.parent

        for dir_path in sorted(dirs_needing_init):  # Sort for deterministic order
            init_file = dir_path / "__init__.py"
            if not init_file.exists():
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
    if not is_dry_run:
        pyproject_file = repo_root / "pyproject.toml"
        if not pyproject_file.exists():
            pyproject_content = '''[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
'''
            pyproject_file.write_text(pyproject_content, encoding="utf-8")
            logger.info(f"Created pyproject.toml with pytest config: {pyproject_file}")
            applied_changes.append({
                "path": str(pyproject_file),
                "action": "created",
                "backed_up": False,
                "auto_generated": True,
            })

    # Store results in plan
    state.plan["applied_changes"] = applied_changes

    if is_dry_run:
        summary_lines = [f"DRY RUN - Would apply {len(applied_changes)} changes:"]
        for change in applied_changes:
            summary_lines.append(f"  {change['action'].upper()}: {change['path']}")
        state.plan["dry_run_summary"] = "\n".join(summary_lines)
        logger.info(state.plan["dry_run_summary"])
    else:
        logger.info(f"Applied {len(applied_changes)} changes to {repo_root}")
        # Bug #28 fix: Mark repo_changes as applied
        if state.repo_changes:
            state.repo_changes.applied = True

    state.completed_steps.append("apply_repo_integration_changes")
    return state

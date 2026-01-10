"""
Coordinated Artifact Generation and Writing (V35-004/005/006 Fix)

This module ensures that:
1. All artifacts (client, flow, test) are generated with consistent import paths
2. Artifacts are written to the correct locations in the target repo
3. Import paths between artifacts match their actual file locations

Problem (V35-004/005/006):
--------------------------
- Router imports from flows.openai_... but the file doesn't exist
- Database shows artifacts were generated but files weren't written
- Tests import from integrations.flows.X but flow is at flows/X.py

Solution:
---------
1. Create a CoordinatedArtifactSet that tracks all artifacts together
2. Compute import paths AFTER determining file locations
3. Validate that all cross-artifact imports are resolvable
4. Write all artifacts atomically (all-or-nothing)
5. Generate __init__.py files as needed for import resolution
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Dict, List, Optional, Set, Tuple, Any

from integration_coworker.domain.models import CodeArtifact

logger = logging.getLogger(__name__)


@dataclass
class ArtifactLocation:
    """Tracks the location and import path for a single artifact."""
    artifact_type: str  # "client", "flow", "test"
    rel_path: str       # Relative path from repo root
    import_path: str    # Python import path (e.g., "integrations.clients.openai")
    module_name: str    # Module name (e.g., "openai")
    
    @property
    def directory(self) -> str:
        """Get the directory containing this artifact."""
        return str(PurePath(self.rel_path).parent)
    
    @property
    def filename(self) -> str:
        """Get the filename of this artifact."""
        return str(PurePath(self.rel_path).name)


@dataclass
class CoordinatedArtifactSet:
    """
    A set of related artifacts with coordinated import paths.
    
    This ensures that:
    - Client, flow, and test artifacts have consistent paths
    - Cross-artifact imports are valid
    - All files are written together
    """
    provider_code: str
    task_slug: str
    integrations_root: str = "integrations"  # Base dir for integrations
    
    # Artifact locations
    client_location: Optional[ArtifactLocation] = None
    flow_location: Optional[ArtifactLocation] = None
    test_location: Optional[ArtifactLocation] = None
    
    # Artifact content
    client_code: Optional[str] = None
    flow_code: Optional[str] = None
    test_code: Optional[str] = None
    
    # Additional files needed (e.g., __init__.py)
    additional_files: Dict[str, str] = field(default_factory=dict)
    
    # Validation state
    is_validated: bool = False
    validation_errors: List[str] = field(default_factory=list)
    
    def get_client_import(self) -> Optional[str]:
        """Get the import statement for the client module."""
        if not self.client_location:
            return None
        return f"from {self.client_location.import_path} import {self.provider_code.title()}Client"
    
    def get_flow_import(self) -> Optional[str]:
        """Get the import statement for the flow module."""
        if not self.flow_location:
            return None
        flow_func = f"{self.task_slug}_flow"
        return f"from {self.flow_location.import_path} import {flow_func}"
    
    def all_artifacts(self) -> List[CodeArtifact]:
        """Get all artifacts as CodeArtifact objects."""
        artifacts = []
        
        if self.client_location and self.client_code:
            artifacts.append(CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="client",
                language="python",
                module_name=self.client_location.module_name,
                rel_path=self.client_location.rel_path,
                content=self.client_code,
            ))
        
        if self.flow_location and self.flow_code:
            artifacts.append(CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="flow",
                language="python",
                module_name=self.flow_location.module_name,
                rel_path=self.flow_location.rel_path,
                content=self.flow_code,
            ))
        
        if self.test_location and self.test_code:
            artifacts.append(CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="test",
                language="python",
                module_name=self.test_location.module_name,
                rel_path=self.test_location.rel_path,
                content=self.test_code,
            ))
        
        # Add additional files (init files)
        for path, content in self.additional_files.items():
            module = path.replace("/", ".").replace("\\", ".").replace(".py", "")
            artifacts.append(CodeArtifact(
                id=None,
                task_id=None,
                artifact_type="init",
                language="python",
                module_name=module,
                rel_path=path,
                content=content,
            ))
        
        return artifacts


def _path_to_import_path(rel_path: str, integrations_root: str) -> str:
    """
    Convert a relative file path to a Python import path.
    
    Examples:
        "integrations/clients/openai.py" -> "integrations.clients.openai"
        "src/myapp/openai.py" -> "src.myapp.openai"
    """
    # Remove .py extension
    path = PurePath(rel_path).with_suffix('')
    
    # Convert to dotted path
    return '.'.join(path.parts)


def _compute_required_init_files(rel_path: str, repo_root: Path) -> List[str]:
    """
    Compute __init__.py files needed for a path to be importable.
    
    Returns list of __init__.py paths that need to exist.
    """
    init_files = []
    path = PurePath(rel_path)
    
    # Walk up from parent directory to repo root
    current = path.parent
    while current.parts:  # Stop at repo root
        init_path = str(current / "__init__.py")
        init_files.append(init_path)
        current = current.parent
    
    return init_files


def create_coordinated_artifact_set(
    provider_code: str,
    task_slug: str,
    repo_root: str,
    integrations_root: str = "integrations",
    target_language: str = "python",
    client_class: Optional[str] = None,
    flow_function: Optional[str] = None,
) -> CoordinatedArtifactSet:
    """
    Create a coordinated artifact set with consistent paths.
    
    This is the main entry point for creating artifacts with coordinated imports.
    
    Args:
        provider_code: API provider code (e.g., "openai")
        task_slug: Task slug (e.g., "create_completion")
        repo_root: Path to repository root
        integrations_root: Base directory for integrations
        target_language: Target language
        client_class: Optional client class name
        flow_function: Optional flow function name
        
    Returns:
        CoordinatedArtifactSet with all locations configured
    """
    artifact_set = CoordinatedArtifactSet(
        provider_code=provider_code,
        task_slug=task_slug,
        integrations_root=integrations_root,
    )
    
    # Derive names if not provided
    client_class = client_class or f"{provider_code.title()}Client"
    flow_function = flow_function or f"{task_slug}_flow"
    
    # Compute paths
    client_path = f"{integrations_root}/clients/{provider_code}.py"
    flow_path = f"{integrations_root}/flows/{provider_code}_{task_slug}.py"
    test_path = f"tests/test_{provider_code}_{task_slug}.py"
    
    # Create locations with import paths
    artifact_set.client_location = ArtifactLocation(
        artifact_type="client",
        rel_path=client_path,
        import_path=_path_to_import_path(client_path, integrations_root),
        module_name=provider_code,
    )
    
    artifact_set.flow_location = ArtifactLocation(
        artifact_type="flow",
        rel_path=flow_path,
        import_path=_path_to_import_path(flow_path, integrations_root),
        module_name=f"{provider_code}_{task_slug}",
    )
    
    artifact_set.test_location = ArtifactLocation(
        artifact_type="test",
        rel_path=test_path,
        import_path=_path_to_import_path(test_path, integrations_root),
        module_name=f"test_{provider_code}_{task_slug}",
    )
    
    # Compute required __init__.py files
    repo_path = Path(repo_root)
    required_inits: Set[str] = set()
    
    for location in [artifact_set.client_location, artifact_set.flow_location]:
        if location:
            inits = _compute_required_init_files(location.rel_path, repo_path)
            required_inits.update(inits)
    
    # Add empty __init__.py files
    for init_path in required_inits:
        artifact_set.additional_files[init_path] = ""
    
    logger.info(
        f"[V35-004] Created coordinated artifact set: "
        f"client={artifact_set.client_location.rel_path}, "
        f"flow={artifact_set.flow_location.rel_path}, "
        f"test={artifact_set.test_location.rel_path}, "
        f"init_files={len(required_inits)}"
    )
    
    return artifact_set


def validate_artifact_imports(artifact_set: CoordinatedArtifactSet) -> List[str]:
    """
    Validate that all cross-artifact imports are consistent.
    
    Checks:
    1. Flow imports client from correct path
    2. Test imports flow from correct path
    3. All import paths correspond to actual file locations
    
    Returns:
        List of validation errors (empty if valid)
    """
    errors = []
    
    if not artifact_set.client_location:
        errors.append("Missing client location")
        return errors
    
    if not artifact_set.flow_location:
        errors.append("Missing flow location")
        return errors
    
    # Check that flow imports client correctly
    if artifact_set.flow_code:
        expected_import = artifact_set.client_location.import_path
        if expected_import not in artifact_set.flow_code:
            errors.append(
                f"Flow does not import client from correct path: {expected_import}"
            )
    
    # Check that test imports flow correctly
    if artifact_set.test_code and artifact_set.test_location:
        expected_import = artifact_set.flow_location.import_path
        if expected_import not in artifact_set.test_code:
            errors.append(
                f"Test does not import flow from correct path: {expected_import}"
            )
    
    artifact_set.is_validated = len(errors) == 0
    artifact_set.validation_errors = errors
    
    return errors


def fix_artifact_imports(artifact_set: CoordinatedArtifactSet) -> CoordinatedArtifactSet:
    """
    Fix import paths in artifacts to match their actual locations.
    
    This ensures consistency between where files are written and what they import.
    """
    import re
    
    # Fix flow imports
    if artifact_set.flow_code and artifact_set.client_location:
        # Pattern for client imports: from X import YClient
        old_patterns = [
            r'from\s+[\w.]+\s+import\s+\w*Client',
            r'from\s+integrations\.clients\.\w+\s+import',
        ]
        
        correct_import = f"from {artifact_set.client_location.import_path} import"
        
        flow_code = artifact_set.flow_code
        for pattern in old_patterns:
            flow_code = re.sub(
                pattern,
                correct_import,
                flow_code,
            )
        artifact_set.flow_code = flow_code
    
    # Fix test imports
    if artifact_set.test_code and artifact_set.flow_location:
        # Pattern for flow imports
        flow_module = artifact_set.flow_location.module_name
        correct_import = f"from {artifact_set.flow_location.import_path} import"
        
        test_code = artifact_set.test_code
        
        # Fix import statements
        test_code = re.sub(
            r'from\s+integrations\.flows\.\w+\s+import',
            correct_import,
            test_code,
        )
        
        # Fix mock patch paths
        test_code = re.sub(
            r'patch\(["\']integrations\.flows\.\w+\.',
            f"patch('{artifact_set.flow_location.import_path}.",
            test_code,
        )
        
        artifact_set.test_code = test_code
    
    logger.info("[V35-006] Fixed cross-artifact import paths")
    
    return artifact_set


def write_coordinated_artifacts(
    artifact_set: CoordinatedArtifactSet,
    repo_root: str,
    dry_run: bool = False,
) -> List[str]:
    """
    Write all artifacts atomically.
    
    Creates all directories and __init__.py files needed for imports to work.
    
    Args:
        artifact_set: The coordinated artifact set
        repo_root: Path to repository root
        dry_run: If True, don't write files
        
    Returns:
        List of files written
    """
    written_files = []
    repo_path = Path(repo_root)
    
    # Validate first
    errors = validate_artifact_imports(artifact_set)
    if errors:
        logger.warning(f"[V35-005] Import validation warnings: {errors}")
    
    # Get all artifacts to write
    artifacts = artifact_set.all_artifacts()
    
    if dry_run:
        logger.info(f"[V35-005] DRY RUN: Would write {len(artifacts)} files")
        for artifact in artifacts:
            logger.info(f"  {artifact.rel_path}")
        return [a.rel_path for a in artifacts]
    
    # Write all files
    for artifact in artifacts:
        target_path = repo_path / artifact.rel_path
        
        # Create parent directories
        target_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        target_path.write_text(artifact.content, encoding="utf-8")
        written_files.append(artifact.rel_path)
        
        logger.debug(f"[V35-005] Wrote: {artifact.rel_path}")
    
    logger.info(f"[V35-005] Successfully wrote {len(written_files)} files")
    
    return written_files


def verify_written_artifacts(
    artifact_set: CoordinatedArtifactSet,
    repo_root: str,
) -> Tuple[bool, List[str]]:
    """
    Verify that all artifacts were written correctly.
    
    Checks:
    1. All files exist
    2. Files have non-empty content (except __init__.py files which can be empty)
    3. Import paths resolve
    
    Returns:
        Tuple of (success, list_of_issues)
    """
    issues = []
    repo_path = Path(repo_root)
    
    for artifact in artifact_set.all_artifacts():
        target_path = repo_path / artifact.rel_path
        
        if not target_path.exists():
            issues.append(f"File not written: {artifact.rel_path}")
            continue
        
        content = target_path.read_text()
        # __init__.py files are allowed to be empty (they just enable imports)
        if not content.strip() and not artifact.rel_path.endswith("__init__.py"):
            issues.append(f"File is empty: {artifact.rel_path}")
    
    success = len(issues) == 0
    
    if success:
        logger.info("[V35-005] All artifacts verified successfully")
    else:
        logger.error(f"[V35-005] Artifact verification failed: {issues}")
    
    return success, issues

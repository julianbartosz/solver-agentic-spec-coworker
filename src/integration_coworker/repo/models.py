"""
Repo integration models.

Defines structures for repository profiles, snapshots, and change sets.
"""
import warnings
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Union, TYPE_CHECKING
from pathlib import Path
from enum import Enum

if TYPE_CHECKING:
    from integration_coworker.repo.providers.base import SourceFile


class FrameworkArchetype(str, Enum):
    """
    Known framework archetypes for repository detection.
    
    These represent common frameworks with well-defined project structures
    that we can use as strong priors for code placement.
    """
    FASTAPI = "fastapi"
    DJANGO = "django"
    FLASK = "flask"
    NEXTJS = "nextjs"
    EXPRESS = "express"
    NESTJS = "nestjs"
    GENERIC_PYTHON = "generic_python"
    GENERIC_TYPESCRIPT = "generic_typescript"
    UNKNOWN = "unknown"


@dataclass
class DetectedProfile:
    """
    Result of the detection layer - identifies repo archetype with confidence.
    
    This is the output of detect_repo_profile() and input to build_effective_repo_profile().
    
    Attributes:
        archetype_name: Framework archetype (e.g., "fastapi", "django", "unknown")
        language: Primary language detected ("python", "typescript", etc.)
        confidence: Detection confidence score 0.0-1.0
        evidence: List of files/patterns that contributed to detection
        detected_paths: Key paths found during detection (src roots, test dirs, etc.)
        metadata: Additional detection metadata (framework version, etc.)
    """
    archetype_name: str  # e.g., "fastapi", "django", "unknown"
    language: str  # Primary language: "python", "typescript", "javascript"
    confidence: float  # 0.0 to 1.0
    evidence: List[str] = field(default_factory=list)  # Files/patterns that matched
    detected_paths: Dict[str, str] = field(default_factory=dict)  # Key paths found
    metadata: Dict[str, Any] = field(default_factory=dict)  # Extra info

    @property
    def is_high_confidence(self) -> bool:
        """Returns True if confidence >= 0.8 (use archetype defaults)."""
        return self.confidence >= 0.8

    @property
    def is_known_archetype(self) -> bool:
        """Returns True if archetype is a known framework (not generic/unknown)."""
        return self.archetype_name not in ("unknown", "generic_python", "generic_typescript")

    def should_use_archetype_defaults(self) -> bool:
        """
        Returns True if we should use archetype's predefined layout.
        
        Criteria: high confidence AND known archetype.
        """
        return self.is_high_confidence and self.is_known_archetype


@dataclass
class RepoProfile:
    """
    Metadata about a target repository's structure and conventions.
    
    This helps the system understand where to place generated code.
    
    Can be constructed from:
    1. Archetype defaults (high-confidence detection)
    2. Heuristic inference (low-confidence or unknown archetype)
    3. LLM-assisted refinement (when heuristics are unsure)
    4. Cached/persisted profile (subsequent runs)
    """
    name: str  # e.g., "next-js-app-router", "django-rest"
    archetype: Optional[str] = None  # High-level classification (e.g., "fastapi_service", "nextjs_app")
    framework: Optional[str] = None  # e.g., "nextjs", "django", "fastapi"
    language: str = "python"
    integrations_root: str = "integrations"  # Where to place integration code
    tests_root: str = "tests"  # Where to place test files
    conventions: Optional[Dict[str, Any]] = None  # Framework-specific patterns
    layout_hints: Optional[Dict[str, Any]] = None  # Additional layout information
    integration_hooks: Optional[Dict[str, Any]] = None  # Files to update (router, settings, etc.)

    # Detection metadata (populated by two-layer pipeline)
    detection_confidence: Optional[float] = None  # 0.0-1.0, from DetectedProfile
    detection_evidence: Optional[List[str]] = None  # Files/patterns that matched
    profile_source: Optional[str] = None  # "archetype", "heuristic", "llm", "cached"


@dataclass
class MockFile:
    """
    A simplified representation of a file for repo analysis.
    
    .. deprecated:: 2.1
        Use :class:`SourceFile` from ``integration_coworker.repo.providers`` instead.
        MockFile will be removed in v3.0.
    """
    path: str  # Relative path from repo root
    content: str  # File contents
    
    def __post_init__(self):
        """V2.1: Emit deprecation warning per GAP-06."""
        warnings.warn(
            "MockFile is deprecated and will be removed in v3.0. "
            "Use SourceFile from integration_coworker.repo.providers instead.",
            DeprecationWarning,
            stacklevel=3,  # Point to caller's caller (past dataclass machinery)
        )


@dataclass
class RepoSnapshot:
    """
    A snapshot of repository structure and key files.
    
    Used for understanding the target repo's layout without persisting everything.
    """
    repo_name: str
    owner: str
    # Files: accepts MockFile (deprecated) or SourceFile
    files: Dict[str, Union["MockFile", "SourceFile"]] = field(default_factory=dict)
    stats: Dict[str, Any] = field(default_factory=dict)  # Stats like total_files, total_size
    tree_markdown: Optional[str] = None  # Tree structure as markdown
    full_markdown: Optional[str] = None  # Full context including file samples
    repo_root: Optional[Path] = None  # Optional explicit root path


@dataclass
class FileChange:
    """Represents a single file change (create, update, or delete)."""
    rel_path: str  # Relative path from repo root
    change_type: str  # "create", "update", or "delete"
    content: Optional[str] = None  # New content (for create/update)
    original_content: Optional[str] = None  # Original content (for update)
    before: Optional[str] = None  # Alias for original_content
    after: Optional[str] = None  # Alias for content


@dataclass
class RepoChangeSet:
    """
    A set of changes to apply to a repository.
    
    Used for both planning (dry-run) and actual file writes.
    """
    repo_root: Path
    changes: List[FileChange] = field(default_factory=list)
    applied: bool = False  # Whether changes have been written to disk

    def files_created(self) -> List[FileChange]:
        """Get list of files to be created."""
        return [c for c in self.changes if c.change_type == "create"]

    def files_updated(self) -> List[FileChange]:
        """Get list of files to be updated."""
        return [c for c in self.changes if c.change_type == "update"]

    def files_deleted(self) -> List[FileChange]:
        """Get list of files to be deleted."""
        return [c for c in self.changes if c.change_type == "delete"]

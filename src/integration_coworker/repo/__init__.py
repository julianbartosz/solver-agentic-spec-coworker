"""
Repository integration package.

Provides tools for analyzing, profiling, and modifying target repositories.
"""
from integration_coworker.repo.models import (
    RepoProfile,
    RepoSnapshot,
    RepoChangeSet,
    FileChange,
    MockFile,
    DetectedProfile,
    FrameworkArchetype,
)
from integration_coworker.repo.detection import (
    detect_repo_profile,
    build_effective_repo_profile,
)
from integration_coworker.repo.profiles import (
    get_profile_by_name,
    detect_profile_from_repo,  # Legacy - prefer detection.detect_repo_profile
    REPO_PROFILES,
)
from integration_coworker.repo.context import (
    filesystem_repo_context_provider,
    repo_context_from_source,
)
from integration_coworker.repo.providers import (
    RepoProvider,
    LocalRepoProvider,
    RepoMetadata,
    SourceFile,
    FileType,
    get_provider,
)

# Legacy aliases for backward compatibility
FilesystemProvider = LocalRepoProvider
get_repo_provider = get_provider

__all__ = [
    # Models
    "RepoProfile",
    "RepoSnapshot",
    "RepoChangeSet",
    "FileChange",
    "MockFile",
    "DetectedProfile",
    "FrameworkArchetype",
    # Detection (M6 two-layer pipeline)
    "detect_repo_profile",
    "build_effective_repo_profile",
    # Profiles (legacy, prefer detection)
    "get_profile_by_name",
    "detect_profile_from_repo",
    "REPO_PROFILES",
    # Context
    "filesystem_repo_context_provider",
    "repo_context_from_source",
    # Providers (V2 abstraction)
    "RepoProvider",
    "LocalRepoProvider",
    "RepoMetadata",
    "SourceFile",
    "FileType",
    "get_provider",
    # Legacy aliases
    "FilesystemProvider",
    "get_repo_provider",
]

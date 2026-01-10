"""
Local Filesystem Spec Discovery

Scans a local repository for OpenAPI/Swagger specification files.
This enables spec auto-discovery to find specs that exist in the target
repo before falling back to remote sources (APIs.guru, web search).

Supported spec formats:
- OpenAPI 3.x (YAML/JSON)
- Swagger 2.0 (YAML/JSON)
- AsyncAPI 2.x (YAML/JSON)

File patterns searched:
- *api*.yaml, *api*.yml, *api*.json
- *openapi*.yaml, *openapi*.yml, *openapi*.json
- *swagger*.yaml, *swagger*.yml, *swagger*.json
- *asyncapi*.yaml, *asyncapi*.yml, *asyncapi*.json
- spec.yaml, spec.yml, spec.json
- specs/*.yaml, specs/*.yml, specs/*.json
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from integration_coworker.discovery.apis_guru import SpecCandidate

logger = logging.getLogger(__name__)

# File patterns that likely contain OpenAPI specs
SPEC_FILE_PATTERNS = [
    # OpenAPI naming patterns
    "*openapi*.yaml",
    "*openapi*.yml",
    "*openapi*.json",
    "openapi.yaml",
    "openapi.yml",
    "openapi.json",
    # Swagger naming patterns
    "*swagger*.yaml",
    "*swagger*.yml",
    "*swagger*.json",
    "swagger.yaml",
    "swagger.yml",
    "swagger.json",
    # AsyncAPI naming patterns
    "*asyncapi*.yaml",
    "*asyncapi*.yml",
    "*asyncapi*.json",
    # API naming patterns
    "*_api.yaml",
    "*_api.yml",
    "*_api.json",
    "*-api.yaml",
    "*-api.yml",
    "*-api.json",
    "api.yaml",
    "api.yml",
    "api.json",
    # Generic spec patterns
    "spec.yaml",
    "spec.yml",
    "spec.json",
]

# Directories commonly containing specs
SPEC_DIRECTORIES = [
    "",  # Root directory
    "specs",
    "spec",
    "api",
    "apis",
    "openapi",
    "swagger",
    "docs",
    "documentation",
    "schema",
    "schemas",
]

# Directories to exclude from search
EXCLUDED_DIRECTORIES = {
    ".git",
    ".github",
    ".vscode",
    ".idea",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    ".tox",
    ".nox",
    "site-packages",
}

# Maximum file size to consider (10MB)
MAX_SPEC_FILE_SIZE = 10 * 1024 * 1024


def _is_likely_openapi_content(content: Dict[str, Any]) -> Tuple[bool, str, str]:
    """
    Check if parsed content looks like an OpenAPI/Swagger/AsyncAPI spec.
    
    Returns:
        Tuple of (is_valid, spec_format, api_name)
    """
    # OpenAPI 3.x
    if "openapi" in content:
        version = str(content.get("openapi", ""))
        if version.startswith("3."):
            info = content.get("info", {})
            title = info.get("title", "Unknown API")
            return True, f"openapi-{version}", title
    
    # Swagger 2.0
    if "swagger" in content:
        version = str(content.get("swagger", ""))
        if version.startswith("2."):
            info = content.get("info", {})
            title = info.get("title", "Unknown API")
            return True, f"swagger-{version}", title
    
    # AsyncAPI
    if "asyncapi" in content:
        version = str(content.get("asyncapi", ""))
        info = content.get("info", {})
        title = info.get("title", "Unknown API")
        return True, f"asyncapi-{version}", title
    
    return False, "", ""


def _parse_spec_file(file_path: Path) -> Optional[Dict[str, Any]]:
    """
    Parse a potential spec file (YAML or JSON).
    
    Returns:
        Parsed content dict or None if parsing fails
    """
    try:
        # Check file size
        if file_path.stat().st_size > MAX_SPEC_FILE_SIZE:
            logger.debug(f"Skipping large file: {file_path}")
            return None
        
        content = file_path.read_text(encoding="utf-8")
        
        # Try YAML first (also handles JSON)
        try:
            return yaml.safe_load(content)
        except yaml.YAMLError:
            pass
        
        # Try JSON explicitly
        import json
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        
        return None
        
    except Exception as e:
        logger.debug(f"Failed to parse {file_path}: {e}")
        return None


def _score_spec_match(
    file_path: Path,
    spec_format: str,
    api_name: str,
    provider_hint: Optional[str] = None,
    keywords: Optional[List[str]] = None,
) -> float:
    """
    Score how well a local spec matches the search intent.
    
    Args:
        file_path: Path to the spec file
        spec_format: Detected format (openapi-3.x, swagger-2.0, etc.)
        api_name: API title from spec
        provider_hint: Optional provider hint from intent
        keywords: Optional keywords from intent
        
    Returns:
        Score from 0.0 to 1.0
    """
    score = 0.5  # Base score for any valid spec
    
    file_name = file_path.name.lower()
    api_name_lower = api_name.lower()
    
    # Boost for OpenAPI 3.x (preferred format)
    if spec_format.startswith("openapi-3."):
        score += 0.1
    
    # Boost for matching provider hint
    if provider_hint:
        provider_lower = provider_hint.lower()
        if provider_lower in file_name:
            score += 0.2
        if provider_lower in api_name_lower:
            score += 0.2
    
    # Boost for matching keywords
    if keywords:
        for keyword in keywords:
            keyword_lower = keyword.lower()
            if keyword_lower in file_name:
                score += 0.05
            if keyword_lower in api_name_lower:
                score += 0.05
    
    # Boost for standard naming conventions
    if any(pattern in file_name for pattern in ["openapi", "swagger", "api.yaml", "api.json"]):
        score += 0.05
    
    # Cap at 1.0
    return min(score, 1.0)


def search_local_filesystem(
    repo_root: Path,
    provider_hint: Optional[str] = None,
    keywords: Optional[List[str]] = None,
    max_results: int = 5,
) -> List[SpecCandidate]:
    """
    Search local filesystem for OpenAPI spec files.
    
    Args:
        repo_root: Root directory of the repository to search
        provider_hint: Optional provider hint from intent analysis
        keywords: Optional keywords from intent analysis
        max_results: Maximum number of candidates to return
        
    Returns:
        List of SpecCandidate objects for found specs
    """
    if not repo_root or not repo_root.exists():
        logger.debug(f"Repo root does not exist: {repo_root}")
        return []
    
    logger.info(f"Searching local filesystem for specs in: {repo_root}")
    
    candidates: List[SpecCandidate] = []
    checked_paths: set = set()
    
    # Search in common directories first
    for dir_name in SPEC_DIRECTORIES:
        search_dir = repo_root / dir_name if dir_name else repo_root
        
        if not search_dir.exists() or not search_dir.is_dir():
            continue
        
        # Check if this is an excluded directory
        if search_dir.name in EXCLUDED_DIRECTORIES:
            continue
        
        for pattern in SPEC_FILE_PATTERNS:
            # Bug #3 Fix: Use recursive glob (rglob) to find files in subdirectories
            # This fixes the issue where specs in subfolders (e.g. src/api.yaml) were missed
            # unless the specific subdirectory was in SPEC_DIRECTORIES.
            
            files_to_check = []
            
            # Recursive check - exclude common ignored dirs to prevent hanging
            # and false positives in large repos
            if not pattern.startswith("**"):
                # Use rglob but filter programmatically
                try:
                    for p in search_dir.rglob(pattern):
                        # Filter out hidden/ignored directories
                        if any(part.startswith('.') or part in ('node_modules', 'venv', 'env', '__pycache__', 'dist', 'build') for part in p.parts):
                            continue
                        files_to_check.append(p)
                except Exception as e:
                    logger.debug(f"rglob failed for {search_dir}: {e}")
                
                # Also include direct matches in the root (legacy behavior safety)
                # Ensure unique
                seen = {p.resolve() for p in files_to_check}
                for p in search_dir.glob(pattern):
                    if p.resolve() not in seen:
                         files_to_check.append(p)
            else:
                 # Pattern already has wildcards, trust it but maybe glob?
                 # Standard glob handles ** if supported (python pathlib does)
                 files_to_check.extend(list(search_dir.glob(pattern)))
            
            for file_path in files_to_check:
                # Skip if already checked
                abs_path = file_path.resolve()
                if abs_path in checked_paths:
                    continue
                checked_paths.add(abs_path)
                
                # Skip directories
                if file_path.is_dir():
                    continue
                
                # Skip files in excluded directories
                if any(excl in file_path.parts for excl in EXCLUDED_DIRECTORIES):
                    continue
                
                # Try to parse as spec
                content = _parse_spec_file(file_path)
                if content is None:
                    continue
                
                # Check if it's a valid spec
                is_valid, spec_format, api_name = _is_likely_openapi_content(content)
                if not is_valid:
                    continue
                
                logger.debug(f"Found local spec: {file_path} ({spec_format}: {api_name})")
                
                # Score the match
                score = _score_spec_match(
                    file_path=file_path,
                    spec_format=spec_format,
                    api_name=api_name,
                    provider_hint=provider_hint,
                    keywords=keywords,
                )
                
                # Create candidate with file:// URL
                # Use absolute path for consistency
                spec_url = f"file://{abs_path}"
                
                # Derive provider from file name or directory
                provider = _derive_provider_from_path(file_path, api_name)
                
                candidates.append(SpecCandidate(
                    provider=provider,
                    api_name=api_name,
                    spec_url=spec_url,
                    spec_format=spec_format,
                    score=score,
                ))
    
    # Also do a recursive search for any remaining patterns not found
    # Limit depth to avoid very deep searches
    if len(candidates) < max_results:
        candidates.extend(_recursive_search(
            repo_root=repo_root,
            checked_paths=checked_paths,
            provider_hint=provider_hint,
            keywords=keywords,
            max_depth=3,
            max_results=max_results - len(candidates),
        ))
    
    # Sort by score and return top results
    candidates.sort(key=lambda c: -c.score)
    
    if candidates:
        logger.info(f"Found {len(candidates)} local spec candidates")
    else:
        logger.debug("No local specs found in repository")
    
    return candidates[:max_results]


def _recursive_search(
    repo_root: Path,
    checked_paths: set,
    provider_hint: Optional[str],
    keywords: Optional[List[str]],
    max_depth: int,
    max_results: int,
) -> List[SpecCandidate]:
    """
    Recursively search for spec files with depth limit.
    """
    candidates: List[SpecCandidate] = []
    
    def search_dir(current_dir: Path, depth: int) -> None:
        if depth > max_depth or len(candidates) >= max_results:
            return
        
        try:
            for entry in current_dir.iterdir():
                if len(candidates) >= max_results:
                    return
                
                if entry.is_dir():
                    # Skip excluded directories
                    if entry.name in EXCLUDED_DIRECTORIES:
                        continue
                    # Recurse
                    search_dir(entry, depth + 1)
                    
                elif entry.is_file():
                    # Skip if already checked
                    abs_path = entry.resolve()
                    if abs_path in checked_paths:
                        continue
                    checked_paths.add(abs_path)
                    
                    # Check if filename matches any pattern
                    file_lower = entry.name.lower()
                    is_potential_spec = (
                        file_lower.endswith(('.yaml', '.yml', '.json')) and
                        any(kw in file_lower for kw in ['api', 'spec', 'openapi', 'swagger', 'asyncapi'])
                    )
                    
                    if not is_potential_spec:
                        continue
                    
                    # Try to parse
                    content = _parse_spec_file(entry)
                    if content is None:
                        continue
                    
                    is_valid, spec_format, api_name = _is_likely_openapi_content(content)
                    if not is_valid:
                        continue
                    
                    logger.debug(f"Found local spec (recursive): {entry}")
                    
                    score = _score_spec_match(
                        file_path=entry,
                        spec_format=spec_format,
                        api_name=api_name,
                        provider_hint=provider_hint,
                        keywords=keywords,
                    )
                    
                    spec_url = f"file://{abs_path}"
                    provider = _derive_provider_from_path(entry, api_name)
                    
                    candidates.append(SpecCandidate(
                        provider=provider,
                        api_name=api_name,
                        spec_url=spec_url,
                        spec_format=spec_format,
                        score=score,
                    ))
                    
        except PermissionError:
            pass  # Skip directories we can't read
    
    search_dir(repo_root, 0)
    return candidates


def _derive_provider_from_path(file_path: Path, api_name: str) -> str:
    """
    Derive a provider name from the file path or API name.
    """
    # Try to extract from filename
    stem = file_path.stem.lower()
    
    # Remove common suffixes
    for suffix in ['_api', '-api', '_spec', '-spec', '_openapi', '-openapi', '_swagger']:
        if stem.endswith(suffix):
            stem = stem[:-len(suffix)]
            break
    
    # Remove common prefixes
    for prefix in ['api_', 'api-', 'spec_', 'spec-', 'openapi_', 'openapi-']:
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    
    # If stem looks reasonable, use it
    if len(stem) >= 2 and stem not in ['spec', 'api', 'openapi', 'swagger']:
        return stem
    
    # Fall back to first word of API name
    if api_name:
        first_word = api_name.split()[0].lower()
        if first_word not in ['the', 'a', 'an']:
            return first_word
    
    return "local"

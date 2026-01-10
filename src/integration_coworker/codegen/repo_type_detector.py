"""
Repository Type Detection and Integration Strategy (V35-003 Fix)

This module detects the type of repository (CLI tool, web app, library, etc.)
and determines the appropriate integration strategy.

Problem (V35-003):
------------------
The coworker generated FastAPI router code for docformatter, which is a CLI tool
that doesn't use FastAPI. The coworker should detect the repo type and generate
appropriate integration code.

Solution:
---------
1. Detect repo archetype (CLI, web service, library)
2. Choose integration strategy based on archetype
3. Generate framework-appropriate code (no FastAPI for CLI tools)
4. Support multiple integration patterns

Repo Types:
-----------
- CLI Tool: Command-line application, no web framework
- Web Service: FastAPI, Flask, Django, Express, etc.
- Library: Reusable package without entry points
- Hybrid: Has both CLI and web interfaces
"""
import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class RepoType(str, Enum):
    """Types of repositories we can detect."""
    CLI_TOOL = "cli_tool"           # Command-line application
    WEB_SERVICE = "web_service"     # Web API/service
    LIBRARY = "library"             # Reusable library
    HYBRID = "hybrid"               # Both CLI and web
    UNKNOWN = "unknown"             # Could not determine


class IntegrationStrategy(str, Enum):
    """Strategies for integrating generated code."""
    MODULE_IMPORT = "module_import"    # Simple module import (CLI/Library)
    FASTAPI_ROUTER = "fastapi_router"  # FastAPI router pattern
    FLASK_BLUEPRINT = "flask_blueprint" # Flask blueprint pattern
    DJANGO_VIEW = "django_view"        # Django view pattern
    EXPRESS_ROUTE = "express_route"    # Express.js route pattern
    STANDALONE = "standalone"          # Standalone file, no integration


@dataclass
class RepoTypeDetectionResult:
    """Result of repo type detection."""
    repo_type: RepoType
    integration_strategy: IntegrationStrategy
    confidence: float  # 0.0 to 1.0
    evidence: List[str] = field(default_factory=list)
    
    # Detected framework (if web service)
    web_framework: Optional[str] = None
    
    # CLI tool info (if CLI)
    cli_entry_points: List[str] = field(default_factory=list)
    
    # Dependencies found
    dependencies: List[str] = field(default_factory=list)
    
    # Whether FastAPI should be used
    should_use_fastapi: bool = False
    
    # Notes for debugging
    detection_notes: List[str] = field(default_factory=list)


# =============================================================================
# DETECTION PATTERNS
# =============================================================================

# CLI tool indicators
CLI_INDICATORS = {
    "entry_points": [
        "console_scripts",    # In pyproject.toml/setup.py
        "scripts",            # In pyproject.toml
        "__main__.py",        # Module entry point
        "cli.py",             # Common CLI module
        "main.py",            # Common main module
    ],
    "cli_frameworks": [
        "click",
        "typer",
        "argparse",
        "fire",
        "docopt",
        "plumbum",
        "clint",
    ],
    "patterns_in_code": [
        "if __name__ ==",
        "argparse.ArgumentParser",
        "@click.command",
        "@app.command",
        "typer.Typer",
    ],
}

# Web framework indicators
WEB_INDICATORS = {
    "fastapi": {
        "imports": ["fastapi", "starlette"],
        "patterns": ["FastAPI()", "APIRouter()", "@app.get", "@app.post"],
        "files": ["app.py", "main.py", "api/"],
    },
    "flask": {
        "imports": ["flask"],
        "patterns": ["Flask(__name__)", "@app.route", "Blueprint"],
        "files": ["app.py", "application.py", "wsgi.py"],
    },
    "django": {
        "imports": ["django"],
        "patterns": ["INSTALLED_APPS", "urlpatterns"],
        "files": ["manage.py", "settings.py", "urls.py"],
    },
    "express": {
        "imports": ["express"],
        "patterns": ["express()", "app.get(", "app.post("],
        "files": ["app.js", "server.js", "index.js"],
    },
}

# Library indicators
LIBRARY_INDICATORS = {
    "files": ["__init__.py", "py.typed"],
    "no_entry_points": True,
    "has_setup_py_or_pyproject": True,
}


# =============================================================================
# DETECTION FUNCTIONS
# =============================================================================

def _check_cli_indicators(repo_root: Path) -> Dict[str, Any]:
    """Check for CLI tool indicators."""
    evidence = []
    cli_entry_points = []
    cli_frameworks_found = []
    
    # Check pyproject.toml for entry points
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            
            # Check for console_scripts or scripts
            if "[project.scripts]" in content or "[tool.poetry.scripts]" in content:
                evidence.append("Found CLI scripts in pyproject.toml")
                # Try to extract script names
                import re
                scripts = re.findall(r'(\w+)\s*=\s*["\']', content)
                cli_entry_points.extend(scripts)
            
            if "console_scripts" in content:
                evidence.append("Found console_scripts entry point")
            
            # Check for CLI framework dependencies
            content_lower = content.lower()
            for framework in CLI_INDICATORS["cli_frameworks"]:
                if framework in content_lower:
                    cli_frameworks_found.append(framework)
                    evidence.append(f"Found CLI framework dependency: {framework}")
        except Exception:
            pass
    
    # Check for __main__.py
    for pkg_dir in repo_root.glob("*/__main__.py"):
        evidence.append(f"Found __main__.py in {pkg_dir.parent.name}")
        cli_entry_points.append(str(pkg_dir))
    
    # Check for cli.py or main.py with CLI patterns
    for pattern_file in ["cli.py", "main.py"]:
        for found in repo_root.rglob(pattern_file):
            if found.is_file():
                try:
                    content = found.read_text()
                    for pattern in CLI_INDICATORS["patterns_in_code"]:
                        if pattern in content:
                            evidence.append(f"Found CLI pattern '{pattern}' in {found.name}")
                            break
                except Exception:
                    pass
    
    return {
        "evidence": evidence,
        "entry_points": cli_entry_points,
        "frameworks": cli_frameworks_found,
        "is_cli": bool(evidence),
    }


def _check_web_indicators(repo_root: Path) -> Dict[str, Any]:
    """Check for web framework indicators."""
    evidence = []
    framework = None
    
    for fw_name, indicators in WEB_INDICATORS.items():
        fw_evidence = []
        
        # Check for framework-specific files
        for file_pattern in indicators.get("files", []):
            if file_pattern.endswith('/'):
                # Directory pattern
                if (repo_root / file_pattern.rstrip('/')).is_dir():
                    fw_evidence.append(f"Found {file_pattern} directory")
            else:
                # File pattern
                if (repo_root / file_pattern).exists():
                    fw_evidence.append(f"Found {file_pattern}")
        
        # Check pyproject.toml/requirements.txt for imports
        for dep_file in ["pyproject.toml", "requirements.txt", "package.json"]:
            dep_path = repo_root / dep_file
            if dep_path.exists():
                try:
                    content = dep_path.read_text().lower()
                    for imp in indicators.get("imports", []):
                        if imp.lower() in content:
                            fw_evidence.append(f"Found {imp} in {dep_file}")
                except Exception:
                    pass
        
        if fw_evidence:
            evidence.extend(fw_evidence)
            if not framework:  # Take first detected framework
                framework = fw_name
    
    return {
        "evidence": evidence,
        "framework": framework,
        "is_web": bool(evidence),
    }


def _check_library_indicators(repo_root: Path) -> Dict[str, Any]:
    """Check for library indicators."""
    evidence = []
    
    # Check for package structure
    init_files = list(repo_root.glob("*/__init__.py"))
    if init_files:
        evidence.append(f"Found {len(init_files)} __init__.py files")
    
    # Check for py.typed (PEP 561)
    py_typed = list(repo_root.glob("*/py.typed"))
    if py_typed:
        evidence.append("Found py.typed marker")
    
    # Check for setup.py or pyproject.toml with package config
    if (repo_root / "setup.py").exists():
        evidence.append("Found setup.py")
    if (repo_root / "pyproject.toml").exists():
        evidence.append("Found pyproject.toml")
    
    return {
        "evidence": evidence,
        "is_library": bool(evidence),
    }


def _get_dependencies(repo_root: Path) -> List[str]:
    """Extract dependencies from repo."""
    deps = []
    
    # Check pyproject.toml
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            # Simple extraction - could be more sophisticated
            import re
            # Poetry style
            deps.extend(re.findall(r'^(\w[\w-]*)\s*=', content, re.MULTILINE))
            # PEP 621 style
            deps.extend(re.findall(r'"(\w[\w-]*)', content))
        except Exception:
            pass
    
    # Check requirements.txt
    requirements = repo_root / "requirements.txt"
    if requirements.exists():
        try:
            for line in requirements.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    # Extract package name
                    pkg = line.split('==')[0].split('>=')[0].split('<=')[0].split('[')[0]
                    deps.append(pkg.strip())
        except Exception:
            pass
    
    # Check package.json
    package_json = repo_root / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text())
            deps.extend(data.get("dependencies", {}).keys())
            deps.extend(data.get("devDependencies", {}).keys())
        except Exception:
            pass
    
    return list(set(deps))


def _determine_integration_strategy(
    repo_type: RepoType,
    web_framework: Optional[str],
) -> IntegrationStrategy:
    """Determine the best integration strategy based on repo type."""
    
    if repo_type == RepoType.CLI_TOOL:
        return IntegrationStrategy.MODULE_IMPORT
    
    if repo_type == RepoType.LIBRARY:
        return IntegrationStrategy.MODULE_IMPORT
    
    if repo_type == RepoType.WEB_SERVICE:
        if web_framework == "fastapi":
            return IntegrationStrategy.FASTAPI_ROUTER
        elif web_framework == "flask":
            return IntegrationStrategy.FLASK_BLUEPRINT
        elif web_framework == "django":
            return IntegrationStrategy.DJANGO_VIEW
        elif web_framework == "express":
            return IntegrationStrategy.EXPRESS_ROUTE
        else:
            # Default to FastAPI for unknown web frameworks
            return IntegrationStrategy.FASTAPI_ROUTER
    
    if repo_type == RepoType.HYBRID:
        # For hybrid, prefer module import (more flexible)
        return IntegrationStrategy.MODULE_IMPORT
    
    # Default to standalone
    return IntegrationStrategy.STANDALONE


# =============================================================================
# MAIN API
# =============================================================================

def detect_repo_type(repo_root: str) -> RepoTypeDetectionResult:
    """
    Detect the type of repository and recommend integration strategy.
    
    This is the main entry point for repo type detection.
    
    Args:
        repo_root: Path to repository root
        
    Returns:
        RepoTypeDetectionResult with type, strategy, and evidence
    """
    path = Path(repo_root)
    
    if not path.exists():
        return RepoTypeDetectionResult(
            repo_type=RepoType.UNKNOWN,
            integration_strategy=IntegrationStrategy.STANDALONE,
            confidence=0.0,
            evidence=["Repository path does not exist"],
        )
    
    # Run all detection checks
    cli_result = _check_cli_indicators(path)
    web_result = _check_web_indicators(path)
    lib_result = _check_library_indicators(path)
    deps = _get_dependencies(path)
    
    # Collect all evidence
    evidence = []
    evidence.extend(cli_result.get("evidence", []))
    evidence.extend(web_result.get("evidence", []))
    evidence.extend(lib_result.get("evidence", []))
    
    # Determine repo type
    is_cli = cli_result.get("is_cli", False)
    is_web = web_result.get("is_web", False)
    is_lib = lib_result.get("is_library", False)
    
    if is_cli and is_web:
        repo_type = RepoType.HYBRID
        confidence = 0.8
    elif is_cli:
        repo_type = RepoType.CLI_TOOL
        confidence = 0.9 if cli_result.get("frameworks") else 0.7
    elif is_web:
        repo_type = RepoType.WEB_SERVICE
        confidence = 0.9
    elif is_lib:
        repo_type = RepoType.LIBRARY
        confidence = 0.6
    else:
        repo_type = RepoType.UNKNOWN
        confidence = 0.3
    
    # Determine integration strategy
    web_framework = web_result.get("framework")
    strategy = _determine_integration_strategy(repo_type, web_framework)
    
    # Create result
    result = RepoTypeDetectionResult(
        repo_type=repo_type,
        integration_strategy=strategy,
        confidence=confidence,
        evidence=evidence,
        web_framework=web_framework,
        cli_entry_points=cli_result.get("entry_points", []),
        dependencies=deps,
        should_use_fastapi=(strategy == IntegrationStrategy.FASTAPI_ROUTER),
    )
    
    # Add detection notes
    result.detection_notes.append(f"Detected repo type: {repo_type.value}")
    result.detection_notes.append(f"Integration strategy: {strategy.value}")
    if web_framework:
        result.detection_notes.append(f"Web framework: {web_framework}")
    
    logger.info(
        f"[V35-003] Repo type detection: type={repo_type.value}, "
        f"strategy={strategy.value}, confidence={confidence:.2f}"
    )
    
    return result


def should_generate_fastapi_router(repo_root: str) -> bool:
    """
    Quick check: should we generate FastAPI router code for this repo?
    
    Args:
        repo_root: Path to repository
        
    Returns:
        True if FastAPI router pattern should be used
    """
    result = detect_repo_type(repo_root)
    return result.should_use_fastapi


def get_integration_template_type(repo_root: str) -> str:
    """
    Get the template type to use for integration code generation.
    
    Args:
        repo_root: Path to repository
        
    Returns:
        Template type: "module", "fastapi", "flask", "django", "express", "standalone"
    """
    result = detect_repo_type(repo_root)
    
    strategy_to_template = {
        IntegrationStrategy.MODULE_IMPORT: "module",
        IntegrationStrategy.FASTAPI_ROUTER: "fastapi",
        IntegrationStrategy.FLASK_BLUEPRINT: "flask",
        IntegrationStrategy.DJANGO_VIEW: "django",
        IntegrationStrategy.EXPRESS_ROUTE: "express",
        IntegrationStrategy.STANDALONE: "standalone",
    }
    
    return strategy_to_template.get(result.integration_strategy, "module")

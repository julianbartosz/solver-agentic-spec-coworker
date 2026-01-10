"""
Repository profiles for integration code placement.

Provides generic fallback profiles for Python and TypeScript projects.
Framework-specific profiles are now deprecated - use config files or
LLM inference instead (see ADR-0002).

Migration:
- Create a .integration-coworker.yaml in your repo root
- Or let the system auto-generate one via LLM inference
"""
import logging
from pathlib import Path
from typing import Optional, List

from integration_coworker.repo.models import RepoProfile

logger = logging.getLogger(__name__)


# =============================================================================
# TypeScript Config Detection Utility (Bug #2 Fix)
# =============================================================================

# All known tsconfig file patterns - handles monorepos and multi-project setups
TSCONFIG_PATTERNS: List[str] = [
    "tsconfig.json",        # Standard
    "tsconfig.base.json",   # Nx monorepos, base config
    "tsconfig.app.json",    # Angular apps
    "tsconfig.lib.json",    # Library projects
    "tsconfig.node.json",   # Node-specific config
    "tsconfig.build.json",  # Build-specific config
]


def has_typescript_config(repo_path: Path) -> bool:
    """
    Check if repository has any TypeScript configuration file.
    
    Bug #2 Fix: Handles all common tsconfig variants including:
    - tsconfig.json (standard)
    - tsconfig.base.json (Nx monorepos)
    - tsconfig.app.json, tsconfig.lib.json (Angular)
    - Any tsconfig*.json pattern
    
    Args:
        repo_path: Path to repository root
    
    Returns:
        True if any tsconfig file exists, False otherwise
    """
    # First check exact known patterns (fast)
    for pattern in TSCONFIG_PATTERNS:
        if (repo_path / pattern).exists():
            logger.debug(f"Found TypeScript config: {pattern}")
            return True
    
    # Fallback: glob for any tsconfig*.json (catches edge cases)
    tsconfig_files = list(repo_path.glob("tsconfig*.json"))
    if tsconfig_files:
        logger.debug(f"Found TypeScript config via glob: {tsconfig_files[0].name}")
        return True
    
    return False


# =============================================================================
# Generic Fallback Profiles
# =============================================================================

GENERIC_PYTHON_PROFILE = RepoProfile(
    name="generic-python",
    framework="generic",
    language="python",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "flows/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    },
)

GENERIC_TYPESCRIPT_PROFILE = RepoProfile(
    name="generic-typescript",
    framework="generic",
    language="typescript",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.ts",
        "flow_module_pattern": "flows/{provider}/{task}.ts",
        "test_module_pattern": "{provider}/{task}.test.ts",
    },
)

# Bug #86 Fix: Add generic profiles for Go, Java, Ruby, C#, JavaScript

GENERIC_JAVASCRIPT_PROFILE = RepoProfile(
    name="generic-javascript",
    framework="generic",
    language="javascript",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.js",
        "flow_module_pattern": "flows/{provider}/{task}.js",
        "test_module_pattern": "{provider}/{task}.test.js",
    },
)

GENERIC_GO_PROFILE = RepoProfile(
    name="generic-go",
    framework="generic",
    language="go",
    integrations_root="internal/integrations",
    tests_root="internal/integrations",  # Go tests are co-located
    conventions={
        "client_module_pattern": "clients/{provider}.go",
        "flow_module_pattern": "flows/{provider}_{task}.go",
        "test_module_pattern": "{provider}_{task}_test.go",
    },
)

GENERIC_JAVA_PROFILE = RepoProfile(
    name="generic-java",
    framework="generic",
    language="java",
    integrations_root="src/main/java/integrations",
    tests_root="src/test/java/integrations",
    conventions={
        "client_module_pattern": "clients/{Provider}Client.java",
        "flow_module_pattern": "flows/{Provider}{Task}Flow.java",
        "test_module_pattern": "{Provider}{Task}FlowTest.java",
    },
)

GENERIC_RUBY_PROFILE = RepoProfile(
    name="generic-ruby",
    framework="generic",
    language="ruby",
    integrations_root="lib/integrations",
    tests_root="spec/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.rb",
        "flow_module_pattern": "flows/{provider}_{task}.rb",
        "test_module_pattern": "{provider}_{task}_spec.rb",
    },
)

GENERIC_CSHARP_PROFILE = RepoProfile(
    name="generic-csharp",
    framework="generic",
    language="csharp",
    integrations_root="Integrations",
    tests_root="Integrations.Tests",
    conventions={
        "client_module_pattern": "Clients/{Provider}Client.cs",
        "flow_module_pattern": "Flows/{Provider}{Task}Flow.cs",
        "test_module_pattern": "{Provider}{Task}FlowTests.cs",
    },
)

# Mapping from language to generic profile
GENERIC_PROFILES_BY_LANGUAGE = {
    "python": GENERIC_PYTHON_PROFILE,
    "typescript": GENERIC_TYPESCRIPT_PROFILE,
    "javascript": GENERIC_JAVASCRIPT_PROFILE,
    "go": GENERIC_GO_PROFILE,
    "java": GENERIC_JAVA_PROFILE,
    "ruby": GENERIC_RUBY_PROFILE,
    "csharp": GENERIC_CSHARP_PROFILE,
}


# =============================================================================
# Legacy Profiles (DEPRECATED per ADR-0002 - kept for test compatibility)
# =============================================================================

# Mock profile for testing - kept for backward compatibility
SUBATOMIC_MOCK_PROFILE = RepoProfile(
    name="subatomic_mock_service",
    archetype="fastapi_service",
    framework="fastapi",
    language="python",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "flows/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    },
    layout_hints={
        "clients_dir": "src/integrations/clients",
        "workflows_dir": "src/integrations/flows",
        "tests_dir": "tests/integrations",
    },
    integration_hooks={
        "router_file": "src/app/router.py",
        "router_registration_marker": "# <AUTO_INTEGRATION_MARKER>",
        "settings_file": "src/app/settings.py",
        "settings_marker": "# <AUTO_INTEGRATION_SETTINGS_MARKER>",
    },
)

# Keep framework profiles for backward compatibility with tests
NEXTJS_APP_ROUTER_PROFILE = RepoProfile(
    name="next-js-app-router",
    framework="nextjs",
    language="typescript",
    integrations_root="lib/integrations",
    tests_root="__tests__/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.ts",
        "flow_module_pattern": "flows/{provider}/{task}.ts",
        "test_module_pattern": "{provider}/{task}.test.ts",
    }
)

DJANGO_REST_PROFILE = RepoProfile(
    name="django-rest",
    framework="django",
    language="python",
    integrations_root="integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "services/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    }
)

FASTAPI_PROFILE = RepoProfile(
    name="fastapi",
    framework="fastapi",
    language="python",
    integrations_root="app/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "services/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    }
)

FLASK_PROFILE = RepoProfile(
    name="flask",
    framework="flask",
    language="python",
    integrations_root="app/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.py",
        "flow_module_pattern": "services/{provider}_{task}.py",
        "test_module_pattern": "test_{provider}_{task}.py",
    },
    layout_hints={
        "clients_dir": "app/integrations/clients",
        "services_dir": "app/integrations/services",
        "tests_dir": "tests/integrations",
    },
)

EXPRESS_PROFILE = RepoProfile(
    name="express",
    framework="express",
    language="typescript",
    integrations_root="src/integrations",
    tests_root="tests/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.ts",
        "flow_module_pattern": "services/{provider}/{task}.ts",
        "test_module_pattern": "{provider}/{task}.test.ts",
    },
    layout_hints={
        "clients_dir": "src/integrations/clients",
        "services_dir": "src/integrations/services",
        "tests_dir": "tests/integrations",
    },
)

NESTJS_PROFILE = RepoProfile(
    name="nestjs",
    framework="nestjs",
    language="typescript",
    integrations_root="src/integrations",
    tests_root="test/integrations",
    conventions={
        "client_module_pattern": "clients/{provider}.client.ts",
        "flow_module_pattern": "services/{provider}-{task}.service.ts",
        "test_module_pattern": "{provider}-{task}.service.spec.ts",
    },
    layout_hints={
        "clients_dir": "src/integrations/clients",
        "services_dir": "src/integrations/services",
        "tests_dir": "test/integrations",
        "module_file": "src/integrations/integrations.module.ts",
    },
)

# Registry for backward compatibility with tests
REPO_PROFILES = {
    "subatomic-mock": SUBATOMIC_MOCK_PROFILE,
    "next-js-app-router": NEXTJS_APP_ROUTER_PROFILE,
    "django-rest": DJANGO_REST_PROFILE,
    "fastapi": FASTAPI_PROFILE,
    "flask": FLASK_PROFILE,
    "express": EXPRESS_PROFILE,
    "nestjs": NESTJS_PROFILE,
    "generic-python": GENERIC_PYTHON_PROFILE,
    "generic-typescript": GENERIC_TYPESCRIPT_PROFILE,
    "generic-javascript": GENERIC_JAVASCRIPT_PROFILE,
    "generic-go": GENERIC_GO_PROFILE,
    "generic-java": GENERIC_JAVA_PROFILE,
    "generic-ruby": GENERIC_RUBY_PROFILE,
    "generic-csharp": GENERIC_CSHARP_PROFILE,
}


def get_profile_by_name(name: str) -> RepoProfile:
    """
    Get a repo profile by name.
    
    Args:
        name: Profile name (e.g., "next-js-app-router")
    
    Returns:
        RepoProfile instance
    
    Raises:
        KeyError: If profile not found
    """
    return REPO_PROFILES[name]


def _detect_framework_from_python_deps(repo_path: Path) -> Optional[str]:
    """
    Detect Python framework from dependency files AND file structure.
    
    Checks:
    1. pyproject.toml dependencies
    2. requirements.txt dependencies
    3. Django file structure (manage.py + settings.py)
    4. Flask imports in app.py
    
    Returns:
        Framework name ('fastapi', 'django', 'flask') or None
    """
    # Priority 1: Check pyproject.toml for explicit dependencies
    pyproject = repo_path / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text().lower()
            # Simple check for framework names in dependencies section
            if "fastapi" in content:
                return "fastapi"
            elif "django" in content:
                return "django"
            elif "flask" in content:
                return "flask"
        except IOError:
            pass
    
    # Priority 2: Check requirements.txt for explicit dependencies
    requirements = repo_path / "requirements.txt"
    if requirements.exists():
        try:
            content = requirements.read_text().lower()
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                # Extract package name (before any version specifiers)
                pkg = line.split("[")[0].split(">=")[0].split("<=")[0].split("==")[0].split("~=")[0].split("<")[0].split(">")[0].strip()
                if pkg == "fastapi":
                    return "fastapi"
                elif pkg == "django":
                    return "django"
                elif pkg == "flask":
                    return "flask"
        except IOError:
            pass
    
    # Priority 3: Django file structure detection
    # Django projects have manage.py + (settings.py OR settings/ directory OR project/settings.py)
    manage_py = repo_path / "manage.py"
    if manage_py.exists():
        # Check for settings.py in root
        if (repo_path / "settings.py").exists():
            return "django"
        # Check for settings/ directory
        if (repo_path / "settings").is_dir():
            return "django"
        # Check for nested settings (e.g., myproject/settings.py)
        for subdir in repo_path.iterdir():
            if subdir.is_dir() and (subdir / "settings.py").exists():
                return "django"
    
    # Priority 4: Flask import detection in app.py
    app_py = repo_path / "app.py"
    if app_py.exists():
        try:
            content = app_py.read_text()
            if "from flask import" in content or "import flask" in content.lower():
                return "flask"
        except IOError:
            pass
    
    return None


def _detect_framework_from_node_deps(repo_path: Path) -> Optional[str]:
    """
    Detect Node.js/TypeScript framework from package.json AND config files.
    
    Checks:
    1. package.json dependencies
    2. next.config.{js,mjs,ts} files
    3. @nestjs/common in package.json
    
    Returns:
        Framework name ('nextjs', 'express', 'nestjs') or None
    """
    import json
    
    package_json = repo_path / "package.json"
    if not package_json.exists():
        return None
    
    # Priority 1: Check for Next.js config files (authoritative)
    next_config_patterns = ["next.config.js", "next.config.mjs", "next.config.ts"]
    for config_file in next_config_patterns:
        if (repo_path / config_file).exists():
            return "nextjs"
    
    try:
        pkg_data = json.loads(package_json.read_text())
        deps = pkg_data.get("dependencies", {})
        dev_deps = pkg_data.get("devDependencies", {})
        all_deps = {**deps, **dev_deps}
        
        # Priority 2: Check for NestJS (most specific)
        if "@nestjs/core" in all_deps or "@nestjs/common" in all_deps:
            return "nestjs"
        
        # Priority 3: Check for Next.js dependency
        if "next" in all_deps:
            return "nextjs"
        
        # Priority 4: Check for Express
        if "express" in all_deps:
            return "express"
    except (json.JSONDecodeError, IOError):
        pass
    
    return None


def detect_profile_from_repo(repo_root) -> RepoProfile:
    """
    Profile detection with config-first approach and framework awareness.
    
    Bug #86/#87 Fix: Now supports all 7 languages (Python, TypeScript,
    JavaScript, Go, Java, Ruby, C#) using config-file-first detection,
    PLUS framework detection for backward compatibility.
    
    Detection priority (per ADR-0002):
    1. Config file (.integration-coworker.yaml) - highest priority
    2. Framework-specific profiles (fastapi, flask, django, nextjs, express, nestjs)
    3. Language-specific generic profiles (python, typescript, javascript, go, java, ruby, csharp)
    4. File extension fallback
    5. Ultimate fallback: generic Python
    
    Args:
        repo_root: Path to repository root (can be None)
    
    Returns:
        RepoProfile - from config file, framework-specific, or generic
    """
    if repo_root is None:
        profile = GENERIC_PYTHON_PROFILE.model_copy() if hasattr(GENERIC_PYTHON_PROFILE, 'model_copy') else GENERIC_PYTHON_PROFILE
        profile.profile_source = "generic_fallback"
        return profile

    repo_path = Path(repo_root)
    
    if not repo_path.exists():
        profile = GENERIC_PYTHON_PROFILE.model_copy() if hasattr(GENERIC_PYTHON_PROFILE, 'model_copy') else GENERIC_PYTHON_PROFILE
        profile.profile_source = "generic_fallback"
        return profile
    
    # =========================================================================
    # PHASE 0: Config file first (per ADR-0002)
    # =========================================================================
    config_file = repo_path / ".integration-coworker.yaml"
    if config_file.exists():
        try:
            from integration_coworker.repo.config_schema import load_config, config_to_profile
            config = load_config(config_file)
            if config is not None:
                profile = config_to_profile(config)
                profile.profile_source = "config_file"
                logger.debug(f"Loaded profile from config file: {profile.name}")
                return profile
        except Exception as e:
            logger.warning(f"Failed to load config file {config_file}: {e}")
            # Fall through to detection
    
    # =========================================================================
    # PHASE 1: Check for languages without framework detection (Go, Java, Ruby, C#)
    # These languages don't have framework-specific profiles, return generic
    # =========================================================================
    
    # Go: go.mod
    if (repo_path / "go.mod").exists() or (repo_path / "go.sum").exists():
        logger.debug("Detected Go repo via go.mod")
        profile = GENERIC_GO_PROFILE.model_copy() if hasattr(GENERIC_GO_PROFILE, 'model_copy') else GENERIC_GO_PROFILE
        profile.profile_source = "generic_fallback"
        return profile
    
    # Java: pom.xml or build.gradle
    if (repo_path / "pom.xml").exists():
        logger.debug("Detected Java repo via pom.xml (Maven)")
        profile = GENERIC_JAVA_PROFILE.model_copy() if hasattr(GENERIC_JAVA_PROFILE, 'model_copy') else GENERIC_JAVA_PROFILE
        profile.profile_source = "generic_fallback"
        return profile
    if (repo_path / "build.gradle").exists() or (repo_path / "build.gradle.kts").exists():
        logger.debug("Detected Java repo via build.gradle (Gradle)")
        profile = GENERIC_JAVA_PROFILE.model_copy() if hasattr(GENERIC_JAVA_PROFILE, 'model_copy') else GENERIC_JAVA_PROFILE
        profile.profile_source = "generic_fallback"
        return profile
    
    # Ruby: Gemfile
    if (repo_path / "Gemfile").exists():
        logger.debug("Detected Ruby repo via Gemfile")
        profile = GENERIC_RUBY_PROFILE.model_copy() if hasattr(GENERIC_RUBY_PROFILE, 'model_copy') else GENERIC_RUBY_PROFILE
        profile.profile_source = "generic_fallback"
        return profile
    
    # C#: .csproj or .sln
    if any(repo_path.glob("*.csproj")) or any(repo_path.glob("*.sln")):
        logger.debug("Detected C# repo via .csproj/.sln")
        profile = GENERIC_CSHARP_PROFILE.model_copy() if hasattr(GENERIC_CSHARP_PROFILE, 'model_copy') else GENERIC_CSHARP_PROFILE
        profile.profile_source = "generic_fallback"
        return profile
    
    # =========================================================================
    # PHASE 2: Framework detection for Python/TypeScript/JavaScript
    # =========================================================================
    
    # Check TypeScript/JavaScript first (via package.json)
    package_json_exists = (repo_path / "package.json").exists()
    has_tsconfig = has_typescript_config(repo_path)
    
    if package_json_exists:
        # Detect Node.js framework
        node_framework = _detect_framework_from_node_deps(repo_path)
        
        if node_framework == "nestjs":
            logger.debug("Detected NestJS framework from package.json")
            profile = NESTJS_PROFILE.model_copy() if hasattr(NESTJS_PROFILE, 'model_copy') else NESTJS_PROFILE
            profile.profile_source = "archetype"
            return profile
        elif node_framework == "nextjs":
            logger.debug("Detected Next.js framework from package.json")
            profile = NEXTJS_APP_ROUTER_PROFILE.model_copy() if hasattr(NEXTJS_APP_ROUTER_PROFILE, 'model_copy') else NEXTJS_APP_ROUTER_PROFILE
            profile.profile_source = "archetype"
            return profile
        elif node_framework == "express":
            logger.debug("Detected Express framework from package.json")
            profile = EXPRESS_PROFILE.model_copy() if hasattr(EXPRESS_PROFILE, 'model_copy') else EXPRESS_PROFILE
            profile.profile_source = "archetype"
            return profile
        
        # No framework detected - return generic based on TypeScript vs JavaScript
        if has_tsconfig:
            logger.debug("Detected TypeScript repo via tsconfig (no framework)")
            profile = GENERIC_TYPESCRIPT_PROFILE.model_copy() if hasattr(GENERIC_TYPESCRIPT_PROFILE, 'model_copy') else GENERIC_TYPESCRIPT_PROFILE
            profile.profile_source = "generic_fallback"
            return profile
        else:
            logger.debug("Detected JavaScript repo via package.json (no framework)")
            profile = GENERIC_JAVASCRIPT_PROFILE.model_copy() if hasattr(GENERIC_JAVASCRIPT_PROFILE, 'model_copy') else GENERIC_JAVASCRIPT_PROFILE
            profile.profile_source = "generic_fallback"
            return profile
    
    # Check Python (via pyproject.toml or requirements.txt)
    pyproject = repo_path / "pyproject.toml"
    requirements = repo_path / "requirements.txt"
    setup_py = repo_path / "setup.py"
    has_python_config = pyproject.exists() or requirements.exists() or setup_py.exists()
    
    # Check for Python files (for file-structure based detection)
    has_python_files = any(repo_path.glob("*.py")) or any(repo_path.glob("**/*.py"))
    
    # Run Python framework detection if config exists OR Python files exist
    if has_python_config or has_python_files:
        # Detect Python framework (checks both deps and file structure)
        python_framework = _detect_framework_from_python_deps(repo_path)
        
        if python_framework == "fastapi":
            logger.debug("Detected FastAPI framework from dependencies")
            profile = FASTAPI_PROFILE.model_copy() if hasattr(FASTAPI_PROFILE, 'model_copy') else FASTAPI_PROFILE
            profile.profile_source = "archetype"
            return profile
        elif python_framework == "django":
            logger.debug("Detected Django framework from file structure")
            profile = DJANGO_REST_PROFILE.model_copy() if hasattr(DJANGO_REST_PROFILE, 'model_copy') else DJANGO_REST_PROFILE
            profile.profile_source = "archetype"
            return profile
        elif python_framework == "flask":
            logger.debug("Detected Flask framework from dependencies or imports")
            profile = FLASK_PROFILE.model_copy() if hasattr(FLASK_PROFILE, 'model_copy') else FLASK_PROFILE
            profile.profile_source = "archetype"
            return profile
        
        # No framework detected - return generic Python if has config
        if has_python_config:
            logger.debug("Detected Python repo (no framework)")
            profile = GENERIC_PYTHON_PROFILE.model_copy() if hasattr(GENERIC_PYTHON_PROFILE, 'model_copy') else GENERIC_PYTHON_PROFILE
            profile.profile_source = "generic_fallback"
            return profile
    
    # =========================================================================
    # PHASE 3: Fallback - check file extensions
    # =========================================================================
    extension_language_map = [
        ("*.go", GENERIC_GO_PROFILE),
        ("**/*.go", GENERIC_GO_PROFILE),
        ("*.java", GENERIC_JAVA_PROFILE),
        ("**/*.java", GENERIC_JAVA_PROFILE),
        ("*.rb", GENERIC_RUBY_PROFILE),
        ("**/*.rb", GENERIC_RUBY_PROFILE),
        ("*.cs", GENERIC_CSHARP_PROFILE),
        ("**/*.cs", GENERIC_CSHARP_PROFILE),
        ("*.ts", GENERIC_TYPESCRIPT_PROFILE),
        ("**/*.ts", GENERIC_TYPESCRIPT_PROFILE),
        ("*.py", GENERIC_PYTHON_PROFILE),
        ("**/*.py", GENERIC_PYTHON_PROFILE),
    ]
    
    for pattern, profile_template in extension_language_map:
        if any(repo_path.glob(pattern)):
            profile = profile_template.model_copy() if hasattr(profile_template, 'model_copy') else profile_template
            profile.profile_source = "generic_fallback"
            logger.debug(f"Detected {profile.language} repo via {pattern} files")
            return profile
    
    # Ultimate fallback
    profile = GENERIC_PYTHON_PROFILE.model_copy() if hasattr(GENERIC_PYTHON_PROFILE, 'model_copy') else GENERIC_PYTHON_PROFILE
    profile.profile_source = "generic_fallback"
    return profile

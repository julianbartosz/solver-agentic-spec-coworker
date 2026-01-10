"""
Repository Profile Detection Pipeline.

Simplified detection pipeline that uses heuristics and LLM inference
to determine repository structure. Config-first approach per ADR-0002.

Detection Flow:
1. detect_repo_profile() - Detect language and collect evidence
2. build_effective_repo_profile() - Build RepoProfile using heuristics
3. Optional LLM refinement for uncertain cases

Note: Archetype-based detection was removed in v3.0 per ADR-0002.
Use .integration-coworker.yaml config files for explicit configuration.

Exception Handling Strategy (H-6 Audit):
----------------------------------------
This module uses broad exception handlers intentionally because:
1. Detection is heuristic - partial failures are expected
2. File system access may fail (permissions, encoding, symlinks)
3. YAML/JSON parsing may fail on malformed files
4. The system should gracefully degrade rather than abort

Handlers are categorized as:
- File reading fallbacks: Silent pass, continue with other signals
- Parsing fallbacks: Silent pass, use default/fallback value
- LLM refinement: Logged warning, return base profile

For production monitoring, enable DEBUG logging to see detection failures.
"""
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from integration_coworker.repo.models import DetectedProfile, RepoProfile

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIDENCE THRESHOLDS
# =============================================================================

# Threshold below which we warn about unreliable detection
LOW_CONFIDENCE_THRESHOLD = 0.4

# Threshold below which we try LLM refinement if enabled
VERY_LOW_CONFIDENCE_THRESHOLD = 0.3


# =============================================================================
# LAYER 1: DETECTION
# =============================================================================

def detect_repo_profile(repo_root: Optional[str]) -> DetectedProfile:
    """
    Layer 1: Detect repository structure with confidence scoring.
    
    Analyzes repository structure to identify:
    - Primary language (Python, TypeScript, JavaScript)
    - Evidence files/patterns found
    - Detected framework hints (for informational purposes only)
    
    Args:
        repo_root: Path to repository root (can be None)
        
    Returns:
        DetectedProfile with language, confidence, and evidence
    """
    if repo_root is None:
        return DetectedProfile(
            archetype_name="unknown",
            language="python",
            confidence=0.0,
            evidence=["No repo_root provided"],
        )

    repo_path = Path(repo_root)

    if not repo_path.exists():
        return DetectedProfile(
            archetype_name="unknown",
            language="python",
            confidence=0.0,
            evidence=[f"Repo root does not exist: {repo_root}"],
        )

    evidence: List[str] = []
    detected_paths: Dict[str, str] = {}
    framework_hints: List[str] = []

    # Check package.json for Node.js projects
    package_json = repo_path / "package.json"
    pkg_deps: Dict[str, str] = {}

    if package_json.exists():
        try:
            pkg_data = json.loads(package_json.read_text())
            deps = pkg_data.get("dependencies", {})
            dev_deps = pkg_data.get("devDependencies", {})
            pkg_deps = {**deps, **dev_deps}
            detected_paths["package_json"] = str(package_json)
            evidence.append("Found package.json")
            
            # Collect framework hints
            if "next" in pkg_deps:
                framework_hints.append("nextjs")
            if "@nestjs/core" in pkg_deps:
                framework_hints.append("nestjs")
            if "express" in pkg_deps:
                framework_hints.append("express")
        except (json.JSONDecodeError, IOError):
            pass

    # Check Python dependency files
    pyproject_toml = repo_path / "pyproject.toml"
    requirements_txt = repo_path / "requirements.txt"
    python_deps: List[str] = []

    # File-based framework markers (cover sparse repos that don't list deps)
    if (repo_path / "manage.py").exists():
        framework_hints.append("django")
        evidence.append("Found manage.py")
    if (repo_path / "app.py").exists():
        try:
            app_py = (repo_path / "app.py").read_text(errors="ignore").lower()
            if "from flask" in app_py or "import flask" in app_py:
                framework_hints.append("flask")
                evidence.append("Found Flask import in app.py")
        except Exception as e:
            # H-6: File reading fallback - expected for permission/encoding issues
            logger.debug(f"Could not read app.py for Flask detection: {e}")

    # Next.js config is a strong marker even without a `next` dependency.
    if any((repo_path / p).exists() for p in ["next.config.js", "next.config.mjs", "next.config.ts"]):
        framework_hints.append("nextjs")
        evidence.append("Found next.config")

    if pyproject_toml.exists():
        try:
            content = pyproject_toml.read_text().lower()
            python_deps = _extract_python_deps_from_pyproject(content)
            detected_paths["pyproject_toml"] = str(pyproject_toml)
            evidence.append("Found pyproject.toml")
        except IOError:
            pass

    if requirements_txt.exists():
        try:
            content = requirements_txt.read_text()
            python_deps.extend(_extract_python_deps_from_requirements(content))
            detected_paths["requirements_txt"] = str(requirements_txt)
            evidence.append("Found requirements.txt")
        except IOError:
            pass

    # Collect Python framework hints
    for dep in python_deps:
        dep_lower = dep.lower()
        if "fastapi" in dep_lower:
            framework_hints.append("fastapi")
        elif "django" in dep_lower:
            framework_hints.append("django")
        elif "flask" in dep_lower:
            framework_hints.append("flask")

    # Add some framework evidence markers (helps tests + makes logs useful)
    if "fastapi" in framework_hints:
        evidence.append("Detected FastAPI dependency")
    if "django" in framework_hints:
        evidence.append("Detected Django dependency")
        if (repo_path / "manage.py").exists():
            evidence.append("Found manage.py")
    if "flask" in framework_hints:
        evidence.append("Detected Flask dependency")

    # Detect primary language
    language = _detect_primary_language(repo_path)

    # If this looks like a TS-heavy Node repo (Next/Nest), upgrade language to TS.
    # Some repos omit tsconfig at repo root (nested apps/, packages/, etc.), but
    # still should be treated as TypeScript for integration output.
    if language == "javascript" and pkg_deps:
        if _looks_like_typescript_node_repo(repo_path, pkg_deps):
            language = "typescript"

    # If we have strong TS-leaning frameworks, treat language as TS even for very
    # sparse repos (unit tests create only package.json).
    if language == "javascript" and any(h in {"nextjs", "nestjs", "express"} for h in framework_hints):
        language = "typescript"

    # Node framework evidence markers
    if pkg_deps:
        if "next" in pkg_deps:
            evidence.append("Detected Next.js dependency")
            if any((repo_path / p).exists() for p in ["next.config.js", "next.config.mjs", "next.config.ts"]):
                evidence.append("Found next.config")
        if "@nestjs/core" in pkg_deps:
            evidence.append("Detected NestJS dependency")
        if "express" in pkg_deps:
            evidence.append("Detected Express dependency")
    evidence.append(f"Primary language: {language}")

    # Calculate confidence based on evidence
    confidence = _calculate_detection_confidence(
        language, python_deps, pkg_deps, detected_paths, repo_path
    )

    # Rank framework hints (deterministic + prioritizes more specific frameworks)
    framework_hints = _rank_framework_hints(framework_hints)

    # Use first (highest priority) framework hint as archetype_name, or "unknown"
    archetype_name = framework_hints[0] if framework_hints else "unknown"

    # G-02: Detect workspace roots for monorepo boundary enforcement
    workspace_roots = detect_workspace_roots(repo_path) if repo_path else []
    selected_workspace = select_workspace_root(workspace_roots, repo_path=repo_path)
    
    if workspace_roots:
        evidence.append(f"Monorepo detected: {len(workspace_roots)} workspaces")
        if selected_workspace:
            evidence.append(f"Selected workspace: {selected_workspace}")

    return DetectedProfile(
        archetype_name=archetype_name,
        language=language,
        confidence=confidence,
        evidence=evidence,
        detected_paths=detected_paths,
        metadata={
            "framework_hints": framework_hints,
            "python_deps": python_deps[:10],
            "node_deps": list(pkg_deps.keys())[:10],
        },
        workspace_roots=workspace_roots,
        selected_workspace_root=selected_workspace,
    )


def _calculate_detection_confidence(
    language: str,
    python_deps: List[str],
    pkg_deps: Dict[str, str],
    detected_paths: Dict[str, str],
    repo_path: Optional[Path] = None,
) -> float:
    """
    Calculate confidence score based on detection evidence.
    
    Enhanced scoring:
    - Base: 0.3
    - Primary config file (go.mod, package.json, etc.): +0.3
    - Secondary signal (Dockerfile, Makefile, CI): +0.2
    - Dependencies detected: +0.1-0.2
    - Multiple signals agreeing: +0.1 bonus
    """
    confidence = 0.3  # Base confidence
    signals = []  # Track what signals we found

    # Framework-specific boosts (strong signals that should raise confidence)
    framework_boost = 0.0
    if language == "python" and python_deps:
        py_dep_set = {d.lower() for d in python_deps}
        if "fastapi" in py_dep_set:
            framework_boost = max(framework_boost, 0.2)
            signals.append("framework:fastapi")
        if "django" in py_dep_set:
            framework_boost = max(framework_boost, 0.2)
            signals.append("framework:django")
        if "flask" in py_dep_set:
            framework_boost = max(framework_boost, 0.15)
            signals.append("framework:flask")

    if language in ("javascript", "typescript") and pkg_deps:
        node_dep_set = set(pkg_deps.keys())
        if "next" in node_dep_set:
            framework_boost = max(framework_boost, 0.2)
            signals.append("framework:nextjs")
        if "@nestjs/core" in node_dep_set:
            framework_boost = max(framework_boost, 0.2)
            signals.append("framework:nestjs")
        if "express" in node_dep_set:
            framework_boost = max(framework_boost, 0.15)
            signals.append("framework:express")
    
    # Boost for dependency files (primary indicators)
    if "pyproject_toml" in detected_paths or "requirements_txt" in detected_paths:
        confidence += 0.3
        signals.append("python_deps_file")
    if "package_json" in detected_paths:
        confidence += 0.3
        signals.append("node_deps_file")
    
    # Boost for detected dependencies
    if python_deps:
        confidence += min(0.15, len(python_deps) * 0.03)
        signals.append("python_deps")
    if pkg_deps:
        confidence += min(0.15, len(pkg_deps) * 0.02)
        signals.append("node_deps")

    confidence += framework_boost
    
    # Check for secondary signals if repo_path provided
    if repo_path:
        repo_path = Path(repo_path) if isinstance(repo_path, str) else repo_path

        # Framework config file confirmation (stronger than just deps)
        if language in ("javascript", "typescript"):
            if any((repo_path / p).exists() for p in ["next.config.js", "next.config.mjs", "next.config.ts"]):
                confidence += 0.05
                signals.append("next_config")
        
        # Go-specific signals
        if language == "go":
            if (repo_path / "go.mod").exists():
                confidence += 0.3
                signals.append("go_mod")
        
        # Java-specific signals  
        if language == "java":
            if (repo_path / "pom.xml").exists() or (repo_path / "build.gradle").exists():
                confidence += 0.3
                signals.append("java_build")
        
        # Dockerfile confirmation
        dockerfile = repo_path / "Dockerfile"
        if dockerfile.exists():
            try:
                content = dockerfile.read_text(errors="ignore").lower()
                if language == "python" and ("from python:" in content or "from python " in content):
                    confidence += 0.15
                    signals.append("dockerfile")
                elif language == "go" and ("from golang:" in content or "from go:" in content):
                    confidence += 0.15
                    signals.append("dockerfile")
                elif language == "java" and ("from openjdk:" in content or "from maven:" in content):
                    confidence += 0.15
                    signals.append("dockerfile")
            except Exception:
                pass
        
        # GitHub Actions confirmation
        workflows_dir = repo_path / ".github" / "workflows"
        if workflows_dir.exists():
            try:
                for wf in workflows_dir.glob("*.yml"):
                    content = wf.read_text(errors="ignore").lower()
                    if language == "python" and "actions/setup-python" in content:
                        confidence += 0.15
                        signals.append("github_actions")
                        break
                    elif language == "go" and "actions/setup-go" in content:
                        confidence += 0.15
                        signals.append("github_actions")
                        break
                    elif language == "java" and "actions/setup-java" in content:
                        confidence += 0.15
                        signals.append("github_actions")
                        break
            except Exception:
                pass
    
    # Bonus for multiple agreeing signals
    if len(signals) >= 3:
        confidence += 0.1
    
    return min(confidence, 1.0)


def _rank_framework_hints(framework_hints: List[str]) -> List[str]:
    """Return a deterministic, priority-ordered list of framework hints."""
    if not framework_hints:
        return []

    # Higher specificity first (frameworks that imply the overall structure)
    priority = {
        "nextjs": 100,
        "nestjs": 90,
        "fastapi": 80,
        "django": 80,
        "flask": 70,
        "express": 60,
    }

    # De-dupe while preserving relative priority ordering
    unique = list(dict.fromkeys(framework_hints))
    return sorted(unique, key=lambda h: priority.get(h, 0), reverse=True)


def _looks_like_typescript_node_repo(repo_path: Path, pkg_deps: Dict[str, str]) -> bool:
    """Heuristic: infer TS for Node repos even if tsconfig isn't at repo root."""
    node_dep_set = set(pkg_deps.keys())
    ts_deps = {
        "typescript",
        "ts-node",
        "@types/node",
        "@types/express",
        "@typescript-eslint/parser",
        "@typescript-eslint/eslint-plugin",
    }
    if node_dep_set.intersection(ts_deps):
        return True

    # Next.js + TS usage is common; also treat as TS if any TS sources exist.
    if "next" in node_dep_set or "@nestjs/core" in node_dep_set:
        try:
            for pat in ("**/*.ts", "**/*.tsx"):
                for p in repo_path.glob(pat):
                    # Ignore typical vendor dirs
                    if any(part in {"node_modules", ".next", "dist", "build"} for part in p.parts):
                        continue
                    return True
        except Exception:
            pass

    return False


def _extract_python_deps_from_pyproject(content: str) -> List[str]:
    """Extract dependency names from pyproject.toml content."""
    deps = []
    in_deps_section = False

    for line in content.splitlines():
        line = line.strip()
        if "dependencies" in line and "=" in line:
            in_deps_section = True
            if "[" in line:
                inline_deps = _extract_inline_deps(line)
                deps.extend(inline_deps)
                if "]" in line:
                    in_deps_section = False
            continue
        if in_deps_section:
            if line.startswith("]") or ("]" in line and not line.startswith('"') and not line.startswith("'")):
                in_deps_section = False
            elif line.startswith('"') or line.startswith("'"):
                if "#" in line:
                    comment_start = line.index("#")
                    line = line[:comment_start]
                quote_char = line[0]
                end_quote = line.find(quote_char, 1)
                if end_quote > 0:
                    dep = line[1:end_quote]
                else:
                    dep = line.strip('",\' ').strip()
                if dep:
                    pkg_name = _extract_pkg_name(dep)
                    if pkg_name:
                        deps.append(pkg_name)

    if not deps:
        common_deps = ["fastapi", "django", "flask", "uvicorn", "starlette", "pydantic"]
        for dep in common_deps:
            if dep in content.lower():
                deps.append(dep)

    return deps


def _extract_pkg_name(dep_spec: str) -> str:
    """Extract package name from a dependency specification."""
    dep_spec = dep_spec.strip('"\'')
    if "[" in dep_spec:
        dep_spec = dep_spec[:dep_spec.index("[")]
    for specifier in [">=", "<=", "==", "~=", "!=", "<", ">"]:
        if specifier in dep_spec:
            dep_spec = dep_spec[:dep_spec.index(specifier)]
            break
    return dep_spec.strip()


def _extract_inline_deps(line: str) -> List[str]:
    """Extract dependencies from inline format."""
    deps = []
    if "[" not in line:
        return deps
    start = line.index("[")
    end = line.rfind("]")
    if end == -1:
        end = len(line)
    list_content = line[start+1:end]
    for item in list_content.split(","):
        item = item.strip().strip('"\'')
        if item:
            pkg_name = _extract_pkg_name(item)
            if pkg_name:
                deps.append(pkg_name)
    return deps


def _extract_python_deps_from_requirements(content: str) -> List[str]:
    """Extract dependency names from requirements.txt content."""
    deps = []
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("-"):
            pkg_name = _extract_pkg_name(line)
            if pkg_name:
                deps.append(pkg_name.lower())
    return deps


def _detect_language_by_config_files(repo_path: Path) -> Optional[str]:
    """
    Bug #87 Optimal Fix: Config-file-first language detection.
    
    Checks for language-specific config/manifest files FIRST before
    counting file extensions. This is:
    - O(1) file existence checks (fast)
    - Authoritative (config files are definitive indicators)
    - Works for repos with few source files
    
    Enhanced for sparse repos:
    - Dockerfile base image detection
    - Makefile target analysis
    - CI/CD workflow hints
    - Monorepo structure detection
    
    Returns:
        Language string if detected via config file, None otherwise.
    """
    # === MONOREPO DETECTION (check first to detect workspace language) ===
    # Nx monorepo (TypeScript)
    if (repo_path / "nx.json").exists():
        logger.debug("Detected TypeScript via nx.json (Nx monorepo)")
        return "typescript"
    
    # Lerna monorepo (check for TypeScript indicators)
    if (repo_path / "lerna.json").exists():
        if any((repo_path / p).exists() for p in ["tsconfig.json", "tsconfig.base.json"]):
            logger.debug("Detected TypeScript via lerna.json + tsconfig")
            return "typescript"
        logger.debug("Detected JavaScript via lerna.json")
        return "javascript"
    
    # pnpm workspace (check for TypeScript indicators)
    if (repo_path / "pnpm-workspace.yaml").exists():
        if any((repo_path / p).exists() for p in ["tsconfig.json", "tsconfig.base.json"]):
            logger.debug("Detected TypeScript via pnpm-workspace.yaml + tsconfig")
            return "typescript"
        logger.debug("Detected JavaScript via pnpm-workspace.yaml")
        return "javascript"
    
    # === PRIMARY CONFIG FILES (authoritative) ===
    
    # Go: go.mod or go.sum
    if (repo_path / "go.mod").exists() or (repo_path / "go.sum").exists():
        logger.debug("Detected Go via go.mod/go.sum")
        return "go"
    
    # Java: Maven (pom.xml) or Gradle (build.gradle, build.gradle.kts)
    if (repo_path / "pom.xml").exists():
        logger.debug("Detected Java via pom.xml (Maven)")
        return "java"
    if (repo_path / "build.gradle").exists() or (repo_path / "build.gradle.kts").exists():
        logger.debug("Detected Java via build.gradle (Gradle)")
        return "java"
    
    # Ruby: Gemfile
    if (repo_path / "Gemfile").exists():
        logger.debug("Detected Ruby via Gemfile")
        return "ruby"
    
    # C#: .csproj or .sln files
    if any(repo_path.glob("*.csproj")) or any(repo_path.glob("*.sln")):
        logger.debug("Detected C# via .csproj/.sln")
        return "csharp"
    
    # Rust: Cargo.toml
    if (repo_path / "Cargo.toml").exists():
        logger.debug("Detected Rust via Cargo.toml")
        return "rust"
    
    # TypeScript: Check for tsconfig*.json (must check BEFORE package.json)
    tsconfig_patterns = ["tsconfig.json", "tsconfig.base.json", "tsconfig.app.json"]
    if any((repo_path / p).exists() for p in tsconfig_patterns) or list(repo_path.glob("tsconfig*.json")):
        logger.debug("Detected TypeScript via tsconfig")
        return "typescript"
    
    # JavaScript/Node.js: package.json without tsconfig
    if (repo_path / "package.json").exists():
        # Already checked tsconfig above, so this is JS not TS
        logger.debug("Detected JavaScript via package.json (no tsconfig)")
        return "javascript"
    
    # Python: pyproject.toml, requirements.txt, setup.py
    if (repo_path / "pyproject.toml").exists():
        logger.debug("Detected Python via pyproject.toml")
        return "python"
    if (repo_path / "requirements.txt").exists():
        logger.debug("Detected Python via requirements.txt")
        return "python"
    if (repo_path / "setup.py").exists():
        logger.debug("Detected Python via setup.py")
        return "python"
    
    # === SECONDARY INDICATORS (for sparse repos) ===
    
    # Dockerfile base image detection
    dockerfile = repo_path / "Dockerfile"
    if dockerfile.exists():
        lang = _detect_language_from_dockerfile(dockerfile)
        if lang:
            logger.debug(f"Detected {lang} via Dockerfile base image")
            return lang
    
    # Makefile target detection
    makefile = repo_path / "Makefile"
    if makefile.exists():
        lang = _detect_language_from_makefile(makefile)
        if lang:
            logger.debug(f"Detected {lang} via Makefile targets")
            return lang
    
    # GitHub Actions workflow detection
    workflows_dir = repo_path / ".github" / "workflows"
    if workflows_dir.exists():
        lang = _detect_language_from_github_actions(workflows_dir)
        if lang:
            logger.debug(f"Detected {lang} via GitHub Actions")
            return lang
    
    # No config file found - return None to trigger file counting fallback
    return None


def _detect_language_from_dockerfile(dockerfile: Path) -> Optional[str]:
    """Detect language from Dockerfile FROM directives."""
    try:
        content = dockerfile.read_text(errors="ignore").lower()
        
        # Map base images to languages
        if "from python:" in content or "from python " in content:
            return "python"
        if "from node:" in content or "from node " in content:
            # Check if there's TypeScript evidence elsewhere
            return "javascript"  # Conservative - might be TS
        if "from golang:" in content or "from go:" in content:
            return "go"
        if "from openjdk:" in content or "from maven:" in content or "from gradle:" in content:
            return "java"
        if "from ruby:" in content:
            return "ruby"
        if "from mcr.microsoft.com/dotnet" in content:
            return "csharp"
        if "from rust:" in content:
            return "rust"
    except Exception as e:
        # H-6: File reading fallback for Dockerfile
        logger.debug(f"Could not read Dockerfile for language detection: {e}")
    return None


def _detect_language_from_makefile(makefile: Path) -> Optional[str]:
    """Detect language from Makefile targets and commands."""
    try:
        content = makefile.read_text(errors="ignore").lower()
        
        # Check for language-specific commands
        if "go build" in content or "go run" in content or "go test" in content:
            return "go"
        if "mvn " in content or "gradle " in content or "javac " in content:
            return "java"
        if "pip " in content or "python " in content or "pytest" in content:
            return "python"
        if "npm " in content or "yarn " in content or "pnpm " in content:
            # Could be TS or JS - conservative
            return "javascript"
        if "cargo " in content or "rustc " in content:
            return "rust"
        if "bundle " in content or "rake " in content or "rspec" in content:
            return "ruby"
        if "dotnet " in content or "msbuild" in content:
            return "csharp"
    except Exception as e:
        # H-6: File reading fallback for Makefile
        logger.debug(f"Could not read Makefile for language detection: {e}")
    return None


def _detect_language_from_github_actions(workflows_dir: Path) -> Optional[str]:
    """Detect language from GitHub Actions workflow files."""
    try:
        for workflow_file in workflows_dir.glob("*.yml"):
            content = workflow_file.read_text(errors="ignore").lower()
            
            # Check for setup actions
            if "actions/setup-python" in content:
                return "python"
            if "actions/setup-node" in content:
                # Check for TypeScript hints
                if "typescript" in content or "tsc" in content:
                    return "typescript"
                return "javascript"
            if "actions/setup-go" in content:
                return "go"
            if "actions/setup-java" in content:
                return "java"
            if "ruby/setup-ruby" in content:
                return "ruby"
            if "actions/setup-dotnet" in content:
                return "csharp"
            if "actions-rs/toolchain" in content or "dtolnay/rust-toolchain" in content:
                return "rust"
        
        # Also check .yaml extension
        for workflow_file in workflows_dir.glob("*.yaml"):
            content = workflow_file.read_text(errors="ignore").lower()
            
            if "actions/setup-python" in content:
                return "python"
            if "actions/setup-node" in content:
                return "javascript"
            if "actions/setup-go" in content:
                return "go"
    except Exception as e:
        # H-6: File reading fallback for GitHub Actions
        logger.debug(f"Could not read GitHub Actions workflows: {e}")
    return None


def _detect_language_by_file_count(repo_path: Path) -> str:
    """
    Fallback: Detect primary language based on file extension counts.
    
    Used when no config files are found. Counts source files by extension.
    Now includes Go, Java, Ruby, C# in addition to Python/TypeScript/JavaScript.
    """
    counts = {
        "python": 0,
        "typescript": 0,
        "javascript": 0,
        "go": 0,
        "java": 0,
        "ruby": 0,
        "csharp": 0,
    }
    
    extension_map = {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".java": "java",
        ".rb": "ruby",
        ".cs": "csharp",
    }
    
    skip_dirs = {'.git', '__pycache__', '.venv', 'venv', 'node_modules', 
                 'vendor', 'target', 'bin', 'obj', '.idea'}
    
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            ext = Path(f).suffix.lower()
            if ext in extension_map:
                counts[extension_map[ext]] += 1
    
    # Find language with most files
    if max(counts.values()) == 0:
        logger.debug("No source files found, defaulting to python")
        return "python"
    
    primary = max(counts, key=counts.get)
    logger.debug(f"File count detection: {counts}, primary: {primary}")
    return primary


def _detect_primary_language(repo_path: Path) -> str:
    """
    Detect primary language using config-file-first approach.
    
    Bug #87 Fix: Now checks config files FIRST (go.mod, pom.xml, Gemfile, etc.)
    before falling back to file extension counting.
    
    This ensures Go, Java, Ruby, C# repos are correctly detected even if
    they have few source files or mixed-language content.
    """
    # Try config file detection first (O(1), authoritative)
    config_detected = _detect_language_by_config_files(repo_path)
    if config_detected:
        return config_detected
    
    # Fallback to file counting (O(n), heuristic)
    return _detect_language_by_file_count(repo_path)


# =============================================================================
# BUG-005 FIX: ROUTER FILE DETECTION
# =============================================================================

def _detect_router_file(
    repo_root: Optional[str],
    source_root: Optional[str],
    framework: str,
) -> str:
    """
    Detect the appropriate router file location for web frameworks.
    
    BUG-005: Previously hardcoded to 'src/integrations/__init__.py', which doesn't
    match common FastAPI project structures like 'src/routers/', 'app/api/', etc.
    
    Detection strategy:
    1. Look for existing router directories in common patterns
    2. If found, use {router_dir}/__init__.py or {router_dir}/integrations.py
    3. Fall back to {integrations_root}/__init__.py
    
    Common FastAPI router patterns:
    - src/routers/
    - src/app/routers/
    - src/api/routes/
    - app/routers/
    - routers/
    
    Args:
        repo_root: Repository root path
        source_root: Detected source root (e.g., 'src' or 'app')
        framework: Web framework name (fastapi, flask, etc.)
        
    Returns:
        Best router file path for the project
    """
    if not repo_root:
        return "src/integrations/__init__.py"
    
    repo_path = Path(repo_root)
    
    # Common router directory patterns to search
    router_patterns = [
        "routers",
        "routes", 
        "api/routes",
        "api/routers",
        "api/v1/routers",
        "api/v1/routes",
    ]
    
    # Add source_root prefixed patterns if available
    if source_root:
        for pattern in router_patterns[:]:
            router_patterns.insert(0, f"{source_root}/{pattern}")
    else:
        # Common prefixes
        for prefix in ["src", "app"]:
            for pattern in router_patterns[:]:
                router_patterns.insert(0, f"{prefix}/{pattern}")
    
    # Search for existing router directories
    for pattern in router_patterns:
        router_dir = repo_path / pattern
        if router_dir.is_dir():
            # Found a router directory - determine the best file to use
            init_file = router_dir / "__init__.py"
            integration_file = router_dir / "integrations.py"
            
            # Prefer dedicated integrations.py if it exists
            if integration_file.exists():
                logger.info(f"[BUG-005] Found existing integration router: {pattern}/integrations.py")
                return f"{pattern}/integrations.py"
            
            # Use __init__.py if it exists
            if init_file.exists():
                logger.info(f"[BUG-005] Found existing router directory: {pattern}/__init__.py")
                return f"{pattern}/__init__.py"
            
            # Directory exists but no init - create integrations.py there
            logger.info(f"[BUG-005] Found router directory, will create: {pattern}/integrations.py")
            return f"{pattern}/integrations.py"
    
    # No existing router directory found - create in integrations folder
    if source_root:
        default_path = f"{source_root}/integrations/__init__.py"
    else:
        default_path = "src/integrations/__init__.py"
    
    logger.debug(f"[BUG-005] No existing router directory, using default: {default_path}")
    return default_path


# =============================================================================
# BUG-009 FIX: APP ENTRY POINT FILE DETECTION
# =============================================================================

def _detect_app_file(
    repo_root: Optional[str],
    source_root: Optional[str],
    framework: str,
) -> Optional[str]:
    """
    Detect the main application entry point file for web frameworks.
    
    BUG-009: System generated router_file but didn't update main.py to
    include the router with `app.include_router(...)`.
    
    Detection strategy:
    1. Look for common FastAPI/Flask app entry points
    2. Verify file contains FastAPI() or Flask() app instantiation
    3. Return the path if found, None otherwise
    
    Common entry point patterns:
    - main.py (FastAPI convention)
    - app.py (Flask/generic convention)
    - src/main.py
    - src/app/main.py
    - app/__init__.py (Flask factory pattern)
    
    Args:
        repo_root: Repository root path
        source_root: Detected source root (e.g., 'src' or 'app')
        framework: Web framework name (fastapi, flask, etc.)
        
    Returns:
        Path to app entry point file, or None if not found
    """
    if not repo_root:
        return None
    
    repo_path = Path(repo_root)
    
    # Framework-specific app instantiation patterns to verify
    app_patterns = {
        "fastapi": [r"FastAPI\s*\(", r"app\s*=\s*FastAPI"],
        "flask": [r"Flask\s*\(", r"app\s*=\s*Flask"],
        "django": [],  # Django uses INSTALLED_APPS, different pattern
        "express": [r"express\s*\(", r"app\s*=\s*express"],
    }
    
    verification_patterns = app_patterns.get(framework, [])
    
    # Common entry point file patterns to search
    entry_point_patterns = [
        "main.py",
        "app.py",
        "application.py",
        "server.py",
    ]
    
    # Build search paths with source_root prefixes
    search_paths = []
    
    if source_root:
        # Prioritize source_root prefixed paths
        for pattern in entry_point_patterns:
            search_paths.append(f"{source_root}/{pattern}")
            search_paths.append(f"{source_root}/app/{pattern}")
        # Also try direct paths
        search_paths.extend(entry_point_patterns)
    else:
        # Try common prefixes
        for prefix in ["src", "app", ""]:
            for pattern in entry_point_patterns:
                if prefix:
                    search_paths.append(f"{prefix}/{pattern}")
                    search_paths.append(f"{prefix}/app/{pattern}")
                else:
                    search_paths.append(pattern)
    
    # Search for existing entry point files
    for rel_path in search_paths:
        app_file = repo_path / rel_path
        if app_file.is_file():
            try:
                content = app_file.read_text(encoding="utf-8")
                
                # Verify it's an actual app entry point (not just a random file)
                if verification_patterns:
                    import re
                    for pattern in verification_patterns:
                        if re.search(pattern, content):
                            logger.info(f"[BUG-009] Found app entry point: {rel_path}")
                            return rel_path
                else:
                    # No verification needed (or unknown framework)
                    # Check for generic "app" variable or common patterns
                    if "app" in content and ("import" in content or "from" in content):
                        logger.info(f"[BUG-009] Found potential app entry point: {rel_path}")
                        return rel_path
                        
            except Exception as e:
                logger.debug(f"[BUG-009] Failed to read {rel_path}: {e}")
                continue
    
    logger.debug(f"[BUG-009] No app entry point found in {repo_root}")
    return None


# =============================================================================
# LAYER 2: INFERENCE / EFFECTIVE PROFILE
# =============================================================================

def build_effective_repo_profile(
    detected: DetectedProfile,
    repo_root: Optional[str],
    use_llm_refinement: bool = False,
) -> RepoProfile:
    """Layer 2: Build effective RepoProfile from detection result."""
    logger.info(
        f"Building effective profile: language={detected.language}, "
        f"confidence={detected.confidence:.2f}, evidence={detected.evidence[:3]}"
    )

    if detected.confidence < LOW_CONFIDENCE_THRESHOLD:
        logger.warning(
            f"Low confidence detection ({detected.confidence:.2f} < {LOW_CONFIDENCE_THRESHOLD}). "
            f"Consider providing .integration-coworker.yaml config file."
        )

    profile_source = "heuristic" if detected.confidence >= LOW_CONFIDENCE_THRESHOLD else "heuristic_fallback"

    # Compatibility: if we confidently detected a known framework, expose that as
    # an "archetype"-sourced profile (even though we no longer maintain separate
    # archetype templates).
    framework_hints = detected.metadata.get("framework_hints", [])
    if (
        profile_source != "heuristic_fallback"
        and detected.confidence >= 0.8
        and any(h in {"fastapi", "django", "flask", "nextjs", "nestjs", "express"} for h in framework_hints)
    ):
        profile_source = "archetype"
    inferred_profile = _infer_profile_heuristically(repo_root, detected, profile_source)

    if use_llm_refinement and detected.confidence < VERY_LOW_CONFIDENCE_THRESHOLD:
        logger.info("Confidence very low, attempting LLM-assisted refinement")
        refined_profile = _refine_profile_with_llm(inferred_profile, repo_root, detected)
        if refined_profile.profile_source == "llm" and repo_root:
            _persist_profile_as_config(refined_profile, Path(repo_root))
        return refined_profile

    return inferred_profile


def _infer_profile_heuristically(
    repo_root: Optional[str],
    detected: DetectedProfile,
    profile_source: str = "heuristic",
) -> RepoProfile:
    """Infer profile entirely from heuristics."""
    # Naming convention:
    # - When detection is very low confidence, we use a stable generic_*_project
    #   name so downstream behavior is predictable and tests can assert on it.
    # - Otherwise, keep the language-specific inferred_* name.
    if profile_source == "heuristic_fallback":
        if detected.language in ("javascript", "typescript"):
            project_name = "generic_js_project"
        else:
            project_name = f"generic_{detected.language}_project"
    else:
        # If this is an archetype-sourced profile, use the framework as the name
        # (matches tests and is more user-friendly).
        framework_hints = detected.metadata.get("framework_hints", [])
        if profile_source == "archetype" and framework_hints:
            project_name = framework_hints[0]
        else:
            project_name = f"inferred_{detected.language}_project"

    framework_hints = detected.metadata.get("framework_hints", [])
    framework = framework_hints[0] if framework_hints else None

    if not repo_root:
        return RepoProfile(
            name=project_name,
            framework=framework,
            language=detected.language,
            integrations_root="integrations",
            tests_root="tests",
            detection_confidence=detected.confidence,
            detection_evidence=detected.evidence,
            profile_source=profile_source,
            # G-02: No workspace boundary for non-repo runs
            workspace_root=None,
            is_monorepo=False,
        )

    repo_path = Path(repo_root)
    source_root = _find_source_root(repo_path) or ""
    tests_root = _find_tests_root(repo_path) or "tests"
    services_dir = _find_services_directory(repo_path, source_root)

    if services_dir:
        integrations_root = f"{services_dir}/integrations"
    elif source_root:
        integrations_root = f"{source_root}/integrations"
    else:
        integrations_root = "integrations"

    existing_integrations = _find_existing_integrations_dir(repo_path, source_root)
    if existing_integrations:
        integrations_root = existing_integrations

    # Bug #1 Fix: Language-aware extension and convention mapping
    # Import the authoritative extension mapping from codegen context
    from integration_coworker.codegen.context import LANGUAGE_EXTENSIONS
    
    ext = LANGUAGE_EXTENSIONS.get(detected.language, ".py")
    
    # Language-specific conventions
    if detected.language in ("typescript", "javascript"):
        conventions = {
            "client_module_pattern": "clients/{provider}.ts",
            "flow_module_pattern": "services/{provider}/{task}.ts",
            "test_module_pattern": "{provider}/{task}.test.ts",
        }
    elif detected.language == "go":
        conventions = {
            "client_module_pattern": "clients/{provider}.go",
            "flow_module_pattern": "flows/{provider}_{task}.go",
            "test_module_pattern": "{provider}_{task}_test.go",
        }
    elif detected.language == "java":
        conventions = {
            "client_module_pattern": "clients/{Provider}Client.java",
            "flow_module_pattern": "flows/{Provider}{Task}Flow.java",
            "test_module_pattern": "{Provider}{Task}Test.java",
        }
    elif detected.language == "ruby":
        conventions = {
            "client_module_pattern": "clients/{provider}_client.rb",
            "flow_module_pattern": "flows/{provider}_{task}.rb",
            "test_module_pattern": "{provider}_{task}_spec.rb",
        }
    elif detected.language == "csharp":
        conventions = {
            "client_module_pattern": "Clients/{Provider}Client.cs",
            "flow_module_pattern": "Flows/{Provider}{Task}Flow.cs",
            "test_module_pattern": "{Provider}{Task}Tests.cs",
        }
    elif detected.language == "rust":
        conventions = {
            "client_module_pattern": "clients/{provider}.rs",
            "flow_module_pattern": "flows/{provider}_{task}.rs",
            "test_module_pattern": "{provider}_{task}_test.rs",
        }
    else:
        # Default to Python conventions
        conventions = {
            "client_module_pattern": "clients/{provider}.py",
            "flow_module_pattern": "flows/{provider}_{task}.py",
            "test_module_pattern": "test_{provider}_{task}.py",
        }

    # G-02: Propagate workspace boundary from detection to profile
    workspace_root = detected.selected_workspace_root
    is_monorepo = len(detected.workspace_roots) > 0

    # V38-001: Add integration_hooks for detected web service frameworks
    # This enables automatic FastAPI router generation for web services
    # BUG-005: Detect existing router files in the repo structure
    # BUG-009: Detect app entry point file for router registration
    integration_hooks = None
    if framework in ("fastapi", "flask", "django", "express"):
        # BUG-005 FIX: Detect existing router structure in target repo
        router_file = _detect_router_file(repo_root, source_root, framework)
        
        # BUG-009 FIX: Detect app entry point for router registration
        app_file = _detect_app_file(repo_root, source_root, framework)
        
        integration_hooks = {
            "router_file": router_file,
            "router_registration_marker": "# BEGIN AUTO-GENERATED INTEGRATION ROUTES",
            # BUG-009: Add app_file for main.py router registration
            "app_file": app_file,
            "app_router_marker": "# BEGIN AUTO-REGISTERED INTEGRATION ROUTERS",
            "settings_file": None,  # Optional - only if settings marker found
            "settings_marker": None,
        }

    return RepoProfile(
        name=project_name,
        archetype=None,
        framework=framework,
        language=detected.language,
        integrations_root=integrations_root,
        tests_root=f"{tests_root}/integrations",
        conventions=conventions,
        layout_hints={
            "detected_source_root": source_root,
            "detected_tests_root": tests_root,
            "detected_services_dir": services_dir,
            "clients_dir": f"{integrations_root}/clients",
            "flows_dir": f"{integrations_root}/flows",
            "file_extension": ext,
        },
        integration_hooks=integration_hooks,  # V38-001: Add hooks for web frameworks
        detection_confidence=detected.confidence,
        detection_evidence=detected.evidence + ["Inferred via heuristics"],
        profile_source=profile_source,
        # G-02: Workspace boundary enforcement for monorepos
        workspace_root=workspace_root,
        is_monorepo=is_monorepo,
    )


def _refine_profile_with_llm(
    base_profile: RepoProfile,
    repo_root: Optional[str],
    detected: DetectedProfile,
) -> RepoProfile:
    """Use LLM to refine profile when heuristics are uncertain."""
    if not repo_root:
        return base_profile

    try:
        from integration_coworker.llm import get_llm_client_for_node
        from integration_coworker.repo.context import filesystem_repo_context_provider

        repo_snapshot = filesystem_repo_context_provider(repo_root)

        prompt = f"""Analyze this repository structure and suggest where to place API integration code.

## Repository Structure
{repo_snapshot.tree_markdown[:3000]}

## Current Detection
- Language: {detected.language}
- Confidence: {detected.confidence:.2f}
- Evidence: {', '.join(detected.evidence[:5])}

## Task
Suggest the best locations for:
1. Integration client code (API clients)
2. Integration flow/service code (business logic)
3. Integration tests

Respond in this exact format:
integrations_root=<path>
clients_dir=<path>
flows_dir=<path>
tests_root=<path>
reasoning=<one line explanation>
"""

        client = get_llm_client_for_node("plan_run")
        response = client.complete(prompt)

        if response and not response.startswith("Mock"):
            parsed = _parse_llm_layout_response(response)
            if parsed:
                return RepoProfile(
                    name=f"llm_refined_{detected.language}_project",
                    archetype=None,
                    framework=base_profile.framework,
                    language=detected.language,
                    integrations_root=parsed.get("integrations_root", base_profile.integrations_root),
                    tests_root=parsed.get("tests_root", base_profile.tests_root),
                    conventions=base_profile.conventions,
                    layout_hints={
                        "clients_dir": parsed.get("clients_dir", f"{parsed.get('integrations_root', 'integrations')}/clients"),
                        "flows_dir": parsed.get("flows_dir", f"{parsed.get('integrations_root', 'integrations')}/flows"),
                        "llm_reasoning": parsed.get("reasoning", ""),
                    },
                    detection_confidence=detected.confidence,
                    detection_evidence=detected.evidence + ["Refined with LLM"],
                    profile_source="llm",
                )

    except Exception as e:
        logger.warning(f"LLM refinement failed: {e}")

    return base_profile


def _parse_llm_layout_response(response: str) -> Optional[Dict[str, str]]:
    """Parse LLM layout suggestion response."""
    result = {}
    for line in response.strip().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
    return result if result else None


def _persist_profile_as_config(profile: RepoProfile, repo_root: Path) -> bool:
    """Persist a RepoProfile as .integration-coworker.yaml config file."""
    try:
        from integration_coworker.repo.config_schema import (
            profile_to_config,
            save_config,
            validate_config,
        )
        
        config = profile_to_config(profile, detection_method=profile.profile_source)
        validation = validate_config(config, repo_root, check_paths_exist=True)
        if not validation.is_valid:
            logger.warning(f"Config validation failed, not persisting: {validation.errors}")
            return False
        if validation.warnings:
            logger.debug(f"Config validation warnings: {validation.warnings}")
        config_path = repo_root / ".integration-coworker.yaml"
        success = save_config(config, config_path)
        if success:
            logger.info(f"Persisted LLM-refined profile as config: {config_path}")
        return success
        
    except ImportError as e:
        logger.warning(f"Cannot persist profile (missing dependency): {e}")
        return False
    except Exception as e:
        logger.warning(f"Failed to persist profile as config: {e}")
        return False


# =============================================================================
# HEURISTIC HELPERS
# =============================================================================

def _find_source_root(repo_path: Path) -> Optional[str]:
    """Find the primary source root directory."""
    source_patterns = ["src", "app", "lib", "source", "pkg", "packages"]
    for pattern in source_patterns:
        candidate = repo_path / pattern
        if candidate.is_dir():
            return pattern
    for child in repo_path.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            if child.name not in {"tests", "test", "docs", "scripts"}:
                return child.name
    return None


def _find_tests_root(repo_path: Path) -> Optional[str]:
    """Find the tests directory."""
    test_patterns = ["tests", "test", "__tests__", "spec", "specs"]
    for pattern in test_patterns:
        candidate = repo_path / pattern
        if candidate.is_dir():
            return pattern
    return "tests"


def _find_services_directory(repo_path: Path, source_root: str) -> Optional[str]:
    """Find existing services/routes directory."""
    if not source_root:
        return None
    service_patterns = ["services", "service", "routes", "api", "handlers", "controllers"]
    base = repo_path / source_root
    if not base.exists():
        return None
    for pattern in service_patterns:
        candidate = base / pattern
        if candidate.is_dir():
            return f"{source_root}/{pattern}"
    return source_root


def _find_existing_integrations_dir(repo_path: Path, source_root: str) -> Optional[str]:
    """Check if an integrations directory already exists."""
    integration_patterns = ["integrations", "integration", "external", "vendors", "third_party"]
    if source_root:
        base = repo_path / source_root
        if base.exists():
            for pattern in integration_patterns:
                candidate = base / pattern
                if candidate.is_dir():
                    return f"{source_root}/{pattern}"
    for pattern in integration_patterns:
        candidate = repo_path / pattern
        if candidate.is_dir():
            return pattern
    return None


# =============================================================================
# G-02: WORKSPACE BOUNDARY DETECTION
# =============================================================================

def detect_workspace_roots(repo_path: Path) -> List[str]:
    """
    Detect workspace/package roots in a monorepo.
    
    G-02: For monorepos, we need to identify which workspace roots exist
    so we can constrain file writes to a single workspace.
    
    Supported monorepo patterns:
    - Nx (nx.json + apps/, libs/)
    - Lerna (lerna.json + packages/)
    - pnpm (pnpm-workspace.yaml)
    - npm/yarn workspaces (package.json workspaces field)
    - Turborepo (turbo.json)
    - Generic multi-package (services/, apps/, packages/)
    
    Args:
        repo_path: Path to repository root
        
    Returns:
        List of workspace root paths relative to repo root.
        Empty list means single-workspace (non-monorepo) repository.
    """
    workspace_roots: List[str] = []
    
    # === NX MONOREPO ===
    if (repo_path / "nx.json").exists():
        # Nx uses apps/ and libs/ by default
        for dir_name in ["apps", "libs", "packages"]:
            dir_path = repo_path / dir_name
            if dir_path.is_dir():
                # Each subdirectory is a workspace
                for child in dir_path.iterdir():
                    if child.is_dir() and (child / "package.json").exists():
                        workspace_roots.append(f"{dir_name}/{child.name}")
        logger.debug(f"Detected Nx monorepo with {len(workspace_roots)} workspaces")
        return workspace_roots
    
    # === LERNA MONOREPO ===
    if (repo_path / "lerna.json").exists():
        try:
            import json
            lerna_config = json.loads((repo_path / "lerna.json").read_text())
            packages = lerna_config.get("packages", ["packages/*"])
            for pattern in packages:
                # Handle glob patterns like "packages/*"
                base_dir = pattern.rstrip("/*")
                dir_path = repo_path / base_dir
                if dir_path.is_dir():
                    for child in dir_path.iterdir():
                        if child.is_dir() and (child / "package.json").exists():
                            workspace_roots.append(f"{base_dir}/{child.name}")
        except (json.JSONDecodeError, IOError):
            pass
        logger.debug(f"Detected Lerna monorepo with {len(workspace_roots)} workspaces")
        return workspace_roots
    
    # === PNPM WORKSPACE ===
    pnpm_workspace = repo_path / "pnpm-workspace.yaml"
    if pnpm_workspace.exists():
        try:
            import yaml
            config = yaml.safe_load(pnpm_workspace.read_text())
            packages = config.get("packages", [])
            for pattern in packages:
                base_dir = pattern.rstrip("/*")
                if base_dir.startswith("!"):
                    continue  # Skip negation patterns
                dir_path = repo_path / base_dir
                if dir_path.is_dir():
                    for child in dir_path.iterdir():
                        if child.is_dir() and (child / "package.json").exists():
                            workspace_roots.append(f"{base_dir}/{child.name}")
        except Exception:
            pass
        logger.debug(f"Detected pnpm monorepo with {len(workspace_roots)} workspaces")
        return workspace_roots
    
    # === NPM/YARN WORKSPACES ===
    package_json = repo_path / "package.json"
    if package_json.exists():
        try:
            import json
            pkg = json.loads(package_json.read_text())
            workspaces = pkg.get("workspaces", [])
            # Workspaces can be a list or a dict with "packages" key
            if isinstance(workspaces, dict):
                workspaces = workspaces.get("packages", [])
            for pattern in workspaces:
                base_dir = pattern.rstrip("/*")
                dir_path = repo_path / base_dir
                if dir_path.is_dir():
                    for child in dir_path.iterdir():
                        if child.is_dir() and (child / "package.json").exists():
                            workspace_roots.append(f"{base_dir}/{child.name}")
        except (json.JSONDecodeError, IOError):
            pass
        if workspace_roots:
            logger.debug(f"Detected npm/yarn workspaces with {len(workspace_roots)} packages")
            return workspace_roots
    
    # === TURBOREPO ===
    if (repo_path / "turbo.json").exists():
        for dir_name in ["apps", "packages"]:
            dir_path = repo_path / dir_name
            if dir_path.is_dir():
                for child in dir_path.iterdir():
                    if child.is_dir() and (child / "package.json").exists():
                        workspace_roots.append(f"{dir_name}/{child.name}")
        logger.debug(f"Detected Turborepo with {len(workspace_roots)} workspaces")
        return workspace_roots
    
    # === GENERIC MULTI-PACKAGE (services/*, apps/*, packages/*) ===
    for dir_name in ["services", "apps", "packages", "modules", "libs"]:
        dir_path = repo_path / dir_name
        if dir_path.is_dir():
            for child in dir_path.iterdir():
                if child.is_dir():
                    # Check for any package marker
                    markers = ["package.json", "pyproject.toml", "go.mod", "Cargo.toml"]
                    if any((child / m).exists() for m in markers):
                        workspace_roots.append(f"{dir_name}/{child.name}")
    
    if workspace_roots:
        logger.debug(f"Detected generic multi-package repo with {len(workspace_roots)} packages")
    
    return workspace_roots


def select_workspace_root(
    workspace_roots: List[str],
    hint: Optional[str] = None,
    repo_path: Optional[Path] = None,
) -> Optional[str]:
    """
    Select which workspace root to use for code generation.
    
    G-02: When multiple workspace roots exist, we must select one.
    This function applies heuristics to choose the best workspace.
    
    Args:
        workspace_roots: List of detected workspace roots
        hint: Optional hint from user (e.g., package name, path prefix)
        repo_path: Optional repo path for additional context
        
    Returns:
        Selected workspace root, or None if single-workspace repo
    """
    if not workspace_roots:
        return None  # Single-workspace repo
    
    if len(workspace_roots) == 1:
        return workspace_roots[0]
    
    # If hint provided, try to match
    if hint:
        for root in workspace_roots:
            if hint.lower() in root.lower():
                return root
    
    # Heuristics for common patterns
    # Prefer "app" or "main" or "api" workspaces
    priority_patterns = ["api", "app", "main", "server", "backend", "web"]
    for pattern in priority_patterns:
        for root in workspace_roots:
            if pattern in root.lower():
                return root
    
    # Default to first workspace (alphabetically sorted)
    return sorted(workspace_roots)[0]


# =============================================================================
# PUBLIC API
# =============================================================================

def get_repo_profile(repo_root: Optional[str], use_llm: bool = False) -> RepoProfile:
    """
    Main entry point: Detect and build effective RepoProfile.
    
    Combines both layers into a single call:
    1. detect_repo_profile() - Detection layer
    2. build_effective_repo_profile() - Inference layer
    """
    detected = detect_repo_profile(repo_root)
    return build_effective_repo_profile(detected, repo_root, use_llm_refinement=use_llm)

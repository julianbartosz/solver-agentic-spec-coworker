#!/usr/bin/env python3
"""
Environment Validation Script
=============================
Validates the Python environment has compatible package versions.
Used in CI/CD pipelines and local development.

Usage:
    python scripts/validate_env.py           # Exit 0 if OK, 1 if issues
    python scripts/validate_env.py --fix     # Print fix instructions
    python scripts/validate_env.py --json    # JSON output for automation
"""

import sys
import json
import subprocess
import importlib.metadata
from pathlib import Path
from typing import NamedTuple


class PackageRequirement(NamedTuple):
    name: str
    min_version: str
    max_version: str | None = None


# Critical packages with version constraints
# These versions are known to work together
CRITICAL_PACKAGES = [
    PackageRequirement("langgraph", "1.0.0", "1.1.0"),
    PackageRequirement("langgraph-checkpoint", "3.0.0", "3.1.0"),
    PackageRequirement("langgraph-checkpoint-postgres", "3.0.0", "3.1.0"),
    PackageRequirement("langchain", "1.1.0", "1.2.0"),
    PackageRequirement("langchain-core", "1.1.0", "1.2.0"),
    PackageRequirement("langchain-openai", "1.1.0", "1.2.0"),
    PackageRequirement("psycopg", "3.3.0", "3.4.0"),
]

# Critical imports that must work
CRITICAL_IMPORTS = [
    "from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver",
    "from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer",
    "from langchain_openai import ChatOpenAI",
    "from langchain_anthropic import ChatAnthropic",
]


def parse_version(version_str: str) -> tuple[int, ...]:
    """Parse version string into tuple for comparison."""
    return tuple(int(x) for x in version_str.split(".")[:3])


def check_python_version() -> tuple[bool, str]:
    """Check Python version is 3.11.x."""
    version = sys.version_info
    if version.major == 3 and version.minor == 11:
        return True, f"Python {version.major}.{version.minor}.{version.micro}"
    return False, f"Python {version.major}.{version.minor}.{version.micro} (expected 3.11.x)"


def check_site_packages() -> tuple[bool, str]:
    """Check for mixed Python versions in site-packages."""
    venv_path = Path(sys.prefix)
    lib_path = venv_path / "lib"
    
    if not lib_path.exists():
        return True, "Not in a venv or non-standard layout"
    
    python_dirs = list(lib_path.glob("python*"))
    if len(python_dirs) > 1:
        return False, f"Mixed versions: {[d.name for d in python_dirs]}"
    return True, "Single Python version in site-packages"


def check_package_versions() -> list[tuple[str, bool, str]]:
    """Check critical package versions."""
    results = []
    
    for req in CRITICAL_PACKAGES:
        try:
            installed = importlib.metadata.version(req.name)
            installed_parsed = parse_version(installed)
            min_parsed = parse_version(req.min_version)
            
            if installed_parsed < min_parsed:
                results.append((req.name, False, f"{installed} < {req.min_version}"))
            elif req.max_version:
                max_parsed = parse_version(req.max_version)
                if installed_parsed >= max_parsed:
                    results.append((req.name, False, f"{installed} >= {req.max_version}"))
                else:
                    results.append((req.name, True, installed))
            else:
                results.append((req.name, True, installed))
                
        except importlib.metadata.PackageNotFoundError:
            results.append((req.name, False, "NOT INSTALLED"))
    
    return results


def check_imports() -> list[tuple[str, bool, str]]:
    """Check critical imports work."""
    results = []
    
    for import_stmt in CRITICAL_IMPORTS:
        try:
            exec(import_stmt)
            # Extract the imported name for display
            name = import_stmt.split("import ")[-1].split(" ")[0]
            results.append((name, True, "OK"))
        except Exception as e:
            name = import_stmt.split("import ")[-1].split(" ")[0]
            results.append((name, False, str(e)[:50]))
    
    return results


def check_jsonplus_serializer() -> tuple[bool, str]:
    """Specifically test the JsonPlusSerializer.dumps_typed method that caused issues."""
    try:
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
        serializer = JsonPlusSerializer()
        # Check the method exists and is callable (it's dumps_typed in v3.x)
        if hasattr(serializer, 'dumps_typed') and callable(getattr(serializer, 'dumps_typed')):
            # Test actual serialization
            result = serializer.dumps_typed({"test": "data"})
            return True, "JsonPlusSerializer.dumps_typed() works"
        return False, "JsonPlusSerializer.dumps_typed() missing"
    except Exception as e:
        return False, str(e)[:50]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate Python environment")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--fix", action="store_true", help="Show fix instructions")
    args = parser.parse_args()
    
    results = {
        "python_version": check_python_version(),
        "site_packages": check_site_packages(),
        "packages": check_package_versions(),
        "imports": check_imports(),
        "jsonplus": check_jsonplus_serializer(),
    }
    
    # Calculate overall status
    all_ok = (
        results["python_version"][0] and
        results["site_packages"][0] and
        all(ok for _, ok, _ in results["packages"]) and
        all(ok for _, ok, _ in results["imports"]) and
        results["jsonplus"][0]
    )
    
    if args.json:
        output = {
            "ok": all_ok,
            "python": {"ok": results["python_version"][0], "version": results["python_version"][1]},
            "site_packages": {"ok": results["site_packages"][0], "message": results["site_packages"][1]},
            "packages": [{"name": n, "ok": o, "version": v} for n, o, v in results["packages"]],
            "imports": [{"name": n, "ok": o, "message": m} for n, o, m in results["imports"]],
            "jsonplus": {"ok": results["jsonplus"][0], "message": results["jsonplus"][1]},
        }
        print(json.dumps(output, indent=2))
    else:
        print("=" * 60)
        print("Environment Validation")
        print("=" * 60)
        
        # Python version
        ok, msg = results["python_version"]
        status = "✓" if ok else "✗"
        print(f"\n{status} Python: {msg}")
        
        # Site packages
        ok, msg = results["site_packages"]
        status = "✓" if ok else "✗"
        print(f"{status} Site-packages: {msg}")
        
        # Packages
        print(f"\nPackages:")
        for name, ok, version in results["packages"]:
            status = "✓" if ok else "✗"
            print(f"  {status} {name}: {version}")
        
        # Imports
        print(f"\nImports:")
        for name, ok, msg in results["imports"]:
            status = "✓" if ok else "✗"
            print(f"  {status} {name}: {msg}")
        
        # JsonPlus
        ok, msg = results["jsonplus"]
        status = "✓" if ok else "✗"
        print(f"\n{status} JsonPlusSerializer: {msg}")
        
        print("\n" + "=" * 60)
        if all_ok:
            print("✓ Environment is healthy!")
        else:
            print("✗ Environment has issues!")
            if args.fix:
                print("\nTo fix, run:")
                print("  ./scripts/setup_env.sh --clean")
        print("=" * 60)
    
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()

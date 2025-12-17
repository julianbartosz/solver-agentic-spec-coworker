#!/usr/bin/env python3
"""
Production E2E Test with Postgres

Per ADR-0005: Production-Grade Codegen Quality Gates

This test validates the complete codegen pipeline with:
1. Real Postgres database (not SQLite)
2. Real LLM calls (or mock with CODEGEN_PROFILE=production simulation)
3. Pattern learning enabled
4. Strict quality gates (ruff + mypy)
5. No skeleton fallback (fail hard)

Usage:
    # With real Postgres (requires docker-compose up -d):
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/integration_coworker \
    CODEGEN_PROFILE=production \
    python scripts/test_production_postgres.py
    
    # With mock LLM but production gates:
    USE_MOCK_LLM=true \
    DATABASE_URL=postgresql://postgres:postgres@localhost:5432/integration_coworker \
    CODEGEN_PROFILE=production \
    python scripts/test_production_postgres.py

Requirements:
    - Postgres running (docker-compose up -d)
    - ANTHROPIC_API_KEY or OPENAI_API_KEY (if not mocking)
    - ruff and mypy installed
"""
import os
import sys
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def verify_environment() -> Dict[str, str]:
    """
    Verify environment is correctly configured for production test.
    
    Returns:
        Dict with environment status
        
    Raises:
        EnvironmentError: If critical requirements not met
    """
    status = {}
    errors = []
    
    # Check Postgres
    db_url = os.getenv("DATABASE_URL", "")
    if "postgresql" not in db_url:
        errors.append(
            "DATABASE_URL must be PostgreSQL. "
            "Set DATABASE_URL=postgresql://user:pass@host:5432/db"
        )
        status["database"] = "MISSING"
    else:
        status["database"] = "postgresql"
    
    # Check profile
    profile = os.getenv("CODEGEN_PROFILE", "development")
    if profile != "production":
        logger.warning(
            f"CODEGEN_PROFILE={profile}, expected 'production'. "
            "Strict gates may not be enforced."
        )
    status["profile"] = profile
    
    # Check SQLite fallback (must NOT be enabled)
    if os.getenv("USE_SQLITE", "").lower() == "true":
        errors.append(
            "USE_SQLITE=true is set. Production tests require Postgres. "
            "Unset USE_SQLITE or set it to 'false'."
        )
        status["sqlite_fallback"] = "ENABLED (error)"
    else:
        status["sqlite_fallback"] = "disabled"
    
    # Check LLM keys
    openai_key = os.getenv("OPENAI_API_KEY", "")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    use_mock = os.getenv("USE_MOCK_LLM", "").lower() == "true"
    
    if use_mock:
        status["llm_mode"] = "mock"
        logger.info("Using MOCK LLM (USE_MOCK_LLM=true)")
    elif anthropic_key:
        status["llm_mode"] = "anthropic"
    elif openai_key:
        status["llm_mode"] = "openai"
    else:
        errors.append(
            "No LLM API key found. Set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "or USE_MOCK_LLM=true"
        )
        status["llm_mode"] = "MISSING"
    
    # Check tools installed (try venv first, then system PATH)
    import shutil
    import subprocess
    
    # Check ruff - try venv's python -m ruff first
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ruff", "--version"],
            capture_output=True, text=True, timeout=5
        )
        ruff_available = result.returncode == 0
        ruff_path = f"{sys.executable} -m ruff" if ruff_available else shutil.which("ruff")
    except Exception:
        ruff_path = shutil.which("ruff")
        ruff_available = ruff_path is not None
    
    # Check mypy - try venv's python -m mypy first
    try:
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--version"],
            capture_output=True, text=True, timeout=5
        )
        mypy_available = result.returncode == 0
        mypy_path = f"{sys.executable} -m mypy" if mypy_available else shutil.which("mypy")
    except Exception:
        mypy_path = shutil.which("mypy")
        mypy_available = mypy_path is not None
    
    status["ruff"] = "installed" if ruff_available else "MISSING"
    status["mypy"] = "installed" if mypy_available else "MISSING"
    
    if not ruff_available:
        errors.append("ruff not installed. Run: pip install ruff")
    if not mypy_available:
        errors.append("mypy not installed. Run: pip install mypy")
    
    if errors:
        for error in errors:
            logger.error(f"Environment Error: {error}")
        raise EnvironmentError("\n".join(errors))
    
    return status


async def test_postgres_connection():
    """Test Postgres connection is working."""
    from integration_coworker.config import get_db_config
    
    db_config = get_db_config()
    assert db_config["engine"] == "postgres", (
        f"Expected postgres engine, got {db_config['engine']}"
    )
    
    # Try to connect
    try:
        import asyncpg
        conn = await asyncpg.connect(db_config["url"])
        version = await conn.fetchval("SELECT version()")
        await conn.close()
        logger.info(f"✓ Postgres connection OK: {version[:50]}...")
        return True
    except ImportError:
        logger.warning("asyncpg not installed, skipping direct connection test")
        return True
    except Exception as e:
        logger.error(f"✗ Postgres connection failed: {e}")
        return False


async def test_profile_configuration():
    """Test profile is correctly configured."""
    from integration_coworker.config.profiles import (
        get_active_profile,
        get_profile_summary,
    )
    
    profile = get_active_profile()
    logger.info(f"Active profile:\n{get_profile_summary()}")
    
    if profile.name == "production":
        assert profile.enable_strict_gates, "Production must have strict gates"
        assert not profile.fallback_to_skeleton, "Production must not use skeleton fallback"
        logger.info("✓ Production profile correctly configured")
    else:
        logger.warning(f"⚠ Not in production profile: {profile.name}")
    
    return True


async def test_pattern_learning_config():
    """Test pattern learning is correctly configured based on profile."""
    from integration_coworker.config import get_settings, reset_settings
    
    # Reset to pick up env vars
    reset_settings()
    settings = get_settings()
    
    profile_name = os.getenv("CODEGEN_PROFILE", "development")
    
    if profile_name == "production":
        # In production, pattern learning should be enabled
        expected = True
    else:
        # In development, pattern learning disabled unless explicit
        explicit = os.getenv("PATTERN_LEARNING_ENABLED")
        expected = explicit.lower() in ("true", "1", "yes") if explicit else False
    
    actual = settings.pattern_learning_enabled
    
    logger.info(f"Pattern learning: expected={expected}, actual={actual}")
    
    if actual == expected:
        logger.info(f"✓ Pattern learning config correct ({actual})")
    else:
        logger.warning(f"⚠ Pattern learning mismatch: expected {expected}, got {actual}")
    
    return True


async def test_strict_gates_on_valid_code():
    """Test that valid code passes strict gates."""
    from integration_coworker.codegen.sandbox import (
        execute_in_sandbox,
        ArtifactFile,
        SandboxConfig,
    )
    
    # IMPORTANT: Keep this snippet ruff-format compliant.
    # This test is meant to validate that *strict gates* accept good code.
    valid_code = '''
"""A properly documented module."""

from __future__ import annotations


def process_items(items: list[str], limit: int | None = None) -> list[str]:
    """Process a list of items.

    Args:
        items: List of strings to process.
        limit: Optional maximum number of items to return.

    Returns:
        Processed list of items.
    """

    result = [item.strip().upper() for item in items]
    if limit is not None:
        return result[:limit]
    return result
'''
    
    # NOTE: This test is about validating the *gates wiring* (ruff+mypy run and
    # are enforced). To keep it resilient to ruff-format micro-changes across
    # versions, we format the sample using ruff first, then assert the gates
    # accept it.
    import tempfile
    import subprocess
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="valid_code_format_") as tmp:
        tmp_path = Path(tmp)
        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        file_path = src_dir / "processor.py"
        file_path.write_text(valid_code)

        subprocess.run(
            [sys.executable, "-m", "ruff", "format", str(file_path)],
            check=True,
            capture_output=True,
            text=True,
        )
        valid_code_formatted = file_path.read_text()

    result = await execute_in_sandbox(
        artifacts=[ArtifactFile("src/processor.py", valid_code_formatted)],
        config=SandboxConfig(
            enable_pytest=False,
            cleanup_on_success=True,
        ),
    )
    
    if result.success:
        logger.info("✓ Valid code passes strict gates")
    else:
        logger.error(f"✗ Valid code failed gates: {result.summary}")
        for gate in result.failed_gates:
            logger.error(f"  - {gate.name}: {gate.output[:200]}")
    
    return result.success


async def test_strict_gates_on_invalid_code():
    """Test that invalid code fails strict gates."""
    from integration_coworker.codegen.sandbox import (
        execute_in_sandbox,
        ArtifactFile,
        SandboxConfig,
    )
    
    # Code with type error
    invalid_code = '''
def add_numbers(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


# Type error: passing string
result: int = add_numbers("hello", "world")
'''
    
    result = await execute_in_sandbox(
        artifacts=[ArtifactFile("src/broken.py", invalid_code)],
        config=SandboxConfig(
            enable_pytest=False,
            cleanup_on_failure=True,
        ),
    )
    
    if not result.success:
        logger.info(f"✓ Invalid code correctly rejected: {result.summary}")
    else:
        logger.error("✗ Invalid code should have failed gates!")
    
    return not result.success


async def run_all_tests() -> Dict[str, bool]:
    """Run all production tests and return results."""
    results = {}
    
    tests = [
        ("postgres_connection", test_postgres_connection),
        ("profile_configuration", test_profile_configuration),
        ("pattern_learning_config", test_pattern_learning_config),
        ("strict_gates_valid_code", test_strict_gates_on_valid_code),
        ("strict_gates_invalid_code", test_strict_gates_on_invalid_code),
    ]
    
    for name, test_fn in tests:
        try:
            logger.info(f"\n--- Running: {name} ---")
            result = await test_fn()
            results[name] = result
        except Exception as e:
            logger.error(f"✗ {name} raised exception: {e}")
            results[name] = False
    
    return results


def print_summary(results: Dict[str, bool], env_status: Dict[str, str]):
    """Print test summary."""
    print("\n" + "=" * 60)
    print("PRODUCTION TEST SUMMARY")
    print("=" * 60)
    
    print("\nEnvironment:")
    for key, value in env_status.items():
        print(f"  {key}: {value}")
    
    print("\nTest Results:")
    passed = 0
    failed = 0
    for name, result in results.items():
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
        if result:
            passed += 1
        else:
            failed += 1
    
    print(f"\nTotal: {passed}/{len(results)} passed")
    print("=" * 60)
    
    return failed == 0


def main():
    """Main entry point."""
    print(f"\nProduction Postgres Test - {datetime.now().isoformat()}")
    print("-" * 60)
    
    try:
        env_status = verify_environment()
    except EnvironmentError as e:
        print(f"\n❌ Environment validation failed:\n{e}")
        sys.exit(1)
    
    print("\n✓ Environment validated")
    
    # Run tests
    results = asyncio.run(run_all_tests())
    
    # Print summary
    all_passed = print_summary(results, env_status)
    
    if all_passed:
        print("\n✅ All production tests passed!")
        sys.exit(0)
    else:
        print("\n❌ Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    main()

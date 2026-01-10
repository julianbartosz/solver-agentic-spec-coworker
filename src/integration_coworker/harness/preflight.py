"""
Preflight checks for production readiness.

Verifies that all required services and configuration are available
before attempting to run a workflow. Fast checks only - no slow operations.
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class PreflightCheck:
    """Result of a single preflight check."""
    name: str
    passed: bool
    message: str
    fix_hint: Optional[str] = None


@dataclass
class PreflightResult:
    """Aggregate result of all preflight checks."""
    checks: List[PreflightCheck] = field(default_factory=list)
    
    @property
    def all_passed(self) -> bool:
        return all(c.passed for c in self.checks)
    
    @property
    def failed_checks(self) -> List[PreflightCheck]:
        return [c for c in self.checks if not c.passed]


def _check_postgres() -> PreflightCheck:
    """Check PostgreSQL connection."""
    try:
        from integration_coworker.persistence.postgres import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        return PreflightCheck(
            name="PostgreSQL",
            passed=True,
            message="Connected successfully",
        )
    except ImportError:
        return PreflightCheck(
            name="PostgreSQL",
            passed=False,
            message="PostgreSQL module not available",
            fix_hint="Install: pip install psycopg2-binary",
        )
    except Exception as e:
        return PreflightCheck(
            name="PostgreSQL",
            passed=False,
            message=f"Connection failed: {str(e)[:100]}",
            fix_hint="Run: docker compose up -d db && sleep 5",
        )


def _check_redis() -> PreflightCheck:
    """Check Redis connection."""
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    try:
        import redis
        r = redis.from_url(redis_url, socket_connect_timeout=5)
        r.ping()
        return PreflightCheck(
            name="Redis",
            passed=True,
            message=f"Connected to {redis_url.split('@')[-1] if '@' in redis_url else redis_url}",
        )
    except ImportError:
        return PreflightCheck(
            name="Redis",
            passed=False,
            message="Redis module not available",
            fix_hint="Install: pip install redis",
        )
    except Exception as e:
        return PreflightCheck(
            name="Redis",
            passed=False,
            message=f"Connection failed: {str(e)[:100]}",
            fix_hint="Run: docker compose up -d redis",
        )


def _check_openai_key() -> PreflightCheck:
    """Check OPENAI_API_KEY is set."""
    key = os.environ.get("OPENAI_API_KEY", "")
    if key and len(key) > 20:
        # Mask for display
        masked = f"{key[:8]}...{key[-4:]}"
        return PreflightCheck(
            name="OPENAI_API_KEY",
            passed=True,
            message=f"Set ({masked})",
        )
    elif key:
        return PreflightCheck(
            name="OPENAI_API_KEY",
            passed=False,
            message="Key appears too short or invalid",
            fix_hint="Set a valid OpenAI API key in .env or environment",
        )
    else:
        return PreflightCheck(
            name="OPENAI_API_KEY",
            passed=False,
            message="Not set",
            fix_hint="Add OPENAI_API_KEY=sk-... to .env or export it",
        )


def _check_database_url() -> PreflightCheck:
    """Check DATABASE_URL is set."""
    url = os.environ.get("DATABASE_URL", "")
    if url:
        # Mask password if present
        if "@" in url:
            masked = url.split("@")[0].split(":")
            if len(masked) > 2:
                masked = f"{masked[0]}:{masked[1]}:***@{url.split('@')[1]}"
            else:
                masked = f"***@{url.split('@')[1]}"
        else:
            masked = url[:30] + "..."
        return PreflightCheck(
            name="DATABASE_URL",
            passed=True,
            message=f"Set ({masked[:50]})",
        )
    else:
        return PreflightCheck(
            name="DATABASE_URL",
            passed=False,
            message="Not set",
            fix_hint="Set DATABASE_URL=postgresql://... in .env",
        )


def _check_critical_imports() -> PreflightCheck:
    """Check that critical imports work."""
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from langchain_openai import ChatOpenAI
        return PreflightCheck(
            name="Critical Imports",
            passed=True,
            message="LangGraph + LangChain available",
        )
    except ImportError as e:
        return PreflightCheck(
            name="Critical Imports",
            passed=False,
            message=f"Import failed: {str(e)[:80]}",
            fix_hint="Run: pip install -e . or check requirements.txt",
        )


def run_preflight_checks() -> PreflightResult:
    """
    Run all preflight checks.
    
    Checks (in order):
    1. DATABASE_URL environment variable
    2. OPENAI_API_KEY environment variable
    3. PostgreSQL connection
    4. Redis connection
    5. Critical imports (LangGraph, LangChain)
    
    Returns:
        PreflightResult with individual check statuses.
        
    Usage:
        result = run_preflight_checks()
        if not result.all_passed:
            for check in result.failed_checks:
                print(f"FAIL: {check.name} - {check.message}")
                if check.fix_hint:
                    print(f"  Fix: {check.fix_hint}")
    """
    result = PreflightResult()
    
    # Order matters: check config before connections
    result.checks.append(_check_database_url())
    result.checks.append(_check_openai_key())
    result.checks.append(_check_postgres())
    result.checks.append(_check_redis())
    result.checks.append(_check_critical_imports())
    
    logger.info(
        f"Preflight: {sum(1 for c in result.checks if c.passed)}/{len(result.checks)} checks passed"
    )
    
    return result

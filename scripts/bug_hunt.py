#!/usr/bin/env python3
"""
Automated Bug Hunting Script

Runs tests in tiers, captures failures, generates report.
Designed for systematic bug finding during production hardening.

Usage:
    python scripts/bug_hunt.py                    # Full hunt (all tiers)
    python scripts/bug_hunt.py --tier unit        # Only unit tests
    python scripts/bug_hunt.py --tier integration # Only integration tests
    python scripts/bug_hunt.py --report bugs.md   # Generate markdown report
    python scripts/bug_hunt.py --verbose          # Show test output
    python scripts/bug_hunt.py --timeout 120      # Custom timeout per test

Environment:
    USE_MOCK_LLM=true          # Use mock LLM (default)
    USE_SQLITE=true            # Use SQLite (default)
    VALIDATION_PROFILE=offline # Network profile (default)

Exit codes:
    0 - All tests passed
    1 - Some tests failed
    2 - Configuration error
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class TestResult:
    """Result of a single test."""
    nodeid: str
    outcome: str  # passed, failed, error, skipped, xfailed, xpassed
    duration: float
    error_message: str = ""
    longrepr: str = ""
    
    @property
    def is_failure(self) -> bool:
        return self.outcome in ("failed", "error")


@dataclass
class TierResult:
    """Result of running a test tier."""
    tier_name: str
    markers: str
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    duration: float = 0.0
    failures: List[TestResult] = field(default_factory=list)


# =============================================================================
# Test Tier Definitions
# =============================================================================

TIERS = {
    "unit": {
        "name": "Unit Tests",
        "markers": "not (slow or integration or integration_live or e2e or docker or postgres)",
        "timeout": 60,
        "description": "Fast unit tests with no external dependencies",
    },
    "integration": {
        "name": "Integration Tests (Mocked)",
        "markers": "integration and not (integration_live or e2e or docker or postgres)",
        "timeout": 120,
        "description": "Integration tests with mocked external services",
    },
    "postgres": {
        "name": "Postgres Tests",
        "markers": "postgres and not integration_live",
        "timeout": 180,
        "description": "Tests requiring Postgres backend",
    },
    "e2e": {
        "name": "E2E Tests",
        "markers": "e2e and not integration_live",
        "timeout": 300,
        "description": "End-to-end tests (may require Docker)",
    },
    "slow": {
        "name": "Slow Tests",
        "markers": "slow and not integration_live",
        "timeout": 600,
        "description": "Tests marked as slow (heavy computation or I/O)",
    },
}


# =============================================================================
# Test Runner
# =============================================================================

def run_tier(
    tier_name: str,
    tier_config: Dict,
    timeout: Optional[int] = None,
    verbose: bool = False,
) -> TierResult:
    """Run tests for a single tier and collect results."""
    
    markers = tier_config["markers"]
    test_timeout = timeout or tier_config["timeout"]
    
    # Use pytest-json-report for structured output
    report_file = Path(f".bug_hunt_{tier_name}.json")
    
    cmd = [
        sys.executable, "-m", "pytest",
        "tests/",
        "-m", markers,
        f"--timeout={test_timeout}",
        "--json-report",
        f"--json-report-file={report_file}",
        "-q",
        "--tb=short",
    ]
    
    if not verbose:
        cmd.append("--no-header")
    
    print(f"\n{'='*60}")
    print(f"🔍 Running: {tier_config['name']}")
    print(f"   Markers: {markers}")
    print(f"   Timeout: {test_timeout}s per test")
    print(f"{'='*60}")
    
    start_time = time.time()
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=not verbose,
            text=True,
            timeout=3600,  # 1 hour max for entire tier
        )
    except subprocess.TimeoutExpired:
        print(f"❌ Tier {tier_name} timed out after 1 hour")
        return TierResult(tier_name=tier_name, markers=markers, errors=1)
    
    duration = time.time() - start_time
    
    # Parse JSON report
    tier_result = TierResult(
        tier_name=tier_name,
        markers=markers,
        duration=duration,
    )
    
    if report_file.exists():
        try:
            with open(report_file) as f:
                data = json.load(f)
            
            # Extract summary
            summary = data.get("summary", {})
            tier_result.passed = summary.get("passed", 0)
            tier_result.failed = summary.get("failed", 0)
            tier_result.skipped = summary.get("skipped", 0)
            tier_result.errors = summary.get("error", 0)
            
            # Extract failures
            for test in data.get("tests", []):
                if test.get("outcome") in ("failed", "error"):
                    tier_result.failures.append(TestResult(
                        nodeid=test.get("nodeid", "unknown"),
                        outcome=test.get("outcome", "unknown"),
                        duration=test.get("duration", 0),
                        error_message=test.get("call", {}).get("crash", {}).get("message", ""),
                        longrepr=test.get("call", {}).get("longrepr", ""),
                    ))
            
            # Cleanup
            report_file.unlink()
            
        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️  Error parsing test results: {e}")
    
    # Print tier summary
    total = tier_result.passed + tier_result.failed + tier_result.skipped + tier_result.errors
    print(f"\n📊 {tier_config['name']} Results:")
    print(f"   ✅ Passed:  {tier_result.passed}")
    print(f"   ❌ Failed:  {tier_result.failed}")
    print(f"   ⏭️  Skipped: {tier_result.skipped}")
    print(f"   💥 Errors:  {tier_result.errors}")
    print(f"   ⏱️  Time:    {tier_result.duration:.1f}s")
    
    if tier_result.failures:
        print(f"\n   Failures:")
        for f in tier_result.failures[:5]:  # Show first 5
            print(f"      • {f.nodeid}")
        if len(tier_result.failures) > 5:
            print(f"      ... and {len(tier_result.failures) - 5} more")
    
    return tier_result


# =============================================================================
# Report Generation
# =============================================================================

def generate_report(results: List[TierResult], output_path: Optional[Path] = None) -> str:
    """Generate a markdown report of test results."""
    
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    lines = [
        "# Bug Hunt Report",
        "",
        f"**Generated**: {now}",
        "",
        "## Summary",
        "",
        "| Tier | Passed | Failed | Skipped | Errors | Duration |",
        "|------|--------|--------|---------|--------|----------|",
    ]
    
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    total_errors = 0
    total_duration = 0
    all_failures = []
    
    for r in results:
        lines.append(
            f"| {r.tier_name} | {r.passed} | {r.failed} | {r.skipped} | "
            f"{r.errors} | {r.duration:.1f}s |"
        )
        total_passed += r.passed
        total_failed += r.failed
        total_skipped += r.skipped
        total_errors += r.errors
        total_duration += r.duration
        all_failures.extend(r.failures)
    
    lines.append(
        f"| **TOTAL** | **{total_passed}** | **{total_failed}** | "
        f"**{total_skipped}** | **{total_errors}** | **{total_duration:.1f}s** |"
    )
    
    # Overall status
    lines.extend([
        "",
        "## Status",
        "",
    ])
    
    if total_failed == 0 and total_errors == 0:
        lines.append("✅ **ALL TESTS PASSED**")
    else:
        lines.append(f"❌ **{total_failed + total_errors} FAILURES FOUND**")
    
    # Failure details
    if all_failures:
        lines.extend([
            "",
            "## Failures",
            "",
        ])
        
        for r in results:
            if r.failures:
                lines.append(f"### {r.tier_name}")
                lines.append("")
                for f in r.failures:
                    lines.append(f"#### `{f.nodeid}`")
                    lines.append("")
                    lines.append(f"- **Outcome**: {f.outcome}")
                    lines.append(f"- **Duration**: {f.duration:.2f}s")
                    if f.error_message:
                        lines.append(f"- **Error**: {f.error_message[:200]}")
                    lines.append("")
    
    # Environment info
    lines.extend([
        "",
        "## Environment",
        "",
        f"- USE_MOCK_LLM: {os.getenv('USE_MOCK_LLM', 'not set')}",
        f"- USE_SQLITE: {os.getenv('USE_SQLITE', 'not set')}",
        f"- VALIDATION_PROFILE: {os.getenv('VALIDATION_PROFILE', 'not set')}",
        f"- Python: {sys.version.split()[0]}",
        "",
    ])
    
    report = "\n".join(lines)
    
    if output_path:
        output_path.write_text(report)
        print(f"\n📝 Report written to: {output_path}")
    
    return report


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Automated bug hunting script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--tier",
        choices=list(TIERS.keys()) + ["all"],
        default="all",
        help="Which test tier to run (default: all)",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="Output markdown report to file",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show test output",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help="Override timeout per test (seconds)",
    )
    parser.add_argument(
        "--list-tiers",
        action="store_true",
        help="List available tiers and exit",
    )
    
    args = parser.parse_args()
    
    # List tiers
    if args.list_tiers:
        print("Available test tiers:")
        print()
        for name, config in TIERS.items():
            print(f"  {name}:")
            print(f"    Description: {config['description']}")
            print(f"    Markers: {config['markers']}")
            print(f"    Default timeout: {config['timeout']}s")
            print()
        return 0
    
    # Set defaults for safe testing
    os.environ.setdefault("USE_MOCK_LLM", "true")
    os.environ.setdefault("USE_SQLITE", "true")
    os.environ.setdefault("VALIDATION_PROFILE", "offline")
    
    print("🐛 Bug Hunt Starting...")
    print(f"   USE_MOCK_LLM={os.getenv('USE_MOCK_LLM')}")
    print(f"   USE_SQLITE={os.getenv('USE_SQLITE')}")
    print(f"   VALIDATION_PROFILE={os.getenv('VALIDATION_PROFILE')}")
    
    # Determine which tiers to run
    if args.tier == "all":
        tiers_to_run = list(TIERS.keys())
    else:
        tiers_to_run = [args.tier]
    
    # Run tiers
    results = []
    for tier_name in tiers_to_run:
        tier_config = TIERS[tier_name]
        result = run_tier(
            tier_name=tier_name,
            tier_config=tier_config,
            timeout=args.timeout,
            verbose=args.verbose,
        )
        results.append(result)
    
    # Generate report
    report_path = args.report or Path("bug_hunt_report.md")
    generate_report(results, report_path)
    
    # Final summary
    total_failures = sum(r.failed + r.errors for r in results)
    
    print()
    print("=" * 60)
    if total_failures == 0:
        print("✅ BUG HUNT COMPLETE - No failures found!")
        return 0
    else:
        print(f"❌ BUG HUNT COMPLETE - {total_failures} failure(s) found")
        print(f"   See {report_path} for details")
        return 1


if __name__ == "__main__":
    sys.exit(main())

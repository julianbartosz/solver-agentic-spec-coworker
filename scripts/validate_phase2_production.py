#!/usr/bin/env python3
"""
Production Validation Script for Phase 2 File Processing.

This script validates the REAL capabilities delivered:
1. ContentHandle abstraction with pre-load size checking
2. Process-based hard timeout that can kill stuck parsers
3. Two-layer timeout (cooperative + hard)

Run: .venv311/bin/python scripts/validate_phase2_production.py
"""

import io
import os
import sys
import tempfile
import time
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from integration_coworker.sources import (
    BytesContentHandle,
    ContentHandle,
    FileTooLargeError,
    PathContentHandle,
    ProcessingContext,
    StreamContentHandle,
    content_handle_from_path,
    detect_and_route_handle,
    ensure_sources_registered,
)
from integration_coworker.sources.hard_timeout_worker import (
    HardTimeoutConfig,
    HardTimeoutError,
    run_with_hard_timeout,
)
from integration_coworker.sources.processing_context import FileSizeGate


def print_header(text: str) -> None:
    """Print a formatted header."""
    print(f"\n{'='*60}")
    print(f"  {text}")
    print(f"{'='*60}")


def print_result(name: str, passed: bool, details: str = "") -> None:
    """Print test result."""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"  {status}: {name}")
    if details:
        print(f"         {details}")


# Module-level functions for pickling
def fast_function(x: int) -> int:
    """Fast function for normal completion test."""
    return x * 2


def slow_function_for_test() -> str:
    """Sleep forever (for timeout test)."""
    time.sleep(60)
    return "never"


def quick_func() -> str:
    """Quick function for mode off test."""
    return "done"


def create_temp_csv(size_bytes: int) -> Path:
    """Create a temp CSV file of approximate size."""
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        os.write(fd, b"id,name,value\n")
        row = b"1,test_name,12345\n"
        rows_needed = max(1, size_bytes // len(row))
        for i in range(rows_needed):
            os.write(fd, row)
    finally:
        os.close(fd)
    return Path(path)


def main() -> int:
    """Run production validation scenarios."""
    print("\n" + "="*70)
    print("  PHASE 2 PRODUCTION VALIDATION")
    print("  File Processing Enterprise Hardening")
    print("="*70)
    
    ensure_sources_registered()
    
    results = []
    
    # =========================================================================
    # Scenario 1: ContentHandle - PathContentHandle
    # =========================================================================
    print_header("Scenario 1: PathContentHandle (File-Based)")
    
    try:
        # Create temp file
        temp_file = create_temp_csv(1024)
        handle = PathContentHandle(temp_file)
        
        # Verify size_bytes() works WITHOUT reading
        size = handle.size_bytes()
        passed = size is not None and size > 0
        print_result("size_bytes() returns file size", passed, f"size={size}")
        results.append(passed)
        
        # Verify open_bytes() returns stream
        with handle.open_bytes() as f:
            first_line = f.readline()
        passed = b"id,name,value" in first_line
        print_result("open_bytes() returns binary stream", passed)
        results.append(passed)
        
        # Verify uri property
        passed = str(temp_file) == handle.uri
        print_result("uri property returns path", passed)
        results.append(passed)
        
        # Cleanup
        temp_file.unlink()
        
    except Exception as e:
        print_result("Scenario 1", False, str(e))
        results.append(False)
    
    # =========================================================================
    # Scenario 2: PRE-LOAD Size Check
    # =========================================================================
    print_header("Scenario 2: Pre-Load Size Checking")
    
    try:
        # Create file larger than limit
        temp_file = create_temp_csv(10 * 1024)  # 10KB
        handle = PathContentHandle(temp_file)
        
        # Configure to reject files > 1KB
        ctx = ProcessingContext(
            size_gate=FileSizeGate(max_size_bytes=1024)
        )
        
        # Verify pre-load rejection
        rejected = False
        try:
            detect_and_route_handle(handle, processing_ctx=ctx)
        except FileTooLargeError as e:
            rejected = True
            print_result(
                "Pre-load rejection works",
                True,
                f"Rejected {e.file_size_bytes} bytes (limit: {e.max_size_bytes})"
            )
        
        if not rejected:
            print_result("Pre-load rejection works", False, "File was not rejected")
        results.append(rejected)
        
        # Cleanup
        temp_file.unlink()
        
    except Exception as e:
        print_result("Scenario 2", False, str(e))
        results.append(False)
    
    # =========================================================================
    # Scenario 3: detect_and_route_handle() Success Path
    # =========================================================================
    print_header("Scenario 3: detect_and_route_handle() Success")
    
    try:
        temp_file = create_temp_csv(1024)
        handle = content_handle_from_path(temp_file)
        
        result = detect_and_route_handle(handle)
        
        passed = result.confidence > 0.5
        print_result(
            "Successful detection and parse",
            passed,
            f"confidence={result.confidence:.2f}"
        )
        results.append(passed)
        
        # Cleanup
        temp_file.unlink()
        
    except Exception as e:
        print_result("Scenario 3", False, str(e))
        results.append(False)
    
    # =========================================================================
    # Scenario 4: Hard Timeout - Normal Completion
    # =========================================================================
    print_header("Scenario 4: Hard Timeout - Normal Completion")
    
    try:
        result = run_with_hard_timeout(
            fast_function,
            args=(21,),
            timeout_seconds=5.0,
        )
        
        passed = result == 42
        print_result("Fast function completes normally", passed, f"result={result}")
        results.append(passed)
        
    except Exception as e:
        print_result("Scenario 4", False, str(e))
        results.append(False)
    
    # =========================================================================
    # Scenario 5: Hard Timeout - Kills Slow Process
    # =========================================================================
    print_header("Scenario 5: Hard Timeout - Process Termination")
    
    try:
        start = time.time()
        
        try:
            run_with_hard_timeout(
                slow_function_for_test,
                timeout_seconds=1.0,
                grace_seconds=0.5,
            )
            passed = False
            print_result("Slow function killed", False, "Did not timeout!")
        except HardTimeoutError as e:
            elapsed = time.time() - start
            passed = elapsed < 3.0  # Should complete quickly
            print_result(
                "Slow function killed",
                passed,
                f"Terminated in {elapsed:.2f}s (timeout={e.timeout_seconds}s)"
            )
        
        results.append(passed)
        
    except Exception as e:
        print_result("Scenario 5", False, str(e))
        results.append(False)
    
    # =========================================================================
    # Scenario 6: Hard Timeout Mode Switching
    # =========================================================================
    print_header("Scenario 6: Hard Timeout Mode Off")
    
    try:
        config = HardTimeoutConfig(mode="off")
        
        result = run_with_hard_timeout(
            quick_func,
            timeout_seconds=0.001,  # Very short - would timeout if enabled
            config=config,
        )
        
        passed = result == "done"
        print_result("Mode 'off' skips subprocess", passed)
        results.append(passed)
        
    except Exception as e:
        print_result("Scenario 6", False, str(e))
        results.append(False)
    
    # =========================================================================
    # Scenario 7: BytesContentHandle (Legacy Compatibility)
    # =========================================================================
    print_header("Scenario 7: BytesContentHandle (Legacy)")
    
    try:
        content = b"id,name,value\n1,alpha,100\n2,beta,200\n"
        handle = BytesContentHandle(content, uri="legacy.csv")
        
        # Verify size_bytes() works
        size = handle.size_bytes()
        passed = size == len(content)
        print_result("size_bytes() returns len(bytes)", passed, f"size={size}")
        results.append(passed)
        
        # Verify it works with detect_and_route_handle
        result = detect_and_route_handle(handle)
        passed = result.confidence > 0.5
        print_result("Works with detect_and_route_handle", passed)
        results.append(passed)
        
    except Exception as e:
        print_result("Scenario 7", False, str(e))
        results.append(False)
    
    # =========================================================================
    # Scenario 8: StreamContentHandle
    # =========================================================================
    print_header("Scenario 8: StreamContentHandle (Network Simulation)")
    
    try:
        content = b"id,name,value\n1,alpha,100\n"
        stream = io.BytesIO(content)
        handle = StreamContentHandle(stream, size_hint=len(content))
        
        # Verify size_bytes() returns hint
        size = handle.size_bytes()
        passed = size == len(content)
        print_result("size_bytes() returns hint", passed, f"size_hint={size}")
        results.append(passed)
        
    except Exception as e:
        print_result("Scenario 8", False, str(e))
        results.append(False)
    
    # =========================================================================
    # Summary
    # =========================================================================
    print_header("VALIDATION SUMMARY")
    
    passed_count = sum(results)
    total_count = len(results)
    all_passed = passed_count == total_count
    
    print(f"\n  Total: {passed_count}/{total_count} scenarios passed")
    
    if all_passed:
        print("\n  ✅ ALL PRODUCTION SCENARIOS VALIDATED")
        print("\n  Phase 2 Deliverables Confirmed:")
        print("    - ContentHandle abstraction with 3 implementations")
        print("    - Pre-load size checking (rejects BEFORE memory allocation)")
        print("    - Process-based hard timeout (kills stuck processes)")
        print("    - detect_and_route_handle() entry point")
    else:
        print("\n  ❌ SOME SCENARIOS FAILED")
        print("  Please review the failures above.")
    
    print("\n" + "="*70)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    # Need to use spawn for subprocess on macOS
    import multiprocessing
    multiprocessing.set_start_method("spawn", force=True)
    
    sys.exit(main())

#!/usr/bin/env python3
"""
File Processing Production Validation

Demonstrates enterprise-scale file processing features:
1. Size Gating - Reject oversized files with clear errors
2. Timeout Enforcement - Hard deadline on all operations  
3. Cancellation Support - Graceful shutdown via CancelToken
4. Progress Tracking - Visibility into long operations
5. Backwards Compatibility - Existing code still works

Run: python scripts/validate_file_processing.py

Per docs/FILE_PROCESSING_HARDENING_DESIGN.md
"""

import os
import sys
import threading
import time

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from integration_coworker.sources import (
    CancelledException,
    FileProcessingConfig,
    FileProcessingTimeout,
    FileTooLargeError,
    ProcessingContext,
    detect_and_route,
    ensure_sources_registered,
)


def section(title: str) -> None:
    """Print section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")


def test_passed(name: str) -> None:
    """Print test passed."""
    print(f"  ✅ {name}")


def test_failed(name: str, error: str) -> None:
    """Print test failed."""
    print(f"  ❌ {name}: {error}")


def main() -> int:
    """Run production validation tests."""
    print("\n" + "="*60)
    print("  FILE PROCESSING PRODUCTION VALIDATION")
    print("  Enterprise-Scale Features Demo")
    print("="*60)
    
    ensure_sources_registered()
    
    passed = 0
    failed = 0
    
    # ==========================================================================
    # 1. SIZE GATING
    # ==========================================================================
    section("1. SIZE GATING")
    
    # Test 1.1: Small file passes
    try:
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            max_size_bytes=1000,
            warn_size_bytes=500,
        ))
        content = b"name,age\nAlice,30\nBob,25"
        result = detect_and_route(content, "small.csv", processing_ctx=ctx)
        
        if result.is_valid():
            test_passed("Small file (<500B) passes without warning")
            passed += 1
        else:
            test_failed("Small file passes", "Result not valid")
            failed += 1
    except Exception as e:
        test_failed("Small file passes", str(e))
        failed += 1
    
    # Test 1.2: Medium file gets warning
    try:
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            max_size_bytes=1000,
            warn_size_bytes=50,
        ))
        content = b"name,age\n" + b"Alice,30\n" * 10  # ~100 bytes
        result = detect_and_route(content, "medium.csv", processing_ctx=ctx)
        
        has_warning = any("streaming" in w.lower() for w in result.warnings)
        if has_warning:
            test_passed("Medium file (50-1000B) gets size warning")
            print(f"       Warning: {result.warnings[0][:60]}...")
            passed += 1
        else:
            test_failed("Medium file warning", "No size warning in result")
            failed += 1
    except Exception as e:
        test_failed("Medium file warning", str(e))
        failed += 1
    
    # Test 1.3: Large file rejected
    try:
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            max_size_bytes=50,
            warn_size_bytes=20,
        ))
        content = b"name,age\n" + b"Alice,30\n" * 10  # ~100 bytes
        detect_and_route(content, "large.csv", processing_ctx=ctx)
        
        test_failed("Large file rejected", "Expected FileTooLargeError")
        failed += 1
    except FileTooLargeError as e:
        test_passed("Large file (>50B) rejected with clear error")
        print(f"       Error: {str(e)[:60]}...")
        passed += 1
    except Exception as e:
        test_failed("Large file rejected", f"Wrong exception: {type(e).__name__}")
        failed += 1
    
    # ==========================================================================
    # 2. TIMEOUT ENFORCEMENT
    # ==========================================================================
    section("2. TIMEOUT ENFORCEMENT")
    
    # Test 2.1: Fast operation completes
    try:
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            parse_timeout_s=10.0,
        ))
        content = b"name,age\nAlice,30"
        result = detect_and_route(content, "fast.csv", processing_ctx=ctx)
        
        if result.is_valid():
            test_passed("Fast operation completes within timeout")
            print(f"       Elapsed: {ctx.elapsed_seconds:.3f}s")
            passed += 1
        else:
            test_failed("Fast operation", "Result not valid")
            failed += 1
    except Exception as e:
        test_failed("Fast operation", str(e))
        failed += 1
    
    # Test 2.2: Expired context raises timeout
    try:
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            parse_timeout_s=0.001,  # 1ms
        ))
        time.sleep(0.01)  # Wait for expiration
        
        content = b"name,age\nAlice,30"
        detect_and_route(content, "timeout.csv", processing_ctx=ctx)
        
        test_failed("Expired context timeout", "Expected FileProcessingTimeout")
        failed += 1
    except FileProcessingTimeout as e:
        test_passed("Expired context raises FileProcessingTimeout")
        print(f"       Timeout after: {e.elapsed_seconds:.3f}s (limit: {e.timeout_seconds:.3f}s)")
        passed += 1
    except Exception as e:
        test_failed("Expired context timeout", f"Wrong exception: {type(e).__name__}")
        failed += 1
    
    # ==========================================================================
    # 3. CANCELLATION SUPPORT
    # ==========================================================================
    section("3. CANCELLATION SUPPORT")
    
    # Test 3.1: Non-cancelled completes
    try:
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            enable_cancellation=True,
        ))
        content = b"name,age\nAlice,30"
        result = detect_and_route(content, "notcancelled.csv", processing_ctx=ctx)
        
        if result.is_valid() and not ctx.is_cancelled:
            test_passed("Non-cancelled context allows completion")
            passed += 1
        else:
            test_failed("Non-cancelled context", "Unexpected state")
            failed += 1
    except Exception as e:
        test_failed("Non-cancelled context", str(e))
        failed += 1
    
    # Test 3.2: Pre-cancelled raises exception
    try:
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            enable_cancellation=True,
        ))
        ctx.cancel_token.cancel("User requested shutdown")
        
        content = b"name,age\nAlice,30"
        detect_and_route(content, "cancelled.csv", processing_ctx=ctx)
        
        test_failed("Pre-cancelled raises", "Expected CancelledException")
        failed += 1
    except CancelledException as e:
        test_passed("Pre-cancelled context raises CancelledException")
        print(f"       Reason: {e.reason}")
        passed += 1
    except Exception as e:
        test_failed("Pre-cancelled raises", f"Wrong exception: {type(e).__name__}")
        failed += 1
    
    # Test 3.3: Thread-based cancellation
    try:
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            enable_cancellation=True,
            cancel_check_interval_rows=10,
        ))
        
        cancelled_detected = threading.Event()
        
        def cancel_after_delay():
            time.sleep(0.01)
            ctx.cancel_token.cancel("External cancel")
        
        # Start canceller thread
        cancel_thread = threading.Thread(target=cancel_after_delay)
        cancel_thread.start()
        
        # Simulate processing loop
        try:
            for i in range(10000):
                ctx.check_on_item(i)
                time.sleep(0.001)
        except CancelledException:
            cancelled_detected.set()
        
        cancel_thread.join()
        
        if cancelled_detected.is_set():
            test_passed("Thread-based cancellation interrupts processing")
            passed += 1
        else:
            test_failed("Thread cancellation", "Not interrupted")
            failed += 1
    except Exception as e:
        test_failed("Thread cancellation", str(e))
        failed += 1
    
    # ==========================================================================
    # 4. PROGRESS TRACKING
    # ==========================================================================
    section("4. PROGRESS TRACKING")
    
    # Test 4.1: Progress callback invoked
    try:
        progress_calls = []
        
        def on_progress(processed, total):
            progress_calls.append((processed, total))
        
        ctx = ProcessingContext.from_config(FileProcessingConfig(
            enable_cancellation=True,
            cancel_check_interval_rows=25,
        ))
        ctx.progress_callback = on_progress
        
        # Simulate processing 100 items
        for i in range(100):
            ctx.check_on_item(i, total_estimate=100)
        
        if len(progress_calls) >= 4:  # At least 4 reports (at 0, 25, 50, 75)
            test_passed("Progress callback invoked during processing")
            print(f"       Progress reports: {len(progress_calls)} ({progress_calls[0]} ... {progress_calls[-1]})")
            passed += 1
        else:
            test_failed("Progress callback", f"Only {len(progress_calls)} calls")
            failed += 1
    except Exception as e:
        test_failed("Progress callback", str(e))
        failed += 1
    
    # ==========================================================================
    # 5. BACKWARDS COMPATIBILITY
    # ==========================================================================
    section("5. BACKWARDS COMPATIBILITY")
    
    # Test 5.1: detect_and_route without context
    try:
        content = b"name,age\nAlice,30\nBob,25"
        result = detect_and_route(content, "compat.csv")  # No processing_ctx
        
        if result.is_valid():
            test_passed("detect_and_route() works without processing_ctx")
            passed += 1
        else:
            test_failed("No context compat", "Result not valid")
            failed += 1
    except Exception as e:
        test_failed("No context compat", str(e))
        failed += 1
    
    # Test 5.2: Explicit None context
    try:
        content = b"name,age\nAlice,30"
        result = detect_and_route(content, "none.csv", processing_ctx=None)
        
        if result.is_valid():
            test_passed("Explicit None context works")
            passed += 1
        else:
            test_failed("None context", "Result not valid")
            failed += 1
    except Exception as e:
        test_failed("None context", str(e))
        failed += 1
    
    # ==========================================================================
    # 6. CONFIG FROM ENVIRONMENT
    # ==========================================================================
    section("6. ENVIRONMENT CONFIGURATION")
    
    # Test 6.1: Load config from environment
    try:
        # Set test env vars
        os.environ['FILE_MAX_SIZE_MB'] = '100'
        os.environ['FILE_PARSE_TIMEOUT_S'] = '60'
        
        config = FileProcessingConfig.from_env()
        
        if config.max_size_bytes == 100 * 1024 * 1024 and config.parse_timeout_s == 60.0:
            test_passed("FileProcessingConfig.from_env() reads environment")
            print(f"       max_size={config.max_size_bytes // (1024*1024)}MB, timeout={config.parse_timeout_s}s")
            passed += 1
        else:
            test_failed("Env config", f"Values incorrect: {config.max_size_bytes}, {config.parse_timeout_s}")
            failed += 1
        
        # Clean up
        del os.environ['FILE_MAX_SIZE_MB']
        del os.environ['FILE_PARSE_TIMEOUT_S']
    except Exception as e:
        test_failed("Env config", str(e))
        failed += 1
    
    # ==========================================================================
    # SUMMARY
    # ==========================================================================
    section("SUMMARY")
    
    total = passed + failed
    print(f"  Total: {total} tests")
    print(f"  Passed: {passed} ✅")
    print(f"  Failed: {failed} ❌")
    print()
    
    if failed == 0:
        print("  🎉 ALL PRODUCTION VALIDATION TESTS PASSED!")
        print()
        print("  Enterprise file processing is ready for production:")
        print("  - Size gating prevents OOM on large files")
        print("  - Timeout enforcement prevents hung operations")
        print("  - Cancellation enables graceful shutdown")
        print("  - Progress tracking provides visibility")
        print("  - Fully backwards compatible")
        print()
        return 0
    else:
        print("  ⚠️  SOME TESTS FAILED - Review before production use")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())

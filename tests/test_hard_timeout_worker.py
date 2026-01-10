"""
Tests for process-based hard timeout worker.

These tests verify that:
1. Normal functions complete successfully
2. Slow functions are terminated after timeout
3. SIGTERM → SIGKILL escalation works
4. Exceptions in workers are propagated
5. Configuration from environment works
"""

import os
import time
from unittest.mock import patch

import pytest

from integration_coworker.sources.hard_timeout_worker import (
    HardTimeoutConfig,
    HardTimeoutError,
    WorkerError,
    run_with_hard_timeout,
    with_hard_timeout,
)


# =============================================================================
# Helper Functions (must be picklable - module level)
# =============================================================================


def simple_add(a: int, b: int) -> int:
    """Simple function that completes immediately."""
    return a + b


def sleep_and_return(seconds: float, value: str) -> str:
    """Sleep for a while then return value."""
    time.sleep(seconds)
    return value


def infinite_loop() -> None:
    """Loop forever (for testing hard kill)."""
    while True:
        time.sleep(0.01)


def raise_exception(msg: str) -> None:
    """Raise an exception with message."""
    raise ValueError(msg)


def cpu_intensive(iterations: int) -> int:
    """CPU-intensive work without sleeping."""
    result = 0
    for i in range(iterations):
        result += i * i
    return result


def add_with_kwargs(a: int, b: int = 10) -> int:
    """Add with default kwarg."""
    return a + b


def return_none() -> None:
    """Return None."""
    pass


def return_dict() -> dict:
    """Return a complex dict."""
    return {"key": "value", "nested": {"a": 1, "b": [1, 2, 3]}}


def return_large_list() -> list:
    """Return a large list."""
    return list(range(10000))


def parse_csv(content: str) -> list:
    """Parse CSV-like content."""
    lines = content.strip().split("\n")
    return [line.split(",") for line in lines]


def slow_parse(content: str) -> dict:
    """Simulate slow parsing."""
    time.sleep(5)
    return {"parsed": True}


def decorated_add_impl(a: int, b: int) -> int:
    """Add two numbers (for decorator test)."""
    return a + b


def slow_decorated_impl() -> str:
    """Sleep and return (for decorator timeout test)."""
    time.sleep(10)
    return "never"


# =============================================================================
# Test Configuration
# =============================================================================


class TestHardTimeoutConfig:
    """Tests for HardTimeoutConfig."""
    
    def test_default_values(self):
        """Should have sensible defaults."""
        config = HardTimeoutConfig()
        assert config.mode == "process"
        assert config.timeout_seconds == 30.0
        assert config.grace_seconds == 5.0
        assert config.use_spawn is True
    
    def test_is_enabled(self):
        """is_enabled should reflect mode."""
        assert HardTimeoutConfig(mode="process").is_enabled is True
        assert HardTimeoutConfig(mode="cooperative").is_enabled is False
        assert HardTimeoutConfig(mode="off").is_enabled is False
    
    def test_from_env_defaults(self):
        """from_env should use defaults when vars not set."""
        # Clear any existing env vars
        with patch.dict(os.environ, {}, clear=True):
            config = HardTimeoutConfig.from_env()
            assert config.mode == "process"
            assert config.timeout_seconds == 30.0
    
    def test_from_env_with_vars(self):
        """from_env should read environment variables."""
        env = {
            "FILE_HARD_TIMEOUT_MODE": "cooperative",
            "FILE_HARD_TIMEOUT_SECONDS": "60",
            "FILE_HARD_TIMEOUT_GRACE_SECONDS": "10",
            "FILE_HARD_TIMEOUT_USE_SPAWN": "false",
        }
        with patch.dict(os.environ, env):
            config = HardTimeoutConfig.from_env()
            assert config.mode == "cooperative"
            assert config.timeout_seconds == 60.0
            assert config.grace_seconds == 10.0
            assert config.use_spawn is False


# =============================================================================
# Test Normal Execution
# =============================================================================


class TestNormalExecution:
    """Tests for normal (non-timeout) execution."""
    
    def test_simple_function_returns_result(self):
        """Simple function should return result."""
        result = run_with_hard_timeout(
            simple_add,
            args=(2, 3),
            timeout_seconds=5.0,
        )
        assert result == 5
    
    def test_function_with_kwargs(self):
        """Should pass kwargs correctly."""
        result = run_with_hard_timeout(
            add_with_kwargs,
            args=(5,),
            kwargs={"b": 20},
            timeout_seconds=5.0,
        )
        assert result == 25
    
    def test_fast_sleep_completes(self):
        """Fast sleep should complete within timeout."""
        result = run_with_hard_timeout(
            sleep_and_return,
            args=(0.1, "done"),
            timeout_seconds=5.0,
        )
        assert result == "done"
    
    def test_cpu_intensive_completes(self):
        """CPU intensive work should complete if fast enough."""
        result = run_with_hard_timeout(
            cpu_intensive,
            args=(10000,),
            timeout_seconds=5.0,
        )
        assert result > 0


# =============================================================================
# Test Timeout Behavior
# =============================================================================


class TestTimeoutBehavior:
    """Tests for timeout termination."""
    
    def test_slow_function_times_out(self):
        """Slow function should be terminated."""
        with pytest.raises(HardTimeoutError) as exc_info:
            run_with_hard_timeout(
                sleep_and_return,
                args=(10.0, "never"),  # 10 second sleep
                timeout_seconds=0.5,  # 0.5 second timeout
                grace_seconds=0.2,
            )
        
        assert exc_info.value.timeout_seconds == 0.5
    
    def test_infinite_loop_killed(self):
        """Infinite loop should be terminated."""
        start = time.time()
        
        with pytest.raises(HardTimeoutError) as exc_info:
            run_with_hard_timeout(
                infinite_loop,
                timeout_seconds=0.5,
                grace_seconds=0.3,
            )
        
        elapsed = time.time() - start
        
        # Should have waited timeout + approximately grace period at most
        assert elapsed < 2.0  # Reasonable upper bound
        # Note: was_killed may be True or False depending on how fast
        # the process responds to SIGTERM
    
    def test_timeout_error_attributes(self):
        """HardTimeoutError should have correct attributes."""
        with pytest.raises(HardTimeoutError) as exc_info:
            run_with_hard_timeout(
                infinite_loop,
                timeout_seconds=0.3,
                grace_seconds=0.2,
            )
        
        error = exc_info.value
        assert error.timeout_seconds == 0.3
        assert error.grace_seconds == 0.2


# =============================================================================
# Test Error Handling
# =============================================================================


class TestErrorHandling:
    """Tests for exception handling."""
    
    def test_worker_exception_propagated(self):
        """Exception in worker should be wrapped and propagated."""
        with pytest.raises(WorkerError) as exc_info:
            run_with_hard_timeout(
                raise_exception,
                args=("test error message",),
                timeout_seconds=5.0,
            )
        
        error = exc_info.value
        assert "ValueError" in error.original_error
        assert "test error message" in error.original_error
    
    def test_worker_error_has_traceback(self):
        """WorkerError should include traceback information."""
        with pytest.raises(WorkerError) as exc_info:
            run_with_hard_timeout(
                raise_exception,
                args=("traceback test",),
                timeout_seconds=5.0,
            )
        
        assert exc_info.value.traceback_str  # Should have traceback


# =============================================================================
# Test Mode Switching
# =============================================================================


class TestModeSwitch:
    """Tests for mode configuration."""
    
    def test_mode_off_calls_directly(self):
        """Mode 'off' should call function directly (no subprocess)."""
        config = HardTimeoutConfig(mode="off")
        
        # This should not use subprocess
        result = run_with_hard_timeout(
            simple_add,
            args=(1, 2),
            config=config,
        )
        assert result == 3
    
    def test_mode_cooperative_calls_directly(self):
        """Mode 'cooperative' should call function directly."""
        config = HardTimeoutConfig(mode="cooperative")
        
        result = run_with_hard_timeout(
            simple_add,
            args=(10, 20),
            config=config,
        )
        assert result == 30
    
    def test_mode_process_uses_subprocess(self):
        """Mode 'process' should use subprocess (can timeout)."""
        config = HardTimeoutConfig(
            mode="process",
            timeout_seconds=0.5,
            grace_seconds=0.2,
        )
        
        with pytest.raises(HardTimeoutError):
            run_with_hard_timeout(
                sleep_and_return,
                args=(10.0, "never"),
                config=config,
            )


# =============================================================================
# Test Decorator
# =============================================================================


class TestDecorator:
    """Tests for @with_hard_timeout decorator."""
    
    def test_decorator_wraps_function(self):
        """Decorator should wrap function correctly."""
        # Use module-level function wrapped at test time
        decorated = with_hard_timeout(timeout_seconds=5.0)(decorated_add_impl)
        result = decorated(10, 20)
        assert result == 30
    
    def test_decorator_preserves_name(self):
        """Decorator should preserve function name."""
        decorated = with_hard_timeout(timeout_seconds=5.0)(decorated_add_impl)
        assert decorated.__name__ == "decorated_add_impl"
    
    def test_decorator_enforces_timeout(self):
        """Decorated function should timeout."""
        decorated = with_hard_timeout(timeout_seconds=0.5, grace_seconds=0.2)(slow_decorated_impl)
        
        with pytest.raises(HardTimeoutError):
            decorated()


# =============================================================================
# Test Edge Cases
# =============================================================================


class TestEdgeCases:
    """Tests for edge cases."""
    
    def test_zero_timeout_not_allowed(self):
        """Zero timeout should still work (instant timeout)."""
        # This is an edge case - function will likely timeout
        with pytest.raises(HardTimeoutError):
            run_with_hard_timeout(
                sleep_and_return,
                args=(0.5, "value"),
                timeout_seconds=0.01,  # Very short
                grace_seconds=0.01,
            )
    
    def test_function_returning_none(self):
        """Function returning None should work."""
        result = run_with_hard_timeout(
            return_none,
            timeout_seconds=5.0,
        )
        assert result is None
    
    def test_function_returning_complex_object(self):
        """Function returning complex object should work."""
        result = run_with_hard_timeout(
            return_dict,
            timeout_seconds=5.0,
        )
        assert result == {"key": "value", "nested": {"a": 1, "b": [1, 2, 3]}}
    
    def test_large_result_pickling(self):
        """Large result should be pickled correctly."""
        result = run_with_hard_timeout(
            return_large_list,
            timeout_seconds=5.0,
        )
        assert len(result) == 10000
        assert result[0] == 0
        assert result[-1] == 9999


# =============================================================================
# Test Integration with File Parsing
# =============================================================================


class TestFileParsingIntegration:
    """Tests simulating real file parsing scenarios."""
    
    def test_parse_csv_like_content(self):
        """Should handle CSV-like parsing."""
        csv_content = "a,b,c\n1,2,3\n4,5,6"
        
        result = run_with_hard_timeout(
            parse_csv,
            args=(csv_content,),
            timeout_seconds=5.0,
        )
        
        assert len(result) == 3
        assert result[0] == ["a", "b", "c"]
    
    def test_timeout_during_parsing(self):
        """Should timeout if parsing takes too long."""
        with pytest.raises(HardTimeoutError):
            run_with_hard_timeout(
                slow_parse,
                args=("content",),
                timeout_seconds=0.5,
                grace_seconds=0.2,
            )

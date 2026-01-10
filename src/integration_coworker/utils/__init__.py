# SPDX-License-Identifier: Apache-2.0
"""Utility modules for integration-coworker.

This package provides shared utilities:
- exceptions: Safe exception handling patterns for production use
- retry: Retry decorators and configuration
- trace_sanitizer: LangSmith payload sanitization and graceful degradation
"""

from integration_coworker.utils.exceptions import (
    ASYNC_FATAL_EXCEPTIONS,
    FATAL_EXCEPTIONS,
    FILE_EXCEPTIONS,
    PARSE_EXCEPTIONS,
    async_expect_exceptions,
    async_safe_cleanup,
    expect_exceptions,
    is_test_mode,
    reraise_async_fatal,
    reraise_fatal,
    require_test_mode,
    safe_cleanup,
)
from integration_coworker.utils.retry import RetryConfig, retry
from integration_coworker.utils.trace_sanitizer import (
    get_tracing_status,
    is_tracing_healthy,
    record_tracing_error,
    record_tracing_success,
    reset_tracing_state,
    sanitize_trace_data,
)

__all__ = [
    # Exceptions
    "FATAL_EXCEPTIONS",
    "ASYNC_FATAL_EXCEPTIONS",
    "FILE_EXCEPTIONS",
    "PARSE_EXCEPTIONS",
    "reraise_fatal",
    "reraise_async_fatal",
    "safe_cleanup",
    "async_safe_cleanup",
    "expect_exceptions",
    "async_expect_exceptions",
    "is_test_mode",
    "require_test_mode",
    # Retry
    "RetryConfig",
    "retry",
    # Trace sanitizer
    "sanitize_trace_data",
    "is_tracing_healthy",
    "record_tracing_error",
    "record_tracing_success",
    "reset_tracing_state",
    "get_tracing_status",
]

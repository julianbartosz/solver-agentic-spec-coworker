"""
Runtime policy library for generated API clients.

This module provides reusable middleware components for auth, retry, and rate limiting
that generated clients can import instead of embedding inline code.

V2 Implementation per Section 3.11 (ADR-0005).

V2.1: This module now re-exports from the standalone `integration_coworker_runtime`
package for backwards compatibility. New code should import directly from
`integration_coworker_runtime`.

Install the runtime package: pip install integration-coworker-runtime
"""
# Try to import from standalone package first
try:
    from integration_coworker_runtime import (
        IntegrationClient,
        IntegrationHttpClient,
        BaseAuth,
        NoAuth,
        BearerAuth,
        ApiKeyAuth,
        BasicAuth,
        BaseRetry,
        NoRetry,
        ExponentialRetry,
        BaseRateLimiter,
        NoRateLimiter,
        TokenBucketRateLimiter,
        SlidingWindowRateLimiter,
        IntegrationError,
        TransientIntegrationError,
        AuthIntegrationError,
    )
except ImportError:
    # Fall back to local implementations if standalone package not installed
    from integration_coworker.runtime.client import IntegrationClient
    from integration_coworker.runtime.http_client import IntegrationHttpClient
    from integration_coworker.runtime.auth import (
        BaseAuth,
        NoAuth,
        BearerAuth,
        ApiKeyAuth,
        BasicAuth,
    )
    from integration_coworker.runtime.retry import (
        BaseRetry,
        NoRetry,
        ExponentialRetry,
    )
    from integration_coworker.runtime.rate_limit import (
        BaseRateLimiter,
        NoRateLimiter,
        TokenBucketRateLimiter,
        SlidingWindowRateLimiter,
    )
    from integration_coworker.runtime.exceptions import (
        IntegrationError,
        TransientIntegrationError,
        AuthIntegrationError,
    )

__all__ = [
    # Client
    "IntegrationClient",
    "IntegrationHttpClient",
    # Auth
    "BaseAuth",
    "NoAuth",
    "BearerAuth",
    "ApiKeyAuth",
    "BasicAuth",
    # Retry
    "BaseRetry",
    "NoRetry",
    "ExponentialRetry",
    # Rate Limiting
    "BaseRateLimiter",
    "NoRateLimiter",
    "TokenBucketRateLimiter",
    "SlidingWindowRateLimiter",
    # Exceptions
    "IntegrationError",
    "TransientIntegrationError",
    "AuthIntegrationError",
    # Test Execution (M5+)
    "is_test_execution_enabled",
    "execute_tests",
    "generate_vscode_test_task",
    "TestExecutionResult",
    "TestStatus",
]

# Test execution (always local, not part of standalone runtime)
from integration_coworker.runtime.test_execution import (
    is_test_execution_enabled,
    execute_tests,
    generate_vscode_test_task,
    TestExecutionResult,
    TestStatus,
)

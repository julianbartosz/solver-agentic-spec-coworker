"""
Security module for integration-coworker.

Provides SSRF protection, URL validation, network security controls,
and secret redaction utilities.
"""

from .ssrf import (
    SSRFBlockedError,
    SSRFConfig,
    validate_url_target,
    is_ip_blocked,
    BLOCKED_NETWORKS,
    DNSResolver,
    default_dns_resolver,
)

from .redaction import (
    redact_secrets,
    redact_secrets_in_list,
    redact_dict_values,
    sanitize_for_logging,
    redact_and_sanitize,
    MAX_REDACTION_INPUT_LENGTH,
)

__all__ = [
    # SSRF protection
    "SSRFBlockedError",
    "SSRFConfig",
    "validate_url_target",
    "is_ip_blocked",
    "BLOCKED_NETWORKS",
    "DNSResolver",
    "default_dns_resolver",
    # Secret redaction
    "redact_secrets",
    "redact_secrets_in_list",
    "redact_dict_values",
    "sanitize_for_logging",
    "redact_and_sanitize",
    "MAX_REDACTION_INPUT_LENGTH",
]

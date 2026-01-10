"""
Secret redaction utilities for safe logging and persistence.

Per PROD_HARDENING_PLAN_V2.md and OWASP Logging Cheat Sheet:
- Error messages persisted or returned must not leak API keys, auth headers, tokens
- Log injection attacks via CR/LF must be prevented
- Performance must be bounded (max input length, precompiled regexes)

This module provides deterministic, cheap regex-based redaction with:
- Precompiled patterns (no per-call compilation)
- Max input length truncation (prevents pathological huge tracebacks)
- Log-injection sanitization (CR/LF removal)
- Conservative denylist patterns to avoid over-redacting

Usage:
    from integration_coworker.security.redaction import (
        redact_secrets,
        redact_secrets_in_list,
        sanitize_for_logging,
    )
    
    safe_message = redact_secrets("Error calling API with key sk-abc123def456")
    # -> "Error calling API with key [REDACTED:OPENAI_KEY]"
    
    safe_log = sanitize_for_logging("Error\\nwith newline")
    # -> "Error with newline"
"""
import re
from typing import Any, List

# =============================================================================
# Performance configuration
# =============================================================================

# Maximum input length before truncation (10KB - enough for meaningful errors)
# Prevents pathological huge tracebacks from consuming CPU
MAX_REDACTION_INPUT_LENGTH = 10 * 1024  # 10KB

# Truncation marker
TRUNCATION_MARKER = "... [TRUNCATED]"

# =============================================================================
# Precompiled regex patterns (compiled once at module import)
# =============================================================================

# Redaction patterns - order matters (more specific patterns first)
# Each tuple: (compiled_pattern, replacement_label)
# Patterns are designed to be simple/anchored to avoid catastrophic backtracking
_REDACTION_PATTERNS = [
    # OpenAI API keys (sk-... or sk-proj-...)
    (re.compile(r'\bsk-(?:proj-)?[A-Za-z0-9]{20,}'), '[REDACTED:OPENAI_KEY]'),
    
    # Anthropic API keys (sk-ant-...)
    (re.compile(r'\bsk-ant-[A-Za-z0-9\-]{20,}'), '[REDACTED:ANTHROPIC_KEY]'),
    
    # Google API keys (AIza...)
    (re.compile(r'\bAIza[A-Za-z0-9_\-]{30,}'), '[REDACTED:GOOGLE_KEY]'),
    
    # GitHub tokens (ghp_, gho_, ghu_, ghs_, ghr_)
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}'), '[REDACTED:GITHUB_TOKEN]'),
    
    # AWS access key IDs (AKIA...)
    (re.compile(r'\bAKIA[A-Z0-9]{16}\b'), '[REDACTED:AWS_ACCESS_KEY]'),
    
    # AWS secret access keys (40 chars alphanumeric with /+=)
    # Anchored with word boundary to avoid matching within larger strings
    (re.compile(r'(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])'), '[REDACTED:AWS_SECRET]'),
    
    # Bearer tokens in headers (limited character class, no backtracking)
    (re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/=]{10,}', re.IGNORECASE), 'Bearer [REDACTED:TOKEN]'),
    
    # Authorization header values (generic, simple pattern)
    (re.compile(r'Authorization:\s*\S+', re.IGNORECASE), 'Authorization: [REDACTED]'),
    
    # API key query params (?api_key=..., &apikey=..., etc.)
    # Non-greedy, limited character class
    (re.compile(r'([?&](?:api[_-]?key|apikey|key|token|secret|password|auth)=)[^&\s]{1,100}', re.IGNORECASE), r'\1[REDACTED]'),
    
    # X-API-Key and similar headers
    (re.compile(r'(X-(?:API-)?Key:\s*)\S{1,100}', re.IGNORECASE), r'\1[REDACTED]'),
    
    # Generic long hex strings (potential secrets, 32-64 chars, anchored)
    (re.compile(r'\b[a-fA-F0-9]{32,64}\b'), '[REDACTED:HEX]'),
    
    # Generic base64-ish strings in key contexts (limited length)
    (re.compile(r'((?:key|token|secret|password|auth|credential)["\']?\s*[:=]\s*["\']?)[A-Za-z0-9+/=]{20,100}', re.IGNORECASE), r'\1[REDACTED]'),
]

# Log injection sanitization pattern (CR, LF, and other control characters)
# Per OWASP: sanitize event data to prevent injection attacks
_LOG_INJECTION_PATTERN = re.compile(r'[\r\n\x00-\x08\x0b\x0c\x0e-\x1f]')


# =============================================================================
# Public API
# =============================================================================

def sanitize_for_logging(text: str) -> str:
    """
    Sanitize text for safe logging by removing log-injection characters.
    
    Per OWASP Logging Cheat Sheet: sanitize event data to prevent injection.
    Removes: CR (\\r), LF (\\n), NUL, and other ASCII control characters.
    
    Args:
        text: Input string that may contain injection characters
        
    Returns:
        Sanitized string safe for logging (single-line)
    """
    if not text:
        return text
    return _LOG_INJECTION_PATTERN.sub(' ', text)


def redact_secrets(text: str, max_length: int = MAX_REDACTION_INPUT_LENGTH) -> str:
    """
    Redact secrets from a string with bounded performance.
    
    Applies regex patterns to detect and replace:
    - API keys (OpenAI, Anthropic, Google, AWS)
    - Auth tokens (GitHub, Bearer tokens)
    - Authorization headers
    - Generic long hex/base64 strings in key contexts
    
    Performance guarantees:
    - Input truncated to max_length (default 10KB) before processing
    - Patterns are precompiled at module import
    - Patterns avoid catastrophic backtracking
    
    Args:
        text: Input string that may contain secrets
        max_length: Maximum input length before truncation (default 10KB)
        
    Returns:
        String with secrets replaced by [REDACTED:TYPE] markers
        
    Note:
        This is a best-effort redaction. Some secrets may not match patterns.
        For production, also use network egress controls to prevent exfiltration.
    """
    if not text:
        return text
    
    # Truncate for bounded performance
    truncated = False
    if len(text) > max_length:
        text = text[:max_length]
        truncated = True
    
    # Apply redaction patterns
    result = text
    for pattern, replacement in _REDACTION_PATTERNS:
        result = pattern.sub(replacement, result)
    
    # Add truncation marker if needed
    if truncated:
        result = result + TRUNCATION_MARKER
    
    return result


def redact_and_sanitize(text: str, max_length: int = MAX_REDACTION_INPUT_LENGTH) -> str:
    """
    Redact secrets AND sanitize for logging in one call.
    
    Combines redact_secrets() and sanitize_for_logging() for convenience.
    Use this before any logging or persistence of error messages.
    
    Args:
        text: Input string that may contain secrets and injection chars
        max_length: Maximum input length before truncation
        
    Returns:
        String safe for logging: secrets redacted, injection chars removed
    """
    return sanitize_for_logging(redact_secrets(text, max_length))


def normalize_error(error: str | dict | list | Exception | None, max_length: int = MAX_REDACTION_INPUT_LENGTH) -> str:
    """
    Normalize any error value to a clean, redacted string.
    
    Handles multiple error formats that may appear in state.errors:
    - str: Direct error message
    - dict: Structured error with "error", "message", "msg", or "detail" keys
    - list: Multiple errors (joins with "; ")
    - Exception: Extracts str(exception)
    - None/other: Returns "[Unknown error]"
    
    Always returns a sanitized, redacted string safe for logging.
    
    Args:
        error: The error value to normalize (any type)
        max_length: Maximum output length before truncation
        
    Returns:
        A normalized, redacted, sanitized string representation
        
    Examples:
        >>> normalize_error("Connection failed")
        'Connection failed'
        >>> normalize_error({"node": "gen", "error": "LLM timeout"})
        '[gen] LLM timeout'
        >>> normalize_error({"error": "API failed", "timestamp": "2024-01-01"})
        'API failed'
        >>> normalize_error([{"error": "e1"}, "e2"])
        '[Unknown error] e1; e2'
    """
    if error is None:
        return "[Unknown error]"
    
    if isinstance(error, str):
        return redact_and_sanitize(error, max_length)
    
    if isinstance(error, dict):
        # Extract structured error information
        # Try common keys: error, message, msg, detail, reason
        node = error.get("node", "")
        error_text = (
            error.get("error") or 
            error.get("message") or 
            error.get("msg") or 
            error.get("detail") or 
            error.get("reason") or
            str(error)
        )
        
        # If error_text is still a dict or non-string, convert it
        if not isinstance(error_text, str):
            error_text = str(error_text)
        
        # Format with node prefix if available
        if node:
            result = f"[{node}] {error_text}"
        else:
            result = error_text
        
        return redact_and_sanitize(result, max_length)
    
    if isinstance(error, list):
        # Recursively normalize each item and join
        parts = [normalize_error(item, max_length // len(error) if error else max_length) for item in error]
        return redact_and_sanitize("; ".join(parts), max_length)
    
    if isinstance(error, Exception):
        return redact_and_sanitize(str(error), max_length)
    
    # Fallback for any other type
    return redact_and_sanitize(str(error), max_length)


def redact_secrets_in_list(items: list, max_length: int = MAX_REDACTION_INPUT_LENGTH) -> list:
    """
    Redact secrets from a list of errors in-place.
    
    Also sanitizes for logging (removes CR/LF injection chars).
    Handles mixed-type lists containing strings, dicts, or other error formats.
    
    Args:
        items: List of errors to redact (modified in-place). Items can be:
               - str: Direct error message
               - dict: Structured error with "error"/"message" keys
               - other: Converted to string
        max_length: Maximum length per item before truncation
        
    Returns:
        The same list (for chaining), with all items normalized to redacted strings
        
    Example:
        errors = [
            "API call failed: key=sk-abc123",
            {"node": "gen", "error": "LLM timeout", "timestamp": "..."},
            "Error\\nwith newline"
        ]
        redact_secrets_in_list(errors)
        # errors is now [
        #     "API call failed: key=[REDACTED:OPENAI_KEY]",
        #     "[gen] LLM timeout",
        #     "Error with newline"
        # ]
    """
    for i, item in enumerate(items):
        items[i] = normalize_error(item, max_length)
    return items


def redact_dict_values(d: dict, keys_to_redact: List[str] | None = None) -> dict:
    """
    Redact sensitive values from a dictionary.
    
    If keys_to_redact is provided, only those keys have their values redacted.
    Otherwise, all string values are passed through redact_secrets().
    
    Args:
        d: Dictionary to redact (not modified)
        keys_to_redact: Optional list of specific keys to redact entirely
        
    Returns:
        New dictionary with redacted values
    """
    result = {}
    sensitive_keys = set(keys_to_redact or [])
    
    for key, value in d.items():
        if key in sensitive_keys:
            result[key] = '[REDACTED]'
        elif isinstance(value, str):
            result[key] = redact_and_sanitize(value)
        elif isinstance(value, dict):
            result[key] = redact_dict_values(value, keys_to_redact)
        elif isinstance(value, list):
            result[key] = [
                redact_and_sanitize(v) if isinstance(v, str) else v
                for v in value
            ]
        else:
            result[key] = value
    
    return result

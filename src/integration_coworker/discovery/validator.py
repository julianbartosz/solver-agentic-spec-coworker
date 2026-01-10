"""
Spec URL Validator for Discovery (Slice 1)

Validates discovered spec URLs by fetching and parsing them.
Uses openapi-spec-validator for compliance validation.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md Section 1B:
- Fetch URL with follow_redirects=True
- Detect YAML vs JSON
- Validate OpenAPI 3.x or Swagger 2.0 structure
- Run openapi-spec-validator for full compliance
- Fail closed: invalid spec cannot be used

Security Hardening (Production-Grade per OWASP SSRF Cheat Sheet):
- SSRF protection: block private IPs, localhost, link-local, metadata endpoints
- DNS resolution validation: resolve hostname and check resulting IP
- Post-redirect IP validation: re-check after all redirects
- Resource limits: max body size, tight timeouts, connection limits
- URL scheme validation: https only by default

References:
- https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html
"""

import asyncio
import ipaddress
import logging
import socket
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yaml

# NOTE: httpx is NOT imported here - all HTTP fetches go through hardened_fetch()
# to ensure SSRF protection is consistently applied

from integration_coworker.config import get_settings

logger = logging.getLogger(__name__)

# =============================================================================
# Security Constants
# =============================================================================

# Maximum spec size to fetch (prevent OOM)
MAX_SPEC_BYTES = 10 * 1024 * 1024  # 10MB (reduced from 50MB)

# Timeout for spec fetch
FETCH_TIMEOUT_SECONDS = 30.0  # Reduced from 60s

# Connection limits per client
MAX_CONNECTIONS = 10
MAX_KEEPALIVE_CONNECTIONS = 5

# Private/reserved IP ranges to block (SSRF protection)
BLOCKED_IP_RANGES = [
    ipaddress.ip_network("0.0.0.0/8"),       # "This" network
    ipaddress.ip_network("10.0.0.0/8"),      # Private-Use
    ipaddress.ip_network("127.0.0.0/8"),     # Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-Local
    ipaddress.ip_network("172.16.0.0/12"),   # Private-Use
    ipaddress.ip_network("192.168.0.0/16"),  # Private-Use
    ipaddress.ip_network("224.0.0.0/4"),     # Multicast
    ipaddress.ip_network("240.0.0.0/4"),     # Reserved
    ipaddress.ip_network("255.255.255.255/32"),  # Broadcast
    # IPv6 equivalents
    ipaddress.ip_network("::1/128"),         # Loopback
    ipaddress.ip_network("fc00::/7"),        # Unique-Local
    ipaddress.ip_network("fe80::/10"),       # Link-Local
    ipaddress.ip_network("ff00::/8"),        # Multicast
]

# Cloud metadata endpoints to block
BLOCKED_HOSTNAMES = frozenset({
    "169.254.169.254",  # AWS/GCP/Azure metadata
    "metadata.google.internal",
    "metadata.gke.internal",
    "100.100.100.200",  # Alibaba Cloud metadata
    "metadata.tencentyun.com",
    "localhost",
    "localhost.localdomain",
})

# Allowed URL schemes
ALLOWED_SCHEMES = frozenset({"https"})
# Set to True to allow http:// in development (NOT recommended for production)
ALLOW_HTTP_SCHEME = False


class SpecFormat(Enum):
    """Detected spec format."""
    OPENAPI_3_0 = "openapi_3.0"
    OPENAPI_3_1 = "openapi_3.1"
    SWAGGER_2 = "swagger_2"
    UNKNOWN = "unknown"


@dataclass
class ValidationResult:
    """
    Result of spec URL validation.
    
    Attributes:
        valid: Whether the spec is valid and usable
        spec_url: The URL that was validated
        spec_format: Detected format (OpenAPI 3.x, Swagger 2.0)
        title: API title from spec info
        version: API version from spec info
        error: Error message if invalid
        warnings: Non-fatal validation warnings
    """
    
    valid: bool
    spec_url: str
    spec_format: SpecFormat = SpecFormat.UNKNOWN
    title: str = ""
    version: str = ""
    error: Optional[str] = None
    warnings: list = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "valid": self.valid,
            "spec_url": self.spec_url,
            "spec_format": self.spec_format.value,
            "title": self.title,
            "version": self.version,
            "error": self.error,
            "warnings": self.warnings,
        }


# =============================================================================
# SSRF Protection
# =============================================================================

def _is_ip_blocked(ip_str: str) -> bool:
    """
    Check if an IP address is in a blocked range.
    
    Returns True if the IP should be blocked (private, loopback, etc.)
    """
    try:
        ip = ipaddress.ip_address(ip_str)
        for network in BLOCKED_IP_RANGES:
            if ip in network:
                return True
        return False
    except ValueError:
        # Invalid IP format - could be a hostname, allow it through
        return False


def _is_hostname_blocked(hostname: str) -> bool:
    """Check if hostname is in blocklist."""
    hostname_lower = hostname.lower()
    if hostname_lower in BLOCKED_HOSTNAMES:
        return True
    # Check for variants
    if hostname_lower.startswith("169.254."):
        return True
    return False


def _resolve_and_validate_hostname(hostname: str) -> Tuple[bool, Optional[str], List[str]]:
    """
    Resolve hostname to IP addresses and validate each one.
    
    This prevents DNS rebinding attacks by checking the actual resolved IP
    before any HTTP request is made.
    
    Per OWASP SSRF Prevention Cheat Sheet:
    "The application should resolve the hostname to an IP address and validate
    that the IP address is not blocked before making the request."
    
    Args:
        hostname: The hostname to resolve and validate
        
    Returns:
        Tuple of (is_safe, error_message, resolved_ips)
    """
    try:
        # Get all address info (IPv4 and IPv6)
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        
        resolved_ips = []
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            resolved_ips.append(ip_str)
            
            # Check if any resolved IP is blocked
            if _is_ip_blocked(ip_str):
                return False, f"Hostname '{hostname}' resolves to blocked IP: {ip_str}", resolved_ips
        
        if not resolved_ips:
            return False, f"Hostname '{hostname}' could not be resolved", []
        
        return True, None, resolved_ips
        
    except socket.gaierror as e:
        return False, f"DNS resolution failed for '{hostname}': {e}", []
    except Exception as e:
        return False, f"DNS resolution error for '{hostname}': {str(e)[:100]}", []


def validate_url_security(url: str, allow_http: bool = ALLOW_HTTP_SCHEME) -> Tuple[bool, Optional[str]]:
    """
    Validate URL for security concerns before fetching.
    
    Checks:
    - URL scheme (https required by default)
    - Hostname not in blocklist
    - Resolved IP not in private ranges (if hostname is IP)
    
    Args:
        url: The URL to validate
        allow_http: Whether to allow http:// scheme (default: False)
        
    Returns:
        Tuple of (is_safe, error_message)
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL format: {str(e)[:100]}"
    
    # Check scheme
    allowed = ALLOWED_SCHEMES | ({"http"} if allow_http else set())
    if parsed.scheme not in allowed:
        return False, f"URL scheme '{parsed.scheme}' not allowed. Use https://"
    
    # Get hostname
    hostname = parsed.hostname
    if not hostname:
        return False, "URL missing hostname"
    
    # Check hostname blocklist
    if _is_hostname_blocked(hostname):
        return False, f"Blocked hostname: {hostname}"
    
    # Check if hostname is an IP address (direct IP)
    if _is_ip_blocked(hostname):
        return False, f"Private/reserved IP address not allowed: {hostname}"
    
    return True, None


def validate_url_security_with_dns(
    url: str, 
    allow_http: bool = ALLOW_HTTP_SCHEME
) -> Tuple[bool, Optional[str], List[str]]:
    """
    Enhanced URL security validation with DNS resolution.
    
    This performs all checks from validate_url_security PLUS:
    - Resolves hostname to IP addresses
    - Validates that all resolved IPs are safe (not private/reserved)
    
    This prevents DNS rebinding attacks where a hostname initially
    resolves to a safe IP but later resolves to an internal IP.
    
    Args:
        url: The URL to validate
        allow_http: Whether to allow http:// scheme
        
    Returns:
        Tuple of (is_safe, error_message, resolved_ips)
    """
    # First do basic validation
    is_safe, error = validate_url_security(url, allow_http)
    if not is_safe:
        return False, error, []
    
    # Now resolve and validate the hostname
    parsed = urlparse(url)
    hostname = parsed.hostname
    
    # Skip DNS resolution for IP literals (already validated)
    try:
        ipaddress.ip_address(hostname)
        return True, None, [hostname]
    except ValueError:
        pass  # Not an IP literal, proceed with DNS resolution
    
    # Resolve hostname and validate resulting IPs
    return _resolve_and_validate_hostname(hostname)


# =============================================================================
# Content Parsing
# =============================================================================

def _detect_content_type(content: str, content_type: Optional[str]) -> str:
    """Detect if content is JSON or YAML."""
    # Check content-type header first
    if content_type:
        if "json" in content_type.lower():
            return "json"
        if "yaml" in content_type.lower() or "yml" in content_type.lower():
            return "yaml"
    
    # Heuristic: try JSON parse first
    stripped = content.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    
    return "yaml"


def _parse_spec_content(content: str, content_type: Optional[str]) -> Tuple[Dict[str, Any], str]:
    """
    Parse spec content as JSON or YAML.
    
    Returns:
        Tuple of (parsed_dict, format_string)
    """
    import json
    
    detected_type = _detect_content_type(content, content_type)
    
    if detected_type == "json":
        try:
            return json.loads(content), "json"
        except json.JSONDecodeError:
            # Try YAML as fallback
            return yaml.safe_load(content), "yaml"
    else:
        return yaml.safe_load(content), "yaml"


def _detect_spec_format(spec: Dict[str, Any]) -> SpecFormat:
    """Detect OpenAPI/Swagger format from parsed spec."""
    # OpenAPI 3.x
    openapi_version = spec.get("openapi", "")
    if openapi_version:
        if openapi_version.startswith("3.1"):
            return SpecFormat.OPENAPI_3_1
        elif openapi_version.startswith("3."):
            return SpecFormat.OPENAPI_3_0
    
    # Swagger 2.0
    swagger_version = spec.get("swagger", "")
    if swagger_version == "2.0":
        return SpecFormat.SWAGGER_2
    
    return SpecFormat.UNKNOWN


def _quick_structure_check(spec: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    """
    Quick structural validation before running full validator.
    
    Checks for required fields to fail fast on clearly invalid specs.
    """
    spec_format = _detect_spec_format(spec)
    
    if spec_format == SpecFormat.UNKNOWN:
        return False, "Missing 'openapi' or 'swagger' version field"
    
    # Check for info object
    if "info" not in spec:
        return False, "Missing required 'info' object"
    
    info = spec.get("info", {})
    if not isinstance(info, dict):
        return False, "'info' must be an object"
    
    if "title" not in info:
        return False, "Missing required 'info.title' field"
    
    # Check for paths (or webhooks in 3.1)
    if "paths" not in spec and "webhooks" not in spec:
        return False, "Missing required 'paths' object"
    
    paths = spec.get("paths", {})
    if paths and not isinstance(paths, dict):
        return False, "'paths' must be an object"
    
    return True, None


# =============================================================================
# OpenAPI Validation (Future-Proof Exception Handling)
# =============================================================================

# Track if we've already logged the missing validator warning (log once)
_VALIDATOR_MISSING_LOGGED = False


def _run_openapi_validator(spec: Dict[str, Any], spec_format: SpecFormat) -> Tuple[bool, Optional[str], list]:
    """
    Run openapi-spec-validator for full compliance validation.
    
    Exception handling is future-proofed to handle API changes:
    - Try importing the expected exception class
    - Fall back to catching any Exception from validate()
    - Treat unexpected exceptions as validation failures
    
    Returns:
        Tuple of (valid, error_message, warnings)
    """
    global _VALIDATOR_MISSING_LOGGED
    warnings = []
    
    # Try to import the validator
    try:
        from openapi_spec_validator import validate
    except ImportError:
        # openapi-spec-validator not installed - this is optional for discovery
        # Log once at DEBUG to avoid spam during normal operation
        if not _VALIDATOR_MISSING_LOGGED:
            logger.debug(
                "openapi-spec-validator not installed. "
                "Proceeding with structural checks only. "
                "For full compliance validation: pip install 'solver-agentic-spec-coworker[discovery]'"
            )
            _VALIDATOR_MISSING_LOGGED = True
        warnings.append("Full OpenAPI validation skipped (validator not installed)")
        return True, None, warnings
    
    # Try to import the exception class(es)
    # Future-proof: handle renamed/removed exception classes
    validation_error_classes = []
    
    for exc_name in ("OpenAPIValidationError", "OpenAPISpecValidatorError", "ValidationError"):
        try:
            exc_module = __import__(
                "openapi_spec_validator.exceptions",
                fromlist=[exc_name]
            )
            exc_class = getattr(exc_module, exc_name, None)
            if exc_class is not None:
                validation_error_classes.append(exc_class)
        except (ImportError, AttributeError):
            continue
    
    # If no specific exceptions found, we'll catch generic Exception
    if validation_error_classes:
        validation_errors = tuple(validation_error_classes)
    else:
        # Fallback: catch any exception from validate()
        logger.debug("No specific validation exception classes found, using generic Exception")
        validation_errors = (Exception,)
    
    try:
        # The validate function will raise on invalid specs
        # It auto-detects the version from the spec content
        validate(spec)
        return True, None, warnings
        
    except validation_errors as e:
        # Extract meaningful error message
        error_msg = str(e)
        if len(error_msg) > 500:
            error_msg = error_msg[:500] + "..."
        return False, f"OpenAPI validation failed: {error_msg}", warnings
        
    except Exception as e:
        # Unexpected exception - treat as validation failure but log
        logger.warning(f"Unexpected exception from openapi-spec-validator: {type(e).__name__}: {e}")
        return False, f"Validation error ({type(e).__name__}): {str(e)[:200]}", warnings


# =============================================================================
# Local File Reading
# =============================================================================

def _read_local_file(file_path: str, max_bytes: int = MAX_SPEC_BYTES) -> Tuple[bool, str, Optional[str]]:
    """
    Read a local file for validation.
    
    Args:
        file_path: Path to the local file
        max_bytes: Maximum file size to read
        
    Returns:
        Tuple of (success, content_or_error, content_type)
    """
    from pathlib import Path
    
    path = Path(file_path)
    
    if not path.exists():
        return False, f"File not found: {file_path}", None
    
    if not path.is_file():
        return False, f"Not a file: {file_path}", None
    
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return False, f"File too large: {size} bytes (max {max_bytes})", None
        
        content = path.read_text(encoding="utf-8")
        
        # Determine content type from extension
        suffix = path.suffix.lower()
        if suffix in (".json",):
            content_type = "application/json"
        elif suffix in (".yaml", ".yml"):
            content_type = "application/yaml"
        else:
            content_type = None
        
        return True, content, content_type
        
    except UnicodeDecodeError as e:
        return False, f"File encoding error: {str(e)[:100]}", None
    except PermissionError:
        return False, f"Permission denied: {file_path}", None
    except Exception as e:
        return False, f"Failed to read file: {str(e)[:200]}", None


# =============================================================================
# Main Validation Entry Point
# =============================================================================

async def validate_spec_url(
    spec_url: str,
    timeout_seconds: float = FETCH_TIMEOUT_SECONDS,
    max_bytes: int = MAX_SPEC_BYTES,
    allow_http: bool = ALLOW_HTTP_SCHEME,
    validate_dns: bool = True,
) -> ValidationResult:
    """
    Validate a spec URL by fetching and parsing it.
    
    Performs:
    1. For file:// URLs: Read from local filesystem
    2. For http(s):// URLs: Fetch via hardened_fetch (SSRF-safe)
    3. Content type detection (JSON/YAML)
    4. Parsing and structural validation
    5. Full OpenAPI compliance validation (if validator installed)
    
    Security is handled by hardened_fetch for remote URLs:
    - Resolves hostname to IP and validates before fetching
    - Re-validates IP after any redirects
    - Blocks private/reserved IP ranges and cloud metadata endpoints
    
    Args:
        spec_url: URL to the OpenAPI/Swagger spec (file:// or http(s)://)
        timeout_seconds: HTTP timeout (for remote URLs)
        max_bytes: Maximum response body size
        allow_http: Whether to allow http:// scheme
        validate_dns: Whether to resolve and validate DNS (default: True)
        
    Returns:
        ValidationResult with validation outcome
    """
    # Step 1: Handle file:// URLs specially (local filesystem)
    if spec_url.startswith("file://"):
        file_path = spec_url[7:]  # Strip "file://" prefix
        
        success, content_or_error, content_type = _read_local_file(file_path, max_bytes)
        
        if not success:
            return ValidationResult(
                valid=False,
                spec_url=spec_url,
                error=content_or_error,
            )
        
        content = content_or_error
    else:
        # Step 1b: Fetch remote spec using hardened_fetch (SSRF protection)
        from integration_coworker.discovery.http_client import hardened_fetch
        
        result = await hardened_fetch(
            spec_url,
            timeout_seconds=timeout_seconds,
            max_bytes=max_bytes,
            allow_http=allow_http,
            validate_dns=validate_dns,
        )
        
        if not result.success:
            return ValidationResult(
                valid=False,
                spec_url=spec_url,
                error=result.error,
            )
        
        content = result.content
        content_type = result.content_type
    
    # Step 2: Parse content
    try:
        spec, content_format = _parse_spec_content(content, content_type)
    except Exception as e:
        return ValidationResult(
            valid=False,
            spec_url=spec_url,
            error=f"Parse error: {str(e)[:200]}",
        )
    
    if not isinstance(spec, dict):
        return ValidationResult(
            valid=False,
            spec_url=spec_url,
            error="Parsed spec is not a dictionary",
        )
    
    # Step 3: Quick structure check
    structure_valid, structure_error = _quick_structure_check(spec)
    if not structure_valid:
        return ValidationResult(
            valid=False,
            spec_url=spec_url,
            error=structure_error,
        )
    
    # Step 4: Detect format
    spec_format = _detect_spec_format(spec)
    
    # Step 5: Full validation
    full_valid, full_error, warnings = _run_openapi_validator(spec, spec_format)
    
    # Extract metadata
    info = spec.get("info", {})
    title = info.get("title", "")
    version = info.get("version", "")
    
    if not full_valid:
        return ValidationResult(
            valid=False,
            spec_url=spec_url,
            spec_format=spec_format,
            title=title,
            version=version,
            error=full_error,
            warnings=warnings,
        )
    
    logger.info(f"Validated spec: {title} v{version} ({spec_format.value})")
    
    return ValidationResult(
        valid=True,
        spec_url=spec_url,
        spec_format=spec_format,
        title=title,
        version=version,
        warnings=warnings,
    )


def validate_spec_url_sync(
    spec_url: str,
    timeout_seconds: float = FETCH_TIMEOUT_SECONDS,
    max_bytes: int = MAX_SPEC_BYTES,
    allow_http: bool = ALLOW_HTTP_SCHEME,
) -> ValidationResult:
    """
    Synchronous wrapper for validate_spec_url.
    
    For use in sync contexts (e.g., tests, CLI).
    """
    return asyncio.run(validate_spec_url(spec_url, timeout_seconds, max_bytes, allow_http))

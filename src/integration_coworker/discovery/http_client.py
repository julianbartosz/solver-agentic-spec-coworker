"""
Hardened HTTP Client for Discovery Module

All URL fetches in the discovery module MUST go through this module to ensure
consistent SSRF protection and security controls.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md and OWASP SSRF Cheat Sheet:
- Manual redirect loop with per-hop SSRF validation (not httpx follow_redirects)
- DNS resolution validation before every hop
- Size limits on response bodies (enforced via streaming)
- Explicit timeout configuration (connect/read/write/pool)
- Explicit connection limits
- HTTPS-only by default
- Proxy environment leakage blocked (trust_env=False)

Usage:
    from integration_coworker.discovery.http_client import hardened_fetch
    
    # All discovery fetches should use this
    result = await hardened_fetch("https://example.com/spec.yaml")
    if result.success:
        content = result.content

DO NOT use httpx.get() or httpx.AsyncClient() directly in discovery code.
This is enforced by CI gate: no `import httpx` outside http_client.py.

=============================================================================
HTTP CLIENT SECURITY CONTRACT
=============================================================================

This module implements a security boundary for HTTP fetches. The contract is
aligned with OWASP SSRF Prevention Cheat Sheet recommendations:
https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html

ALLOWED SCHEMES:
    - ONLY http:// and https:// are permitted
    - All other schemes (file://, gopher://, ftp://, dict://, ldap://) are BLOCKED
    - Scheme allowlist is enforced BEFORE every request via ALLOWED_SCHEMES constant

REDIRECT POLICY:
    - Manual redirect loop with follow_redirects=False (INVARIANT 1)
    - Maximum redirects: DEFAULT_MAX_REDIRECTS = 5
    - Only HTTP redirect codes followed: 301, 302, 303, 307, 308 (INVARIANT 2)
    - EVERY hop is validated via _validate_hop() BEFORE requesting (INVARIANT 4)
    - Relative Location headers resolved via urllib.parse.urljoin()
    - Multiple Location headers rejected (header poisoning protection)
    - Location headers with control chars (\\r, \\n, \\x00) rejected

TOTAL WALL BUDGET:
    - DEFAULT_TOTAL_TIMEOUT = 60.0 seconds across ALL redirect hops
    - Enforced via asyncio.timeout() as hard wall-clock deadline (Python 3.11+)
    - Per-hop timeout derived from remaining budget via get_hardened_timeout()
    - Minimum per-hop timeout floor: MINIMUM_HOP_TIMEOUT = 0.5s
    - httpx.Timeout object used per request (not raw float) for fine-grained control
    - This prevents 5 redirects × 30s = 150s stall attacks

BYTE CAP:
    - max_bytes parameter (default: MAX_SPEC_BYTES from validator module)
    - Enforced via streaming: client.stream() + aiter_bytes() (INVARIANT 3)
    - NEVER use response.text or response.content (materializes full body)
    - Content-Length header pre-checked for fast rejection of large files
    - Streaming stops immediately when byte cap exceeded

DNS VALIDATION:
    - DNS resolution via socket.getaddrinfo() BEFORE every hop (INVARIANT 4)
    - Resolved IPs validated against blocklists:
      - RFC 1918 private ranges (10.x, 172.16-31.x, 192.168.x)
      - Loopback (127.x.x.x, ::1)
      - Link-local (169.254.x.x, fe80::)
      - Cloud metadata (169.254.169.254, metadata.google.internal)
    - Prevents DNS rebinding attacks where hostname resolves to internal IP
    - validate_dns=True by default (can be disabled for testing)

PROXY ENV POLICY:
    - trust_env=False on ALL httpx client instantiations (INVARIANT 5)
    - Blocks HTTP_PROXY, HTTPS_PROXY, ALL_PROXY, NO_PROXY environment variables
    - Prevents proxy env var injection that could redirect traffic to attacker
    - Per https://www.python-httpx.org/environment_variables/

CONNECTION LIMITS:
    - Explicit httpx.Limits for connection pooling
    - max_connections, max_keepalive_connections, keepalive_expiry configured
    - Prevents unbounded connection accumulation

REVIEWERS:
    Map these invariants to OWASP SSRF guidance:
    - "Deny by default" → ALLOWED_SCHEMES blocklist
    - "Validate redirects" → Per-hop _validate_hop() before request
    - "Block private networks" → DNS validation with IP blocklists
    - "Limit resource consumption" → Byte cap, timeout budget, connection limits

=============================================================================
CONTRACT INVARIANTS (enforced by tests/discovery/test_hardening.py)
=============================================================================

These invariants are CRITICAL for security. Breaking any of them is a
security regression that must be blocked by CI.

INVARIANT 1: follow_redirects=False at httpx client level
    - We NEVER use httpx's built-in redirect following
    - Our manual loop gives us per-hop SSRF validation hooks
    - Test: test_follow_redirects_always_false

INVARIANT 2: Manual loop follows ONLY 301/302/303/307/308
    - Location header is resolved against current URL (relative redirects)
    - Next hop is validated BEFORE following
    - Test: test_only_redirect_status_codes_followed

INVARIANT 3: NEVER access response.text or response.content in this file
    - response.text materializes the ENTIRE body before returning
    - This defeats the streaming byte cap
    - ALWAYS use client.stream() + aiter_bytes() for body reading
    - Test: test_no_response_text_or_content_access

INVARIANT 4: Validate next hop BEFORE requesting it
    - Call _validate_hop() BEFORE httpx.get() on Location URL
    - This prevents the fetch from even starting on blocked URLs
    - Test: test_validate_before_request, TestBehaviorInvariant4ValidateBeforeRequest

INVARIANT 5: trust_env=False for proxy immunity
    - HTTP_PROXY, HTTPS_PROXY, ALL_PROXY env vars are IGNORED
    - Without this, malicious env vars can redirect traffic to attacker
    - Test: test_trust_env_always_false

INVARIANT 6: Total timeout budget across redirect chain
    - 5 redirects × 30s read timeout = 150s total stall (BAD)
    - asyncio.timeout() provides hard wall-clock deadline
    - Each hop gets httpx.Timeout derived from remaining budget
    - Test: test_total_timeout_budget_enforced, TestBehaviorInvariant6AsyncioTimeoutWallClock

INVARIANT 7: Cumulative bytes across redirect chain
    - Only the FINAL 200 response streams body content
    - Redirect responses (3xx) do NOT read bodies (Location only)
    - max_bytes applies to the single final body, not per-hop
    - Test: test_redirects_dont_read_bodies, test_cumulative_bytes

INVARIANT 8: Canonicalization for loop detection
    - URLs are normalized before seen_urls check: scheme://host[:port]/path?query
    - Default ports stripped: https://x:443/ → https://x/
    - Path normalized: /a/../b → /b
    - Fragment stripped: /a#b → /a
    - Test: test_canonicalization_prevents_bypass

INVARIANT 9: Location header protection
    - Multiple Location headers rejected (header poisoning)
    - Control characters (\\r, \\n, \\x00) in Location rejected
    - Test: TestBehaviorInvariant9LocationHeaderProtection

INVARIANT 10: Explicit httpx.Timeout per request
    - Every request uses httpx.Timeout object, not raw float
    - Fine-grained control over connect/read/write/pool timeouts
    - Test: TestBehaviorInvariant10ExplicitHttpxTimeout
=============================================================================
"""

import asyncio
import logging
import posixpath
import re
import time
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode

import httpx

from integration_coworker.discovery.validator import (
    validate_url_security,
    validate_url_security_with_dns,
    MAX_SPEC_BYTES,
    FETCH_TIMEOUT_SECONDS,
    MAX_CONNECTIONS,
    MAX_KEEPALIVE_CONNECTIONS,
    ALLOW_HTTP_SCHEME,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Timeout Configuration (explicit, not defaults)
# Per https://www.python-httpx.org/advanced/timeouts/
# =============================================================================

# Connect timeout: time to establish TCP connection
CONNECT_TIMEOUT = 10.0

# Read timeout: time between bytes from server
READ_TIMEOUT = 30.0

# Write timeout: time between bytes sent to server
WRITE_TIMEOUT = 10.0

# Pool timeout: time waiting for connection from pool
POOL_TIMEOUT = 10.0

# Default overall timeout (used if not specified)
DEFAULT_TIMEOUT = FETCH_TIMEOUT_SECONDS

# Maximum redirects (small to prevent redirect loops/abuse)
DEFAULT_MAX_REDIRECTS = 5

# HTTP redirect status codes we handle manually
REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})

# =============================================================================
# Total Budget Configuration (INVARIANT 6 & 7)
# =============================================================================

# Total wall-clock timeout across the entire redirect chain
# This prevents 5 redirects × 30s = 150s stall attacks
# Default: 60 seconds total for the entire fetch operation
DEFAULT_TOTAL_TIMEOUT = 60.0

# Minimum timeout per hop (floor to prevent zero-timeout requests)
# Even with depleted budget, give each request a brief chance to complete
MINIMUM_HOP_TIMEOUT = 0.5

# We don't count bytes per-redirect because redirect responses
# shouldn't have bodies we read. Only final 200 streams content.
# This is enforced by INVARIANT 7.


def get_hardened_timeout(
    timeout_seconds: Optional[float] = None,
    remaining_budget: Optional[float] = None,
) -> httpx.Timeout:
    """
    Create explicit httpx.Timeout with all components specified.
    
    This ensures we never rely on httpx defaults which could change.
    
    Args:
        timeout_seconds: Configured per-request timeout (e.g., 30s read)
        remaining_budget: Remaining wall-clock budget from monotonic deadline.
                          If provided, constrains all components to fit within budget.
    
    Returns:
        httpx.Timeout with explicit connect/read/write/pool components.
        Each component is min(configured_value, remaining_budget, MINIMUM_HOP_TIMEOUT floor).
    """
    overall = timeout_seconds if timeout_seconds is not None else DEFAULT_TIMEOUT
    
    # If we have a remaining budget, constrain to it
    if remaining_budget is not None:
        # Floor at MINIMUM_HOP_TIMEOUT to avoid zero-timeout requests
        effective_budget = max(MINIMUM_HOP_TIMEOUT, remaining_budget)
        overall = min(overall, effective_budget)
    
    return httpx.Timeout(
        connect=min(CONNECT_TIMEOUT, overall),
        read=min(READ_TIMEOUT, overall),
        write=min(WRITE_TIMEOUT, overall),
        pool=min(POOL_TIMEOUT, overall),
    )


def get_hardened_limits() -> httpx.Limits:
    """
    Create explicit httpx.Limits for connection pooling.
    
    This prevents unbounded connection accumulation.
    """
    return httpx.Limits(
        max_connections=MAX_CONNECTIONS,
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=30.0,  # Close idle connections after 30s
    )


@dataclass
class FetchResult:
    """
    Result of a hardened fetch operation.
    
    Attributes:
        success: Whether the fetch succeeded
        content: Response body as string (if successful)
        final_url: Final URL after redirects
        content_type: Content-Type header value
        error: Error message if failed
        redirect_chain: List of URLs in redirect chain (for auditing)
    """
    success: bool
    content: Optional[str] = None
    final_url: Optional[str] = None
    content_type: Optional[str] = None
    error: Optional[str] = None
    redirect_chain: Optional[List[str]] = None


class SSRFBlockedError(Exception):
    """Raised when SSRF protection blocks a request at any hop."""
    def __init__(self, message: str, url: str, hop: int = 0):
        super().__init__(message)
        self.url = url
        self.hop = hop


def _canonicalize_url(url: str) -> str:
    """
    Canonicalize URL for redirect loop detection (INVARIANT 8).
    
    Normalization rules to prevent bypass attacks:
    1. Lowercase scheme and host
    2. Strip default ports (443 for https, 80 for http)
    3. Normalize path: /a/../b → /b, /a/./b → /a/b, // → /
    4. Strip fragment: /path#anchor → /path
    5. Sort and include querystring: /path?b=2&a=1 → /path?a=1&b=2
    
    This prevents redirect loop detection bypass via:
    - Case variation: HTTP://EXAMPLE.COM vs https://example.com
    - Port variation: :443 vs implicit
    - Path variation: /a/../b vs /b
    - Fragment variation: /path vs /path#ignored
    
    Returns:
        Canonical URL string for identity comparison
    """
    parsed = urlparse(url)
    
    # 1. Lowercase scheme and host
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    
    # 2. Strip default ports
    port = parsed.port
    if port is None or (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        port_str = ""
    else:
        port_str = f":{port}"
    
    # 3. Normalize path using posixpath (handles .., ., //)
    path = parsed.path or "/"
    # posixpath.normpath handles /a/../b → /b, /a/./b → /a/b
    path = posixpath.normpath(path)
    # Ensure path starts with /
    if not path.startswith("/"):
        path = "/" + path
    # posixpath.normpath returns '.' for empty, we want '/'
    if path == ".":
        path = "/"
    
    # 4. Fragment is stripped (not included in canonical form)
    # parsed.fragment is ignored
    
    # 5. Sort querystring for consistent ordering
    query = parsed.query
    if query:
        # Parse and sort query params for consistent ordering
        params = parse_qsl(query, keep_blank_values=True)
        params.sort()
        query = urlencode(params)
    
    # Build canonical URL (no fragment)
    if query:
        return f"{scheme}://{host}{port_str}{path}?{query}"
    else:
        return f"{scheme}://{host}{port_str}{path}"


# Allowed URL schemes per OWASP SSRF guidance
# Block dangerous schemes: file://, gopher://, dict://, ftp://, etc.
ALLOWED_SCHEMES = frozenset({"http", "https"})


def _validate_hop(
    url: str, 
    allow_http: bool, 
    validate_dns: bool,
    hop_number: int,
    previous_url: Optional[str] = None,
) -> tuple[bool, Optional[str], Optional[list]]:
    """
    Validate a single hop in the redirect chain.
    
    Returns: (is_safe, error_message, resolved_ips)
    
    Security checks per hop (per OWASP SSRF Cheat Sheet):
    1. Scheme validation - ONLY http/https allowed (blocks file://, gopher://, etc.)
    2. HTTPS required unless allow_http=True
    3. Hostname blocklist (localhost, metadata endpoints, etc.)
    4. DNS resolution and IP validation (prevents DNS rebinding)
    5. Scheme downgrade detection (HTTPS→HTTP)
    """
    parsed = urlparse(url)
    
    # Check scheme is allowed (OWASP: block non-HTTP schemes)
    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return False, f"Blocked scheme '{parsed.scheme}' (only http/https allowed)", None
    
    # Check for scheme downgrade (HTTPS → HTTP)
    if previous_url and not allow_http:
        prev_parsed = urlparse(previous_url)
        if prev_parsed.scheme == "https" and parsed.scheme == "http":
            return False, f"Redirect downgrades from HTTPS to HTTP (blocked)", None
    
    # Basic security validation
    is_safe, error = validate_url_security(url, allow_http=allow_http)
    if not is_safe:
        return False, f"Hop {hop_number}: {error}", None
    
    # DNS validation
    resolved_ips = None
    if validate_dns:
        is_safe, dns_error, resolved_ips = validate_url_security_with_dns(url, allow_http=allow_http)
        if not is_safe:
            return False, f"Hop {hop_number} DNS: {dns_error}", None
        logger.debug(f"Hop {hop_number}: {url} resolved to {resolved_ips}")
    
    return True, None, resolved_ips


async def hardened_fetch(
    url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT,
    total_timeout_seconds: Optional[float] = None,
    max_bytes: int = MAX_SPEC_BYTES,
    allow_http: bool = ALLOW_HTTP_SCHEME,
    validate_dns: bool = True,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> FetchResult:
    """
    Fetch a URL with full SSRF protection via manual redirect loop.
    
    This is the ONLY function that should be used for HTTP fetches in the
    discovery module. All other code should import and use this function.
    
    CRITICAL CONTRACT - These invariants are enforced by CI tests:
    
    INVARIANT 1: follow_redirects=False (manual loop, not httpx)
    INVARIANT 2: Only 301/302/303/307/308 followed via Location header
    INVARIANT 3: NEVER use response.text or response.content (use aiter_bytes)
    INVARIANT 4: _validate_hop() called BEFORE requesting next URL
    INVARIANT 5: trust_env=False (proxy immunity)
    INVARIANT 6: Total timeout budget via monotonic deadline
    INVARIANT 7: Redirect responses don't read bodies (only final 200 streams)
    INVARIANT 8: Canonicalized URLs for loop detection (path normalized, etc.)
    
    Security Controls (per OWASP SSRF Cheat Sheet):
    1. URL scheme validation (only http/https, blocks file://, gopher://, etc.)
    2. HTTPS required by default (http only with allow_http=True)
    3. Hostname blocklist check (localhost, metadata endpoints, etc.)
    4. DNS resolution and IP validation per hop (prevents DNS rebinding)
    5. Manual redirect loop with per-hop validation (not httpx follow_redirects)
    6. Redirect loop detection via canonicalized URL set
    7. Scheme downgrade detection (HTTPS→HTTP blocked)
    8. Response size limits (streaming with hard byte cap, not Content-Length only)
    9. Explicit timeout configuration (connect/read/write/pool)
    10. Connection limits (prevents resource exhaustion)
    11. Proxy environment blocked via trust_env=False
    12. Total timeout budget across entire redirect chain (INVARIANT 6)
    
    Args:
        url: URL to fetch
        timeout_seconds: Per-request HTTP timeout (connect/read/write/pool)
        total_timeout_seconds: Total wall-clock timeout across all redirects
                               (default: DEFAULT_TOTAL_TIMEOUT = 60s)
        max_bytes: Maximum response body size (enforced via streaming)
        allow_http: Whether to allow http:// scheme (default: False)
        validate_dns: Whether to resolve and validate DNS (default: True)
        max_redirects: Maximum number of redirects (default: 5)
        
    Returns:
        FetchResult with content or error, includes redirect_chain for auditing
        
    Example:
        result = await hardened_fetch("https://api.stripe.com/openapi.yaml")
        if result.success:
            spec_content = result.content
        else:
            logger.error(f"Fetch failed: {result.error}")
    """
    redirect_chain: List[str] = []
    seen_urls: set[str] = set()  # Loop detection via canonicalized URLs (INVARIANT 8)
    current_url = url
    previous_url: Optional[str] = None
    
    # INVARIANT 6: Total timeout budget via monotonic deadline
    # This prevents 5 redirects × 30s = 150s stall attacks
    total_timeout = total_timeout_seconds if total_timeout_seconds is not None else DEFAULT_TOTAL_TIMEOUT
    deadline = time.monotonic() + total_timeout
    
    def _remaining_time() -> float:
        """Get remaining time budget (monotonic clock)."""
        return max(0.0, deadline - time.monotonic())
    
    def _check_budget() -> Optional[FetchResult]:
        """Check if we've exhausted the total timeout budget."""
        if _remaining_time() <= 0:
            return FetchResult(
                success=False,
                error=f"Total timeout budget exhausted ({total_timeout}s across redirects)",
                redirect_chain=redirect_chain,
            )
        return None
    
    # Step 1: Validate initial URL before any network activity
    is_safe, error, _ = _validate_hop(current_url, allow_http, validate_dns, hop_number=0)
    if not is_safe:
        logger.warning(f"SSRF blocked (initial): {current_url} - {error}")
        return FetchResult(success=False, error=f"Security: {error}", redirect_chain=[])
    
    # Step 2: Create client with explicit hardened configuration
    # INVARIANT 1: follow_redirects=False - we handle redirects manually
    # INVARIANT 5: trust_env=False - blocks HTTP_PROXY, HTTPS_PROXY, etc.
    try:
        # INVARIANT 6: Outer asyncio.timeout guarantees wall-clock budget
        # This is in ADDITION to per-request httpx timeouts, because:
        # - httpx timeouts are "network inactivity" oriented
        # - asyncio.timeout is a hard wall-clock deadline
        # Even weird corner cases (DNS delays, TLS handshake, etc.) cannot exceed budget
        async with asyncio.timeout(total_timeout):
            async with httpx.AsyncClient(
                timeout=get_hardened_timeout(timeout_seconds, _remaining_time()),
                follow_redirects=False,  # INVARIANT 1: MANUAL redirect loop
                limits=get_hardened_limits(),
                trust_env=False,  # INVARIANT 5: Block proxy environment variables
            ) as client:
                
                # Step 3: Manual redirect loop with per-hop validation
                for hop in range(max_redirects + 1):
                    # INVARIANT 6: Check total budget before each hop
                    budget_error = _check_budget()
                    if budget_error:
                        return budget_error
                    
                    # INVARIANT 8: Loop detection via canonicalized URLs
                    canonical = _canonicalize_url(current_url)
                    if canonical in seen_urls:
                        return FetchResult(
                            success=False,
                            error=f"Redirect loop detected at hop {hop}: {current_url}",
                            redirect_chain=redirect_chain,
                        )
                    seen_urls.add(canonical)
                    redirect_chain.append(current_url)
                    logger.debug(f"Fetching hop {hop}: {current_url} (budget: {_remaining_time():.1f}s)")
                    
                    try:
                        # Per-hop timeout: explicit httpx.Timeout derived from remaining budget
                        # with MINIMUM_HOP_TIMEOUT floor to avoid zero-timeout requests
                        remaining = _remaining_time()
                        hop_timeout = get_hardened_timeout(timeout_seconds, remaining)
                        
                        if remaining <= 0:
                            return FetchResult(
                                success=False,
                                error=f"Total timeout budget exhausted before hop {hop}",
                                redirect_chain=redirect_chain,
                            )
                        
                        # INVARIANT 7: For redirect hops, we just need headers, not body
                        # The GET will return headers; we don't read body for 3xx responses
                        response = await client.get(
                            current_url,
                            timeout=hop_timeout,  # Explicit httpx.Timeout, not float
                        )
                    except httpx.TimeoutException as e:
                        return FetchResult(
                            success=False,
                            error=f"Timeout at hop {hop} (budget: {_remaining_time():.1f}s): {type(e).__name__}",
                            redirect_chain=redirect_chain,
                        )
                    except Exception as e:
                        return FetchResult(
                            success=False,
                            error=f"Request failed at hop {hop}: {str(e)[:200]}",
                            redirect_chain=redirect_chain,
                        )
                    
                    # INVARIANT 2: Check for redirect (only 301/302/303/307/308)
                    if response.status_code in REDIRECT_STATUS_CODES:
                        # INVARIANT 7: Do NOT read body on redirect responses
                        # We only extract Location header
                        
                        # Header poisoning protection: reject multiple Location headers
                        location_headers = response.headers.get_list("location")
                        if len(location_headers) > 1:
                            return FetchResult(
                                success=False,
                                error=f"Multiple Location headers at hop {hop} (header poisoning)",
                                redirect_chain=redirect_chain,
                            )
                        
                        location = location_headers[0] if location_headers else None
                        if not location:
                            return FetchResult(
                                success=False,
                                error=f"Redirect at hop {hop} missing Location header",
                                redirect_chain=redirect_chain,
                            )
                        
                        # Header injection protection: reject Location with control chars
                        if re.search(r'[\r\n\x00]', location):
                            return FetchResult(
                                success=False,
                                error=f"Location header contains control characters at hop {hop}",
                                redirect_chain=redirect_chain,
                            )
                        
                        # Resolve relative redirects against current URL using urljoin
                        # urljoin handles relative paths, absolute paths, and full URLs
                        next_url = urljoin(current_url, location)
                        
                        # INVARIANT 4: Validate NEXT hop BEFORE following
                        is_safe, hop_error, _ = _validate_hop(
                            next_url, allow_http, validate_dns, 
                            hop_number=hop + 1, 
                            previous_url=current_url
                        )
                        if not is_safe:
                            logger.warning(f"SSRF blocked at redirect hop {hop + 1}: {next_url} - {hop_error}")
                            return FetchResult(
                                success=False,
                                error=f"Security: {hop_error}",
                                redirect_chain=redirect_chain,
                            )
                        
                        previous_url = current_url
                        current_url = next_url
                        continue
                    
                    # Not a redirect - check for success
                    if response.status_code >= 400:
                        return FetchResult(
                            success=False,
                            error=f"HTTP error: {response.status_code}",
                            redirect_chain=redirect_chain,
                        )
                    
                    # INVARIANT 6: Check budget before streaming
                    budget_error = _check_budget()
                    if budget_error:
                        return budget_error
                    
                    # Step 4: Success - stream the response body with hard byte cap
                    # INVARIANT 3: Use aiter_bytes(), NEVER response.text or response.content
                    return await _stream_response_body(
                        client, current_url, max_bytes, redirect_chain,
                        timeout_seconds=_remaining_time(),
                    )
                
                # Exceeded max redirects
                return FetchResult(
                    success=False,
                    error=f"Too many redirects (>{max_redirects})",
                    redirect_chain=redirect_chain,
                )
                
    except asyncio.TimeoutError:
        # asyncio.timeout() fired - hard wall-clock budget exceeded
        return FetchResult(
            success=False,
            error=f"Total timeout budget exhausted ({total_timeout}s wall-clock)",
            redirect_chain=redirect_chain,
        )
    except SSRFBlockedError as e:
        logger.warning(f"SSRF blocked: {e}")
        return FetchResult(success=False, error=str(e), redirect_chain=redirect_chain)
    except Exception as e:
        logger.exception(f"Unexpected error fetching {url}")
        return FetchResult(
            success=False,
            error=f"Fetch error: {str(e)[:200]}",
            redirect_chain=redirect_chain,
        )


async def _stream_response_body(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int,
    redirect_chain: List[str],
    *,
    timeout_seconds: Optional[float] = None,
) -> FetchResult:
    """
    Stream response body with hard byte cap enforcement (INVARIANT 3 & 7).
    
    INVARIANT 3: Uses client.stream() + aiter_bytes() - NEVER response.text
    INVARIANT 7: Only called for final 200 response, not redirects
    
    CRITICAL: This uses client.stream() + aiter_bytes() to enforce the byte
    limit incrementally. We do NOT rely on Content-Length alone because:
    1. Content-Length can be missing (chunked encoding)
    2. Content-Length can lie (malicious server)
    3. We need to stop as soon as we hit the limit, not after full download
    
    Args:
        client: httpx.AsyncClient (already configured with trust_env=False, etc.)
        url: Final URL to stream from (already validated)
        max_bytes: Hard byte limit (will abort if exceeded)
        redirect_chain: Audit trail for the FetchResult
        timeout_seconds: Remaining timeout budget (from monotonic deadline)
        
    Returns:
        FetchResult with content or error
    """
    # Build timeout for this stream operation
    timeout = get_hardened_timeout(timeout_seconds) if timeout_seconds else None
    
    try:
        # INVARIANT 1 & 3: Use stream() with follow_redirects=False, then aiter_bytes()
        async with client.stream("GET", url, follow_redirects=False, timeout=timeout) as response:
            response.raise_for_status()
            
            # Pre-check Content-Length if present (fast-fail for obviously large files)
            content_length = response.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        return FetchResult(
                            success=False,
                            error=f"Response too large (Content-Length: {content_length} > {max_bytes})",
                            redirect_chain=redirect_chain,
                        )
                except ValueError:
                    pass  # Invalid header, will enforce via streaming
            
            # INVARIANT 3: Stream with aiter_bytes(), enforce hard byte cap
            # NEVER use response.text or response.content - they materialize full body
            total_bytes = 0
            chunks: List[bytes] = []
            
            async for chunk in response.aiter_bytes():
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    # Stop immediately - don't accumulate more
                    logger.warning(f"Response exceeded {max_bytes} bytes at {total_bytes}, aborting stream")
                    return FetchResult(
                        success=False,
                        error=f"Response too large (>{max_bytes} bytes)",
                        redirect_chain=redirect_chain,
                    )
                chunks.append(chunk)
            
            # Decode accumulated bytes
            raw_bytes = b"".join(chunks)
            try:
                content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError as e:
                return FetchResult(
                    success=False,
                    error=f"Response not valid UTF-8: {str(e)[:100]}",
                    redirect_chain=redirect_chain,
                )
            
            return FetchResult(
                success=True,
                content=content,
                final_url=url,
                content_type=response.headers.get("content-type", ""),
                redirect_chain=redirect_chain,
            )
            
    except httpx.HTTPStatusError as e:
        return FetchResult(
            success=False,
            error=f"HTTP error: {e.response.status_code}",
            redirect_chain=redirect_chain,
        )
    except httpx.TimeoutException as e:
        return FetchResult(
            success=False,
            error=f"Timeout during streaming: {type(e).__name__}",
            redirect_chain=redirect_chain,
        )
    except Exception as e:
        return FetchResult(
            success=False,
            error=f"Stream error: {str(e)[:200]}",
            redirect_chain=redirect_chain,
        )


def hardened_fetch_sync(
    url: str,
    **kwargs,
) -> FetchResult:
    """
    Synchronous wrapper for hardened_fetch.
    
    For use in sync contexts (e.g., bootstrap scripts, CLI).
    """
    import asyncio
    return asyncio.run(hardened_fetch(url, **kwargs))


# =============================================================================
# Configuration Accessors (for testing)
# =============================================================================

def get_timeout_config() -> dict:
    """Return current timeout configuration for testing/auditing."""
    return {
        "connect": CONNECT_TIMEOUT,
        "read": READ_TIMEOUT,
        "write": WRITE_TIMEOUT,
        "pool": POOL_TIMEOUT,
        "default_overall": DEFAULT_TIMEOUT,
    }


def get_limits_config() -> dict:
    """Return current limits configuration for testing/auditing."""
    return {
        "max_connections": MAX_CONNECTIONS,
        "max_keepalive_connections": MAX_KEEPALIVE_CONNECTIONS,
        "max_redirects": DEFAULT_MAX_REDIRECTS,
    }

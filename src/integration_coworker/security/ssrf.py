"""
SSRF (Server-Side Request Forgery) protection module.

Provides application-layer protection against SSRF attacks by:
1. Validating URL scheme (http/https only)
2. Resolving hostname to IP and blocking private/loopback/link-local ranges
3. Validating redirect targets (not just initial URL)
4. DNS rebinding mitigation: resolve immediately before each request

OWASP Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html

DEFENSE IN DEPTH (per OWASP):
This module provides application-layer validation, which is ONE layer of
SSRF defense. Complete protection requires multiple layers:

1. Application layer (this module): URL/IP validation before requests
2. Network layer: Egress firewall rules blocking RFC1918/metadata IPs
3. Cloud layer: Instance metadata service protections (IMDSv2, etc.)
4. DNS layer: Internal DNS resolver that blocks private IP responses

This module alone does NOT provide complete SSRF protection due to:
- DNS rebinding: Attacker-controlled DNS could return different IPs
  between our validation check and httpx's actual TCP connect
- TOCTOU window: Inherent race condition in "check-then-use" pattern

For production deployments, deploy network egress controls (proxy/firewall)
that block RFC1918, link-local (169.254.x.x), and cloud metadata IPs at
the network layer, where there is no TOCTOU window.

Design Notes:
- We resolve DNS immediately before validation to minimize TOCTOU window
- Each redirect hop is validated independently
- IPv6 private/loopback ranges are also blocked
- Resolver is injectable for testing without real DNS
"""

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import Optional, Set, Callable, List
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Type alias for DNS resolver function
# Takes hostname, returns list of IP strings
DNSResolver = Callable[[str], List[str]]

# Blocked IP networks - private, loopback, link-local, metadata
# Based on OWASP SSRF Prevention Cheat Sheet
BLOCKED_NETWORKS = [
    # IPv4 private (RFC1918)
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # IPv4 loopback
    ipaddress.ip_network("127.0.0.0/8"),
    # IPv4 link-local (includes AWS/GCP metadata 169.254.169.254)
    ipaddress.ip_network("169.254.0.0/16"),
    # IPv4 CGNAT (Carrier-grade NAT)
    ipaddress.ip_network("100.64.0.0/10"),
    # IPv6 loopback
    ipaddress.ip_network("::1/128"),
    # IPv6 link-local
    ipaddress.ip_network("fe80::/10"),
    # IPv6 unique local (private)
    ipaddress.ip_network("fc00::/7"),
    # IPv6 documentation range (shouldn't be routable but block anyway)
    ipaddress.ip_network("2001:db8::/32"),
    # IPv4-mapped IPv6 addresses in private ranges
    # These need special handling - see is_ip_blocked()
]

# Allowed URL schemes
ALLOWED_SCHEMES = frozenset({"http", "https"})


class SSRFBlockedError(Exception):
    """Raised when a URL is blocked due to SSRF protection."""
    
    def __init__(self, url: str, ip: str, reason: str):
        self.url = url
        self.ip = ip
        self.reason = reason
        super().__init__(f"SSRF blocked: {url} resolved to {ip} ({reason})")


class SSRFSchemeError(SSRFBlockedError):
    """Raised when URL uses a disallowed scheme."""
    
    def __init__(self, url: str, scheme: str):
        super().__init__(url, "N/A", f"Scheme '{scheme}' not allowed (only http/https)")
        self.scheme = scheme


class SSRFRedirectLimitError(SSRFBlockedError):
    """Raised when redirect limit is exceeded."""
    
    def __init__(self, url: str, limit: int):
        super().__init__(url, "N/A", f"Exceeded maximum redirects ({limit})")
        self.limit = limit


@dataclass
class SSRFConfig:
    """Configuration for SSRF protection."""
    
    # Maximum redirect hops allowed
    max_redirects: int = 5
    
    # Allowed URL schemes
    allowed_schemes: Set[str] = field(default_factory=lambda: set(ALLOWED_SCHEMES))
    
    # Additional blocked networks (on top of defaults)
    extra_blocked_networks: list = field(default_factory=list)
    
    # If True, block all IPv6 addresses (stricter but may break some endpoints)
    block_all_ipv6: bool = False
    
    # DNS resolution timeout in seconds
    dns_timeout: float = 5.0


def is_ip_blocked(ip_str: str, config: Optional[SSRFConfig] = None) -> tuple[bool, str]:
    """
    Check if an IP address should be blocked.
    
    Args:
        ip_str: IP address as string (IPv4 or IPv6)
        config: Optional SSRFConfig for additional restrictions
        
    Returns:
        (is_blocked, reason) tuple
    """
    config = config or SSRFConfig()
    
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True, "Invalid IP address format"
    
    # Check if IPv6 and we're blocking all IPv6
    if config.block_all_ipv6 and ip.version == 6:
        return True, "IPv6 addresses blocked by policy"
    
    # Handle IPv4-mapped IPv6 addresses (::ffff:x.x.x.x)
    # These can bypass IPv4-only blocklists
    if hasattr(ip, 'ipv4_mapped') and ip.ipv4_mapped is not None:
        # Check the mapped IPv4 address instead
        ip = ip.ipv4_mapped
        logger.debug(f"Converted IPv4-mapped IPv6 to {ip}")
    
    # Check against all blocked networks
    all_networks = BLOCKED_NETWORKS + config.extra_blocked_networks
    for network in all_networks:
        try:
            if ip in network:
                return True, f"IP in blocked range {network}"
        except TypeError:
            # IP version mismatch (e.g., checking IPv6 against IPv4 network)
            continue
    
    # Check built-in properties for safety
    if ip.is_private:
        return True, "IP is private (is_private=True)"
    if ip.is_loopback:
        return True, "IP is loopback"
    if ip.is_link_local:
        return True, "IP is link-local"
    if ip.is_reserved:
        return True, "IP is reserved"
    if ip.is_multicast:
        return True, "IP is multicast"
    
    return False, ""


def default_dns_resolver(hostname: str, timeout: float = 5.0) -> List[str]:
    """
    Default DNS resolver using socket.getaddrinfo.
    
    Uses socket.getaddrinfo for both IPv4 and IPv6 resolution.
    This resolution happens immediately before our validation to minimize
    the TOCTOU window (though it cannot be fully eliminated without
    network-layer controls).
    
    Args:
        hostname: Hostname to resolve
        timeout: DNS resolution timeout (default 5.0s)
        
    Returns:
        List of resolved IP addresses
        
    Raises:
        SSRFBlockedError: If DNS resolution fails
    """
    original_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        # AF_UNSPEC = both IPv4 and IPv6
        results = socket.getaddrinfo(
            hostname, 
            None, 
            socket.AF_UNSPEC, 
            socket.SOCK_STREAM
        )
        # Extract unique IPs from results
        # getaddrinfo returns: (family, type, proto, canonname, sockaddr)
        # sockaddr is (ip, port) for IPv4, (ip, port, flow, scope) for IPv6
        ips = list(set(sockaddr[0] for _, _, _, _, sockaddr in results))
        return ips
    except socket.gaierror as e:
        raise SSRFBlockedError(
            hostname, 
            "unresolvable", 
            f"DNS resolution failed: {e}"
        )
    except socket.timeout:
        raise SSRFBlockedError(
            hostname,
            "timeout",
            f"DNS resolution timed out after {timeout}s"
        )
    finally:
        socket.setdefaulttimeout(original_timeout)


def validate_url_target(
    url: str, 
    config: Optional[SSRFConfig] = None,
    resolver: Optional[DNSResolver] = None
) -> List[str]:
    """
    Validate a URL target for SSRF safety.
    
    Performs:
    1. Scheme validation (http/https only)
    2. Hostname extraction
    3. DNS resolution (immediately before validation)
    4. IP blocklist check for ALL resolved addresses
    
    This should be called before each HTTP request, including for
    redirect targets.
    
    Args:
        url: URL to validate
        config: Optional SSRFConfig
        resolver: Optional DNS resolver callable. Signature: (hostname: str) -> List[str].
                  Defaults to default_dns_resolver which uses socket.getaddrinfo.
                  For testing, pass a stub that returns known IPs without real DNS.
        
    Returns:
        List of resolved IP addresses (for logging/debugging)
        
    Raises:
        SSRFBlockedError: If URL fails validation
        SSRFSchemeError: If scheme is not allowed
    """
    config = config or SSRFConfig()
    
    # Use default resolver if none provided
    if resolver is None:
        resolver = lambda h: default_dns_resolver(h, config.dns_timeout)
    
    # Parse URL
    try:
        parsed = urlparse(url)
    except Exception as e:
        raise SSRFBlockedError(url, "N/A", f"URL parsing failed: {e}")
    
    # 1. Scheme validation
    scheme = (parsed.scheme or "").lower()
    if scheme not in config.allowed_schemes:
        raise SSRFSchemeError(url, scheme)
    
    # 2. Extract hostname
    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError(url, "N/A", "No hostname in URL")
    
    # Check if hostname is already an IP address (no DNS needed)
    try:
        # Try to parse as IP directly
        direct_ip = ipaddress.ip_address(hostname)
        blocked, reason = is_ip_blocked(str(direct_ip), config)
        if blocked:
            raise SSRFBlockedError(url, str(direct_ip), reason)
        return [str(direct_ip)]
    except ValueError:
        # Not an IP, need DNS resolution
        pass
    
    # 3. DNS resolution (minimize TOCTOU by resolving immediately)
    resolved_ips = resolver(hostname)
    
    if not resolved_ips:
        raise SSRFBlockedError(url, "empty", "DNS resolution returned no addresses")
    
    # 4. Check ALL resolved IPs - block if ANY is forbidden
    for ip_str in resolved_ips:
        blocked, reason = is_ip_blocked(ip_str, config)
        if blocked:
            logger.warning(
                f"SSRF blocked: {url} -> {ip_str} ({reason}). "
                f"All resolved IPs: {resolved_ips}"
            )
            raise SSRFBlockedError(url, ip_str, reason)
    
    logger.debug(f"SSRF check passed for {url} -> {resolved_ips}")
    return resolved_ips


def validate_redirect_chain(
    initial_url: str,
    redirect_urls: list[str],
    config: Optional[SSRFConfig] = None,
    resolver: Optional[DNSResolver] = None
) -> None:
    """
    Validate a chain of redirect URLs.
    
    Args:
        initial_url: The original URL (for error context)
        redirect_urls: List of redirect target URLs
        config: Optional SSRFConfig
        resolver: Optional DNS resolver callable. See validate_url_target for details.
        
    Raises:
        SSRFRedirectLimitError: If too many redirects
        SSRFBlockedError: If any redirect target fails validation
    """
    config = config or SSRFConfig()
    
    if len(redirect_urls) > config.max_redirects:
        raise SSRFRedirectLimitError(initial_url, config.max_redirects)
    
    for i, redirect_url in enumerate(redirect_urls):
        try:
            validate_url_target(redirect_url, config, resolver)
        except SSRFBlockedError as e:
            # Enhance error message with redirect context
            raise SSRFBlockedError(
                initial_url,
                e.ip,
                f"Redirect #{i+1} to {redirect_url}: {e.reason}"
            )

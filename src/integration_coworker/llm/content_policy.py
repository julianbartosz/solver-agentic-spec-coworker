"""
Content Policy Enforcement (SEC-004)

Semantic validation layer for generated code that goes beyond AST analysis.
Validates that generated code:
- Only references endpoints that exist in the Silver API model
- Doesn't include insecure credential patterns
- Follows security best practices

This is complementary to codegen/security.py which handles syntactic (AST) checks.
content_policy.py handles semantic checks that require understanding of the API model.

Per V2_IMPLEMENTATION_PLAN_SUPPLEMENT.md Section 2.
"""
import ast
import re
import logging
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Set, Dict, Any

logger = logging.getLogger(__name__)


class PolicyViolationType(Enum):
    """Types of content policy violations."""
    HALLUCINATED_ENDPOINT = "hallucinated_endpoint"
    INSECURE_CREDENTIAL_USAGE = "insecure_credential_usage"
    DATA_LEAKAGE = "data_leakage"
    BANNED_PATTERN = "banned_pattern"
    INSECURE_URL_CONSTRUCTION = "insecure_url_construction"


class PolicySeverity(str, Enum):
    """Severity levels for policy violations."""
    CRITICAL = "critical"  # Must be fixed
    HIGH = "high"          # Should be fixed
    MEDIUM = "medium"      # Consider fixing
    LOW = "low"            # Informational


@dataclass
class PolicyViolation:
    """A content policy violation detected in generated code."""
    type: PolicyViolationType
    message: str
    line_number: Optional[int] = None
    severity: PolicySeverity = PolicySeverity.HIGH
    suggestion: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "type": self.type.value,
            "message": self.message,
            "line_number": self.line_number,
            "severity": self.severity.value,
            "suggestion": self.suggestion,
        }


# Regex patterns for insecure credential usage
INSECURE_CREDENTIAL_PATTERNS = [
    # API key in query string
    (r'[?&]api_key=', "API key in query string (use header instead)"),
    (r'[?&]apikey=', "API key in query string (use header instead)"),
    (r'[?&]key=', "Potential API key in query string"),
    (r'[?&]token=', "Token in query string (use header instead)"),
    (r'[?&]access_token=', "Access token in query string (use header instead)"),
    (r'[?&]secret=', "Secret in query string"),
    
    # Hardcoded secrets - but exclude common test placeholders (Bug #31 fix)
    # Pattern: Match realistic-looking keys but not test placeholders
    (r'api_key\s*=\s*["\'](?!test_|mock_|fake_|your_)[a-zA-Z0-9_-]{32,}["\']', "Hardcoded API key detected"),
    (r'secret\s*=\s*["\'](?!test_|mock_|fake_|your_)[a-zA-Z0-9_-]{32,}["\']', "Hardcoded secret detected"),
    (r'password\s*=\s*["\'](?!test_|mock_|fake_|your_|password)[^"\']{12,}["\']', "Hardcoded password detected"),
    
    # HTTP Basic Auth in URL
    (r'https?://[^:]+:[^@]+@', "Credentials in URL (use separate auth)"),
]

# Whitelist patterns for test code - these are acceptable (Bug #31 fix)
TEST_CREDENTIAL_WHITELIST = [
    r'test_api_key',
    r'test_key',
    r'mock_api_key',
    r'fake_api_key',
    r'your_api_key_here',
    r'your_auth_token_here',
    r'test_\w+_key',
    r'ACXXXXXXXX+',  # Twilio-style masked test SID
    r'test_\d+',
    r'sk_test_\w*',  # Stripe test keys pattern
    r'pk_test_\w*',  # Stripe test keys pattern
]

# Patterns that suggest data leakage
DATA_LEAKAGE_PATTERNS = [
    (r'print\s*\(\s*.*(?:password|secret|token|api_key)', "Printing sensitive data"),
    (r'logging\.(?:debug|info|warning|error)\s*\(.*(?:password|secret|token|api_key)', "Logging sensitive data"),
]


class ContentPolicyEnforcer:
    """
    Enforces content policies on generated code.
    
    Uses the Silver API model to validate that generated code only references
    valid endpoints and follows security best practices.
    """
    
    def __init__(
        self,
        valid_paths: Optional[Set[str]] = None,
        valid_methods: Optional[Set[str]] = None,
        base_urls: Optional[Set[str]] = None,
    ):
        """
        Initialize the policy enforcer.
        
        Args:
            valid_paths: Set of valid API paths from the Silver model
            valid_methods: Set of valid HTTP methods
            base_urls: Set of valid base URLs for the API
        """
        self.valid_paths = valid_paths or set()
        self.valid_methods = valid_methods or {"GET", "POST", "PUT", "PATCH", "DELETE"}
        self.base_urls = base_urls or set()
        
        # Compile regex patterns
        self._credential_patterns = [
            (re.compile(pattern, re.IGNORECASE), msg)
            for pattern, msg in INSECURE_CREDENTIAL_PATTERNS
        ]
        self._leakage_patterns = [
            (re.compile(pattern, re.IGNORECASE), msg)
            for pattern, msg in DATA_LEAKAGE_PATTERNS
        ]
        # Bug #31 fix: Compile whitelist patterns
        self._credential_whitelist = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in TEST_CREDENTIAL_WHITELIST
        ]
    
    @classmethod
    def from_endpoints(cls, endpoints: List[Any]) -> "ContentPolicyEnforcer":
        """
        Create a policy enforcer from a list of Endpoint objects.
        
        Args:
            endpoints: List of Endpoint domain objects from the Silver model
        """
        valid_paths = set()
        valid_methods = set()
        
        for endpoint in endpoints:
            if hasattr(endpoint, 'path'):
                valid_paths.add(endpoint.path)
            if hasattr(endpoint, 'method'):
                valid_methods.add(endpoint.method.upper())
        
        return cls(valid_paths=valid_paths, valid_methods=valid_methods)
    
    def validate_code(self, code: str) -> List[PolicyViolation]:
        """
        Validate code against content policies.
        
        Args:
            code: Python source code to validate
            
        Returns:
            List of policy violations found
        """
        violations = []
        
        # Check for insecure credential patterns
        violations.extend(self._check_credential_patterns(code))
        
        # Check for data leakage
        violations.extend(self._check_data_leakage(code))
        
        # Check for hallucinated endpoints (if we have a valid_paths list)
        if self.valid_paths:
            violations.extend(self._check_hallucinated_endpoints(code))
        
        return violations
    
    def _check_credential_patterns(self, code: str) -> List[PolicyViolation]:
        """Check for insecure credential usage patterns."""
        violations = []
        
        for line_num, line in enumerate(code.split('\n'), 1):
            for pattern, message in self._credential_patterns:
                match = pattern.search(line)
                if match:
                    # Bug #31 fix: Check if matched value is in whitelist
                    matched_text = match.group(0)
                    is_whitelisted = any(
                        wp.search(matched_text) or wp.search(line)
                        for wp in self._credential_whitelist
                    )
                    if is_whitelisted:
                        logger.debug(f"Credential pattern matched but whitelisted: {matched_text[:30]}...")
                        continue
                    
                    violations.append(PolicyViolation(
                        type=PolicyViolationType.INSECURE_CREDENTIAL_USAGE,
                        message=message,
                        line_number=line_num,
                        severity=PolicySeverity.CRITICAL,
                        suggestion="Use environment variables and secure headers for credentials.",
                    ))
        
        return violations
    
    def _check_data_leakage(self, code: str) -> List[PolicyViolation]:
        """Check for data leakage patterns."""
        violations = []
        
        for line_num, line in enumerate(code.split('\n'), 1):
            for pattern, message in self._leakage_patterns:
                if pattern.search(line):
                    violations.append(PolicyViolation(
                        type=PolicyViolationType.DATA_LEAKAGE,
                        message=message,
                        line_number=line_num,
                        severity=PolicySeverity.HIGH,
                        suggestion="Avoid printing or logging sensitive data. Use redaction if necessary.",
                    ))
        
        return violations
    
    def _check_hallucinated_endpoints(self, code: str) -> List[PolicyViolation]:
        """
        Check for API endpoints that don't exist in the Silver model.
        
        Uses AST to find string literals that look like API paths,
        then validates them against the known valid paths.
        """
        violations = []
        
        try:
            tree = ast.parse(code)
        except SyntaxError:
            # If code doesn't parse, skip this check
            return violations
        
        # Find all string literals that look like API paths
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                value = node.value
                
                # Check if it looks like an API path
                if self._looks_like_api_path(value):
                    # Normalize the path for comparison
                    normalized = self._normalize_path(value)
                    
                    if not self._is_valid_path(normalized):
                        violations.append(PolicyViolation(
                            type=PolicyViolationType.HALLUCINATED_ENDPOINT,
                            message=f"API path '{value}' not found in specification",
                            line_number=node.lineno if hasattr(node, 'lineno') else None,
                            severity=PolicySeverity.HIGH,
                            suggestion=f"Valid paths include: {', '.join(list(self.valid_paths)[:3])}...",
                        ))
        
        return violations
    
    def _looks_like_api_path(self, value: str) -> bool:
        """Check if a string looks like an API path."""
        # Must start with / and contain path segments
        if not value.startswith('/'):
            return False
        
        # Filter out common non-path strings
        if value in ('/', '//', '/*', '/.*'):
            return False
        
        # Should have at least one path segment
        segments = [s for s in value.split('/') if s]
        if not segments:
            return False
        
        # Common API path patterns
        api_indicators = ['v1', 'v2', 'v3', 'api', 'rest']
        has_version = any(seg.lower() in api_indicators for seg in segments)
        
        # If it has a version prefix or looks like a resource path
        has_resource = any(seg.isalpha() or seg.startswith('{') for seg in segments)
        
        return has_version or has_resource
    
    def _normalize_path(self, path: str) -> str:
        """Normalize a path for comparison."""
        # Remove query strings
        if '?' in path:
            path = path.split('?')[0]
        
        # Replace path parameters with placeholders
        # e.g., /users/123 -> /users/{id}
        segments = []
        for seg in path.split('/'):
            if seg and seg.isdigit():
                segments.append('{id}')
            elif seg and seg.startswith('{') and seg.endswith('}'):
                segments.append(seg)
            else:
                segments.append(seg)
        
        return '/'.join(segments)
    
    def _is_valid_path(self, path: str) -> bool:
        """Check if a path matches any valid path (with pattern matching)."""
        if path in self.valid_paths:
            return True
        
        # Try matching with path parameter wildcards
        for valid_path in self.valid_paths:
            if self._paths_match(path, valid_path):
                return True
        
        return False
    
    def _paths_match(self, path: str, pattern: str) -> bool:
        """Check if a path matches a pattern with path parameters."""
        path_parts = path.split('/')
        pattern_parts = pattern.split('/')
        
        if len(path_parts) != len(pattern_parts):
            return False
        
        for p, pat in zip(path_parts, pattern_parts):
            # Pattern parameter (e.g., {id}) matches anything
            if pat.startswith('{') and pat.endswith('}'):
                continue
            # Otherwise must match exactly
            if p != pat:
                return False
        
        return True


def validate_generated_code(
    code: str,
    endpoints: Optional[List[Any]] = None,
) -> tuple[bool, List[PolicyViolation]]:
    """
    Convenience function to validate generated code.
    
    Args:
        code: Python source code to validate
        endpoints: Optional list of Endpoint objects from the Silver model
        
    Returns:
        Tuple of (is_valid, violations)
        is_valid is False if any CRITICAL or HIGH severity violations found
    """
    if endpoints:
        enforcer = ContentPolicyEnforcer.from_endpoints(endpoints)
    else:
        enforcer = ContentPolicyEnforcer()
    
    violations = enforcer.validate_code(code)
    
    # Determine if valid (no critical/high violations)
    is_valid = not any(
        v.severity in (PolicySeverity.CRITICAL, PolicySeverity.HIGH)
        for v in violations
    )
    
    return is_valid, violations


def format_violations(violations: List[PolicyViolation]) -> str:
    """Format violations as human-readable string."""
    if not violations:
        return "No spec compliance violations found."
    
    lines = ["Spec compliance violations detected:"]
    for v in violations:
        loc = f"Line {v.line_number}: " if v.line_number else ""
        lines.append(f"  [{v.severity.value.upper()}] {loc}{v.message}")
        if v.suggestion:
            lines.append(f"    Suggestion: {v.suggestion}")
    
    return "\n".join(lines)

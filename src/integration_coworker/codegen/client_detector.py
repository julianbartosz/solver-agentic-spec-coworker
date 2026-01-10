"""
Client Detection for Target Repositories (V39-007)

This module provides robust detection of existing API clients in target repositories
to prevent duplicate client generation and enable reuse of existing code.

Design Principles:
1. Scan repo for files that look like API clients
2. Parse and analyze found clients for compatibility
3. Match against the target API/provider being integrated
4. Provide import paths and class names for reuse in flows

Detection Strategy:
- File naming patterns (e.g., openai_client.py, StripeClient.py)
- Class naming patterns (e.g., *Client, *API, *Service)
- HTTP library imports (httpx, requests, aiohttp, urllib3)
- Base URL matching against target API
- Method signature analysis for API compatibility
"""
import ast
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class ExistingClientInfo:
    """Information about an existing API client found in the repo."""
    
    # File location
    file_path: str  # Relative path from repo root
    module_path: str  # Python import path (e.g., "src.clients.openai_client")
    
    # Class information
    class_name: str  # e.g., "OpenAIClient"
    base_classes: List[str] = field(default_factory=list)
    
    # Available methods
    methods: List[str] = field(default_factory=list)
    async_methods: List[str] = field(default_factory=list)
    
    # HTTP client library used
    http_library: Optional[str] = None  # "httpx", "requests", "aiohttp", etc.
    
    # API information (if detectable)
    base_url: Optional[str] = None
    api_version: Optional[str] = None
    
    # Match confidence (0.0 - 1.0)
    confidence: float = 0.0
    
    # Detection notes
    match_reasons: List[str] = field(default_factory=list)


@dataclass
class ClientDetectionResult:
    """Result of scanning a repository for existing API clients."""
    
    # Found clients matching the target API
    matching_clients: List[ExistingClientInfo] = field(default_factory=list)
    
    # Other clients found (for different APIs)
    other_clients: List[ExistingClientInfo] = field(default_factory=list)
    
    # Best match for the target API (if any)
    best_match: Optional[ExistingClientInfo] = None
    
    # Whether to use existing client vs generate new
    should_use_existing: bool = False
    
    # Import statement to use existing client
    import_statement: Optional[str] = None
    
    # Scan statistics
    files_scanned: int = 0
    clients_found: int = 0
    
    # Detection notes
    detection_notes: List[str] = field(default_factory=list)


# =============================================================================
# DETECTION PATTERNS
# =============================================================================

# File patterns that likely contain API clients
CLIENT_FILE_PATTERNS = [
    r".*_client\.py$",
    r".*client\.py$",
    r".*_api\.py$",
    r".*api\.py$",
    r".*_service\.py$",
    r"clients/.*\.py$",
    r"api/.*\.py$",
    r"services/.*\.py$",
    r"integrations/.*\.py$",
    r"sdk/.*\.py$",
]

# Class name patterns for API clients
CLIENT_CLASS_PATTERNS = [
    r".*Client$",
    r".*API$",
    r".*Service$",
    r".*SDK$",
    r".*Connector$",
    r".*Gateway$",
]

# HTTP libraries that indicate API client functionality
HTTP_LIBRARIES = {
    "httpx": ["httpx", "httpx.AsyncClient", "httpx.Client"],
    "requests": ["requests", "requests.Session"],
    "aiohttp": ["aiohttp", "aiohttp.ClientSession"],
    "urllib3": ["urllib3"],
    "http.client": ["http.client"],
}

# Known API provider patterns (name -> base URL patterns)
KNOWN_API_PROVIDERS: Dict[str, List[str]] = {
    "openai": ["api.openai.com", "openai.azure.com"],
    "anthropic": ["api.anthropic.com"],
    "stripe": ["api.stripe.com"],
    "github": ["api.github.com"],
    "gitlab": ["gitlab.com/api", "gitlab"],
    "slack": ["slack.com/api"],
    "twilio": ["api.twilio.com"],
    "sendgrid": ["api.sendgrid.com"],
    "aws": ["amazonaws.com"],
    "google": ["googleapis.com"],
    "azure": ["azure.com", "microsoft.com"],
    "digitalocean": ["api.digitalocean.com"],
    "cloudflare": ["api.cloudflare.com"],
    "datadog": ["api.datadoghq.com"],
    "sentry": ["sentry.io"],
    "jira": ["atlassian.net", "jira"],
    "notion": ["api.notion.com"],
    "airtable": ["api.airtable.com"],
    "hubspot": ["api.hubapi.com"],
    "salesforce": ["salesforce.com"],
    "zendesk": ["zendesk.com"],
    "intercom": ["api.intercom.io"],
    "segment": ["api.segment.io"],
    "mixpanel": ["api.mixpanel.com"],
    "amplitude": ["api.amplitude.com"],
}


# =============================================================================
# AST ANALYSIS HELPERS
# =============================================================================

class ClientClassVisitor(ast.NodeVisitor):
    """AST visitor to extract client class information."""
    
    def __init__(self):
        self.classes: List[Dict] = []
        self.imports: Set[str] = set()
        self.string_literals: List[str] = []
        self.current_class: Optional[str] = None
    
    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.add(alias.name)
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        if node.module:
            self.imports.add(node.module)
            for alias in node.names:
                self.imports.add(f"{node.module}.{alias.name}")
        self.generic_visit(node)
    
    def visit_ClassDef(self, node: ast.ClassDef):
        # Extract base class names
        base_names = []
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_names.append(base.id)
            elif isinstance(base, ast.Attribute):
                base_names.append(base.attr)
        
        # Extract methods
        methods = []
        async_methods = []
        for item in node.body:
            if isinstance(item, ast.FunctionDef):
                methods.append(item.name)
            elif isinstance(item, ast.AsyncFunctionDef):
                async_methods.append(item.name)
        
        self.classes.append({
            "name": node.name,
            "bases": base_names,
            "methods": methods,
            "async_methods": async_methods,
            "lineno": node.lineno,
        })
        
        # Visit nested content for string literals
        self.current_class = node.name
        self.generic_visit(node)
        self.current_class = None
    
    def visit_Constant(self, node: ast.Constant):
        # Capture string literals (for base URLs, API versions, etc.)
        if isinstance(node.value, str) and len(node.value) > 5:
            self.string_literals.append(node.value)
        self.generic_visit(node)
    
    def visit_Str(self, node: ast.Str):
        # Python < 3.8 compatibility
        if len(node.s) > 5:
            self.string_literals.append(node.s)
        self.generic_visit(node)


def _analyze_python_file(file_path: Path) -> Optional[Dict]:
    """
    Analyze a Python file to extract client class information.
    
    Returns dict with classes, imports, and detected patterns, or None on error.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(file_path))
        
        visitor = ClientClassVisitor()
        visitor.visit(tree)
        
        return {
            "classes": visitor.classes,
            "imports": visitor.imports,
            "string_literals": visitor.string_literals,
        }
    except (SyntaxError, UnicodeDecodeError, OSError) as e:
        logger.debug(f"Failed to analyze {file_path}: {e}")
        return None


def _detect_http_library(imports: Set[str]) -> Optional[str]:
    """Detect which HTTP library is used based on imports."""
    for lib_name, patterns in HTTP_LIBRARIES.items():
        for pattern in patterns:
            if pattern in imports or any(imp.startswith(pattern) for imp in imports):
                return lib_name
    return None


def _extract_base_url(string_literals: List[str]) -> Optional[str]:
    """Extract base URL from string literals."""
    url_pattern = re.compile(r"https?://[a-zA-Z0-9.-]+(?:/v\d+)?/?")
    for literal in string_literals:
        match = url_pattern.search(literal)
        if match:
            return match.group(0).rstrip("/")
    return None


def _match_provider(
    class_name: str,
    file_path: str,
    base_url: Optional[str],
    target_provider: str
) -> Tuple[bool, float, List[str]]:
    """
    Check if a client matches the target provider.
    
    Returns (is_match, confidence, reasons).
    """
    reasons = []
    confidence = 0.0
    target_lower = target_provider.lower()
    
    # Check class name
    if target_lower in class_name.lower():
        confidence += 0.4
        reasons.append(f"Class name contains '{target_provider}'")
    
    # Check file path
    if target_lower in file_path.lower():
        confidence += 0.3
        reasons.append(f"File path contains '{target_provider}'")
    
    # Check base URL against known providers
    if base_url and target_lower in KNOWN_API_PROVIDERS:
        for url_pattern in KNOWN_API_PROVIDERS[target_lower]:
            if url_pattern in base_url.lower():
                confidence += 0.3
                reasons.append(f"Base URL matches known {target_provider} pattern")
                break
    
    # Also check if base_url contains provider name
    if base_url and target_lower in base_url.lower():
        confidence += 0.2
        reasons.append(f"Base URL contains '{target_provider}'")
    
    is_match = confidence >= 0.3
    return is_match, min(confidence, 1.0), reasons


def _is_client_class(class_info: Dict, imports: Set[str]) -> Tuple[bool, List[str]]:
    """
    Determine if a class looks like an API client.
    
    Returns (is_client, reasons).
    """
    reasons = []
    
    # Check class name patterns
    class_name = class_info["name"]
    for pattern in CLIENT_CLASS_PATTERNS:
        if re.match(pattern, class_name, re.IGNORECASE):
            reasons.append(f"Class name matches pattern '{pattern}'")
            break
    
    # Check for HTTP-related methods
    http_methods = {"get", "post", "put", "delete", "patch", "request", "fetch", "call"}
    methods = set(m.lower() for m in class_info["methods"] + class_info["async_methods"])
    http_overlap = methods & http_methods
    if http_overlap:
        reasons.append(f"Has HTTP methods: {', '.join(http_overlap)}")
    
    # Check for HTTP library imports
    http_lib = _detect_http_library(imports)
    if http_lib:
        reasons.append(f"Uses HTTP library: {http_lib}")
    
    # Check for common client patterns
    client_patterns = {"__init__", "_make_request", "_request", "authenticate", "auth"}
    pattern_overlap = methods & client_patterns
    if pattern_overlap:
        reasons.append(f"Has client patterns: {', '.join(pattern_overlap)}")
    
    is_client = len(reasons) >= 2
    return is_client, reasons


# =============================================================================
# MAIN DETECTION FUNCTIONS
# =============================================================================

def scan_repo_for_clients(
    repo_root: str,
    target_provider: Optional[str] = None,
    target_base_url: Optional[str] = None,
    exclude_dirs: Optional[Set[str]] = None,
) -> ClientDetectionResult:
    """
    Scan a repository for existing API clients.
    
    Args:
        repo_root: Path to repository root
        target_provider: Provider name to match (e.g., "openai", "stripe")
        target_base_url: Base URL of target API (for matching)
        exclude_dirs: Directories to skip (default: venv, node_modules, etc.)
    
    Returns:
        ClientDetectionResult with found clients and recommendations
    """
    result = ClientDetectionResult()
    repo_path = Path(repo_root)
    
    if not repo_path.exists():
        result.detection_notes.append(f"Repository path does not exist: {repo_root}")
        return result
    
    # Default exclusions
    if exclude_dirs is None:
        exclude_dirs = {
            ".venv", "venv", ".env", "env",
            "node_modules", ".git", "__pycache__",
            ".tox", ".pytest_cache", ".mypy_cache",
            "dist", "build", "*.egg-info",
            "site-packages",
        }
    
    # Compile file patterns
    compiled_patterns = [re.compile(p) for p in CLIENT_FILE_PATTERNS]
    
    # Scan repository
    python_files = []
    for py_file in repo_path.rglob("*.py"):
        # Skip excluded directories
        rel_path = py_file.relative_to(repo_path)
        if any(excl in str(rel_path) for excl in exclude_dirs):
            continue
        
        # Check if file matches client patterns
        rel_str = str(rel_path)
        if any(p.match(rel_str) for p in compiled_patterns):
            python_files.append(py_file)
    
    result.files_scanned = len(python_files)
    logger.debug(f"[V39-007] Scanning {len(python_files)} potential client files in {repo_root}")
    
    # Analyze each file
    for py_file in python_files:
        analysis = _analyze_python_file(py_file)
        if not analysis:
            continue
        
        rel_path = str(py_file.relative_to(repo_path))
        
        for class_info in analysis["classes"]:
            # Check if this looks like a client class
            is_client, client_reasons = _is_client_class(class_info, analysis["imports"])
            
            if not is_client:
                continue
            
            # Build client info
            http_lib = _detect_http_library(analysis["imports"])
            base_url = _extract_base_url(analysis["string_literals"])
            
            # Calculate module path
            module_path = _file_path_to_module(rel_path)
            
            client_info = ExistingClientInfo(
                file_path=rel_path,
                module_path=module_path,
                class_name=class_info["name"],
                base_classes=class_info["bases"],
                methods=class_info["methods"],
                async_methods=class_info["async_methods"],
                http_library=http_lib,
                base_url=base_url,
                match_reasons=client_reasons,
            )
            
            result.clients_found += 1
            
            # Check if this client matches the target provider
            if target_provider:
                is_match, confidence, match_reasons = _match_provider(
                    class_info["name"],
                    rel_path,
                    base_url,
                    target_provider,
                )
                
                if is_match:
                    client_info.confidence = confidence
                    client_info.match_reasons.extend(match_reasons)
                    result.matching_clients.append(client_info)
                    logger.info(
                        f"[V39-007] Found matching client: {class_info['name']} "
                        f"in {rel_path} (confidence={confidence:.2f})"
                    )
                else:
                    result.other_clients.append(client_info)
            else:
                result.other_clients.append(client_info)
    
    # Determine best match
    if result.matching_clients:
        result.matching_clients.sort(key=lambda c: c.confidence, reverse=True)
        result.best_match = result.matching_clients[0]
        
        # Decide if we should use existing client
        if result.best_match.confidence >= 0.5:
            result.should_use_existing = True
            result.import_statement = _generate_import_statement(result.best_match)
            result.detection_notes.append(
                f"Found existing {target_provider} client: {result.best_match.class_name} "
                f"with confidence {result.best_match.confidence:.2f}"
            )
        else:
            result.detection_notes.append(
                f"Found potential {target_provider} client but confidence too low "
                f"({result.best_match.confidence:.2f}). Will generate new client."
            )
    else:
        result.detection_notes.append(
            f"No existing {target_provider} client found. Will generate new client."
        )
    
    logger.info(
        f"[V39-007] Client scan complete: {result.clients_found} clients found, "
        f"{len(result.matching_clients)} matching target provider"
    )
    
    return result


def _file_path_to_module(file_path: str) -> str:
    """Convert file path to Python module path."""
    # Remove .py extension
    module = file_path.replace(".py", "")
    # Replace path separators with dots
    module = module.replace("/", ".").replace("\\", ".")
    # Remove leading dots
    module = module.lstrip(".")
    # Handle src/ prefix
    if module.startswith("src."):
        module = module[4:]
    return module


def _generate_import_statement(client_info: ExistingClientInfo) -> str:
    """Generate import statement for an existing client."""
    return f"from {client_info.module_path} import {client_info.class_name}"


def check_for_existing_client(
    repo_root: str,
    provider_code: str,
    base_url: Optional[str] = None,
) -> Optional[ExistingClientInfo]:
    """
    Quick check for an existing client for a specific provider.
    
    This is a convenience function that returns just the best match
    or None if no suitable client is found.
    
    Args:
        repo_root: Path to repository root
        provider_code: Provider name (e.g., "openai", "stripe")
        base_url: Optional base URL for additional matching
    
    Returns:
        ExistingClientInfo if found with high confidence, else None
    """
    result = scan_repo_for_clients(
        repo_root=repo_root,
        target_provider=provider_code,
        target_base_url=base_url,
    )
    
    if result.should_use_existing and result.best_match:
        return result.best_match
    
    return None


# =============================================================================
# INTEGRATION WITH CODEGEN CONTEXT
# =============================================================================

@dataclass
class ClientDecision:
    """Decision about whether to use existing or generate new client."""
    
    use_existing: bool
    existing_client: Optional[ExistingClientInfo] = None
    
    # If using existing client
    import_statement: Optional[str] = None
    client_class_name: Optional[str] = None
    client_module_path: Optional[str] = None
    
    # Reasons for decision
    reasons: List[str] = field(default_factory=list)
    
    def __bool__(self) -> bool:
        """Returns True if using existing client."""
        return self.use_existing


def decide_client_strategy(
    repo_root: str,
    provider_code: str,
    base_url: Optional[str] = None,
    force_generate: bool = False,
) -> ClientDecision:
    """
    Decide whether to use an existing client or generate a new one.
    
    This is the main entry point for the client detection feature.
    
    Args:
        repo_root: Path to repository root
        provider_code: Provider name (e.g., "openai", "stripe")
        base_url: Optional base URL for matching
        force_generate: If True, always generate new client
    
    Returns:
        ClientDecision with strategy and client details
    """
    if force_generate:
        return ClientDecision(
            use_existing=False,
            reasons=["force_generate flag is set"],
        )
    
    if not repo_root:
        return ClientDecision(
            use_existing=False,
            reasons=["No repo_root provided"],
        )
    
    # Scan for existing clients
    result = scan_repo_for_clients(
        repo_root=repo_root,
        target_provider=provider_code,
        target_base_url=base_url,
    )
    
    if result.should_use_existing and result.best_match:
        client = result.best_match
        return ClientDecision(
            use_existing=True,
            existing_client=client,
            import_statement=result.import_statement,
            client_class_name=client.class_name,
            client_module_path=client.module_path,
            reasons=client.match_reasons + result.detection_notes,
        )
    
    return ClientDecision(
        use_existing=False,
        reasons=result.detection_notes,
    )

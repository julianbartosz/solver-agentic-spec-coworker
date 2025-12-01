"""
Policy Templates for Code Generation

Provides code snippets that can be injected into generated code based on
attached policies (AUTH, RETRY, RATE_LIMIT, LOGGING, IDEMPOTENCY).

These templates are designed to be combined with the main API client code
to provide production-ready resilience patterns.
"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass


@dataclass
class PolicyCodeSnippet:
    """A code snippet for a specific policy."""
    imports: List[str]  # Import statements needed
    setup_code: str  # Code to run at initialization
    pre_request_code: str  # Code to run before each request
    post_request_code: str  # Code to run after each request
    wrapper_code: str  # Decorator or wrapper code


# ---------------------------------------------------------------------------
# AUTH Policy Templates
# ---------------------------------------------------------------------------

def get_auth_template(config: Dict[str, Any]) -> PolicyCodeSnippet:
    """Generate auth-related code snippets based on auth type."""
    auth_type = config.get("type", "bearer")

    if auth_type == "bearer":
        return PolicyCodeSnippet(
            imports=["import os"],
            setup_code='''
        # Bearer token authentication
        self._auth_token = os.environ.get("{env_var}", "")
        if not self._auth_token:
            raise ValueError("Missing bearer token. Set {env_var} environment variable.")
'''.format(env_var=config.get("env_var", "API_TOKEN")),
            pre_request_code='''
        headers["Authorization"] = f"Bearer {self._auth_token}"
''',
            post_request_code="",
            wrapper_code="",
        )

    elif auth_type == "api_key":
        location = config.get("location", "header")
        key_name = config.get("key_name", "X-API-Key")
        env_var = config.get("env_var", "API_KEY")

        if location == "header":
            pre_request = f'''
        headers["{key_name}"] = self._api_key
'''
        else:  # query
            pre_request = f'''
        params["{key_name}"] = self._api_key
'''

        return PolicyCodeSnippet(
            imports=["import os"],
            setup_code=f'''
        # API Key authentication
        self._api_key = os.environ.get("{env_var}", "")
        if not self._api_key:
            raise ValueError("Missing API key. Set {env_var} environment variable.")
''',
            pre_request_code=pre_request,
            post_request_code="",
            wrapper_code="",
        )

    elif auth_type == "basic":
        return PolicyCodeSnippet(
            imports=["import os", "import base64"],
            setup_code='''
        # Basic authentication
        username = os.environ.get("API_USERNAME", "")
        password = os.environ.get("API_PASSWORD", "")
        if not username or not password:
            raise ValueError("Missing credentials. Set API_USERNAME and API_PASSWORD.")
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._basic_auth = f"Basic {credentials}"
''',
            pre_request_code='''
        headers["Authorization"] = self._basic_auth
''',
            post_request_code="",
            wrapper_code="",
        )

    elif auth_type == "oauth2":
        flow = config.get("flow", "client_credentials")
        token_url = config.get("token_url", "")

        return PolicyCodeSnippet(
            imports=["import os", "import time", "import httpx"],
            setup_code=f'''
        # OAuth2 {flow} authentication
        self._oauth_token = None
        self._token_expires_at = 0
        self._client_id = os.environ.get("OAUTH_CLIENT_ID", "")
        self._client_secret = os.environ.get("OAUTH_CLIENT_SECRET", "")
        self._token_url = "{token_url}"
        if not self._client_id or not self._client_secret:
            raise ValueError("Missing OAuth credentials. Set OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET.")
''',
            pre_request_code='''
        # Refresh token if expired
        if time.time() >= self._token_expires_at:
            self._refresh_oauth_token()
        headers["Authorization"] = f"Bearer {self._oauth_token}"
''',
            post_request_code="",
            wrapper_code='''
    def _refresh_oauth_token(self) -> None:
        """Refresh the OAuth2 access token."""
        response = httpx.post(
            self._token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
        )
        response.raise_for_status()
        data = response.json()
        self._oauth_token = data["access_token"]
        # Default to 1 hour minus buffer if expires_in not provided
        expires_in = data.get("expires_in", 3600) - 60
        self._token_expires_at = time.time() + expires_in
''',
        )

    else:  # No auth
        return PolicyCodeSnippet(
            imports=[],
            setup_code="",
            pre_request_code="",
            post_request_code="",
            wrapper_code="",
        )


# ---------------------------------------------------------------------------
# RETRY Policy Templates
# ---------------------------------------------------------------------------

def get_retry_template(config: Dict[str, Any]) -> PolicyCodeSnippet:
    """Generate retry logic code snippets."""
    max_attempts = config.get("max_attempts", 3)
    backoff_type = config.get("backoff_type", "exponential")
    initial_delay_ms = config.get("initial_delay_ms", 100)
    max_delay_ms = config.get("max_delay_ms", 5000)
    retryable_codes = config.get("retryable_status_codes", [429, 500, 502, 503, 504])

    return PolicyCodeSnippet(
        imports=["import time", "import random", "from typing import Callable, TypeVar"],
        setup_code=f'''
        # Retry configuration
        self._max_retries = {max_attempts}
        self._initial_delay_ms = {initial_delay_ms}
        self._max_delay_ms = {max_delay_ms}
        self._retryable_status_codes = {retryable_codes}
''',
        pre_request_code="",
        post_request_code="",
        wrapper_code=f'''
    T = TypeVar("T")
    
    def _with_retry(self, operation: Callable[[], T]) -> T:
        """Execute operation with exponential backoff retry."""
        last_exception = None
        delay_ms = self._initial_delay_ms
        
        for attempt in range(self._max_retries):
            try:
                response = operation()
                # Check if retryable status code
                if hasattr(response, "status_code") and response.status_code in self._retryable_status_codes:
                    raise RetryableError(f"Retryable status code: {{response.status_code}}")
                return response
            except (RetryableError, httpx.TransportError) as e:
                last_exception = e
                if attempt < self._max_retries - 1:
                    # {"Exponential" if backoff_type == "exponential" else "Linear"} backoff with jitter
                    jitter = random.uniform(0.8, 1.2)
                    sleep_time = min(delay_ms * jitter, self._max_delay_ms) / 1000
                    time.sleep(sleep_time)
                    {"delay_ms *= 2" if backoff_type == "exponential" else "delay_ms += self._initial_delay_ms"}
        
        raise last_exception


class RetryableError(Exception):
    """Exception for retryable errors."""
    pass
''',
    )


# ---------------------------------------------------------------------------
# RATE_LIMIT Policy Templates
# ---------------------------------------------------------------------------

def get_rate_limit_template(config: Dict[str, Any]) -> PolicyCodeSnippet:
    """Generate rate limiting code snippets."""
    requests_per_second = config.get("requests_per_second", 10)
    burst_size = config.get("burst_size", 20)

    return PolicyCodeSnippet(
        imports=["import time", "import threading"],
        setup_code=f'''
        # Rate limiting configuration
        self._rate_limit = {requests_per_second}  # requests per second
        self._burst_size = {burst_size}
        self._tokens = {burst_size}  # Token bucket
        self._last_refill = time.time()
        self._rate_limit_lock = threading.Lock()
''',
        pre_request_code='''
        self._acquire_rate_limit_token()
''',
        post_request_code="",
        wrapper_code='''
    def _acquire_rate_limit_token(self) -> None:
        """Acquire a token from the rate limiter (blocking if necessary)."""
        with self._rate_limit_lock:
            now = time.time()
            # Refill tokens based on elapsed time
            elapsed = now - self._last_refill
            tokens_to_add = elapsed * self._rate_limit
            self._tokens = min(self._burst_size, self._tokens + tokens_to_add)
            self._last_refill = now
            
            # Wait if no tokens available
            while self._tokens < 1:
                sleep_time = (1 - self._tokens) / self._rate_limit
                time.sleep(sleep_time)
                now = time.time()
                elapsed = now - self._last_refill
                self._tokens = min(self._burst_size, self._tokens + elapsed * self._rate_limit)
                self._last_refill = now
            
            self._tokens -= 1
''',
    )


# ---------------------------------------------------------------------------
# LOGGING Policy Templates
# ---------------------------------------------------------------------------

def get_logging_template(config: Dict[str, Any]) -> PolicyCodeSnippet:
    """Generate logging code snippets."""
    log_request = config.get("log_request", True)
    log_response = config.get("log_response", True)
    log_headers = config.get("log_headers", False)
    redact_fields = config.get("redact_fields", ["Authorization", "api_key"])

    return PolicyCodeSnippet(
        imports=["import logging"],
        setup_code='''
        # Logging configuration
        self._logger = logging.getLogger(self.__class__.__name__)
        self._redact_fields = ''' + repr(redact_fields) + '''
''',
        pre_request_code=f'''
        {"self._log_request(method, url, headers, params, json_body)" if log_request else ""}
''' if log_request else "",
        post_request_code=f'''
        {"self._log_response(response)" if log_response else ""}
''' if log_response else "",
        wrapper_code='''
    def _redact_sensitive(self, data: dict) -> dict:
        """Redact sensitive fields from data for logging."""
        if not isinstance(data, dict):
            return data
        redacted = {}
        for k, v in data.items():
            if any(field.lower() in k.lower() for field in self._redact_fields):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = v
        return redacted
    
    def _log_request(self, method: str, url: str, headers: dict, params: dict, body: dict) -> None:
        """Log outgoing request details."""
        self._logger.debug(f"Request: {method} {url}")
        ''' + ('''self._logger.debug(f"Headers: {self._redact_sensitive(headers)}")''' if log_headers else "") + '''
        if params:
            self._logger.debug(f"Params: {params}")
        if body:
            self._logger.debug(f"Body: {body}")
    
    def _log_response(self, response) -> None:
        """Log response details."""
        self._logger.debug(f"Response: {response.status_code}")
        try:
            self._logger.debug(f"Body: {response.json()}")
        except Exception:
            self._logger.debug(f"Body: {response.text[:500]}")
''',
    )


# ---------------------------------------------------------------------------
# IDEMPOTENCY Policy Templates
# ---------------------------------------------------------------------------

def get_idempotency_template(config: Dict[str, Any]) -> PolicyCodeSnippet:
    """Generate idempotency key code snippets."""
    header_name = config.get("header_name", "Idempotency-Key")
    key_generator = config.get("key_generator", "uuid4")

    if key_generator == "uuid4":
        imports = ["import uuid"]
        key_gen_code = "str(uuid.uuid4())"
    else:
        imports = ["import hashlib", "import json"]
        key_gen_code = "hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()"

    return PolicyCodeSnippet(
        imports=imports,
        setup_code=f'''
        # Idempotency configuration
        self._idempotency_header = "{header_name}"
''',
        pre_request_code=f'''
        # Add idempotency key for POST/PUT/PATCH requests
        if method.upper() in ("POST", "PUT", "PATCH"):
            idempotency_key = {key_gen_code}
            headers[self._idempotency_header] = idempotency_key
''',
        post_request_code="",
        wrapper_code="",
    )


# ---------------------------------------------------------------------------
# Combined Policy Template Builder
# ---------------------------------------------------------------------------

def build_policy_code(
    policies: List[Dict[str, Any]],
    policy_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Build combined code snippets from a list of policies.
    
    Args:
        policies: List of policy dicts with 'policy_type' and 'config' keys
        policy_types: Optional filter for specific policy types
        
    Returns:
        Dict with:
        - imports: Combined import statements
        - setup_code: Combined initialization code
        - pre_request_code: Combined pre-request code
        - post_request_code: Combined post-request code
        - wrapper_code: Combined helper methods/decorators
    """
    all_imports: List[str] = []
    all_setup: List[str] = []
    all_pre_request: List[str] = []
    all_post_request: List[str] = []
    all_wrapper: List[str] = []

    template_getters = {
        "AUTH": get_auth_template,
        "auth": get_auth_template,
        "RETRY": get_retry_template,
        "retry": get_retry_template,
        "RATE_LIMIT": get_rate_limit_template,
        "rate_limit": get_rate_limit_template,
        "LOGGING": get_logging_template,
        "logging": get_logging_template,
        "IDEMPOTENCY": get_idempotency_template,
        "idempotency": get_idempotency_template,
    }

    for policy in policies:
        policy_type = policy.get("policy_type", "")
        # Handle both string and enum types
        if hasattr(policy_type, "value"):
            policy_type = policy_type.value

        if policy_types and policy_type.upper() not in [pt.upper() for pt in policy_types]:
            continue

        template_getter = template_getters.get(policy_type)
        if not template_getter:
            continue

        config = policy.get("config", {})
        snippet = template_getter(config)

        all_imports.extend(snippet.imports)
        if snippet.setup_code.strip():
            all_setup.append(snippet.setup_code)
        if snippet.pre_request_code.strip():
            all_pre_request.append(snippet.pre_request_code)
        if snippet.post_request_code.strip():
            all_post_request.append(snippet.post_request_code)
        if snippet.wrapper_code.strip():
            all_wrapper.append(snippet.wrapper_code)

    # Deduplicate imports
    unique_imports = list(dict.fromkeys(all_imports))

    return {
        "imports": "\n".join(unique_imports),
        "setup_code": "\n".join(all_setup),
        "pre_request_code": "\n".join(all_pre_request),
        "post_request_code": "\n".join(all_post_request),
        "wrapper_code": "\n".join(all_wrapper),
    }


def inject_policies_into_client_code(
    client_code: str,
    policies: List[Dict[str, Any]],
) -> str:
    """
    Inject policy code into generated client code.
    
    This modifies the client code to include:
    - Policy-related imports
    - Initialization code in __init__
    - Pre/post request hooks
    - Helper methods
    
    Args:
        client_code: The generated client Python code
        policies: List of policies to inject
        
    Returns:
        Modified client code with policies injected
    """
    policy_code = build_policy_code(policies)

    # Simple injection approach: add imports at top, setup in __init__, helpers at end
    lines = client_code.split("\n")
    result_lines: List[str] = []

    # Track injection points
    imports_added = False
    in_init = False
    init_done = False
    last_import_index = -1

    # First pass: find last import line
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import_index = i

    for i, line in enumerate(lines):
        result_lines.append(line)

        # Add imports after last import line
        if not imports_added and i == last_import_index and policy_code["imports"]:
            result_lines.append("")
            result_lines.append("# Policy imports")
            result_lines.append(policy_code["imports"])
            imports_added = True

        # Detect __init__ method
        if "def __init__" in line:
            in_init = True

        # Add setup code at end of __init__
        if in_init and not init_done:
            # Look for end of __init__ (next def or class, or end of indented block)
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            is_end_of_init = (
                (next_line.strip().startswith("def ") and "def __init__" not in next_line) or
                next_line.strip().startswith("class ") or
                (next_line.strip() == "" and i + 2 < len(lines) and
                 (lines[i + 2].strip().startswith("def ") or lines[i + 2].strip().startswith("class ")))
            )
            if is_end_of_init and policy_code["setup_code"]:
                result_lines.append("")
                result_lines.append("        # Policy initialization")
                for setup_line in policy_code["setup_code"].split("\n"):
                    result_lines.append(setup_line)
                init_done = True
                in_init = False

    # Add wrapper code at end of class
    if policy_code["wrapper_code"]:
        result_lines.append("")
        result_lines.append("    # Policy helper methods")
        result_lines.append(policy_code["wrapper_code"])

    return "\n".join(result_lines)

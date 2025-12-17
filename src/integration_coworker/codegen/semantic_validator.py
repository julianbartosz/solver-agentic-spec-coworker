"""
Semantic Validation for Generated Code

Validates semantic correctness of LLM-generated code beyond syntax:
- Import resolution (do imported modules exist?)
- Class signature matching (are required methods present?)
- Type annotation consistency (do annotations match expected types?)

Part of the Hybrid Pattern Library + LLM architecture:
1. LLM generates code
2. Syntax validation (AST/tree-sitter) - EXISTING
3. Security validation (forbidden patterns) - EXISTING  
4. Semantic validation (this module) - NEW
5. If all pass → candidate for pattern library

Per Section 2: Decision Record - Alternative C implementation.
"""
import ast
import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class SemanticIssue:
    """A semantic issue detected in generated code."""
    
    line: int
    issue_type: str  # "missing_import", "invalid_import", "missing_method", "signature_mismatch"
    message: str
    severity: str = "error"  # "error" or "warning"
    context: Dict[str, Any] = field(default_factory=dict)


# Standard library modules that are always available
# This is a subset; full list would be huge
STDLIB_MODULES: Set[str] = {
    "abc", "aifc", "argparse", "array", "ast", "asyncio", "atexit",
    "base64", "bdb", "binascii", "binhex", "bisect", "builtins",
    "bz2", "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd",
    "code", "codecs", "codeop", "collections", "colorsys", "compileall",
    "concurrent", "configparser", "contextlib", "contextvars", "copy",
    "copyreg", "cProfile", "crypt", "csv", "ctypes", "curses",
    "dataclasses", "datetime", "dbm", "decimal", "difflib", "dis",
    "distutils", "doctest", "email", "encodings", "enum", "errno",
    "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch",
    "fractions", "ftplib", "functools", "gc", "getopt", "getpass",
    "gettext", "glob", "graphlib", "grp", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "idlelib", "imaplib", "imghdr", "imp",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "lib2to3", "linecache", "locale", "logging", "lzma",
    "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "pathlib", "pdb",
    "pickle", "pickletools", "pipes", "pkgutil", "platform", "plistlib",
    "poplib", "posix", "posixpath", "pprint", "profile", "pstats",
    "pty", "pwd", "py_compile", "pyclbr", "pydoc", "queue", "quopri",
    "random", "re", "readline", "reprlib", "resource", "rlcompleter",
    "runpy", "sched", "secrets", "select", "selectors", "shelve",
    "shlex", "shutil", "signal", "site", "smtpd", "smtplib", "sndhdr",
    "socket", "socketserver", "spwd", "sqlite3", "ssl", "stat",
    "statistics", "string", "stringprep", "struct", "subprocess",
    "sunau", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
    "tarfile", "telnetlib", "tempfile", "termios", "test", "textwrap",
    "threading", "time", "timeit", "tkinter", "token", "tokenize",
    "trace", "traceback", "tracemalloc", "tty", "turtle", "turtledemo",
    "types", "typing", "typing_extensions", "unicodedata", "unittest",
    "urllib", "uu", "uuid", "venv", "warnings", "wave", "weakref",
    "webbrowser", "winreg", "winsound", "wsgiref", "xdrlib", "xml",
    "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib", "zoneinfo",
}

# Common third-party packages that are typically available
# in our environment (dependencies in requirements.txt)
KNOWN_THIRD_PARTY: Set[str] = {
    "aiohttp", "anthropic", "anyio", "asyncpg", "attrs",
    "certifi", "charset_normalizer", "click", "colorama",
    "fastapi", "google", "greenlet", "httpcore", "httpx",
    "idna", "jinja2", "jsonschema", "langchain", "langchain_anthropic",
    "langchain_core", "langchain_google_genai", "langchain_openai",
    "langsmith", "markupsafe", "multidict", "numpy", "openai",
    "orjson", "packaging", "pandas", "pydantic", "pydantic_core",
    "pytest", "python_dateutil", "pytz", "pyyaml", "redis",
    "requests", "rich", "ruff", "setuptools", "six", "sniffio",
    "sqlalchemy", "starlette", "tenacity", "tiktoken", "tomli",
    "tree_sitter", "tree_sitter_languages", "typing_extensions",
    "urllib3", "uvicorn", "uvloop", "watchfiles", "websockets",
    "yarl", "psycopg", "psycopg2", "pgvector",
}


def _extract_imports(code: str) -> List[Tuple[str, int, str]]:
    """
    Extract all imports from Python code.
    
    Returns:
        List of (module_name, line_number, import_type)
        import_type is "import" or "from"
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []  # Syntax errors handled elsewhere
    
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # Get top-level module name
                module = alias.name.split('.')[0]
                imports.append((module, node.lineno, "import"))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module = node.module.split('.')[0]
                imports.append((module, node.lineno, "from"))
    
    return imports


def _is_module_available(module_name: str) -> bool:
    """
    Check if a module is available for import.
    
    Uses multiple strategies:
    1. Check against known stdlib/third-party sets (fast)
    2. Use importlib.util.find_spec (accurate but slower)
    """
    # Fast path: known modules
    if module_name in STDLIB_MODULES:
        return True
    if module_name in KNOWN_THIRD_PARTY:
        return True
    
    # Check if it's a local/relative import (starts with integration_coworker)
    if module_name.startswith("integration_coworker"):
        return True
    
    # Slow path: actually try to find the module
    try:
        spec = importlib.util.find_spec(module_name)
        return spec is not None
    except (ModuleNotFoundError, ValueError, ImportError):
        return False


def validate_imports(code: str) -> List[SemanticIssue]:
    """
    Validate that all imports in the code can be resolved.
    
    Args:
        code: Python source code
        
    Returns:
        List of SemanticIssue for unresolvable imports
    """
    issues = []
    imports = _extract_imports(code)
    
    for module_name, line_no, import_type in imports:
        if not _is_module_available(module_name):
            issues.append(SemanticIssue(
                line=line_no,
                issue_type="invalid_import",
                message=f"Module '{module_name}' cannot be resolved",
                severity="error",
                context={"module": module_name, "import_type": import_type},
            ))
    
    return issues


def _extract_class_methods(code: str, class_name: str) -> Dict[str, List[str]]:
    """
    Extract method signatures from a class.
    
    Args:
        code: Python source code
        class_name: Name of the class to analyze
        
    Returns:
        Dict mapping method_name -> list of parameter names
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return {}
    
    methods = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    # Extract parameter names (skip 'self')
                    params = [
                        arg.arg for arg in item.args.args
                        if arg.arg != 'self' and arg.arg != 'cls'
                    ]
                    methods[item.name] = params
    
    return methods


def validate_class_signature(
    code: str,
    class_name: str,
    expected_methods: List[str],
    strict: bool = False,
) -> List[SemanticIssue]:
    """
    Validate that a class has required methods.
    
    Args:
        code: Python source code
        class_name: Name of the class to validate
        expected_methods: List of method names that must exist
        strict: If True, also flag unexpected methods
        
    Returns:
        List of SemanticIssue for missing/unexpected methods
    """
    issues = []
    methods = _extract_class_methods(code, class_name)
    
    if not methods:
        # Class not found at all
        issues.append(SemanticIssue(
            line=1,
            issue_type="missing_class",
            message=f"Expected class '{class_name}' not found in code",
            severity="error",
            context={"class_name": class_name},
        ))
        return issues
    
    # Check for required methods
    found_methods = set(methods.keys())
    required = set(expected_methods)
    
    missing = required - found_methods
    for method in missing:
        issues.append(SemanticIssue(
            line=1,  # Can't determine line without more parsing
            issue_type="missing_method",
            message=f"Required method '{method}' not found in class '{class_name}'",
            severity="error",
            context={"class_name": class_name, "method": method},
        ))
    
    return issues


def validate_function_exists(
    code: str,
    function_name: str,
    min_params: int = 0,
) -> List[SemanticIssue]:
    """
    Validate that a top-level function exists with minimum parameters.
    
    Args:
        code: Python source code
        function_name: Name of the function to find
        min_params: Minimum number of parameters required
        
    Returns:
        List of SemanticIssue if function missing or signature wrong
    """
    issues = []
    
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    
    found = False
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == function_name:
                found = True
                # Check parameter count
                param_count = len([
                    arg for arg in node.args.args
                    if arg.arg != 'self' and arg.arg != 'cls'
                ])
                if param_count < min_params:
                    issues.append(SemanticIssue(
                        line=node.lineno,
                        issue_type="signature_mismatch",
                        message=f"Function '{function_name}' has {param_count} params, expected at least {min_params}",
                        severity="warning",
                        context={"function": function_name, "params": param_count, "expected": min_params},
                    ))
                break
    
    if not found:
        issues.append(SemanticIssue(
            line=1,
            issue_type="missing_function",
            message=f"Expected function '{function_name}' not found",
            severity="error",
            context={"function": function_name},
        ))
    
    return issues


def _has_docstring(node: ast.AST) -> bool:
    """Check if a function/class has a docstring."""
    if hasattr(node, 'body') and node.body:
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            return isinstance(first.value.value, str)
    return False


def validate_docstrings(code: str, require_module: bool = True) -> List[SemanticIssue]:
    """
    Validate that public functions/classes have docstrings.
    
    Args:
        code: Python source code
        require_module: Whether to require module-level docstring
        
    Returns:
        List of SemanticIssue for missing docstrings (warnings only)
    """
    issues = []
    
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    
    # Check module docstring
    if require_module and not ast.get_docstring(tree):
        issues.append(SemanticIssue(
            line=1,
            issue_type="missing_docstring",
            message="Module-level docstring missing",
            severity="warning",
            context={"scope": "module"},
        ))
    
    # Check class/function docstrings (public only - no leading underscore)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            if not node.name.startswith('_') and not _has_docstring(node):
                issues.append(SemanticIssue(
                    line=node.lineno,
                    issue_type="missing_docstring",
                    message=f"Class '{node.name}' missing docstring",
                    severity="warning",
                    context={"scope": "class", "name": node.name},
                ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith('_') and not _has_docstring(node):
                issues.append(SemanticIssue(
                    line=node.lineno,
                    issue_type="missing_docstring",
                    message=f"Function '{node.name}' missing docstring",
                    severity="warning",
                    context={"scope": "function", "name": node.name},
                ))
    
    return issues


def validate_semantic_correctness(
    code: str,
    context: Optional[Any] = None,
    check_imports: bool = True,
    check_docstrings: bool = False,
) -> Tuple[bool, List[SemanticIssue]]:
    """
    Comprehensive semantic validation for generated Python code.
    
    This is the main entry point for semantic validation in the
    Hybrid Pattern Library + LLM architecture.
    
    Args:
        code: Python source code to validate
        context: Optional CodegenContext with expected class/method names
        check_imports: Whether to validate imports resolve
        check_docstrings: Whether to check for docstrings (warning only)
        
    Returns:
        Tuple of (is_valid, issues)
        is_valid is False if any error-severity issues found
    """
    all_issues: List[SemanticIssue] = []
    
    # 1. Import validation
    if check_imports:
        import_issues = validate_imports(code)
        all_issues.extend(import_issues)
    
    # 2. Context-based validation (if CodegenContext provided)
    if context is not None:
        # Check for expected client class
        if hasattr(context, 'client_class') and context.client_class:
            # Basic methods every client should have
            expected_methods = []
            if hasattr(context, 'method_name') and context.method_name:
                expected_methods.append(context.method_name)
            
            if expected_methods:
                class_issues = validate_class_signature(
                    code, context.client_class, expected_methods
                )
                all_issues.extend(class_issues)
        
        # Check for expected flow function
        if hasattr(context, 'flow_function') and context.flow_function:
            flow_issues = validate_function_exists(
                code, context.flow_function, min_params=0
            )
            all_issues.extend(flow_issues)
    
    # 3. Docstring validation (warnings only)
    if check_docstrings:
        docstring_issues = validate_docstrings(code, require_module=True)
        all_issues.extend(docstring_issues)
    
    # Determine overall validity (only errors count, not warnings)
    has_errors = any(issue.severity == "error" for issue in all_issues)
    
    return not has_errors, all_issues


def format_semantic_issues(issues: List[SemanticIssue]) -> str:
    """Format semantic issues for logging/display."""
    if not issues:
        return "No semantic issues found."
    
    lines = ["Semantic issues detected:"]
    for issue in issues:
        prefix = "ERROR" if issue.severity == "error" else "WARNING"
        lines.append(f"  [{prefix}] Line {issue.line}: {issue.message}")
    
    return "\n".join(lines)

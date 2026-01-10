#!/usr/bin/env python3
"""
AST-Based Security Gate for HTTP Client

This script uses Python's AST module to reliably detect forbidden patterns
in http_client.py. Unlike grep-based checks, AST scanning:

1. Distinguishes code from comments/docstrings
2. Understands Python syntax (not just string matching)
3. Won't false-positive on documentation explaining what NOT to do
4. Is immune to string escaping and formatting variations

Usage:
    python scripts/ast_security_gate.py

Exit codes:
    0 - All gates passed
    1 - Security violation detected

This is called by scripts/ci_hardening_gates.sh as a more robust
replacement for grep-based INVARIANT 3 checks.
"""

import ast
import sys
from pathlib import Path
from typing import List, Tuple, Set


class ResponseAttributeVisitor(ast.NodeVisitor):
    """
    AST visitor that detects forbidden attribute access patterns.
    
    INVARIANT 3: NEVER access response.text or response.content in http_client.py
    
    This visitor finds:
    - Any `.text` attribute access on a variable that could be a response
    - Any `.content` attribute access on a variable that could be a response
    
    It ignores:
    - String literals (documentation, error messages)
    - Comments (handled by AST automatically)
    - Docstrings (function/class/module docstrings)
    """
    
    # Variable names that likely hold httpx Response objects
    RESPONSE_NAMES = frozenset({
        'response', 'resp', 'r', 'res', 'http_response', 'result',
    })
    
    # Forbidden attributes on response objects
    FORBIDDEN_ATTRS = frozenset({
        'text',     # response.text - materializes full body
        'content',  # response.content - materializes full body as bytes
    })
    
    def __init__(self):
        self.violations: List[Tuple[int, int, str]] = []  # (line, col, message)
        self._in_docstring = False
        
    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check for forbidden attribute access patterns."""
        # Skip if we're accessing a forbidden attribute
        if node.attr not in self.FORBIDDEN_ATTRS:
            self.generic_visit(node)
            return
        
        # Check if the base is a Name node (variable reference)
        if isinstance(node.value, ast.Name):
            var_name = node.value.id.lower()
            
            # Check if variable name suggests it's a response object
            if var_name in self.RESPONSE_NAMES:
                self.violations.append((
                    node.lineno,
                    node.col_offset,
                    f"INVARIANT 3 VIOLATION: {node.value.id}.{node.attr} "
                    f"detected. This materializes the full response body, "
                    f"defeating the streaming byte cap. Use aiter_bytes() instead."
                ))
        
        self.generic_visit(node)
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Track when we're in a function (to handle docstrings)."""
        # Skip docstring (first statement if it's a string)
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]  # Skip docstring
        
        for child in body:
            self.visit(child)
        
        # Don't call generic_visit to avoid re-visiting children
        
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Handle async function definitions same as regular functions."""
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]
        
        for child in body:
            self.visit(child)
    
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Handle class definitions."""
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body = body[1:]
        
        for child in body:
            self.visit(child)


class DirectHttpxUsageVisitor(ast.NodeVisitor):
    """
    AST visitor that detects direct httpx usage outside http_client.py.
    
    This catches:
    - import httpx
    - from httpx import ...
    - httpx.AsyncClient()
    - httpx.Client()
    - httpx.get(), httpx.post(), etc.
    """
    
    def __init__(self):
        self.violations: List[Tuple[int, int, str]] = []
        self._httpx_imported = False
        
    def visit_Import(self, node: ast.Import) -> None:
        """Check for 'import httpx'."""
        for alias in node.names:
            if alias.name == 'httpx':
                self.violations.append((
                    node.lineno,
                    node.col_offset,
                    f"Direct 'import httpx' found. Use hardened_fetch() from "
                    f"http_client.py instead."
                ))
                self._httpx_imported = True
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Check for 'from httpx import ...'."""
        if node.module and ('httpx' in node.module or node.module == 'httpx'):
            self.violations.append((
                node.lineno,
                node.col_offset,
                f"Direct 'from {node.module} import ...' found. Use hardened_fetch() "
                f"from http_client.py instead."
            ))
            self._httpx_imported = True
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call) -> None:
        """Check for httpx.AsyncClient(), httpx.get(), etc."""
        if isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                if node.func.value.id == 'httpx':
                    self.violations.append((
                        node.lineno,
                        node.col_offset,
                        f"Direct httpx.{node.func.attr}() call found. Use hardened_fetch() "
                        f"from http_client.py instead."
                    ))
        self.generic_visit(node)


def check_http_client(http_client_path: Path) -> List[str]:
    """
    Check http_client.py for INVARIANT 3 violations.
    
    Returns list of error messages (empty if clean).
    """
    source = http_client_path.read_text()
    
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"Syntax error in {http_client_path}: {e}"]
    
    visitor = ResponseAttributeVisitor()
    visitor.visit(tree)
    
    errors = []
    for line, col, msg in visitor.violations:
        errors.append(f"{http_client_path}:{line}:{col}: {msg}")
    
    return errors


def check_discovery_module(discovery_path: Path) -> List[str]:
    """
    Check all discovery/*.py files (except allowed modules) for direct httpx usage.
    
    Allowed modules:
    - http_client.py: The hardened HTTP module itself
    - web_search_providers.py: Uses httpx with same hardened settings (trust_env=False)
    
    Returns list of error messages (empty if clean).
    """
    errors = []
    
    # Files allowed to use httpx directly (with hardened settings)
    ALLOWED_HTTPX_FILES = frozenset({
        "http_client.py",
        "web_search_providers.py",  # Uses trust_env=False, explicit timeouts
    })
    
    for py_file in discovery_path.glob("*.py"):
        if py_file.name in ALLOWED_HTTPX_FILES:
            continue  # These files are allowed to use httpx
        if py_file.name.startswith("__"):
            continue  # Skip __pycache__, __init__.py is OK
        
        try:
            source = py_file.read_text()
            tree = ast.parse(source)
        except SyntaxError as e:
            errors.append(f"Syntax error in {py_file}: {e}")
            continue
        
        visitor = DirectHttpxUsageVisitor()
        visitor.visit(tree)
        
        for line, col, msg in visitor.violations:
            errors.append(f"{py_file}:{line}:{col}: {msg}")
    
    return errors


def check_required_patterns(http_client_path: Path) -> List[str]:
    """
    Check that http_client.py contains required security patterns.
    
    This is a positive check (presence of patterns) rather than
    negative check (absence of patterns).
    
    Returns list of error messages (empty if all patterns present).
    """
    source = http_client_path.read_text()
    
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [f"Syntax error in {http_client_path}: {e}"]
    
    errors = []
    
    # Check for aiter_bytes usage (INVARIANT 3 positive check)
    has_aiter_bytes = "aiter_bytes" in source
    if not has_aiter_bytes:
        errors.append(
            f"{http_client_path}: Missing aiter_bytes() call. "
            f"INVARIANT 3 requires streaming body reads."
        )
    
    # Check for trust_env=False (INVARIANT 5)
    has_trust_env_false = "trust_env=False" in source
    if not has_trust_env_false:
        errors.append(
            f"{http_client_path}: Missing trust_env=False. "
            f"INVARIANT 5 requires blocking proxy environment variables."
        )
    
    # Check for follow_redirects=False (INVARIANT 1)
    has_follow_redirects_false = "follow_redirects=False" in source
    if not has_follow_redirects_false:
        errors.append(
            f"{http_client_path}: Missing follow_redirects=False. "
            f"INVARIANT 1 requires manual redirect loop."
        )
    
    # Check for asyncio.timeout (INVARIANT 6 wall-clock budget)
    has_asyncio_timeout = "asyncio.timeout" in source
    if not has_asyncio_timeout:
        errors.append(
            f"{http_client_path}: Missing asyncio.timeout(). "
            f"INVARIANT 6 requires wall-clock timeout budget."
        )
    
    return errors


def main() -> int:
    """
    Run all AST-based security gates.
    
    Returns 0 if all gates pass, 1 if any violations found.
    """
    # Find project root
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    
    discovery_path = project_root / "src" / "integration_coworker" / "discovery"
    http_client_path = discovery_path / "http_client.py"
    
    if not discovery_path.exists():
        print(f"ERROR: Discovery module not found at {discovery_path}")
        return 1
    
    if not http_client_path.exists():
        print(f"ERROR: http_client.py not found at {http_client_path}")
        return 1
    
    print("=== AST-Based Security Gates ===")
    print()
    
    all_errors = []
    
    # Gate 1: Check http_client.py for INVARIANT 3 violations
    print("Gate AST-1: Checking http_client.py for response.text/content access...")
    errors = check_http_client(http_client_path)
    if errors:
        all_errors.extend(errors)
        print("  FAILED")
        for e in errors:
            print(f"    {e}")
    else:
        print("  PASSED")
    
    # Gate 2: Check discovery module for direct httpx usage
    print("Gate AST-2: Checking discovery/ for direct httpx usage...")
    errors = check_discovery_module(discovery_path)
    if errors:
        all_errors.extend(errors)
        print("  FAILED")
        for e in errors:
            print(f"    {e}")
    else:
        print("  PASSED")
    
    # Gate 3: Check for required positive patterns
    print("Gate AST-3: Checking http_client.py for required security patterns...")
    errors = check_required_patterns(http_client_path)
    if errors:
        all_errors.extend(errors)
        print("  FAILED")
        for e in errors:
            print(f"    {e}")
    else:
        print("  PASSED")
    
    print()
    if all_errors:
        print(f"=== AST security gates FAILED ({len(all_errors)} violations) ===")
        return 1
    else:
        print("=== All AST security gates PASSED ===")
        return 0


if __name__ == "__main__":
    sys.exit(main())

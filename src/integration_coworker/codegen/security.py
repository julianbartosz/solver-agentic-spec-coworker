"""
Security validation for generated code.

Uses AST analysis to detect potentially dangerous patterns.

v2: Implements FT-SEC-003 from V2 Implementation Plan Section 3.7
v1.1: Added fix_code_style for ruff-based auto-formatting (FT-008)
"""
import ast
import logging
import os
import subprocess
import tempfile
from typing import List, Set, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SecurityViolation:
    """A detected security issue in generated code."""
    line: int
    column: int
    pattern: str
    description: str
    severity: str  # "error" or "warning"


# Forbidden function names
FORBIDDEN_FUNCTIONS: Set[str] = {
    "exec",
    "eval",
    "compile",
    "__import__",
    "open",  # Warning only - sometimes needed
}

# Forbidden module.function patterns
FORBIDDEN_CALLS: Set[Tuple[str, str]] = {
    ("os", "system"),
    ("os", "popen"),
    ("os", "spawn"),
    ("os", "spawnl"),
    ("os", "spawnle"),
    ("os", "spawnlp"),
    ("os", "spawnlpe"),
    ("os", "spawnv"),
    ("os", "spawnve"),
    ("os", "spawnvp"),
    ("os", "spawnvpe"),
    ("subprocess", "call"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("pickle", "loads"),
    ("pickle", "load"),
    ("marshal", "loads"),
    ("marshal", "load"),
    ("ctypes", "CDLL"),
    ("ctypes", "cdll"),
}

# Dangerous attribute accesses
FORBIDDEN_ATTRS: Set[str] = {
    "__code__",
    "__globals__",
    "__builtins__",
    "__subclasses__",
    "__mro__",
    "__bases__",
}


class SecurityVisitor(ast.NodeVisitor):
    """
    AST visitor that detects security violations.
    """
    
    def __init__(self):
        self.violations: List[SecurityViolation] = []
        self._imported_modules: dict[str, str] = {}  # alias -> module
    
    def visit_Import(self, node: ast.Import):
        """Track imported modules."""
        for alias in node.names:
            name = alias.asname or alias.name
            self._imported_modules[name] = alias.name
        self.generic_visit(node)
    
    def visit_ImportFrom(self, node: ast.ImportFrom):
        """Track from imports."""
        if node.module:
            for alias in node.names:
                name = alias.asname or alias.name
                self._imported_modules[name] = f"{node.module}.{alias.name}"
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call):
        """Check function calls for forbidden patterns."""
        # Direct function calls: exec(), eval(), etc.
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name in FORBIDDEN_FUNCTIONS:
                severity = "warning" if func_name == "open" else "error"
                self.violations.append(SecurityViolation(
                    line=node.lineno,
                    column=node.col_offset,
                    pattern=func_name,
                    description=f"Forbidden function call: {func_name}()",
                    severity=severity,
                ))
        
        # Attribute calls: os.system(), subprocess.run(), etc.
        elif isinstance(node.func, ast.Attribute):
            if isinstance(node.func.value, ast.Name):
                module_alias = node.func.value.id
                method_name = node.func.attr
                
                # Resolve alias to actual module
                module = self._imported_modules.get(module_alias, module_alias)
                
                if (module, method_name) in FORBIDDEN_CALLS:
                    self.violations.append(SecurityViolation(
                        line=node.lineno,
                        column=node.col_offset,
                        pattern=f"{module}.{method_name}",
                        description=f"Forbidden call: {module}.{method_name}()",
                        severity="error",
                    ))
        
        self.generic_visit(node)
    
    def visit_Attribute(self, node: ast.Attribute):
        """Check for forbidden attribute access."""
        if node.attr in FORBIDDEN_ATTRS:
            self.violations.append(SecurityViolation(
                line=node.lineno,
                column=node.col_offset,
                pattern=node.attr,
                description=f"Forbidden attribute access: {node.attr}",
                severity="error",
            ))
        self.generic_visit(node)


def validate_code_security(
    code: str,
    allow_subprocess: bool = False,
    allow_file_io: bool = True,
) -> Tuple[bool, List[SecurityViolation]]:
    """
    Validate code for security issues.
    
    Args:
        code: Python source code to validate
        allow_subprocess: If True, allow subprocess calls (for CLI integrations)
        allow_file_io: If True, allow open() calls
        
    Returns:
        Tuple of (is_valid, violations)
        is_valid is False if any "error" severity violations found
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return False, [SecurityViolation(
            line=e.lineno or 0,
            column=e.offset or 0,
            pattern="SyntaxError",
            description=str(e),
            severity="error",
        )]
    
    visitor = SecurityVisitor()
    visitor.visit(tree)
    
    # Filter based on options
    filtered = []
    for v in visitor.violations:
        # Skip subprocess if allowed
        if allow_subprocess and "subprocess" in v.pattern:
            continue
        # Downgrade open() to warning if file IO allowed
        if allow_file_io and v.pattern == "open":
            v.severity = "warning"
            filtered.append(v)
            continue
        
        filtered.append(v)
    
    has_errors = any(v.severity == "error" for v in filtered)
    return not has_errors, filtered


def format_violations(violations: List[SecurityViolation]) -> str:
    """Format violations as human-readable string."""
    if not violations:
        return "No security issues found."
    
    lines = ["Security violations detected:"]
    for v in violations:
        lines.append(f"  [{v.severity.upper()}] Line {v.line}: {v.description}")
    return "\n".join(lines)


# =============================================================================
# V1.1: Code Style Fixing (FT-008 - Strict Codegen Mode)
# =============================================================================

def fix_code_style(content: str) -> str:
    """
    Auto-fix code style issues using ruff.
    
    This function attempts to fix common style issues (unused imports,
    formatting, etc.) using ruff's --fix flag. If ruff is not installed
    or fails, the original content is returned unchanged.
    
    Args:
        content: Python source code to fix
        
    Returns:
        Fixed Python source code, or original if fixing fails
    """
    if not content or not content.strip():
        return content
    
    # Write to temp file
    with tempfile.NamedTemporaryFile(
        mode='w',
        suffix='.py',
        delete=False,
        encoding='utf-8'
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    
    try:
        # Run ruff check --fix (auto-fix safe issues)
        result = subprocess.run(
            ['ruff', 'check', '--fix', '--select', 'I,F401,W', tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        # Also run ruff format for consistent formatting
        subprocess.run(
            ['ruff', 'format', tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        # Read back fixed content
        with open(tmp_path, 'r', encoding='utf-8') as f:
            fixed_content = f.read()
        
        logger.debug(f"Code style fixed via ruff (exit={result.returncode})")
        return fixed_content
        
    except FileNotFoundError:
        # Ruff not installed - graceful degradation
        logger.debug("ruff not installed, skipping code style fixing")
        return content
    except subprocess.TimeoutExpired:
        logger.warning("ruff timed out, returning original content")
        return content
    except Exception as e:
        logger.warning(f"Code style fixing failed: {e}")
        return content
    finally:
        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def validate_syntax(code: str) -> Tuple[bool, str]:
    """
    Validate Python code syntax using ast.parse.
    
    Args:
        code: Python source code to validate
        
    Returns:
        Tuple of (is_valid, error_message)
        error_message is empty string if valid
    """
    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"Syntax error at line {e.lineno}: {e.msg}"


# Alias for backwards compatibility with V1.1 strict codegen imports
check_syntax = validate_syntax

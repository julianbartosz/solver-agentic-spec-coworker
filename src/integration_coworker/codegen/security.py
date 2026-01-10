"""
Security validation for generated code.

Uses AST analysis to detect potentially dangerous patterns.

v2: Implements FT-SEC-003 from V2 Implementation Plan Section 3.7
v1.1: Added fix_code_style for ruff-based auto-formatting (FT-008)
"""
import ast
import logging
import os
import re
import subprocess
import tempfile
from typing import List, Optional, Set, Tuple
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
        import sys
        
        # 1. Run ruff check --fix (linting)
        # Use sys.executable to ensure we use the environment's ruff module
        # Select E501 explicitly to encourage line length fixes if possible via lint
        check_cmd = [sys.executable, '-m', 'ruff', 'check', '--fix', '--select', 'I,F401,F841,W,E501', tmp_path]
        
        # Fallback to binary 'ruff' if module run fails
        try:
             result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=30)
        except Exception:
             check_cmd = ['ruff', 'check', '--fix', '--select', 'I,F401,F841,W,E501', tmp_path]
             result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=30)
        
        # 2. Run ruff format (formatting)
        # This handles the bulk of line wrapping
        format_cmd = [sys.executable, '-m', 'ruff', 'format', tmp_path]
        try:
             subprocess.run(format_cmd, capture_output=True, text=True, timeout=30)
        except Exception:
             format_cmd = ['ruff', 'format', tmp_path]
             subprocess.run(format_cmd, capture_output=True, text=True, timeout=30)
        
        # Read back fixed content
        with open(tmp_path, 'r', encoding='utf-8') as f:
            fixed_content = f.read()
            
        # 3. Simple Docstring Line Length Sanity Check (Fallback)
        # Ruff sometimes skips docstrings. If lines are > 120 chars, try to wrap simple docstrings.
        lines = fixed_content.split('\n')
        modified = False
        for i, line in enumerate(lines):
            if len(line) > 120 and (line.strip().startswith('"""') or line.strip().startswith("'''")):
                 # Very naive wrapping for one-line docstrings that are too long
                 stripped = line.strip()
                 indent = line[:line.find(stripped)]
                 content = stripped[3:-3] if stripped.endswith('"""') or stripped.endswith("'''") else stripped[3:]
                 if len(content) > 100 and ' ' in content:
                     # Wrap at space
                     mid = len(content) // 2
                     space_idx = content.find(' ', mid)
                     if space_idx != -1:
                        new_line = f'{indent}"""{content[:space_idx]}\n{indent}{content[space_idx+1:]}"""'
                        lines[i] = new_line
                        modified = True
        
        if modified:
             fixed_content = '\n'.join(lines)
        
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


# =============================================================================
# V32-001: Syntax Self-Repair
# =============================================================================
# LLM-generated code can have subtle syntax errors that pass ast.parse() but 
# fail ruff's stricter parsing, OR vice versa (ruff accepts but ast doesn't).
# This provides a two-pass repair mechanism:
#   1. First try to fix via ruff --fix (catches formatting issues)
#   2. If still broken, try LLM-based syntax repair
# =============================================================================

def repair_syntax_errors(code: str, error_message: str = "") -> Tuple[str, bool, str]:
    """
    Attempt to repair syntax errors in Python code.
    
    V32-001: This is the primary syntax repair entry point. It uses a multi-strategy
    approach:
    1. Strip trailing whitespace (W293 fix)
    2. Fix duplicate __init__ methods (V40-003)
    3. ruff format --fix (handles indentation, whitespace issues)
    4. Common pattern fixes (orphaned return, missing colons)
    5. Indentation normalization
    
    Args:
        code: Python code with potential syntax errors
        error_message: Optional error message to help guide repair
        
    Returns:
        Tuple of (repaired_code, was_repaired, repair_method)
        - repaired_code: The code after repair attempt
        - was_repaired: True if repairs were made and code now validates
        - repair_method: Description of what fixed it (or 'failed' if not)
    """
    original_code = code
    
    # Strategy 0: Always strip trailing whitespace from blank lines (W293 fix)
    code = _strip_trailing_whitespace(code)
    
    # Strategy 0.5: Fix duplicate __init__ methods (V40-003)
    code = _fix_duplicate_init_methods(code)
    
    # Strategy 1: Try ruff format first (fastest, safest)
    try:
        formatted = fix_code_style(code)
        is_valid, _ = validate_syntax(formatted)
        if is_valid:
            if formatted != original_code:
                return formatted, True, "ruff_format"
            return formatted, False, "already_valid"
    except Exception as e:
        logger.debug(f"[V32-001] ruff format failed: {e}")
    
    # Strategy 2: Fix common LLM syntax patterns
    repaired = _fix_common_syntax_patterns(code, error_message)
    if repaired != code:
        is_valid, _ = validate_syntax(repaired)
        if is_valid:
            return repaired, True, "pattern_fix"
    
    # Strategy 3: Try to normalize indentation
    indent_fixed = _normalize_indentation(code)
    if indent_fixed != code:
        is_valid, _ = validate_syntax(indent_fixed)
        if is_valid:
            return indent_fixed, True, "indent_normalize"
    
    # Strategy 4: Combine ruff + indent normalization
    try:
        combined = fix_code_style(indent_fixed)
        is_valid, _ = validate_syntax(combined)
        if is_valid:
            return combined, True, "combined_fix"
    except Exception:
        pass
    
    # All strategies failed
    return original_code, False, "failed"


def _strip_trailing_whitespace(code: str) -> str:
    """
    V40-003: Strip trailing whitespace from all lines, especially blank lines.
    
    This fixes ruff W293: Blank line contains whitespace.
    LLMs commonly generate code with trailing spaces on blank lines.
    """
    lines = code.split('\n')
    return '\n'.join(line.rstrip() for line in lines)


def _fix_duplicate_init_methods(code: str) -> str:
    """
    V40-003: Fix duplicate __init__ methods in class definitions.
    
    LLMs sometimes merge two class templates, resulting in:
    - Two __init__ methods in the same class
    - Mixed docstrings and code
    - Policy initialization code at wrong indentation
    
    This function:
    1. Detects classes with multiple __init__ methods
    2. Keeps only the first complete __init__
    3. Removes orphaned code between __init__ methods
    """
    lines = code.split('\n')
    fixed_lines = []
    
    in_class = False
    class_indent = 0
    init_count = 0
    init_start_line = -1
    skip_until_next_method = False
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        current_indent = len(line) - len(line.lstrip()) if line.strip() else 0
        
        # Track class definitions
        if stripped.startswith('class ') and stripped.endswith(':'):
            in_class = True
            class_indent = current_indent
            init_count = 0
            skip_until_next_method = False
            fixed_lines.append(line)
            i += 1
            continue
        
        # Reset on dedent past class level
        if in_class and stripped and current_indent <= class_indent and not stripped.startswith('class '):
            in_class = False
            init_count = 0
            skip_until_next_method = False
        
        # Track __init__ methods within a class
        if in_class and re.match(r'\s*def\s+__init__\s*\(', line):
            init_count += 1
            if init_count == 1:
                # First __init__ - keep it
                init_start_line = i
                fixed_lines.append(line)
            else:
                # Duplicate __init__ - skip this and following lines until next method
                logger.debug(f"[V40-003] Removing duplicate __init__ at line {i+1}")
                skip_until_next_method = True
                i += 1
                continue
        
        # Skip lines until we hit another method definition
        if skip_until_next_method:
            # Check if we've reached a new method (def at class method indent level)
            method_indent = class_indent + 4  # Assuming 4-space indent
            if re.match(r'\s*def\s+', line) and current_indent == method_indent:
                if not re.match(r'\s*def\s+__init__\s*\(', line):
                    # Different method - stop skipping
                    skip_until_next_method = False
                    fixed_lines.append(line)
            # Also stop if we exit the class
            elif stripped and current_indent <= class_indent:
                skip_until_next_method = False
                fixed_lines.append(line)
            i += 1
            continue
        
        fixed_lines.append(line)
        i += 1
    
    return '\n'.join(fixed_lines)


def _fix_concatenated_statements(code: str) -> str:
    """
    V44-001: Fix statements concatenated on the same line.
    
    LLMs sometimes generate code where multiple statements are on one line:
    - "from X import Y from Z import W"  -> separate lines
    - "import X import Y"                 -> separate lines
    - "class X: def foo(self):"           -> separate lines
    - "x = 1 y = 2"                        -> separate lines (after statement ends)
    
    This is a pre-AST fix that must run BEFORE other syntax fixes because
    concatenated statements cause parse failures that block all other fixes.
    """
    lines = code.split('\n')
    fixed_lines = []
    
    for line in lines:
        # Check for multiple 'from X import' patterns on same line
        # Pattern: "from X import Y from Z import W" 
        if line.count('from ') > 1 and ' import ' in line:
            # Split by 'from' and rejoin with newlines
            parts = re.split(r'(?<!\w)(from\s+)', line)
            current_line = ''
            result_lines = []
            
            for i, part in enumerate(parts):
                if part.strip().startswith('from'):
                    # Start of new import - save previous if exists
                    if current_line.strip():
                        result_lines.append(current_line.rstrip())
                    current_line = part
                else:
                    current_line += part
            
            if current_line.strip():
                result_lines.append(current_line.rstrip())
            
            # Get the indentation from the original line
            indent_match = re.match(r'^(\s*)', line)
            indent = indent_match.group(1) if indent_match else ''
            
            # Add proper indentation to each line
            for i, result_line in enumerate(result_lines):
                if result_line.strip():
                    if i == 0:
                        fixed_lines.append(result_line)
                    else:
                        fixed_lines.append(indent + result_line.strip())
            
            if result_lines:
                logger.debug(f"[V44-001] Split {len(result_lines)} concatenated imports")
                continue
        
        # Check for multiple 'import X' statements on same line (not 'from X import')
        # Pattern: "import X import Y" (rare but possible)
        if line.count('import ') > 1 and 'from ' not in line:
            parts = re.split(r'\b(import\s+)', line)
            if len(parts) > 2:  # Actually has multiple imports
                indent_match = re.match(r'^(\s*)', line)
                indent = indent_match.group(1) if indent_match else ''
                
                current_import = ''
                for part in parts:
                    if part.strip().startswith('import'):
                        if current_import.strip():
                            fixed_lines.append(current_import.rstrip())
                        current_import = indent + part
                    else:
                        current_import += part
                
                if current_import.strip():
                    fixed_lines.append(current_import.rstrip())
                
                logger.debug("[V44-001] Split concatenated import statements")
                continue
        
        # Check for class definition followed by method on same line
        # Pattern: "class X: def foo(self):"
        class_def_match = re.match(r'^(\s*)(class\s+\w+[^:]*:)\s*(def\s+.+)$', line)
        if class_def_match:
            indent = class_def_match.group(1)
            class_part = class_def_match.group(2)
            method_part = class_def_match.group(3)
            fixed_lines.append(indent + class_part)
            fixed_lines.append(indent + '    ' + method_part)  # Indent method inside class
            logger.debug("[V44-001] Split class and method definition")
            continue
        
        # Check for multiple assignments on same line without semicolons
        # Pattern: "x = 1 y = 2" (not "x = 1; y = 2" which is valid but unusual)
        # Be careful not to match things like "x = y = 1" (chained assignment)
        assignment_matches = list(re.finditer(r'(\w+)\s*=\s*(?![=])', line))
        if len(assignment_matches) > 1:
            # Check if they're separated by proper syntax or if it's chained
            # Chained: "x = y = 1" has assignments next to each other
            # Concatenated: "x = 1 y = 2" has value between assignments
            is_concatenated = False
            for i in range(len(assignment_matches) - 1):
                m1 = assignment_matches[i]
                m2 = assignment_matches[i + 1]
                between = line[m1.end():m2.start()]
                # If there's no comma/semicolon between assignments, it's concatenated
                if between.strip() and ',' not in between and ';' not in between:
                    # Check if it's a value (not part of chained assignment)
                    if not between.strip().startswith('='):
                        is_concatenated = True
                        break
            
            if is_concatenated:
                # Try to split - this is tricky, so be conservative
                indent_match = re.match(r'^(\s*)', line)
                indent = indent_match.group(1) if indent_match else ''
                
                # Simple heuristic: split at pattern "value identifier ="
                # e.g., "x = 1 y = 2" -> split before "y ="
                split_line = re.sub(r'(\S)\s+(\w+\s*=\s*)', r'\1\n' + indent + r'\2', line)
                if '\n' in split_line:
                    for sub_line in split_line.split('\n'):
                        if sub_line.strip():
                            fixed_lines.append(sub_line)
                    logger.debug("[V44-001] Split concatenated assignments")
                    continue
        
        # No fixes needed, keep original line
        fixed_lines.append(line)
    
    return '\n'.join(fixed_lines)


def _fix_common_syntax_patterns(code: str, error_message: str = "") -> str:
    """
    Fix common LLM-generated syntax patterns that cause errors.
    
    V40-002: Enhanced with more robust pattern fixes based on production failures.
    V44-001: Added concatenated statement separation fix.
    
    Common patterns:
    - Concatenated import statements on same line [V44-001]
    - Double-dot imports (from clients..module) [V33-001]
    - Orphaned 'return' statements after method (indentation wrong)
    - Missing colons after def/class/if/for/while
    - Inconsistent indentation (tabs vs spaces)
    - Trailing incomplete statements
    - Unclosed strings (f-strings, multi-line strings)
    - Unbalanced brackets/parentheses
    - Invalid escape sequences in strings
    """
    # V44-001: Fix concatenated statements on same line
    # LLMs sometimes generate: "from X import Y from Z import W" on one line
    # This must be split into separate lines FIRST (before other fixes)
    code = _fix_concatenated_statements(code)
    
    # V33-001: Fix double-dot imports in non-root layouts
    # Pattern: "from clients..twilio_messaging" -> "from clients.twilio_messaging"
    # This happens when LLM incorrectly generates module paths with double dots
    code = re.sub(
        r'\bfrom\s+([\w.]+)\.\.(\w+)',  # Match 'from xxx..yyy'
        r'from \1.\2',  # Replace with 'from xxx.yyy'
        code
    )
    # Also fix triple-dots or more (defensive)
    while '..' in code and re.search(r'\bfrom\s+[\w.]*\.\.', code):
        code = re.sub(r'(\bfrom\s+[\w.]*)\.\.([\w.])', r'\1.\2', code)
    
    # V40-002: Fix invalid escape sequences in strings
    # Common LLM error: \s, \d, etc. in regular strings instead of raw strings
    code = _fix_invalid_escape_sequences(code)
    
    # V40-002: Fix unbalanced quotes (especially in f-strings)
    code = _fix_unbalanced_quotes(code)
    
    lines = code.split('\n')
    fixed_lines = []
    
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        
        # Fix 1: Orphaned return at column 0 that should be indented
        # This happens when LLM generates:
        #   def method(self):
        #       do_something()
        #   return result  <- Should be indented inside method
        if stripped.startswith('return ') and not line.startswith(' ') and not line.startswith('\t'):
            # Look back for a function def to determine correct indent
            correct_indent = _find_expected_indent(fixed_lines)
            if correct_indent:
                line = correct_indent + stripped
                logger.debug(f"[V32-001] Fixed orphaned return statement")
        
        # Fix 2: Missing colon after def/class/if/for/while
        if re.match(r'^(\s*)(def|class|if|elif|else|for|while|try|except|finally|with|async def)\s+', line):
            if not stripped.endswith(':') and not stripped.endswith('\\'):
                # Check if next line is indented (body follows)
                if i + 1 < len(lines) and lines[i + 1].startswith((' ', '\t')):
                    line = line.rstrip() + ':'
                    logger.debug(f"[V32-001] Added missing colon after {stripped.split()[0]}")
        
        # Fix 3: Remove completely empty/broken lines that cause syntax errors
        # (lines with only whitespace or incomplete statements)
        if stripped in ('return', 'yield', 'raise', 'pass', 'break', 'continue'):
            # These are valid on their own, keep them
            pass
        elif stripped.endswith('=') or stripped.endswith('==') or stripped.endswith('('):
            # Incomplete statement - this will cause syntax errors
            # Try to merge with next line
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith('#'):
                    line = line.rstrip() + ' ' + next_line
                    i += 1  # Skip next line
                    logger.debug(f"[V32-001] Merged incomplete statement")
        
        # V40-002 Fix 4: Handle truncated f-strings
        # LLMs sometimes truncate f-strings mid-expression
        if 'f"' in line or "f'" in line:
            line = _fix_truncated_fstring(line)
        
        fixed_lines.append(line)
        i += 1
    
    return '\n'.join(fixed_lines)


def _fix_invalid_escape_sequences(code: str) -> str:
    """
    V40-002: Fix invalid escape sequences by converting to raw strings or escaping.
    
    Common LLM errors:
    - Using regex patterns like \s, \d, \w in regular strings
    - Using file paths with backslashes on Windows
    """
    # Find lines with potential invalid escape sequences in string literals
    lines = code.split('\n')
    fixed_lines = []
    
    # Regex metacharacters that are invalid escape sequences in Python strings
    invalid_escapes = [r'\\s', r'\\d', r'\\w', r'\\S', r'\\D', r'\\W', r'\\b', r'\\B', r'\\A', r'\\Z']
    
    for line in lines:
        # Skip already-raw strings or byte strings
        if re.search(r'\br["\']|\br"""|\br\'\'\'', line):
            fixed_lines.append(line)
            continue
        
        # Check for invalid escapes in non-raw strings
        has_invalid = any(esc.replace('\\\\', '\\') in line for esc in invalid_escapes)
        
        if has_invalid:
            # Convert string literals containing regex to raw strings
            # Pattern: find quoted strings with regex metacharacters
            def make_raw(match):
                quote = match.group(1)
                content = match.group(2)
                # Check if this string has regex-like content
                if any(meta in content for meta in ['\\s', '\\d', '\\w', '\\S', '\\D', '\\W']):
                    return f'r{quote}{content}{quote}'
                return match.group(0)
            
            line = re.sub(r'(["\'])((?:[^"\'\\]|\\.)*?)\1', make_raw, line)
        
        fixed_lines.append(line)
    
    return '\n'.join(fixed_lines)


def _fix_unbalanced_quotes(code: str) -> str:
    """
    V40-002: Fix unbalanced quotes, especially in f-strings.
    
    Common LLM errors:
    - Truncated f-strings: f"Hello {name  <- missing closing quote
    - Nested quotes issues: f"Value: {data["key"]}"  <- should use single quotes inside
    """
    lines = code.split('\n')
    fixed_lines = []
    in_multiline_string = False
    multiline_quote = None
    
    for line in lines:
        # Track multi-line strings
        if not in_multiline_string:
            if '"""' in line or "'''" in line:
                triple_double = line.count('"""')
                triple_single = line.count("'''")
                if triple_double % 2 == 1:
                    in_multiline_string = True
                    multiline_quote = '"""'
                elif triple_single % 2 == 1:
                    in_multiline_string = True
                    multiline_quote = "'''"
        else:
            if multiline_quote and multiline_quote in line:
                in_multiline_string = False
                multiline_quote = None
            fixed_lines.append(line)
            continue
        
        # For regular lines, check for unbalanced single-line strings
        if not in_multiline_string:
            # Count quotes (simplified - doesn't handle all edge cases)
            line = _balance_line_quotes(line)
        
        fixed_lines.append(line)
    
    # If we ended in a multiline string, close it
    if in_multiline_string and multiline_quote:
        fixed_lines.append(multiline_quote)
        logger.debug("[V40-002] Closed unclosed multi-line string")
    
    return '\n'.join(fixed_lines)


def _balance_line_quotes(line: str) -> str:
    """
    V40-002: Attempt to balance quotes on a single line.
    
    This is a best-effort fix for common patterns.
    """
    # Check for f-strings with nested quotes issue
    # Pattern: f"...{dict["key"]}..." should be f"...{dict['key']}..."
    if 'f"' in line:
        # Replace inner double quotes with single quotes in f-string expressions
        def fix_fstring_quotes(match):
            prefix = match.group(1)
            content = match.group(2)
            # Replace double quotes inside {} with single quotes
            fixed = re.sub(r'\{([^}]*)"([^}]*)"([^}]*)\}', r"{\1'\2'\3}", content)
            return f'{prefix}"{fixed}"'
        
        line = re.sub(r'(f)"((?:[^"\\]|\\.)*)"', fix_fstring_quotes, line)
    
    if "f'" in line:
        # Replace inner single quotes with double quotes in f-string expressions
        def fix_fstring_quotes_single(match):
            prefix = match.group(1)
            content = match.group(2)
            # Replace single quotes inside {} with double quotes
            fixed = re.sub(r"\{([^}]*)'([^}]*)'([^}]*)\}", r'{\1"\2"\3}', content)
            return f"{prefix}'{fixed}'"
        
        line = re.sub(r"(f)'((?:[^'\\]|\\.)*)'", fix_fstring_quotes_single, line)
    
    return line


def _fix_truncated_fstring(line: str) -> str:
    """
    V40-002: Fix truncated f-strings that are missing closing delimiters.
    
    Common pattern: f"Hello {name  <- LLM truncated mid-expression
    Fix: f"Hello {name}"  <- Add placeholder closing
    """
    # Count open/close braces in f-string
    in_fstring = False
    brace_depth = 0
    quote_char = None
    i = 0
    
    while i < len(line):
        char = line[i]
        
        # Detect f-string start
        if not in_fstring and char == 'f' and i + 1 < len(line) and line[i+1] in '"\'':
            in_fstring = True
            quote_char = line[i+1]
            i += 2
            continue
        
        if in_fstring:
            if char == quote_char and (i == 0 or line[i-1] != '\\'):
                # End of f-string
                in_fstring = False
                quote_char = None
            elif char == '{' and (i == 0 or line[i-1] != '{'):
                brace_depth += 1
            elif char == '}' and (i == 0 or line[i-1] != '}'):
                brace_depth -= 1
        
        i += 1
    
    # If we're still in an f-string with unclosed braces, try to fix
    if in_fstring and brace_depth > 0:
        # Add closing braces and quote
        line = line.rstrip() + '}' * brace_depth + (quote_char or '"')
        logger.debug(f"[V40-002] Fixed truncated f-string: added {brace_depth} braces")
    elif in_fstring and brace_depth == 0:
        # Just missing the closing quote
        line = line.rstrip() + (quote_char or '"')
        logger.debug("[V40-002] Fixed truncated f-string: added closing quote")
    
    return line


def _find_expected_indent(previous_lines: List[str]) -> Optional[str]:
    """
    Find the expected indentation for a statement based on previous lines.
    
    Looks for the most recent function/class/control structure and returns
    its body indentation.
    """
    for line in reversed(previous_lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        
        # Check if this is a block opener
        if stripped.endswith(':'):
            # Get indent of this line + 4 spaces (standard Python indent)
            current_indent = len(line) - len(line.lstrip())
            return ' ' * (current_indent + 4)
        
        # Otherwise, use the indent of this line
        current_indent = len(line) - len(line.lstrip())
        if current_indent > 0:
            return ' ' * current_indent
    
    # Default to 4 spaces if we can't determine
    return '    '


def _normalize_indentation(code: str) -> str:
    """
    Normalize indentation to use consistent 4-space indents.
    
    Handles:
    - Mixed tabs and spaces
    - Inconsistent indent levels
    - Unexpected indentation errors
    """
    # Convert tabs to spaces first
    code = code.replace('\t', '    ')
    
    lines = code.split('\n')
    normalized = []
    expected_indent = 0
    indent_stack = [0]
    
    for line in lines:
        stripped = line.strip()
        
        # Empty lines: preserve
        if not stripped:
            normalized.append('')
            continue
        
        # Comments: preserve indent
        if stripped.startswith('#'):
            normalized.append(' ' * expected_indent + stripped)
            continue
        
        current_indent = len(line) - len(line.lstrip())
        
        # Adjust expected indent based on content
        # Dedent for closing statements
        if stripped.startswith(('return', 'break', 'continue', 'pass', 'raise')) and not stripped.endswith(':'):
            # These should be at current block level
            pass
        elif stripped.startswith(('else:', 'elif ', 'except ', 'except:', 'finally:', 'except(')):
            # These should match the if/try indent level
            if len(indent_stack) > 1:
                expected_indent = indent_stack[-2]
        elif stripped.startswith((')', ']', '}')):
            # Closing brackets might need dedent
            if len(indent_stack) > 1 and current_indent < indent_stack[-1]:
                indent_stack.pop()
                expected_indent = indent_stack[-1]
        
        # Build the normalized line
        normalized.append(' ' * expected_indent + stripped)
        
        # Adjust indent for next line
        if stripped.endswith(':'):
            indent_stack.append(expected_indent + 4)
            expected_indent = indent_stack[-1]
        elif stripped.startswith(('return ', 'return)', 'raise ', 'break', 'continue')):
            # After these, likely back to parent block
            if len(indent_stack) > 1:
                indent_stack.pop()
                expected_indent = indent_stack[-1]
    
    return '\n'.join(normalized)


def validate_and_repair_syntax(code: str) -> Tuple[str, bool]:
    """
    Validate syntax and attempt repair if needed.
    
    V32-001: Convenience function that combines validation + repair.
    Use this as a pre-gate before sandbox execution.
    
    Args:
        code: Python code to validate/repair
        
    Returns:
        Tuple of (code, is_valid)
        - If code was valid, returns original code and True
        - If code was invalid but repaired, returns repaired code and True
        - If code was invalid and couldn't be repaired, returns original and False
    """
    is_valid, error = validate_syntax(code)
    if is_valid:
        return code, True
    
    repaired, was_repaired, method = repair_syntax_errors(code, error)
    if was_repaired:
        logger.info(f"[V32-001] Syntax auto-repair succeeded via {method}")
        return repaired, True
    
    logger.warning(f"[V32-001] Syntax auto-repair failed: {error}")
    return code, False

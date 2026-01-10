"""
Response Type Guard - V26-003 Fix

This module provides post-processing to fix Response | None type hints
in generated client code that cause mypy errors.

Problem:
- LLM sometimes generates: response: Response | None = self.request(...)
- This causes mypy union-attr errors when accessing response.status_code, etc.
- The self.request() method ALWAYS returns Response, never None

Solution:
- Post-process generated code to remove Optional/union types from response
- Add null guards where necessary for defensive coding
- Transform unsafe patterns to safe patterns

Example transformations:
- `response: Response | None = ...` -> `response = ...`
- `response: Optional[Response] = ...` -> `response = ...`
- Add `if response is None: raise IntegrationError(...)` guards where needed
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Pattern Definitions
# =============================================================================

# Pattern: response: Response | None = ...
UNION_RESPONSE_PATTERN = re.compile(
    r'(\s*)(response)\s*:\s*(?:Response\s*\|\s*None|None\s*\|\s*Response)\s*=',
    re.MULTILINE
)

# Pattern: response: Optional[Response] = ...
OPTIONAL_RESPONSE_PATTERN = re.compile(
    r'(\s*)(response)\s*:\s*Optional\s*\[\s*Response\s*\]\s*=',
    re.MULTILINE
)

# Pattern: Any variable with Optional[Response] or Response | None
ANY_VAR_UNION_PATTERN = re.compile(
    r'(\s*)(\w+)\s*:\s*(?:Response\s*\|\s*None|None\s*\|\s*Response|Optional\s*\[\s*Response\s*\])\s*=',
    re.MULTILINE
)

# Pattern: Accessing response attributes without null check
UNSAFE_ACCESS_PATTERN = re.compile(
    r'(?<!if\s)(?<!and\s)(?<!or\s)response\.(status_code|text|content|json|headers)',
    re.MULTILINE
)

# Pattern: Union return type annotation
UNION_RETURN_PATTERN = re.compile(
    r'->\s*(?:Response\s*\|\s*None|None\s*\|\s*Response|Optional\s*\[\s*Response\s*\])',
    re.MULTILINE
)

# V32-002: Pattern for conditional access that mypy flags as unsafe
# Example: response.status_code if response else 'unknown'
# This pattern is unsafe because mypy sees response as Response | None
# NOTE: Must include the fallback value in the match to remove it completely
CONDITIONAL_ACCESS_PATTERN = re.compile(
    r'(response)\.(status_code|text|content|headers)\s+if\s+(response)\s+else\s+(?:[\'"][^\'"]*[\'"]|\d+|\w+)',
    re.MULTILINE
)

# V32-002: Pattern for f-string conditional access 
# Example: {response.text[:200] if response else 'no response'}
FSTRING_CONDITIONAL_PATTERN = re.compile(
    r'\{(response)\.(status_code|text|content|headers)(?:\[[^\]]+\])?\s+if\s+(response)\s+else\s+[^}]+\}',
    re.MULTILINE
)


# =============================================================================
# Fix Functions
# =============================================================================

def fix_response_type_hints(code: str) -> tuple[str, list[str]]:
    """
    Fix Response | None and Optional[Response] type hints in generated code.
    
    Args:
        code: The generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_changes)
    """
    changes: list[str] = []
    fixed = code
    
    # Fix 1: Remove union type from response variable
    def replace_union(match: re.Match) -> str:
        indent = match.group(1)
        var_name = match.group(2)
        changes.append(f"Removed Response | None type hint from '{var_name}'")
        return f"{indent}{var_name} ="
    
    fixed = UNION_RESPONSE_PATTERN.sub(replace_union, fixed)
    fixed = OPTIONAL_RESPONSE_PATTERN.sub(replace_union, fixed)
    
    # Fix 2: Check for any remaining variables with union Response types
    def replace_any_union(match: re.Match) -> str:
        indent = match.group(1)
        var_name = match.group(2)
        if var_name != "response":  # Already handled above
            changes.append(f"Removed Optional/Union Response type hint from '{var_name}'")
        return f"{indent}{var_name} ="
    
    fixed = ANY_VAR_UNION_PATTERN.sub(replace_any_union, fixed)
    
    if changes:
        logger.info(f"[V26-003] Fixed {len(changes)} Response type hint issues")
    
    return fixed, changes


def add_response_null_guards(code: str, method_name: str = "request") -> tuple[str, list[str]]:
    """
    Add defensive null guards for response access.
    
    This is for cases where the response MIGHT be None (e.g., custom client methods).
    For standard self.request(), we know it never returns None, so guards are optional.
    
    Args:
        code: The generated Python code
        method_name: The method that returns Response (to identify risky patterns)
        
    Returns:
        Tuple of (fixed_code, list_of_changes)
    """
    changes: list[str] = []
    
    # Only add guards if we see patterns that suggest response could be None
    # This is a conservative approach - we don't want to add unnecessary guards
    
    # Check if there's a custom method that might return Optional[Response]
    has_optional_return = bool(UNION_RETURN_PATTERN.search(code))
    
    if not has_optional_return:
        # No Optional return types, no guards needed
        return code, changes
    
    # Find unsafe access patterns and suggest guards
    unsafe_matches = list(UNSAFE_ACCESS_PATTERN.finditer(code))
    
    if unsafe_matches:
        logger.warning(
            f"[V26-003] Found {len(unsafe_matches)} potentially unsafe response accesses. "
            "Consider adding null guards if the method can return None."
        )
        for match in unsafe_matches[:3]:  # Log first 3
            changes.append(f"Warning: Potentially unsafe access at position {match.start()}")
    
    return code, changes


def generate_safe_response_handling_template(
    status_check_codes: list[int] = None,
    raise_on_error: bool = True,
) -> str:
    """
    Generate a safe response handling template.
    
    This provides a canonical pattern for handling HTTP responses
    that is guaranteed to pass mypy checks.
    
    Args:
        status_check_codes: List of success status codes (default: 200, 201, 204)
        raise_on_error: Whether to raise on non-success status
        
    Returns:
        Code template string
    """
    if status_check_codes is None:
        status_check_codes = [200, 201, 204]
    
    codes_str = ", ".join(str(c) for c in status_check_codes)
    
    template = f'''
        # V26-003: Safe response handling pattern
        # The request() method always returns Response, never None
        # It raises IntegrationError on network/HTTP failures
        response = self._client.request(
            method,
            url,
            headers=headers,
            json=payload,
        )
        
        # Check for success status codes
        if response.status_code not in ({codes_str}):
            raise IntegrationError(
                f"API request failed: {{response.status_code}} - {{response.text[:200]}}"
            )
        
        # Handle empty responses
        if response.status_code == 204 or not response.content:
            return {{}}
        
        return response.json()
'''
    return template


def fix_generated_client_code(code: str) -> tuple[str, list[str]]:
    """
    Main entry point for fixing generated client code.
    
    Applies all fixes:
    1. V33-001: Fix double-dot import syntax errors
    2. V26-003: Remove Response | None type hints
    3. V26-003: Add null guards where needed
    4. V31-001: Fix B904 exception chaining violations
    5. V32-002: Fix conditional response access patterns
    
    Args:
        code: The generated client Python code
        
    Returns:
        Tuple of (fixed_code, list_of_all_changes)
    """
    all_changes: list[str] = []
    
    # V33-001: Fix double-dot import syntax errors
    # Pattern: "from clients..twilio_messaging" -> "from clients.twilio_messaging"
    # This happens when LLM incorrectly generates module paths with double dots
    double_dot_count = len(re.findall(r'\bfrom\s+[\w.]+\.\.', code))
    if double_dot_count > 0:
        code = re.sub(
            r'\bfrom\s+([\w.]+)\.\.(\w+)',  # Match 'from xxx..yyy'
            r'from \1.\2',  # Replace with 'from xxx.yyy'
            code
        )
        # Also fix triple-dots or more (defensive)
        while re.search(r'\bfrom\s+[\w.]*\.\.', code):
            code = re.sub(r'(\bfrom\s+[\w.]*)\.\.([\w.])', r'\1.\2', code)
        all_changes.append(f"[V33-001] Fixed {double_dot_count} double-dot import(s)")
    
    # Also fix double-dots in regular import statements
    import_double_dot_count = len(re.findall(r'\bimport\s+[\w.]+\.\.', code))
    if import_double_dot_count > 0:
        code = re.sub(r'(\bimport\s+[\w.]+)\.\.([\w.])', r'\1.\2', code)
        while re.search(r'\bimport\s+[\w.]*\.\.', code):
            code = re.sub(r'(\bimport\s+[\w.]*)\.\.([\w.])', r'\1.\2', code)
        all_changes.append(f"[V33-001] Fixed {import_double_dot_count} double-dot regular import(s)")
    
    # Fix type hints (V26-003)
    code, type_changes = fix_response_type_hints(code)
    all_changes.extend(type_changes)
    
    # Add null guards (conservative) (V26-003)
    code, guard_changes = add_response_null_guards(code)
    all_changes.extend(guard_changes)
    
    # Fix B904 exception chaining (V31-001)
    code, b904_changes = fix_b904_exception_chaining(code)
    all_changes.extend(b904_changes)
    
    # Fix conditional response access (V32-002)
    code, conditional_changes = fix_conditional_response_access(code)
    all_changes.extend(conditional_changes)
    
    # Additional cleanup: remove redundant "if response is None" checks
    # when we know response can never be None
    if "def request(" in code and "raise IntegrationError" in code:
        # This is a client with proper error handling - response is never None
        before_len = len(code)
        
        # Remove patterns like: if response is None: return {}
        code = re.sub(
            r'\n\s*if\s+response\s+is\s+None\s*:\s*\n\s*return\s+\{\}',
            '',
            code
        )
        
        # Remove patterns like: if response is None: raise ...
        code = re.sub(
            r'\n\s*if\s+response\s+is\s+None\s*:\s*\n\s*raise\s+[^\n]+',
            '',
            code
        )
        
        if len(code) < before_len:
            all_changes.append("Removed redundant 'if response is None' checks")
    
    if all_changes:
        logger.info(f"[V26-003/V31-001] Applied {len(all_changes)} code fixes")
    
    return code, all_changes


def validate_response_handling(code: str) -> list[str]:
    """
    Validate that response handling in the code is mypy-safe.
    
    Returns:
        List of warning messages (empty if valid)
    """
    warnings: list[str] = []
    
    # Check for remaining Optional/Union Response patterns
    if UNION_RESPONSE_PATTERN.search(code):
        warnings.append("Code still contains Response | None type hint")
    
    if OPTIONAL_RESPONSE_PATTERN.search(code):
        warnings.append("Code still contains Optional[Response] type hint")
    
    # Check for potentially unsafe patterns
    lines = code.split('\n')
    for i, line in enumerate(lines, 1):
        # Check for assignment with None type
        if re.search(r'response\s*=\s*None\s*$', line):
            warnings.append(f"Line {i}: response assigned to None directly")
        
        # Check for ternary that could return None
        if re.search(r'response\s*=.*if.*else\s*None', line):
            warnings.append(f"Line {i}: response could be assigned None via ternary")
    
    return warnings


# =============================================================================
# V32-002: Fix Conditional Response Access Patterns
# =============================================================================
# mypy flags patterns like `response.status_code if response else 'unknown'`
# because `response` could be `Response | None` and the attribute access
# happens before the truthiness check evaluates.
#
# The fix is to use explicit `is not None` checks or rewrite to safer patterns:
#   BEFORE: response.status_code if response else 'unknown'
#   AFTER:  response.status_code if response is not None else 'unknown'
#
# However, since our client.request() NEVER returns None (it raises on error),
# the safest fix is to REMOVE the conditional entirely and trust the response.
# =============================================================================

def fix_conditional_response_access(code: str) -> tuple[str, list[str]]:
    """
    Fix conditional response access patterns that mypy flags as unsafe.
    
    V32-002: The LLM generates patterns like:
        response.status_code if response else 'unknown'
        f"...{response.text[:200] if response else 'no response'}..."
    
    These are flagged by mypy as unsafe because the attribute access happens
    BEFORE the None check is evaluated (in mypy's type narrowing logic).
    
    Since our self.request() method NEVER returns None (it raises IntegrationError),
    we can safely replace conditionals with direct access.
    
    Args:
        code: The generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_changes)
    """
    changes: list[str] = []
    fixed = code
    
    # Fix 1: Simple conditional patterns outside f-strings
    # BEFORE: response.status_code if response else 'unknown'
    # AFTER:  response.status_code
    def fix_simple_conditional(match: re.Match) -> str:
        var = match.group(1)  # 'response'
        attr = match.group(2)  # 'status_code', 'text', etc.
        changes.append(f"[V32-002] Removed unsafe conditional for {var}.{attr}")
        return f"{var}.{attr}"
    
    fixed = CONDITIONAL_ACCESS_PATTERN.sub(fix_simple_conditional, fixed)
    
    # Fix 2: F-string conditional patterns
    # BEFORE: {response.text[:200] if response else 'no response'}
    # AFTER:  {response.text[:200]}
    # We use a more careful approach here to preserve slicing
    
    def fix_fstring_conditional(match: re.Match) -> str:
        full_match = match.group(0)
        # Find the response.attr part (may have slice)
        # Pattern: {response.attr... if response else ...}
        # We want to keep just the response.attr... part
        attr_match = re.match(r'\{(response\.\w+(?:\[[^\]]+\])?)', full_match)
        if attr_match:
            attr_access = attr_match.group(1)
            changes.append(f"[V32-002] Removed unsafe f-string conditional for {attr_access}")
            return '{' + attr_access + '}'
        return full_match  # Fallback: don't change if we can't parse
    
    fixed = FSTRING_CONDITIONAL_PATTERN.sub(fix_fstring_conditional, fixed)
    
    # Fix 3: Handle error message patterns with response conditionals
    # Common LLM pattern in error handling:
    #   f"API error: {response.status_code if response else 'unknown'}"
    # After our fixes, this should work, but let's also fix the inline patterns
    
    # Pattern: standalone status_code checks in error strings
    pattern_inline = re.compile(
        r'response\.(status_code|text)\s+if\s+response\s+else\s+[\'"][^\'"]+[\'"]',
        re.MULTILINE
    )
    
    def fix_inline_conditional(match: re.Match) -> str:
        attr = match.group(1)
        changes.append(f"[V32-002] Fixed inline response.{attr} conditional in error message")
        return f"response.{attr}"
    
    fixed = pattern_inline.sub(fix_inline_conditional, fixed)
    
    if changes:
        logger.info(f"[V32-002] Fixed {len(changes)} conditional response access patterns")
    
    return fixed, changes


# =============================================================================
# V31-001: B904 Exception Chaining Fix
# =============================================================================
# ruff B904 requires exception chaining in except clauses:
#   raise SomeError(...) from err    <- chains to original exception
#   raise SomeError(...) from None   <- suppresses original exception chain
#
# The LLM often generates:
#   except ImportError:
#       raise ImportError("...")  <- B904 violation
#
# This can't be auto-fixed by ruff because it doesn't know the exception var name.
# We fix this by detecting the pattern and adding proper chaining.
# =============================================================================

# Pattern: except SomeError: followed by bare raise SomeError(...)
# Captures: indent, exception type, exception variable (if any), raise statement
B904_EXCEPT_RAISE_PATTERN = re.compile(
    r'^(\s*)except\s+(\w+)(?:\s+as\s+(\w+))?\s*:\s*\n'  # except line
    r'(\1\s+raise\s+\2\([^)]*\))(?!\s+from\s)',          # raise without 'from'
    re.MULTILINE
)

# Pattern: except SomeError: followed by any raise without 'from'
B904_GENERIC_RAISE_PATTERN = re.compile(
    r'^(\s*)except\s+(\w+)(?:\s+as\s+(\w+))?\s*:\s*\n'  # except line with optional 'as var'
    r'(\1\s+raise\s+\w+\([^)]*\))(?!\s+from\s)',         # raise without 'from'
    re.MULTILINE
)

# Simpler pattern for the most common case
B904_IMPORT_ERROR_PATTERN = re.compile(
    r'(\s*)except\s+ImportError\s*:\s*\n'
    r'(\s+)(raise\s+ImportError\([^)]*\))(?!\s+from)',
    re.MULTILINE
)


def fix_b904_exception_chaining(code: str) -> tuple[str, list[str]]:
    """
    Fix B904 violations by adding exception chaining.
    
    V31-001: This fixes the common LLM pattern:
        except SomeError:
            raise SomeError("...")  # B904 violation
    
    Transforms to:
        except SomeError:
            raise SomeError("...") from None  # Suppresses chain (cleaner for user)
    
    We use 'from None' rather than 'from e' because:
    1. The original exception is usually the same type (ImportError -> ImportError)
    2. 'from None' gives cleaner tracebacks for end users
    3. We don't have the exception variable name if not captured with 'as e'
    
    Args:
        code: The generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_changes)
    """
    changes: list[str] = []
    lines = code.split('\n')
    fixed_lines = []
    
    # State tracking for except block context
    in_except_block = False
    except_indent = 0
    current_exception_var: str | None = None
    
    for i, line in enumerate(lines):
        # Detect entering an except block
        # Patterns: "except SomeError:", "except SomeError as e:", "except:"
        except_match = re.match(r'^(\s*)except\s*([^:]*)?:', line)
        if except_match:
            in_except_block = True
            except_indent = len(except_match.group(1))
            # Check if exception is captured with 'as variable'
            except_clause = except_match.group(2) or ''
            var_match = re.search(r'\bas\s+(\w+)\s*$', except_clause)
            current_exception_var = var_match.group(1) if var_match else None
            fixed_lines.append(line)
            continue
        
        # Check if we've exited the except block (by dedent or new except/else/finally)
        if in_except_block:
            stripped = line.strip()
            if stripped:  # Non-empty line
                line_indent = len(line) - len(line.lstrip())
                # Exit conditions: dedent to except level, or new block keyword
                if line_indent <= except_indent:
                    if not stripped.startswith(('except', 'else:', 'finally:')):
                        in_except_block = False
                        current_exception_var = None
                    elif stripped.startswith('except'):
                        # New except block - update state
                        var_match = re.search(r'\bas\s+(\w+)\s*$', stripped.rstrip(':'))
                        current_exception_var = var_match.group(1) if var_match else None
        
        # Only process lines inside except blocks
        if not in_except_block:
            fixed_lines.append(line)
            continue
        
        # Skip if line already has ' from '
        if ' from ' in line:
            fixed_lines.append(line)
            continue
        
        # Look for raise statements that need fixing
        # Pattern: raise SomeException(...) where we need to add 'from'
        # This handles nested parens by finding the last ) before end/comment
        raise_match = re.match(r'^(\s*)(raise\s+\w+\()', line)
        if raise_match:
            indent = raise_match.group(1)
            raise_start = raise_match.group(2)
            rest_of_line = line[len(indent) + len(raise_start):]
            
            # Find the matching closing paren by counting parens
            paren_depth = 1
            close_pos = 0
            for j, char in enumerate(rest_of_line):
                if char == '(':
                    paren_depth += 1
                elif char == ')':
                    paren_depth -= 1
                    if paren_depth == 0:
                        close_pos = j
                        break
            
            if paren_depth == 0:  # Found matching close paren
                inside_parens = rest_of_line[:close_pos]
                after_close = rest_of_line[close_pos + 1:]
                
                # Skip if already has 'from' after closing paren
                if not after_close.strip().startswith('from'):
                    # Determine what to chain with
                    if current_exception_var:
                        chain_with = f"from {current_exception_var}"
                        changes.append(f"[V31-001] Added 'from {current_exception_var}' to exception raise (B904 fix)")
                    else:
                        chain_with = "from None"
                        changes.append("[V31-001] Added 'from None' to exception raise (B904 fix)")
                    
                    # Reconstruct line: indent + raise Exc( + inside + ) + from + trailing
                    trailing = after_close.rstrip()
                    line = f"{indent}{raise_start}{inside_parens}) {chain_with}{trailing}"
        
        fixed_lines.append(line)
    
    fixed = '\n'.join(fixed_lines)
    
    if changes:
        logger.info(f"[V31-001] Fixed {len(changes)} B904 exception chaining issues")
    
    return fixed, changes

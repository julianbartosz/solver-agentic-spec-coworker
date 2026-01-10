"""
Import Path Fixer and Validator for Generated Code (V35-001 Fix)

This module addresses the LLM hallucination problem where the LLM generates
imports from non-existent packages like `integration_framework` instead of
the correct `integration_coworker_runtime` package.

Production Design:
------------------
1. **Pre-Generation**: Update skeleton templates with correct imports
2. **Post-Generation**: Validate and fix imports in generated code
3. **Runtime Validation**: Check imports can be resolved before sandbox execution

This is a critical safeguard because:
- LLMs will ignore skeleton code and invent their own import paths
- Wrong imports cause immediate ImportError at sandbox execution
- The runtime package `integration_coworker_runtime` is pip-installable and
  should be the ONLY source for integration runtime components

Valid Import Sources:
--------------------
- `integration_coworker_runtime` - The pip-installable runtime package
- Standard library modules
- Third-party packages specified in repo's requirements

Invalid Import Patterns (will be auto-fixed):
--------------------------------------------
- `integration_framework.*` - Completely hallucinated by LLM
- `integration_coworker.runtime.*` - Internal package, not available in target repos
- `integrations.clients.*` - May not exist yet (circular import issue)
"""
import ast
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# CONSTANTS
# =============================================================================

# The CORRECT runtime package for generated code
RUNTIME_PACKAGE = "integration_coworker_runtime"

# Common hallucinated import patterns that MUST be fixed
# Note: "integrations.exceptions" is handled specially in fix_flow_imports()
# because it needs to be redirected to the actual client module, not runtime
HALLUCINATED_PATTERNS: Dict[str, str] = {
    # Pattern → Replacement
    "integration_framework.core.client": RUNTIME_PACKAGE,
    "integration_framework.core.exceptions": RUNTIME_PACKAGE,
    "integration_framework.core.http_client": RUNTIME_PACKAGE,
    "integration_framework.client": RUNTIME_PACKAGE,
    "integration_framework.exceptions": RUNTIME_PACKAGE,
    "integration_framework.http_client": RUNTIME_PACKAGE,
    "integration_framework": RUNTIME_PACKAGE,
    # Internal package (not available in target repos)
    "integration_coworker.runtime.client": RUNTIME_PACKAGE,
    "integration_coworker.runtime.http_client": RUNTIME_PACKAGE,
    "integration_coworker.runtime.exceptions": RUNTIME_PACKAGE,
    "integration_coworker.runtime.auth": RUNTIME_PACKAGE,
    "integration_coworker.runtime.retry": RUNTIME_PACKAGE,
    "integration_coworker.runtime.rate_limit": RUNTIME_PACKAGE,
    "integration_coworker.runtime": RUNTIME_PACKAGE,
}

# V38-007: Patterns that are completely invalid and need special handling
# These are redirected to the client module, not the runtime package
INVALID_EXCEPTION_PATTERNS: Set[str] = {
    "integrations.exceptions",           # Non-existent module
    "integrations.core.exceptions",      # Non-existent module  
    "integrations.errors",               # Non-existent module
    "integration.exceptions",            # Non-existent module
}

# Mapping from hallucinated class names to correct ones
CLASS_NAME_FIXES: Dict[str, str] = {
    "IntegrationHTTPClient": "IntegrationHttpClient",  # Case fix
    "IntegrationClient": "IntegrationClient",  # Keep as-is
    "IntegrationHttpClient": "IntegrationHttpClient",  # Already correct
}

# Valid runtime exports (what's actually available from the package)
VALID_RUNTIME_EXPORTS: Set[str] = {
    # Clients
    "IntegrationClient",
    "IntegrationHttpClient",
    # Auth
    "BaseAuth",
    "NoAuth",
    "BearerAuth",
    "ApiKeyAuth",
    "BasicAuth",
    # Retry
    "BaseRetry",
    "NoRetry",
    "ExponentialRetry",
    # Rate Limiting
    "BaseRateLimiter",
    "NoRateLimiter",
    "TokenBucketRateLimiter",
    "SlidingWindowRateLimiter",
    # Exceptions
    "IntegrationError",
    "TransientIntegrationError",
    "AuthIntegrationError",
}


# V45-002: Common stdlib modules that LLMs use but forget to import
# Maps module name to common patterns that indicate usage
STDLIB_USAGE_PATTERNS: Dict[str, List[str]] = {
    "uuid": ["uuid.uuid4", "uuid.uuid1", "uuid.UUID"],
    "json": ["json.dumps", "json.loads", "json.load", "json.dump"],
    "re": ["re.match", "re.search", "re.compile", "re.sub", "re.findall"],
    "os": ["os.path", "os.environ", "os.getenv", "os.makedirs"],
    "datetime": ["datetime.datetime", "datetime.date", "datetime.time", "datetime.timedelta"],
    "base64": ["base64.b64encode", "base64.b64decode"],
    "hashlib": ["hashlib.sha256", "hashlib.md5", "hashlib.sha1"],
    "time": ["time.sleep", "time.time"],
    "logging": ["logging.getLogger", "logging.info", "logging.error"],
    "functools": ["functools.wraps", "@functools.lru_cache"],
    "collections": ["collections.defaultdict", "collections.OrderedDict"],
    "pathlib": ["Path(", "pathlib.Path"],
}

# V45-002-FIX: Typing module imports require special handling because they're
# used directly (e.g., `Optional[str]`) not prefixed (e.g., `typing.Optional`)
# Maps the typing name to a regex pattern that matches usage
TYPING_USAGE_PATTERNS: Dict[str, str] = {
    "Optional": r"\bOptional\s*\[",           # Optional[X]
    "List": r"\bList\s*\[",                   # List[X]  
    "Dict": r"\bDict\s*\[",                   # Dict[X, Y]
    "Any": r"\bAny\b",                        # Any (not in comment)
    "Union": r"\bUnion\s*\[",                 # Union[X, Y]
    "Tuple": r"\bTuple\s*\[",                 # Tuple[X, ...]
    "Set": r"\bSet\s*\[",                     # Set[X]
    "Callable": r"\bCallable\s*\[",           # Callable[..., X]
    "Literal": r"\bLiteral\s*\[",             # Literal[X]
    "TypeVar": r"\bTypeVar\s*\(",             # TypeVar("T")
}


@dataclass
class ImportFix:
    """Represents a single import fix to apply."""
    line_number: int
    original_line: str
    fixed_line: str
    reason: str


@dataclass
class ImportValidationResult:
    """Result of import validation for generated code."""
    is_valid: bool
    fixes_applied: List[ImportFix] = field(default_factory=list)
    unresolved_imports: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# =============================================================================
# IMPORT DETECTION AND FIXING
# =============================================================================

def _extract_imports_with_lines(code: str) -> List[Tuple[int, str, str]]:
    """
    Extract all imports with their line numbers and types.
    
    Returns:
        List of (line_number, import_statement, import_type)
        import_type is "import" or "from"
    """
    results = []
    lines = code.split('\n')
    
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('import '):
            results.append((i, line, 'import'))
        elif stripped.startswith('from ') and ' import ' in stripped:
            results.append((i, line, 'from'))
    
    return results


def _fix_hallucinated_import(line: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Fix a single import line if it contains hallucinated patterns.
    
    Returns:
        Tuple of (fixed_line, reason) or (None, None) if no fix needed
    """
    original = line
    
    # Check each hallucinated pattern
    for pattern, replacement in HALLUCINATED_PATTERNS.items():
        if pattern in line:
            # Handle "from X import Y" style
            from_match = re.match(
                rf'^(\s*)from\s+{re.escape(pattern)}(?:\.(\w+))?\s+import\s+(.+)$',
                line
            )
            if from_match:
                indent = from_match.group(1)
                submodule = from_match.group(2)
                imports = from_match.group(3)
                
                # Fix class names in imports
                fixed_imports = []
                for imp in imports.split(','):
                    imp = imp.strip()
                    # Handle "X as Y" aliases
                    if ' as ' in imp:
                        name, alias = imp.split(' as ', 1)
                        name = name.strip()
                        alias = alias.strip()
                        fixed_name = CLASS_NAME_FIXES.get(name, name)
                        fixed_imports.append(f"{fixed_name} as {alias}")
                    else:
                        fixed_name = CLASS_NAME_FIXES.get(imp, imp)
                        fixed_imports.append(fixed_name)
                
                fixed_line = f"{indent}from {replacement} import {', '.join(fixed_imports)}"
                return fixed_line, f"Fixed hallucinated import: {pattern} -> {replacement}"
            
            # Handle "import X" style
            import_match = re.match(
                rf'^(\s*)import\s+{re.escape(pattern)}(?:\s+as\s+(\w+))?$',
                line
            )
            if import_match:
                indent = import_match.group(1)
                alias = import_match.group(2)
                if alias:
                    fixed_line = f"{indent}import {replacement} as {alias}"
                else:
                    fixed_line = f"{indent}import {replacement}"
                return fixed_line, f"Fixed hallucinated import: {pattern} -> {replacement}"
    
    # Check for class name case issues (IntegrationHTTPClient -> IntegrationHttpClient)
    for wrong, correct in CLASS_NAME_FIXES.items():
        if wrong != correct and wrong in line:
            fixed_line = line.replace(wrong, correct)
            if fixed_line != line:
                return fixed_line, f"Fixed class name case: {wrong} -> {correct}"
    
    return None, None


def fix_imports_in_code(code: str) -> Tuple[str, List[ImportFix]]:
    """
    Fix all hallucinated imports in generated code.
    
    This is the main entry point for post-generation import fixing.
    
    Args:
        code: Generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[ImportFix] = []
    lines = code.split('\n')
    
    for i, line in enumerate(lines):
        fixed_line, reason = _fix_hallucinated_import(line)
        if fixed_line is not None and reason is not None:
            fixes.append(ImportFix(
                line_number=i + 1,
                original_line=line,
                fixed_line=fixed_line,
                reason=reason,
            ))
            lines[i] = fixed_line
    
    fixed_code = '\n'.join(lines)
    
    if fixes:
        logger.info(f"[V35-001] Fixed {len(fixes)} hallucinated imports in generated code")
        for fix in fixes:
            logger.debug(f"  Line {fix.line_number}: {fix.reason}")
    
    return fixed_code, fixes


def fix_missing_stdlib_imports(code: str) -> Tuple[str, List[ImportFix]]:
    """
    V45-002: Detect and fix missing stdlib imports in generated code.
    
    LLMs sometimes use stdlib modules (like uuid.uuid4()) without including
    the corresponding import statement. This function:
    
    1. Parses the code to find existing imports
    2. Scans for patterns indicating stdlib module usage
    3. Adds missing import statements at the top (or extends existing imports)
    
    V45-006: Enhanced to properly merge typing imports into existing 
    `from typing import` statements rather than creating duplicate lines.
    
    Args:
        code: Generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[ImportFix] = []
    
    # Parse existing imports to avoid duplicates
    existing_imports: Set[str] = set()
    existing_from_typing: Set[str] = set()  # Track specific typing imports
    typing_import_line_number: Optional[int] = None  # V45-006: Track the line for merging
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    existing_imports.add(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    existing_imports.add(node.module.split('.')[0])
                    # Track which names are imported from typing
                    if node.module == "typing":
                        for alias in node.names:
                            existing_from_typing.add(alias.name)
                        # V45-006: Store the first typing import line for merging
                        if typing_import_line_number is None and hasattr(node, 'lineno'):
                            typing_import_line_number = node.lineno - 1  # Convert to 0-indexed
    except SyntaxError:
        # Can't parse, return unchanged
        return code, fixes
    
    # Find missing imports by scanning for usage patterns
    missing_imports: List[str] = []
    for module, patterns in STDLIB_USAGE_PATTERNS.items():
        if module in existing_imports:
            continue
        
        # Check if any usage pattern exists in the code
        for pattern in patterns:
            if pattern in code:
                missing_imports.append(module)
                fixes.append(ImportFix(
                    line_number=0,  # Will be inserted at top
                    original_line="",
                    fixed_line=f"import {module}",
                    reason=f"V45-002: Added missing import for '{module}' (found usage: {pattern})",
                ))
                break
    
    # V45-002-FIX: Handle typing module specially - use "from typing import X"
    missing_typing_names: List[str] = []
    for typing_name, pattern in TYPING_USAGE_PATTERNS.items():
        # Skip if already imported from typing
        if typing_name in existing_from_typing:
            continue
        # Check if pattern is used in code (using regex for precision)
        if re.search(pattern, code):
            missing_typing_names.append(typing_name)
    
    if not missing_imports and not missing_typing_names:
        return code, fixes
    
    lines = code.split('\n')
    
    # V45-006: If there's an existing typing import, merge into it instead of adding new line
    if missing_typing_names and typing_import_line_number is not None:
        # Merge into existing typing import line
        original_line = lines[typing_import_line_number]
        
        # Handle various from typing import formats
        # Simple: from typing import X, Y
        # Multi-line: from typing import (\n    X,\n    Y,\n)
        if '(' in original_line and ')' not in original_line:
            # Multi-line import, add to it
            # Find the closing paren and add before it
            for i in range(typing_import_line_number, len(lines)):
                if ')' in lines[i]:
                    # Add the new names before the closing paren
                    indent = '    '  # Standard Python indent
                    new_items = ',\n'.join(f"{indent}{name}," for name in sorted(missing_typing_names))
                    lines[i] = new_items + '\n' + lines[i]
                    break
        else:
            # Single-line import
            # Extract existing imports and merge
            match = re.match(r'^(\s*from\s+typing\s+import\s+)(.+)$', original_line)
            if match:
                prefix = match.group(1)
                existing_items_str = match.group(2).strip()
                # Remove trailing comma if present
                existing_items_str = existing_items_str.rstrip(',').strip()
                # Parse existing items
                existing_items = [item.strip() for item in existing_items_str.split(',')]
                # Merge with missing names
                all_items = sorted(set(existing_items) | set(missing_typing_names))
                lines[typing_import_line_number] = f"{prefix}{', '.join(all_items)}"
        
        fixes.append(ImportFix(
            line_number=typing_import_line_number + 1,  # 1-indexed
            original_line=original_line,
            fixed_line=lines[typing_import_line_number],
            reason=f"V45-006: Merged missing typing imports: {', '.join(missing_typing_names)}",
        ))
    elif missing_typing_names:
        # No existing typing import, create new one (original behavior)
        typing_import_line = f"from typing import {', '.join(sorted(missing_typing_names))}"
        fixes.append(ImportFix(
            line_number=0,
            original_line="",
            fixed_line=typing_import_line,
            reason=f"V45-002-FIX: Added missing typing imports: {', '.join(missing_typing_names)}",
        ))
        
        # Find insertion point for new typing import
        insert_index = _find_import_insertion_point(lines)
        lines.insert(insert_index, typing_import_line)
    
    # Insert missing stdlib imports at the appropriate location
    if missing_imports:
        insert_index = _find_import_insertion_point(lines)
        for mod in reversed(sorted(missing_imports)):
            lines.insert(insert_index, f"import {mod}")
    
    fixed_code = '\n'.join(lines)
    
    if fixes:
        logger.info(f"[V45-002/V45-006] Added/merged {len(fixes)} missing stdlib/typing imports")
        for fix in fixes:
            logger.debug(f"  {fix.reason}")
    
    return fixed_code, fixes


def _find_import_insertion_point(lines: List[str]) -> int:
    """
    V45-006: Find the best line index to insert new imports.
    
    Returns the line index (0-based) where new imports should be inserted.
    This is after any module docstring and before the first import.
    """
    insert_index = 0
    in_docstring = False
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Track docstring state
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if in_docstring:
                in_docstring = False
                insert_index = i + 1
            elif stripped.count('"""') == 2 or stripped.count("'''") == 2:
                # Single-line docstring
                insert_index = i + 1
            else:
                in_docstring = True
            continue
        
        if in_docstring:
            continue
        
        # Found first non-docstring, non-empty line
        if stripped and (stripped.startswith('import ') or stripped.startswith('from ')):
            insert_index = i
            break
        elif stripped and not stripped.startswith('#'):
            # Non-import code found, insert before it
            insert_index = i
            break
    
    return insert_index


# =============================================================================
# V45-004: CROSS-LANGUAGE HALLUCINATION FIXER
# =============================================================================
# LLMs trained on multiple languages sometimes mix syntax from JavaScript,
# TypeScript, Java, etc. into Python code. This function detects and fixes
# these cross-language hallucinations.
# =============================================================================

# Patterns that indicate cross-language hallucinations
# Format: (regex_pattern, replacement, description)
CROSS_LANGUAGE_FIXES: List[Tuple[str, str, str]] = [
    # JavaScript/TypeScript: `new ClassName()` → `ClassName()`
    (r'\bnew\s+([A-Z][a-zA-Z0-9_]*)\s*\(', r'\1(', 'JS/TS "new" keyword removed'),
    
    # TypeScript: `let x: type =` → `x: type =` (let is not Python)
    (r'\blet\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1:', 'TS "let" keyword removed'),
    
    # TypeScript: `const x: type =` → `x: type =` (const is not Python)
    (r'\bconst\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1:', 'TS "const" keyword removed'),
    
    # JavaScript: `var x =` → `x =` (var is not Python)
    (r'\bvar\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=', r'\1 =', 'JS "var" keyword removed'),
    
    # JavaScript: `async function name()` → `async def name():`
    (r'\basync\s+function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)\s*\{?', r'async def \1(\2):', 'JS async function converted'),
    
    # JavaScript: `function name()` → `def name():`  
    (r'\bfunction\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)\s*\{?', r'def \1(\2):', 'JS function converted'),
]


def fix_cross_language_hallucinations(code: str) -> Tuple[str, List[ImportFix]]:
    """
    V45-004: Detect and fix cross-language syntax hallucinations.
    
    LLMs sometimes mix JavaScript/TypeScript/Java syntax into Python code:
    - `new ClassName()` instead of `ClassName()`
    - `let x = ` or `const x =` instead of `x =`
    - `var x = ` instead of `x =`
    - `function name()` instead of `def name():`
    
    This function detects and fixes these patterns.
    
    Args:
        code: Generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[ImportFix] = []
    fixed_code = code
    
    for pattern, replacement, description in CROSS_LANGUAGE_FIXES:
        # Find all matches before replacing
        matches = list(re.finditer(pattern, fixed_code))
        if matches:
            for match in matches:
                # Calculate line number for this match
                line_num = fixed_code[:match.start()].count('\n') + 1
                original_text = match.group(0)
                fixed_text = re.sub(pattern, replacement, original_text)
                
                fixes.append(ImportFix(
                    line_number=line_num,
                    original_line=original_text,
                    fixed_line=fixed_text,
                    reason=f"V45-004: {description}",
                ))
            
            # Apply the fix
            fixed_code = re.sub(pattern, replacement, fixed_code)
    
    if fixes:
        logger.info(f"[V45-004] Fixed {len(fixes)} cross-language hallucinations")
        for fix in fixes:
            logger.debug(f"  Line {fix.line_number}: {fix.reason} - '{fix.original_line}' → '{fix.fixed_line}'")
    
    return fixed_code, fixes


def validate_runtime_imports(code: str) -> ImportValidationResult:
    """
    Validate that all runtime imports are valid.
    
    Checks that:
    1. Imports from integration_coworker_runtime use valid exports
    2. No hallucinated import patterns remain
    3. Warns about potentially problematic imports
    
    Args:
        code: Python code to validate
        
    Returns:
        ImportValidationResult with validation status and details
    """
    result = ImportValidationResult(is_valid=True)
    
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        result.is_valid = False
        result.warnings.append(f"Syntax error prevented import validation: {e}")
        return result
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                # Check for remaining hallucinated patterns
                for pattern in HALLUCINATED_PATTERNS.keys():
                    if node.module.startswith(pattern):
                        result.is_valid = False
                        result.unresolved_imports.append(
                            f"Line {node.lineno}: Hallucinated import from '{node.module}' not fixed"
                        )
                
                # Validate integration_coworker_runtime imports
                if node.module == RUNTIME_PACKAGE or node.module.startswith(f"{RUNTIME_PACKAGE}."):
                    for alias in node.names:
                        name = alias.name
                        if name not in VALID_RUNTIME_EXPORTS and name != '*':
                            result.warnings.append(
                                f"Line {node.lineno}: Import '{name}' from {RUNTIME_PACKAGE} "
                                f"may not be a valid export"
                            )
        
        elif isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name.split('.')[0]
                # Check for remaining hallucinated patterns
                for pattern in HALLUCINATED_PATTERNS.keys():
                    if alias.name.startswith(pattern):
                        result.is_valid = False
                        result.unresolved_imports.append(
                            f"Line {node.lineno}: Hallucinated import '{alias.name}' not fixed"
                        )
    
    return result


# =============================================================================
# V45-003: AUTO-ADD MISSING RUNTIME IMPORTS
# =============================================================================
# When policy_mode="runtime", code may use IntegrationHttpClient or 
# IntegrationError but forget to import them. This function detects usage
# patterns and adds the missing imports from integration_coworker_runtime.
# =============================================================================

def add_missing_runtime_imports(code: str) -> Tuple[str, List[ImportFix]]:
    """
    Add missing runtime package imports when names are used but not imported.
    
    V45-003: Scans code for usage of runtime package exports (IntegrationHttpClient,
    IntegrationError, etc.) and adds missing imports from integration_coworker_runtime.
    
    Args:
        code: Python code to fix
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[ImportFix] = []
    
    try:
        tree = ast.parse(code)
    except SyntaxError:
        # Can't parse, return unchanged
        return code, fixes
    
    # Collect all imported names
    imported_names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.asname if alias.asname else alias.name.split('.')[0]
                imported_names.add(name)
        elif isinstance(node, ast.ImportFrom):
            if node.names:
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name
                    imported_names.add(name)
    
    # Collect all names used in the code
    used_names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            # Handle Class.method patterns
            if isinstance(node.value, ast.Name):
                used_names.add(node.value.id)
        elif isinstance(node, ast.Call):
            # Handle calls like IntegrationError("message")
            if isinstance(node.func, ast.Name):
                used_names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                used_names.add(node.func.value.id)
    
    # Find runtime exports that are used but not imported
    missing_imports: List[str] = []
    for name in VALID_RUNTIME_EXPORTS:
        if name in used_names and name not in imported_names:
            missing_imports.append(name)
    
    if not missing_imports:
        return code, fixes
    
    # Generate the import statement
    import_statement = f"from {RUNTIME_PACKAGE} import {', '.join(sorted(missing_imports))}"
    
    # Find the best place to insert the import (after other imports)
    lines = code.split('\n')
    insert_line = 0
    in_docstring = False
    found_import_section = False
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Track docstrings
        if stripped.startswith('"""') or stripped.startswith("'''"):
            if stripped.count('"""') % 2 == 1 or stripped.count("'''") % 2 == 1:
                in_docstring = not in_docstring
            continue
        
        if in_docstring:
            continue
        
        # Find import section
        if stripped.startswith('import ') or stripped.startswith('from '):
            found_import_section = True
            insert_line = i + 1
        elif found_import_section and stripped and not stripped.startswith('#'):
            # End of import section
            break
    
    # Insert the import
    if insert_line > 0:
        lines.insert(insert_line, import_statement)
    else:
        # No existing imports, add after docstring or at top
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                # Find end of docstring
                if stripped.count('"""') % 2 == 1 or stripped.count("'''") % 2 == 1:
                    for j in range(i + 1, len(lines)):
                        if '"""' in lines[j] or "'''" in lines[j]:
                            insert_line = j + 1
                            break
                    break
            elif stripped and not stripped.startswith('#'):
                insert_line = i
                break
        
        lines.insert(insert_line, import_statement)
    
    fixed_code = '\n'.join(lines)
    fixes.append(ImportFix(
        line_number=insert_line + 1,
        original_line="",
        fixed_line=import_statement,
        reason=f"V45-003: Added missing runtime imports: {', '.join(missing_imports)}",
    ))
    
    logger.info(f"[V45-003] Added missing runtime imports: {', '.join(missing_imports)}")
    
    return fixed_code, fixes


def get_correct_runtime_import(class_names: List[str]) -> str:
    """
    Generate the correct import statement for runtime classes.
    
    This should be used by skeleton templates to ensure correct imports.
    
    Args:
        class_names: List of class names to import
        
    Returns:
        Correct import statement
    """
    # Validate and fix class names
    fixed_names = []
    for name in class_names:
        fixed = CLASS_NAME_FIXES.get(name, name)
        if fixed in VALID_RUNTIME_EXPORTS:
            fixed_names.append(fixed)
        else:
            logger.warning(f"Unknown runtime export: {name}")
            fixed_names.append(fixed)
    
    return f"from {RUNTIME_PACKAGE} import {', '.join(fixed_names)}"


# =============================================================================
# INTEGRATION WITH CODEGEN PIPELINE
# =============================================================================

def fix_generated_code_imports(
    code: str,
    artifact_type: str,
    provider_code: str = "unknown",
) -> Tuple[str, ImportValidationResult]:
    """
    Complete import fixing and validation for generated code.
    
    This is the main entry point to be called after LLM code generation
    and before sandbox execution.
    
    Args:
        code: Generated code
        artifact_type: Type of artifact ("client", "flow", "test")
        provider_code: Provider identifier for logging
        
    Returns:
        Tuple of (fixed_code, validation_result)
    """
    logger.debug(f"[V35-001] Validating imports for {artifact_type} ({provider_code})")
    all_fixes: List[ImportFix] = []
    
    # Step 1: Fix cross-language hallucinations (V45-004)
    # Must run FIRST because JS `new` can cause syntax errors
    fixed_code, cross_lang_fixes = fix_cross_language_hallucinations(code)
    all_fixes.extend(cross_lang_fixes)
    
    # Step 2: Fix hallucinated imports
    fixed_code, import_fixes = fix_imports_in_code(fixed_code)
    all_fixes.extend(import_fixes)
    
    # Step 3: Add missing stdlib/typing imports (V45-002)
    fixed_code, stdlib_fixes = fix_missing_stdlib_imports(fixed_code)
    all_fixes.extend(stdlib_fixes)
    
    # Step 4: Validate the fixed code
    result = validate_runtime_imports(fixed_code)
    result.fixes_applied = all_fixes
    
    if not result.is_valid:
        logger.error(
            f"[V35-001] Import validation failed for {artifact_type} ({provider_code}): "
            f"{len(result.unresolved_imports)} unresolved imports"
        )
        for issue in result.unresolved_imports:
            logger.error(f"  {issue}")
    elif all_fixes:
        logger.info(
            f"[V35-001] Fixed {len(all_fixes)} issues for {artifact_type} ({provider_code})"
        )
    
    return fixed_code, result


# =============================================================================
# V38-007: FLOW-SPECIFIC IMPORT FIXING
# =============================================================================

def fix_flow_imports(
    code: str,
    client_import_module: str,
    policy_mode: str = "inline",
) -> Tuple[str, List[ImportFix]]:
    """
    Fix flow-specific import issues that can't be handled by generic import fixer.
    
    V38-007: This specifically handles the case where LLM generates imports like:
        from integrations.exceptions import IntegrationError
    
    Which should be:
        - In inline mode: from {client_module} import IntegrationError
        - In runtime mode: from integration_coworker_runtime import IntegrationError
    
    Args:
        code: Flow code to fix
        client_import_module: The client module path (e.g., "integrations.clients.openai")
        policy_mode: "inline" or "runtime"
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[ImportFix] = []
    lines = code.split('\n')
    
    # Determine the correct import target
    if policy_mode == "runtime":
        exception_import_module = RUNTIME_PACKAGE
    else:
        exception_import_module = client_import_module
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Check for invalid exception import patterns
        for invalid_pattern in INVALID_EXCEPTION_PATTERNS:
            # Match: from integrations.exceptions import IntegrationError
            pattern = rf'^(\s*)from\s+{re.escape(invalid_pattern)}\s+import\s+(.+)$'
            match = re.match(pattern, line)
            if match:
                indent = match.group(1)
                imports = match.group(2)
                
                # Filter to only keep IntegrationError and related exceptions
                exception_imports = []
                other_imports = []
                for imp in imports.split(','):
                    imp = imp.strip()
                    # Remove any 'as X' aliases for simplicity
                    base_name = imp.split(' as ')[0].strip() if ' as ' in imp else imp
                    if 'Error' in base_name or 'Exception' in base_name:
                        exception_imports.append(imp)
                    else:
                        other_imports.append(imp)
                
                if exception_imports:
                    fixed_line = f"{indent}from {exception_import_module} import {', '.join(exception_imports)}"
                    fixes.append(ImportFix(
                        line_number=i + 1,
                        original_line=line,
                        fixed_line=fixed_line,
                        reason=f"V38-007: Fixed invalid exception import: {invalid_pattern} -> {exception_import_module}",
                    ))
                    lines[i] = fixed_line
                    
                    # If there were other non-exception imports, log a warning
                    if other_imports:
                        logger.warning(
                            f"V38-007: Dropped non-exception imports from invalid module: {other_imports}"
                        )
                break
    
    fixed_code = '\n'.join(lines)
    
    if fixes:
        logger.info(f"[V38-007] Fixed {len(fixes)} flow exception imports")
        for fix in fixes:
            logger.debug(f"  Line {fix.line_number}: {fix.reason}")
    
    return fixed_code, fixes


def extract_method_params(code: str, method_name: str) -> List[str]:
    """
    Extract parameter names from a method in the given code.
    
    V45-009: Used to dynamically extract valid params from client code
    instead of hardcoding them. This prevents incorrectly removing valid
    parameters like 'data' when the client uses it instead of 'payload'.
    
    Args:
        code: Python source code containing the method
        method_name: Name of the method to extract params from
        
    Returns:
        List of parameter names (including 'self') that the method accepts.
        Returns empty list if method not found or parse error.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        logger.warning(f"[V45-009] Failed to parse code for param extraction")
        return []
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == method_name:
                params = []
                # Add regular args
                for arg in node.args.args:
                    params.append(arg.arg)
                # Add keyword-only args
                for arg in node.args.kwonlyargs:
                    params.append(arg.arg)
                # Check for **kwargs
                if node.args.kwarg:
                    params.append(f"**{node.args.kwarg.arg}")
                return params
    
    logger.debug(f"[V45-009] Method '{method_name}' not found in code")
    return []


def fix_flow_client_signature_mismatch(
    flow_code: str,
    client_method_name: str,
    valid_params: List[str],
) -> Tuple[str, List[str]]:
    """
    Fix flow code that passes parameters the client method doesn't accept.
    
    V38-007: This handles cases where LLM generates flow code like:
        client.create_response(max_retries=3)
    
    When the client method signature is:
        def create_response(self, payload, idempotency_key=None)
    
    V45-009: Now accepts dynamically extracted params from client code.
    If the client method accepts **kwargs, no params are removed.
    
    BUG #2 FIX: Replaced fragile regex with AST-based replacement to handle
    complex nested arguments and multi-line calls robustly.
    
    Args:
        flow_code: Flow code to fix
        client_method_name: Name of the client method being called
        valid_params: List of parameter names the client method accepts
                     (may include '**kwargs' if method accepts arbitrary kwargs)
        
    Returns:
        Tuple of (fixed_code, list_of_removed_params)
    """
    removed_params: List[str] = []
    
    # V45-009: If client method accepts **kwargs, don't remove any params
    has_kwargs = any(p.startswith('**') for p in valid_params)
    if has_kwargs:
        logger.debug(f"[V45-009] Method '{client_method_name}' accepts **kwargs, skipping param removal")
        return flow_code, removed_params
    
    # Create a set of valid param names for faster lookup
    valid_set = set(valid_params)
    
    try:
        # Parse the flow code
        tree = ast.parse(flow_code)
    except SyntaxError:
        logger.warning(f"[BUG-002] Failed to parse flow code for signature fix: invalid syntax")
        return flow_code, []

    # Map replacements: (start_offset, end_offset) -> new_text
    replacements = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check if this call matches the client method
            is_match = False
            if isinstance(node.func, ast.Attribute) and node.func.attr == client_method_name:
                is_match = True
            elif isinstance(node.func, ast.Name) and node.func.id == client_method_name:
                # Less likely for client calls, but possible
                is_match = True
            
            if is_match:
                # Check keywords
                bad_keywords = []
                good_keywords = []
                
                for kw in node.keywords:
                    # kw.arg is the parameter name (can be None for **kwargs expansion)
                    if kw.arg:
                        if kw.arg not in valid_set:
                            bad_keywords.append(kw.arg)
                            removed_params.append(kw.arg)
                        else:
                            good_keywords.append(kw)
                    else:
                        # **kwargs expansion - keep it
                        good_keywords.append(kw)
                
                if bad_keywords:
                    # Reconstruct the call arguments using ast.unparse (Python 3.9+)
                    # We create a dummy call node with just the args/keywords we want to keep
                    
                    # Create copy of args
                    new_args = node.args
                    new_keywords = good_keywords
                    
                    # Create dummy Call node
                    dummy_call = ast.Call(
                        func=node.func,
                        args=new_args,
                        keywords=new_keywords
                    )
                    
                    try:
                        # Unparse the whole call
                        # Note: This reformats the call, losing original formatting/comments within the call
                        # But it guarantees syntactic correctness
                        new_call_code = ast.unparse(dummy_call)
                        
                        # Get source segment of original call
                        # We need Python 3.8+ end_lineno/end_col_offset
                        if hasattr(node, 'end_lineno') and hasattr(node, 'end_col_offset'):
                            # Calculate offsets in original code
                            # This is tricky without a dedicated library like asttokens
                            # But we can assume standard AST behavior
                            pass
                        
                        # Fallback/Simplification: 
                        # Since we can't easily map AST nodes to exact byte offsets without 'asttokens',
                        # and replacing just the text via regex is what broke before...
                        #
                        # Robust Approach without extra deps:
                        # 1. We know this specific Call node is bad.
                        # 2. We generated 'new_call_code'.
                        # 3. We can replace the specific substring corresponding to this node.
                        # But ast doesn't give us the exact source text of a node easily.
                        
                        # BETTER STRATEGY:
                        # Since we are fixing generated code, slight reformatting is fine.
                        # We can use the 'ast.unparse' of the WHOLE tree if we modified the tree?
                        # No, 'ast.unparse' destroys comments in the whole file. That's bad.
                        
                        # Compromise: Use the regex approach but guided by AST?
                        # No, regex parsing is the problem.
                        
                        # Hacky but effective:
                        # Locate the call in source lines using lineno/col_offset.
                        # Extract the text roughly.
                        # Actually, let's use the 'ast.get_source_segment' if available (3.8+).
                        segment = ast.get_source_segment(flow_code, node)
                        if segment:
                            # Replace this segment with new_call_code
                            # We need to handle indentation for multi-line replacements
                            replacements.append((node, segment, new_call_code))
                            
                    except Exception as e:
                        logger.warning(f"[BUG-002] AST unparse failed: {e}")

    # Apply replacements from bottom up to preserve offsets if we had them
    # But strings are immutable.
    # Since we don't have offsets, let's just do single pass or handle overlaps.
    # With 'replacements' list of (node, original_text, new_text), we need to be careful.
    # The 'segment' might not be unique in the file!
    # But 'node.lineno' helps.
    
    if not replacements:
        return flow_code, removed_params
        
    # Apply replacements using line/col info
    # Sort by position reverse
    replacements.sort(key=lambda x: (x[0].lineno, x[0].col_offset), reverse=True)
    
    flow_lines = flow_code.splitlines(keepends=True)
    
    # This is still hard without asttokens because ast.get_source_segment might span lines
    # and we need to stitch it back.
    # But since this is a critical fix, let's try a safer regex for kwarg removal
    # that handles nested structures properly.
    
    # Fallback to smart regex that counts parens?
    # No, let's stick to the AST analysis logic but finding the text is hard.
    
    # Wait, 'replacements' contains the exact segment text.
    # Can we just replace the *first occurrence* of that segment starting from node.lineno?
    
    current_code = flow_code
    
    # Re-parse to ensure positions are valid for the *original* code
    # We apply changes sequentially? No, offsets shift.
    # We need to build a new string.
    
    # Let's try the safer string replacement using AST coordinates.
    # If we have Python 3.11, ast nodes have end_lineno/end_col_offset.
    
    # Convert into a list of characters to mute
    # This is getting complicated.
    
    # Simplified AST-guided Regex:
    # We know WHICH args are bad.
    # We can search for `arg_name=` patterns inside the call text.
    
    for node, segment, new_call_code in replacements:
         # Find the segment in the code to ensure we replace the right one
         # Use the lineno to narrow down start search
         start_line_idx = node.lineno - 1
         
         # Note: get_source_segment returns the exact text.
         # But if there are multiple identical calls, we need the one at start_line.
         # Python's ast doesn't give byte offsets easily (node.col_offset is char offset on line).
         
         # Let's trust that we can replace the text.
         # Issue: indentation. 'new_call_code' from unparse has no indentation.
         # 'segment' has internal newlines with indentation.
         
         # We need to re-indent 'new_call_code'.
         # Get indentation of start line
         line_prefix = flow_lines[start_line_idx][:node.col_offset]
         indent = ""
         for char in line_prefix:
             if char.isspace():
                 indent += char
             else:
                 indent = "" # Reset if non-space char found before node ? 
                 # Actually node.col_offset is where the call starts.
                 # If call is `x = client.foo()`, col_offset points to `client`.
                 # Indentation is at start of line.
         
         # Just replace the segment in the original text?
         # If I replace `client.call(a=1, b=2)` with `client.call(a=1)`, it should work.
         # If it spans lines, `ast.get_source_segment` captures usage.
         
         # Let's try a simpler approach: 
         # Regex removal of specific keywords, but balanced.
         pass
         
    # Let's go with the replace call strategy using count=1 from the known location
    # Ideally I'd use `LibCST` but I can't.
    
    # Re-implementation using purely AST replacement string building is risky for format
    # But better than broken code.
    
    # Let's try:
    for kw_name in removed_params:
         # Simple regex to remove `kw_name=...,` or `, kw_name=...`
         # This is what the old code did and failed on nested dicts.
         # Example: payload={"a": (1,2)}
         
         # Regex for balanced parens/braces is impossible with re.
         # We MUST use the AST node filtering + unparse for the args part.
         
         pass

    # Final decision: Use AST unparse on the node, replace the segment.
    # Accept that comments INSIDE the call might be lost.
    
    lines = flow_code.splitlines()
    for node, segment, new_code in replacements:
        # segment is the exact text to replace.
        # We need to find where it is.
        # node.lineno, node.col_offset (1-based lines, 0-based cols)
        
        start_line = node.lineno - 1
        start_col = node.col_offset
        end_line = node.end_lineno - 1
        end_col = node.end_col_offset
        
        # Extract preamble and postscript
        # We need to reconstruct the file.
        # But since we have multiple replacements and indices shift, 
        # let's do one replacement (the first one) and return?
        # Recursion!
        
        # Replace ONLY ONE, then recurse.
        # This handles offset shifting.
        
        pre = []
        for i in range(start_line):
            pre.append(lines[i])
        
        # Line containing start of call
        start_line_content = lines[start_line]
        pre_text = start_line_content[:start_col]
        
        # Line containing end of call
        end_line_content = lines[end_line]
        post_text = end_line_content[end_col:]
        
        # Middle is replaced by new_code
        # But new_code might be multi-line. We shoud indent it if needed?
        # ast.unparse returns valid python but no indentation (except internal blocks?)
        # For a call, it's usually one line or standard style.
        
        # Construct the new text chunk
        new_text = pre_text + new_code + post_text
        
        # Reassemble
        # Note: If start_line == end_line, this logic holds.
        # If start_line != end_line, we skip lines between them.
        
        new_source_lines = pre + new_text.splitlines() + lines[end_line+1:]
        new_source = "\n".join(new_source_lines)
        
        # Verify valid syntax (Sanity check)
        try:
            ast.parse(new_source)
            logger.info(f"[BUG-002] AST-based fix applied for signature mismatch")
            # Recurse to handle other calls if any
            # BUG FIX: Merge removed params from recursion
            final_code, subsequent_removed = fix_flow_client_signature_mismatch(new_source, client_method_name, valid_params)
            return final_code, removed_params + subsequent_removed
        except SyntaxError:
            logger.warning(f"[BUG-002] AST fix produced invalid syntax, reverting to original")
            return flow_code, []
            
    return flow_code, removed_params



# =============================================================================
# SKELETON TEMPLATE IMPORT GENERATION
# =============================================================================

def get_client_skeleton_imports() -> str:
    """Get correct imports for client skeleton templates."""
    return f'''from typing import Dict, Any, Optional

from {RUNTIME_PACKAGE} import IntegrationHttpClient, IntegrationError
'''


def get_flow_skeleton_imports(client_module: str, client_class: str) -> str:
    """Get correct imports for flow skeleton templates."""
    return f'''from typing import Dict, Any

from integrations.clients.{client_module} import {client_class}
from {RUNTIME_PACKAGE} import IntegrationError
'''


def get_test_skeleton_imports(flow_module: str, flow_function: str) -> str:
    """Get correct imports for test skeleton templates."""
    return f'''import os
from unittest.mock import MagicMock, patch

import pytest
from integrations.flows.{flow_module} import {flow_function}
'''


# =============================================================================
# BUG-002 FIX: INLINE MODE IMPORT STRIPPING
# =============================================================================

# V42-002: Inline IntegrationHttpClient class definition for policy_mode=inline
# This provides a minimal, standalone HTTP client that doesn't require external dependencies
INLINE_HTTP_CLIENT_CLASS = '''
# V42-002: Inline HTTP client class (policy_mode=inline)
# Standalone implementation - no external dependencies required
from typing import Dict, Any, Optional
import httpx


class IntegrationError(Exception):
    """Integration operation error."""
    pass


class IntegrationHttpClient:
    """
    Standalone HTTP client for API integrations.
    
    This is an inline implementation that doesn't require external packages.
    Provides standard HTTP methods with retry logic and error handling.
    """
    
    def __init__(
        self,
        base_url: str = "",
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
        retries: int = 3,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the HTTP client.
        
        Args:
            base_url: Base URL for API requests
            api_key: Optional API key for authentication
            timeout_s: Request timeout in seconds
            retries: Number of retry attempts for failed requests
            **kwargs: Additional configuration options
        """
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.retries = retries
        self._client: Optional[httpx.Client] = None
    
    @property
    def client(self) -> httpx.Client:
        """Lazy-initialize httpx client."""
        if self._client is None:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=self.timeout_s,
            )
        return self._client
    
    def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """
        Make an HTTP request with retry logic.
        
        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: URL path (appended to base_url)
            params: Query parameters
            json: JSON body payload
            headers: Additional headers
            **kwargs: Additional request options
            
        Returns:
            httpx.Response object
            
        Raises:
            IntegrationError: On request failure after retries
        """
        url = f"{self.base_url}{path}" if not path.startswith("http") else path
        last_error = None
        
        for attempt in range(self.retries + 1):
            try:
                response = self.client.request(
                    method=method,
                    url=url,
                    params=params,
                    json=json,
                    headers=headers,
                    **kwargs,
                )
                response.raise_for_status()
                return response
            except httpx.HTTPStatusError as e:
                # Don't retry client errors (4xx except 429)
                if 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                    raise IntegrationError(
                        f"HTTP {e.response.status_code}: {e.response.text}"
                    ) from e
                last_error = e
            except httpx.RequestError as e:
                last_error = e
        
        raise IntegrationError(
            f"Request failed after {self.retries + 1} attempts: {last_error}"
        ) from last_error
    
    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None
    
    def __enter__(self) -> "IntegrationHttpClient":
        return self
    
    def __exit__(self, *args: Any) -> None:
        self.close()
'''


def strip_runtime_imports_for_inline_mode(code: str) -> Tuple[str, List[ImportFix]]:
    """
    Strip integration_coworker_runtime imports from code for inline mode.
    
    BUG-002: When policy_mode='inline', generated code should NOT import from
    integration_coworker_runtime since that package won't exist in the target repo.
    
    V42-002: Enhanced to handle IntegrationHttpClient properly:
    - Detects if code inherits from IntegrationHttpClient
    - Provides inline IntegrationHttpClient implementation when needed
    - Ensures standalone code works without external runtime package
    
    V42-003: Enhanced to handle hallucinated framework imports directly:
    - Strips imports from integration_framework.* patterns
    - Strips imports from integration_coworker.runtime.* patterns
    - Adds inline implementations even if hallucination fix wasn't applied
    
    This function:
    1. Removes import lines for integration_coworker_runtime
    2. Removes import lines for hallucinated framework packages
    3. Adds inline IntegrationError class if the code uses it
    4. Adds inline IntegrationHttpClient class if the code inherits from it
    5. Logs what was removed for debugging
    
    Args:
        code: Generated Python code that may contain runtime imports
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[ImportFix] = []
    lines = code.split('\n')
    modified_lines = []
    
    uses_integration_error = "IntegrationError" in code
    uses_http_client = "IntegrationHttpClient" in code
    needs_inline_error = False
    needs_inline_http_client = False
    has_existing_error_def = "class IntegrationError" in code
    has_existing_client_def = "class IntegrationHttpClient" in code
    
    # V42-002: Check if code inherits from IntegrationHttpClient
    inherits_from_http_client = bool(re.search(r'class\s+\w+\s*\(\s*IntegrationHttpClient\s*\)', code))
    
    # V42-003: Patterns to strip in inline mode (runtime + hallucinated)
    # These are patterns where we should strip the import and add inline definitions
    inline_mode_strip_patterns = [
        RUNTIME_PACKAGE,  # integration_coworker_runtime
        "integration_framework",  # Hallucinated framework
        "integration_coworker.runtime",  # Internal package
    ]
    
    # Track what we're removing
    removed_runtime_imports = []
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # V42-003: Check for any import that should be stripped in inline mode
        should_strip = False
        strip_reason = None
        
        for pattern in inline_mode_strip_patterns:
            if f"from {pattern}" in line or f"import {pattern}" in line:
                should_strip = True
                strip_reason = pattern
                break
        
        if should_strip:
            # Record the fix
            fixes.append(ImportFix(
                line_number=i + 1,
                original_line=line,
                fixed_line="# (removed - inline mode)",
                reason=f"V42-003: Stripped import from '{strip_reason}' for inline mode",
            ))
            
            # Check if this import had IntegrationError
            if "IntegrationError" in line:
                needs_inline_error = True
                removed_runtime_imports.append("IntegrationError")
            
            # V42-002: Check if this import had IntegrationHttpClient
            if "IntegrationHttpClient" in line:
                needs_inline_http_client = True
                removed_runtime_imports.append("IntegrationHttpClient")
            
            # Skip this line (don't add to output)
            continue
        
        modified_lines.append(line)
    
    # V42-002: If code inherits from IntegrationHttpClient, we need to provide inline definition
    # This is critical because otherwise the class would inherit from undefined base
    if (needs_inline_http_client or inherits_from_http_client) and uses_http_client and not has_existing_client_def:
        # V43-001 Fix: Insert inline class BEFORE any class that uses it, not just "after imports"
        # The previous logic found "first non-import line" but that could be a class definition
        # that inherits from IntegrationHttpClient, causing NameError.
        #
        # New strategy:
        # 1. Find where to insert: after imports/docstrings but BEFORE any class definition
        # 2. Specifically look for 'class X(IntegrationHttpClient)' and insert before it
        
        insert_index = 0
        found_class_def = False
        
        for j, line in enumerate(modified_lines):
            stripped = line.strip()
            
            # Skip empty lines, imports, comments, and docstring markers at start
            if not stripped:
                continue
            if stripped.startswith(('import ', 'from ')):
                insert_index = j + 1
                continue
            if stripped.startswith('#'):
                insert_index = j + 1
                continue
            if stripped.startswith(('"""', "'''")):
                # Docstring - skip past it
                insert_index = j + 1
                continue
            
            # If we hit a class definition that inherits from IntegrationHttpClient,
            # we MUST insert before it
            if stripped.startswith('class ') and 'IntegrationHttpClient' in stripped:
                insert_index = j  # Insert RIGHT HERE, before this class
                found_class_def = True
                break
            
            # If we hit any other code (not an import/comment/docstring), 
            # insert here if we haven't found a specific class yet
            if not found_class_def:
                insert_index = j
                break
        
        modified_lines.insert(insert_index, INLINE_HTTP_CLIENT_CLASS)
        fixes.append(ImportFix(
            line_number=insert_index + 1,
            original_line="",
            fixed_line="[inline IntegrationHttpClient class]",
            reason="V42-002: Added inline IntegrationHttpClient definition for inheritance",
        ))
        
        # No need for separate IntegrationError - it's included in INLINE_HTTP_CLIENT_CLASS
        needs_inline_error = False
    elif needs_inline_error and uses_integration_error and not has_existing_error_def:
        # Only IntegrationError needed, not the full client
        inline_error_def = '''
# BUG-002: Inline error class (policy_mode=inline)
class IntegrationError(Exception):
    """Integration operation error for inline-mode code."""
    pass
'''
        # Insert after imports section (find first non-import, non-empty line)
        insert_index = 0
        for j, line in enumerate(modified_lines):
            stripped = line.strip()
            if stripped and not stripped.startswith(('import ', 'from ', '#', '"""', "'''")):
                insert_index = j
                break
            insert_index = j + 1
        
        modified_lines.insert(insert_index, inline_error_def)
        fixes.append(ImportFix(
            line_number=insert_index + 1,
            original_line="",
            fixed_line=inline_error_def.strip(),
            reason="BUG-002: Added inline IntegrationError definition",
        ))
    
    fixed_code = '\n'.join(modified_lines)
    
    if fixes:
        logger.info(f"[BUG-002] Stripped {len(removed_runtime_imports)} runtime imports for inline mode")
        for fix in fixes[:3]:  # Log first 3 fixes
            logger.debug(f"  {fix.reason}")
    
    return fixed_code, fixes


def task_forbids_runtime_imports(task_description: str) -> bool:
    """
    V40-003: Check if task description explicitly forbids runtime imports.
    
    Users sometimes explicitly state they don't want integration_coworker_runtime
    imports in their generated code. This function detects such requirements.
    
    Patterns detected:
    - "NOT integration_coworker_runtime"
    - "do not use integration_coworker_runtime"
    - "without integration_coworker_runtime"
    - "no integration_coworker_runtime"
    - "standalone" (implies no external runtime dependencies)
    
    Args:
        task_description: The task description text
        
    Returns:
        True if runtime imports are explicitly forbidden
    """
    if not task_description:
        return False
    
    # Normalize for matching
    text = task_description.lower()
    
    # Explicit negative patterns
    forbidden_patterns = [
        "not integration_coworker_runtime",
        "no integration_coworker_runtime", 
        "without integration_coworker_runtime",
        "don't use integration_coworker_runtime",
        "do not use integration_coworker_runtime",
        "avoid integration_coworker_runtime",
        "exclude integration_coworker_runtime",
        "standalone code",
        "self-contained code",
        "no external runtime",
    ]
    
    for pattern in forbidden_patterns:
        if pattern in text:
            logger.info(f"[V40-003] Task forbids runtime imports: matched '{pattern}'")
            return True
    
    return False


def fix_imports_for_policy_mode(
    code: str, 
    policy_mode: str = "inline",
    artifact_type: str = "client",
    task_description: str = "",
) -> Tuple[str, List[ImportFix]]:
    """
    Fix imports based on the code generation policy mode.
    
    BUG-002: This is the main entry point for policy-aware import fixing.
    V40-003: Enhanced to detect task-level runtime import restrictions.
    V43-002: Also fixes implicit Optional type annotations.
    V45-002: Also fixes missing stdlib/typing imports.
    V45-004: Also fixes cross-language syntax hallucinations (JS/TS → Python).
    
    - inline mode: Strip runtime imports, use inline definitions
    - runtime mode: Keep runtime imports, validate they're correct
    - task override: If task explicitly forbids runtime, force inline mode
    
    Args:
        code: Generated Python code
        policy_mode: "inline" or "runtime"
        artifact_type: Type of artifact for logging
        task_description: Optional task description to check for explicit restrictions
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    all_fixes: List[ImportFix] = []
    fixed_code = code
    
    # V40-003: Check if task explicitly forbids runtime imports
    # This overrides even "runtime" policy_mode
    force_inline = task_forbids_runtime_imports(task_description)
    if force_inline and policy_mode == "runtime":
        logger.warning(
            f"[V40-003] Task explicitly forbids runtime imports - "
            f"overriding policy_mode='runtime' to 'inline' for {artifact_type}"
        )
        policy_mode = "inline"
    
    # V45-004: Fix cross-language hallucinations FIRST (before any parsing)
    # This catches JS/TS syntax like "new ClassName()" that causes syntax errors
    fixed_code, cross_lang_fixes = fix_cross_language_hallucinations(fixed_code)
    all_fixes.extend(cross_lang_fixes)
    if cross_lang_fixes:
        logger.info(
            f"[V45-004] Fixed {len(cross_lang_fixes)} cross-language hallucinations in {artifact_type}"
        )
    
    # Fix any hallucinated import patterns (applies to both modes)
    fixed_code, hallucination_fixes = fix_imports_in_code(fixed_code)
    all_fixes.extend(hallucination_fixes)
    
    # V45-002: Fix missing stdlib/typing imports (applies to both modes)
    # This catches "Optional", "List", "Dict" usage without imports
    fixed_code, stdlib_fixes = fix_missing_stdlib_imports(fixed_code)
    all_fixes.extend(stdlib_fixes)
    if stdlib_fixes:
        logger.info(
            f"[V45-002] Added {len(stdlib_fixes)} missing stdlib/typing imports in {artifact_type}"
        )
    
    # V43-002: Fix implicit Optional type annotations (applies to both modes)
    # This prevents mypy errors like "Incompatible default for argument"
    fixed_code, type_fixes = fix_implicit_optional_annotations(fixed_code)
    if type_fixes:
        # Convert TypeAnnotationFix to ImportFix for consistent return type
        for type_fix in type_fixes:
            all_fixes.append(ImportFix(
                line_number=type_fix.line_number,
                original_line=type_fix.original,
                fixed_line=type_fix.fixed,
                reason=type_fix.reason,
            ))
        logger.info(
            f"[V43-002] Fixed {len(type_fixes)} implicit Optional annotations in {artifact_type}"
        )
    
    # V45-008: Fix numeric type mismatches (applies to both modes)
    # This prevents mypy errors like "int annotation with float default"
    fixed_code, numeric_fixes = fix_numeric_type_mismatches(fixed_code)
    if numeric_fixes:
        # Convert TypeAnnotationFix to ImportFix for consistent return type
        for numeric_fix in numeric_fixes:
            all_fixes.append(ImportFix(
                line_number=numeric_fix.line_number,
                original_line=numeric_fix.original,
                fixed_line=numeric_fix.fixed,
                reason=numeric_fix.reason,
            ))
        logger.info(
            f"[V45-008] Fixed {len(numeric_fixes)} numeric type mismatches in {artifact_type}"
        )
    
    # V45-013: Fix hallucinated [REDACTED] type annotations (applies to both modes)
    # LLMs sometimes see redacted content in context and hallucinate it as a type
    fixed_code, redacted_fixes = fix_redacted_type_hallucinations(fixed_code)
    if redacted_fixes:
        # Convert TypeAnnotationFix to ImportFix for consistent return type
        for redacted_fix in redacted_fixes:
            all_fixes.append(ImportFix(
                line_number=redacted_fix.line_number,
                original_line=redacted_fix.original,
                fixed_line=redacted_fix.fixed,
                reason=redacted_fix.reason,
            ))
        logger.info(
            f"[V45-013] Fixed {len(redacted_fixes)} hallucinated [REDACTED] annotations in {artifact_type}"
        )
    
    # Then, apply mode-specific fixes
    if policy_mode == "inline":
        # For inline mode, strip any remaining runtime imports
        fixed_code, inline_fixes = strip_runtime_imports_for_inline_mode(fixed_code)
        all_fixes.extend(inline_fixes)
        
        if inline_fixes:
            logger.info(
                f"[BUG-002] Applied inline mode import fixes to {artifact_type}: "
                f"{len(inline_fixes)} fixes"
            )
    else:
        # V45-003: For runtime mode, add missing runtime imports first
        fixed_code, runtime_import_fixes = add_missing_runtime_imports(fixed_code)
        all_fixes.extend(runtime_import_fixes)
        
        if runtime_import_fixes:
            logger.info(
                f"[V45-003] Added missing runtime imports for {artifact_type}: "
                f"{len(runtime_import_fixes)} fixes"
            )
        
        # Then validate runtime imports are valid
        validation = validate_runtime_imports(fixed_code)
        if not validation.is_valid:
            for issue in validation.unresolved_imports:
                logger.warning(f"[BUG-002] Runtime mode import issue: {issue}")
    
    return fixed_code, all_fixes


# =============================================================================
# V43-002: Type Annotation Fixer
# =============================================================================
# LLMs often generate code with implicit Optional patterns:
#   - `param: str = None` instead of `param: Optional[str] = None`
# This causes mypy errors: "Incompatible default for argument"
# =============================================================================

@dataclass
class TypeAnnotationFix:
    """Record of a type annotation fix applied."""
    line_number: int
    original: str
    fixed: str
    reason: str


def fix_implicit_optional_annotations(code: str) -> Tuple[str, List[TypeAnnotationFix]]:
    """
    Fix implicit Optional type annotations in generated code.
    
    V43-002: LLMs often generate `param: str = None` instead of the correct
    `param: Optional[str] = None`. This causes mypy errors like:
        "Incompatible default for argument 'hint' (default has type 'None',
         argument has type 'str')"
    
    This function:
    1. Finds parameters with `type = None` pattern
    2. Converts them to `Optional[type] = None`
    3. Ensures `Optional` is imported from typing
    
    Args:
        code: Generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[TypeAnnotationFix] = []
    lines = code.split('\n')
    modified_lines = []
    needs_optional_import = False
    
    # Pattern to match implicit Optional: `name: Type = None`
    # This matches: param_name: SomeType = None
    # But NOT: param_name: Optional[SomeType] = None
    # Handles complex types like List[str], Dict[str, int], etc.
    implicit_optional_pattern = re.compile(
        r'(\w+)\s*:\s*((?!Optional\b)[A-Za-z_][A-Za-z0-9_\[\], \.]*)\s*=\s*None\b'
    )
    
    for i, line in enumerate(lines):
        original_line = line
        
        # Find all matches in this line
        matches = list(implicit_optional_pattern.finditer(line))
        
        if matches:
            # Process matches from right to left to preserve positions
            for match in reversed(matches):
                param_name = match.group(1)
                type_hint = match.group(2).strip()
                
                # Skip if type already contains Optional
                if 'Optional' in type_hint:
                    continue
                
                # Build the fixed annotation
                fixed_annotation = f'{param_name}: Optional[{type_hint}] = None'
                
                # Replace in line
                line = line[:match.start()] + fixed_annotation + line[match.end():]
                needs_optional_import = True
                
                fixes.append(TypeAnnotationFix(
                    line_number=i + 1,
                    original=f'{param_name}: {type_hint} = None',
                    fixed=fixed_annotation,
                    reason="V43-002: Fixed implicit Optional annotation",
                ))
        
        modified_lines.append(line)
    
    fixed_code = '\n'.join(modified_lines)
    
    # If we made fixes, ensure Optional is imported
    if needs_optional_import and fixes:
        fixed_code = _ensure_optional_import(fixed_code)
    
    if fixes:
        logger.info(f"[V43-002] Fixed {len(fixes)} implicit Optional annotations")
        for fix in fixes[:3]:  # Log first 3 fixes
            logger.debug(f"  Line {fix.line_number}: {fix.original} -> {fix.fixed}")
    
    return fixed_code, fixes


# =============================================================================
# V45-008: Float/Int Type Mismatch Fixer
# =============================================================================
# LLMs often generate code with mismatched numeric types:
#   - `timeout: int = 30.0` - int annotation with float default
#   - `count: float = 10` - float annotation with int default (less common)
# This causes mypy errors: "Incompatible default for argument"
# =============================================================================

def fix_numeric_type_mismatches(code: str) -> Tuple[str, List[TypeAnnotationFix]]:
    """
    Fix numeric type mismatches in generated code.
    
    V45-008: LLMs often generate `param: int = 30.0` instead of the correct
    `param: float = 30.0` or `param: int = 30`. This causes mypy errors like:
        "Incompatible default for argument 'timeout' (default has type 'float',
         argument has type 'int')"
    
    Fix strategy:
    - If annotation is `int` but default is float (e.g., 30.0), change annotation to `float`
    - If annotation is `float` but default is int (e.g., 30), leave as-is (int is subtype of float)
    
    Args:
        code: Generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[TypeAnnotationFix] = []
    lines = code.split('\n')
    modified_lines = []
    
    # Pattern to match int annotation with float default: `name: int = 30.0`
    # This matches: param_name: int = 123.45
    # Must have explicit decimal point to be considered float
    int_with_float_pattern = re.compile(
        r'(\w+)\s*:\s*(int)\s*=\s*(-?\d+\.\d*)\b'
    )
    
    for i, line in enumerate(lines):
        original_line = line
        
        # Find all int-with-float-default matches in this line
        matches = list(int_with_float_pattern.finditer(line))
        
        if matches:
            # Process matches from right to left to preserve positions
            for match in reversed(matches):
                param_name = match.group(1)
                float_value = match.group(3)
                
                # Build the fixed annotation - change int to float
                fixed_annotation = f'{param_name}: float = {float_value}'
                
                # Replace in line
                line = line[:match.start()] + fixed_annotation + line[match.end():]
                
                fixes.append(TypeAnnotationFix(
                    line_number=i + 1,
                    original=f'{param_name}: int = {float_value}',
                    fixed=fixed_annotation,
                    reason="V45-008: Fixed int annotation with float default",
                ))
        
        modified_lines.append(line)
    
    fixed_code = '\n'.join(modified_lines)
    
    if fixes:
        logger.info(f"[V45-008] Fixed {len(fixes)} numeric type mismatches")
        for fix in fixes[:3]:  # Log first 3 fixes
            logger.debug(f"  Line {fix.line_number}: {fix.original} -> {fix.fixed}")
    
    return fixed_code, fixes


# =============================================================================
# V45-013: Redacted Token Fixer
# =============================================================================
# LLMs sometimes hallucinate [REDACTED] as a type when they see redacted content
# in error messages or logs included in their context.
# Example: `api_key: [REDACTED] = None` instead of `api_key: Optional[str] = None`
# This causes syntax errors as [REDACTED] is not a valid Python type.
# =============================================================================

def fix_redacted_type_hallucinations(code: str) -> Tuple[str, List[TypeAnnotationFix]]:
    """
    Fix hallucinated [REDACTED] type annotations in generated code.
    
    V45-013: LLMs that see redacted content in their context sometimes generate
    invalid type annotations using [REDACTED] as a type placeholder:
        `api_key: [REDACTED] = None` - invalid Python syntax
        
    This function:
    1. Finds type annotations containing [REDACTED]
    2. Replaces with appropriate types based on common parameter names
    3. Falls back to Optional[str] for unknown parameters
    
    Args:
        code: Generated Python code
        
    Returns:
        Tuple of (fixed_code, list_of_fixes_applied)
    """
    fixes: List[TypeAnnotationFix] = []
    lines = code.split('\n')
    modified_lines = []
    needs_optional_import = False
    
    # Pattern to match [REDACTED] in type annotations
    # Matches: `param: [REDACTED] = value` or `param: [REDACTED]`
    redacted_pattern = re.compile(
        r'(\w+)\s*:\s*\[REDACTED\]\s*(=\s*[^,\)]+)?'
    )
    
    # Heuristic type mapping based on common parameter names
    # When we don't know what type was redacted, guess based on parameter name
    type_hints_by_name = {
        'api_key': 'Optional[str]',
        'token': 'Optional[str]',
        'secret': 'Optional[str]',
        'password': 'Optional[str]',
        'key': 'Optional[str]',
        'auth': 'Optional[str]',
        'authorization': 'Optional[str]',
        'bearer': 'Optional[str]',
        'access_token': 'Optional[str]',
        'refresh_token': 'Optional[str]',
        'client_id': 'Optional[str]',
        'client_secret': 'Optional[str]',
        'url': 'Optional[str]',
        'base_url': 'Optional[str]',
        'endpoint': 'Optional[str]',
        'timeout': 'Optional[float]',
        'retries': 'Optional[int]',
    }
    
    for i, line in enumerate(lines):
        original_line = line
        
        # Find all matches in this line
        matches = list(redacted_pattern.finditer(line))
        
        if matches:
            # Process matches from right to left to preserve positions
            for match in reversed(matches):
                param_name = match.group(1)
                default_part = match.group(2) or ''
                
                # Determine replacement type based on parameter name
                param_lower = param_name.lower()
                replacement_type = type_hints_by_name.get(
                    param_lower, 
                    'Optional[str]'  # Default fallback
                )
                
                # If there's a default of None, ensure we use Optional
                if '= None' in default_part or '=None' in default_part:
                    if not replacement_type.startswith('Optional'):
                        replacement_type = f'Optional[{replacement_type}]'
                
                # Build the fixed annotation
                fixed_annotation = f'{param_name}: {replacement_type}{default_part}'
                
                # Replace in line
                line = line[:match.start()] + fixed_annotation + line[match.end():]
                
                if 'Optional' in replacement_type:
                    needs_optional_import = True
                
                fixes.append(TypeAnnotationFix(
                    line_number=i + 1,
                    original=f'{param_name}: [REDACTED]{default_part}',
                    fixed=fixed_annotation,
                    reason="V45-013: Fixed hallucinated [REDACTED] type annotation",
                ))
        
        modified_lines.append(line)
    
    fixed_code = '\n'.join(modified_lines)
    
    # If we made fixes, ensure Optional is imported
    if needs_optional_import and fixes:
        fixed_code = _ensure_optional_import(fixed_code)
    
    if fixes:
        logger.info(f"[V45-013] Fixed {len(fixes)} hallucinated [REDACTED] type annotations")
        for fix in fixes[:3]:  # Log first 3 fixes
            logger.debug(f"  Line {fix.line_number}: {fix.original} -> {fix.fixed}")
    
    return fixed_code, fixes


def _ensure_optional_import(code: str) -> str:
    """
    Ensure Optional is imported from typing.
    
    Handles various import styles:
    - `from typing import X` -> `from typing import X, Optional`
    - No typing import -> adds `from typing import Optional`
    """
    lines = code.split('\n')
    
    # Check if Optional is already imported
    optional_imported = False
    typing_import_line = None
    
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Check for existing Optional import
        if 'Optional' in line and ('from typing' in line or 'typing.Optional' in line):
            optional_imported = True
            break
        
        # Find the typing import line
        if stripped.startswith('from typing import') and typing_import_line is None:
            typing_import_line = i
    
    if optional_imported:
        return code
    
    if typing_import_line is not None:
        # Add Optional to existing typing import
        line = lines[typing_import_line]
        # Handle multi-line imports (parentheses)
        if '(' in line and ')' not in line:
            # Multi-line import - add after opening paren
            line = line.rstrip()
            if not line.endswith(','):
                line += ','
            line += ' Optional,'
        elif ')' in line:
            # Import closes on same line
            line = line.replace(')', ', Optional)')
        else:
            # Simple single-line import
            line = line.rstrip()
            if line.endswith(','):
                line = line[:-1]
            # Find the closing part and add Optional
            line += ', Optional'
        
        lines[typing_import_line] = line
    else:
        # No typing import - add one after other imports
        insert_idx = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith(('import ', 'from ')):
                insert_idx = i + 1
            elif stripped and not stripped.startswith('#') and not stripped.startswith('"""'):
                break
        
        lines.insert(insert_idx, 'from typing import Optional')
    
    return '\n'.join(lines)


# =============================================================================
# V44-002: Line-Based Import Sanitizer (AST-Free)
# =============================================================================
# This sanitizer works on raw text WITHOUT AST parsing, making it robust
# against syntax errors. It's designed to strip/fix hallucinated imports
# even when the code is too broken for AST parsing to work.
# =============================================================================

# Patterns to completely remove (hallucinated frameworks that don't exist)
HALLUCINATED_IMPORT_PATTERNS = [
    r'^\s*from\s+integration_framework\b',
    r'^\s*from\s+integration_coworker\.runtime\b',
    r'^\s*import\s+integration_framework\b',
    r'^\s*import\s+integration_coworker\.runtime\b',
]

# Patterns to replace (redirect to correct package)
IMPORT_REDIRECT_PATTERNS = {
    # Pattern -> (replacement_from, keep_imports_list)
    r'from\s+integration_framework\.core\.client\s+import\s+(.+)': 
        ('integration_coworker_runtime', ['IntegrationHttpClient', 'IntegrationClient']),
    r'from\s+integration_framework\.core\.exceptions\s+import\s+(.+)': 
        ('integration_coworker_runtime', ['IntegrationError']),
    r'from\s+integration_framework\.client\s+import\s+(.+)': 
        ('integration_coworker_runtime', ['IntegrationHttpClient', 'IntegrationClient']),
    r'from\s+integration_framework\.exceptions\s+import\s+(.+)': 
        ('integration_coworker_runtime', ['IntegrationError']),
}


def sanitize_hallucinated_imports_line_based(
    code: str,
    strip_instead_of_redirect: bool = True,
) -> Tuple[str, List[ImportFix]]:
    """
    V44-002: Sanitize hallucinated imports using line-based processing.
    
    This function works WITHOUT AST parsing, making it robust against
    syntax errors. It processes code line-by-line to:
    
    1. Strip imports from hallucinated packages (integration_framework.*)
    2. Optionally redirect to correct packages instead of stripping
    3. Preserve code structure and indentation
    4. Log all modifications for debugging
    
    This is a "nuclear option" that can fix hallucinated imports even when
    the code is completely broken and AST parsing fails. It should run BEFORE
    any AST-based processing.
    
    Args:
        code: Raw Python code (may have syntax errors)
        strip_instead_of_redirect: If True, remove hallucinated imports entirely.
                                   If False, redirect to integration_coworker_runtime.
    
    Returns:
        Tuple of (sanitized_code, list_of_fixes_applied)
    """
    fixes: List[ImportFix] = []
    lines = code.split('\n')
    fixed_lines = []
    
    # Track what imports were stripped (for inline class injection later)
    stripped_classes = set()
    
    # V44-002 Bug Fix: Handle multi-line imports correctly
    skip_until_symbol = None  # None, ')', etc.
    
    for i, line in enumerate(lines):
        original_line = line
        stripped_content = line.strip()
        
        # Skip empty lines and comments
        if not stripped_content or stripped_content.startswith('#'):
            if skip_until_symbol:
                 # Even inside a skip block, keep comments? 
                 # Usually if we strip the parent import, we strip the children too.
                 # But comments might be relevant. Let's just strip everything in the block.
                 # Actually, let's keep it consistent: ignore comments logic for skipping.
                 pass
            else:
                fixed_lines.append(line)
                continue
        
        # If we are in a skip block (multi-line import being stripped)
        if skip_until_symbol:
            # Record as part of the previous fix (implicit)
            # Or just create a new fix record
            fixes.append(ImportFix(
                line_number=i + 1,
                original_line=original_line,
                fixed_line="# (stripped - multi-line part)",
                reason=f"V44-002: Stripped multi-line continuation",
            ))
            
            if strip_instead_of_redirect:
                # Still check if we found the end symbol
                if skip_until_symbol in line:
                    skip_until_symbol = None
                continue
            else:
                # Redirect mode not supported for multi-line generally in this simplistic fixer
                # Fallback to comment out
                fixed_lines.append(f"# STRIPPED: {stripped_content}")
                if skip_until_symbol in line:
                    skip_until_symbol = None
                continue

        # Check for hallucinated import patterns
        should_strip = False
        strip_reason = None
        
        for pattern in HALLUCINATED_IMPORT_PATTERNS:
            if re.match(pattern, line):
                should_strip = True
                strip_reason = f"Hallucinated import pattern: {pattern}"
                
                # Track what classes were being imported
                import_match = re.search(r'import\s+(.+)$', stripped_content)
                if import_match:
                    imports_str = import_match.group(1)
                    for cls in ['IntegrationHttpClient', 'IntegrationError', 'IntegrationClient']:
                        if cls in imports_str:
                            stripped_classes.add(cls)
                break
        
        if should_strip:
            # Check if this is a multi-line import start
            if stripped_content.endswith('(') or stripped_content.endswith('\\'):
                 skip_until_symbol = ')' if stripped_content.endswith('(') else None 
                 # Backslash continuation is line-by-line, handled by next line logic usually
                 # But blindly stripping line i might leave line i+1 as invalid syntax depending on context.
                 # Assuming parenthesized imports for now.

            # Record the fix
            fixes.append(ImportFix(
                line_number=i + 1,
                original_line=original_line,
                fixed_line="# (stripped - hallucinated import)",
                reason=f"V44-002: {strip_reason}",
            ))
            
            # Either strip entirely or replace with comment
            if strip_instead_of_redirect:
                # Don't add the line at all (but log what was stripped)
                logger.debug(f"[V44-002] Stripped line {i+1}: {stripped_content}")
                continue
            else:
                # Add as comment (preserves line numbers for debugging)
                fixed_lines.append(f"# STRIPPED: {stripped_content}")
                continue
        
        # Check for redirect patterns (if not stripping)
        if not strip_instead_of_redirect:
            for pattern, (target_pkg, valid_imports) in IMPORT_REDIRECT_PATTERNS.items():
                match = re.search(pattern, line)
                if match:
                    imports_str = match.group(1)
                    # Filter to only valid imports
                    imports = [i.strip() for i in imports_str.split(',')]
                    valid_filtered = [i for i in imports if i in valid_imports]
                    
                    if valid_filtered:
                        # Create redirected import
                        indent = re.match(r'^(\s*)', line).group(1)
                        new_line = f"{indent}from {target_pkg} import {', '.join(valid_filtered)}"
                        
                        fixes.append(ImportFix(
                            line_number=i + 1,
                            original_line=original_line,
                            fixed_line=new_line,
                            reason=f"V44-002: Redirected to {target_pkg}",
                        ))
                        
                        fixed_lines.append(new_line)
                        continue
        
        # No modification needed
        fixed_lines.append(line)
    
    # Log summary
    if fixes:
        logger.info(f"[V44-002] Line-based sanitizer applied {len(fixes)} fixes")
        if stripped_classes:
            logger.info(f"[V44-002] Stripped classes: {stripped_classes}")
    
    return '\n'.join(fixed_lines), fixes


def pre_ast_sanitize_code(code: str) -> Tuple[str, List[ImportFix]]:
    """
    V44-002: Pre-process code before any AST-based operations.
    
    This is the recommended entry point for sanitizing LLM-generated code
    before attempting AST parsing. It applies multiple fixes in the CORRECT
    order:
    
    1. Fix concatenated statements FIRST (line-based, AST-free)
       - This splits `from X import A from Y import B` into two separate lines
       - MUST happen before import sanitization so each import is on its own line
    2. THEN sanitize hallucinated imports (line-based, AST-free)
       - Now each import line can be individually checked and stripped
    
    After this function runs, the code should be parseable by AST for
    further validation and fixing.
    
    Args:
        code: Raw LLM-generated code
        
    Returns:
        Tuple of (sanitized_code, list_of_all_fixes)
    """
    all_fixes: List[ImportFix] = []
    
    # Step 1: Fix concatenated statements FIRST
    # This MUST run before import sanitization because it splits lines like:
    #   `from X import A from Y import B` → two separate lines
    # Without this, the whole concatenated line would be stripped as one unit
    # (This is in security.py but we import it here for the unified API)
    try:
        from integration_coworker.codegen.security import _fix_concatenated_statements
        fixed_code = _fix_concatenated_statements(code)
        if fixed_code != code:
            all_fixes.append(ImportFix(
                line_number=0,
                original_line="",
                fixed_line="",
                reason="V44-001: Fixed concatenated statements",
            ))
            code = fixed_code
            logger.debug("[V44-001] Fixed concatenated statements before import sanitization")
    except ImportError:
        logger.warning("[V44-002] Could not import _fix_concatenated_statements")
    
    # Step 2: NOW sanitize hallucinated imports (strip entirely for inline mode)
    # Each import is now on its own line, so line-based sanitization works correctly
    code, import_fixes = sanitize_hallucinated_imports_line_based(code, strip_instead_of_redirect=True)
    all_fixes.extend(import_fixes)
    
    # Step 3: V45-002 - Fix missing stdlib imports
    # LLMs sometimes use stdlib modules without importing them (e.g., uuid.uuid4())
    # This MUST run after hallucinated imports are stripped, so we don't add
    # imports for modules that were incorrectly used
    code, stdlib_fixes = fix_missing_stdlib_imports(code)
    all_fixes.extend(stdlib_fixes)
    
    return code, all_fixes


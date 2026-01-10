"""
Tree-sitter based syntax validation for generated code.

Bug #88 Fix: Provides actual syntax validation for all 7 supported languages
using tree-sitter parsers. Falls back to regex-based detection when tree-sitter
is not available.

Supported Languages:
- Python (via tree-sitter-python)
- TypeScript (via tree-sitter-typescript)
- JavaScript (via tree-sitter-javascript)
- Go (via tree-sitter-go)
- Java (via tree-sitter-java)
- Ruby (via tree-sitter-ruby)
- C# (via tree-sitter-c-sharp)

Installation:
    pip install "solver-agentic-spec-coworker[validation]"
    
    Or individually:
    pip install tree-sitter tree-sitter-python tree-sitter-typescript ...

Usage:
    from integration_coworker.codegen.syntax_validator import validate_syntax
    
    result = validate_syntax(code, "python")
    if result.is_valid:
        print("Code is syntactically valid!")
    else:
        print(f"Syntax error at line {result.error_line}: {result.error_message}")
"""
import ast
import logging
import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)

# Track whether tree-sitter is available
_TREE_SITTER_AVAILABLE = False
_TREE_SITTER_LANGUAGES: Dict[str, any] = {}

try:
    import tree_sitter
    _TREE_SITTER_AVAILABLE = True
    logger.debug("tree-sitter is available")
except ImportError:
    # WARNING level: Operators should know tree-sitter is unavailable
    # This affects code validation quality in production
    logger.warning(
        "tree-sitter not installed - using fallback validation. "
        "Install with: pip install 'solver-agentic-spec-coworker[validation]'"
    )


def is_tree_sitter_available() -> bool:
    """Check if tree-sitter is available for syntax validation.
    
    Returns:
        True if tree-sitter is installed and can be used for validation,
        False if fallback regex-based validation will be used.
    """
    return _TREE_SITTER_AVAILABLE


def _load_tree_sitter_languages():
    """Lazily load tree-sitter language grammars."""
    global _TREE_SITTER_LANGUAGES
    
    if not _TREE_SITTER_AVAILABLE:
        return
    
    if _TREE_SITTER_LANGUAGES:
        return  # Already loaded
    
    import tree_sitter
    
    # Language import mapping
    # Core supported languages
    language_modules = {
        "python": ("tree_sitter_python", "language"),
        "typescript": ("tree_sitter_typescript", "language_typescript"),
        "tsx": ("tree_sitter_typescript", "language_tsx"),
        "javascript": ("tree_sitter_javascript", "language"),
        "go": ("tree_sitter_go", "language"),
        "java": ("tree_sitter_java", "language"),
        "ruby": ("tree_sitter_ruby", "language"),
        "csharp": ("tree_sitter_c_sharp", "language"),
        "rust": ("tree_sitter_rust", "language"),
        "cpp": ("tree_sitter_cpp", "language"),
        "c": ("tree_sitter_c", "language"),
    }
    
    # Dynamic loading for other languages
    # Try to load tree_sitter_{lang} if not in map
    known_langs = set(language_modules.keys())
    
    # Add any other installed tree-sitter packages dynamically
    # This allows users to pip install tree-sitter-xyz and have it work
    # Bug #102 fix: Use importlib.metadata instead of deprecated pkg_resources
    try:
        import importlib.metadata
        for dist in importlib.metadata.distributions():
            name = dist.metadata.get("Name", "").lower()
            if name.startswith("tree-sitter-") and name != "tree-sitter":
                lang_name = name.replace("tree-sitter-", "").replace("-", "_")
                if lang_name not in known_langs:
                    module_name = f"tree_sitter_{lang_name}"
                    language_modules[lang_name] = (module_name, "language")
                    logger.debug(f"Discovered dynamic tree-sitter language: {lang_name}")
    except Exception as e:
        logger.debug(f"Could not discover dynamic tree-sitter languages: {e}")

    for lang_key, (module_name, func_name) in language_modules.items():
        try:
            module = __import__(module_name)
            lang_func = getattr(module, func_name)
            lang_ptr = lang_func()
            # tree-sitter 0.21+ wraps language pointers automatically
            # tree-sitter 0.22+ / 0.23+ may use different API
            try:
                # Try new API first (0.21+)
                _TREE_SITTER_LANGUAGES[lang_key] = tree_sitter.Language(lang_ptr)
            except TypeError:
                # Fallback: some versions expect the pointer directly
                _TREE_SITTER_LANGUAGES[lang_key] = lang_ptr
            logger.debug(f"Loaded tree-sitter language: {lang_key}")
        except (ImportError, AttributeError) as e:
            logger.debug(f"Could not load tree-sitter-{lang_key}: {e}")


@dataclass
class SyntaxValidationResult:
    """Result of syntax validation."""
    
    is_valid: bool
    """Whether the code is syntactically valid."""
    
    language: str
    """The language that was validated."""
    
    method: str
    """Validation method used: 'tree-sitter', 'ast', or 'regex'."""
    
    error_message: Optional[str] = None
    """Error message if validation failed."""
    
    error_line: Optional[int] = None
    """Line number where error occurred (1-indexed)."""
    
    error_column: Optional[int] = None
    """Column number where error occurred (0-indexed)."""
    
    error_node_type: Optional[str] = None
    """Type of the error node (tree-sitter only)."""
    
    warnings: List[str] = None
    """Non-fatal warnings about the code."""
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def validate_syntax(code: str, language: str) -> SyntaxValidationResult:
    """
    Validate code syntax for the specified language.
    
    Uses tree-sitter for accurate syntax validation when available,
    falls back to AST (Python) or regex (other languages) otherwise.
    
    Args:
        code: Source code to validate
        language: Programming language ('python', 'typescript', 'go', etc.)
    
    Returns:
        SyntaxValidationResult with validation details
    
    Example:
        >>> result = validate_syntax("def foo():\\n    pass", "python")
        >>> result.is_valid
        True
        
        >>> result = validate_syntax("def foo(", "python")
        >>> result.is_valid
        False
        >>> result.error_message
        'unexpected EOF while parsing'
    """
    language = language.lower().strip()
    
    # Normalize language names
    lang_map = {
        "py": "python",
        "ts": "typescript",
        "js": "javascript",
        "c#": "csharp",
        "cs": "csharp",
    }
    language = lang_map.get(language, language)
    
    # Try tree-sitter first
    if _TREE_SITTER_AVAILABLE:
        _load_tree_sitter_languages()
        
        # Map language to tree-sitter key
        ts_lang_map = {
            "typescript": "typescript",
            "javascript": "javascript",
            "python": "python",
            "go": "go",
            "java": "java",
            "ruby": "ruby",
            "csharp": "csharp",
            "cpp": "cpp",
            "c++": "cpp",
            "rust": "rust",
        }
        # Default to language name if not mapped (dynamic support)
        ts_lang = ts_lang_map.get(language, language)
        
        if ts_lang and ts_lang in _TREE_SITTER_LANGUAGES:
            return _validate_with_tree_sitter(code, language, ts_lang)
    
    # Fallback: Python AST for Python code
    if language == "python":
        return _validate_python_ast(code)
    
    # Fallback: Regex-based validation for other languages
    return _validate_with_regex(code, language)


def _validate_with_tree_sitter(
    code: str, 
    language: str, 
    ts_lang: str
) -> SyntaxValidationResult:
    """Validate syntax using tree-sitter parser."""
    import tree_sitter
    
    # tree-sitter 0.21+ uses Parser(language) constructor
    # tree-sitter 0.20 and below uses parser.set_language(language)
    lang = _TREE_SITTER_LANGUAGES[ts_lang]
    try:
        # New API (tree-sitter 0.21+)
        parser = tree_sitter.Parser(lang)
    except TypeError:
        # Old API fallback
        parser = tree_sitter.Parser()
        parser.set_language(lang)
    
    # Parse the code
    tree = parser.parse(bytes(code, "utf-8"))
    
    # Check for error nodes
    errors = _find_error_nodes(tree.root_node)
    
    if not errors:
        return SyntaxValidationResult(
            is_valid=True,
            language=language,
            method="tree-sitter",
        )
    
    # Get the first error
    first_error = errors[0]
    error_line = first_error.start_point[0] + 1  # 1-indexed
    error_column = first_error.start_point[1]
    
    # Try to get context around the error
    error_context = _get_error_context(code, error_line)
    
    return SyntaxValidationResult(
        is_valid=False,
        language=language,
        method="tree-sitter",
        error_message=f"Syntax error: {first_error.type} near '{error_context}'",
        error_line=error_line,
        error_column=error_column,
        error_node_type=first_error.type,
        warnings=[f"Found {len(errors)} syntax error(s)" if len(errors) > 1 else None],
    )


def _find_error_nodes(node) -> List:
    """Recursively find all ERROR and MISSING nodes in the tree."""
    errors = []
    
    if node.type == "ERROR" or node.is_missing:
        errors.append(node)
    
    for child in node.children:
        errors.extend(_find_error_nodes(child))
    
    return errors


def _get_error_context(code: str, error_line: int, context_chars: int = 30) -> str:
    """Get text context around an error line."""
    lines = code.split("\n")
    if 1 <= error_line <= len(lines):
        line = lines[error_line - 1]
        if len(line) > context_chars:
            return line[:context_chars] + "..."
        return line.strip()
    return "<unknown>"


def _validate_python_ast(code: str) -> SyntaxValidationResult:
    """Validate Python code using the ast module."""
    try:
        ast.parse(code)
        return SyntaxValidationResult(
            is_valid=True,
            language="python",
            method="ast",
        )
    except SyntaxError as e:
        return SyntaxValidationResult(
            is_valid=False,
            language="python",
            method="ast",
            error_message=str(e.msg) if e.msg else str(e),
            error_line=e.lineno,
            error_column=e.offset,
        )


def _validate_with_regex(code: str, language: str) -> SyntaxValidationResult:
    """
    Fallback regex-based validation.
    
    This is NOT actual syntax validation - it only checks for common patterns
    that indicate the code is likely well-formed.
    
    Note: This method CANNOT detect actual syntax errors like:
    - Missing semicolons (where required)
    - Unclosed brackets/braces
    - Invalid keywords
    - Type errors
    
    For accurate validation, install tree-sitter:
        pip install "solver-agentic-spec-coworker[validation]"
    """
    warnings = [
        "Using regex-based validation (limited accuracy). "
        "Install tree-sitter for full syntax validation: "
        "pip install 'solver-agentic-spec-coworker[validation]'"
    ]
    
    # Check for basic structure indicators
    checks = _get_regex_checks(language)
    
    # If no checks defined for this language, we can't validate structure
    # Fail open (assume valid) but warn
    if not checks:
        return SyntaxValidationResult(
            is_valid=True,
            language=language,
            method="none",
            warnings=["Unknown language, skipping structure validation"],
        )

    has_structure = False
    for pattern, description in checks:
        if re.search(pattern, code, re.MULTILINE):
            has_structure = True
            break
    
    if not has_structure and code.strip():
        return SyntaxValidationResult(
            is_valid=False,
            language=language,
            method="regex",
            error_message=f"Code does not appear to contain valid {language} structure",
            warnings=warnings,
        )
    
    # Check for obviously broken patterns
    broken_patterns = _get_broken_patterns(language)
    for pattern, error_msg in broken_patterns:
        match = re.search(pattern, code)
        if match:
            # Try to find line number
            prefix = code[:match.start()]
            line_no = prefix.count("\n") + 1
            return SyntaxValidationResult(
                is_valid=False,
                language=language,
                method="regex",
                error_message=error_msg,
                error_line=line_no,
                warnings=warnings,
            )
    
    # Passed basic checks
    return SyntaxValidationResult(
        is_valid=True,
        language=language,
        method="regex",
        warnings=warnings,
    )


def _get_regex_checks(language: str) -> List[Tuple[str, str]]:
    """Get regex patterns that indicate valid code structure."""
    patterns = {
        "python": [
            (r"^(def|class|import|from|async def)\s+\w+", "function/class/import"),
            (r"^\s*(if|for|while|with|try|except)\s+", "control flow"),
        ],
        "typescript": [
            (r"(?:export\s+)?(?:async\s+)?function\s+\w+", "function"),
            (r"(?:export\s+)?(?:class|interface|type)\s+\w+", "class/interface/type"),
            (r"(?:const|let|var)\s+\w+\s*[=:]", "variable declaration"),
            (r"import\s+.*\s+from\s+['\"]", "import statement"),
        ],
        "javascript": [
            (r"(?:async\s+)?function\s+\w+", "function"),
            (r"class\s+\w+", "class"),
            (r"(?:const|let|var)\s+\w+\s*=", "variable declaration"),
            (r"(?:module\.exports|export\s+)", "export"),
        ],
        "go": [
            (r"^package\s+\w+", "package declaration"),
            (r"^func\s+(?:\([^)]+\)\s*)?\w+\s*\(", "function"),
            (r"^type\s+\w+\s+(?:struct|interface)\s*{", "type declaration"),
            (r"^import\s+(?:\(|\")", "import"),
        ],
        "java": [
            (r"(?:public|private|protected)?\s*class\s+\w+", "class"),
            (r"(?:public|private|protected)\s+(?:static\s+)?(?:\w+)\s+\w+\s*\(", "method"),
            (r"^package\s+[\w.]+;", "package"),
            (r"^import\s+[\w.]+;", "import"),
        ],
        "ruby": [
            (r"^class\s+\w+", "class"),
            (r"^module\s+\w+", "module"),
            (r"^def\s+\w+", "method"),
            (r"^require\s+['\"]", "require"),
        ],
        "csharp": [
            (r"(?:public|private|protected|internal)?\s*class\s+\w+", "class"),
            (r"(?:public|private|protected)\s+(?:static\s+)?(?:async\s+)?(?:\w+)\s+\w+\s*\(", "method"),
            (r"^namespace\s+[\w.]+", "namespace"),
            (r"^using\s+[\w.]+;", "using"),
        ],
    }
    return patterns.get(language, [])


def _get_broken_patterns(language: str) -> List[Tuple[str, str]]:
    """Get regex patterns that indicate obviously broken code."""
    common_broken = [
        # Unmatched braces at start of line (common LLM error)
        (r"^\s*\}\s*$(?!\n\s*else)", "Unmatched closing brace"),
    ]
    
    language_broken = {
        "python": [
            # Syntax errors Python AST would catch, but for completeness
            (r"def\s+\w+\s*\(\s*$", "Incomplete function definition"),
            (r"class\s+\w+\s*:\s*$(?!\n)", "Empty class body"),
        ],
        "typescript": [
            (r"function\s+\w+\s*\(\s*$", "Incomplete function definition"),
            (r"=>\s*$", "Incomplete arrow function"),
        ],
        "javascript": [
            (r"function\s+\w+\s*\(\s*$", "Incomplete function definition"),
            (r"=>\s*$", "Incomplete arrow function"),
        ],
        "go": [
            (r"func\s+\w+\s*\(\s*$", "Incomplete function definition"),
            (r"type\s+\w+\s+struct\s*$", "Incomplete struct definition"),
        ],
        "java": [
            (r"class\s+\w+\s*$", "Incomplete class definition"),
            (r"\)\s*throws\s*$", "Incomplete throws clause"),
        ],
        "ruby": [
            (r"def\s+\w+\s*$", "Incomplete method definition"),
            (r"class\s+\w+\s*<\s*$", "Incomplete inheritance"),
        ],
        "csharp": [
            (r"class\s+\w+\s*:\s*$", "Incomplete inheritance"),
            (r"\)\s*=>\s*$", "Incomplete expression body"),
        ],
    }
    
    return common_broken + language_broken.get(language, [])


def is_tree_sitter_available() -> bool:
    """Check if tree-sitter is available for syntax validation."""
    return _TREE_SITTER_AVAILABLE


def get_available_languages() -> List[str]:
    """Get list of languages with tree-sitter support available."""
    if not _TREE_SITTER_AVAILABLE:
        return []
    
    _load_tree_sitter_languages()
    return list(_TREE_SITTER_LANGUAGES.keys())


def validate_code_artifact(
    code: str,
    language: str,
    artifact_type: str = "unknown",
) -> SyntaxValidationResult:
    """
    Validate a code artifact with additional context.
    
    This is a convenience wrapper around validate_syntax that provides
    better error messages for common artifact types (client, flow, test).
    
    Args:
        code: Source code to validate
        language: Programming language
        artifact_type: Type of artifact ('client', 'flow', 'test')
    
    Returns:
        SyntaxValidationResult with validation details
    """
    result = validate_syntax(code, language)
    
    if not result.is_valid and artifact_type != "unknown":
        # Enhance error message with artifact context
        result.error_message = f"[{artifact_type}] {result.error_message}"
    
    return result

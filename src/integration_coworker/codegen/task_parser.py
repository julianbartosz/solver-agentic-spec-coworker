"""
Task Description Parser and Path Extractor (V35-002 Fix)

This module extracts explicit file paths and function signatures from task
descriptions to ensure the coworker generates code at the user-requested
locations instead of using default templates.

Problem (V35-002):
------------------
User requested: "Add a new file src/docformatter/ai_enhancer.py with a function 
enhance_docstring(original: str, function_code: str) -> str"

But the coworker created: "src/integrations/__init__.py" (generic template)

Solution:
---------
1. Parse task descriptions for explicit file paths
2. Extract function/class signatures from task descriptions
3. Override default template paths when explicit paths are provided
4. Generate code structure that matches user's explicit requirements

Design Philosophy:
-----------------
- User's explicit requirements take precedence over templates
- If user says "create file at X", file goes at X
- If user says "add function Y", function Y is generated
- Templates are fallbacks, not defaults when user specifies structure
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import List, Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FunctionSignature:
    """Represents an extracted function signature."""
    name: str
    parameters: List[Tuple[str, Optional[str]]]  # (name, type_hint)
    return_type: Optional[str] = None
    is_async: bool = False
    docstring_hint: Optional[str] = None


@dataclass 
class ClassSignature:
    """Represents an extracted class signature."""
    name: str
    base_classes: List[str] = field(default_factory=list)
    methods: List[FunctionSignature] = field(default_factory=list)


@dataclass
class TaskPathExtractionResult:
    """Result of parsing a task description for paths and signatures."""
    # File paths explicitly mentioned
    explicit_file_paths: List[str] = field(default_factory=list)
    
    # The primary target path (most specific path mentioned)
    primary_target_path: Optional[str] = None
    
    # Functions explicitly requested
    requested_functions: List[FunctionSignature] = field(default_factory=list)
    
    # Classes explicitly requested
    requested_classes: List[ClassSignature] = field(default_factory=list)
    
    # Module names mentioned
    module_names: List[str] = field(default_factory=list)
    
    # Whether the task has explicit structure requirements
    has_explicit_structure: bool = False
    
    # V38-007: Whether async/concurrent code should be generated
    is_async_required: bool = False
    
    # V38-008: Whether streaming response handling is required
    is_streaming_required: bool = False
    
    # V39-006: Whether a REST endpoint/router is explicitly requested
    # This overrides repo type detection when user explicitly wants a router
    is_router_requested: bool = False
    
    # Original task description
    task_description: str = ""
    
    # Confidence that we correctly extracted the intent
    extraction_confidence: float = 0.0
    
    # Raw extraction notes for debugging
    extraction_notes: List[str] = field(default_factory=list)


# =============================================================================
# PATH EXTRACTION PATTERNS
# =============================================================================

# Patterns for extracting file paths from task descriptions
FILE_PATH_PATTERNS = [
    # "Add a new file X" / "Create a new file X"
    r'(?:add|create|put|place|write|generate)\s+(?:a\s+)?(?:new\s+)?file\s+(?:at\s+)?[`"\']?([a-zA-Z0-9_/\-\.]+\.(?:py|ts|js|go|java|rb))[`"\']?',
    
    # "in X.py" / "to X.py"
    r'(?:in|to|at|into)\s+[`"\']?([a-zA-Z0-9_/\-\.]+\.(?:py|ts|js|go|java|rb))[`"\']?',
    
    # Backtick quoted paths: `path/to/file.py`
    r'`([a-zA-Z0-9_/\-\.]+\.(?:py|ts|js|go|java|rb))`',
    
    # "file path/to/file.py" / "module path/to/file.py"
    r'(?:file|module|script)\s+[`"\']?([a-zA-Z0-9_/\-\.]+\.(?:py|ts|js|go|java|rb))[`"\']?',
    
    # "src/X/Y.py" style paths with src prefix
    r'(src/[a-zA-Z0-9_/\-\.]+\.(?:py|ts|js))',
    
    # "lib/X/Y.py" style paths
    r'(lib/[a-zA-Z0-9_/\-\.]+\.(?:py|ts|js))',
]

# Patterns for extracting function signatures
# NOTE: Order matters - more specific patterns should come first
FUNCTION_SIGNATURE_PATTERNS = [
    # "function X(args) -> return_type"
    r'(?:function|method|def)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(([^)]*)\)(?:\s*->\s*([a-zA-Z_][a-zA-Z0-9_\[\],\s]*))?',
    
    # V37-002 Fix: "function name X" / "with function name X" - MUST come before generic patterns
    # This prevents "name" from being extracted as the function name
    r'(?:with\s+)?function\s+name\s+[`"\']?([a-zA-Z_][a-zA-Z0-9_]*)[`"\']?',
    
    # "a function X that" / "function called X"
    r'(?:a\s+)?function\s+(?:called\s+)?[`"\']?([a-zA-Z_][a-zA-Z0-9_]*)[`"\']?',
    
    # "with a method X" (but not "with function name X" which is handled above)
    r'with\s+(?:a\s+)?(?:function|method)\s+[`"\']?([a-zA-Z_][a-zA-Z0-9_]*)[`"\']?',
    
    # Backtick function: `enhance_docstring()`
    r'`([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)`',
]

# Reserved words that should NOT be extracted as function names
FUNCTION_NAME_BLACKLIST = {
    'name', 'called', 'function', 'method', 'def', 'class', 'import', 'from', 
    'return', 'with', 'the', 'a', 'an', 'at', 'in', 'to', 'for', 'of', 'on',
}

# Patterns for extracting class names
CLASS_PATTERNS = [
    # "class X" / "a class called X"
    r'(?:class|type)\s+(?:called\s+)?[`"\']?([A-Z][a-zA-Z0-9_]*)[`"\']?',
    
    # "XClient" / "XService" patterns
    r'[`"\']?([A-Z][a-zA-Z0-9]*(?:Client|Service|Handler|Manager|Controller))[`"\']?',
]

# Patterns for extracting module names
MODULE_PATTERNS = [
    # "module X"
    r'module\s+(?:called\s+)?[`"\']?([a-zA-Z_][a-zA-Z0-9_]*)[`"\']?',
    
    # "in the X module"
    r'in\s+(?:the\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\s+module',
]


# =============================================================================
# V38-007: ASYNC DETECTION PATTERNS
# =============================================================================
# Keywords that indicate async/concurrent execution requirements
# These are detected from task descriptions to generate appropriate async code

ASYNC_INDICATOR_KEYWORDS = frozenset({
    # Direct async keywords
    "async", "await", "asynchronous", "asyncio",
    # Concurrency concepts
    "concurrent", "concurrently", "parallel", "parallelism",
    # Pattern indicators
    "non-blocking", "nonblocking", "event-driven", "event loop",
    # Batch/bulk operations that imply parallelism
    "batch", "bulk", "multiple", "many", "several", "mass",
    # Real-time/streaming patterns
    "streaming", "stream", "real-time", "realtime",
    # Performance-oriented
    "high-performance", "high-throughput", "scalable",
})

# Phrase patterns that strongly indicate async requirements
ASYNC_PHRASE_PATTERNS = [
    r'async\s+(?:function|method|handler|processor|batch)',
    r'(?:process|handle|send|fetch|call)\s+(?:multiple|many|several|batch)\s+\w+\s+(?:concurrently|in parallel|simultaneously|async)',
    r'(?:concurrent|parallel)\s+(?:processing|execution|requests|calls|operations)',
    r'asyncio\.(?:gather|create_task|run)',
    r'await\s+',
    r'async\s+def\s+',
]


def _detect_async_requirement(description: str) -> bool:
    """
    V38-007: Detect if task description implies async/concurrent code generation.
    
    Returns True if the task description contains keywords or patterns that
    indicate async/concurrent execution requirements.
    
    This enables the code generator to produce async/await patterns when
    the user's task semantically requires concurrency.
    """
    if not description:
        return False
    
    description_lower = description.lower()
    words = set(re.findall(r'\b[a-z]+\b', description_lower))
    
    # Check keyword intersection
    if words & ASYNC_INDICATOR_KEYWORDS:
        return True
    
    # Check phrase patterns
    for pattern in ASYNC_PHRASE_PATTERNS:
        if re.search(pattern, description_lower):
            return True
    
    return False


# =============================================================================
# V38-008: STREAMING DETECTION PATTERNS
# =============================================================================
# Keywords that indicate streaming/SSE response handling
# Streaming requires special code patterns (iterating over response chunks)

STREAMING_INDICATOR_KEYWORDS = frozenset({
    # Direct streaming keywords
    "streaming", "stream", "streams",
    # Real-time delivery
    "real-time", "realtime", "live",
    # Token-level processing
    "token", "tokens", "chunk", "chunks",
    # SSE/Event patterns
    "sse", "server-sent", "event-stream",
    # Progressive delivery
    "incremental", "progressive", "iterative",
    # Arrival patterns
    "arrive", "arrives", "arriving",
})

# Phrase patterns that strongly indicate streaming requirements
STREAMING_PHRASE_PATTERNS = [
    r'stream(?:ing|s)?\s+(?:response|tokens?|data|output|chunks?)',
    r'(?:tokens?|chunks?|data)\s+(?:as|while)\s+(?:they|it)\s+arrive',
    r'real[- ]?time\s+(?:generation|output|response|delivery)',
    r'(?:iterate|loop)\s+(?:over|through)\s+(?:response|chunks?|tokens?)',
    r'server[- ]?sent\s+events?',
    r'stream\s*=\s*true',
]


def _detect_streaming_requirement(description: str) -> bool:
    """
    V38-008: Detect if task description implies streaming response handling.
    
    Returns True if the task description contains keywords or patterns that
    indicate streaming/SSE response requirements.
    
    This enables the code generator to produce proper streaming patterns
    (e.g., iterating over response.iter_lines() instead of response.json()).
    """
    if not description:
        return False
    
    description_lower = description.lower()
    words = set(re.findall(r'\b[a-z-]+\b', description_lower))
    
    # Check keyword intersection
    if words & STREAMING_INDICATOR_KEYWORDS:
        return True
    
    # Check phrase patterns
    for pattern in STREAMING_PHRASE_PATTERNS:
        if re.search(pattern, description_lower):
            return True
    
    return False


# =============================================================================
# V39-006: ROUTER/ENDPOINT DETECTION PATTERNS
# =============================================================================
# Keywords that indicate user wants a REST endpoint/router generated
# This overrides repo type detection when explicitly requested

ROUTER_INDICATOR_KEYWORDS = frozenset({
    # Direct router keywords
    "router", "routers", "apirouter",
    # Endpoint keywords  
    "endpoint", "endpoints",
    # REST API patterns
    "api", "rest", "restful",
    # FastAPI specific
    "fastapi",
    # HTTP route patterns
    "route", "routes", "routing",
    # HTTP methods as indicators
    "get", "post", "put", "delete", "patch",
})

# Phrase patterns that strongly indicate router/endpoint requirements
ROUTER_PHRASE_PATTERNS = [
    r'(?:create|add|build|implement)\s+(?:a\s+)?(?:fastapi\s+)?(?:api\s+)?(?:router|endpoint|route)',
    r'expose\s+(?:as\s+)?(?:an?\s+)?(?:api|rest|endpoint|route)',
    r'(?:rest\s+)?api\s+endpoint',
    r'fastapi\s+(?:router|endpoint|route)',
    r'http\s+(?:endpoint|route|api)',
    r'(?:get|post|put|delete|patch)\s+(?:endpoint|route|request)',
    r'apirouter',
    r'include_router',
    r'@(?:app|router)\s*\.\s*(?:get|post|put|delete|patch)',
]


def _detect_router_requirement(description: str) -> bool:
    """
    V39-006: Detect if task description explicitly requests a router/endpoint.
    
    Returns True if the task description contains keywords or patterns that
    indicate the user wants a REST endpoint/FastAPI router generated.
    
    This overrides automatic repo type detection - if user explicitly asks
    for a router, we generate one regardless of repo type.
    """
    if not description:
        return False
    
    description_lower = description.lower()
    words = set(re.findall(r'\b[a-z]+\b', description_lower))
    
    # Check keyword intersection - require at least one strong indicator
    strong_indicators = {"router", "endpoint", "fastapi", "apirouter"}
    if words & strong_indicators:
        return True
    
    # Check phrase patterns for more nuanced detection
    for pattern in ROUTER_PHRASE_PATTERNS:
        if re.search(pattern, description_lower):
            return True
    
    return False


# =============================================================================
# EXTRACTION FUNCTIONS
# =============================================================================

def _extract_file_paths(description: str) -> List[str]:
    """Extract all file paths from a task description."""
    paths = []
    
    for pattern in FILE_PATH_PATTERNS:
        matches = re.findall(pattern, description, re.IGNORECASE)
        paths.extend(matches)
    
    # Deduplicate while preserving order
    seen = set()
    unique_paths = []
    for path in paths:
        normalized = path.strip('`"\' ')
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique_paths.append(normalized)
    
    return unique_paths


def _extract_function_signatures(description: str) -> List[FunctionSignature]:
    """
    Extract function signatures from a task description.
    
    V37-002 Fix: Uses FUNCTION_NAME_BLACKLIST to prevent extracting common
    words like 'name' as function names (e.g., from "function name X").
    """
    functions = []
    seen_names = set()
    
    # First try to find complete signatures with parameters and return types
    complete_pattern = r'([a-zA-Z_][a-zA-Z0-9_]*)\s*\(\s*([^)]*)\s*\)\s*(?:->\s*([a-zA-Z_][a-zA-Z0-9_\[\],\s]*))?'
    
    for match in re.finditer(complete_pattern, description):
        name = match.group(1)
        params_str = match.group(2)
        return_type = match.group(3)
        
        # V37-002: Use blacklist instead of hardcoded list
        if name in seen_names or name.lower() in FUNCTION_NAME_BLACKLIST:
            continue
        
        # Parse parameters
        parameters = []
        if params_str.strip():
            for param in params_str.split(','):
                param = param.strip()
                if ':' in param:
                    param_name, param_type = param.split(':', 1)
                    parameters.append((param_name.strip(), param_type.strip()))
                elif param:
                    parameters.append((param.strip(), None))
        
        functions.append(FunctionSignature(
            name=name,
            parameters=parameters,
            return_type=return_type.strip() if return_type else None,
        ))
        seen_names.add(name)
    
    # Also try simpler patterns (order matters - specific patterns first)
    for pattern in FUNCTION_SIGNATURE_PATTERNS[1:]:  # Skip the first complete pattern
        for match in re.finditer(pattern, description, re.IGNORECASE):
            name = match.group(1)
            # V37-002: Use blacklist instead of hardcoded list
            if name not in seen_names and name.lower() not in FUNCTION_NAME_BLACKLIST:
                functions.append(FunctionSignature(
                    name=name,
                    parameters=[],
                ))
                seen_names.add(name)
    
    return functions


def _extract_class_signatures(description: str) -> List[ClassSignature]:
    """Extract class signatures from a task description."""
    classes = []
    seen_names = set()
    
    for pattern in CLASS_PATTERNS:
        for match in re.finditer(pattern, description, re.IGNORECASE):
            name = match.group(1)
            if name not in seen_names:
                classes.append(ClassSignature(name=name))
                seen_names.add(name)
    
    return classes


def _extract_module_names(description: str) -> List[str]:
    """Extract module names from a task description."""
    modules = []
    seen = set()
    
    for pattern in MODULE_PATTERNS:
        for match in re.finditer(pattern, description, re.IGNORECASE):
            name = match.group(1)
            if name not in seen:
                modules.append(name)
                seen.add(name)
    
    return modules


def _determine_primary_path(paths: List[str], functions: List[FunctionSignature]) -> Optional[str]:
    """
    Determine the primary target path from extracted paths.
    
    Prioritizes:
    1. Paths with more specific directory structure
    2. Paths that match function names
    3. Paths with .py extension (for Python)
    """
    if not paths:
        return None
    
    # Score each path
    scored_paths: List[Tuple[str, int]] = []
    
    for path in paths:
        score = 0
        
        # More path components = more specific
        score += len(PurePath(path).parts) * 10
        
        # Prefer src/ paths
        if path.startswith('src/'):
            score += 20
        
        # Prefer paths that match function names
        path_stem = PurePath(path).stem
        for func in functions:
            if func.name in path_stem or path_stem in func.name:
                score += 30
        
        # Prefer .py files
        if path.endswith('.py'):
            score += 5
        
        scored_paths.append((path, score))
    
    # Return highest scoring path
    scored_paths.sort(key=lambda x: -x[1])
    return scored_paths[0][0]


def _calculate_confidence(result: TaskPathExtractionResult) -> float:
    """Calculate confidence score for the extraction."""
    confidence = 0.0
    
    if result.explicit_file_paths:
        confidence += 0.3
        if result.primary_target_path:
            confidence += 0.2
    
    if result.requested_functions:
        confidence += 0.2
        # Higher confidence if we have parameter info
        if any(f.parameters for f in result.requested_functions):
            confidence += 0.1
    
    if result.requested_classes:
        confidence += 0.1
    
    # Cap at 1.0
    return min(confidence, 1.0)


# =============================================================================
# MAIN API
# =============================================================================

def extract_task_requirements(task_description: str) -> TaskPathExtractionResult:
    """
    Extract explicit paths and signatures from a task description.
    
    This is the main entry point for the task parser.
    
    Args:
        task_description: The user's task description
        
    Returns:
        TaskPathExtractionResult with extracted paths and signatures
    """
    result = TaskPathExtractionResult(task_description=task_description)
    
    if not task_description:
        return result
    
    # Extract paths
    result.explicit_file_paths = _extract_file_paths(task_description)
    
    # Extract functions
    result.requested_functions = _extract_function_signatures(task_description)
    
    # Extract classes
    result.requested_classes = _extract_class_signatures(task_description)
    
    # Extract module names
    result.module_names = _extract_module_names(task_description)
    
    # V38-007: Detect async/concurrent requirements
    result.is_async_required = _detect_async_requirement(task_description)
    
    # V38-008: Detect streaming requirements
    result.is_streaming_required = _detect_streaming_requirement(task_description)
    
    # V39-006: Detect router/endpoint requirements
    result.is_router_requested = _detect_router_requirement(task_description)
    
    # Determine primary path
    result.primary_target_path = _determine_primary_path(
        result.explicit_file_paths,
        result.requested_functions,
    )
    
    # Set has_explicit_structure flag
    result.has_explicit_structure = bool(
        result.explicit_file_paths or
        result.requested_functions or
        result.requested_classes
    )
    
    # V38-007: If async detected, mark functions as async
    if result.is_async_required:
        for func in result.requested_functions:
            func.is_async = True
    
    # Calculate confidence
    result.extraction_confidence = _calculate_confidence(result)
    
    # Add extraction notes
    if result.explicit_file_paths:
        result.extraction_notes.append(
            f"Found {len(result.explicit_file_paths)} explicit file paths"
        )
    if result.requested_functions:
        result.extraction_notes.append(
            f"Found {len(result.requested_functions)} function signatures"
        )
    if result.primary_target_path:
        result.extraction_notes.append(
            f"Primary target path: {result.primary_target_path}"
        )
    # V38-007: Note async detection
    if result.is_async_required:
        result.extraction_notes.append("Async/concurrent code patterns required")
    # V38-008: Note streaming detection
    if result.is_streaming_required:
        result.extraction_notes.append("Streaming response handling required")
    # V39-006: Note router detection
    if result.is_router_requested:
        result.extraction_notes.append("REST endpoint/router explicitly requested")
    
    logger.info(
        f"[V35-002] Task extraction: "
        f"paths={len(result.explicit_file_paths)}, "
        f"functions={len(result.requested_functions)}, "
        f"async={result.is_async_required}, "
        f"streaming={result.is_streaming_required}, "
        f"router={result.is_router_requested}, "
        f"confidence={result.extraction_confidence:.2f}"
    )
    
    return result


def should_override_template_path(result: TaskPathExtractionResult) -> bool:
    """
    Determine if we should override template paths with extracted paths.
    
    Returns True if:
    - User provided explicit file path
    - Extraction confidence is high enough
    """
    return (
        result.has_explicit_structure and
        result.primary_target_path is not None and
        result.extraction_confidence >= 0.3
    )


def get_target_file_path(
    result: TaskPathExtractionResult,
    artifact_type: str,
    default_path: str,
) -> str:
    """
    Get the target file path for an artifact, using extracted path if available.
    
    Args:
        result: Task extraction result
        artifact_type: Type of artifact ("client", "flow", "test")
        default_path: Default path from templates
        
    Returns:
        Final file path to use
    """
    if not should_override_template_path(result):
        return default_path
    
    # For the primary artifact (usually flow or client), use extracted path
    if artifact_type in ("flow", "client") and result.primary_target_path:
        logger.info(
            f"[V35-002] Overriding {artifact_type} path: {default_path} -> {result.primary_target_path}"
        )
        return result.primary_target_path
    
    # For tests, derive from primary path
    if artifact_type == "test" and result.primary_target_path:
        # Convert src/package/module.py -> tests/test_module.py
        primary = PurePath(result.primary_target_path)
        test_path = f"tests/test_{primary.stem}.py"
        return test_path
    
    return default_path


def generate_function_from_signature(sig: FunctionSignature) -> str:
    """
    Generate a Python function stub from a signature.
    
    Args:
        sig: Function signature
        
    Returns:
        Python function code stub
    """
    # Build parameter list
    params = []
    for name, type_hint in sig.parameters:
        if type_hint:
            params.append(f"{name}: {type_hint}")
        else:
            params.append(name)
    
    params_str = ", ".join(params)
    
    # Build return type annotation
    return_annotation = f" -> {sig.return_type}" if sig.return_type else ""
    
    # Build function
    async_prefix = "async " if sig.is_async else ""
    
    return f'''{async_prefix}def {sig.name}({params_str}){return_annotation}:
    """
    {sig.docstring_hint or 'TODO: Add docstring.'}
    """
    # TODO: Implement
    raise NotImplementedError("{sig.name} not yet implemented")
'''

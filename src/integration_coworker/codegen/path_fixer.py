"""
Hybrid AST Validation + Fuzzy Match Auto-Fix for API Paths.

Bug #30 Fix: Prevents LLM hallucination of API paths by:
1. Parsing generated code with AST to find all string literals that look like API paths
2. Fuzzy-matching hallucinated paths to closest valid path from the spec
3. Replacing hallucinated paths with valid ones if confidence is above threshold
4. Falling back to template skeleton if no confident match is found

This approach preserves LLM intelligence while guaranteeing valid paths in output.
"""
import ast
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import List, Set, Tuple, Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class PathReplacement:
    """A single path replacement operation."""
    line_number: int
    column_offset: int
    old_path: str
    new_path: str
    confidence: float
    method: Optional[str] = None  # HTTP method if detectable
    
    def __str__(self) -> str:
        return f"Line {self.line_number}: '{self.old_path}' -> '{self.new_path}' ({self.confidence:.2%})"


@dataclass
class PathFixResult:
    """Result of path fixing operation."""
    fixed_code: str
    replacements: List[PathReplacement] = field(default_factory=list)
    unfixable_paths: List[str] = field(default_factory=list)
    success: bool = True
    
    @property
    def num_fixes(self) -> int:
        return len(self.replacements)
    
    @property
    def num_unfixable(self) -> int:
        return len(self.unfixable_paths)


class PathFixer:
    """
    Fix hallucinated API paths in LLM-generated code using fuzzy matching.
    
    Uses AST parsing to find string literals that look like API paths,
    then fuzzy-matches them against valid paths from the spec.
    
    Attributes:
        valid_paths: Set of valid API paths from the specification
        path_methods: Optional mapping of path -> HTTP method for better matching
        threshold: Minimum confidence score (0.0-1.0) to apply a fix
        strict_mode: If True, raise on unfixable paths instead of returning them
    """
    
    # Patterns that indicate a string is likely an API path
    PATH_PATTERNS = [
        r'^/v\d+/',           # Versioned API: /v1/, /v2/
        r'^/api/',            # API prefix
        r'^/[A-Z][a-z]+',     # Resource path: /Users, /Messages
        r'^/\{[^}]+\}',       # Path parameter start
        r'^/[a-z_]+/',        # Snake case resource
    ]
    
    # Patterns to exclude (not API paths)
    EXCLUDE_PATTERNS = [
        r'^/$',               # Root only
        r'^/\*',              # Glob patterns
        r'^/\.\*',            # Regex patterns
        r'\.py$',             # Python file paths
        r'\.json$',           # JSON file paths
        r'\.yaml$',           # YAML file paths
        r'^/home/',           # Unix home paths
        r'^/Users/',          # macOS user paths
        r'^/tmp/',            # Temp paths
        r'^/etc/',            # Config paths
    ]
    
    def __init__(
        self,
        valid_paths: Set[str],
        path_methods: Optional[Dict[str, str]] = None,
        threshold: float = 0.65,
        strict_mode: bool = False,
    ):
        """
        Initialize PathFixer.
        
        Args:
            valid_paths: Set of valid API paths from the spec
            path_methods: Optional dict mapping path -> HTTP method
            threshold: Minimum confidence for auto-fix (0.0-1.0)
            strict_mode: If True, raise on unfixable paths
        """
        self.valid_paths = valid_paths
        self.path_methods = path_methods or {}
        self.threshold = threshold
        self.strict_mode = strict_mode
        
        # Pre-compile patterns for efficiency
        self._path_patterns = [re.compile(p) for p in self.PATH_PATTERNS]
        self._exclude_patterns = [re.compile(p, re.IGNORECASE) for p in self.EXCLUDE_PATTERNS]
        
        # Build normalized paths for fuzzy matching
        self._normalized_paths = {self._normalize_path(p): p for p in valid_paths}
    
    @classmethod
    def from_endpoints(
        cls,
        endpoints: List[Any],
        threshold: float = 0.65,
        strict_mode: bool = False,
    ) -> "PathFixer":
        """
        Create PathFixer from a list of Endpoint objects.
        
        Args:
            endpoints: List of Endpoint domain objects with path and method
            threshold: Minimum confidence for auto-fix
            strict_mode: If True, raise on unfixable paths
        """
        valid_paths = set()
        path_methods = {}
        
        for ep in endpoints:
            if hasattr(ep, 'path') and ep.path:
                valid_paths.add(ep.path)
                if hasattr(ep, 'method') and ep.method:
                    path_methods[ep.path] = ep.method.upper()
        
        return cls(
            valid_paths=valid_paths,
            path_methods=path_methods,
            threshold=threshold,
            strict_mode=strict_mode,
        )
    
    def fix_code(self, code: str) -> PathFixResult:
        """
        Parse code, find hallucinated paths, and fix them.
        
        Args:
            code: Python source code to fix
            
        Returns:
            PathFixResult with fixed code and replacement details
        """
        if not self.valid_paths:
            # No paths to validate against
            return PathFixResult(fixed_code=code, success=True)
        
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.warning(f"Cannot parse code for path fixing: {e}")
            return PathFixResult(fixed_code=code, success=False)
        
        # Find all path-like strings
        candidates = self._find_path_candidates(tree)
        
        if not candidates:
            return PathFixResult(fixed_code=code, success=True)
        
        # Determine replacements
        replacements: List[PathReplacement] = []
        unfixable: List[str] = []
        
        for node, path_str in candidates:
            # Skip if already valid
            if path_str in self.valid_paths:
                continue
            
            # Normalize and check again
            normalized = self._normalize_path(path_str)
            if normalized in self._normalized_paths:
                # Exact match after normalization
                valid_path = self._normalized_paths[normalized]
                if valid_path != path_str:
                    replacements.append(PathReplacement(
                        line_number=node.lineno,
                        column_offset=node.col_offset,
                        old_path=path_str,
                        new_path=valid_path,
                        confidence=1.0,
                    ))
                continue
            
            # Fuzzy match
            best_match, confidence = self._find_best_match(path_str)
            
            if confidence >= self.threshold:
                replacements.append(PathReplacement(
                    line_number=node.lineno,
                    column_offset=node.col_offset,
                    old_path=path_str,
                    new_path=best_match,
                    confidence=confidence,
                ))
            else:
                unfixable.append(path_str)
                if self.strict_mode:
                    raise ValueError(
                        f"Cannot fix hallucinated path '{path_str}' "
                        f"(best match: '{best_match}' at {confidence:.2%})"
                    )
        
        if not replacements:
            return PathFixResult(
                fixed_code=code,
                unfixable_paths=unfixable,
                success=len(unfixable) == 0,
            )
        
        # Apply replacements
        fixed_code = self._apply_replacements(code, replacements)
        
        # Log what was fixed
        for repl in replacements:
            logger.info(f"Auto-fixed path: {repl}")
        
        return PathFixResult(
            fixed_code=fixed_code,
            replacements=replacements,
            unfixable_paths=unfixable,
            success=len(unfixable) == 0,
        )
    
    def _find_path_candidates(self, tree: ast.AST) -> List[Tuple[ast.Constant, str]]:
        """
        Find all string literals that look like API paths.
        
        Returns list of (node, path_string) tuples.
        """
        candidates = []
        
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if not isinstance(node.value, str):
                continue
            
            value = node.value
            
            # Must start with /
            if not value.startswith('/'):
                continue
            
            # Skip excluded patterns
            if any(p.search(value) for p in self._exclude_patterns):
                continue
            
            # Check if it looks like an API path
            if self._looks_like_api_path(value):
                candidates.append((node, value))
        
        return candidates
    
    def _looks_like_api_path(self, value: str) -> bool:
        """Check if string looks like an API path."""
        # Must have at least one segment after /
        segments = [s for s in value.split('/') if s]
        if not segments:
            return False
        
        # Check for API path patterns
        if any(p.search(value) for p in self._path_patterns):
            return True
        
        # Check for path parameters
        if '{' in value and '}' in value:
            return True
        
        # Check for resource-like segments (capitalized or snake_case)
        for seg in segments:
            if seg.startswith('{'):
                continue
            # Capitalized like /Messages or snake_case like /message_status
            if seg[0].isupper() or '_' in seg:
                return True
        
        return False
    
    def _normalize_path(self, path: str) -> str:
        """
        Normalize a path for comparison.
        
        - Removes trailing slashes
        - Lowercases for comparison
        - Replaces numeric IDs with {id}
        """
        # Remove trailing slash
        path = path.rstrip('/')
        
        # Replace numeric segments with {id}
        segments = []
        for seg in path.split('/'):
            if seg and seg.isdigit():
                segments.append('{id}')
            else:
                segments.append(seg)
        
        return '/'.join(segments).lower()
    
    def _find_best_match(self, path: str) -> Tuple[str, float]:
        """
        Find the best matching valid path using fuzzy matching.
        
        Uses a combination of:
        - SequenceMatcher for overall similarity
        - Segment matching for structural similarity
        - Extra weight for matching prefixes
        
        Returns:
            Tuple of (best_path, confidence_score)
        """
        if not self.valid_paths:
            return ("", 0.0)
        
        best_path = ""
        best_score = 0.0
        
        path_lower = path.lower()
        path_segments = [s for s in path.split('/') if s]
        
        for valid_path in self.valid_paths:
            valid_lower = valid_path.lower()
            valid_segments = [s for s in valid_path.split('/') if s]
            
            # 1. Overall string similarity
            string_score = SequenceMatcher(None, path_lower, valid_lower).ratio()
            
            # 2. Segment overlap
            segment_matches = 0
            for seg in path_segments:
                seg_lower = seg.lower()
                # Check if segment appears in valid path (exact or partial)
                for valid_seg in valid_segments:
                    valid_seg_lower = valid_seg.lower()
                    if valid_seg_lower.startswith('{'):
                        # Path parameter - any non-empty segment matches
                        if seg and not seg.startswith('{'):
                            segment_matches += 0.5
                            break
                    elif seg_lower == valid_seg_lower:
                        segment_matches += 1.0
                        break
                    elif seg_lower in valid_seg_lower or valid_seg_lower in seg_lower:
                        segment_matches += 0.5
                        break
            
            segment_score = segment_matches / max(len(path_segments), len(valid_segments))
            
            # 3. Prefix match bonus
            prefix_score = 0.0
            common_prefix_len = 0
            for i, (c1, c2) in enumerate(zip(path_lower, valid_lower)):
                if c1 == c2:
                    common_prefix_len = i + 1
                else:
                    break
            prefix_score = common_prefix_len / max(len(path), len(valid_path))
            
            # 4. Resource name match bonus (first capitalized segment)
            resource_score = 0.0
            path_resource = self._extract_resource_name(path)
            valid_resource = self._extract_resource_name(valid_path)
            if path_resource and valid_resource:
                resource_score = SequenceMatcher(
                    None, 
                    path_resource.lower(), 
                    valid_resource.lower()
                ).ratio()
            
            # Combined score with weights
            # String similarity: 30%
            # Segment matching: 35%
            # Prefix match: 15%
            # Resource name: 20%
            combined_score = (
                0.30 * string_score +
                0.35 * segment_score +
                0.15 * prefix_score +
                0.20 * resource_score
            )
            
            if combined_score > best_score:
                best_score = combined_score
                best_path = valid_path
        
        return (best_path, best_score)
    
    def _extract_resource_name(self, path: str) -> Optional[str]:
        """Extract the primary resource name from a path."""
        segments = [s for s in path.split('/') if s and not s.startswith('{')]
        
        # Skip version segments
        segments = [s for s in segments if not re.match(r'^v\d+$', s, re.IGNORECASE)]
        
        # Return first remaining segment (the resource)
        return segments[0] if segments else None
    
    def _apply_replacements(
        self,
        code: str,
        replacements: List[PathReplacement],
    ) -> str:
        """
        Apply replacements to code.
        
        Processes replacements from bottom to top to preserve line numbers.
        """
        lines = code.split('\n')
        
        # Sort by line number descending (process from bottom)
        sorted_replacements = sorted(
            replacements,
            key=lambda r: (r.line_number, r.column_offset),
            reverse=True,
        )
        
        for repl in sorted_replacements:
            line_idx = repl.line_number - 1  # 0-indexed
            if 0 <= line_idx < len(lines):
                line = lines[line_idx]
                # Replace the old path with new path
                # Handle both single and double quotes
                for quote in ['"', "'"]:
                    old_quoted = f'{quote}{repl.old_path}{quote}'
                    new_quoted = f'{quote}{repl.new_path}{quote}'
                    if old_quoted in line:
                        line = line.replace(old_quoted, new_quoted, 1)
                        break
                lines[line_idx] = line
        
        return '\n'.join(lines)


def fix_hallucinated_paths(
    code: str,
    endpoints: List[Any],
    threshold: float = 0.65,
) -> PathFixResult:
    """
    Convenience function to fix hallucinated paths in code.
    
    Args:
        code: Python source code
        endpoints: List of Endpoint objects from the spec
        threshold: Minimum confidence for auto-fix
        
    Returns:
        PathFixResult with fixed code and details
    """
    fixer = PathFixer.from_endpoints(endpoints, threshold=threshold)
    return fixer.fix_code(code)

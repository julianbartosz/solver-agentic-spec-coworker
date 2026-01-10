"""
Sandbox Failure Attribution Logic (PR #9)

Pure functions for classifying sandbox failures:
- Environment/toolchain issues (missing deps, wrong Python version)
- Generated code issues (syntax errors, type errors, logic bugs)
- Test framework issues (import errors, fixture problems)
- Timeout/resource exhaustion

No runtime imports, no LLM calls - just deterministic pattern matching.

Per ADR-QUALITY-SIGNALS: Attribution helps target regeneration and
provides actionable feedback for HITL review.

Production Safety:
- Deterministic outputs: stable ordering for same inputs (CI reproducibility)
- False-positive resistance: multi-signal detection to avoid misclassification
- Hard bounds everywhere: evidence, artifacts, and summaries are bounded
- Signals-only: writes quality_refs but does NOT trigger interrupts
- Secret redaction: API keys, tokens, passwords never appear in summaries
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple
import json
import re
import hashlib

# Import ALL bounds from single source of truth (production_guardrails)
from integration_coworker.graph.production_guardrails import (
    # Evidence bounds
    MAX_EVIDENCE_BYTES,
    MAX_EVIDENCE_LINES,
    MAX_STACK_FRAMES,
    # Attribution bounds
    MAX_ATTRIBUTIONS,
    MAX_FIX_HINTS,
    MAX_RAW_SIGNALS,
    MAX_HINT_LENGTH,
    MAX_FILE_PATH_LENGTH,
    # Summary bounds
    MAX_SUMMARY_CATEGORIES,
    MAX_SUMMARY_HINTS,
    MAX_SUMMARY_FINGERPRINTS,
    MAX_ARTIFACT_SUMMARY_BYTES,
    # Utilities
    normalize_evidence as _normalize_evidence_impl,
    compute_fingerprint,
    redact_secrets,
    bounded_list,
    safe_json_dumps,
)


# =============================================================================
# Attribution Categories
# =============================================================================

class FailureCategory(str, Enum):
    """
    Categories for sandbox failure attribution.
    
    Categories are mutually exclusive and ordered by priority:
    higher categories shadow lower ones when multiple signals present.
    """
    # Environment issues (not code's fault)
    ENV_MISSING_DEPENDENCY = "env_missing_dependency"
    ENV_PYTHON_VERSION = "env_python_version"
    ENV_TOOL_MISSING = "env_tool_missing"
    ENV_TIMEOUT = "env_timeout"
    ENV_RESOURCE_EXHAUSTED = "env_resource_exhausted"
    
    # Test framework issues (may be env or test code)
    TEST_IMPORT_ERROR = "test_import_error"
    TEST_FIXTURE_ERROR = "test_fixture_error"
    TEST_COLLECTION_ERROR = "test_collection_error"
    
    # Generated code issues (regeneration targets)
    CODE_SYNTAX_ERROR = "code_syntax_error"
    CODE_TYPE_ERROR = "code_type_error"
    CODE_RUNTIME_ERROR = "code_runtime_error"
    CODE_ASSERTION_FAILURE = "code_assertion_failure"
    CODE_SECURITY_VIOLATION = "code_security_violation"
    
    # Unknown (fallback)
    UNKNOWN = "unknown"


@dataclass
class AttributionResult:
    """
    Result of attributing a single failure.
    
    Attributes:
        category: The failure category
        confidence: 0.0-1.0 confidence in attribution
        likely_cause_file: Optional file path most likely to blame
        likely_cause_lines: Optional (start, end) line range
        fix_hints: Suggestions for resolution
        raw_signals: Debug info about detection (pattern matches)
        evidence_fingerprint: Hash of normalized evidence for deduplication
        gate_name: Name of the gate that produced this failure
    """
    category: FailureCategory
    confidence: float
    likely_cause_file: Optional[str] = None
    likely_cause_lines: Optional[Tuple[int, int]] = None
    fix_hints: List[str] = field(default_factory=list)
    raw_signals: List[str] = field(default_factory=list)
    evidence_fingerprint: str = ""
    gate_name: str = ""
    
    def sort_key(self) -> tuple:
        """
        Return stable sort key for deterministic ordering.
        
        Order by: (category, file, line, confidence_desc, fingerprint)
        """
        return (
            self.category.value,
            self.likely_cause_file or "",
            self.likely_cause_lines[0] if self.likely_cause_lines else 0,
            -self.confidence,  # Higher confidence first
            self.evidence_fingerprint,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to JSON-serializable dict with hard bounds.
        
        Keys are explicitly sorted for deterministic serialization.
        Lists are bounded but NOT sorted (order may be meaningful).
        """
        # Build dict with explicit key order for determinism
        d = {
            "category": self.category.value,
            "confidence": round(self.confidence, 3),
            "evidence_fingerprint": self.evidence_fingerprint,
            "fix_hints": [h[:MAX_HINT_LENGTH] for h in self.fix_hints[:MAX_FIX_HINTS]],
            "gate_name": self.gate_name,
            "likely_cause_file": (self.likely_cause_file or "")[:MAX_FILE_PATH_LENGTH] or None,
            "likely_cause_lines": list(self.likely_cause_lines) if self.likely_cause_lines else None,
            "raw_signals": sorted(self.raw_signals[:MAX_RAW_SIGNALS]),  # Sort signals for determinism
        }
        return dict(sorted(d.items()))  # Alphabetical key order
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AttributionResult":
        """Create from dict."""
        lines = data.get("likely_cause_lines")
        return cls(
            category=FailureCategory(data["category"]),
            confidence=data.get("confidence", 0.0),
            likely_cause_file=data.get("likely_cause_file"),
            likely_cause_lines=tuple(lines) if lines else None,
            fix_hints=data.get("fix_hints", []),
            raw_signals=data.get("raw_signals", []),
            evidence_fingerprint=data.get("evidence_fingerprint", ""),
            gate_name=data.get("gate_name", ""),
        )


# =============================================================================
# Evidence Normalization (False-Positive Resistance + Secret Redaction)
# =============================================================================

def normalize_evidence(output: str) -> str:
    """
    Normalize output for stable fingerprinting.
    
    Delegates to production_guardrails.normalize_evidence which:
    - Bounds output size (bytes and lines)
    - Redacts secrets (API keys, tokens, passwords)
    - Removes volatile tokens (timestamps, PIDs, addresses)
    - Strips ANSI escape codes
    - Normalizes paths
    
    Same input always produces same output (deterministic).
    """
    return _normalize_evidence_impl(
        output,
        max_bytes=MAX_EVIDENCE_BYTES,
        max_lines=MAX_EVIDENCE_LINES,
        redact=True,  # Always redact secrets
    )


def compute_evidence_fingerprint(output: str, gate_name: str) -> str:
    """
    Compute a stable fingerprint for evidence deduplication.
    
    Uses normalized output + gate name for stable hashing.
    Fingerprint is deterministic: same input always produces same output.
    Secrets are redacted before fingerprinting.
    """
    normalized = normalize_evidence(output)
    return compute_fingerprint(normalized, context=gate_name)


def extract_stack_frames(output: str) -> List[str]:
    """
    Extract top N stack frames from traceback.
    
    Returns list of "file:line:func" strings for fingerprinting.
    """
    frames = []
    # Match Python traceback lines
    pattern = re.compile(r'File "([^"]+)", line (\d+), in (\w+)')
    for match in pattern.finditer(output):
        if len(frames) >= MAX_STACK_FRAMES:
            break
        file_path = match.group(1)
        line = match.group(2)
        func = match.group(3)
        # Normalize path
        if 'site-packages' in file_path:
            continue  # Skip library frames
        frames.append(f"{file_path}:{line}:{func}")
    return frames


# =============================================================================
# Pattern Registry (Multi-Signal Scoring with Prefilters)
# =============================================================================
# 
# False-positive resistance: Multi-signal detection requires 2+ signals
# for high-confidence environment attributions (not code's fault).
#
# Patterns are compiled once at module load for:
# - Performance (no runtime re.compile)
# - Auditability (clear registry)
# - Testability (patterns can be validated independently)
#
# Each pattern has:
# - regex: Compiled pattern (re.IGNORECASE always)
# - prefilter_tokens: Set of substrings that MUST appear before regex is run
#   (cheap check avoids regex evaluation when clearly irrelevant)
# - category: Target FailureCategory
# - hint_template: Fix suggestion (use {1}, {2} for groups)
# - base_confidence: Starting confidence (0.0-1.0)
# - priority: Higher priority patterns shadow lower ones (100+=infra, 10=env, 5=test, 1=code)
# - signal_weight: How much this pattern contributes to multi-signal scoring
#

@dataclass
class PatternSpec:
    """Specification for a detection pattern with prefilter."""
    name: str  # Human-readable name for debugging/auditing
    regex: re.Pattern
    category: FailureCategory
    hint_template: str
    base_confidence: float
    priority: int  # 100+=infra, 10=env, 5=test, 1=code
    signal_weight: float = 1.0  # How much this signal contributes
    requires_second_signal: bool = False  # If True, needs corroboration
    prefilter_tokens: Set[str] = field(default_factory=set)  # Must match at least one before regex
    
    def matches_prefilter(self, text: str) -> bool:
        """Check if text passes prefilter (cheap substring check)."""
        if not self.prefilter_tokens:
            return True  # No prefilter = always pass
        text_lower = text.lower()
        return any(token.lower() in text_lower for token in self.prefilter_tokens)


@dataclass
class NegativePattern:
    """
    Negative pattern to suppress false positives.
    
    If matched, reduces confidence or blocks certain category attributions.
    Use when a pattern matches but context shows it's NOT the assumed category.
    """
    name: str
    regex: re.Pattern
    suppresses_categories: List[FailureCategory]  # Don't attribute to these
    confidence_penalty: float = 0.5  # Reduce confidence by this factor
    prefilter_tokens: Set[str] = field(default_factory=set)  # Must match at least one before regex
    
    def matches_prefilter(self, text: str) -> bool:
        """Check if text passes prefilter (cheap substring check)."""
        if not self.prefilter_tokens:
            return True
        text_lower = text.lower()
        return any(token.lower() in text_lower for token in self.prefilter_tokens)


# =============================================================================
# Compiled Pattern Registry
# =============================================================================

# Environment patterns (priority=10, override everything else)
_ENV_PATTERNS: List[PatternSpec] = [
    # Missing dependency - STRONG signal
    PatternSpec(
        name="module_not_found",
        regex=re.compile(r"ModuleNotFoundError: No module named ['\"](\w+)['\"]", re.I),
        category=FailureCategory.ENV_MISSING_DEPENDENCY,
        hint_template="Install missing module: {1}",
        base_confidence=0.95,
        priority=10,
        signal_weight=1.0,
        prefilter_tokens={"ModuleNotFoundError", "No module named"},
    ),
    PatternSpec(
        name="import_error_from",
        regex=re.compile(r"ImportError:.*cannot import name.*from ['\"](\w+)['\"]", re.I),
        category=FailureCategory.ENV_MISSING_DEPENDENCY,
        hint_template="Check if module {1} is installed correctly",
        base_confidence=0.85,
        priority=10,
        signal_weight=0.8,
        requires_second_signal=True,  # ImportError can be test assertion
        prefilter_tokens={"ImportError", "cannot import"},
    ),
    PatternSpec(
        name="pip_install_suggestion",
        regex=re.compile(r"pip install\s+(\S+)", re.I),
        category=FailureCategory.ENV_MISSING_DEPENDENCY,
        hint_template="Run: pip install {1}",
        base_confidence=0.7,
        priority=10,
        signal_weight=0.5,
        requires_second_signal=True,
        prefilter_tokens={"pip install"},
    ),
    
    # Python version issues
    PatternSpec(
        name="match_stmt_syntax",
        regex=re.compile(r"SyntaxError:.*match\s+", re.I),
        category=FailureCategory.ENV_PYTHON_VERSION,
        hint_template="match statement requires Python 3.10+",
        base_confidence=0.9,
        priority=10,
        prefilter_tokens={"SyntaxError", "match"},
        signal_weight=1.0,
    ),
    PatternSpec(
        name="python_requires_version",
        regex=re.compile(r"python_requires.*>=\s*3\.(\d+)", re.I),
        category=FailureCategory.ENV_PYTHON_VERSION,
        hint_template="Requires Python 3.{1}+",
        base_confidence=0.85,
        priority=10,
        signal_weight=0.8,
    ),
    
    # Tool missing
    PatternSpec(
        name="tool_not_found",
        regex=re.compile(r"(ruff|mypy|pytest|bandit).*not found", re.I),
        category=FailureCategory.ENV_TOOL_MISSING,
        hint_template="Install tool: pip install {1}",
        base_confidence=0.95,
        priority=10,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="command_not_found_tool",
        regex=re.compile(r"command not found.*\b(ruff|mypy|pytest|bandit)\b", re.I),
        category=FailureCategory.ENV_TOOL_MISSING,
        hint_template="Install tool: pip install {1}",
        base_confidence=0.95,
        priority=10,
        signal_weight=1.0,
    ),
    
    # Timeout - INFRASTRUCTURE FAILURE (highest priority)
    PatternSpec(
        name="timeout",
        regex=re.compile(r"timed? out|timeout|exceeded.*limit", re.I),
        category=FailureCategory.ENV_TIMEOUT,
        hint_template="Increase timeout or simplify tests",
        base_confidence=0.9,
        priority=10,
        signal_weight=1.0,
    ),
    
    # Resource exhaustion
    PatternSpec(
        name="resource_exhausted",
        regex=re.compile(r"memory|oom|killed|cannot allocate", re.I),
        category=FailureCategory.ENV_RESOURCE_EXHAUSTED,
        hint_template="Reduce test scope or increase memory limit",
        base_confidence=0.85,
        priority=10,
        signal_weight=0.9,
        requires_second_signal=True,  # "memory" alone is weak
    ),
    
    # Container/sandbox infrastructure failures
    PatternSpec(
        name="container_failure",
        regex=re.compile(r"container.*failed|docker.*error|sandbox.*error", re.I),
        category=FailureCategory.ENV_RESOURCE_EXHAUSTED,
        hint_template="Sandbox infrastructure failure - retry or check container",
        base_confidence=0.95,
        priority=10,
        signal_weight=1.0,
    ),
]

# Test framework patterns (priority=5)
_TEST_PATTERNS: List[PatternSpec] = [
    # Import errors in test files
    PatternSpec(
        name="test_import_error",
        regex=re.compile(r"tests?[/\\].*ImportError|ImportError.*tests?[/\\]", re.I),
        category=FailureCategory.TEST_IMPORT_ERROR,
        hint_template="Check test imports and PYTHONPATH",
        base_confidence=0.85,
        priority=5,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="pytest_import_error",
        regex=re.compile(r"E\s+ImportError:", re.I),
        category=FailureCategory.TEST_IMPORT_ERROR,
        hint_template="Fix import statement in test file",
        base_confidence=0.8,
        priority=5,
        signal_weight=0.8,
        requires_second_signal=True,
    ),
    
    # Fixture errors
    PatternSpec(
        name="fixture_not_found",
        regex=re.compile(r"fixture ['\"](\w+)['\"].*not found", re.I),
        category=FailureCategory.TEST_FIXTURE_ERROR,
        hint_template="Define or import fixture: {1}",
        base_confidence=0.9,
        priority=5,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="fixture_definition",
        regex=re.compile(r"@pytest\.fixture|conftest\.py", re.I),
        category=FailureCategory.TEST_FIXTURE_ERROR,
        hint_template="Check conftest.py fixture definitions",
        base_confidence=0.7,
        priority=5,
        signal_weight=0.5,
        requires_second_signal=True,
    ),
    
    # Collection errors
    PatternSpec(
        name="no_tests_collected",
        regex=re.compile(r"collected 0 items|no tests ran|exit code 5", re.I),
        category=FailureCategory.TEST_COLLECTION_ERROR,
        hint_template="No tests found - check test discovery patterns",
        base_confidence=0.85,
        priority=5,
        signal_weight=1.0,
    ),
]

# Code patterns (priority=1, lowest - shadowed by env/test)
_CODE_PATTERNS: List[PatternSpec] = [
    # Syntax errors
    PatternSpec(
        name="syntax_error",
        regex=re.compile(r"SyntaxError:|invalid syntax", re.I),
        category=FailureCategory.CODE_SYNTAX_ERROR,
        hint_template="Fix Python syntax error",
        base_confidence=0.95,
        priority=1,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="indentation_error",
        regex=re.compile(r"IndentationError:", re.I),
        category=FailureCategory.CODE_SYNTAX_ERROR,
        hint_template="Fix indentation",
        base_confidence=0.95,
        priority=1,
        signal_weight=1.0,
    ),
    
    # Type errors
    PatternSpec(
        name="type_error_argument",
        regex=re.compile(r"TypeError:.*argument|expected.*got", re.I),
        category=FailureCategory.CODE_TYPE_ERROR,
        hint_template="Fix type mismatch in function call",
        base_confidence=0.85,
        priority=1,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="mypy_incompatible",
        regex=re.compile(r"mypy.*error:.*incompatible type", re.I),
        category=FailureCategory.CODE_TYPE_ERROR,
        hint_template="Fix type annotation",
        base_confidence=0.9,
        priority=1,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="incompatible_type",
        regex=re.compile(r"error:.*incompatible type", re.I),
        category=FailureCategory.CODE_TYPE_ERROR,
        hint_template="Fix type annotation",
        base_confidence=0.85,
        priority=1,
        signal_weight=0.8,
    ),
    
    # Runtime errors
    PatternSpec(
        name="attribute_error",
        regex=re.compile(r"AttributeError:.*has no attribute ['\"](\w+)['\"]", re.I),
        category=FailureCategory.CODE_RUNTIME_ERROR,
        hint_template="Add missing attribute: {1}",
        base_confidence=0.85,
        priority=1,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="key_error",
        regex=re.compile(r"KeyError:.*['\"](\w+)['\"]", re.I),
        category=FailureCategory.CODE_RUNTIME_ERROR,
        hint_template="Add missing key: {1}",
        base_confidence=0.85,
        priority=1,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="name_error",
        regex=re.compile(r"NameError:.*['\"](\w+)['\"].*not defined", re.I),
        category=FailureCategory.CODE_RUNTIME_ERROR,
        hint_template="Define or import: {1}",
        base_confidence=0.9,
        priority=1,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="runtime_errors",
        regex=re.compile(r"ValueError:|IndexError:|ZeroDivisionError:", re.I),
        category=FailureCategory.CODE_RUNTIME_ERROR,
        hint_template="Fix runtime error",
        base_confidence=0.8,
        priority=1,
        signal_weight=0.8,
    ),
    
    # Assertion failures
    PatternSpec(
        name="assertion_error",
        regex=re.compile(r"AssertionError|assert.*failed", re.I),
        category=FailureCategory.CODE_ASSERTION_FAILURE,
        hint_template="Fix assertion or implementation",
        base_confidence=0.7,
        priority=1,
        signal_weight=0.7,
    ),
    
    # Security violations
    PatternSpec(
        name="bandit_high_severity",
        regex=re.compile(r"bandit.*B\d{3}.*high|severity.*high", re.I),
        category=FailureCategory.CODE_SECURITY_VIOLATION,
        hint_template="Fix security issue flagged by bandit",
        base_confidence=0.95,
        priority=1,
        signal_weight=1.0,
    ),
    PatternSpec(
        name="dangerous_functions",
        regex=re.compile(r"eval\(|exec\(|__import__", re.I),
        category=FailureCategory.CODE_SECURITY_VIOLATION,
        hint_template="Remove dangerous function call",
        base_confidence=0.9,
        priority=1,
        signal_weight=1.0,
    ),
]

# =============================================================================
# Negative Patterns (False-Positive Blockers)
# =============================================================================
# 
# These patterns detect when a match is likely a FALSE POSITIVE.
# Example: "ImportError" in a test assertion message is NOT a real import error.
#

_NEGATIVE_PATTERNS: List[NegativePattern] = [
    # Test assertions mentioning error names shouldn't be attributed to those errors
    NegativePattern(
        name="assertion_mentions_error",
        regex=re.compile(r"(assert|expect|should|must).*(ImportError|ModuleNotFoundError|TypeError)", re.I),
        suppresses_categories=[
            FailureCategory.ENV_MISSING_DEPENDENCY,
            FailureCategory.TEST_IMPORT_ERROR,
        ],
        confidence_penalty=0.3,
    ),
    # String literals mentioning errors
    NegativePattern(
        name="string_literal_error",
        regex=re.compile(r"['\"].*?(ImportError|ModuleNotFoundError|TypeError).*?['\"]", re.I),
        suppresses_categories=[
            FailureCategory.ENV_MISSING_DEPENDENCY,
            FailureCategory.TEST_IMPORT_ERROR,
        ],
        confidence_penalty=0.4,
    ),
    # pytest.raises context - error is expected, not real
    NegativePattern(
        name="pytest_raises_context",
        regex=re.compile(r"pytest\.raises\s*\(.*?(Error|Exception)", re.I),
        suppresses_categories=[
            FailureCategory.ENV_MISSING_DEPENDENCY,
            FailureCategory.TEST_IMPORT_ERROR,
            FailureCategory.CODE_RUNTIME_ERROR,
        ],
        confidence_penalty=0.5,
    ),
    # Mock/patch related - errors might be mocked
    NegativePattern(
        name="mock_error_context",
        regex=re.compile(r"(mock|patch|MagicMock).*(Error|Exception)", re.I),
        suppresses_categories=[
            FailureCategory.ENV_MISSING_DEPENDENCY,
        ],
        confidence_penalty=0.3,
    ),
    # "memory" in variable names or comments, not actual OOM
    NegativePattern(
        name="memory_in_identifier",
        regex=re.compile(r"(def|class|#|memory_|_memory|in_memory)", re.I),
        suppresses_categories=[
            FailureCategory.ENV_RESOURCE_EXHAUSTED,
        ],
        confidence_penalty=0.5,
    ),
    # "timeout" in test file/function names (e.g., test_timeout_handling.py)
    # This shouldn't trigger actual timeout detection
    NegativePattern(
        name="timeout_in_test_name",
        regex=re.compile(r"(test_|_test|::test).*timeout", re.I),
        suppresses_categories=[
            FailureCategory.ENV_TIMEOUT,
        ],
        confidence_penalty=0.3,
    ),
    # "TypeError" or "ImportError" mentioned in another error message
    # e.g., "ValueError: Expected TypeError but got ImportError"
    NegativePattern(
        name="error_mentioned_in_message",
        regex=re.compile(r"(ValueError|Exception|Error):.*?(TypeError|ImportError|ModuleNotFoundError)", re.I),
        suppresses_categories=[
            FailureCategory.CODE_TYPE_ERROR,
            FailureCategory.ENV_MISSING_DEPENDENCY,
            FailureCategory.TEST_IMPORT_ERROR,
        ],
        confidence_penalty=0.3,
    ),
    # "expected" in error message context (comparison, not actual type error)
    NegativePattern(
        name="expected_in_comparison",
        regex=re.compile(r"(expected|but got|should be).*?(TypeError|ImportError|int|str)", re.I),
        suppresses_categories=[
            FailureCategory.CODE_TYPE_ERROR,
        ],
        confidence_penalty=0.4,
    ),
]

# File path extraction patterns
_FILE_PATTERNS: List[re.Pattern] = [
    re.compile(r"File ['\"]([^'\"]+\.py)['\"].*line (\d+)", re.I),
    re.compile(r"([^\s]+\.py):(\d+):", re.I),
    re.compile(r"in\s+([^\s]+\.py).*line\s+(\d+)", re.I),
]

# =============================================================================
# Infrastructure Signal Patterns (Override everything)
# =============================================================================
# 
# Infrastructure failures (timeout, container crash) should ALWAYS override
# downstream symptoms. If sandbox timed out, any ModuleNotFoundError is
# meaningless because the environment wasn't set up.
#

_INFRASTRUCTURE_PATTERNS: List[PatternSpec] = [
    PatternSpec(
        name="hard_timeout",
        regex=re.compile(r"(hard )?timeout|killed|SIGKILL|exceeded.*time.*limit", re.I),
        category=FailureCategory.ENV_TIMEOUT,
        hint_template="Sandbox timed out - downstream errors are symptoms",
        base_confidence=0.99,
        priority=100,  # Highest priority - overrides everything
        signal_weight=2.0,  # Double weight
    ),
    PatternSpec(
        name="container_crashed",
        regex=re.compile(r"container (exited|crashed|died)|sandbox.*fail|docker daemon", re.I),
        category=FailureCategory.ENV_RESOURCE_EXHAUSTED,
        hint_template="Container infrastructure failure - all errors are symptoms",
        base_confidence=0.99,
        priority=100,
        signal_weight=2.0,
    ),
]


# =============================================================================
# Multi-Signal Scoring Engine
# =============================================================================

@dataclass
class SignalMatch:
    """A single signal match from pattern detection."""
    pattern_name: str
    category: FailureCategory
    hint: str
    confidence: float
    priority: int
    weight: float
    requires_corroboration: bool
    match: re.Match


def _collect_signals(output: str) -> Tuple[List[SignalMatch], List[str]]:
    """
    Collect all matching signals from output.
    
    Uses prefilters for performance: cheap substring checks before regex.
    This avoids evaluating expensive regex when clearly irrelevant.
    
    Returns:
        (matches, negatives_triggered) - matched signals and triggered negative patterns
    """
    matches: List[SignalMatch] = []
    negatives_triggered: List[str] = []
    
    # Helper to check pattern with prefilter
    def try_match(spec: PatternSpec) -> Optional[SignalMatch]:
        # Prefilter: cheap substring check first
        if not spec.matches_prefilter(output):
            return None
        # Only run regex if prefilter passes
        match = spec.regex.search(output)
        if match:
            hint = _format_hint(spec.hint_template, match)
            return SignalMatch(
                pattern_name=spec.name,
                category=spec.category,
                hint=hint,
                confidence=spec.base_confidence,
                priority=spec.priority,
                weight=spec.signal_weight,
                requires_corroboration=spec.requires_second_signal,
                match=match,
            )
        return None
    
    # Check infrastructure patterns first (highest priority)
    for spec in _INFRASTRUCTURE_PATTERNS:
        result = try_match(spec)
        if result:
            matches.append(result)
    
    # Check environment patterns
    for spec in _ENV_PATTERNS:
        result = try_match(spec)
        if result:
            matches.append(result)
    
    # Check test patterns
    for spec in _TEST_PATTERNS:
        result = try_match(spec)
        if result:
            matches.append(result)
    
    # Check code patterns
    for spec in _CODE_PATTERNS:
        result = try_match(spec)
        if result:
            matches.append(result)
    
    # Check negative patterns (also with prefilter)
    for neg in _NEGATIVE_PATTERNS:
        if neg.matches_prefilter(output) and neg.regex.search(output):
            negatives_triggered.append(neg.name)
    
    return matches, negatives_triggered


def _apply_negative_filters(
    matches: List[SignalMatch],
    negatives_triggered: List[str],
    output: str,
) -> List[SignalMatch]:
    """
    Apply negative patterns to filter/penalize matches.
    
    This reduces false positives by penalizing matches when
    context suggests the pattern is not a real error.
    """
    if not negatives_triggered:
        return matches
    
    filtered = []
    for match in matches:
        should_suppress = False
        confidence_penalty = 1.0
        
        for neg in _NEGATIVE_PATTERNS:
            if neg.name in negatives_triggered:
                if match.category in neg.suppresses_categories:
                    confidence_penalty *= neg.confidence_penalty
                    if confidence_penalty < 0.4:
                        should_suppress = True
                        break
        
        if not should_suppress:
            # Apply penalty but keep the match
            filtered.append(SignalMatch(
                pattern_name=match.pattern_name,
                category=match.category,
                hint=match.hint,
                confidence=match.confidence * confidence_penalty,
                priority=match.priority,
                weight=match.weight,
                requires_corroboration=match.requires_corroboration,
                match=match.match,
            ))
    
    return filtered


def _score_and_select(
    matches: List[SignalMatch],
) -> Optional[SignalMatch]:
    """
    Score matches and select the best one using multi-signal logic.
    
    Algorithm:
    1. If infrastructure signal present (priority >= 100), use it immediately
    2. Group by category, sum weights
    3. For categories requiring corroboration, need total_weight >= 1.5
    4. Select highest priority category, then highest confidence within
    """
    if not matches:
        return None
    
    # Infrastructure signals override everything
    infra_signals = [m for m in matches if m.priority >= 100]
    if infra_signals:
        # Return highest priority, then highest confidence
        return max(infra_signals, key=lambda m: (m.priority, m.confidence))
    
    # Group by category
    by_category: Dict[FailureCategory, List[SignalMatch]] = {}
    for match in matches:
        if match.category not in by_category:
            by_category[match.category] = []
        by_category[match.category].append(match)
    
    # Filter categories that need corroboration but don't have enough signals
    valid_categories: Dict[FailureCategory, List[SignalMatch]] = {}
    for cat, cat_matches in by_category.items():
        total_weight = sum(m.weight for m in cat_matches)
        needs_corroboration = any(m.requires_corroboration for m in cat_matches)
        
        if needs_corroboration and total_weight < 1.5:
            # Not enough evidence - skip this category
            continue
        
        valid_categories[cat] = cat_matches
    
    if not valid_categories:
        # Fall back to best single match even if it needs corroboration
        return max(matches, key=lambda m: (m.priority, m.confidence))
    
    # Select best category by priority, then best match within by confidence
    best_cat = max(
        valid_categories.keys(),
        key=lambda c: max(m.priority for m in valid_categories[c])
    )
    best_match = max(
        valid_categories[best_cat],
        key=lambda m: (m.priority, m.confidence)
    )
    
    return best_match


# =============================================================================
# Attribution Functions (Updated for Multi-Signal)
# =============================================================================

def attribute_failure(
    output: str,
    gate_name: str = "",
    exit_code: int = 1,
) -> AttributionResult:
    """
    Attribute a single sandbox failure using multi-signal scoring.
    
    Multi-signal detection reduces false positives:
    1. Collects ALL matching signals from output
    2. Applies negative patterns to penalize false positives
    3. Scores categories by total signal weight
    4. Requires corroboration (weight >= 1.5) for weak signals
    5. Infrastructure signals (timeout/crash) override everything
    
    Priority order (infrastructure shadows everything):
    1. Infrastructure failures (timeout, container crash) - priority 100
    2. Environment issues (not code's fault) - priority 10
    3. Test framework issues - priority 5
    4. Generated code issues - priority 1
    
    Args:
        output: Combined stdout+stderr from the gate
        gate_name: Name of the gate (ruff, mypy, pytest, etc.)
        exit_code: Exit code of the gate
        
    Returns:
        AttributionResult with category, confidence, hints, and fingerprint
    """
    signals: List[str] = []
    fingerprint = compute_evidence_fingerprint(output, gate_name)
    
    # Special case: success
    if exit_code == 0:
        return AttributionResult(
            category=FailureCategory.UNKNOWN,
            confidence=1.0,
            fix_hints=["No failure to attribute (exit code 0)"],
            raw_signals=["exit_code=0"],
            evidence_fingerprint=fingerprint,
            gate_name=gate_name,
        )
    
    # Extract file location (used for all categories)
    file_path: Optional[str] = None
    line_num: Optional[int] = None
    for pattern in _FILE_PATTERNS:
        match = pattern.search(output)
        if match:
            file_path = match.group(1)
            try:
                line_num = int(match.group(2))
            except (IndexError, ValueError):
                pass
            signals.append(f"file_match:{file_path}:{line_num}")
            break
    
    # Collect all signals
    matches, negatives = _collect_signals(output)
    
    # Record what we found
    for m in matches:
        signals.append(f"signal:{m.pattern_name}:{m.category.value}")
    for neg in negatives:
        signals.append(f"negative:{neg}")
    
    # Apply negative pattern filtering
    filtered = _apply_negative_filters(matches, negatives, output)
    
    # Score and select best match
    best = _score_and_select(filtered)
    
    if best:
        return AttributionResult(
            category=best.category,
            confidence=best.confidence,
            likely_cause_file=file_path,
            likely_cause_lines=(line_num, line_num) if line_num else None,
            fix_hints=[best.hint],
            raw_signals=signals,
            evidence_fingerprint=fingerprint,
            gate_name=gate_name,
        )
    
    # Fallback based on gate name
    category = _category_from_gate(gate_name)
    signals.append(f"fallback_gate:{gate_name}")
    
    return AttributionResult(
        category=category,
        confidence=0.3,  # Low confidence for fallback
        likely_cause_file=file_path,
        likely_cause_lines=(line_num, line_num) if line_num else None,
        fix_hints=[f"Check {gate_name} output for details"],
        raw_signals=signals,
        evidence_fingerprint=fingerprint,
        gate_name=gate_name,
    )


def attribute_gate_results(
    gate_results: List[Dict[str, Any]],
) -> List[AttributionResult]:
    """
    Attribute multiple gate failures with deterministic ordering.
    
    Args:
        gate_results: List of gate result dicts with keys:
            - name: str
            - passed: bool
            - output: str
            - return_code: int
            
    Returns:
        List of AttributionResult for failed gates only, sorted deterministically
        by (category, file, line, confidence_desc, fingerprint)
    """
    attributions = []
    for gate in gate_results:
        if gate.get("passed", True):
            continue
        result = attribute_failure(
            output=gate.get("output", ""),
            gate_name=gate.get("name", ""),
            exit_code=gate.get("return_code", 1),
        )
        attributions.append(result)
    
    # Deterministic sorting for CI reproducibility
    attributions.sort(key=lambda a: a.sort_key())
    
    # Hard bound on total attributions
    return attributions[:MAX_ATTRIBUTIONS]


def summarize_attributions(
    attributions: List[AttributionResult],
) -> Dict[str, Any]:
    """
    Create a bounded summary of attributions.
    
    All outputs are deterministically sorted:
    - by_category: sorted alphabetically by category name
    - top_hints: sorted alphabetically for stable output
    - fingerprints: sorted for deduplication stability
    
    Returns:
        Dict with keys in alphabetical order:
            - actionable: bool (True if code issues found)
            - by_category: Dict[str, int] (sorted by key)
            - fingerprints: List[str] (sorted, for deduplication tracking)
            - primary_category: str (most common category)
            - top_hints: List[str] (sorted, max MAX_SUMMARY_HINTS)
            - total_failures: int
    """
    if not attributions:
        return dict(sorted({
            "actionable": False,
            "by_category": {},
            "fingerprints": [],
            "primary_category": "unknown",
            "top_hints": [],
            "total_failures": 0,
        }.items()))
    
    # Count by category (deterministic ordering)
    by_category: Dict[str, int] = {}
    for attr in attributions:
        cat = attr.category.value
        by_category[cat] = by_category.get(cat, 0) + 1
    
    # Sort categories alphabetically for determinism
    sorted_categories = dict(sorted(by_category.items()))
    
    # Bound categories (keep top N by count, then re-sort alphabetically)
    if len(sorted_categories) > MAX_SUMMARY_CATEGORIES:
        sorted_by_count = sorted(sorted_categories.items(), key=lambda x: (-x[1], x[0]))
        sorted_categories = dict(sorted(dict(sorted_by_count[:MAX_SUMMARY_CATEGORIES]).items()))
    
    # Collect hints (deduplicated, then sorted alphabetically)
    seen_hints: set = set()
    for attr in attributions:
        for hint in attr.fix_hints:
            bounded_hint = hint[:MAX_HINT_LENGTH]
            if bounded_hint not in seen_hints:
                seen_hints.add(bounded_hint)
    top_hints = sorted(seen_hints)[:MAX_SUMMARY_HINTS]  # Sort then bound
    
    # Collect fingerprints (sorted for determinism)
    fingerprints = sorted(set(attr.evidence_fingerprint for attr in attributions if attr.evidence_fingerprint))
    
    # Check if any are actionable code issues
    code_categories = {
        FailureCategory.CODE_SYNTAX_ERROR.value,
        FailureCategory.CODE_TYPE_ERROR.value,
        FailureCategory.CODE_RUNTIME_ERROR.value,
        FailureCategory.CODE_ASSERTION_FAILURE.value,
        FailureCategory.CODE_SECURITY_VIOLATION.value,
    }
    actionable = any(cat in code_categories for cat in sorted_categories)
    
    # Primary category: most common, tie-break alphabetically
    primary_category = max(
        sorted_categories.keys(),
        key=lambda k: (sorted_categories[k], k),  # (count, alpha for ties)
    ) if sorted_categories else "unknown"
    
    # Return with sorted keys for determinism
    return dict(sorted({
        "actionable": actionable,
        "by_category": sorted_categories,
        "fingerprints": fingerprints,
        "primary_category": primary_category,
        "top_hints": top_hints,
        "total_failures": len(attributions),
    }.items()))


# =============================================================================
# Helpers
# =============================================================================

def _format_hint(template: str, match: re.Match) -> str:
    """Format hint template with match groups."""
    hint = template
    for i, group in enumerate(match.groups(), 1):
        if group:
            hint = hint.replace(f"{{{i}}}", group)
    return hint


def _category_from_gate(gate_name: str) -> FailureCategory:
    """Infer category from gate name as fallback."""
    gate_lower = gate_name.lower()
    if gate_lower in ("ruff", "mypy"):
        return FailureCategory.CODE_TYPE_ERROR
    elif gate_lower == "bandit":
        return FailureCategory.CODE_SECURITY_VIOLATION
    elif gate_lower == "pytest":
        return FailureCategory.CODE_ASSERTION_FAILURE
    else:
        return FailureCategory.UNKNOWN


def is_environment_failure(attribution: AttributionResult) -> bool:
    """Check if failure is an environment issue (not code's fault)."""
    return attribution.category.value.startswith("env_")


def is_code_failure(attribution: AttributionResult) -> bool:
    """Check if failure is a generated code issue (regeneration target)."""
    return attribution.category.value.startswith("code_")


def is_test_failure(attribution: AttributionResult) -> bool:
    """Check if failure is a test framework issue."""
    return attribution.category.value.startswith("test_")

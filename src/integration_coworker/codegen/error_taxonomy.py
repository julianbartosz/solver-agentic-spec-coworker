"""
Error Taxonomy for Codegen Sandbox Failures

Provides structured classification of sandbox errors to enable targeted
LLM feedback prompts for regeneration attempts.

Design Principles:
- Each error class maps to specific repair strategies
- Error extraction is AST/regex-based for reliability
- Feedback prompts are templates with error-specific context
- Supports composable error chains (e.g., import error → undefined name)

Architecture:
- ErrorClass: Enum of high-level error categories
- SandboxError: Structured error with classification + context
- ErrorExtractor: Extracts structured errors from sandbox output
- FeedbackPromptBuilder: Generates targeted LLM prompts from errors

References:
- V42-007: Sandbox → Regeneration Loop
- ADR-0005: Production Codegen Quality Gates
"""
import re
import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Error Classification Taxonomy
# =============================================================================

class ErrorClass(Enum):
    """
    High-level classification of sandbox errors.
    
    Each class maps to specific repair strategies and feedback prompts.
    """
    # Syntax errors - code doesn't parse
    SYNTAX_ERROR = auto()
    
    # Import errors - module/name not found
    IMPORT_ERROR = auto()
    UNDEFINED_NAME = auto()  # F821 - often caused by import issues
    
    # Type errors - type annotation issues
    TYPE_ERROR = auto()
    TYPE_MISMATCH = auto()
    
    # Test errors - test-specific failures
    TEST_ASSERTION = auto()
    TEST_MOCK_ERROR = auto()  # Wrong mock path, mock not applied
    TEST_FIXTURE_ERROR = auto()  # Missing fixture, wrong fixture type
    TEST_PARAMETER_ERROR = auto()  # Wrong parameters passed to flow
    
    # Security errors - blocked patterns
    SECURITY_VIOLATION = auto()
    
    # Runtime errors - would fail at runtime
    ATTRIBUTE_ERROR = auto()
    KEY_ERROR = auto()
    VALUE_ERROR = auto()
    
    # Style/lint errors - formatting issues
    LINT_ERROR = auto()
    FORMAT_ERROR = auto()
    
    # Unknown - fallback
    UNKNOWN = auto()


class RepairStrategy(Enum):
    """
    Repair strategies for each error class.
    
    Maps error classes to appropriate repair approaches.
    """
    # Regenerate the entire artifact with error context
    REGENERATE_WITH_CONTEXT = auto()
    
    # Apply targeted inline fix
    INLINE_FIX = auto()
    
    # Fix imports specifically
    FIX_IMPORTS = auto()
    
    # Fix mock/patch paths in tests
    FIX_MOCK_PATHS = auto()
    
    # Add missing fixtures
    ADD_FIXTURES = auto()
    
    # Align test parameters with flow signature
    ALIGN_PARAMETERS = auto()
    
    # Security-related - may need full regeneration
    SECURITY_REGENERATE = auto()
    
    # Style only - use auto-formatter
    AUTO_FORMAT = auto()
    
    # No repair possible
    NO_REPAIR = auto()


# Error class → primary repair strategy mapping
ERROR_REPAIR_STRATEGIES: dict[ErrorClass, RepairStrategy] = {
    ErrorClass.SYNTAX_ERROR: RepairStrategy.REGENERATE_WITH_CONTEXT,
    ErrorClass.IMPORT_ERROR: RepairStrategy.FIX_IMPORTS,
    ErrorClass.UNDEFINED_NAME: RepairStrategy.FIX_IMPORTS,
    ErrorClass.TYPE_ERROR: RepairStrategy.INLINE_FIX,
    ErrorClass.TYPE_MISMATCH: RepairStrategy.INLINE_FIX,
    ErrorClass.TEST_ASSERTION: RepairStrategy.REGENERATE_WITH_CONTEXT,
    ErrorClass.TEST_MOCK_ERROR: RepairStrategy.FIX_MOCK_PATHS,
    ErrorClass.TEST_FIXTURE_ERROR: RepairStrategy.ADD_FIXTURES,
    ErrorClass.TEST_PARAMETER_ERROR: RepairStrategy.ALIGN_PARAMETERS,
    ErrorClass.SECURITY_VIOLATION: RepairStrategy.SECURITY_REGENERATE,
    ErrorClass.ATTRIBUTE_ERROR: RepairStrategy.REGENERATE_WITH_CONTEXT,
    ErrorClass.KEY_ERROR: RepairStrategy.INLINE_FIX,
    ErrorClass.VALUE_ERROR: RepairStrategy.INLINE_FIX,
    ErrorClass.LINT_ERROR: RepairStrategy.AUTO_FORMAT,
    ErrorClass.FORMAT_ERROR: RepairStrategy.AUTO_FORMAT,
    ErrorClass.UNKNOWN: RepairStrategy.REGENERATE_WITH_CONTEXT,
}


@dataclass
class SandboxError:
    """
    Structured representation of a sandbox error.
    
    Contains all context needed to generate targeted repair prompts.
    """
    # Classification
    error_class: ErrorClass
    repair_strategy: RepairStrategy
    
    # Error details
    gate_name: str  # Which gate failed (ruff, mypy, pytest, etc.)
    error_code: Optional[str] = None  # e.g., F821, E501, etc.
    message: str = ""
    
    # Location
    file_path: Optional[str] = None
    line_number: Optional[int] = None
    column: Optional[int] = None
    
    # Context
    problematic_code: Optional[str] = None  # The code snippet with the error
    suggested_fix: Optional[str] = None  # If the tool suggests a fix
    
    # For import errors
    missing_import: Optional[str] = None
    import_source: Optional[str] = None  # Where it should be imported from
    
    # For test errors
    test_name: Optional[str] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    
    # For mock errors
    mock_target: Optional[str] = None  # What was being patched
    correct_target: Optional[str] = None  # What should be patched
    
    # Raw output for fallback
    raw_output: str = ""
    
    @property
    def is_fixable(self) -> bool:
        """Check if this error can potentially be fixed automatically."""
        return self.repair_strategy != RepairStrategy.NO_REPAIR
    
    @property
    def priority(self) -> int:
        """
        Priority for fixing (lower = fix first).
        
        Import errors should be fixed first as they often cause cascading errors.
        """
        priority_map = {
            ErrorClass.IMPORT_ERROR: 1,
            ErrorClass.UNDEFINED_NAME: 2,
            ErrorClass.SYNTAX_ERROR: 3,
            ErrorClass.TEST_MOCK_ERROR: 4,
            ErrorClass.TEST_FIXTURE_ERROR: 5,
            ErrorClass.TEST_PARAMETER_ERROR: 6,
            ErrorClass.TYPE_ERROR: 7,
            ErrorClass.TYPE_MISMATCH: 8,
            ErrorClass.TEST_ASSERTION: 9,
            ErrorClass.SECURITY_VIOLATION: 10,
            ErrorClass.ATTRIBUTE_ERROR: 11,
            ErrorClass.KEY_ERROR: 12,
            ErrorClass.VALUE_ERROR: 13,
            ErrorClass.LINT_ERROR: 14,
            ErrorClass.FORMAT_ERROR: 15,
            ErrorClass.UNKNOWN: 99,
        }
        return priority_map.get(self.error_class, 99)


@dataclass
class SandboxErrorReport:
    """
    Collection of errors from a sandbox run with analysis.
    """
    errors: list[SandboxError] = field(default_factory=list)
    gate_name: str = ""
    raw_output: str = ""
    
    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
    
    @property
    def fixable_errors(self) -> list[SandboxError]:
        """Errors that can potentially be auto-fixed."""
        return [e for e in self.errors if e.is_fixable]
    
    @property
    def by_priority(self) -> list[SandboxError]:
        """Errors sorted by fix priority."""
        return sorted(self.errors, key=lambda e: e.priority)
    
    @property
    def by_class(self) -> dict[ErrorClass, list[SandboxError]]:
        """Errors grouped by class."""
        result: dict[ErrorClass, list[SandboxError]] = {}
        for error in self.errors:
            if error.error_class not in result:
                result[error.error_class] = []
            result[error.error_class].append(error)
        return result
    
    @property
    def primary_error_class(self) -> Optional[ErrorClass]:
        """The most important error class to address first."""
        if not self.errors:
            return None
        return self.by_priority[0].error_class
    
    @property
    def primary_repair_strategy(self) -> Optional[RepairStrategy]:
        """The repair strategy for the primary error."""
        if not self.errors:
            return None
        return self.by_priority[0].repair_strategy


# =============================================================================
# Error Extraction
# =============================================================================

class ErrorExtractor:
    """
    Extracts structured SandboxError objects from raw gate output.
    
    Uses pattern matching to identify error types and extract context.
    """
    
    # Ruff error patterns
    RUFF_PATTERNS = [
        # F821: undefined name 'X'
        (r"(\S+\.py):(\d+):(\d+): (F821) Undefined name `(\w+)`",
         ErrorClass.UNDEFINED_NAME, "undefined_name"),
        # F401: 'X' imported but unused
        (r"(\S+\.py):(\d+):(\d+): (F401) `([^`]+)` imported but unused",
         ErrorClass.IMPORT_ERROR, "unused_import"),
        # E501: line too long
        (r"(\S+\.py):(\d+):(\d+): (E501) Line too long",
         ErrorClass.LINT_ERROR, "line_too_long"),
        # General ruff pattern
        (r"(\S+\.py):(\d+):(\d+): ([A-Z]\d+) (.+)",
         ErrorClass.LINT_ERROR, "general"),
    ]
    
    # Mypy error patterns
    MYPY_PATTERNS = [
        # error: Name "X" is not defined
        (r"(\S+\.py):(\d+): error: Name \"(\w+)\" is not defined",
         ErrorClass.UNDEFINED_NAME, "name_not_defined"),
        # error: Cannot find implementation or library stub for module named "X"
        (r"(\S+\.py):(\d+): error: Cannot find implementation or library stub for module named \"([^\"]+)\"",
         ErrorClass.IMPORT_ERROR, "module_not_found"),
        # error: Incompatible types
        (r"(\S+\.py):(\d+): error: Incompatible types in assignment",
         ErrorClass.TYPE_MISMATCH, "incompatible_types"),
        # General mypy error
        (r"(\S+\.py):(\d+): error: (.+)",
         ErrorClass.TYPE_ERROR, "general"),
    ]
    
    # Pytest error patterns
    PYTEST_PATTERNS = [
        # AttributeError: <module 'X'> does not have the attribute 'Y'
        (r"AttributeError: <?module '([^']+)'(?: from [^>]+)?> does not have the attribute '(\w+)'",
         ErrorClass.TEST_MOCK_ERROR, "mock_attribute_error"),
        # ModuleNotFoundError: No module named 'X'
        (r"ModuleNotFoundError: No module named '([^']+)'",
         ErrorClass.IMPORT_ERROR, "module_not_found"),
        # ImportError: cannot import name 'X' from 'Y'
        (r"ImportError: cannot import name '(\w+)' from '([^']+)'",
         ErrorClass.IMPORT_ERROR, "import_name_error"),
        # KeyError: 'X'
        (r"KeyError: ['\"](\w+)['\"]",
         ErrorClass.KEY_ERROR, "key_error"),
        # AssertionError
        (r"AssertionError:?\s*(.*)",
         ErrorClass.TEST_ASSERTION, "assertion_error"),
        # fixture 'X' not found
        (r"fixture '(\w+)' not found",
         ErrorClass.TEST_FIXTURE_ERROR, "fixture_not_found"),
        # TypeError: X() missing required positional argument: 'Y'
        (r"TypeError: (\w+)\(\) missing (\d+) required positional argument",
         ErrorClass.TEST_PARAMETER_ERROR, "missing_argument"),
        # ValueError: X is required
        (r"ValueError: (\w+) is required",
         ErrorClass.VALUE_ERROR, "value_required"),
        # httpcore.ConnectError - test making real HTTP calls
        (r"httpcore\.ConnectError|ConnectError",
         ErrorClass.TEST_MOCK_ERROR, "unmocked_http"),
    ]
    
    # Security patterns (from bandit)
    BANDIT_PATTERNS = [
        (r"Issue: \[(\w+):(\w+)\] (.+)",
         ErrorClass.SECURITY_VIOLATION, "bandit_issue"),
    ]
    
    @classmethod
    def extract_from_gate(
        cls,
        gate_name: str,
        output: str,
        artifacts: Optional[dict[str, str]] = None,
    ) -> SandboxErrorReport:
        """
        Extract structured errors from a gate's output.
        
        Args:
            gate_name: Name of the gate (ruff, mypy, pytest, bandit)
            output: Raw output from the gate
            artifacts: Optional dict of {filepath: content} for context extraction
            
        Returns:
            SandboxErrorReport with all extracted errors
        """
        errors: list[SandboxError] = []
        
        if gate_name == "ruff":
            errors = cls._extract_ruff_errors(output, artifacts)
        elif gate_name == "mypy":
            errors = cls._extract_mypy_errors(output, artifacts)
        elif gate_name == "pytest":
            errors = cls._extract_pytest_errors(output, artifacts)
        elif gate_name == "bandit":
            errors = cls._extract_bandit_errors(output, artifacts)
        else:
            # Unknown gate - try generic extraction
            errors = cls._extract_generic_errors(gate_name, output)
        
        return SandboxErrorReport(
            errors=errors,
            gate_name=gate_name,
            raw_output=output,
        )
    
    @classmethod
    def _extract_ruff_errors(
        cls,
        output: str,
        artifacts: Optional[dict[str, str]] = None,
    ) -> list[SandboxError]:
        """Extract errors from ruff output."""
        errors = []
        
        for pattern, error_class, error_type in cls.RUFF_PATTERNS:
            for match in re.finditer(pattern, output, re.MULTILINE):
                groups = match.groups()
                
                error = SandboxError(
                    error_class=error_class,
                    repair_strategy=ERROR_REPAIR_STRATEGIES[error_class],
                    gate_name="ruff",
                    file_path=groups[0] if len(groups) > 0 else None,
                    line_number=int(groups[1]) if len(groups) > 1 else None,
                    column=int(groups[2]) if len(groups) > 2 else None,
                    error_code=groups[3] if len(groups) > 3 else None,
                    message=match.group(0),
                    raw_output=output,
                )
                
                # Extract specific details based on error type
                if error_type == "undefined_name" and len(groups) > 4:
                    error.missing_import = groups[4]
                elif error_type == "unused_import" and len(groups) > 4:
                    error.missing_import = groups[4]
                
                # Extract problematic code if artifacts provided
                if artifacts and error.file_path and error.line_number:
                    error.problematic_code = cls._extract_code_context(
                        artifacts, error.file_path, error.line_number
                    )
                
                errors.append(error)
        
        return errors
    
    @classmethod
    def _extract_mypy_errors(
        cls,
        output: str,
        artifacts: Optional[dict[str, str]] = None,
    ) -> list[SandboxError]:
        """Extract errors from mypy output."""
        errors = []
        
        for pattern, error_class, error_type in cls.MYPY_PATTERNS:
            for match in re.finditer(pattern, output, re.MULTILINE):
                groups = match.groups()
                
                error = SandboxError(
                    error_class=error_class,
                    repair_strategy=ERROR_REPAIR_STRATEGIES[error_class],
                    gate_name="mypy",
                    file_path=groups[0] if len(groups) > 0 else None,
                    line_number=int(groups[1]) if len(groups) > 1 else None,
                    message=match.group(0),
                    raw_output=output,
                )
                
                # Extract specific details
                if error_type == "name_not_defined" and len(groups) > 2:
                    error.missing_import = groups[2]
                elif error_type == "module_not_found" and len(groups) > 2:
                    error.import_source = groups[2]
                
                if artifacts and error.file_path and error.line_number:
                    error.problematic_code = cls._extract_code_context(
                        artifacts, error.file_path, error.line_number
                    )
                
                errors.append(error)
        
        return errors
    
    @classmethod
    def _extract_pytest_errors(
        cls,
        output: str,
        artifacts: Optional[dict[str, str]] = None,
    ) -> list[SandboxError]:
        """Extract errors from pytest output."""
        errors = []
        
        # Track current test for context
        current_test = None
        test_pattern = r"(test_\w+)"
        for line in output.split('\n'):
            test_match = re.search(test_pattern, line)
            if test_match:
                current_test = test_match.group(1)
        
        for pattern, error_class, error_type in cls.PYTEST_PATTERNS:
            for match in re.finditer(pattern, output, re.MULTILINE | re.DOTALL):
                groups = match.groups()
                
                error = SandboxError(
                    error_class=error_class,
                    repair_strategy=ERROR_REPAIR_STRATEGIES[error_class],
                    gate_name="pytest",
                    message=match.group(0),
                    raw_output=output,
                    test_name=current_test,
                )
                
                # Extract specific details based on error type
                if error_type == "mock_attribute_error" and len(groups) >= 2:
                    error.mock_target = groups[0]
                    error.missing_import = groups[1]
                    # Suggest correct patch target
                    error.suggested_fix = (
                        f"The mock patch path is incorrect. The module '{groups[0]}' "
                        f"doesn't have attribute '{groups[1]}'. Check how the class is "
                        f"imported in the flow code and patch where it's looked up."
                    )
                elif error_type == "module_not_found" and len(groups) >= 1:
                    error.import_source = groups[0]
                elif error_type == "import_name_error" and len(groups) >= 2:
                    error.missing_import = groups[0]
                    error.import_source = groups[1]
                elif error_type == "key_error" and len(groups) >= 1:
                    error.expected_value = groups[0]
                elif error_type == "fixture_not_found" and len(groups) >= 1:
                    error.missing_import = groups[0]  # fixture name
                elif error_type == "unmocked_http":
                    error.suggested_fix = (
                        "Tests are making real HTTP calls. Every test must mock "
                        "the client class: `with patch('module.ClientClass') as MockClient:`"
                    )
                
                errors.append(error)
        
        return errors
    
    @classmethod
    def _extract_bandit_errors(
        cls,
        output: str,
        artifacts: Optional[dict[str, str]] = None,
    ) -> list[SandboxError]:
        """Extract errors from bandit security scan."""
        errors = []
        
        for pattern, error_class, error_type in cls.BANDIT_PATTERNS:
            for match in re.finditer(pattern, output, re.MULTILINE):
                groups = match.groups()
                
                error = SandboxError(
                    error_class=error_class,
                    repair_strategy=ERROR_REPAIR_STRATEGIES[error_class],
                    gate_name="bandit",
                    error_code=f"{groups[0]}:{groups[1]}" if len(groups) >= 2 else None,
                    message=groups[2] if len(groups) >= 3 else match.group(0),
                    raw_output=output,
                )
                
                errors.append(error)
        
        return errors
    
    @classmethod
    def _extract_generic_errors(
        cls,
        gate_name: str,
        output: str,
    ) -> list[SandboxError]:
        """Generic error extraction for unknown gates."""
        errors = []
        
        # Look for common error patterns
        error_patterns = [
            (r"error:", ErrorClass.UNKNOWN),
            (r"Error:", ErrorClass.UNKNOWN),
            (r"ERROR:", ErrorClass.UNKNOWN),
            (r"failed", ErrorClass.UNKNOWN),
            (r"FAILED", ErrorClass.UNKNOWN),
        ]
        
        for pattern, error_class in error_patterns:
            if re.search(pattern, output):
                errors.append(SandboxError(
                    error_class=error_class,
                    repair_strategy=RepairStrategy.REGENERATE_WITH_CONTEXT,
                    gate_name=gate_name,
                    message=output[:500],  # First 500 chars
                    raw_output=output,
                ))
                break  # Only add one generic error
        
        return errors
    
    @classmethod
    def _extract_code_context(
        cls,
        artifacts: dict[str, str],
        file_path: str,
        line_number: int,
        context_lines: int = 3,
    ) -> Optional[str]:
        """Extract code context around an error location."""
        # Normalize path
        for path, content in artifacts.items():
            if path.endswith(file_path) or file_path.endswith(path):
                lines = content.split('\n')
                start = max(0, line_number - context_lines - 1)
                end = min(len(lines), line_number + context_lines)
                
                context = []
                for i in range(start, end):
                    marker = ">>> " if i == line_number - 1 else "    "
                    context.append(f"{marker}{i+1}: {lines[i]}")
                
                return '\n'.join(context)
        
        return None


# =============================================================================
# Feedback Prompt Builder
# =============================================================================

class FeedbackPromptBuilder:
    """
    Builds targeted LLM feedback prompts from sandbox errors.
    
    Different error classes get different prompt templates optimized
    for that type of fix.
    """
    
    @classmethod
    def build_prompt(
        cls,
        error_report: SandboxErrorReport,
        original_code: str,
        artifact_type: str,
        flow_code: Optional[str] = None,
        client_code: Optional[str] = None,
    ) -> str:
        """
        Build a targeted feedback prompt for regeneration.
        
        Args:
            error_report: Structured errors from sandbox
            original_code: The code that failed
            artifact_type: Type of artifact (client, flow, test)
            flow_code: Flow code for context (useful for test fixes)
            client_code: Client code for context
            
        Returns:
            Formatted prompt string for LLM
        """
        if not error_report.has_errors:
            return ""
        
        # Get primary repair strategy
        strategy = error_report.primary_repair_strategy
        
        if strategy == RepairStrategy.FIX_IMPORTS:
            return cls._build_import_fix_prompt(error_report, original_code, artifact_type)
        elif strategy == RepairStrategy.FIX_MOCK_PATHS:
            return cls._build_mock_fix_prompt(error_report, original_code, flow_code)
        elif strategy == RepairStrategy.ADD_FIXTURES:
            return cls._build_fixture_fix_prompt(error_report, original_code)
        elif strategy == RepairStrategy.ALIGN_PARAMETERS:
            return cls._build_parameter_fix_prompt(error_report, original_code, flow_code)
        elif strategy == RepairStrategy.INLINE_FIX:
            return cls._build_inline_fix_prompt(error_report, original_code, artifact_type)
        elif strategy == RepairStrategy.SECURITY_REGENERATE:
            return cls._build_security_fix_prompt(error_report, original_code, artifact_type)
        else:
            return cls._build_general_fix_prompt(error_report, original_code, artifact_type)
    
    @classmethod
    def _build_import_fix_prompt(
        cls,
        error_report: SandboxErrorReport,
        original_code: str,
        artifact_type: str,
    ) -> str:
        """Build prompt for import-related fixes."""
        import_errors = error_report.by_class.get(ErrorClass.IMPORT_ERROR, [])
        undefined_errors = error_report.by_class.get(ErrorClass.UNDEFINED_NAME, [])
        
        all_errors = import_errors + undefined_errors
        
        missing_names = []
        for error in all_errors:
            if error.missing_import:
                missing_names.append(error.missing_import)
            if error.import_source:
                missing_names.append(f"module '{error.import_source}'")
        
        error_details = '\n'.join([
            f"- {e.message}" for e in all_errors[:5]
        ])
        
        return f"""The generated {artifact_type} code has import errors that need to be fixed.

ERRORS FOUND:
{error_details}

UNDEFINED/MISSING NAMES: {', '.join(set(missing_names))}

ORIGINAL CODE:
```python
{original_code}
```

REQUIREMENTS:
1. Add any missing imports at the top of the file
2. If a name is undefined, either import it or define it
3. Do NOT import from non-existent modules (e.g., integration_framework.*, integration_coworker_runtime.*)
4. For inline policy mode, all HTTP client code must be self-contained
5. Use standard library or common packages only (httpx, requests, etc.)

Return the complete fixed Python code with all imports resolved:"""

    @classmethod
    def _build_mock_fix_prompt(
        cls,
        error_report: SandboxErrorReport,
        original_code: str,
        flow_code: Optional[str] = None,
    ) -> str:
        """Build prompt for mock/patch path fixes."""
        mock_errors = error_report.by_class.get(ErrorClass.TEST_MOCK_ERROR, [])
        
        error_details = '\n'.join([
            f"- {e.message}" + (f"\n  Suggestion: {e.suggested_fix}" if e.suggested_fix else "")
            for e in mock_errors[:5]
        ])
        
        flow_context = ""
        if flow_code:
            # Extract import statements from flow
            import_lines = [
                line for line in flow_code.split('\n')
                if line.strip().startswith(('import ', 'from '))
            ]
            flow_context = f"""
FLOW CODE IMPORTS (patch targets must match these):
```python
{chr(10).join(import_lines[:10])}
```
"""
        
        return f"""The test code has mock patching errors. The patch paths don't match how classes are imported.

ERRORS FOUND:
{error_details}

{flow_context}

ORIGINAL TEST CODE:
```python
{original_code}
```

CRITICAL RULES FOR MOCK PATCHING:
1. When you use `from X.Y import ClassName`, patch at the MODULE WHERE IT'S USED, not where defined
2. Example: If flow has `from clients.api import ApiClient`, patch `'flows.my_flow.ApiClient'`
3. The patch target must be the FULL module path where the name is looked up
4. If the class is imported as an alias, patch the ALIAS name
5. Every test method MUST wrap flow calls in a `with patch(...):` context manager

Return the complete fixed test code with correct patch paths:"""

    @classmethod
    def _build_fixture_fix_prompt(
        cls,
        error_report: SandboxErrorReport,
        original_code: str,
    ) -> str:
        """Build prompt for missing fixture fixes."""
        fixture_errors = error_report.by_class.get(ErrorClass.TEST_FIXTURE_ERROR, [])
        
        missing_fixtures = [
            e.missing_import for e in fixture_errors if e.missing_import
        ]
        
        error_details = '\n'.join([
            f"- {e.message}" for e in fixture_errors[:5]
        ])
        
        return f"""The test code is missing required pytest fixtures.

ERRORS FOUND:
{error_details}

MISSING FIXTURES: {', '.join(set(missing_fixtures))}

ORIGINAL TEST CODE:
```python
{original_code}
```

REQUIREMENTS:
1. Add @pytest.fixture decorated functions for each missing fixture
2. Common fixtures needed: api_key, sample_payload, mock_client, mock_response
3. Fixtures should return sensible test data
4. Place fixtures at module level, before the test class

Example fixtures to add:
```python
@pytest.fixture
def api_key():
    return "test_api_key_12345"

@pytest.fixture
def sample_payload():
    return {{"key": "value", "data": "test"}}

@pytest.fixture
def mock_response():
    return {{"success": True, "data": {{"id": "123"}}}}

@pytest.fixture
def mock_client(mock_response):
    from unittest.mock import MagicMock
    client = MagicMock()
    client.some_method.return_value = mock_response
    return client
```

Return the complete fixed test code with all required fixtures:"""

    @classmethod
    def _build_parameter_fix_prompt(
        cls,
        error_report: SandboxErrorReport,
        original_code: str,
        flow_code: Optional[str] = None,
    ) -> str:
        """Build prompt for parameter mismatch fixes."""
        param_errors = error_report.by_class.get(ErrorClass.TEST_PARAMETER_ERROR, [])
        value_errors = error_report.by_class.get(ErrorClass.VALUE_ERROR, [])
        
        all_errors = param_errors + value_errors
        
        error_details = '\n'.join([
            f"- {e.message}" for e in all_errors[:5]
        ])
        
        flow_signature = ""
        if flow_code:
            # Extract function signature
            sig_match = re.search(r'def (\w+)\([^)]+\)', flow_code)
            if sig_match:
                flow_signature = f"\nFLOW FUNCTION SIGNATURE: `{sig_match.group(0)}`"
        
        return f"""The test code is calling the flow function with incorrect parameters.

ERRORS FOUND:
{error_details}
{flow_signature}

ORIGINAL TEST CODE:
```python
{original_code}
```

STANDARD FLOW SIGNATURE:
```python
def flow_function(api_key: str, payload: Dict[str, Any], **kwargs) -> Dict[str, Any]
```

REQUIREMENTS:
1. EVERY call to the flow function must pass BOTH api_key AND payload
2. api_key must be a non-empty string
3. payload must be a non-empty dictionary
4. Use fixtures: `flow_function(api_key=api_key, payload=sample_payload)`

Return the complete fixed test code with correct parameters:"""

    @classmethod
    def _build_inline_fix_prompt(
        cls,
        error_report: SandboxErrorReport,
        original_code: str,
        artifact_type: str,
    ) -> str:
        """Build prompt for inline/targeted fixes."""
        errors = error_report.by_priority[:5]
        
        error_details = '\n'.join([
            f"- Line {e.line_number or '?'}: {e.message}"
            + (f"\n  Code: {e.problematic_code}" if e.problematic_code else "")
            for e in errors
        ])
        
        return f"""The generated {artifact_type} code has errors that need inline fixes.

ERRORS FOUND:
{error_details}

ORIGINAL CODE:
```python
{original_code}
```

REQUIREMENTS:
1. Fix each error while preserving the overall structure
2. Don't remove functionality, only fix the specific issues
3. Ensure the code remains syntactically valid
4. Keep all existing imports and class/function definitions

Return the complete fixed Python code:"""

    @classmethod
    def _build_security_fix_prompt(
        cls,
        error_report: SandboxErrorReport,
        original_code: str,
        artifact_type: str,
    ) -> str:
        """Build prompt for security violation fixes."""
        security_errors = error_report.by_class.get(ErrorClass.SECURITY_VIOLATION, [])
        
        error_details = '\n'.join([
            f"- {e.error_code}: {e.message}" for e in security_errors[:5]
        ])
        
        return f"""The generated {artifact_type} code has security violations that must be fixed.

SECURITY ISSUES FOUND:
{error_details}

ORIGINAL CODE:
```python
{original_code}
```

SECURITY REQUIREMENTS:
1. DO NOT use exec(), eval(), or compile() with user input
2. DO NOT use subprocess with shell=True
3. DO NOT hardcode API keys, passwords, or secrets
4. DO NOT use pickle with untrusted data
5. Use safe alternatives: json.loads() instead of eval(), subprocess.run() with list args

Return the complete fixed Python code without security violations:"""

    @classmethod
    def _build_general_fix_prompt(
        cls,
        error_report: SandboxErrorReport,
        original_code: str,
        artifact_type: str,
    ) -> str:
        """Build general fix prompt as fallback."""
        error_details = '\n'.join([
            f"- [{e.gate_name}] {e.message}" for e in error_report.by_priority[:5]
        ])
        
        return f"""The generated {artifact_type} code failed validation with these errors.

ERRORS FOUND:
{error_details}

ORIGINAL CODE:
```python
{original_code}
```

Please fix ALL the errors and return the complete corrected Python code.
Ensure the code:
1. Is syntactically valid Python
2. Has all required imports
3. Follows proper type hints
4. Handles errors appropriately

Return the complete fixed Python code:"""


# =============================================================================
# Convenience Functions
# =============================================================================

def extract_and_classify_errors(
    gate_results: list[dict[str, Any]],
    artifacts: Optional[dict[str, str]] = None,
) -> dict[str, SandboxErrorReport]:
    """
    Extract and classify errors from all sandbox gate results.
    
    Args:
        gate_results: List of gate result dicts with 'name', 'passed', 'output'
        artifacts: Optional dict of {filepath: content}
        
    Returns:
        Dict mapping gate name to SandboxErrorReport
    """
    reports = {}
    
    for gate in gate_results:
        if not gate.get('passed', True):
            gate_name = gate.get('name', 'unknown')
            output = gate.get('output', '')
            
            report = ErrorExtractor.extract_from_gate(
                gate_name=gate_name,
                output=output,
                artifacts=artifacts,
            )
            
            if report.has_errors:
                reports[gate_name] = report
    
    return reports


def get_primary_error_for_feedback(
    gate_results: list[dict[str, Any]],
    artifacts: Optional[dict[str, str]] = None,
) -> Optional[SandboxErrorReport]:
    """
    Get the most important error report for feedback prompt generation.
    
    Prioritizes errors that are most likely to be root causes:
    1. Import errors (often cause cascading undefined names)
    2. Mock errors (tests can't run without proper mocks)
    3. Other errors
    
    Args:
        gate_results: List of gate result dicts
        artifacts: Optional artifact contents
        
    Returns:
        Most important SandboxErrorReport or None
    """
    reports = extract_and_classify_errors(gate_results, artifacts)
    
    if not reports:
        return None
    
    # Priority order for gates
    gate_priority = ['ruff', 'mypy', 'pytest', 'bandit', 'coverage']
    
    for gate in gate_priority:
        if gate in reports:
            return reports[gate]
    
    # Return first available
    return next(iter(reports.values()))

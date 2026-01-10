"""
Code-Test Cross-Validation Module (V25-001/V25-002 Fix)

This module provides semantic validation between generated code and tests to ensure:
1. Test assertions match actual code behavior
2. Test repair has full context to make correct fixes
3. Generated artifacts are internally consistent before sandbox validation
4. V27-005: Test parameters match actual flow function signatures

Design Principles:
- Sequential validation: Flow code is finalized BEFORE test generation
- Context-aware: Tests see actual flow implementation, not just skeleton
- Behavior extraction: AST analysis extracts actual validation patterns from code
- Smart repair: Test repair knows both the error AND the expected behavior

Architecture:
- CodeBehaviorExtractor: AST-based extraction of validation patterns from flow code
- TestAssertionAnalyzer: AST-based analysis of test assertions
- CrossValidator: Compares extracted behaviors with test assertions
- SmartTestRepair: Context-aware test repair with flow code knowledge
- V27-005: FlowSignatureExtractor for parameter name validation

References:
- Bug V25-001: Test/Code Generation Mismatch
- Bug V25-002: Test Repair Mechanism Failure
- Bug V27-005: Test parameter signature mismatch
- BUG_REPORT_V25_DEMO.md
"""
import ast
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ValidationBehavior(Enum):
    """Types of validation behavior that can be extracted from code."""
    RAISES_ON_NONE = "raises_on_none"  # Raises ValueError when param is None
    RAISES_ON_EMPTY = "raises_on_empty"  # Raises ValueError when param is empty string/dict
    RAISES_ON_MISSING = "raises_on_missing"  # Raises ValueError when param is missing
    CONVERTS_NONE_TO_DEFAULT = "converts_none_to_default"  # Silently converts None to default value
    ACCEPTS_NONE = "accepts_none"  # Explicitly accepts None as valid
    REQUIRED_PARAM = "required_param"  # Parameter is required (from type hints or validation)
    OPTIONAL_PARAM = "optional_param"  # Parameter is optional (has default or Optional type)


@dataclass
class ParameterValidation:
    """Describes how a parameter is validated in the code."""
    param_name: str
    behavior: ValidationBehavior
    error_message: Optional[str] = None  # The error message if raises
    default_value: Optional[str] = None  # The default value if converts
    source_line: Optional[int] = None  # Line number where behavior is defined


@dataclass
class CodeBehavior:
    """Extracted behavior patterns from code."""
    function_name: str
    parameter_validations: list[ParameterValidation] = field(default_factory=list)
    raises_exceptions: list[str] = field(default_factory=list)  # Exception types the function can raise
    return_type: Optional[str] = None
    has_try_except: bool = False
    calls_external_client: bool = False
    client_method_called: Optional[str] = None


@dataclass
class TestAssertion:
    """Describes an assertion made in test code."""
    test_method: str
    assertion_type: str  # "raises", "equals", "is_not_none", etc.
    expected_exception: Optional[str] = None
    expected_message_pattern: Optional[str] = None
    param_tested: Optional[str] = None
    param_value: Optional[str] = None  # "None", '""', '{}', etc.
    source_line: Optional[int] = None


@dataclass
class ValidationMismatch:
    """Describes a mismatch between code behavior and test assertion."""
    severity: str  # "error", "warning"
    code_behavior: ParameterValidation
    test_assertion: TestAssertion
    description: str
    suggested_fix: str


@dataclass
class CrossValidationResult:
    """Result of cross-validating code and tests."""
    is_valid: bool
    mismatches: list[ValidationMismatch] = field(default_factory=list)
    code_behaviors: list[CodeBehavior] = field(default_factory=list)
    test_assertions: list[TestAssertion] = field(default_factory=list)
    
    @property
    def error_count(self) -> int:
        return sum(1 for m in self.mismatches if m.severity == "error")
    
    @property
    def warning_count(self) -> int:
        return sum(1 for m in self.mismatches if m.severity == "warning")


class CodeBehaviorExtractor(ast.NodeVisitor):
    """
    Extract validation behavior from flow code using AST analysis.
    
    Detects patterns like:
    - `if param is None: raise ValueError("...")`  → RAISES_ON_NONE
    - `if param is None: param = {}`  → CONVERTS_NONE_TO_DEFAULT
    - `if not param: raise ValueError("...")`  → RAISES_ON_EMPTY
    """
    
    def __init__(self):
        self.behaviors: list[CodeBehavior] = []
        self._current_function: Optional[CodeBehavior] = None
        self._current_function_params: list[str] = []
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._analyze_function(node)
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._analyze_function(node)
    
    def _analyze_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Analyze a function for validation patterns."""
        # Skip private/dunder methods
        if node.name.startswith('_'):
            self.generic_visit(node)
            return
        
        # Create behavior record
        behavior = CodeBehavior(function_name=node.name)
        self._current_function = behavior
        
        # Extract parameter names (excluding self/cls)
        self._current_function_params = [
            arg.arg for arg in node.args.args
            if arg.arg not in ('self', 'cls')
        ]
        
        # Check for Optional type hints
        for arg in node.args.args:
            if arg.arg in ('self', 'cls'):
                continue
            if arg.annotation:
                annotation_str = ast.unparse(arg.annotation) if hasattr(ast, 'unparse') else str(arg.annotation)
                if 'Optional' in annotation_str or 'None' in annotation_str:
                    behavior.parameter_validations.append(ParameterValidation(
                        param_name=arg.arg,
                        behavior=ValidationBehavior.OPTIONAL_PARAM,
                    ))
        
        # Check for default values (indicates optional)
        defaults = node.args.defaults
        num_defaults = len(defaults)
        num_args = len(node.args.args) - (1 if node.args.args and node.args.args[0].arg in ('self', 'cls') else 0)
        args_without_self = [a for a in node.args.args if a.arg not in ('self', 'cls')]
        
        for i, arg in enumerate(args_without_self):
            default_index = i - (num_args - num_defaults)
            if default_index >= 0 and default_index < num_defaults:
                # This parameter has a default
                default_val = defaults[default_index]
                if isinstance(default_val, ast.Constant) and default_val.value is None:
                    behavior.parameter_validations.append(ParameterValidation(
                        param_name=arg.arg,
                        behavior=ValidationBehavior.ACCEPTS_NONE,
                        default_value="None",
                    ))
        
        # Analyze function body for validation patterns
        for stmt in node.body:
            self._analyze_validation_pattern(stmt, behavior)
        
        # Check for try/except
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Try):
                behavior.has_try_except = True
                break
        
        # Check for external client calls
        for stmt in ast.walk(node):
            if isinstance(stmt, ast.Call):
                if isinstance(stmt.func, ast.Attribute):
                    # Check if it looks like a client method call
                    value = stmt.func.value
                    if isinstance(value, ast.Name):
                        if 'client' in value.id.lower():
                            behavior.calls_external_client = True
                            behavior.client_method_called = stmt.func.attr
        
        self.behaviors.append(behavior)
        self._current_function = None
        self.generic_visit(node)
    
    def _analyze_validation_pattern(self, stmt: ast.stmt, behavior: CodeBehavior) -> None:
        """Analyze a statement for validation patterns."""
        if not isinstance(stmt, ast.If):
            return
        
        # Pattern: if param is None: ...
        # Pattern: if not param: ...
        test = stmt.test
        
        param_name = None
        is_none_check = False
        is_falsy_check = False
        
        # Check for `if param is None:` or `if param == None:`
        if isinstance(test, ast.Compare):
            if len(test.ops) == 1 and len(test.comparators) == 1:
                if isinstance(test.ops[0], (ast.Is, ast.Eq)):
                    if isinstance(test.comparators[0], ast.Constant) and test.comparators[0].value is None:
                        if isinstance(test.left, ast.Name):
                            param_name = test.left.id
                            is_none_check = True
        
        # Check for `if not param:` (falsy check)
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            if isinstance(test.operand, ast.Name):
                param_name = test.operand.id
                is_falsy_check = True
        
        if not param_name:
            return
        
        # Analyze the body to see what happens
        if not stmt.body:
            return
        
        body_stmt = stmt.body[0]
        
        # Pattern: raise ValueError("message")
        if isinstance(body_stmt, ast.Raise):
            if body_stmt.exc and isinstance(body_stmt.exc, ast.Call):
                exc_call = body_stmt.exc
                if isinstance(exc_call.func, ast.Name):
                    exc_type = exc_call.func.id
                    behavior.raises_exceptions.append(exc_type)
                    
                    # Extract error message
                    error_msg = None
                    if exc_call.args and isinstance(exc_call.args[0], ast.Constant):
                        error_msg = str(exc_call.args[0].value)
                    
                    validation = ParameterValidation(
                        param_name=param_name,
                        behavior=ValidationBehavior.RAISES_ON_NONE if is_none_check else ValidationBehavior.RAISES_ON_EMPTY,
                        error_message=error_msg,
                        source_line=stmt.lineno,
                    )
                    behavior.parameter_validations.append(validation)
                    return
        
        # Pattern: param = {} or param = default
        if isinstance(body_stmt, ast.Assign):
            for target in body_stmt.targets:
                if isinstance(target, ast.Name) and target.id == param_name:
                    # This is an assignment to the same param
                    default_repr = "unknown"
                    if isinstance(body_stmt.value, ast.Dict):
                        default_repr = "{}"
                    elif isinstance(body_stmt.value, ast.List):
                        default_repr = "[]"
                    elif isinstance(body_stmt.value, ast.Constant):
                        default_repr = repr(body_stmt.value.value)
                    
                    validation = ParameterValidation(
                        param_name=param_name,
                        behavior=ValidationBehavior.CONVERTS_NONE_TO_DEFAULT,
                        default_value=default_repr,
                        source_line=stmt.lineno,
                    )
                    behavior.parameter_validations.append(validation)
                    return


class TestAssertionAnalyzer(ast.NodeVisitor):
    """
    Extract test assertions from test code using AST analysis.
    
    Detects patterns like:
    - `with pytest.raises(ValueError, match="..."): flow(param=None)`
    - `assert result is not None`
    - `assert result["key"] == expected`
    """
    
    def __init__(self):
        self.assertions: list[TestAssertion] = []
        self._current_test: Optional[str] = None
    
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if node.name.startswith('test_'):
            self._current_test = node.name
            self.generic_visit(node)
            self._current_test = None
        else:
            self.generic_visit(node)
    
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        if node.name.startswith('test_'):
            self._current_test = node.name
            self.generic_visit(node)
            self._current_test = None
        else:
            self.generic_visit(node)
    
    def visit_With(self, node: ast.With) -> None:
        """Analyze `with pytest.raises(...)` patterns."""
        if not self._current_test:
            self.generic_visit(node)
            return
        
        for item in node.items:
            ctx = item.context_expr
            
            # Check for pytest.raises(ExceptionType, match="...")
            if isinstance(ctx, ast.Call):
                func = ctx.func
                
                # Check for pytest.raises or self.assertRaises
                is_pytest_raises = False
                if isinstance(func, ast.Attribute):
                    if func.attr == 'raises':
                        is_pytest_raises = True
                    elif func.attr == 'assertRaises':
                        is_pytest_raises = True
                
                if is_pytest_raises and ctx.args:
                    exc_type = None
                    match_pattern = None
                    
                    # Get exception type
                    if isinstance(ctx.args[0], ast.Name):
                        exc_type = ctx.args[0].id
                    elif isinstance(ctx.args[0], ast.Attribute):
                        exc_type = ctx.args[0].attr
                    
                    # Get match pattern from kwargs
                    for kw in ctx.keywords:
                        if kw.arg == 'match' and isinstance(kw.value, ast.Constant):
                            match_pattern = str(kw.value.value)
                    
                    # Analyze the body to find what's being tested
                    param_tested = None
                    param_value = None
                    
                    # V31-002 FIX: Collect ALL parameters that are None, "", or {}
                    # We'll use the match_pattern to identify which one is being tested
                    candidate_params = []
                    
                    for stmt in node.body:
                        # Look for flow function calls
                        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                            call = stmt.value
                            for kw in call.keywords:
                                if isinstance(kw.value, ast.Constant) and kw.value.value is None:
                                    candidate_params.append((kw.arg, "None"))
                                elif isinstance(kw.value, ast.Constant) and kw.value.value == "":
                                    candidate_params.append((kw.arg, '""'))
                                # V31-002 FIX: Detect empty dict {} passed to payload
                                # This is the pattern: flow(api_key="...", payload={})
                                elif isinstance(kw.value, ast.Dict) and len(kw.value.keys) == 0:
                                    candidate_params.append((kw.arg, "{}"))
                    
                    # V31-002: Use match pattern to identify which param is being tested
                    # If match says "api_key is required", then api_key is the tested param
                    if match_pattern and candidate_params:
                        for param_name, pvalue in candidate_params:
                            if param_name in match_pattern:
                                param_tested = param_name
                                param_value = pvalue
                                break
                    
                    # Fallback: use the last candidate if match_pattern doesn't help
                    if not param_tested and candidate_params:
                        param_tested, param_value = candidate_params[-1]
                    
                    assertion = TestAssertion(
                        test_method=self._current_test,
                        assertion_type="raises",
                        expected_exception=exc_type,
                        expected_message_pattern=match_pattern,
                        param_tested=param_tested,
                        param_value=param_value,
                        source_line=node.lineno,
                    )
                    self.assertions.append(assertion)
        
        self.generic_visit(node)


def extract_code_behaviors(code: str) -> list[CodeBehavior]:
    """
    Extract validation behaviors from Python code.
    
    Args:
        code: Python source code
        
    Returns:
        List of CodeBehavior objects describing function behaviors
    """
    try:
        tree = ast.parse(code)
        extractor = CodeBehaviorExtractor()
        extractor.visit(tree)
        return extractor.behaviors
    except SyntaxError as e:
        logger.warning(f"Could not parse code for behavior extraction: {e}")
        return []


def extract_test_assertions(test_code: str) -> list[TestAssertion]:
    """
    Extract assertions from Python test code.
    
    Args:
        test_code: Python test source code
        
    Returns:
        List of TestAssertion objects describing test assertions
    """
    try:
        tree = ast.parse(test_code)
        analyzer = TestAssertionAnalyzer()
        analyzer.visit(tree)
        return analyzer.assertions
    except SyntaxError as e:
        logger.warning(f"Could not parse test code for assertion extraction: {e}")
        return []


def cross_validate_code_and_tests(
    flow_code: str,
    test_code: str,
    flow_function_name: Optional[str] = None,
) -> CrossValidationResult:
    """
    Cross-validate that test assertions match actual code behavior.
    
    This is the core fix for V25-001: ensures tests don't assert behavior
    that the code doesn't implement.
    
    Args:
        flow_code: The generated flow code
        test_code: The generated test code
        flow_function_name: Optional name of the flow function to focus on
        
    Returns:
        CrossValidationResult with any mismatches found
    """
    result = CrossValidationResult(is_valid=True)
    
    # Extract behaviors from flow code
    code_behaviors = extract_code_behaviors(flow_code)
    result.code_behaviors = code_behaviors
    
    # Extract assertions from test code
    test_assertions = extract_test_assertions(test_code)
    result.test_assertions = test_assertions
    
    # Find the primary flow function behavior
    flow_behavior = None
    for behavior in code_behaviors:
        if flow_function_name and behavior.function_name == flow_function_name:
            flow_behavior = behavior
            break
        elif not flow_function_name and '_flow' in behavior.function_name.lower():
            flow_behavior = behavior
            break
    
    if not flow_behavior:
        logger.debug("No flow function found in code behaviors, skipping cross-validation")
        return result
    
    # Check each test assertion against code behavior
    for assertion in test_assertions:
        if assertion.assertion_type != "raises":
            continue
        
        # Look for matching parameter validation in code
        param_validation = None
        for pv in flow_behavior.parameter_validations:
            if pv.param_name == assertion.param_tested:
                param_validation = pv
                break
        
        if not param_validation:
            # Test asserts exception for param, but code doesn't validate it
            result.is_valid = False
            result.mismatches.append(ValidationMismatch(
                severity="error",
                code_behavior=ParameterValidation(
                    param_name=assertion.param_tested or "unknown",
                    behavior=ValidationBehavior.ACCEPTS_NONE,
                ),
                test_assertion=assertion,
                description=(
                    f"Test '{assertion.test_method}' expects {assertion.expected_exception} "
                    f"when {assertion.param_tested}={assertion.param_value}, but code does not "
                    f"validate this parameter."
                ),
                suggested_fix=(
                    f"Either add validation to flow: "
                    f"`if {assertion.param_tested} is None: raise ValueError(\"{assertion.param_tested} is required\")` "
                    f"OR change test to expect code's actual behavior (accepts {assertion.param_value})."
                ),
            ))
            continue
        
        # Check if the behavior matches the assertion
        if assertion.param_value == "None":
            if param_validation.behavior == ValidationBehavior.CONVERTS_NONE_TO_DEFAULT:
                # Test expects exception, but code converts to default
                result.is_valid = False
                result.mismatches.append(ValidationMismatch(
                    severity="error",
                    code_behavior=param_validation,
                    test_assertion=assertion,
                    description=(
                        f"Test '{assertion.test_method}' expects {assertion.expected_exception} "
                        f"when {assertion.param_tested}=None, but code converts None to "
                        f"{param_validation.default_value} instead of raising."
                    ),
                    suggested_fix=(
                        f"Fix the flow code line {param_validation.source_line}: "
                        f"change `if {assertion.param_tested} is None: {assertion.param_tested} = {param_validation.default_value}` "
                        f"to `if {assertion.param_tested} is None: raise ValueError(\"{assertion.expected_message_pattern or f'{assertion.param_tested} is required'}\")`"
                    ),
                ))
            elif param_validation.behavior == ValidationBehavior.ACCEPTS_NONE:
                # Test expects exception, but code explicitly accepts None
                result.is_valid = False
                result.mismatches.append(ValidationMismatch(
                    severity="error",
                    code_behavior=param_validation,
                    test_assertion=assertion,
                    description=(
                        f"Test '{assertion.test_method}' expects {assertion.expected_exception} "
                        f"when {assertion.param_tested}=None, but code has Optional type hint "
                        f"or default=None, meaning None is explicitly accepted."
                    ),
                    suggested_fix=(
                        f"Change test to not expect exception for {assertion.param_tested}=None, "
                        f"since the code signature indicates None is a valid value."
                    ),
                ))
        
        # V31-002 FIX: Handle empty dict {} case (used in test_missing_payload)
        # If code has `if not param: raise` (RAISES_ON_EMPTY), it raises for {} too
        # If code doesn't have this, then test with payload={} will fail
        elif assertion.param_value == "{}":
            if param_validation.behavior not in (ValidationBehavior.RAISES_ON_EMPTY, ValidationBehavior.RAISES_ON_NONE):
                # Test expects exception for empty dict, but code doesn't validate emptiness
                result.is_valid = False
                result.mismatches.append(ValidationMismatch(
                    severity="error",
                    code_behavior=param_validation,
                    test_assertion=assertion,
                    description=(
                        f"Test '{assertion.test_method}' expects {assertion.expected_exception} "
                        f"when {assertion.param_tested}={{}}, but code does not validate "
                        f"for empty values (behavior: {param_validation.behavior.value})."
                    ),
                    suggested_fix=(
                        f"Either add falsy check to flow: "
                        f"`if not {assertion.param_tested}: raise ValueError(\"{assertion.param_tested} is required\")` "
                        f"OR remove test_missing_payload as code accepts empty dict."
                    ),
                ))
            # If behavior IS RAISES_ON_EMPTY, the test and code are aligned - no mismatch
    
    return result


def generate_test_with_code_context(
    flow_code: str,
    test_template: str,
    flow_function_name: str,
    client_class: str,
    flow_import_module: str,
) -> str:
    """
    Generate test code that is consistent with actual flow code behavior.
    
    This is the primary fix for V25-001: instead of generating tests from
    a template/skeleton, we analyze the actual flow code and generate tests
    that match its behavior.
    
    Args:
        flow_code: The actual generated flow code
        test_template: The test template/skeleton to modify
        flow_function_name: Name of the flow function
        client_class: Client class name for mocking
        flow_import_module: Import path for the flow
        
    Returns:
        Test code consistent with flow behavior
    """
    # Extract behaviors from flow code
    behaviors = extract_code_behaviors(flow_code)
    
    # Find the flow function behavior
    flow_behavior = None
    for behavior in behaviors:
        if behavior.function_name == flow_function_name:
            flow_behavior = behavior
            break
        elif '_flow' in behavior.function_name.lower():
            flow_behavior = behavior
            break
    
    if not flow_behavior:
        logger.warning("Could not extract flow behavior, using template as-is")
        return test_template
    
    # Build validation test cases based on actual behavior
    validation_tests = []
    
    for pv in flow_behavior.parameter_validations:
        if pv.behavior == ValidationBehavior.RAISES_ON_NONE:
            # Code raises on None - test should expect raises
            validation_tests.append({
                "param": pv.param_name,
                "value": "None",
                "expects_raise": True,
                "exception": "ValueError",
                "message_pattern": pv.error_message or f"{pv.param_name} is required",
            })
        elif pv.behavior == ValidationBehavior.RAISES_ON_EMPTY:
            # Code raises on empty - test should expect raises
            validation_tests.append({
                "param": pv.param_name,
                "value": '""',
                "expects_raise": True,
                "exception": "ValueError",
                "message_pattern": pv.error_message or f"{pv.param_name} is required",
            })
        elif pv.behavior == ValidationBehavior.CONVERTS_NONE_TO_DEFAULT:
            # Code converts None to default - test should NOT expect raises
            validation_tests.append({
                "param": pv.param_name,
                "value": "None",
                "expects_raise": False,
                "note": f"Code converts None to {pv.default_value}",
            })
    
    # Modify test template to match actual behavior
    modified_test = test_template
    
    # Remove tests that expect raises for params that don't raise
    for vt in validation_tests:
        if not vt.get("expects_raise"):
            # Find and remove/modify the test that expects raises for this param
            pattern = rf'def test_[^(]*missing_{vt["param"]}[^(]*\([^)]*\):.*?(?=\n\s*def |\Z)'
            modified_test = re.sub(pattern, '', modified_test, flags=re.DOTALL)
            
            # Also look for tests with "payload" or param name in them that use pytest.raises
            pattern2 = rf'with pytest\.raises\([^)]*\):[^\n]*\n[^\n]*{vt["param"]}\s*=\s*None'
            if re.search(pattern2, modified_test):
                logger.info(
                    f"[code_test_validator] Removing pytest.raises assertion for "
                    f"{vt['param']}=None (code converts to {vt.get('note', 'default')})"
                )
    
    return modified_test


def repair_test_with_code_context(
    test_code: str,
    flow_code: str,
    pytest_errors: list[dict[str, str]],
    flow_function_name: Optional[str] = None,
) -> tuple[str, list[str]]:
    """
    Repair test code using knowledge of actual flow code behavior.
    
    This is the primary fix for V25-002: the repair mechanism now has
    full context of what the flow code actually does.
    
    V26-004 ENHANCEMENT: Now enforces corrections, not just detects.
    Uses multiple strategies: AST rewriting, regex patterns, and line-level removal.
    
    Args:
        test_code: The failing test code
        flow_code: The actual flow code being tested
        pytest_errors: List of pytest errors
        flow_function_name: Name of the flow function
        
    Returns:
        Tuple of (repaired_test_code, list_of_changes_made)
    """
    changes = []
    repaired = test_code
    
    # Cross-validate to find mismatches
    validation_result = cross_validate_code_and_tests(
        flow_code=flow_code,
        test_code=test_code,
        flow_function_name=flow_function_name,
    )
    
    if validation_result.is_valid:
        logger.debug("Cross-validation passed, no semantic repairs needed")
        return test_code, []
    
    # V26-004: Enforce corrections for each mismatch
    for mismatch in validation_result.mismatches:
        if mismatch.severity != "error":
            continue
        
        assertion = mismatch.test_assertion
        code_behavior = mismatch.code_behavior
        
        # Handle the case where test expects raises but code doesn't raise
        if assertion.assertion_type == "raises":
            # V31-002 FIX: Handle both CONVERTS_NONE_TO_DEFAULT and cases where
            # code simply doesn't validate the parameter at all (ACCEPTS_NONE, etc.)
            should_remove_test = (
                code_behavior.behavior == ValidationBehavior.CONVERTS_NONE_TO_DEFAULT or
                code_behavior.behavior == ValidationBehavior.ACCEPTS_NONE or
                # V31-002: Also handle when test expects {} to raise but code doesn't check emptiness
                (assertion.param_value == "{}" and code_behavior.behavior != ValidationBehavior.RAISES_ON_EMPTY)
            )
            
            if should_remove_test:
                test_method = assertion.test_method
                param_name = assertion.param_tested or "payload"
                param_value = assertion.param_value or "None"
                
                logger.info(
                    f"[V31-002] Enforcing fix: test {test_method} expects ValueError "
                    f"but code doesn't validate {param_name}={param_value} (behavior: {code_behavior.behavior.value})"
                )
                
                # V26-004: Strategy 1 - Remove entire test method using AST
                repaired, method_removed = _remove_test_method_ast(repaired, test_method)
                if method_removed:
                    changes.append(f"V31-002: Removed test method '{test_method}' (code accepts {param_value})")
                    continue
                
                # V26-004: Strategy 2 - Remove pytest.raises block for this param
                repaired, block_removed = _remove_pytest_raises_block(
                    repaired, param_name, code_behavior.default_value or ""
                )
                if block_removed:
                    changes.append(
                        f"V31-002: Removed pytest.raises block for {param_name}={param_value}"
                    )
                    continue
                
                # V26-004: Strategy 3 - Comment out the problematic test
                repaired, commented = _comment_out_raises_test(repaired, test_method)
                if commented:
                    changes.append(
                        f"V31-002: Commented out {test_method} (mismatched ValueError expectation)"
                    )
                    continue
                
                # V31-002: Strategy 4 - Line-level removal of pytest.raises
                # Extended to handle both =None and ={} patterns
                lines = repaired.split('\n')
                new_lines = []
                in_raises_block = False
                block_indent = 0
                skipped_lines = 0
                
                for i, line in enumerate(lines):
                    stripped = line.strip()
                    
                    # Detect start of pytest.raises block with our param
                    if f'pytest.raises' in line and not in_raises_block:
                        # Look ahead for our param with None or {}
                        lookahead = '\n'.join(lines[i:min(i+10, len(lines))])
                        matches_none = f'{param_name}=None' in lookahead or f'{param_name}= None' in lookahead
                        matches_empty = f'{param_name}={{}}' in lookahead or f'{param_name}= {{}}' in lookahead
                        
                        if matches_none or matches_empty:
                            in_raises_block = True
                            block_indent = len(line) - len(line.lstrip())
                            skipped_lines += 1
                            continue
                    
                    if in_raises_block:
                        current_indent = len(line) - len(line.lstrip()) if stripped else block_indent + 4
                        # Exit block when we return to original or lesser indent
                        if stripped and current_indent <= block_indent and not line.strip().startswith('#'):
                            in_raises_block = False
                            new_lines.append(line)
                        else:
                            skipped_lines += 1
                            continue
                    else:
                        new_lines.append(line)
                
                if skipped_lines > 0:
                    repaired = '\n'.join(new_lines)
                    changes.append(
                        f"V31-002: Removed {skipped_lines} lines with pytest.raises({param_name}={param_value})"
                    )
    
    return repaired, changes


def _remove_test_method_ast(code: str, method_name: str) -> tuple[str, bool]:
    """
    Remove a test method from code using AST manipulation.
    
    V26-004: AST-based removal is more reliable than regex for complex test methods.
    
    Returns:
        Tuple of (modified_code, was_removed)
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, False
    
    # Find the test method and get its line range
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            start_line = node.lineno
            end_line = node.end_lineno if hasattr(node, 'end_lineno') else None
            
            if end_line is None:
                return code, False
            
            lines = code.split('\n')
            # Remove the method lines (convert to 0-indexed)
            new_lines = lines[:start_line-1] + lines[end_line:]
            
            # Clean up any consecutive blank lines
            cleaned_lines = []
            prev_blank = False
            for line in new_lines:
                is_blank = not line.strip()
                if is_blank and prev_blank:
                    continue
                cleaned_lines.append(line)
                prev_blank = is_blank
            
            return '\n'.join(cleaned_lines), True
    
    return code, False


def _remove_pytest_raises_block(code: str, param_name: str, default_value: str) -> tuple[str, bool]:
    """
    Remove pytest.raises blocks that test for param=None when code accepts None.
    
    V26-004: Handles various patterns of pytest.raises blocks.
    
    Returns:
        Tuple of (modified_code, was_removed)
    """
    # Pattern 1: with pytest.raises(...): followed by call with param=None
    pattern1 = re.compile(
        rf'(\s*)with\s+pytest\.raises\([^)]*\):\s*\n'
        rf'\s+[^\n]*{re.escape(param_name)}\s*=\s*None[^\n]*\n?',
        re.MULTILINE
    )
    
    new_code, count1 = pattern1.subn(r'\n\1# V26-004: Removed - code accepts None\n', code)
    
    # Pattern 2: with pytest.raises(..., match="...param..."):
    pattern2 = re.compile(
        rf'(\s*)with\s+pytest\.raises\([^)]*match\s*=\s*["\'][^"\']*{re.escape(param_name)}[^"\']*["\']\s*[^)]*\):\s*\n'
        rf'(\s+[^\n]+\n)*?',
        re.MULTILINE
    )
    
    new_code, count2 = pattern2.subn(r'\1# V26-004: Removed - code accepts None\n', new_code)
    
    return new_code, (count1 + count2) > 0


def _comment_out_raises_test(code: str, method_name: str) -> tuple[str, bool]:
    """
    Comment out a test method instead of removing it.
    
    V26-004: Safer fallback - preserves the original test for reference.
    
    Returns:
        Tuple of (modified_code, was_commented)
    """
    # Find the method definition
    pattern = re.compile(
        rf'(\n[ \t]*)def {re.escape(method_name)}\([^)]*\):',
        re.MULTILINE
    )
    
    match = pattern.search(code)
    if not match:
        return code, False
    
    start_pos = match.start()
    indent = match.group(1)
    
    # Find the end of the method (next method def or class end)
    rest_of_code = code[match.end():]
    next_def = re.search(r'\n[ \t]*def ', rest_of_code)
    
    if next_def:
        end_pos = match.end() + next_def.start()
    else:
        # Find end of class or file
        end_pos = len(code)
    
    # Extract and comment out the method
    method_code = code[start_pos:end_pos]
    commented = '\n'.join([
        f'{indent}# V26-004 SKIP: Test expects ValueError but code accepts None',
        *[f'{indent}# {line}' if line.strip() else line for line in method_code.split('\n')]
    ])
    
    new_code = code[:start_pos] + commented + code[end_pos:]
    return new_code, True


def build_repair_prompt_with_context(
    test_code: str,
    flow_code: str,
    pytest_errors: list[dict[str, str]],
) -> str:
    """
    Build an LLM repair prompt that includes flow code context.
    
    This gives the LLM full visibility into what the code actually does,
    enabling it to make semantically correct repairs.
    
    Args:
        test_code: The failing test code
        flow_code: The actual flow code being tested
        pytest_errors: List of pytest errors
        
    Returns:
        A detailed prompt for LLM repair
    """
    # Extract behaviors for context
    behaviors = extract_code_behaviors(flow_code)
    
    behavior_summary = []
    for b in behaviors:
        if '_flow' in b.function_name.lower():
            behavior_summary.append(f"Function: {b.function_name}")
            for pv in b.parameter_validations:
                if pv.behavior == ValidationBehavior.RAISES_ON_NONE:
                    behavior_summary.append(
                        f"  - {pv.param_name}: RAISES ValueError when None "
                        f"(message: \"{pv.error_message}\")"
                    )
                elif pv.behavior == ValidationBehavior.CONVERTS_NONE_TO_DEFAULT:
                    behavior_summary.append(
                        f"  - {pv.param_name}: CONVERTS None to {pv.default_value} "
                        f"(does NOT raise)"
                    )
                elif pv.behavior == ValidationBehavior.ACCEPTS_NONE:
                    behavior_summary.append(
                        f"  - {pv.param_name}: ACCEPTS None as valid (Optional parameter)"
                    )
    
    error_summary = "\n".join([
        f"- {e['error_type']}: {e['error_detail']} in {e['test_name']}"
        for e in pytest_errors[:5]
    ])
    
    prompt = f"""Fix the failing tests based on the ACTUAL FLOW CODE behavior.

## PYTEST ERRORS:
{error_summary}

## ACTUAL FLOW CODE (this is what the tests MUST match):
```python
{flow_code}
```

## EXTRACTED FLOW BEHAVIOR:
{chr(10).join(behavior_summary)}

## FAILING TEST CODE:
```python
{test_code}
```

## CRITICAL RULES:
1. Tests MUST match the actual behavior of the flow code shown above
2. If the flow converts None to a default value (e.g., `if payload is None: payload = {{}}`),
   then tests MUST NOT expect ValueError for payload=None
3. If the flow raises ValueError for a parameter, tests SHOULD expect that ValueError
4. Do NOT invent validation that doesn't exist in the flow code
5. Every test method must mock the client class before calling the flow

## EXAMPLE FIX:
If the flow has:
```python
if payload is None:
    payload = {{}}  # Converts to empty dict, does NOT raise
```

Then this test is WRONG:
```python
with pytest.raises(ValueError, match="payload is required"):
    flow(api_key="test", payload=None)
```

And should be changed to:
```python
def test_flow_accepts_none_payload(self):
    with patch('module.Client') as MockClient:
        mock_client = MockClient.return_value
        mock_client.method.return_value = {{"data": []}}
        result = flow(api_key="test", payload=None)
        assert result["success"] is True
```

Return the complete fixed test code:"""
    
    return prompt


def extract_flow_parameters(flow_code: str, flow_function_name: Optional[str] = None) -> list[str]:
    """
    V27-005: Extract parameter names from a flow function.
    
    This helps validate that test templates use the actual parameters
    defined in the flow function, not hardcoded assumptions.
    
    Args:
        flow_code: The generated flow code
        flow_function_name: Name of the flow function to extract params from
        
    Returns:
        List of parameter names (excluding 'self', 'cls')
    """
    try:
        tree = ast.parse(flow_code)
    except SyntaxError:
        logger.warning("V27-005: Could not parse flow code for parameter extraction")
        return []
    
    params = []
    
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # If specific function name given, match it; otherwise find flow functions
            if flow_function_name and node.name != flow_function_name:
                continue
            elif not flow_function_name and '_flow' not in node.name.lower():
                continue
            
            # Extract parameter names (excluding self/cls)
            for arg in node.args.args:
                if arg.arg not in ('self', 'cls'):
                    params.append(arg.arg)
            
            if flow_function_name:
                break  # Found the specific function
    
    return params


def fix_test_parameter_mismatches(
    test_code: str,
    flow_code: str,
    flow_function_name: Optional[str] = None,
) -> tuple[str, bool, list[str]]:
    """
    V27-005: Fix tests that use wrong parameter names.
    
    The test template may use hardcoded 'api_key' and 'payload', but the
    actual flow function may have different parameters. This function
    removes or comments out tests that reference non-existent parameters.
    
    Args:
        test_code: The generated test code
        flow_code: The generated flow code  
        flow_function_name: Name of the flow function
        
    Returns:
        Tuple of (fixed_test_code, was_modified, list_of_changes)
    """
    changes = []
    fixed = test_code
    
    # Extract actual flow parameters
    actual_params = extract_flow_parameters(flow_code, flow_function_name)
    
    if not actual_params:
        logger.debug("V27-005: No parameters extracted from flow, skipping mismatch fix")
        return test_code, False, []
    
    actual_param_set = set(actual_params)
    
    # Common hardcoded parameter names that might not exist
    template_params = {'api_key', 'payload', 'params', 'data', 'body'}
    
    # Find parameters used in tests that don't exist in flow
    missing_params = []
    for param in template_params:
        # Check if param is used in a validation test but doesn't exist
        if f'test_{flow_function_name or ""}' in test_code or f'test_' in test_code:
            if param not in actual_param_set and f'{param}=' in test_code:
                missing_params.append(param)
    
    if not missing_params:
        return test_code, False, []
    
    # Remove tests for missing parameters
    for param in missing_params:
        # Pattern: test methods that reference the missing parameter
        # E.g., test_flow_missing_api_key, test_flow_missing_payload
        patterns = [
            rf'def test_[a-z_]*missing_{param}[a-z_]*\(.*?\):\s*""".*?""".*?(?=\n    def |\n\nclass |\Z)',
            rf'def test_[a-z_]*{param}[a-z_]*\(.*?\):\s*""".*?""".*?pytest\.raises.*?{param}\s*=.*?(?=\n    def |\n\nclass |\Z)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, fixed, re.DOTALL)
            if match:
                # Comment out the test instead of removing
                method_code = match.group(0)
                commented = re.sub(r'^', '    # V27-005 SKIP: Parameter not in flow: ', method_code, flags=re.MULTILINE)
                fixed = fixed.replace(method_code, f"\n    # V27-005: Test removed - '{param}' parameter not in flow signature\n")
                changes.append(f"V27-005: Removed test for non-existent parameter '{param}'")
    
    was_modified = fixed != test_code
    return fixed, was_modified, changes


def extract_client_method_from_flow(flow_code: str) -> Optional[str]:
    """
    V29-001 Fix: Extract the actual client method name used in flow code.
    
    When LLM generates flow code, it may use a different method name than what
    was specified in the CodegenContext. This function extracts the actual
    method name that the flow code calls on the client.
    
    Detection patterns:
    - client.method_name(...)  -> method_name
    - self._client.method_name(...) -> method_name
    - self.client.method_name(...) -> method_name
    
    Args:
        flow_code: The generated flow code
        
    Returns:
        The actual client method name used, or None if not found
    """
    try:
        tree = ast.parse(flow_code)
    except SyntaxError:
        logger.warning("V29-001: Could not parse flow code for client method extraction")
        return None
    
    # Look for client method calls
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            value = node.func.value
            
            # Pattern: client.method() or variable_client.method()
            if isinstance(value, ast.Name):
                var_name = value.id.lower()
                if 'client' in var_name:
                    logger.debug(f"V29-001: Found client method call: {var_name}.{method_name}()")
                    return method_name
            
            # Pattern: self._client.method() or self.client.method()
            elif isinstance(value, ast.Attribute):
                if isinstance(value.value, ast.Name) and value.value.id == 'self':
                    attr_name = value.attr.lower()
                    if 'client' in attr_name:
                        logger.debug(f"V29-001: Found client method call: self.{value.attr}.{method_name}()")
                        return method_name
    
    return None


def align_test_mock_with_flow(
    test_code: str,
    flow_code: str,
    expected_method_name: str,
) -> tuple[str, bool, list[str]]:
    """
    V29-001 Fix: Align test mock method names with actual flow code behavior.
    
    When tests are generated, they may use the expected method name from
    CodegenContext (e.g., "create_issue"). But if the LLM generated flow code
    uses a different method (e.g., "issues_create"), the tests will fail.
    
    This function:
    1. Extracts the actual client method from flow code
    2. Replaces mock method references in tests to match
    
    Args:
        test_code: The generated test code
        flow_code: The generated flow code  
        expected_method_name: The method name tests currently expect
        
    Returns:
        Tuple of (fixed_test_code, was_modified, list_of_changes)
    """
    changes = []
    fixed = test_code
    
    # Extract actual method from flow
    actual_method = extract_client_method_from_flow(flow_code)
    
    if not actual_method:
        logger.debug("V29-001: No client method found in flow code, skipping alignment")
        return test_code, False, []
    
    if actual_method == expected_method_name:
        logger.debug(f"V29-001: Method names already aligned: {actual_method}")
        return test_code, False, []
    
    logger.info(
        f"V29-001: Aligning test mocks - expected '{expected_method_name}' "
        f"but flow uses '{actual_method}'"
    )
    
    # Patterns to replace in test code:
    # - mock_client.expected_method.return_value -> mock_client.actual_method.return_value
    # - mock_client.expected_method.called -> mock_client.actual_method.called
    # - mock_client.expected_method.call_count -> mock_client.actual_method.call_count
    # - mock_client.expected_method.assert_called -> mock_client.actual_method.assert_called
    
    patterns = [
        # Mock return value setup
        (rf'mock_client\.{re.escape(expected_method_name)}\.return_value',
         f'mock_client.{actual_method}.return_value'),
        # Mock called checks
        (rf'mock_client\.{re.escape(expected_method_name)}\.called',
         f'mock_client.{actual_method}.called'),
        # Mock call count
        (rf'mock_client\.{re.escape(expected_method_name)}\.call_count',
         f'mock_client.{actual_method}.call_count'),
        # Mock assert_called variants
        (rf'mock_client\.{re.escape(expected_method_name)}\.assert_called',
         f'mock_client.{actual_method}.assert_called'),
        # Generic mock method access (for other assertions)
        (rf'mock_client\.{re.escape(expected_method_name)}([^\w])',
         rf'mock_client.{actual_method}\1'),
    ]
    
    for pattern, replacement in patterns:
        if re.search(pattern, fixed):
            fixed = re.sub(pattern, replacement, fixed)
            changes.append(
                f"V29-001: Replaced mock_client.{expected_method_name} "
                f"with mock_client.{actual_method}"
            )
    
    # Remove duplicate changes
    changes = list(dict.fromkeys(changes))
    
    was_modified = fixed != test_code
    if was_modified:
        logger.info(f"V29-001: Fixed {len(changes)} mock method references in test code")
    
    return fixed, was_modified, changes


# =============================================================================
# V42-006: Extract Correct Mock Patch Path from Flow Code
# =============================================================================

def extract_client_import_info(flow_code: str) -> Optional[dict]:
    """
    V42-006 Fix: Extract client import information from flow code.
    
    When tests need to patch the client class, they must patch it where
    it's LOOKED UP (the flow module namespace), not where it's DEFINED
    (the client module). But we need to know:
    1. What class name is imported
    2. Whether it's renamed (aliased)
    3. The source module (for fallback)
    
    Detection patterns:
    - `from X.Y import ClientClass` -> imported as 'ClientClass' in flow namespace
    - `from X.Y import ClientClass as Alias` -> imported as 'Alias' in flow namespace
    - `import X.Y` -> client accessed as X.Y.ClientClass
    
    Args:
        flow_code: The generated flow code
        
    Returns:
        Dict with keys:
        - 'class_name': Name in flow namespace to patch
        - 'source_module': Original module (for reference)
        - 'import_style': 'from' or 'import'
        - 'alias': Original name if aliased, None otherwise
    """
    try:
        tree = ast.parse(flow_code)
    except SyntaxError:
        logger.warning("V42-006: Could not parse flow code for import extraction")
        return None
    
    # Look for import statements that import a client class
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            # Pattern: from X.Y import ClientClass [as Alias]
            for alias in node.names:
                name_lower = alias.name.lower()
                # Match client class patterns
                if 'client' in name_lower or name_lower.endswith('api'):
                    local_name = alias.asname if alias.asname else alias.name
                    logger.debug(
                        f"V42-006: Found client import: from {node.module} import {alias.name}"
                        + (f" as {alias.asname}" if alias.asname else "")
                    )
                    return {
                        'class_name': local_name,  # This is what to patch in flow namespace
                        'source_module': node.module,
                        'import_style': 'from',
                        'alias': alias.name if alias.asname else None,
                        'original_name': alias.name,
                    }
        
        elif isinstance(node, ast.Import):
            # Pattern: import X.Y.ClientModule
            for alias in node.names:
                name_parts = alias.name.split('.')
                if any('client' in part.lower() for part in name_parts):
                    local_name = alias.asname if alias.asname else alias.name
                    logger.debug(f"V42-006: Found module import: import {alias.name}")
                    return {
                        'class_name': local_name,  # Module name for attribute access
                        'source_module': alias.name,
                        'import_style': 'import',
                        'alias': alias.name if alias.asname else None,
                        'original_name': alias.name,
                    }
    
    return None


def fix_test_mock_patch_path(
    test_code: str,
    flow_code: str,
    flow_module_path: str,
) -> tuple[str, bool, list[str]]:
    """
    V42-006 Fix: Correct mock patch paths in tests to match actual flow imports.
    
    Problem: Tests patch 'flow_module.ClientClass' but this only works if
    the flow imports the client with `from X import ClientClass`. If the
    client is imported differently, the patch fails with AttributeError.
    
    Solution: Extract how the client is actually imported in the flow code
    and ensure the patch path matches the name bound in the flow's namespace.
    
    Args:
        test_code: Generated test code
        flow_code: Generated flow code  
        flow_module_path: The import path of the flow module (e.g., 'integrations.flows.openai_flow')
        
    Returns:
        Tuple of (fixed_test_code, was_modified, list_of_changes)
    """
    changes = []
    fixed = test_code
    
    # Extract client import info from flow
    import_info = extract_client_import_info(flow_code)
    
    if not import_info:
        logger.debug("V42-006: No client import found in flow code")
        return test_code, False, []
    
    class_name = import_info['class_name']
    original_name = import_info.get('original_name', class_name)
    source_module = import_info['source_module']
    
    # Build the correct patch path
    # When using `from X import Y`, patch at the location where Y is used
    correct_patch_path = f"{flow_module_path}.{class_name}"
    
    # Find existing patch statements and fix them
    # Pattern: patch('some.module.ClientName')
    patch_pattern = r"patch\s*\(\s*['\"]([^'\"]+)['\"]"
    
    def fix_patch(match):
        current_path = match.group(1)
        current_parts = current_path.rsplit('.', 1)
        
        if len(current_parts) != 2:
            return match.group(0)
        
        current_module, current_class = current_parts
        
        # Check if this is a client class patch that needs fixing
        if 'client' in current_class.lower() or current_class == original_name:
            # Only fix if the module path is wrong
            expected_module = flow_module_path
            if current_module != expected_module:
                new_path = f"{expected_module}.{class_name}"
                changes.append(
                    f"V42-006: Fixed patch path '{current_path}' -> '{new_path}'"
                )
                logger.info(f"V42-006: Correcting patch path: {current_path} -> {new_path}")
                return f"patch('{new_path}'"
        
        return match.group(0)
    
    fixed = re.sub(patch_pattern, fix_patch, fixed)
    
    # Also check for incorrect patch paths where the class name doesn't match
    # Pattern: patch('correct.module.WrongClassName')
    if import_info.get('alias'):
        # If there's an alias, we need to patch the aliased name
        wrong_class_pattern = rf"patch\s*\(\s*['\"]({re.escape(flow_module_path)})\.({re.escape(original_name)})['\"]"
        if re.search(wrong_class_pattern, fixed):
            replacement = rf"patch('{flow_module_path}.{class_name}'"
            fixed = re.sub(wrong_class_pattern, replacement, fixed)
            changes.append(
                f"V42-006: Fixed aliased class name '{original_name}' -> '{class_name}'"
            )
    
    was_modified = fixed != test_code
    if was_modified:
        logger.info(f"V42-006: Fixed {len(changes)} mock patch paths in test code")
    
    return fixed, was_modified, changes


# =============================================================================
# V33-003: Fix Required Parameter Validation in Tests
# =============================================================================

def _extract_required_params_from_flow(flow_code: str) -> list[tuple[str, str]]:
    """
    Extract required parameter names and their expected sources from flow code.
    
    Looks for patterns like:
    - `if "owner" not in payload: raise ValueError("owner is required")`
    - `if payload.get("list_id") is None: raise ValueError(...)`
    - `owner = payload.get("owner") or kwargs.get("owner")`
    
    Returns:
        List of (param_name, default_value) tuples
    """
    params = []
    
    # Pattern 1: if "key" not in payload: raise ValueError
    for match in re.finditer(
        r'if\s+["\'](\w+)["\']\s+not\s+in\s+payload.*?raise\s+ValueError',
        flow_code,
        re.DOTALL
    ):
        param_name = match.group(1)
        if param_name not in ('api_key', 'payload'):  # Exclude standard params
            params.append((param_name, f"test_{param_name}"))
    
    # Pattern 2: raise ValueError("param_name is required")
    for match in re.finditer(
        r'raise\s+ValueError\s*\(\s*["\'](\w+)\s+is\s+required',
        flow_code
    ):
        param_name = match.group(1)
        if param_name not in ('api_key', 'payload'):
            params.append((param_name, f"test_{param_name}"))
    
    # Pattern 3: raise ValueError(f"{field} is required") after for field in [...]
    for match in re.finditer(
        r'for\s+\w+\s+in\s+\[([^\]]+)\].*?raise\s+ValueError',
        flow_code,
        re.DOTALL
    ):
        fields_str = match.group(1)
        for field_match in re.finditer(r'["\'](\w+)["\']', fields_str):
            param_name = field_match.group(1)
            if param_name not in ('api_key', 'payload'):
                params.append((param_name, f"test_{param_name}"))
    
    # Remove duplicates while preserving order
    seen = set()
    unique_params = []
    for p in params:
        if p[0] not in seen:
            seen.add(p[0])
            unique_params.append(p)
    
    return unique_params


def fix_test_required_parameters(
    test_code: str,
    flow_code: str,
) -> tuple[str, bool, list[str]]:
    """
    V33-003 Fix: Ensure tests provide required parameters that flow validates.
    
    When flow code validates required parameters like `owner`, `list_id`, etc.,
    the tests must provide valid values in their payloads. Otherwise, the tests
    fail with ValueError before reaching the mocked client.
    
    This function:
    1. Extracts required parameters from flow code validation logic
    2. Finds test payloads that are missing these parameters
    3. Injects sensible default values into the payloads
    
    Args:
        test_code: Generated test code
        flow_code: Generated flow code
        
    Returns:
        Tuple of (fixed_test_code, was_modified, list_of_changes)
    """
    changes = []
    fixed = test_code
    
    # Extract required parameters from flow
    required_params = _extract_required_params_from_flow(flow_code)
    
    if not required_params:
        return test_code, False, []
    
    logger.debug(f"V33-003: Found required params in flow: {[p[0] for p in required_params]}")
    
    # Find test payloads and inject required params
    # Pattern: payload={"key": "value"}
    # Needs to become: payload={"key": "value", "owner": "test_owner", ...}
    
    def inject_params(match: re.Match) -> str:
        """Inject required params into a payload dict literal."""
        original = match.group(0)
        
        # Check if it already has the required params
        has_all = all(
            f'"{p[0]}"' in original or f"'{p[0]}'" in original
            for p in required_params
        )
        if has_all:
            return original
        
        # Extract existing content inside braces
        inner_match = re.search(r'payload\s*=\s*\{([^}]*)\}', original)
        if not inner_match:
            return original
        
        existing_content = inner_match.group(1).strip()
        
        # Build new params to add
        new_params = []
        for param_name, default_val in required_params:
            if f'"{param_name}"' not in original and f"'{param_name}'" not in original:
                new_params.append(f'"{param_name}": "{default_val}"')
        
        if not new_params:
            return original
        
        # Construct new payload
        if existing_content:
            new_content = existing_content.rstrip(',') + ', ' + ', '.join(new_params)
        else:
            new_content = ', '.join(new_params)
        
        # Build list of added params for logging
        added_params = [p[0] for p in required_params if f'"{p[0]}"' not in original]
        changes.append(f"V33-003: Added required params {added_params} to test payload")
        
        return f'payload={{{new_content}}}'
    
    # Apply to all payload dict literals in test code
    fixed = re.sub(r'payload\s*=\s*\{[^}]*\}', inject_params, fixed)
    
    # Also handle payloads passed as positional arguments
    # Pattern: flow_function(api_key, {"key": "value"})
    # This is less common but let's handle it
    
    was_modified = fixed != test_code
    
    if was_modified:
        # Remove duplicate change messages
        changes = list(dict.fromkeys(changes))
        logger.info(f"V33-003: Fixed {len(changes)} test payload(s) with required parameters")
    
    return fixed, was_modified, changes


# Convenience function for the node to use
def validate_and_fix_tests(
    flow_code: str,
    test_code: str,
    flow_function_name: Optional[str] = None,
    flow_module_path: Optional[str] = None,  # V42-006: Added for mock patch path fixing
) -> tuple[str, bool, list[str]]:
    """
    Validate tests against flow code and fix any mismatches.
    
    This is the main entry point for the generate_code_and_tests node.
    
    Args:
        flow_code: The generated flow code
        test_code: The generated test code
        flow_function_name: Name of the flow function
        flow_module_path: Import path of the flow module (e.g., 'integrations.flows.openai_flow')
        
    Returns:
        Tuple of (fixed_test_code, was_modified, list_of_changes)
    """
    # First, cross-validate
    result = cross_validate_code_and_tests(
        flow_code=flow_code,
        test_code=test_code,
        flow_function_name=flow_function_name,
    )
    
    if result.is_valid:
        return test_code, False, []
    
    # Report mismatches
    for m in result.mismatches:
        logger.warning(
            f"[V25-001] Code/Test mismatch: {m.description}"
        )
    
    # Attempt automatic repair for semantic mismatches
    fixed_code, changes = repair_test_with_code_context(
        test_code=test_code,
        flow_code=flow_code,
        pytest_errors=[],  # No pytest errors yet, this is pre-validation
        flow_function_name=flow_function_name,
    )
    
    # V27-005: Also fix parameter mismatches
    fixed_code, param_modified, param_changes = fix_test_parameter_mismatches(
        test_code=fixed_code,
        flow_code=flow_code,
        flow_function_name=flow_function_name,
    )
    changes.extend(param_changes)
    
    # V29-001: Fix mock method name mismatches
    # Extract what method name the test expects from the test code itself
    expected_method_match = re.search(r'mock_client\.(\w+)\.return_value', fixed_code)
    if expected_method_match:
        expected_method = expected_method_match.group(1)
        fixed_code, mock_modified, mock_changes = align_test_mock_with_flow(
            test_code=fixed_code,
            flow_code=flow_code,
            expected_method_name=expected_method,
        )
        changes.extend(mock_changes)
    
    # V33-003: Fix missing required parameters in test payloads
    fixed_code, req_param_modified, req_param_changes = fix_test_required_parameters(
        test_code=fixed_code,
        flow_code=flow_code,
    )
    changes.extend(req_param_changes)
    
    # V42-006: Fix mock patch paths to match actual flow imports
    if flow_module_path:
        fixed_code, patch_modified, patch_changes = fix_test_mock_patch_path(
            test_code=fixed_code,
            flow_code=flow_code,
            flow_module_path=flow_module_path,
        )
        changes.extend(patch_changes)
    
    was_modified = fixed_code != test_code
    
    return fixed_code, was_modified, changes

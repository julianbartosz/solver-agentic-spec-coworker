"""
Interface Compatibility Validator for Generated Code (V45-004)

This module addresses the critical bug where LLM-generated flow and client code
have incompatible interfaces - the flow calls methods with arguments that the
client doesn't accept.

Problem:
- Flow calls: `client = OpenaiClient(api_key=api_key)` 
- Client has: `def __init__(self)` - no parameters
- Result: TypeError at runtime despite sandbox passing

Solution:
1. Extract interface signatures from client code (AST)
2. Extract usage patterns from flow code (AST)
3. Detect mismatches between them
4. Auto-fix the code to make interfaces compatible

Design Principles:
- Non-invasive: Only fix what's broken, preserve LLM-generated logic
- Robust: Handle edge cases, malformed code gracefully
- Traceable: Log all fixes for debugging
"""
import ast
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple, Any

logger = logging.getLogger(__name__)


# Class patterns that should NOT be considered as the primary client class
# These are typically helper classes, exceptions, or utilities
NON_CLIENT_CLASS_PATTERNS = frozenset({
    "error", "exception", "retryable", "timeout", "integration",
    "config", "settings", "response", "request", "model", "schema",
    "base", "abstract", "mixin", "helper", "util", "wrapper",
})

# Class patterns that indicate an actual client class
CLIENT_CLASS_INDICATORS = frozenset({
    "client", "api", "service", "sdk", "connector", "adapter",
})


def is_likely_client_class(class_name: str) -> bool:
    """
    Determine if a class name looks like an actual API client class.
    
    BUG-CLIENT-001 FIX: Prevent exception classes (RetryableError, IntegrationError)
    from being identified as the primary client class.
    
    Args:
        class_name: Name of the class
        
    Returns:
        True if the class appears to be an API client
        
    Examples:
        >>> is_likely_client_class("OpenaiClient")
        True
        >>> is_likely_client_class("RetryableError")
        False
        >>> is_likely_client_class("IntegrationError")
        False
        >>> is_likely_client_class("StripeApi")
        True
    """
    name_lower = class_name.lower()
    
    # First, exclude known non-client patterns
    for pattern in NON_CLIENT_CLASS_PATTERNS:
        if pattern in name_lower:
            return False
    
    # Check for positive client indicators
    for indicator in CLIENT_CLASS_INDICATORS:
        if indicator in name_lower:
            return True
    
    # Default: if it doesn't look like an exception/utility, consider it a candidate
    # This handles cases like "Stripe" or custom provider names
    return True


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class ParameterInfo:
    """Information about a function/method parameter."""
    name: str
    has_default: bool = False
    default_value: Optional[str] = None
    annotation: Optional[str] = None
    is_args: bool = False  # *args
    is_kwargs: bool = False  # **kwargs


@dataclass
class MethodSignature:
    """Signature of a method/function."""
    name: str
    parameters: List[ParameterInfo] = field(default_factory=list)
    is_async: bool = False
    return_annotation: Optional[str] = None
    
    @property
    def required_params(self) -> List[str]:
        """Get names of required (no default) parameters, excluding self."""
        return [p.name for p in self.parameters 
                if not p.has_default and not p.is_args and not p.is_kwargs 
                and p.name != 'self']
    
    @property
    def accepts_kwargs(self) -> bool:
        """Check if method accepts **kwargs."""
        return any(p.is_kwargs for p in self.parameters)
    
    @property  
    def accepts_args(self) -> bool:
        """Check if method accepts *args."""
        return any(p.is_args for p in self.parameters)
    
    @property
    def param_names(self) -> Set[str]:
        """Get all parameter names (excluding self)."""
        return {p.name for p in self.parameters if p.name != 'self'}


@dataclass
class ClassInterface:
    """Interface of a class including __init__ and methods."""
    name: str
    init_signature: Optional[MethodSignature] = None
    methods: Dict[str, MethodSignature] = field(default_factory=dict)
    base_classes: List[str] = field(default_factory=list)


@dataclass
class CallUsage:
    """Usage of a call in flow code."""
    class_name: str
    method_name: Optional[str]  # None for constructor calls
    arg_names: List[str]  # Keyword argument names used
    positional_count: int  # Number of positional args
    line_number: int


@dataclass
class InterfaceMismatch:
    """A detected interface mismatch."""
    severity: str  # "error" or "warning"
    location: str  # e.g., "flow.py:42"
    message: str
    fix_suggestion: Optional[str] = None


@dataclass  
class InterfaceValidationResult:
    """Result of interface validation."""
    is_valid: bool
    mismatches: List[InterfaceMismatch] = field(default_factory=list)
    client_interface: Optional[ClassInterface] = None
    flow_usages: List[CallUsage] = field(default_factory=list)


@dataclass
class InterfaceFix:
    """A fix applied to resolve interface mismatch."""
    file_type: str  # "client" or "flow"
    line_number: int
    original: str
    fixed: str
    reason: str


# =============================================================================
# AST EXTRACTION
# =============================================================================

def _extract_parameter_info(arg: ast.arg, defaults: Dict[str, ast.expr]) -> ParameterInfo:
    """Extract parameter info from AST arg node."""
    annotation = None
    if arg.annotation:
        try:
            annotation = ast.unparse(arg.annotation)
        except Exception:
            pass
    
    has_default = arg.arg in defaults
    default_value = None
    if has_default:
        try:
            default_value = ast.unparse(defaults[arg.arg])
        except Exception:
            pass
    
    return ParameterInfo(
        name=arg.arg,
        has_default=has_default,
        default_value=default_value,
        annotation=annotation,
    )


def _extract_method_signature(func: ast.FunctionDef) -> MethodSignature:
    """Extract method signature from AST function definition."""
    params = []
    
    # Build defaults mapping
    args = func.args
    defaults = {}
    
    # defaults apply to the LAST n args
    num_defaults = len(args.defaults)
    num_args = len(args.args)
    for i, default in enumerate(args.defaults):
        arg_idx = num_args - num_defaults + i
        if 0 <= arg_idx < num_args:
            defaults[args.args[arg_idx].arg] = default
    
    # kw_defaults apply to kwonlyargs
    for arg, default in zip(args.kwonlyargs, args.kw_defaults):
        if default is not None:
            defaults[arg.arg] = default
    
    # Extract regular args
    for arg in args.args:
        params.append(_extract_parameter_info(arg, defaults))
    
    # Extract *args
    if args.vararg:
        params.append(ParameterInfo(
            name=args.vararg.arg,
            is_args=True,
        ))
    
    # Extract keyword-only args
    for arg in args.kwonlyargs:
        params.append(_extract_parameter_info(arg, defaults))
    
    # Extract **kwargs
    if args.kwarg:
        params.append(ParameterInfo(
            name=args.kwarg.arg,
            is_kwargs=True,
        ))
    
    return_annotation = None
    if func.returns:
        try:
            return_annotation = ast.unparse(func.returns)
        except Exception:
            pass
    
    return MethodSignature(
        name=func.name,
        parameters=params,
        is_async=isinstance(func, ast.AsyncFunctionDef),
        return_annotation=return_annotation,
    )


def extract_class_interface(code: str, class_name: Optional[str] = None) -> Optional[ClassInterface]:
    """
    Extract class interface from Python code.
    
    BUG-CLIENT-001 FIX: When class_name is None, prioritize actual client classes
    over exception classes (RetryableError, IntegrationError) or utility classes.
    
    Args:
        code: Python source code
        class_name: Specific class to extract (or None for best client class)
        
    Returns:
        ClassInterface or None if not found
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.warning(f"Failed to parse code for interface extraction: {e}")
        return None
    
    # Collect all class definitions
    all_classes: List[ast.ClassDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            all_classes.append(node)
    
    if not all_classes:
        return None
    
    # If specific class requested, find it
    if class_name is not None:
        for node in all_classes:
            if node.name == class_name:
                return _build_class_interface(node)
        return None
    
    # BUG-CLIENT-001 FIX: When no class_name specified, find the best client class
    # Priority: 1) Classes with "Client" in name, 2) Other likely client classes, 3) First non-exception class
    
    # First pass: Look for classes with explicit "Client" suffix
    for node in all_classes:
        if node.name.endswith("Client") and is_likely_client_class(node.name):
            logger.debug(f"[BUG-CLIENT-001] Selected client class '{node.name}' (has Client suffix)")
            return _build_class_interface(node)
    
    # Second pass: Look for other likely client classes
    for node in all_classes:
        if is_likely_client_class(node.name):
            logger.debug(f"[BUG-CLIENT-001] Selected client class '{node.name}' (likely client)")
            return _build_class_interface(node)
    
    # Last resort: Return first class even if it looks like an exception
    # This shouldn't happen in well-formed code but provides fallback
    logger.warning(
        f"[BUG-CLIENT-001] No likely client class found in code. "
        f"Available classes: {[c.name for c in all_classes]}. Using first class."
    )
    return _build_class_interface(all_classes[0])


def _build_class_interface(node: ast.ClassDef) -> ClassInterface:
    """Build ClassInterface from an AST ClassDef node."""
    interface = ClassInterface(
        name=node.name,
        base_classes=[ast.unparse(b) for b in node.bases],
    )
    
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            sig = _extract_method_signature(item)
            if item.name == '__init__':
                interface.init_signature = sig
            else:
                interface.methods[item.name] = sig
    
    return interface


def extract_call_usages(code: str, client_class_name: str) -> List[CallUsage]:
    """
    Extract how a client class is used in flow code.
    
    Args:
        code: Flow Python source code
        client_class_name: Name of the client class to track
        
    Returns:
        List of CallUsage objects
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        logger.warning(f"Failed to parse code for usage extraction: {e}")
        return []
    
    usages = []
    
    # Track variable assignments to map instance names to class
    instance_vars: Dict[str, str] = {}  # var_name -> class_name
    
    class UsageVisitor(ast.NodeVisitor):
        def visit_Assign(self, node: ast.Assign):
            # Track: client = ClientClass(...)
            if isinstance(node.value, ast.Call):
                if isinstance(node.value.func, ast.Name):
                    called_class = node.value.func.id
                    if called_class == client_class_name:
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                instance_vars[target.id] = called_class
            self.generic_visit(node)
        
        def visit_Call(self, node: ast.Call):
            # Constructor call: ClientClass(...)
            if isinstance(node.func, ast.Name):
                if node.func.id == client_class_name:
                    usages.append(CallUsage(
                        class_name=client_class_name,
                        method_name=None,  # Constructor
                        arg_names=[kw.arg for kw in node.keywords if kw.arg],
                        positional_count=len(node.args),
                        line_number=node.lineno,
                    ))
            
            # Method call: instance.method(...)
            elif isinstance(node.func, ast.Attribute):
                if isinstance(node.func.value, ast.Name):
                    var_name = node.func.value.id
                    if var_name in instance_vars or var_name.lower() == 'client':
                        usages.append(CallUsage(
                            class_name=instance_vars.get(var_name, client_class_name),
                            method_name=node.func.attr,
                            arg_names=[kw.arg for kw in node.keywords if kw.arg],
                            positional_count=len(node.args),
                            line_number=node.lineno,
                        ))
            
            self.generic_visit(node)
    
    visitor = UsageVisitor()
    visitor.visit(tree)
    
    return usages


# =============================================================================
# INTERFACE VALIDATION
# =============================================================================

def validate_interface_compatibility(
    client_code: str,
    flow_code: str,
    client_class_name: Optional[str] = None,
) -> InterfaceValidationResult:
    """
    Validate that flow code uses client in a compatible way.
    
    Args:
        client_code: Client Python source code
        flow_code: Flow Python source code
        client_class_name: Name of client class (auto-detected if None)
        
    Returns:
        InterfaceValidationResult with mismatches
    """
    result = InterfaceValidationResult(is_valid=True)
    
    # Extract client interface
    client_interface = extract_class_interface(client_code, client_class_name)
    if not client_interface:
        result.mismatches.append(InterfaceMismatch(
            severity="warning",
            location="client",
            message="Could not extract client interface from code",
        ))
        return result
    
    result.client_interface = client_interface
    
    # Extract how flow uses the client
    usages = extract_call_usages(flow_code, client_interface.name)
    result.flow_usages = usages
    
    if not usages:
        # No direct usages found, might be using a different pattern
        return result
    
    # Check each usage for compatibility
    for usage in usages:
        if usage.method_name is None:
            # Constructor call
            _check_constructor_compatibility(usage, client_interface, result)
        else:
            # Method call
            _check_method_compatibility(usage, client_interface, result)
    
    return result


def _check_constructor_compatibility(
    usage: CallUsage,
    interface: ClassInterface,
    result: InterfaceValidationResult,
) -> None:
    """Check if constructor call is compatible."""
    init = interface.init_signature
    
    if not init:
        # No __init__ defined, defaults to object.__init__() - no args allowed
        if usage.arg_names or usage.positional_count > 0:
            result.is_valid = False
            result.mismatches.append(InterfaceMismatch(
                severity="error",
                location=f"flow:{usage.line_number}",
                message=(
                    f"Constructor call `{interface.name}()` passes arguments "
                    f"({', '.join(usage.arg_names) or f'{usage.positional_count} positional'}), "
                    f"but class has no __init__ or only default __init__"
                ),
                fix_suggestion="Remove arguments from constructor call or add __init__ parameters",
            ))
        return
    
    # Check keyword arguments
    for arg_name in usage.arg_names:
        if arg_name not in init.param_names and not init.accepts_kwargs:
            result.is_valid = False
            result.mismatches.append(InterfaceMismatch(
                severity="error",
                location=f"flow:{usage.line_number}",
                message=(
                    f"Constructor passes keyword argument `{arg_name}`, "
                    f"but `{interface.name}.__init__` doesn't accept it. "
                    f"Available params: {init.param_names or 'none'}"
                ),
                fix_suggestion=f"Add `{arg_name}` parameter to __init__ or use **kwargs",
            ))
    
    # Check positional arguments count
    # Exclude 'self' from the count
    max_positional = len([p for p in init.parameters if p.name != 'self' and not p.is_kwargs])
    if init.accepts_args:
        max_positional = float('inf')
    
    if usage.positional_count > max_positional:
        result.is_valid = False
        result.mismatches.append(InterfaceMismatch(
            severity="error",
            location=f"flow:{usage.line_number}",
            message=(
                f"Constructor passes {usage.positional_count} positional args, "
                f"but `{interface.name}.__init__` accepts at most {max_positional}"
            ),
        ))


def _check_method_compatibility(
    usage: CallUsage,
    interface: ClassInterface,
    result: InterfaceValidationResult,
) -> None:
    """Check if method call is compatible."""
    method_name = usage.method_name
    
    if method_name not in interface.methods:
        result.is_valid = False
        result.mismatches.append(InterfaceMismatch(
            severity="error",
            location=f"flow:{usage.line_number}",
            message=(
                f"Flow calls `client.{method_name}()`, but `{interface.name}` "
                f"has no such method. Available: {list(interface.methods.keys())}"
            ),
        ))
        return
    
    method = interface.methods[method_name]
    
    # Check keyword arguments
    for arg_name in usage.arg_names:
        if arg_name not in method.param_names and not method.accepts_kwargs:
            result.is_valid = False
            result.mismatches.append(InterfaceMismatch(
                severity="error",
                location=f"flow:{usage.line_number}",
                message=(
                    f"Method call passes keyword argument `{arg_name}`, "
                    f"but `{interface.name}.{method_name}` doesn't accept it. "
                    f"Available params: {method.param_names or 'none'}"
                ),
                fix_suggestion=f"Add `{arg_name}` parameter to method or use **kwargs",
            ))


# =============================================================================
# INTERFACE FIXING
# =============================================================================

def fix_interface_mismatches(
    client_code: str,
    flow_code: str,
    validation_result: InterfaceValidationResult,
) -> Tuple[str, str, List[InterfaceFix]]:
    """
    Fix interface mismatches between client and flow.
    
    Strategy:
    1. If flow passes args that client doesn't accept -> add params to client
    2. If client method signature differs -> update flow call
    
    Args:
        client_code: Original client code
        flow_code: Original flow code
        validation_result: Result from validate_interface_compatibility
        
    Returns:
        Tuple of (fixed_client_code, fixed_flow_code, list_of_fixes)
    """
    fixes: List[InterfaceFix] = []
    fixed_client = client_code
    fixed_flow = flow_code
    
    if validation_result.is_valid or not validation_result.client_interface:
        return fixed_client, fixed_flow, fixes
    
    interface = validation_result.client_interface
    
    # Group mismatches by type
    constructor_mismatches = []
    method_mismatches = []
    
    for mismatch in validation_result.mismatches:
        if "Constructor" in mismatch.message or "__init__" in mismatch.message:
            constructor_mismatches.append(mismatch)
        else:
            method_mismatches.append(mismatch)
    
    # Fix constructor issues - prefer fixing client to accept the args
    if constructor_mismatches:
        fixed_client, client_fixes = _fix_client_init(
            fixed_client, interface, validation_result.flow_usages
        )
        fixes.extend(client_fixes)
    
    # Fix method issues - first try to rename parameters in flow to match client
    # V45-004-FIX: This catches payload/data, body/data type mismatches
    fixed_flow, flow_param_fixes = _fix_flow_parameter_names(
        fixed_flow, interface, validation_result.flow_usages
    )
    fixes.extend(flow_param_fixes)
    
    # Then add **kwargs to client if still needed
    if method_mismatches:
        fixed_client, method_fixes = _fix_client_methods(
            fixed_client, interface, validation_result.flow_usages
        )
        fixes.extend(method_fixes)
    
    return fixed_client, fixed_flow, fixes


def _fix_client_init(
    client_code: str,
    interface: ClassInterface,
    usages: List[CallUsage],
) -> Tuple[str, List[InterfaceFix]]:
    """Fix client __init__ to accept arguments passed by flow."""
    fixes: List[InterfaceFix] = []
    
    # Collect all keyword args passed to constructor
    needed_params: Set[str] = set()
    for usage in usages:
        if usage.method_name is None:  # Constructor call
            needed_params.update(usage.arg_names)
    
    if not needed_params:
        return client_code, fixes
    
    # Get current init params (excluding self)
    current_params = set()
    has_kwargs = False
    if interface.init_signature:
        current_params = {p.name for p in interface.init_signature.parameters if p.name != 'self'}
        has_kwargs = interface.init_signature.accepts_kwargs
    
    # Find missing params
    missing_params = needed_params - current_params
    
    if not missing_params or has_kwargs:
        return client_code, fixes  # Already accepts these or has **kwargs
    
    # Parse and modify AST
    try:
        tree = ast.parse(client_code)
    except SyntaxError:
        return client_code, fixes
    
    class InitFixer(ast.NodeTransformer):
        def __init__(self):
            self.fixed = False
            self.fix_line = 0
        
        def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
            if node.name != interface.name:
                return node
            
            # Find __init__
            for i, item in enumerate(node.body):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == '__init__':
                    self._fix_init(item, missing_params)
                    self.fixed = True
                    self.fix_line = item.lineno
                    break
            else:
                # No __init__, need to add one
                new_init = self._create_init(missing_params)
                # Insert after docstring if present
                insert_idx = 0
                if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                    insert_idx = 1
                node.body.insert(insert_idx, new_init)
                self.fixed = True
                self.fix_line = node.lineno
            
            return node
        
        def _fix_init(self, init: ast.FunctionDef, params: Set[str]) -> None:
            """Add missing parameters to existing __init__."""
            # Add **kwargs if not present to accept any additional args
            if not init.args.kwarg:
                init.args.kwarg = ast.arg(arg='kwargs', annotation=None)
            
            # Also add the specific missing params with defaults
            for param in sorted(params):
                # Check if already present
                existing = {a.arg for a in init.args.args + init.args.kwonlyargs}
                if param not in existing:
                    # Add as keyword-only arg with None default
                    init.args.kwonlyargs.append(ast.arg(arg=param, annotation=ast.Name(id='Optional', ctx=ast.Load())))
                    init.args.kw_defaults.append(ast.Constant(value=None))
                    
                    # V45-004-FIX: Also add assignment to store the param as an instance attribute
                    # self.{param} = {param}
                    assign = ast.Assign(
                        targets=[ast.Attribute(
                            value=ast.Name(id='self', ctx=ast.Load()),
                            attr=param,
                            ctx=ast.Store()
                        )],
                        value=ast.Name(id=param, ctx=ast.Load())
                    )
                    # Insert assignment at end of init body (but before return if any)
                    insert_pos = len(init.body)
                    for i, stmt in enumerate(init.body):
                        if isinstance(stmt, ast.Return):
                            insert_pos = i
                            break
                    init.body.insert(insert_pos, assign)
        
        def _create_init(self, params: Set[str]) -> ast.FunctionDef:
            """Create a new __init__ method with the required params."""
            args = ast.arguments(
                posonlyargs=[],
                args=[ast.arg(arg='self', annotation=None)],
                vararg=None,
                kwonlyargs=[
                    ast.arg(arg=p, annotation=ast.Name(id='Optional', ctx=ast.Load()))
                    for p in sorted(params)
                ],
                kw_defaults=[ast.Constant(value=None) for _ in params],
                kwarg=ast.arg(arg='kwargs', annotation=None),
                defaults=[],
            )
            
            # V45-004-FIX: Create body that stores each param as instance attribute
            # self.{param} = {param} for each param
            body = []
            for param in sorted(params):
                assign = ast.Assign(
                    targets=[ast.Attribute(
                        value=ast.Name(id='self', ctx=ast.Load()),
                        attr=param,
                        ctx=ast.Store()
                    )],
                    value=ast.Name(id=param, ctx=ast.Load())
                )
                body.append(assign)
            
            # If no params, add pass
            if not body:
                body = [ast.Pass()]
            
            return ast.FunctionDef(
                name='__init__',
                args=args,
                body=body,
                decorator_list=[],
                returns=None,
            )
    
    fixer = InitFixer()
    tree = fixer.visit(tree)
    
    if fixer.fixed:
        ast.fix_missing_locations(tree)
        try:
            fixed_code = ast.unparse(tree)
            fixes.append(InterfaceFix(
                file_type="client",
                line_number=fixer.fix_line,
                original="(original __init__)",
                fixed=f"Added params: {sorted(missing_params)}, **kwargs",
                reason=f"V45-004: Flow passes {missing_params} to constructor",
            ))
            return fixed_code, fixes
        except Exception as e:
            logger.warning(f"Failed to unparse fixed AST: {e}")
    
    return client_code, fixes


def _fix_client_methods(
    client_code: str,
    interface: ClassInterface,
    usages: List[CallUsage],
) -> Tuple[str, List[InterfaceFix]]:
    """Fix client methods to accept arguments passed by flow."""
    fixes: List[InterfaceFix] = []
    
    # Collect all method calls and their args
    method_args: Dict[str, Set[str]] = {}
    for usage in usages:
        if usage.method_name:
            if usage.method_name not in method_args:
                method_args[usage.method_name] = set()
            method_args[usage.method_name].update(usage.arg_names)
    
    if not method_args:
        return client_code, fixes
    
    # Parse and modify AST
    try:
        tree = ast.parse(client_code)
    except SyntaxError:
        return client_code, fixes
    
    class MethodFixer(ast.NodeTransformer):
        def __init__(self):
            self.fixes_applied: List[Tuple[str, int, Set[str]]] = []
        
        def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
            if node.name != interface.name:
                return node
            
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name in method_args:
                        needed = method_args[item.name]
                        current = {a.arg for a in item.args.args + item.args.kwonlyargs}
                        missing = needed - current - {'self'}
                        
                        if missing and not item.args.kwarg:
                            # Add **kwargs to accept any keyword args
                            item.args.kwarg = ast.arg(arg='kwargs', annotation=None)
                            self.fixes_applied.append((item.name, item.lineno, missing))
            
            return node
    
    fixer = MethodFixer()
    tree = fixer.visit(tree)
    
    if fixer.fixes_applied:
        ast.fix_missing_locations(tree)
        try:
            fixed_code = ast.unparse(tree)
            for method_name, line, params in fixer.fixes_applied:
                fixes.append(InterfaceFix(
                    file_type="client",
                    line_number=line,
                    original=f"{method_name}(...)",
                    fixed=f"{method_name}(..., **kwargs)",
                    reason=f"V45-004: Flow passes {params} to {method_name}",
                ))
            return fixed_code, fixes
        except Exception as e:
            logger.warning(f"Failed to unparse fixed AST: {e}")
    
    return client_code, fixes


# =============================================================================
# V45-004-FIX: FLOW PARAMETER NAME FIXING
# =============================================================================
# Common parameter name aliases that LLMs use interchangeably.
# Format: {client_param: [flow_aliases]}
# When flow uses an alias, we rename it to match the client.
# =============================================================================

PARAMETER_ALIASES: Dict[str, List[str]] = {
    # Data/body/payload variants
    "data": ["payload", "body", "request_body", "json_body", "content"],
    "body": ["payload", "data", "request_body", "json_body", "content"],
    "payload": ["data", "body", "request_body", "json_body", "content"],
    
    # Request parameters
    "params": ["query_params", "query", "parameters", "qs"],
    "query_params": ["params", "query", "parameters", "qs"],
    
    # Headers
    "headers": ["http_headers", "request_headers", "hdrs"],
    
    # Timeout
    "timeout": ["timeout_seconds", "timeout_s", "timeout_ms", "request_timeout"],
    
    # Authentication
    "api_key": ["apikey", "key", "auth_key", "token", "api_token"],
    "auth": ["authentication", "credentials", "auth_info"],
    
    # Identifiers
    "id": ["identifier", "resource_id", "item_id", "obj_id"],
    "name": ["title", "label", "identifier"],
    
    # Pagination  
    "limit": ["page_size", "per_page", "max_results", "count"],
    "offset": ["skip", "start", "page_offset"],
    "page": ["page_number", "page_num", "pg"],
}


def _fix_flow_parameter_names(
    flow_code: str,
    interface: ClassInterface,
    usages: List[CallUsage],
) -> Tuple[str, List[InterfaceFix]]:
    """
    Fix parameter names in flow code to match client method signatures.
    
    V45-004-FIX: When flow calls client.method(payload=...) but client expects
    client.method(data=...), rename the parameter in the flow.
    
    Args:
        flow_code: Original flow code
        interface: Client interface extracted from client code
        usages: List of call usages extracted from flow code
        
    Returns:
        Tuple of (fixed_flow_code, list_of_fixes_applied)
    """
    fixes: List[InterfaceFix] = []
    
    if not interface or not usages:
        return flow_code, fixes
    
    # Build a mapping of method_name -> {flow_param -> client_param}
    renames_needed: Dict[str, Dict[str, str]] = {}
    
    for usage in usages:
        if not usage.method_name or usage.method_name not in interface.methods:
            continue
        
        method = interface.methods[usage.method_name]
        client_params = method.param_names
        
        for flow_arg in usage.arg_names:
            if flow_arg in client_params:
                continue  # Already matches
            
            # Check if flow_arg is an alias for any client param
            for client_param in client_params:
                aliases = PARAMETER_ALIASES.get(client_param, [])
                if flow_arg in aliases:
                    # Found an alias - need to rename
                    if usage.method_name not in renames_needed:
                        renames_needed[usage.method_name] = {}
                    renames_needed[usage.method_name][flow_arg] = client_param
                    break
    
    if not renames_needed:
        return flow_code, fixes
    
    # Parse flow code and fix parameter names in method calls
    try:
        tree = ast.parse(flow_code)
    except SyntaxError:
        return flow_code, fixes
    
    class ParamRenamer(ast.NodeTransformer):
        def __init__(self):
            self.renames_applied: List[Tuple[int, str, str, str]] = []  # (line, method, old, new)
        
        def visit_Call(self, node: ast.Call) -> ast.Call:
            # First, visit children
            self.generic_visit(node)
            
            # Check if this is a method call on client
            method_name = None
            if isinstance(node.func, ast.Attribute):
                method_name = node.func.attr
            
            if method_name and method_name in renames_needed:
                rename_map = renames_needed[method_name]
                
                # Rename keyword arguments
                for kw in node.keywords:
                    if kw.arg and kw.arg in rename_map:
                        old_name = kw.arg
                        new_name = rename_map[old_name]
                        kw.arg = new_name
                        self.renames_applied.append((node.lineno, method_name, old_name, new_name))
            
            return node
    
    renamer = ParamRenamer()
    tree = renamer.visit(tree)
    
    if renamer.renames_applied:
        ast.fix_missing_locations(tree)
        try:
            fixed_code = ast.unparse(tree)
            for line, method, old_name, new_name in renamer.renames_applied:
                fixes.append(InterfaceFix(
                    file_type="flow",
                    line_number=line,
                    original=f"{method}(..., {old_name}=...)",
                    fixed=f"{method}(..., {new_name}=...)",
                    reason=f"V45-004-FIX: Renamed '{old_name}' to '{new_name}' to match client signature",
                ))
            logger.info(f"[V45-004-FIX] Renamed {len(renamer.renames_applied)} parameters in flow code")
            return fixed_code, fixes
        except Exception as e:
            logger.warning(f"Failed to unparse fixed flow AST: {e}")
    
    return flow_code, fixes


# =============================================================================
# HIGH-LEVEL API
# =============================================================================

def validate_and_fix_interfaces(
    client_code: str,
    flow_code: str,
    client_class_name: Optional[str] = None,
    auto_fix: bool = True,
) -> Tuple[str, str, InterfaceValidationResult, List[InterfaceFix]]:
    """
    Validate and optionally fix interface compatibility between client and flow.
    
    This is the main entry point for interface validation.
    
    Args:
        client_code: Client Python source code
        flow_code: Flow Python source code
        client_class_name: Name of client class (auto-detected if None)
        auto_fix: Whether to auto-fix mismatches
        
    Returns:
        Tuple of (fixed_client, fixed_flow, validation_result, fixes_applied)
    """
    # Validate
    result = validate_interface_compatibility(client_code, flow_code, client_class_name)
    
    if result.is_valid or not auto_fix:
        return client_code, flow_code, result, []
    
    # Log mismatches
    for mismatch in result.mismatches:
        logger.warning(f"[V45-004] Interface mismatch: {mismatch.message}")
    
    # Fix
    fixed_client, fixed_flow, fixes = fix_interface_mismatches(
        client_code, flow_code, result
    )
    
    # Re-validate after fixes
    new_result = validate_interface_compatibility(fixed_client, fixed_flow, client_class_name)
    
    if not new_result.is_valid:
        # Still have issues, log but return what we have
        logger.warning(
            f"[V45-004] Interface mismatches remain after fix attempt: "
            f"{len(new_result.mismatches)} issues"
        )
    else:
        logger.info(
            f"[V45-004] Fixed {len(fixes)} interface mismatches between client and flow"
        )
    
    return fixed_client, fixed_flow, new_result, fixes


def check_artifacts_interface_compatibility(
    artifacts: List[Any],  # List[ArtifactFile] but avoiding circular import
) -> Tuple[List[Any], List[InterfaceFix]]:
    """
    Check and fix interface compatibility across artifacts.
    
    Args:
        artifacts: List of ArtifactFile objects
        
    Returns:
        Tuple of (fixed_artifacts, fixes_applied)
    """
    # Find client and flow artifacts
    client_artifact = None
    flow_artifact = None
    
    for artifact in artifacts:
        path = artifact.path.lower()
        if 'client' in path and path.endswith('.py'):
            client_artifact = artifact
        elif 'flow' in path and path.endswith('.py'):
            flow_artifact = artifact
    
    if not client_artifact or not flow_artifact:
        return artifacts, []
    
    # Validate and fix
    fixed_client, fixed_flow, result, fixes = validate_and_fix_interfaces(
        client_artifact.content,
        flow_artifact.content,
        auto_fix=True,
    )
    
    if not fixes:
        return artifacts, []
    
    # Update artifacts
    fixed_artifacts = []
    for artifact in artifacts:
        if artifact is client_artifact:
            # Create new artifact with fixed content
            from integration_coworker.codegen.sandbox import ArtifactFile
            fixed_artifacts.append(ArtifactFile(
                path=artifact.path,
                content=fixed_client,
            ))
        elif artifact is flow_artifact:
            from integration_coworker.codegen.sandbox import ArtifactFile
            fixed_artifacts.append(ArtifactFile(
                path=artifact.path,
                content=fixed_flow,
            ))
        else:
            fixed_artifacts.append(artifact)
    
    return fixed_artifacts, fixes


__all__ = [
    "ParameterInfo",
    "MethodSignature",
    "ClassInterface",
    "CallUsage",
    "InterfaceMismatch",
    "InterfaceValidationResult",
    "InterfaceFix",
    "extract_class_interface",
    "extract_call_usages",
    "validate_interface_compatibility",
    "fix_interface_mismatches",
    "validate_and_fix_interfaces",
    "check_artifacts_interface_compatibility",
]

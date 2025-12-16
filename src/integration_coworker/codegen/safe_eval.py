"""Safe expression evaluation for cross-field validation rules.

Replaces unsafe eval() with simpleeval, which uses AST parsing
and does not allow attribute access or function calls by default.

Per docs/BUCKET_2_NO_INTERPRETATION_PLAN.md Step 1
"""

from __future__ import annotations

import operator
from typing import Any, Callable, Dict, Optional

# Use simpleeval for safe AST-based evaluation
try:
    from simpleeval import EvalWithCompoundTypes, InvalidExpression, FeatureNotAvailable
    SIMPLEEVAL_AVAILABLE = True
except ImportError:
    SIMPLEEVAL_AVAILABLE = False
    EvalWithCompoundTypes = None
    InvalidExpression = Exception
    FeatureNotAvailable = Exception


# Whitelist safe operators for cross-field validation
SAFE_OPERATORS = {
    # Comparison
    "<": operator.lt,
    ">": operator.gt,
    "<=": operator.le,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
    # Arithmetic
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "//": operator.floordiv,
    "%": operator.mod,
    "**": operator.pow,
    # Unary
    "not": operator.not_,
}

# Whitelist safe functions (minimal subset)
SAFE_FUNCTIONS: Dict[str, Callable] = {
    "len": len,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "abs": abs,
    "min": min,
    "max": max,
    "round": round,
}


class CrossFieldEvaluationError(Exception):
    """Raised when cross-field expression evaluation fails."""
    pass


class SafeExpressionEvaluator:
    """Evaluates cross-field validation expressions safely.
    
    Uses simpleeval to parse and evaluate expressions without
    allowing arbitrary code execution.
    
    Allowed:
        - Comparisons: amount > 100
        - Arithmetic: total == qty * price
        - Boolean logic: flag and amount > 0
        - Membership: status in ['A', 'B']
        - Safe functions: len, str, int, float, abs, min, max, round
        
    Denied:
        - Attribute access: obj.attr
        - Import: __import__
        - Arbitrary function calls
        - Lambda expressions
        - Comprehensions
    """
    
    def __init__(
        self,
        extra_functions: Optional[Dict[str, Callable]] = None,
    ):
        """Initialize evaluator.
        
        Args:
            extra_functions: Optional additional whitelisted functions
        """
        if not SIMPLEEVAL_AVAILABLE:
            raise ImportError(
                "simpleeval is required for safe expression evaluation. "
                "Install with: pip install simpleeval>=1.0.0"
            )
        
        self._functions = SAFE_FUNCTIONS.copy()
        if extra_functions:
            self._functions.update(extra_functions)
    
    def evaluate(
        self,
        expression: str,
        record: Dict[str, Any],
    ) -> Any:
        """Evaluate expression against record fields.
        
        Args:
            expression: Cross-field validation expression
            record: Dict of field_name → value
            
        Returns:
            Evaluation result (typically bool for validation)
            
        Raises:
            CrossFieldEvaluationError: If expression is invalid or unsafe
        """
        try:
            evaluator = EvalWithCompoundTypes(
                names=record,
                functions=self._functions,
            )
            return evaluator.eval(expression)
        except InvalidExpression as e:
            raise CrossFieldEvaluationError(
                f"Invalid expression '{expression}': {e}"
            ) from e
        except FeatureNotAvailable as e:
            raise CrossFieldEvaluationError(
                f"Unsafe operation in expression '{expression}': {e}"
            ) from e
        except NameError as e:
            raise CrossFieldEvaluationError(
                f"Unknown variable in expression '{expression}': {e}"
            ) from e
        except Exception as e:
            raise CrossFieldEvaluationError(
                f"Failed to evaluate '{expression}': {type(e).__name__}: {e}"
            ) from e


# Module-level singleton for common use
_default_evaluator: Optional[SafeExpressionEvaluator] = None


def safe_eval_cross_field(expression: str, record: Dict[str, Any]) -> Any:
    """Convenience function for safe cross-field evaluation.
    
    Args:
        expression: Validation expression (e.g., "total > qty * price")
        record: Dict mapping field names to values
        
    Returns:
        Evaluation result
        
    Raises:
        CrossFieldEvaluationError: On invalid/unsafe expression
    """
    global _default_evaluator
    
    if _default_evaluator is None:
        _default_evaluator = SafeExpressionEvaluator()
    
    return _default_evaluator.evaluate(expression, record)


__all__ = [
    "SafeExpressionEvaluator",
    "safe_eval_cross_field",
    "CrossFieldEvaluationError",
    "SAFE_FUNCTIONS",
]

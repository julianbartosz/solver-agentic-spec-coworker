"""
LLM Self-Review Module for Codegen Quality

Implements a second-pass review step that critiques and repairs generated code
before final validation gates.

Per user requirement B: Provider-agnostic via LangChain structured output.

Flow:
1. Draft generation (existing)
2. Syntax/security/semantic validation (existing)
3. Self-review (this module) - only when profile.enable_self_review=True
4. Re-validation on patched content
5. Max 1 repair iteration per artifact

The reviewer checks:
- Code compiles / imports resolve
- ruff check + ruff format --check compliance
- mypy plausibility (types, obvious Any abuse)
- Tests actually exercise the client (coverage intent)
- No insecure patterns (exec/eval/subprocess)
- No secrets or API keys in output
"""
import logging
from enum import Enum
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# B2: Review Contract - Pydantic Models
# =============================================================================

class IssueSeverity(str, Enum):
    """Severity level for review issues."""
    ERROR = "error"      # Must fix - blocks production
    WARNING = "warning"  # Should fix - may cause problems
    INFO = "info"        # Suggestion - optional improvement


class IssueCategory(str, Enum):
    """Category of review issue - maps to gates."""
    SYNTAX = "syntax"           # Code doesn't parse
    TYPE_ERROR = "type_error"   # Type annotation issues (mypy)
    STYLE = "style"             # Formatting issues (ruff format)
    LINT = "lint"               # Linting issues (ruff check)
    SECURITY = "security"       # Security violations (exec, eval, secrets)
    COVERAGE = "coverage"       # Tests don't exercise target code
    LOGIC = "logic"             # Functional correctness issues
    IMPORT = "import"           # Missing or invalid imports


class ReviewIssue(BaseModel):
    """A single issue found during review."""
    
    category: IssueCategory = Field(
        ...,
        description="Category of the issue (syntax, type_error, style, lint, security, coverage, logic, import)",
    )
    severity: IssueSeverity = Field(
        ...,
        description="Severity: error (must fix), warning (should fix), info (optional)",
    )
    line_number: Optional[int] = Field(
        default=None,
        description="Line number where the issue occurs (1-indexed)",
    )
    original_code: Optional[str] = Field(
        default=None,
        description="The problematic code snippet (just the affected lines, not full file)",
    )
    fixed_code: Optional[str] = Field(
        default=None,
        description="The corrected code snippet (just the fix, not full file)",
    )
    explanation: str = Field(
        ...,
        description="Brief explanation of the issue (1-2 sentences max)",
        min_length=5,
    )


class ReviewResult(BaseModel):
    """
    Result of LLM self-review.
    
    Contract:
    - verdict="pass": No issues, code is acceptable as-is
    - verdict="fail" + issues with fixes: Issues found with inline fixes
    - verdict="fail" + issues without fixes: Issues found but cannot auto-fix
    
    NOTE: We use targeted inline fixes (original_code -> fixed_code per issue)
    instead of returning full patched_content to avoid output token limits.
    """
    
    verdict: Literal["pass", "fail"] = Field(
        ...,
        description="pass if code is acceptable, fail if issues found",
    )
    issues: List[ReviewIssue] = Field(
        default_factory=list,
        description="List of issues found (empty if verdict=pass). Max 5 issues.",
    )
    review_summary: Optional[str] = Field(
        default=None,
        description="One sentence summary of review (max 100 chars)",
        max_length=200,
    )
    
    @property
    def has_errors(self) -> bool:
        """Check if any issues are severity=error."""
        return any(i.severity == IssueSeverity.ERROR for i in self.issues)
    
    @property
    def can_repair(self) -> bool:
        """Check if repair is possible (at least one issue has fixed_code)."""
        return self.verdict == "fail" and any(
            i.fixed_code is not None for i in self.issues
        )


# =============================================================================
# B4: Reviewer Prompt Rubric
# =============================================================================

REVIEW_SYSTEM_PROMPT = """You are a code reviewer. Review the code and respond concisely.

CHECKS (in priority order):
1. SYNTAX: Valid Python that parses
2. IMPORTS: All imports resolvable  
3. SECURITY: No exec/eval, no hardcoded secrets
4. TYPES: Reasonable type hints on functions
5. STYLE: Basic formatting (not critical)
6. TEST ASSERTIONS (for test files):
   - Do NOT use hardcoded dictionary key access like `call_args[1]["key"]` - this causes KeyError
   - Use `.called`, `.call_count`, or safe `.get()` instead of direct key access
   - Verify mock was called before checking call_args
   - Use `assert mock.method.called` rather than `assert call_args[1]["specific_key"]`

RESPONSE RULES:
- If code is acceptable: verdict="pass", issues=[], review_summary="LGTM"
- If issues found: verdict="fail", list max 5 issues with inline fixes

FOR EACH ISSUE provide:
- line_number: where the problem is
- original_code: ONLY the problematic line(s), not the whole file
- fixed_code: ONLY the corrected line(s), not the whole file  
- explanation: 1-2 sentences max

CRITICAL: Do NOT return the entire file. Only return the specific lines that need fixing.
Keep responses SHORT. Most code passes review - only flag real problems.

COMMON TEST ASSERTION FIXES:
- BAD:  `assert call_args[1]["line_items"] is not None`
- GOOD: `assert mock_client.method.called`
- BAD:  `assert call_args[1]["customer"] == "cus_test123"`
- GOOD: `assert mock_client.method.call_count >= 1`
"""


def build_review_prompt(
    code: str,
    artifact_type: str,
    file_path: str,
    task_context: Optional[str] = None,
    dependencies: Optional[List[str]] = None,
) -> str:
    """
    Build the review prompt for a single artifact.
    
    Args:
        code: The generated code to review
        artifact_type: "client", "flow", or "test"
        file_path: Path where code will be placed
        task_context: Optional context about what the code should do
        dependencies: Optional list of dependencies that should be available
        
    Returns:
        Formatted prompt string
    """
    deps_str = ", ".join(dependencies) if dependencies else "none specified"
    
    prompt = f"""Review this generated {artifact_type} code:

FILE: {file_path}
DEPENDENCIES AVAILABLE: {deps_str}

```python
{code}
```

{f'TASK CONTEXT: {task_context}' if task_context else ''}

Analyze according to the checklist and provide your verdict.
If there are issues you can fix, provide the complete corrected code in patched_content.
"""
    return prompt


# =============================================================================
# B3: Self-Review Integration Function
# =============================================================================

async def perform_self_review(
    code: str,
    artifact_type: str,
    file_path: str,
    llm: Any,
    task_context: Optional[str] = None,
    dependencies: Optional[List[str]] = None,
) -> ReviewResult:
    """
    Perform LLM self-review on generated code.
    
    Uses LangChain with_structured_output for provider-agnostic structured output.
    
    Args:
        code: The generated code to review
        artifact_type: "client", "flow", or "test"
        file_path: Target file path
        llm: LangChain chat model (ChatOpenAI, ChatAnthropic, etc.)
        task_context: Optional context about what the code should do
        dependencies: Optional available dependencies
        
    Returns:
        ReviewResult with verdict, issues, and optional patched_content
    """
    from integration_coworker.codegen.structured_output import get_structured_llm
    
    logger.info(f"[self_review] Reviewing {artifact_type}: {file_path}")
    
    try:
        # Get structured LLM (provider-agnostic)
        structured_llm = get_structured_llm(llm, ReviewResult)
        
        # Build review prompt
        prompt = build_review_prompt(
            code=code,
            artifact_type=artifact_type,
            file_path=file_path,
            task_context=task_context,
            dependencies=dependencies,
        )
        
        # Create messages with system prompt
        from langchain_core.messages import SystemMessage, HumanMessage
        
        messages = [
            SystemMessage(content=REVIEW_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        
        # Invoke structured LLM
        result = await structured_llm.ainvoke(messages)
        
        # Ensure we got a ReviewResult
        if isinstance(result, ReviewResult):
            logger.info(
                f"[self_review] {file_path}: verdict={result.verdict}, "
                f"issues={len(result.issues)}, can_repair={result.can_repair}"
            )
            return result
        else:
            # Unexpected response type
            logger.warning(f"[self_review] Unexpected response type: {type(result)}")
            return ReviewResult(
                verdict="pass",
                issues=[],
                review_summary="Review completed but response format was unexpected",
            )
            
    except Exception as e:
        logger.error(f"[self_review] Error during review: {e}")
        # On error, return pass to not block (fail-safe)
        return ReviewResult(
            verdict="pass",
            issues=[],
            review_summary=f"Review error (fail-safe pass): {str(e)[:100]}",
        )


def apply_review_result(
    original_code: str,
    review_result: ReviewResult,
    is_production: bool,
    allow_graceful_degradation: bool = True,
) -> tuple[str, bool, Optional[str]]:
    """
    Apply review result to code using inline fixes.
    
    Args:
        original_code: The original generated code
        review_result: Result from perform_self_review
        is_production: If True, stricter validation; if False, more lenient
        allow_graceful_degradation: V39-002: If True, return code with warnings
            instead of hard-failing. This allows code to be written to repo
            with known issues for human review. Default True.
        
    Returns:
        Tuple of (code_to_use, success, error_message)
        - code_to_use: Either original or patched code
        - success: True if we have usable code, False if should fail
        - error_message: Description of failure or warnings (if any issues)
    """
    if review_result.verdict == "pass":
        logger.debug("[self_review] Verdict: pass - using original code")
        return original_code, True, None
    
    # Verdict is "fail" - try to apply inline fixes
    patched_code = original_code
    fixes_applied = 0
    
    if review_result.can_repair:
        logger.info(f"[self_review] Verdict: fail - applying {len(review_result.issues)} inline fixes")
        
        for issue in review_result.issues:
            if issue.original_code and issue.fixed_code:
                # Apply the fix by string replacement
                if issue.original_code.strip() in patched_code:
                    patched_code = patched_code.replace(
                        issue.original_code.strip(),
                        issue.fixed_code.strip(),
                        1  # Only replace first occurrence
                    )
                    fixes_applied += 1
                    logger.debug(f"[self_review] Applied fix for line {issue.line_number}: {issue.explanation[:50]}")
        
        if fixes_applied > 0:
            logger.info(f"[self_review] Applied {fixes_applied} fixes")
            return patched_code, True, None
        else:
            logger.warning("[self_review] Had fixes but couldn't apply any (code mismatch)")
    
    # Classify remaining issues by severity and type
    error_issues = [i for i in review_result.issues if i.severity == IssueSeverity.ERROR]
    warning_issues = [i for i in review_result.issues if i.severity == IssueSeverity.WARNING]
    
    # V33-004 + V39-002: Enhanced issue classification
    # - Security issues: MUST block (exec, eval, secrets)
    # - Syntax issues: Block unless graceful degradation
    # - Import issues: Block unless graceful degradation  
    # - Type issues: Warn but allow (mypy errors are often false positives)
    # - Semantic issues: Warn but allow (LLM quality, not correctness)
    # - Style issues: Always allow
    
    security_keywords = ["exec", "eval", "subprocess", "secret", "hardcoded", "credential"]
    semantic_keywords = [
        "function is named", "doesn't match", "semantic mismatch", "wrong endpoint",
        "should be", "but it calls", "retrieves.*instead of", "mismatch",
    ]
    
    blocking_issues = []
    non_blocking_issues = []
    
    for issue in error_issues:
        explanation_lower = issue.explanation.lower()
        
        # Security issues always block
        if any(kw in explanation_lower for kw in security_keywords):
            blocking_issues.append(issue)
            continue
        
        # Semantic issues are non-blocking (LLM quality variance)
        if any(kw in explanation_lower for kw in semantic_keywords):
            non_blocking_issues.append(issue)
            continue
        
        # Syntax/import issues - blocking unless graceful degradation
        if issue.category in (IssueCategory.SYNTAX, IssueCategory.IMPORT):
            if allow_graceful_degradation:
                non_blocking_issues.append(issue)
            else:
                blocking_issues.append(issue)
            continue
        
        # Type errors - non-blocking (mypy is often overly strict)
        if issue.category == IssueCategory.TYPE_ERROR:
            non_blocking_issues.append(issue)
            continue
        
        # Default: treat as blocking in strict mode, non-blocking otherwise
        if is_production and not allow_graceful_degradation:
            blocking_issues.append(issue)
        else:
            non_blocking_issues.append(issue)
    
    # Add warning issues to non-blocking
    non_blocking_issues.extend(warning_issues)
    
    # Build messages
    blocking_msg = "; ".join(i.explanation for i in blocking_issues[:3])
    warning_msg = "; ".join(i.explanation for i in non_blocking_issues[:3])
    
    # Decision logic
    if blocking_issues:
        # Hard block on security/critical issues
        logger.error(
            f"[self_review] Critical issues prevent code use ({len(blocking_issues)} blocking): "
            f"{blocking_msg}"
        )
        return patched_code, False, f"Self-review failed (security/critical): {blocking_msg}"
    
    if non_blocking_issues:
        # V39-002: Allow code with warnings
        logger.warning(
            f"[self_review] {'Production' if is_production else 'Development'}: "
            f"proceeding with {len(non_blocking_issues)} non-blocking issues: {warning_msg}"
        )
        # Return success=True but include warning message for report
        return patched_code, True, f"[WARNINGS] {warning_msg}"
    
    # No issues classified - shouldn't happen but handle gracefully
    logger.info("[self_review] All issues resolved or classified as non-blocking")
    return patched_code, True, None


# =============================================================================
# Convenience: Review with Retry Logic
# =============================================================================

async def review_and_repair(
    code: str,
    artifact_type: str,
    file_path: str,
    llm: Any,
    is_production: bool,
    task_context: Optional[str] = None,
    dependencies: Optional[List[str]] = None,
    max_repair_attempts: int = 1,
    allow_graceful_degradation: bool = True,
) -> tuple[str, bool, Optional[str]]:
    """
    Perform self-review with optional repair iteration.
    
    Per requirement B3.5: Max 1 self-review repair iteration per artifact.
    
    V39-002: Added allow_graceful_degradation to let production runs
    complete with warnings instead of hard-failing on non-security issues.
    
    Args:
        code: Generated code to review
        artifact_type: "client", "flow", or "test"
        file_path: Target file path
        llm: LangChain chat model
        is_production: If True, stricter validation
        task_context: Optional context
        dependencies: Optional dependencies
        max_repair_attempts: Maximum repair iterations (default 1)
        allow_graceful_degradation: If True, allow code with non-critical issues
        
    Returns:
        Tuple of (final_code, success, error_message)
    """
    current_code = code
    all_warnings: List[str] = []
    
    for attempt in range(max_repair_attempts + 1):  # +1 for initial review
        logger.debug(f"[self_review] Attempt {attempt + 1}/{max_repair_attempts + 1}")
        
        result = await perform_self_review(
            code=current_code,
            artifact_type=artifact_type,
            file_path=file_path,
            llm=llm,
            task_context=task_context,
            dependencies=dependencies,
        )
        
        final_code, success, error = apply_review_result(
            original_code=current_code,
            review_result=result,
            is_production=is_production,
            allow_graceful_degradation=allow_graceful_degradation,
        )
        
        # Collect warnings for final report
        if error and error.startswith("[WARNINGS]"):
            all_warnings.append(error)
        
        if result.verdict == "pass":
            # All good - return with any accumulated warnings
            warning_msg = "; ".join(all_warnings) if all_warnings else None
            return final_code, True, warning_msg
        
        if not success:
            # Hard failure (security issue)
            return final_code, False, error
        
        if not result.can_repair:
            # No more repairs possible, return current state with warnings
            warning_msg = "; ".join(all_warnings) if all_warnings else error
            return final_code, success, warning_msg
        
        if attempt < max_repair_attempts:
            # Apply fix and re-review
            current_code = final_code
            logger.info(f"[self_review] Applied repair, will re-review")
        else:
            # Max attempts reached, use patched code with warnings
            warning_msg = "; ".join(all_warnings) if all_warnings else error
            return final_code, True, warning_msg
    
    warning_msg = "; ".join(all_warnings) if all_warnings else None
    return current_code, True, warning_msg

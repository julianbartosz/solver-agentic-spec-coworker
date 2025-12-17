"""
Implicit Signal Collection

Records automatic quality signals from run outcomes:
- Compile success/failure (py_compile check)
- Test pass/fail (if tests are executed)
- Lint score (ruff violations)

These signals supplement human feedback for confidence scoring.

Per docs/decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from integration_coworker.domain.models import (
    FeedbackRecord,
    FeedbackType,
    FeedbackSource,
)
from integration_coworker.feedback.langsmith_sync import _upsert_feedback_record, _get_template_key_for_run

logger = logging.getLogger(__name__)


def record_compile_result(
    run_id: str,
    success: bool,
    template_key: Optional[str] = None,
) -> Optional[int]:
    """
    Record compile check result as implicit feedback.
    
    Args:
        run_id: The run ID
        success: Whether compilation succeeded
        template_key: Optional template key (will be looked up if not provided)
        
    Returns:
        Feedback record ID
    """
    if template_key is None:
        template_key = _get_template_key_for_run(run_id)
    
    record = FeedbackRecord(
        run_id=run_id,
        template_key=template_key,
        feedback_type=FeedbackType.AUTO_COMPILE,
        score=1.0 if success else 0.0,
        comment="Automatic: py_compile check",
        source=FeedbackSource.AUTO,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    
    record_id = _upsert_feedback_record(record)
    
    if record_id:
        logger.debug(
            f"Recorded compile {'success' if success else 'failure'} "
            f"for run {run_id}"
        )
    
    return record_id


def record_test_result(
    run_id: str,
    passed: int,
    failed: int,
    template_key: Optional[str] = None,
) -> Optional[int]:
    """
    Record test execution result as implicit feedback.
    
    Args:
        run_id: The run ID
        passed: Number of tests passed
        failed: Number of tests failed
        template_key: Optional template key
        
    Returns:
        Feedback record ID
    """
    if template_key is None:
        template_key = _get_template_key_for_run(run_id)
    
    total = passed + failed
    score = passed / total if total > 0 else 0.0
    
    record = FeedbackRecord(
        run_id=run_id,
        template_key=template_key,
        feedback_type=FeedbackType.AUTO_TEST,
        score=score,
        comment=f"Automatic: {passed}/{total} tests passed",
        source=FeedbackSource.AUTO,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    
    record_id = _upsert_feedback_record(record)
    
    if record_id:
        logger.debug(
            f"Recorded test result {passed}/{total} for run {run_id}"
        )
    
    return record_id


def record_lint_result(
    run_id: str,
    violations: int,
    total_lines: int,
    template_key: Optional[str] = None,
) -> Optional[int]:
    """
    Record lint check result as implicit feedback.
    
    Score is based on violations per 100 lines.
    0 violations = 1.0, 10+ violations per 100 lines = 0.0
    
    Args:
        run_id: The run ID
        violations: Number of lint violations
        total_lines: Total lines of generated code
        template_key: Optional template key
        
    Returns:
        Feedback record ID
    """
    if template_key is None:
        template_key = _get_template_key_for_run(run_id)
    
    if total_lines <= 0:
        score = 0.5  # Unknown
    else:
        violations_per_100 = (violations / total_lines) * 100
        # 0 violations = 1.0, 10+ per 100 lines = 0.0
        score = max(0.0, 1.0 - (violations_per_100 / 10))
    
    record = FeedbackRecord(
        run_id=run_id,
        template_key=template_key,
        feedback_type=FeedbackType.AUTO_LINT,
        score=score,
        comment=f"Automatic: {violations} lint violations in {total_lines} lines",
        source=FeedbackSource.AUTO,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    
    record_id = _upsert_feedback_record(record)
    
    if record_id:
        logger.debug(
            f"Recorded lint result ({violations} violations) for run {run_id}"
        )
    
    return record_id


def record_validation_result(
    run_id: str,
    has_errors: bool,
    error_count: int = 0,
    warning_count: int = 0,
    template_key: Optional[str] = None,
) -> Optional[int]:
    """
    Record validation check result as implicit feedback.
    
    Args:
        run_id: The run ID
        has_errors: Whether there were validation errors
        error_count: Number of errors
        warning_count: Number of warnings
        template_key: Optional template key
        
    Returns:
        Feedback record ID
    """
    if template_key is None:
        template_key = _get_template_key_for_run(run_id)
    
    # Errors have high penalty, warnings moderate
    if has_errors:
        # Start at 0.5, reduce by 0.1 per error up to 0
        score = max(0.0, 0.5 - (error_count * 0.1))
    else:
        # Start at 1.0, reduce by 0.05 per warning down to 0.6
        score = max(0.6, 1.0 - (warning_count * 0.05))
    
    record = FeedbackRecord(
        run_id=run_id,
        template_key=template_key,
        feedback_type=FeedbackType.AUTO_COMPILE,  # Reuse compile type for validation
        score=score,
        comment=f"Automatic: {error_count} errors, {warning_count} warnings",
        source=FeedbackSource.AUTO,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    
    record_id = _upsert_feedback_record(record)
    
    if record_id:
        logger.debug(
            f"Recorded validation result (errors={error_count}, warnings={warning_count}) "
            f"for run {run_id}"
        )
    
    return record_id

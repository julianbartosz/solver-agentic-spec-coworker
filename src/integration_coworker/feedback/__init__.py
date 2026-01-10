"""
Feedback Module for Quality-Based KG Learning

This module enables the Knowledge Graph to learn from quality signals:
1. LangSmith feedback (human ratings, automated evaluations)
2. Implicit signals (compile success, test pass, lint score)
3. Automatic hooks (integrated into workflow nodes)

The feedback flows into template confidence_score, which influences
GraphRAG scoring during align_task_with_kg.

Architecture:
- langsmith_sync.py: Sync feedback from LangSmith API
- confidence.py: Aggregate feedback → confidence scores
- implicit_signals.py: Auto-generated feedback from run outcomes
- hooks.py: Safe wrappers for auto-recording in workflow nodes

Per docs/decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md
"""

from integration_coworker.feedback.langsmith_sync import (
    sync_langsmith_feedback,
    fetch_feedback_for_run,
    is_langsmith_available,
    apply_confidence_decay,
)
from integration_coworker.feedback.confidence import (
    compute_confidence_score,
    update_node_confidence,
    update_all_confidences,
    get_confidence_for_template,
)
from integration_coworker.feedback.implicit_signals import (
    record_compile_result,
    record_test_result,
    record_lint_result,
)
from integration_coworker.feedback.hooks import (
    safe_record_compile_result,
    safe_record_syntax_check,
    safe_record_test_result,
    safe_record_lint_result,
    safe_record_validation_result,
    safe_record_security_check,
    enable_feedback_hooks,
    disable_feedback_hooks,
    are_hooks_enabled,
)

__all__ = [
    # LangSmith sync
    "sync_langsmith_feedback",
    "fetch_feedback_for_run",
    "is_langsmith_available",
    "apply_confidence_decay",
    # Confidence scoring
    "compute_confidence_score",
    "update_node_confidence",
    "update_all_confidences",
    "get_confidence_for_template",
    # Implicit signals (direct)
    "record_compile_result",
    "record_test_result",
    "record_lint_result",
    # Safe hooks (for workflow integration)
    "safe_record_compile_result",
    "safe_record_syntax_check",
    "safe_record_test_result",
    "safe_record_lint_result",
    "safe_record_validation_result",
    "safe_record_security_check",
    # Hook control
    "enable_feedback_hooks",
    "disable_feedback_hooks",
    "are_hooks_enabled",
]

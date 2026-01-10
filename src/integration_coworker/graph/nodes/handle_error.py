"""
Error handling node for the workflow.

Production Hardening (per PROD_HARDENING_PLAN_V2.md + OWASP Logging Cheat Sheet):
- Re-entrancy guard: prevents infinite loops if called multiple times
- Secret redaction: sanitizes state.errors before ANY logging/persistence
- Log-injection sanitization: removes CR/LF to prevent log injection attacks
- Idempotent: safe to call multiple times for same run
- No I/O: pure state transformation, no DB/network calls
- Bounded: completes in O(n) time where n = len(errors)

V5.0: Deep instrumentation for observability
"""
import logging
import time
from integration_coworker.graph.state import WorkflowState
from integration_coworker.security.redaction import (
    redact_secrets_in_list,
    sanitize_for_logging,
    normalize_error,
)
from integration_coworker.graph.node_trace import (
    log_step_event,
    step_context,
)

logger = logging.getLogger(__name__)


def handle_error(state: WorkflowState) -> WorkflowState:
    """
    Handle errors that occur during workflow execution.
    
    Reads: errors, plan, run_id
    Writes: plan["failed"], plan["error_summary"], plan["error_count"],
            plan["_internal"]["handling_error"], errors (redacted in-place), completed_steps
    
    Contract per Appendix C.3.17 + Production Hardening + OWASP:
    - Sets plan["failed"] = True
    - Redacts secrets from state.errors in-place BEFORE any logging
    - Sanitizes for log-injection (CR/LF removal) BEFORE logging
    - Sets stable error summary for downstream consumers
    - Does NOT write to DB or disk (pure transformation)
    - Is idempotent: re-entrancy guard prevents duplicate processing
    
    Guarantees:
    - Never loops: explicit _internal check prevents re-entrancy
    - Never hangs: no I/O, pure computation
    - Never swallows silently: logs with context
    - Never leaks secrets: redacts before logging/returning
    - No log injection: sanitizes CR/LF before logging
    - Idempotent: multiple calls produce same result
    - Returns terminal state: plan["failed"]=True is definitive
    """
    node_name = "handle_error"
    run_id = state.run_id
    
    # Re-entrancy guard: use plan["_internal"] to avoid polluting user-facing plan
    internal = state.plan.setdefault("_internal", {})
    if internal.get("handling_error"):
        # Already processed - idempotent return
        log_step_event(
            "node.step.skip",
            node_name=node_name,
            step="idempotent_skip",
            run_id=run_id,
            reason="already_handled",
        )
        logger.debug(
            "handle_error called again (idempotent skip)",
            extra={"run_id": state.run_id}
        )
        return state
    
    # Mark as handling (before any other processing)
    internal["handling_error"] = True
    error_count = len(state.errors) if state.errors else 0
    
    log_step_event(
        "node.step.start",
        node_name=node_name,
        step="handle_errors",
        run_id=run_id,
        error_count=error_count,
    )
    
    # CRITICAL: Redact secrets from errors in-place BEFORE any logging
    # Per OWASP: "Do not log" access tokens, passwords, encryption keys, etc.
    # This also sanitizes for log-injection (CR/LF removal)
    with step_context(node_name, "redact_secrets", run_id=run_id) as ctx:
        if state.errors:
            redact_secrets_in_list(state.errors)
    
    # Set failure flags
    state.plan["failed"] = True
    state.plan["error_count"] = error_count
    
    # Build stable error summary for CLI/API consumers
    # Summary is already sanitized since it comes from redacted errors
    with step_context(node_name, "build_summary", run_id=run_id) as ctx:
        if state.errors:
            # Take first error as primary, normalize it (handles str/dict/etc.)
            # Note: redact_secrets_in_list already normalized all errors to strings
            primary_error = state.errors[0]
            # Double-check normalization in case errors were added after redaction
            if not isinstance(primary_error, str):
                primary_error = normalize_error(primary_error)
            if len(primary_error) > 200:
                primary_error = primary_error[:197] + "..."
            state.plan["error_summary"] = primary_error
            if len(state.errors) > 1:
                state.plan["error_summary"] += f" (+{len(state.errors) - 1} more)"
        else:
            state.plan["error_summary"] = "Unknown error (no details captured)"
    
    # Log with structured context (logger.error, NOT logger.exception)
    # We don't have an active exception here - this is post-hoc processing
    # Summary is already redacted and sanitized from above
    logger.error(
        "Workflow error handled",
        extra={
            "run_id": state.run_id,
            "error_count": error_count,
            # Summary already sanitized (came from redacted errors)
            "error_summary": state.plan["error_summary"],
            "provider_code": state.provider_code,
        }
    )
    
    # Mark as completed
    if "handle_error" not in state.completed_steps:
        state.completed_steps.append("handle_error")
    
    log_step_event(
        "node.step.end",
        node_name=node_name,
        step="handle_complete",
        run_id=run_id,
        error_count=error_count,
        summary_length=len(state.plan.get("error_summary", "")),
    )
    
    return state

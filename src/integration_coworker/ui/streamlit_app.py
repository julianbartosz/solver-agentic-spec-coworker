"""
Streamlit UI for the Agentic Integration Co-Worker.

Provides:
- Multi-panel interface (Inputs / Run View / Artifacts / Logs)
- Real-time run status and progress tracking
- Error capture with interactive recovery (Retry/Skip/Restart)
- Artifact browser with code highlighting
- Graph trace visualization
- System Status preflight panel (DATABASE_URL, OPENAI_API_KEY, Postgres, Redis)
- Fresh Reset for clean reruns
- Advanced Options (timeout, sandbox gates, live tests)

V3.1 additions:
- Integrated with harness module for production parity
- Subprocess-based runner with reliable timeout (kills stuck LLM/DB calls)
- Dynamic table discovery for fresh reset (no hardcoded table lists)

V4.0 additions (Production Hardening):
- Uses harness runner for subprocess isolation and timeout enforcement
- st.status for long-running progress UI (not just spinner)
- Advanced Options (timeout, sandbox gates, live tests) actually wired through

V5.0 additions (Full Production Hardening):
- P1: Feedback Interface (👍/👎 on Run Status with LangSmith sync)
- P1: Resume Capability (Resume interrupted runs from checkpoints)
- P1: Cache Management (View stats, clear patterns, Redis health)
- P1: Artifact Cleanup (Purge old artifacts by age)
- P2: KG Search (Graph traversal queries by entity/endpoint/pattern)
- P2: Confidence Display (Confidence scores on workflow templates)
- P2: Full Config Validation (validate_startup_config with warnings)
- P3: LangSmith Sync (Bidirectional feedback synchronization)
- P3: Enhanced Cache Stats (Hit rate, bypass rate, local metrics)
- P3: Full System Status (Mirror CLI 'status' command)
"""
import streamlit as st
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import traceback
import os


# -----------------------------------------------------------------
# Session State Initialization
# -----------------------------------------------------------------
def _init_session_state() -> None:
    """Initialize Streamlit session state with default values."""
    defaults = {
        "last_result": None,
        "last_result_dict": None,  # V4: For harness subprocess results
        "last_error": None,
        "run_history": [],
        "pending_recovery_action": None,
        "is_running": False,
        "current_run_id": None,
        # V3.1: Preflight and advanced options
        "preflight_result": None,
        "timeout_seconds": 600,  # 10 min default
        "sandbox_gates": ["ruff", "mypy", "bandit", "pytest", "coverage"],
        "enable_live_tests": False,
        # V3.2: Showcase mode and KG tracking
        "showcase_mode": False,
        "showcase_progress": [],  # List of {spec, status, duration, error}
        "showcase_current_idx": 0,
        "kg_before": None,  # KG state before run
        "kg_after": None,   # KG state after run
        "node_timings": {},  # Node execution timings
        "parallel_speedup": None,  # Calculated speedup from parallel execution
        # V5: HITL approval gate
        "pending_approval": None,  # Dict with run params awaiting approval
        "hitl_approved": False,    # Whether HITL approval was granted
        "approved_run_params": None,  # Params to run after approval
        # V5.1: HITL Review Dialog (PR #4)
        "pending_review_kind": None,  # "code" | "sandbox" | None
        "review_artifact_refs": None,  # Dict of artifact refs for dialog
        "review_decisions": {},  # {"code": {...}, "sandbox": {...}}
        # V5.0: Production Hardening
        "feedback_comment": "",    # Comment for feedback submission
        "cache_clear_pattern": "", # Pattern for cache clearing
        "artifact_purge_days": 30, # Days for artifact purge
        "kg_search_entity": "",    # KG search entity
        "kg_search_endpoint": "",  # KG search endpoint
        "kg_search_pattern": "",   # KG search pattern
        "kg_search_results": None, # KG search results cache
        "langsmith_sync_days": 7,  # Days for LangSmith sync
        "full_system_status": None,# Full system status cache
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


# -----------------------------------------------------------------
# HITL Review Dialog Handler (PR #4)
# -----------------------------------------------------------------
def _handle_pending_review() -> None:
    """
    Handle pending HITL review dialog at TOP-LEVEL of app render.
    
    CRITICAL: Per @st.dialog contract (https://docs.streamlit.io/develop/api-reference/execution-flow/st.dialog):
    - Only ONE dialog function may be called per script run
    - Dialog must be called at top-level (not nested in tabs/columns/expanders)
    - Dialog function is implicitly invoked once defined with @st.dialog
    - st.rerun() is the ONLY way to close/dismiss the dialog
    
    Per @st.rerun contract (https://docs.streamlit.io/develop/api-reference/execution-flow/st.rerun):
    - st.rerun() immediately reruns the entire script
    - ONLY call st.rerun() AFTER persisting user decision to session_state
    - Never call during intermediate UI interactions
    
    This function implements a simple gate:
    - IF pending_review_kind exists: show dialog once, return early
    - ELSE: continue to normal render
    
    The dialog internally handles:
    - Rendering review content (bounded summaries)
    - Collecting user decision (approve/reject/feedback)
    - Persisting decision to session_state.review_decisions
    - Calling st.rerun() to dismiss and resume normal flow
    """
    pending_kind = st.session_state.get("pending_review_kind")
    if not pending_kind:
        # No pending review - continue to normal render
        return
    
    run_id = st.session_state.get("current_run_id", "unknown")
    artifact_refs = st.session_state.get("review_artifact_refs", {})
    
    # Import dialog function lazily (streamlit is optional dependency)
    try:
        from integration_coworker.ui.review_dialog import show_review_dialog
    except ImportError:
        st.error("Review dialog not available. Install with: pip install '.[ui]'")
        # Clear pending state to avoid infinite error loop
        st.session_state.pending_review_kind = None
        return
    
    # Show dialog - this is the ONLY dialog call per script run
    # Dialog handles its own state management and calls st.rerun() after decision
    decision = show_review_dialog(
        run_id=run_id,
        pending_kind=pending_kind,
        artifact_refs=artifact_refs,
    )
    
    # If decision was submitted, the dialog already called st.rerun()
    # If we get here, dialog is still open - we should not continue rendering
    # The early return ensures no other UI is rendered while dialog is active
    if decision:
        # Persist decision to session state
        decisions = st.session_state.get("review_decisions", {})
        decisions[pending_kind] = decision
        st.session_state.review_decisions = decisions
        # Clear pending state BEFORE rerun
        st.session_state.pending_review_kind = None
        st.session_state.review_artifact_refs = None
        # Rerun to dismiss dialog and resume normal flow
        st.rerun()
    
    # Dialog is open but no decision yet - stop here
    # This prevents rendering other UI elements while dialog is active
    st.stop()


# -----------------------------------------------------------------
# Path Constants (resolved at import time for stability)
# -----------------------------------------------------------------
_UI_FILE = Path(__file__).resolve()
_REPO_ROOT = _UI_FILE.parents[3]  # src/integration_coworker/ui -> repo root
STRIPE_SPEC = _REPO_ROOT / "specs" / "stripe_api.json"

# Max upload size for spec files
MAX_UPLOAD_MB = 50

# Task presets for common integration scenarios
TASK_PRESETS = {
    "Custom": "",
    "Checkout Flow": (
        "Implement a minimal Stripe Checkout flow: create a Checkout Session, "
        "handle success/cancel redirects, and include webhook verification with a test harness."
    ),
    "Customer Search": (
        "Implement customer search with pagination, idempotency keys, "
        "and structured error handling."
    ),
    "Refund Processing": (
        "Implement refund creation and retrieval with proper error handling, "
        "idempotency, and webhook notification for refund status changes."
    ),
    "Subscription Management": (
        "Implement subscription lifecycle: create subscription, update billing, "
        "cancel with proration, and handle subscription webhooks."
    ),
}

# Available sandbox gates
ALL_SANDBOX_GATES = ["ruff", "mypy", "bandit", "pytest", "coverage", "contract_tests", "live_tests"]

# Showcase specs for batch integration runs
SHOWCASE_SPECS = [
    ("stripe_api.json", "Create a checkout session for a one-time payment"),
    ("twilio_messaging_v1.json", "Send an SMS message to a phone number"),
    ("github_api.json", "Create a new issue in a repository"),
]

QUICK_SHOWCASE_SPECS = SHOWCASE_SPECS[:3]  # First 3 for quick mode

# Extended showcase (all 15 specs):
# FULL_SHOWCASE_SPECS = [
#     ("stripe_api.json", "Create a checkout session for a one-time payment"),
#     ("twilio_messaging_v1.json", "Send an SMS message to a phone number"),
#     ("github_api.json", "Create a new issue in a repository"),
#     ("slack_api.yaml", "Send a message to a Slack channel"),
#     ("openai_api.yaml", "Create a chat completion with GPT-4"),
#     ("spotify_api.yaml", "Search for tracks by artist name"),
#     ("zoom_api.yaml", "Create a new meeting with participants"),
#     ("mailchimp_api.yaml", "Add a subscriber to a mailing list"),
#     ("asana_api.yaml", "Create a new task in a project"),
#     ("box_api.yaml", "Upload a file to a folder"),
#     ("circleci_api.yaml", "Trigger a pipeline build"),
#     ("digitalocean_api.yaml", "Create a new droplet"),
#     ("plaid_api.yaml", "Link a bank account"),
#     ("petstore_v3.json", "Add a new pet to the store"),
#     ("httpbin_api.json", "Make a POST request with JSON body"),
# ]


# -----------------------------------------------------------------
# Secrets Loading
# -----------------------------------------------------------------
def _apply_secrets() -> None:
    """
    Bridge st.secrets to os.environ for coworker config compatibility.
    
    Per Streamlit docs, secrets are accessible via st.secrets OR as env vars.
    The coworker reads os.environ directly, so we use setdefault to avoid
    overwriting any explicitly-set env vars.
    
    Ref: https://docs.streamlit.io/develop/concepts/connections/secrets-management
    
    Secrets file location:
    - Local dev: ~/.streamlit/secrets.toml
    - Per-app: <app_dir>/.streamlit/secrets.toml
    - Streamlit Cloud: Set via dashboard
    
    Example secrets.toml:
        OPENAI_API_KEY = "sk-..."
        DATABASE_URL = "postgresql://user:pass@host:5432/db"
        REDIS_URL = "redis://localhost:6379"
    """
    import os
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DATABASE_URL", "REDIS_URL"):
        try:
            val = st.secrets.get(key, None)
        except Exception:
            val = None
        if val:
            os.environ.setdefault(key, str(val))


# -----------------------------------------------------------------
# Preflight Checks
# -----------------------------------------------------------------
def _run_preflight() -> Dict[str, Any]:
    """
    Run preflight checks using the harness module.
    
    Returns dict suitable for display in System Status panel.
    """
    try:
        from integration_coworker.harness.preflight import run_preflight_checks
        result = run_preflight_checks()
        return {
            "checks": [
                {"name": c.name, "passed": c.passed, "message": c.message}
                for c in result.checks
            ],
            "all_passed": result.all_passed,
            "critical_failed": not result.all_passed,  # Use inverse of all_passed
        }
    except ImportError:
        # Harness not available, fall back to basic checks
        checks = []
        
        # Check DATABASE_URL
        db_url = os.environ.get("DATABASE_URL", "")
        checks.append({
            "name": "DATABASE_URL",
            "passed": bool(db_url),
            "message": "Set" if db_url else "Not set - required for persistence",
        })
        
        # Check OPENAI_API_KEY
        api_key = os.environ.get("OPENAI_API_KEY", "")
        checks.append({
            "name": "OPENAI_API_KEY",
            "passed": api_key.startswith("sk-"),
            "message": "Valid format" if api_key.startswith("sk-") else "Missing or invalid",
        })
        
        all_passed = all(c["passed"] for c in checks)
        return {"checks": checks, "all_passed": all_passed, "critical_failed": not all_passed}


# -----------------------------------------------------------------
# Production Hardening Helper Functions (V5.0)
# -----------------------------------------------------------------

def _create_feedback(run_id: str, is_positive: bool, comment: Optional[str] = None) -> bool:
    """
    Create feedback for a run (P1 Feedback Interface).
    
    Uses langsmith_sync.create_feedback which stores locally and optionally
    syncs to LangSmith if configured.
    """
    try:
        from integration_coworker.feedback.langsmith_sync import create_feedback
        from integration_coworker.domain.models import FeedbackType
        
        score = 1.0 if is_positive else 0.0
        create_feedback(
            run_id=run_id,
            score=score,
            feedback_type=FeedbackType.THUMBS,
            comment=comment,
        )
        return True
    except Exception as e:
        st.error(f"Failed to create feedback: {e}")
        return False


def _get_resumable_runs() -> List[Dict[str, Any]]:
    """
    Get list of runs that can be resumed (P1 Resume Capability).
    
    Returns runs with checkpoints that are either running or failed.
    """
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        engine = get_engine_type()
        
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT DISTINCT rs.run_id, rs.provider_code, rs.status, rs.started_at,
                               (SELECT MAX(created_at) FROM integration_gold.run_checkpoints 
                                WHERE run_id = rs.run_id) as last_checkpoint
                        FROM integration_gold.run_status rs
                        WHERE rs.status IN ('running', 'failed', 'interrupted')
                          AND EXISTS (SELECT 1 FROM integration_gold.run_checkpoints 
                                      WHERE run_id = rs.run_id)
                        ORDER BY rs.started_at DESC
                        LIMIT 10
                    """)
                    return [
                        {"run_id": r[0], "provider_code": r[1], "status": r[2], 
                         "started_at": r[3], "last_checkpoint": r[4]}
                        for r in cur.fetchall()
                    ]
        else:
            # SQLite fallback
            from integration_coworker.persistence.db import get_connection
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT DISTINCT rs.run_id, rs.provider_code, rs.status, rs.started_at
                FROM run_status rs
                WHERE rs.status IN ('running', 'failed', 'interrupted')
                  AND EXISTS (SELECT 1 FROM run_checkpoints WHERE run_id = rs.run_id)
                ORDER BY rs.started_at DESC
                LIMIT 10
            """)
            result = [
                {"run_id": r[0], "provider_code": r[1], "status": r[2], "started_at": r[3]}
                for r in cur.fetchall()
            ]
            conn.close()
            return result
    except Exception as e:
        st.warning(f"Could not fetch resumable runs: {e}")
        return []


def _resume_run(run_id: str) -> Dict[str, Any]:
    """
    Resume an interrupted run (P1 Resume Capability).
    
    Returns result dict from recovery.resume_run().
    """
    try:
        from integration_coworker.api.recovery import resume_run
        result = resume_run(run_id)
        return {"success": True, "result": result}
    except ValueError as e:
        return {"success": False, "error": f"No checkpoint found: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_llm_cache_stats() -> Optional[Dict[str, Any]]:
    """
    Get LLM cache statistics (P1 Cache Management, P3 Enhanced Stats).
    
    Returns stats from cache.get_stats() and cache.get_local_metrics().
    """
    try:
        from integration_coworker.llm.cache import get_llm_cache
        
        cache = get_llm_cache()
        
        # Basic availability check
        is_available = cache.is_available()
        
        result = {
            "available": is_available,
            "stats": None,
            "metrics": None,
        }
        
        if is_available:
            # Get stats (CacheStats object)
            try:
                stats = cache.get_stats()
                result["stats"] = {
                    "hits": getattr(stats, "hits", 0),
                    "misses": getattr(stats, "misses", 0),
                    "size_bytes": getattr(stats, "size_bytes", 0),
                    "entry_count": getattr(stats, "entry_count", 0),
                }
            except Exception:
                pass
            
            # Get local metrics (CacheMetrics object) - P3 Enhanced Stats
            try:
                metrics = cache.get_local_metrics()
                result["metrics"] = {
                    "requests": getattr(metrics, "requests", 0),
                    "hits": getattr(metrics, "hits", 0),
                    "misses": getattr(metrics, "misses", 0),
                    "bypasses": getattr(metrics, "bypasses", 0),
                    "errors": getattr(metrics, "errors", 0),
                    "hit_rate": getattr(metrics, "hit_rate", 0.0),
                    "bypass_rate": getattr(metrics, "bypass_rate", 0.0),
                }
            except Exception:
                pass
        
        return result
    except ImportError:
        return {"available": False, "error": "Cache module not available"}
    except Exception as e:
        return {"available": False, "error": str(e)}


def _clear_llm_cache(pattern: Optional[str] = None) -> Dict[str, Any]:
    """
    Clear LLM cache entries (P1 Cache Management).
    
    Args:
        pattern: Optional glob pattern to match keys (e.g., "stripe:*")
        
    Returns:
        Dict with deleted count or error.
    """
    try:
        from integration_coworker.llm.cache import get_llm_cache
        
        cache = get_llm_cache()
        
        if not cache.is_available():
            return {"success": False, "error": "Cache not available (Redis not connected)"}
        
        deleted = cache.clear(pattern)
        return {"success": True, "deleted": deleted}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _purge_old_artifacts(days: int, dry_run: bool = False) -> Dict[str, Any]:
    """
    Purge artifacts older than N days (P1 Artifact Cleanup).
    
    Uses FilesystemArtifactStore.purge_older_than().
    """
    try:
        from integration_coworker.persistence.artifacts.fs import get_artifact_store
        
        store = get_artifact_store()
        purged_runs = store.purge_older_than(days, dry_run=dry_run)
        
        return {
            "success": True,
            "purged_count": len(purged_runs),
            "purged_runs": purged_runs[:20],  # Limit display
            "dry_run": dry_run,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _kg_search(
    entity: Optional[str] = None,
    endpoint: Optional[str] = None,
    pattern: Optional[str] = None,
    provider: Optional[str] = None,
    max_depth: int = 3,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Search Knowledge Graph (P2 KG Search).
    
    Uses find_related_tasks_via_graph for graph traversal queries.
    """
    try:
        from integration_coworker.kg import find_related_tasks_via_graph, get_kg_node_count
        
        # Get KG stats first
        kg_stats = get_kg_node_count()
        
        # Run the query
        matches = find_related_tasks_via_graph(
            entity_name=entity,
            endpoint_path=endpoint,
            pattern_key=pattern,
            provider_code=provider,
            max_depth=max_depth,
            top_k=limit,
        )
        
        return {
            "success": True,
            "kg_stats": kg_stats,
            "results": [
                {
                    "task_key": m.task_key,
                    "task_description": m.task_description,
                    "provider_code": m.provider_code,
                    "graph_distance": m.graph_distance,
                    "associated_templates": m.associated_templates,
                }
                for m in matches
            ],
        }
    except ImportError:
        return {"success": False, "error": "KG module not available"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_template_confidence(template_key: str) -> Optional[float]:
    """
    Get confidence score for a workflow template (P2 Confidence Display).
    
    Uses confidence.get_confidence_for_template().
    """
    try:
        from integration_coworker.feedback.confidence import get_confidence_for_template
        return get_confidence_for_template(template_key)
    except Exception:
        return None


def _validate_config() -> Dict[str, Any]:
    """
    Run full config validation (P2 Full Config Validation).
    
    Uses startup.validate_startup_config() to get detailed validation results.
    """
    try:
        from integration_coworker.config.startup import validate_startup_config, get_config_summary
        
        result = validate_startup_config(fail_fast=False, emit_summary=False)
        summary = get_config_summary()
        
        return {
            "success": result.valid,
            "errors": result.errors,
            "warnings": result.warnings,
            "config_summary": summary,
        }
    except ImportError:
        # Fallback if startup module not available
        return {
            "success": True,
            "errors": [],
            "warnings": ["Config validation module not available"],
            "config_summary": {},
        }
    except Exception as e:
        return {
            "success": False,
            "errors": [str(e)],
            "warnings": [],
            "config_summary": {},
        }


def _sync_langsmith_feedback(days: int = 7) -> Dict[str, Any]:
    """
    Sync feedback with LangSmith (P3 LangSmith Sync).
    
    Pulls feedback from LangSmith to local DB. Push happens automatically
    when creating feedback via create_feedback(also_langsmith=True).
    """
    try:
        from integration_coworker.feedback.langsmith_sync import (
            sync_langsmith_feedback,
            is_langsmith_available,
        )
        
        if not is_langsmith_available():
            return {"success": False, "error": "LangSmith not configured (set LANGSMITH_API_KEY)"}
        
        result = sync_langsmith_feedback(days=days)
        
        # API returns: synced (pulled from LS), skipped, errors, templates_updated
        return {
            "success": True,
            "synced": result.get("synced", 0),
            "skipped": result.get("skipped", 0),
            "errors": result.get("errors", 0),
            "templates_updated": result.get("templates_updated", []),
        }
    except ImportError:
        return {"success": False, "error": "LangSmith module not available"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_full_system_status() -> Dict[str, Any]:
    """
    Get full system status (P3 Full System Status).
    
    Mirrors the CLI 'status' command output.
    """
    try:
        from integration_coworker.config import get_settings
        
        settings = get_settings()
        warnings = settings.validate()
        
        status = {
            "database": {},
            "llm": {},
            "embeddings": {},
            "cache": {},
            "warnings": warnings,
        }
        
        # Database status
        if settings.database.use_sqlite:
            status["database"] = {
                "engine": "SQLite (test mode)",
                "path": str(settings.database.sqlite_path),
                "connected": True,
            }
        else:
            status["database"]["engine"] = "PostgreSQL + pgvector"
            try:
                from integration_coworker.persistence.postgres import check_connection, check_pgvector
                status["database"]["connected"] = check_connection()
                status["database"]["pgvector"] = check_pgvector() if status["database"]["connected"] else False
            except Exception as e:
                status["database"]["connected"] = False
                status["database"]["error"] = str(e)
        
        # LLM status
        if settings.llm.use_mock:
            status["llm"] = {"mode": "Mock", "configured": True}
        elif settings.llm.api_key:
            status["llm"] = {
                "mode": "Real",
                "model": settings.llm.default_model,
                "configured": True,
            }
        else:
            status["llm"] = {"mode": "Not configured", "configured": False}
        
        # Embeddings status
        status["embeddings"] = {
            "model": settings.embedding.model,
            "dimensions": settings.embedding.dimensions,
            "batch_size": settings.embedding.batch_size,
        }
        
        # Cache status (from P1/P3)
        cache_info = _get_llm_cache_stats()
        status["cache"] = cache_info
        
        return status
    except Exception as e:
        return {"error": str(e)}


# -----------------------------------------------------------------
# P1: HITL Integration Helper Functions (V5.1)
# -----------------------------------------------------------------

def _get_hitl_status(run_id: str) -> Dict[str, Any]:
    """
    Check if a workflow is paused at HITL gate.
    
    Uses runtime.is_workflow_paused() and runtime.get_interrupt_payload().
    """
    try:
        import asyncio
        from integration_coworker.graph.runtime import (
            is_workflow_paused,
            get_interrupt_payload,
        )
        
        paused = is_workflow_paused(run_id)
        
        if not paused:
            return {"paused": False, "run_id": run_id}
        
        # Get interrupt payload for details
        payload = asyncio.run(get_interrupt_payload(run_id))
        
        return {
            "paused": True,
            "run_id": run_id,
            "gate": payload.get("gate") if payload else "unknown",
            "payload": payload,
        }
    except Exception as e:
        return {"paused": False, "error": str(e)}


def _submit_hitl_approval(run_id: str, approved: bool, comment: Optional[str] = None) -> Dict[str, Any]:
    """
    Submit HITL approval/rejection decision.
    
    Uses runtime.resume_with_approval().
    """
    try:
        from integration_coworker.graph.runtime import resume_with_approval
        
        decision = {
            "approved": approved,
            "comment": comment,
        } if comment else approved
        
        result = resume_with_approval(run_id, decision)
        
        return {
            "success": True,
            "run_id": run_id,
            "approved": approved,
            "completed_steps": getattr(result, "completed_steps", []),
        }
    except ValueError as e:
        return {"success": False, "error": f"Invalid state: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_pending_hitl_runs() -> List[Dict[str, Any]]:
    """
    Get list of runs currently paused at HITL gates.
    
    Queries run_status for running status and checks each for HITL pause.
    """
    try:
        from integration_coworker.persistence.db import get_engine_type
        from integration_coworker.graph.runtime import is_workflow_paused
        
        engine = get_engine_type()
        pending = []
        
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT run_id, provider_code, started_at
                        FROM integration_gold.run_status
                        WHERE status = 'running'
                        ORDER BY started_at DESC
                        LIMIT 10
                    """)
                    runs = cur.fetchall()
        else:
            from integration_coworker.persistence.db import get_connection
            # Use context manager to prevent connection leaks
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT run_id, provider_code, started_at
                    FROM run_status
                    WHERE status = 'running'
                    ORDER BY started_at DESC
                    LIMIT 10
                """)
                runs = cur.fetchall()
        
        # Check each running run for HITL pause
        for run in runs:
            run_id = run[0]
            try:
                if is_workflow_paused(run_id):
                    pending.append({
                        "run_id": run_id,
                        "provider_code": run[1],
                        "started_at": run[2],
                    })
            except Exception as e:
                # Log but continue - don't block other runs
                import logging
                logging.getLogger(__name__).debug(f"Failed to check HITL status for {run_id}: {e}")
        
        return pending
    except Exception as e:
        return []


# -----------------------------------------------------------------
# P1: Deep Health Check Helper Functions (V5.1)
# -----------------------------------------------------------------

def _run_deep_health_check(check_llm: bool = True) -> Dict[str, Any]:
    """
    Run comprehensive health checks including actual LLM API calls.
    
    Mirrors CLI 'health --check-llm' functionality.
    """
    import os
    try:
        from integration_coworker.config import get_settings
        
        settings = get_settings()
        checks = {}
        
        # Database check
        if settings.database.use_sqlite:
            checks["database"] = {"status": "ok", "message": "SQLite configured"}
        else:
            try:
                from integration_coworker.persistence.postgres import check_connection, check_pgvector
                if check_connection():
                    checks["database"] = {"status": "ok", "message": "PostgreSQL connected"}
                    if check_pgvector():
                        checks["pgvector"] = {"status": "ok", "message": "pgvector available"}
                    else:
                        checks["pgvector"] = {"status": "warning", "message": "pgvector not installed"}
                else:
                    checks["database"] = {"status": "error", "message": "Connection failed"}
            except Exception as e:
                checks["database"] = {"status": "error", "message": str(e)[:50]}
        
        # LLM Keys check
        openai_key = os.getenv("OPENAI_API_KEY", "")
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        
        if settings.llm.use_mock:
            checks["llm_keys"] = {"status": "ok", "message": "Mock mode"}
        elif openai_key and anthropic_key:
            checks["llm_keys"] = {"status": "ok", "message": "Both keys configured"}
        elif openai_key or anthropic_key:
            checks["llm_keys"] = {"status": "warning", "message": "Only one provider configured"}
        else:
            checks["llm_keys"] = {"status": "error", "message": "No API keys"}
        
        # LangSmith check
        tracing = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
        ls_key = os.getenv("LANGCHAIN_API_KEY", "") or os.getenv("LANGSMITH_API_KEY", "")
        
        if tracing and ls_key:
            checks["langsmith"] = {"status": "ok", "message": f"Enabled: {os.getenv('LANGCHAIN_PROJECT', 'default')}"}
        elif tracing:
            checks["langsmith"] = {"status": "warning", "message": "Tracing on but no key"}
        else:
            checks["langsmith"] = {"status": "skip", "message": "Tracing disabled"}
        
        # Archetype validation (mirrors CLI health check)
        try:
            from integration_coworker.config import list_available_archetypes
            archetypes = list_available_archetypes()
            checks["archetypes"] = {"status": "ok", "message": f"{len(archetypes)} archetypes loaded"}
        except Exception as e:
            checks["archetypes"] = {"status": "error", "message": str(e)[:50]}
        
        # Actual LLM connectivity test
        if check_llm and not settings.llm.use_mock:
            checks["llm_connectivity"] = {}
            
            # Test OpenAI
            if openai_key:
                try:
                    from langchain_openai import ChatOpenAI
                    llm = ChatOpenAI(model="gpt-4o-mini", max_tokens=10, timeout=10)
                    llm.invoke("Say 'OK'")
                    checks["llm_connectivity"]["openai"] = "ok"
                except Exception as e:
                    checks["llm_connectivity"]["openai"] = f"error: {str(e)[:30]}"
            
            # Test Anthropic
            if anthropic_key:
                try:
                    from langchain_anthropic import ChatAnthropic
                    llm = ChatAnthropic(model="claude-sonnet-4-5-20250929", max_tokens=10, timeout=10)
                    llm.invoke("Say 'OK'")
                    checks["llm_connectivity"]["anthropic"] = "ok"
                except ImportError:
                    checks["llm_connectivity"]["anthropic"] = "langchain-anthropic not installed"
                except Exception as e:
                    checks["llm_connectivity"]["anthropic"] = f"error: {str(e)[:30]}"
        
        return {"success": True, "checks": checks}
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------
# P1: Skip Failing Step Helper Functions (V5.1)
# -----------------------------------------------------------------

def _skip_failing_step(run_id: str, failed_step: str) -> Dict[str, Any]:
    """
    Skip a failing step and continue execution.
    
    Uses recovery.skip_failing_step() with dependency cascade analysis.
    """
    try:
        from integration_coworker.api.recovery import skip_failing_step, RecoveryContext
        from integration_coworker.persistence.checkpoints import load_checkpoint
        
        # Load checkpoint to get original context for potential retry fallback
        checkpoint = load_checkpoint(run_id)
        
        # Create recovery context with data from checkpoint if available
        spec_refs = []
        task_description = ""
        if checkpoint and hasattr(checkpoint, 'task'):
            spec_refs = getattr(checkpoint.task, 'spec_refs', []) or []
            task_description = getattr(checkpoint.task, 'task_description', '') or ''
        
        context = RecoveryContext(
            run_id=run_id,
            spec_refs=spec_refs,
            task_description=task_description,
            failed_step=failed_step,
        )
        
        result = skip_failing_step(context)
        
        return {
            "success": True,
            "skipped_step": failed_step,
            "completed_steps": result.completed_steps if result else [],
            "warnings": result.errors if result else [],
        }
    except ValueError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------
# P2: KG Dump/Export Helper Functions (V5.1)
# -----------------------------------------------------------------

def _export_kg_data(
    provider: Optional[str] = None,
    node_type: Optional[str] = None,
    include_edges: bool = True,
    include_steps: bool = True,
    limit: int = 100,
) -> Dict[str, Any]:
    """
    Export Knowledge Graph data for download.
    
    Returns structured data suitable for JSON export.
    """
    try:
        from integration_coworker.persistence.db import get_connection, get_engine_type
        
        engine = get_engine_type()
        is_postgres = engine == "postgres"
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            output = {
                "engine": engine,
                "filter": {"provider": provider, "node_type": node_type},
                "nodes": [],
                "edges": [],
                "workflow_steps": [],
            }
            
            # Build node query
            if is_postgres:
                base_query = """
                    SELECT id, node_type, provider_code, key, name, description, 
                           usage_count, confidence_score
                    FROM kg.nodes
                """
                param_placeholder = "%s"
            else:
                base_query = """
                    SELECT id, node_type, provider_code, key, name, description,
                           usage_count, confidence_score
                    FROM kg_nodes
                """
                param_placeholder = "?"
            
            where_clauses = []
            params = []
            
            if provider:
                where_clauses.append(f"provider_code = {param_placeholder}")
                params.append(provider)
            
            if node_type:
                where_clauses.append(f"node_type = {param_placeholder}")
                params.append(node_type)
            
            if where_clauses:
                base_query += " WHERE " + " AND ".join(where_clauses)
            
            base_query += f" ORDER BY node_type, key LIMIT {param_placeholder}"
            params.append(limit)
            
            cur.execute(base_query, params)
            nodes = cur.fetchall()
            
            for node in nodes:
                output["nodes"].append({
                    "id": node[0],
                    "type": node[1],
                    "provider_code": node[2],
                    "key": node[3],
                    "name": node[4],
                    "description": node[5],
                    "usage_count": node[6],
                    "confidence_score": node[7],
                })
            
            # Get edges
            if include_edges and nodes:
                node_ids = [n[0] for n in nodes]
                placeholders = ",".join([param_placeholder] * len(node_ids))
                
                if is_postgres:
                    edge_query = f"""
                        SELECT e.id, e.src_node_id, e.dst_node_id, e.relation_type, e.weight,
                               src.key as src_key, dst.key as dst_key
                        FROM kg.edges e
                        JOIN kg.nodes src ON e.src_node_id = src.id
                        JOIN kg.nodes dst ON e.dst_node_id = dst.id
                        WHERE e.src_node_id IN ({placeholders}) OR e.dst_node_id IN ({placeholders})
                    """
                else:
                    edge_query = f"""
                        SELECT e.id, e.src_node_id, e.dst_node_id, e.relation_type, e.weight,
                               src.key as src_key, dst.key as dst_key
                        FROM kg_edges e
                        JOIN kg_nodes src ON e.src_node_id = src.id
                        JOIN kg_nodes dst ON e.dst_node_id = dst.id
                        WHERE e.src_node_id IN ({placeholders}) OR e.dst_node_id IN ({placeholders})
                    """
                
                cur.execute(edge_query, node_ids + node_ids)
                edges = cur.fetchall()
                
                for edge in edges:
                    output["edges"].append({
                        "id": edge[0],
                        "src_node_id": edge[1],
                        "dst_node_id": edge[2],
                        "relation_type": edge[3],
                        "weight": edge[4],
                        "src_key": edge[5],
                        "dst_key": edge[6],
                    })
            
            # Get workflow steps
            if include_steps:
                template_ids = [n[0] for n in nodes if n[1] == "workflow_template"]
                if template_ids:
                    placeholders = ",".join([param_placeholder] * len(template_ids))
                    
                    if is_postgres:
                        steps_query = f"""
                            SELECT s.id, s.template_node_id, s.step_key, s.step_type, 
                                   s.position, s.label
                            FROM kg.workflow_steps s
                            WHERE s.template_node_id IN ({placeholders})
                            ORDER BY s.template_node_id, s.position
                        """
                    else:
                        steps_query = f"""
                            SELECT s.id, s.template_node_id, s.step_key, s.step_type,
                                   s.position, s.label
                            FROM kg_workflow_steps s
                            WHERE s.template_node_id IN ({placeholders})
                            ORDER BY s.template_node_id, s.position
                        """
                    
                    cur.execute(steps_query, template_ids)
                    steps = cur.fetchall()
                    
                    for step in steps:
                        output["workflow_steps"].append({
                            "id": step[0],
                            "template_node_id": step[1],
                            "step_key": step[2],
                            "step_type": step[3],
                            "position": step[4],
                            "label": step[5],
                        })
            
            return {"success": True, "data": output}
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------
# P2: Database Initialization Helper Functions (V5.1)
# -----------------------------------------------------------------

def _initialize_database(seed_kg: bool = True) -> Dict[str, Any]:
    """
    Initialize database schema and optionally seed KG.
    
    Uses persistence.db.init_schema() and persistence.seed_kg.seed_knowledge_graph().
    """
    try:
        from integration_coworker.persistence.db import init_schema, get_engine_type
        
        engine = get_engine_type()
        result = {"success": True, "engine": engine, "steps": []}
        
        # Initialize schema
        init_schema()
        result["steps"].append("Schema initialized")
        
        # Check pgvector for Postgres
        if engine == "postgres":
            try:
                from integration_coworker.persistence.postgres import check_pgvector
                if check_pgvector():
                    result["steps"].append("pgvector available")
                else:
                    result["steps"].append("pgvector not installed (optional)")
            except Exception:
                pass
        
        # Seed KG
        if seed_kg:
            try:
                from integration_coworker.persistence.seed_kg import seed_knowledge_graph
                added, skipped = seed_knowledge_graph()
                if added > 0:
                    result["steps"].append(f"Seeded {added} workflow templates")
                elif skipped > 0:
                    result["steps"].append(f"KG already seeded ({skipped} templates)")
                else:
                    result["steps"].append("KG ready")
            except Exception as e:
                result["steps"].append(f"KG seed warning: {str(e)[:50]}")
        
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------
# P3: Cache Check Details Helper Functions (V5.1)
# -----------------------------------------------------------------

def _get_cache_connection_details() -> Dict[str, Any]:
    """
    Get detailed Redis cache connection information.
    
    Returns host, port, connection status, and latency.
    """
    import os
    import time
    
    redis_url = os.getenv("REDIS_URL", "")
    
    result = {
        "configured": bool(redis_url),
        "url": redis_url[:30] + "..." if len(redis_url) > 30 else redis_url,
        "connected": False,
        "latency_ms": None,
    }
    
    if not redis_url:
        return result
    
    try:
        from integration_coworker.llm.cache import get_llm_cache
        
        cache = get_llm_cache()
        
        # Check connection
        start = time.time()
        available = cache.is_available()
        latency = (time.time() - start) * 1000
        
        result["connected"] = available
        result["latency_ms"] = round(latency, 2)
        
        # Parse URL for display info
        if "://" in redis_url:
            # redis://host:port/db
            parts = redis_url.split("://")[1].split("/")[0]
            if "@" in parts:
                parts = parts.split("@")[1]  # Remove auth
            result["host_port"] = parts
        
        return result
    except Exception as e:
        result["error"] = str(e)[:50]
        return result


# -----------------------------------------------------------------
# P3: Run History Export Helper Functions (V5.1)
# -----------------------------------------------------------------

def _export_run_history(limit: int = 100) -> Dict[str, Any]:
    """
    Export run history as structured data for CSV download.
    """
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        engine = get_engine_type()
        
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            run_id, provider_code, task_slug, status,
                            started_at, ended_at, error_message
                        FROM integration_gold.run_status
                        ORDER BY started_at DESC
                        LIMIT %s
                    """, (limit,))
                    rows = cur.fetchall()
        else:
            from integration_coworker.persistence.db import get_connection
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT 
                    run_id, provider_code, task_slug, status,
                    started_at, ended_at, error_message
                FROM run_status
                ORDER BY started_at DESC
                LIMIT ?
            """, (limit,))
            rows = cur.fetchall()
            conn.close()
        
        # Convert to list of dicts
        runs = []
        for row in rows:
            runs.append({
                "run_id": row[0],
                "provider_code": row[1],
                "task_slug": row[2],
                "status": row[3],
                "started_at": str(row[4]) if row[4] else "",
                "ended_at": str(row[5]) if row[5] else "",
                "error_message": row[6] or "",
            })
        
        return {"success": True, "runs": runs}
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------
# P3: Template Feedback Aggregation Helper Functions (V5.1)
# -----------------------------------------------------------------

def _get_template_feedback_records(template_key: str) -> Dict[str, Any]:
    """
    Get all feedback records for a template with aggregation.
    """
    try:
        from integration_coworker.feedback.confidence import get_feedback_for_template
        
        records = get_feedback_for_template(template_key)
        
        # Aggregate stats
        total = len(records)
        positive = sum(1 for r in records if r.score >= 0.5)
        negative = total - positive
        avg_score = sum(r.score for r in records) / total if total > 0 else 0.0
        
        # Convert records to dicts
        record_dicts = []
        for r in records[:20]:  # Limit display
            # Safe enum value extraction - handle both Enum and string values
            fb_type = r.feedback_type.value if hasattr(r.feedback_type, 'value') else str(r.feedback_type or 'unknown')
            fb_source = r.source.value if hasattr(r.source, 'value') else str(r.source or 'unknown')
            
            record_dicts.append({
                "id": r.id,
                "run_id": r.run_id,
                "score": r.score,
                "feedback_type": fb_type,
                "comment": r.comment,
                "source": fb_source,
                "created_at": r.created_at,
            })
        
        return {
            "success": True,
            "template_key": template_key,
            "total": total,
            "positive": positive,
            "negative": negative,
            "avg_score": avg_score,
            "records": record_dicts,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_all_templates_with_feedback() -> List[Dict[str, Any]]:
    """
    Get all templates that have feedback records.
    """
    try:
        from integration_coworker.persistence.db import get_connection, get_engine_type
        
        engine = get_engine_type()
        is_postgres = engine == "postgres"
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            if is_postgres:
                cur.execute("""
                    SELECT 
                        n.key,
                        n.name,
                        n.confidence_score,
                        COUNT(f.id) as feedback_count,
                        AVG(f.score) as avg_score
                    FROM kg.nodes n
                    LEFT JOIN kg.feedback_records f ON n.key = f.template_key
                    WHERE n.node_type = 'workflow_template'
                    GROUP BY n.key, n.name, n.confidence_score
                    HAVING COUNT(f.id) > 0
                    ORDER BY feedback_count DESC
                    LIMIT 20
                """)
            else:
                cur.execute("""
                    SELECT 
                        n.key,
                        n.name,
                        n.confidence_score,
                        COUNT(f.id) as feedback_count,
                        AVG(f.score) as avg_score
                    FROM kg_nodes n
                    LEFT JOIN kg_feedback_records f ON n.key = f.template_key
                    WHERE n.node_type = 'workflow_template'
                    GROUP BY n.key, n.name, n.confidence_score
                    HAVING COUNT(f.id) > 0
                    ORDER BY feedback_count DESC
                    LIMIT 20
                """)
            
            rows = cur.fetchall()
            
            templates = []
            for row in rows:
                templates.append({
                    "key": row[0],
                    "name": row[1],
                    "confidence_score": row[2],
                    "feedback_count": row[3],
                    "avg_score": row[4],
                })
            
            return templates
    except Exception as e:
        return []


# -----------------------------------------------------------------
# P1: KG Query Helper Functions (V5.2)
# -----------------------------------------------------------------

def _kg_query(
    entity_name: Optional[str] = None,
    endpoint_path: Optional[str] = None,
    pattern_key: Optional[str] = None,
    provider_code: Optional[str] = None,
    max_depth: int = 3,
    limit: int = 10,
) -> Dict[str, Any]:
    """
    Query the Knowledge Graph using BFS graph traversal.
    
    Uses kg.find_related_tasks_via_graph().
    """
    try:
        from integration_coworker.kg import (
            find_related_tasks_via_graph,
            get_kg_node_count,
        )
        
        # Get KG stats first
        kg_stats = get_kg_node_count()
        
        if not kg_stats or sum(kg_stats.values()) == 0:
            return {"success": False, "error": "KG is empty. Run an integration with --persist first."}
        
        # Run the query
        matches = find_related_tasks_via_graph(
            entity_name=entity_name,
            endpoint_path=endpoint_path,
            pattern_key=pattern_key,
            provider_code=provider_code,
            max_depth=max_depth,
            top_k=limit,
        )
        
        # Convert to dicts
        results = []
        for match in matches:
            results.append({
                "task_key": match.task_key,
                "task_description": match.task_description,
                "provider_code": match.provider_code,
                "graph_distance": match.graph_distance,
                "path": match.path,
                "relation_types": match.relation_types,
                "associated_templates": match.associated_templates,
            })
        
        return {
            "success": True,
            "kg_stats": kg_stats,
            "results": results,
            "query": {
                "entity": entity_name,
                "endpoint": endpoint_path,
                "pattern": pattern_key,
                "provider": provider_code,
            },
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_kg_stats() -> Dict[str, Any]:
    """Get KG node counts by type. Returns {'_error': msg} on failure."""
    try:
        from integration_coworker.kg import get_kg_node_count
        result = get_kg_node_count()
        return result if result else {}
    except Exception as e:
        return {"_error": str(e)[:100]}


# -----------------------------------------------------------------
# P1: Feedback Submission Helper Functions (V5.2)
# -----------------------------------------------------------------

def _submit_feedback(
    run_id: str,
    score: float,
    feedback_type: str = "thumbs",
    comment: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Submit human feedback for a completed run.
    
    Uses feedback.langsmith_sync.create_feedback().
    """
    try:
        from integration_coworker.feedback.langsmith_sync import create_feedback
        from integration_coworker.domain.models import FeedbackType
        
        # Map feedback type string to enum
        fb_type_map = {
            "thumbs": FeedbackType.THUMBS,
            "thumbs_up": FeedbackType.THUMBS,
            "thumbs_down": FeedbackType.THUMBS,
            "score": FeedbackType.SCORE,
        }
        fb_type = fb_type_map.get(feedback_type, FeedbackType.THUMBS)
        
        record_id = create_feedback(
            run_id=run_id,
            score=score,
            feedback_type=fb_type,
            comment=comment,
            also_langsmith=True,
        )
        
        return {
            "success": True,
            "feedback_id": record_id,
            "run_id": run_id,
            "score": score,
            "feedback_type": feedback_type,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_runs_for_feedback(limit: int = 20) -> List[Dict[str, Any]]:
    """Get recent completed runs available for feedback."""
    try:
        from integration_coworker.persistence.db import get_connection, get_engine_type
        
        engine = get_engine_type()
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            if engine == "postgres":
                cur.execute("""
                    SELECT r.run_id, r.provider_code, r.task_slug, r.status, r.ended_at,
                           COUNT(f.id) as feedback_count
                    FROM integration_gold.run_status r
                    LEFT JOIN kg.feedback_records f ON r.run_id = f.run_id
                    WHERE r.status = 'completed'
                    GROUP BY r.run_id, r.provider_code, r.task_slug, r.status, r.ended_at
                    ORDER BY r.ended_at DESC NULLS LAST
                    LIMIT %s
                """, (limit,))
            else:
                cur.execute("""
                    SELECT r.run_id, r.provider_code, r.task_slug, r.status, r.ended_at,
                           COUNT(f.id) as feedback_count
                    FROM run_status r
                    LEFT JOIN kg_feedback_records f ON r.run_id = f.run_id
                    WHERE r.status = 'completed'
                    GROUP BY r.run_id, r.provider_code, r.task_slug, r.status, r.ended_at
                    ORDER BY r.ended_at DESC
                    LIMIT ?
                """, (limit,))
            
            rows = cur.fetchall()
            
            runs = []
            for row in rows:
                runs.append({
                    "run_id": row[0],
                    "provider_code": row[1] or "unknown",
                    "task_slug": row[2] or "no task",
                    "status": row[3],
                    "ended_at": str(row[4]) if row[4] else "",
                    "feedback_count": row[5] or 0,
                })
            
            return runs
    except Exception as e:
        return []


# -----------------------------------------------------------------
# P1: Run Status Detail Helper Functions (V5.2)
# -----------------------------------------------------------------

def _get_run_detail(run_id: str) -> Dict[str, Any]:
    """
    Get detailed status for a specific run.
    
    Returns comprehensive run information including checkpoints, artifacts, etc.
    """
    try:
        from integration_coworker.persistence.db import get_connection, get_engine_type
        from integration_coworker.persistence.checkpoints import load_checkpoint, get_completed_nodes
        
        engine = get_engine_type()
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            # Get run status
            if engine == "postgres":
                cur.execute("""
                    SELECT run_id, provider_code, task_slug, status,
                           started_at, ended_at, error_message
                    FROM integration_gold.run_status
                    WHERE run_id = %s
                """, (run_id,))
            else:
                cur.execute("""
                    SELECT run_id, provider_code, task_slug, status,
                           started_at, ended_at, error_message
                    FROM run_status
                    WHERE run_id = ?
                """, (run_id,))
            
            row = cur.fetchone()
            
            if not row:
                return {"success": False, "error": f"Run {run_id} not found"}
            
            result = {
                "success": True,
                "run_id": row[0],
                "provider_code": row[1],
                "task_slug": row[2],
                "status": row[3],
                "started_at": str(row[4]) if row[4] else None,
                "ended_at": str(row[5]) if row[5] else None,
                "error_message": row[6],
                "completed_nodes": [],
                "has_checkpoint": False,
                "artifacts": [],
                "feedback": [],
            }
            
            # Get completed nodes from checkpoint
            try:
                completed = get_completed_nodes(run_id)
                result["completed_nodes"] = completed or []
                checkpoint = load_checkpoint(run_id)
                result["has_checkpoint"] = checkpoint is not None
            except Exception as e:
                result["checkpoint_error"] = str(e)[:100]
            
            # Get artifacts
            try:
                from integration_coworker.persistence.artifacts.fs import get_artifact_store
                store = get_artifact_store()
                artifacts = store.list_artifacts(run_id)
                result["artifacts"] = [
                    {"name": a.name, "size": a.size, "type": a.content_type}
                    for a in artifacts[:10]
                ]
            except Exception as e:
                result["artifacts_error"] = str(e)[:100]
            
            # Get feedback records
            if engine == "postgres":
                cur.execute("""
                    SELECT id, score, feedback_type, comment, source, created_at
                    FROM kg.feedback_records
                    WHERE run_id = %s
                    ORDER BY created_at DESC
                """, (run_id,))
            else:
                cur.execute("""
                    SELECT id, score, feedback_type, comment, source, created_at
                    FROM kg_feedback_records
                    WHERE run_id = ?
                    ORDER BY created_at DESC
                """, (run_id,))
            
            feedback_rows = cur.fetchall()
            for fb_row in feedback_rows:
                result["feedback"].append({
                    "id": fb_row[0],
                    "score": fb_row[1],
                    "type": fb_row[2],
                    "comment": fb_row[3],
                    "source": fb_row[4],
                    "created_at": str(fb_row[5]) if fb_row[5] else None,
                })
            
            return result
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------
# P2: KG Confidence Audit Helper Functions (V5.2)
# -----------------------------------------------------------------

def _get_kg_confidence_list(
    provider: Optional[str] = None,
    threshold: Optional[float] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    Get list of templates with their confidence scores.
    """
    try:
        from integration_coworker.persistence.db import get_connection, get_engine_type, kg_table
        
        engine = get_engine_type()
        nodes_table = kg_table("nodes")
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            where_clauses = ["node_type = ?"]
            params: List[Any] = ["workflow_template"]
            
            if provider:
                where_clauses.append("provider_code = ?")
                params.append(provider)
            
            if threshold is not None:
                where_clauses.append("confidence_score < ?")
                params.append(threshold)
            
            where_sql = " AND ".join(where_clauses)
            
            # Adjust placeholders for postgres
            if engine == "postgres":
                where_sql = where_sql.replace("?", "%s")
            
            query = f"""
                SELECT key, name, provider_code, confidence_score, usage_count
                FROM {nodes_table}
                WHERE {where_sql}
                ORDER BY confidence_score ASC, usage_count DESC
                LIMIT ?
            """
            if engine == "postgres":
                query = query.replace("LIMIT ?", "LIMIT %s")
            
            params.append(limit)
            cur.execute(query, params)
            rows = cur.fetchall()
            
            templates = []
            for row in rows:
                templates.append({
                    "key": row[0],
                    "name": row[1],
                    "provider_code": row[2],
                    "confidence_score": row[3] or 0.5,
                    "usage_count": row[4] or 0,
                })
            
            return {"success": True, "templates": templates}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _get_confidence_history(template_key: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Get confidence history for a template."""
    try:
        from integration_coworker.persistence.db import get_connection, get_engine_type, kg_table
        
        engine = get_engine_type()
        history_table = kg_table("confidence_history")
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            if engine == "postgres":
                cur.execute(f"""
                    SELECT created_at, old_confidence, new_confidence, feedback_count, reason
                    FROM {history_table}
                    WHERE node_key = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (template_key, limit))
            else:
                cur.execute(f"""
                    SELECT created_at, old_confidence, new_confidence, feedback_count, reason
                    FROM {history_table}
                    WHERE node_key = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (template_key, limit))
            
            rows = cur.fetchall()
            
            history = []
            for row in rows:
                history.append({
                    "timestamp": str(row[0]) if row[0] else None,
                    "old_score": row[1],
                    "new_score": row[2],
                    "feedback_count": row[3],
                    "reason": row[4],
                })
            
            return history
    except Exception as e:
        # Return empty list but log error for debugging
        import logging
        logging.getLogger(__name__).warning(f"Failed to get confidence history for {template_key}: {e}")
        return []


# -----------------------------------------------------------------
# P2: Config Validation Detail Helper Functions (V5.2)
# -----------------------------------------------------------------

def _get_full_config_summary() -> Dict[str, Any]:
    """
    Get comprehensive configuration summary mirroring CLI 'status' command.
    """
    import os
    try:
        from integration_coworker.config import get_settings
        
        settings = get_settings()
        warnings = settings.validate()
        
        config = {
            "database": {},
            "llm": {},
            "embeddings": {},
            "redis": {},
            "langsmith": {},
            "warnings": warnings,
        }
        
        # Database config
        if settings.database.use_sqlite:
            config["database"] = {
                "engine": "SQLite",
                "mode": "test",
                "path": settings.database.sqlite_path,
            }
        else:
            # Mask password in URL
            display_url = settings.database.url
            if display_url and "@" in display_url and ":" in display_url.split("@")[0]:
                parts = display_url.split("@")
                user_pass = parts[0].split("://")[1]
                if ":" in user_pass:
                    user = user_pass.split(":")[0]
                    display_url = f"postgresql://{user}:***@{parts[1]}"
            
            config["database"] = {
                "engine": "PostgreSQL",
                "url": display_url,
                "connected": False,
                "pgvector": False,
            }
            
            # Check connection
            try:
                from integration_coworker.persistence.postgres import check_connection, check_pgvector
                config["database"]["connected"] = check_connection()
                config["database"]["pgvector"] = check_pgvector()
            except Exception as e:
                config["database"]["error"] = str(e)[:50]
        
        # LLM config
        if settings.llm.use_mock:
            config["llm"] = {"mode": "Mock", "reason": "USE_MOCK_LLM=true"}
        elif settings.llm.api_key:
            config["llm"] = {
                "mode": "Real",
                "model": settings.llm.default_model,
                "api_key": f"{settings.llm.api_key[:8]}...",
                "base_url": settings.llm.base_url,
            }
        else:
            config["llm"] = {"mode": "Not configured", "error": "Set OPENAI_API_KEY"}
        
        # Embeddings config
        config["embeddings"] = {
            "model": settings.embedding.model,
            "dimensions": settings.embedding.dimensions,
            "batch_size": settings.embedding.batch_size,
        }
        
        # Redis config
        redis_url = os.getenv("REDIS_URL", "")
        if redis_url:
            config["redis"] = {
                "configured": True,
                "url": redis_url[:30] + "..." if len(redis_url) > 30 else redis_url,
            }
        else:
            config["redis"] = {"configured": False}
        
        # LangSmith config
        tracing = os.getenv("LANGCHAIN_TRACING_V2", "").lower() == "true"
        ls_key = os.getenv("LANGCHAIN_API_KEY", "") or os.getenv("LANGSMITH_API_KEY", "")
        config["langsmith"] = {
            "tracing_enabled": tracing,
            "has_key": bool(ls_key),
            "project": os.getenv("LANGCHAIN_PROJECT", "default"),
        }
        
        return {"success": True, "config": config}
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------
# P3: Package Version Display Helper Functions (V5.2)
# -----------------------------------------------------------------

def _get_package_versions() -> Dict[str, str]:
    """Get versions of key installed packages."""
    packages = {
        "streamlit": None,
        "langchain": None,
        "langchain-openai": None,
        "langchain-anthropic": None,
        "openai": None,
        "psycopg": None,
        "pyyaml": None,
        "pydantic": None,
        "redis": None,
    }
    
    try:
        from importlib.metadata import version, PackageNotFoundError
        
        for pkg in packages:
            try:
                packages[pkg] = version(pkg)
            except PackageNotFoundError:
                packages[pkg] = "not installed"
    except ImportError:
        pass
    
    return packages


# -----------------------------------------------------------------
# P3: Performance Metrics Helper Functions (V5.2)
# -----------------------------------------------------------------

def _run_performance_benchmark(timeout_per_test: float = 5.0) -> Dict[str, Any]:
    """
    Run performance benchmarks mirroring CLI health --perf-summary.
    
    Args:
        timeout_per_test: Max seconds per benchmark test (default 5s)
    """
    import time
    import random
    import signal
    
    results = {}
    
    # Reduced iterations for UI context (faster feedback)
    UI_ITERATIONS = 50  # Reduced from 100
    
    def timeout_handler(signum, frame):
        raise TimeoutError("Benchmark timed out")
    
    try:
        # Test state creation
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.api.types import IntegrationOptions
        
        start = time.perf_counter()
        for _ in range(UI_ITERATIONS):
            WorkflowState(
                run_id="perf-test",
                source_refs=["test.yaml"],
                spec_refs=["test.yaml"],
                task_description="Test",
                options=IntegrationOptions(),
                completed_steps=[],
            )
            # Check elapsed time to prevent hangs
            if time.perf_counter() - start > timeout_per_test:
                results["state_creation_ms"] = "timeout"
                break
        else:
            results["state_creation_ms"] = round((time.perf_counter() - start) * (100 / UI_ITERATIONS) * 10, 2)
    except Exception as e:
        results["state_creation_ms"] = f"error: {str(e)[:30]}"
    
    try:
        # Test cosine similarity
        from integration_coworker.retrieval.semantic_search import cosine_similarity
        vec_a = [random.random() for _ in range(1536)]
        vec_b = [random.random() for _ in range(1536)]
        
        start = time.perf_counter()
        for _ in range(UI_ITERATIONS):
            cosine_similarity(vec_a, vec_b)
            if time.perf_counter() - start > timeout_per_test:
                results["cosine_similarity_ms"] = "timeout"
                break
        else:
            results["cosine_similarity_ms"] = round((time.perf_counter() - start) * (100 / UI_ITERATIONS) * 10, 2)
    except Exception as e:
        results["cosine_similarity_ms"] = f"error: {str(e)[:30]}"
    
    try:
        # Test YAML parsing
        import yaml
        sample_yaml = """
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0.0"
paths:
  /items:
    get:
      operationId: listItems
      responses:
        "200":
          description: Success
"""
        yaml_iterations = UI_ITERATIONS // 2  # YAML is slower
        start = time.perf_counter()
        for _ in range(yaml_iterations):
            yaml.safe_load(sample_yaml)
            if time.perf_counter() - start > timeout_per_test:
                results["yaml_parse_ms"] = "timeout"
                break
        else:
            results["yaml_parse_ms"] = round((time.perf_counter() - start) * (50 / yaml_iterations) * 20, 2)
    except Exception as e:
        results["yaml_parse_ms"] = f"error: {str(e)[:30]}"
    
    # Determine overall status
    all_fast = all(
        isinstance(v, float) and v < 10.0
        for v in results.values()
        if isinstance(v, (int, float))
    )
    
    return {
        "success": True,
        "status": "ok" if all_fast else "warning",
        "metrics": results,
    }


# -----------------------------------------------------------------
# Main Entry Point
# -----------------------------------------------------------------
def main() -> None:
    """Main entry point for the Streamlit application."""
    _apply_secrets()  # Bridge st.secrets -> os.environ for coworker config
    st.set_page_config(
        page_title="Integration Co-Worker",
        page_icon="🔧",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    _init_session_state()

    # V5.1: HITL Review Dialog (PR #4)
    # Check for pending review BEFORE any other rendering (per @st.dialog invariants)
    _handle_pending_review()

    # Title and description
    st.title("🔧 Agentic Integration Co-Worker")
    st.caption("Design and generate API integrations from OpenAPI specs")

    # System Status Panel (V3.1)
    _render_system_status()

    # Sidebar for inputs
    with st.sidebar:
        _layout_sidebar_inputs()

    # Main content area with tabs
    _layout_main_tabs()


# -----------------------------------------------------------------
# System Status Panel
# -----------------------------------------------------------------
def _render_system_status() -> None:
    """Render the system status preflight panel."""
    with st.expander("🔧 System Status", expanded=False):
        col1, col2 = st.columns([3, 1])
        
        with col2:
            if st.button("🔄 Refresh", key="refresh_preflight"):
                st.session_state.preflight_result = _run_preflight()
                st.session_state.full_system_status = _get_full_system_status()
        
        # Run preflight if not cached
        if st.session_state.preflight_result is None:
            st.session_state.preflight_result = _run_preflight()
        
        result = st.session_state.preflight_result
        
        with col1:
            if result["all_passed"]:
                st.success("✅ All systems ready")
            elif result["critical_failed"]:
                st.error("❌ Critical checks failed - run may fail")
            else:
                st.warning("⚠️ Some checks failed - run may have issues")
        
        # Show individual checks
        for check in result["checks"]:
            icon = "✅" if check["passed"] else "❌"
            st.text(f"{icon} {check['name']}: {check['message']}")
        
        # -----------------------------------------------------------------
        # P2: Full Config Validation (V5.0)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("📋 Config Validation")
        
        if st.button("🔍 Validate Config", key="validate_config"):
            with st.spinner("Validating configuration..."):
                config_result = _validate_config()
            
            if config_result.get("success"):
                st.success("✅ Configuration is valid!")
            else:
                st.error("❌ Configuration has errors")
            
            # Show errors
            if config_result.get("errors"):
                for err in config_result["errors"]:
                    st.error(f"❌ {err}")
            
            # Show warnings
            if config_result.get("warnings"):
                for warn in config_result["warnings"]:
                    st.warning(f"⚠️ {warn}")
            
            # Show config summary
            if config_result.get("config_summary"):
                with st.expander("📦 Config Summary", expanded=False):
                    for key, value in config_result["config_summary"].items():
                        st.text(f"  {key}: {value}")
        
        # -----------------------------------------------------------------
        # P1: Cache Management (V5.0)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("🗃️ LLM Cache Management")
        
        cache_stats = _get_llm_cache_stats()
        
        if cache_stats and cache_stats.get("available"):
            # Show stats
            stats = cache_stats.get("stats", {})
            metrics = cache_stats.get("metrics", {})
            
            cache_col1, cache_col2, cache_col3 = st.columns(3)
            with cache_col1:
                st.metric("Cache Hits", stats.get("hits", metrics.get("hits", 0)))
            with cache_col2:
                st.metric("Cache Misses", stats.get("misses", metrics.get("misses", 0)))
            with cache_col3:
                hit_rate = metrics.get("hit_rate", 0)
                st.metric("Hit Rate", f"{hit_rate:.1%}" if hit_rate else "N/A")
            
            # P3: Enhanced Cache Stats - bypass rate
            if metrics:
                bypass_rate = metrics.get("bypass_rate", 0)
                if bypass_rate > 0:
                    st.caption(f"📊 Bypass rate: {bypass_rate:.1%} | Errors: {metrics.get('errors', 0)}")
            
            # Cache clear controls
            clear_col1, clear_col2 = st.columns([2, 1])
            with clear_col1:
                pattern = st.text_input(
                    "Cache pattern (optional)",
                    key="cache_clear_pattern",
                    placeholder="e.g., stripe:* or leave empty for all",
                )
            with clear_col2:
                if st.button("🗑️ Clear Cache", key="clear_cache"):
                    pattern_to_clear = pattern if pattern else None
                    with st.spinner("Clearing cache..."):
                        clear_result = _clear_llm_cache(pattern_to_clear)
                    
                    if clear_result.get("success"):
                        st.success(f"✅ Cleared {clear_result.get('deleted', 0)} entries")
                    else:
                        st.error(f"❌ {clear_result.get('error', 'Unknown error')}")
        else:
            st.info("ℹ️ LLM Cache not available (Redis not connected)")
        
        # -----------------------------------------------------------------
        # P3: Full System Status (V5.0)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("📊 Full System Status")
        
        # Load full status on demand
        if st.session_state.full_system_status is None:
            st.session_state.full_system_status = _get_full_system_status()
        
        full_status = st.session_state.full_system_status
        
        if full_status.get("error"):
            st.error(f"❌ {full_status['error']}")
        else:
            # Database
            db = full_status.get("database", {})
            db_icon = "✅" if db.get("connected") else "❌"
            st.markdown(f"**Database:** {db_icon} {db.get('engine', 'Unknown')}")
            if db.get("pgvector") is not None:
                pgv_icon = "✅" if db["pgvector"] else "⚠️"
                st.caption(f"  pgvector: {pgv_icon}")
            
            # LLM
            llm = full_status.get("llm", {})
            llm_icon = "✅" if llm.get("configured") else "⚠️"
            st.markdown(f"**LLM:** {llm_icon} {llm.get('mode', 'Unknown')} {llm.get('model', '')}")
            
            # Embeddings
            embed = full_status.get("embeddings", {})
            st.markdown(f"**Embeddings:** {embed.get('model', 'N/A')} ({embed.get('dimensions', 'N/A')}d)")
            
            # Warnings
            if full_status.get("warnings"):
                with st.expander(f"⚠️ {len(full_status['warnings'])} Warnings", expanded=False):
                    for warn in full_status["warnings"]:
                        st.warning(warn)
        
        # -----------------------------------------------------------------
        # P1: Deep Health Check (V5.1)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("🩺 Deep Health Check")
        
        col1, col2 = st.columns([2, 1])
        with col1:
            check_llm = st.checkbox(
                "Include LLM connectivity test",
                value=False,
                key="deep_health_check_llm",
                help="⚠️ Makes actual API calls to verify LLM connectivity",
            )
        
        with col2:
            if st.button("🔬 Run Deep Check", key="run_deep_health_check"):
                with st.spinner("Running deep health checks..."):
                    deep_result = _run_deep_health_check(check_llm=check_llm)
                
                if deep_result.get("success"):
                    checks = deep_result.get("checks", {})
                    
                    for check_name, check_info in checks.items():
                        if isinstance(check_info, dict):
                            status = check_info.get("status", "ok")
                            message = check_info.get("message", "")
                            
                            if status == "ok":
                                st.success(f"✅ {check_name}: {message}")
                            elif status == "warning":
                                st.warning(f"⚠️ {check_name}: {message}")
                            elif status == "error":
                                st.error(f"❌ {check_name}: {message}")
                            else:
                                st.info(f"ℹ️ {check_name}: {message}")
                        else:
                            # Handle llm_connectivity dict
                            with st.expander(f"🔗 {check_name}", expanded=False):
                                for provider, status in check_info.items():
                                    if status == "ok":
                                        st.success(f"✅ {provider}: Connected")
                                    else:
                                        st.error(f"❌ {provider}: {status}")
                else:
                    st.error(f"❌ Deep check failed: {deep_result.get('error', 'Unknown error')}")
        
        # -----------------------------------------------------------------
        # P3: Cache Connection Details (V5.1)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("🔌 Cache Connection Details")
        
        cache_details = _get_cache_connection_details()
        
        if cache_details.get("configured"):
            detail_col1, detail_col2 = st.columns(2)
            with detail_col1:
                st.markdown(f"**URL:** `{cache_details.get('url', 'N/A')}`")
                if cache_details.get("host_port"):
                    st.caption(f"Host: {cache_details['host_port']}")
            with detail_col2:
                if cache_details.get("connected"):
                    latency = cache_details.get("latency_ms", 0)
                    st.success(f"✅ Connected ({latency:.1f}ms)")
                else:
                    st.error(f"❌ Not connected: {cache_details.get('error', 'Unknown')}")
        else:
            st.info("ℹ️ Redis cache not configured (REDIS_URL not set)")
        
        # -----------------------------------------------------------------
        # P2: Database Initialization (V5.1)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("🗄️ Database Initialization")
        
        init_col1, init_col2 = st.columns([2, 1])
        with init_col1:
            seed_kg = st.checkbox(
                "Seed Knowledge Graph",
                value=True,
                key="init_db_seed_kg",
                help="Populate KG with curated workflow templates",
            )
        
        with init_col2:
            if st.button("🚀 Initialize DB", key="init_database_btn"):
                with st.spinner("Initializing database..."):
                    init_result = _initialize_database(seed_kg=seed_kg)
                
                if init_result.get("success"):
                    st.success(f"✅ Database initialized ({init_result.get('engine', 'unknown')})")
                    for step in init_result.get("steps", []):
                        st.caption(f"  → {step}")
                else:
                    st.error(f"❌ Init failed: {init_result.get('error', 'Unknown error')}")
        
        # -----------------------------------------------------------------
        # P2: KG Dump/Export (V5.1)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("📦 Knowledge Graph Export")
        
        kg_col1, kg_col2, kg_col3 = st.columns(3)
        with kg_col1:
            kg_provider = st.text_input(
                "Provider filter",
                key="kg_export_provider",
                placeholder="e.g., stripe",
            )
        with kg_col2:
            kg_node_type = st.selectbox(
                "Node type",
                options=["All", "workflow_template", "endpoint", "schema"],
                key="kg_export_node_type",
            )
        with kg_col3:
            kg_limit = st.number_input(
                "Limit",
                min_value=10,
                max_value=1000,
                value=100,
                key="kg_export_limit",
            )
        
        if st.button("📥 Export KG Data", key="export_kg_btn"):
            with st.spinner("Exporting Knowledge Graph..."):
                export_result = _export_kg_data(
                    provider=kg_provider if kg_provider else None,
                    node_type=kg_node_type if kg_node_type != "All" else None,
                    limit=kg_limit,
                )
            
            if export_result.get("success"):
                import json
                data = export_result.get("data", {})
                
                # Show summary
                nodes_count = len(data.get("nodes", []))
                edges_count = len(data.get("edges", []))
                steps_count = len(data.get("workflow_steps", []))
                
                st.success(f"✅ Exported: {nodes_count} nodes, {edges_count} edges, {steps_count} steps")
                
                # Download button
                json_str = json.dumps(data, indent=2, default=str)
                st.download_button(
                    label="💾 Download JSON",
                    data=json_str,
                    file_name="kg_export.json",
                    mime="application/json",
                    key="download_kg_json",
                )
            else:
                st.error(f"❌ Export failed: {export_result.get('error', 'Unknown error')}")
        
        # -----------------------------------------------------------------
        # P3: Template Feedback Aggregation (V5.1)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("📊 Template Feedback")
        
        templates_with_feedback = _get_all_templates_with_feedback()
        
        if templates_with_feedback:
            st.caption(f"Found {len(templates_with_feedback)} templates with feedback")
            
            # Template selector
            template_options = {t["key"]: f"{t['name']} ({t['feedback_count']} reviews)" 
                               for t in templates_with_feedback}
            
            selected_template = st.selectbox(
                "Select template",
                options=list(template_options.keys()),
                format_func=lambda x: template_options.get(x, x),
                key="feedback_template_selector",
            )
            
            if selected_template:
                feedback_data = _get_template_feedback_records(selected_template)
                
                if feedback_data.get("success"):
                    # Show aggregate stats
                    stat_col1, stat_col2, stat_col3, stat_col4 = st.columns(4)
                    with stat_col1:
                        st.metric("Total", feedback_data.get("total", 0))
                    with stat_col2:
                        st.metric("Positive", feedback_data.get("positive", 0))
                    with stat_col3:
                        st.metric("Negative", feedback_data.get("negative", 0))
                    with stat_col4:
                        avg = feedback_data.get("avg_score", 0)
                        st.metric("Avg Score", f"{avg:.2f}")
                    
                    # Show recent records
                    records = feedback_data.get("records", [])
                    if records:
                        with st.expander(f"📝 Recent Feedback ({len(records)})", expanded=False):
                            for rec in records[:10]:
                                score_icon = "👍" if rec.get("score", 0) >= 0.5 else "👎"
                                st.text(f"{score_icon} {rec.get('feedback_type', 'unknown')}: {rec.get('comment', 'No comment')[:50]}")
                else:
                    st.warning(f"⚠️ {feedback_data.get('error', 'Could not load feedback')}")
        else:
            st.info("ℹ️ No templates with feedback found")
        
        # -----------------------------------------------------------------
        # P1: HITL Pending Approvals (V5.1)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("🛑 HITL Pending Approvals")
        
        pending_runs = _get_pending_hitl_runs()
        
        if pending_runs:
            st.warning(f"⚠️ {len(pending_runs)} runs awaiting approval")
            
            for run in pending_runs:
                with st.expander(f"🔶 {run.get('run_id', 'unknown')[:8]}... - {run.get('provider_code', 'unknown')}", expanded=True):
                    # Get HITL status details
                    hitl_status = _get_hitl_status(run.get("run_id", ""))
                    
                    st.caption(f"Started: {run.get('started_at', 'unknown')}")
                    
                    if hitl_status.get("paused"):
                        payload = hitl_status.get("payload", {})
                        st.write(f"**Gate:** {hitl_status.get('gate', 'unknown')}")
                        
                        if payload:
                            with st.expander("📋 Payload Details", expanded=False):
                                st.json(payload)
                        
                        # Approval controls
                        hitl_comment = st.text_input(
                            "Comment (optional)",
                            key=f"hitl_comment_{run.get('run_id', '')}",
                            placeholder="Reason for approval/rejection",
                        )
                        
                        approve_col, reject_col = st.columns(2)
                        with approve_col:
                            if st.button("✅ Approve", key=f"approve_{run.get('run_id', '')}", type="primary"):
                                with st.spinner("Submitting approval..."):
                                    result = _submit_hitl_approval(
                                        run.get("run_id", ""),
                                        approved=True,
                                        comment=hitl_comment if hitl_comment else None,
                                    )
                                
                                if result.get("success"):
                                    st.success("✅ Approved! Workflow resuming...")
                                    st.rerun()
                                else:
                                    st.error(f"❌ {result.get('error', 'Unknown error')}")
                        
                        with reject_col:
                            if st.button("❌ Reject", key=f"reject_{run.get('run_id', '')}"):
                                with st.spinner("Submitting rejection..."):
                                    result = _submit_hitl_approval(
                                        run.get("run_id", ""),
                                        approved=False,
                                        comment=hitl_comment if hitl_comment else "Rejected by user",
                                    )
                                
                                if result.get("success"):
                                    st.info("⏹️ Workflow rejected and stopped")
                                    st.rerun()
                                else:
                                    st.error(f"❌ {result.get('error', 'Unknown error')}")
                    else:
                        st.info("ℹ️ Run is in progress but not at HITL gate")
        else:
            st.success("✅ No pending HITL approvals")
        
        # -----------------------------------------------------------------
        # P1: KG Query Panel (V5.2)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("🔍 Knowledge Graph Query")
        
        kg_stats = _get_kg_stats()
        if "_error" in kg_stats:
            st.warning(f"⚠️ KG connection issue: {kg_stats['_error']}")
        elif kg_stats:
            # Filter out internal keys for display
            display_stats = {k: v for k, v in kg_stats.items() if not k.startswith('_')}
            st.caption(f"📊 KG contains: {sum(display_stats.values())} nodes ({', '.join(f'{t}={c}' for t, c in display_stats.items())})")
        else:
            st.caption("📊 KG is empty. Run an integration with --persist first.")
        query_col1, query_col2, query_col3 = st.columns(3)
        with query_col1:
            kg_entity = st.text_input(
                "Entity name",
                key="kg_query_entity",
                placeholder="e.g., Payment, Customer",
            )
        with query_col2:
            kg_endpoint = st.text_input(
                "Endpoint path",
                key="kg_query_endpoint",
                placeholder="e.g., /checkout/sessions",
            )
        with query_col3:
            kg_pattern = st.text_input(
                "Pattern key",
                key="kg_query_pattern",
                placeholder="e.g., pattern.crud_create",
            )
        
        kg_opt_col1, kg_opt_col2, kg_opt_col3 = st.columns(3)
        with kg_opt_col1:
            kg_provider_filter = st.text_input(
                "Provider filter (optional)",
                key="kg_query_provider",
                placeholder="e.g., stripe",
            )
        with kg_opt_col2:
            kg_max_depth = st.number_input(
                "Max depth",
                min_value=1,
                max_value=10,
                value=3,
                key="kg_query_depth",
            )
        with kg_opt_col3:
            kg_limit = st.number_input(
                "Max results",
                min_value=1,
                max_value=50,
                value=10,
                key="kg_query_limit",
            )
        
        if st.button("🔎 Search KG", key="kg_query_btn"):
            if not (kg_entity or kg_endpoint or kg_pattern):
                st.warning("⚠️ Specify at least one of: Entity, Endpoint, or Pattern")
            else:
                with st.spinner("Querying Knowledge Graph..."):
                    query_result = _kg_query(
                        entity_name=kg_entity if kg_entity else None,
                        endpoint_path=kg_endpoint if kg_endpoint else None,
                        pattern_key=kg_pattern if kg_pattern else None,
                        provider_code=kg_provider_filter if kg_provider_filter else None,
                        max_depth=int(kg_max_depth),
                        limit=int(kg_limit),
                    )
                
                if query_result.get("success"):
                    results = query_result.get("results", [])
                    if results:
                        st.success(f"🔍 Found {len(results)} related tasks")
                        
                        for i, match in enumerate(results, 1):
                            distance = match.get("graph_distance", 0)
                            hop_str = f"({distance} hop{'s' if distance != 1 else ''})"
                            
                            with st.expander(f"{i}. {match.get('task_key', 'unknown')} {hop_str}", expanded=i <= 3):
                                if match.get("provider_code"):
                                    st.write(f"**Provider:** {match['provider_code']}")
                                if match.get("task_description"):
                                    st.write(f"**Description:** {match['task_description'][:100]}...")
                                if match.get("associated_templates"):
                                    st.write(f"**Templates:** {', '.join(match['associated_templates'][:3])}")
                                if match.get("path"):
                                    st.caption(f"Path: {' → '.join(match['path'])}")
                    else:
                        st.info("No related tasks found. Try increasing depth or different search terms.")
                else:
                    st.error(f"❌ {query_result.get('error', 'Query failed')}")
        
        # -----------------------------------------------------------------
        # P1: Feedback Submission Panel (V5.2)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("👍 Submit Feedback")
        
        runs_for_feedback = _get_runs_for_feedback(limit=10)
        
        if runs_for_feedback:
            # Run selector
            run_options = {
                r["run_id"]: f"{r['provider_code']} - {r['task_slug'][:30]} ({r['feedback_count']} feedback)"
                for r in runs_for_feedback
            }
            
            selected_run = st.selectbox(
                "Select completed run",
                options=list(run_options.keys()),
                format_func=lambda x: run_options.get(x, x),
                key="feedback_run_selector",
            )
            
            if selected_run:
                fb_col1, fb_col2 = st.columns([2, 1])
                
                with fb_col1:
                    feedback_comment = st.text_input(
                        "Comment (optional)",
                        key="feedback_comment",
                        placeholder="Describe what worked or didn't work",
                    )
                
                with fb_col2:
                    feedback_score = st.slider(
                        "Score",
                        min_value=0.0,
                        max_value=1.0,
                        value=0.5,
                        step=0.1,
                        key="feedback_score_slider",
                    )
                
                fb_btn_col1, fb_btn_col2, fb_btn_col3 = st.columns(3)
                
                with fb_btn_col1:
                    if st.button("👍 Thumbs Up", key="feedback_thumbs_up", type="primary"):
                        with st.spinner("Submitting feedback..."):
                            result = _submit_feedback(
                                run_id=selected_run,
                                score=1.0,
                                feedback_type="thumbs_up",
                                comment=feedback_comment if feedback_comment else None,
                            )
                        if result.get("success"):
                            st.success("✅ Positive feedback recorded!")
                            st.rerun()
                        else:
                            st.error(f"❌ {result.get('error', 'Failed')}")
                
                with fb_btn_col2:
                    if st.button("👎 Thumbs Down", key="feedback_thumbs_down"):
                        with st.spinner("Submitting feedback..."):
                            result = _submit_feedback(
                                run_id=selected_run,
                                score=0.0,
                                feedback_type="thumbs_down",
                                comment=feedback_comment if feedback_comment else None,
                            )
                        if result.get("success"):
                            st.success("✅ Negative feedback recorded!")
                            st.rerun()
                        else:
                            st.error(f"❌ {result.get('error', 'Failed')}")
                
                with fb_btn_col3:
                    if st.button("📊 Score", key="feedback_score_btn"):
                        with st.spinner("Submitting feedback..."):
                            result = _submit_feedback(
                                run_id=selected_run,
                                score=feedback_score,
                                feedback_type="score",
                                comment=feedback_comment if feedback_comment else None,
                            )
                        if result.get("success"):
                            st.success(f"✅ Score {feedback_score} recorded!")
                            st.rerun()
                        else:
                            st.error(f"❌ {result.get('error', 'Failed')}")
        else:
            st.info("ℹ️ No completed runs available for feedback")
        
        # -----------------------------------------------------------------
        # P1: Run Status Detail Panel (V5.2)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("📋 Run Status Detail")
        
        run_id_input = st.text_input(
            "Run ID",
            key="run_status_detail_id",
            placeholder="Enter run ID to view details",
            max_chars=100,
        )
        
        if st.button("🔍 View Run Details", key="run_status_detail_btn") and run_id_input:
            # Basic input validation
            run_id_clean = run_id_input.strip()
            if not run_id_clean or len(run_id_clean) < 8:
                st.warning("⚠️ Please enter a valid run ID (at least 8 characters)")
            elif not all(c.isalnum() or c in '-_' for c in run_id_clean):
                st.warning("⚠️ Run ID should only contain alphanumeric characters, hyphens, or underscores")
            else:
                with st.spinner("Loading run details..."):
                    run_detail = _get_run_detail(run_id_clean)
                
                if run_detail.get("success"):
                    # Status overview
                    status = run_detail.get("status", "unknown")
                    status_icon = "✅" if status == "completed" else "❌" if status == "failed" else "⏳"
                    
                    st.write(f"**Status:** {status_icon} {status}")
                    st.write(f"**Provider:** {run_detail.get('provider_code', 'unknown')}")
                    st.write(f"**Task:** {run_detail.get('task_slug', 'unknown')}")
                    
                    if run_detail.get("started_at"):
                        st.write(f"**Started:** {run_detail['started_at']}")
                    if run_detail.get("ended_at"):
                        st.write(f"**Ended:** {run_detail['ended_at']}")
                    
                    if run_detail.get("error_message"):
                        st.error(f"**Error:** {run_detail['error_message']}")
                    
                    # Show any loading errors (new: checkpoint/artifact error indicators)
                    if run_detail.get("checkpoint_error"):
                        st.warning(f"⚠️ Checkpoint load issue: {run_detail['checkpoint_error']}")
                    if run_detail.get("artifacts_error"):
                        st.warning(f"⚠️ Artifacts load issue: {run_detail['artifacts_error']}")
                    
                    # Checkpoint info
                    if run_detail.get("has_checkpoint"):
                        st.success("✅ Checkpoint available (resumable)")
                    
                    # Completed nodes
                    completed_nodes = run_detail.get("completed_nodes", [])
                    if completed_nodes:
                        with st.expander(f"📋 Completed Nodes ({len(completed_nodes)})", expanded=False):
                            for node in completed_nodes:
                                st.text(f"  ✓ {node}")
                    
                    # Artifacts
                    artifacts = run_detail.get("artifacts", [])
                    if artifacts:
                        with st.expander(f"📁 Artifacts ({len(artifacts)})", expanded=False):
                            for art in artifacts:
                                st.text(f"  • {art.get('name', 'unknown')} ({art.get('size', 0)} bytes)")
                    
                    # Feedback
                    feedback = run_detail.get("feedback", [])
                    if feedback:
                        with st.expander(f"📊 Feedback ({len(feedback)})", expanded=False):
                            for fb in feedback:
                                score_icon = "👍" if fb.get("score", 0) >= 0.5 else "👎"
                                st.text(f"  {score_icon} {fb.get('type', 'unknown')}: {fb.get('score', 0):.1f}")
                else:
                    st.error(f"❌ {run_detail.get('error', 'Run not found')}")
        
        # -----------------------------------------------------------------
        # P2: KG Confidence Audit Panel (V5.2)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("📈 KG Confidence Audit")
        
        conf_col1, conf_col2, conf_col3 = st.columns(3)
        with conf_col1:
            conf_provider = st.text_input(
                "Provider filter",
                key="conf_audit_provider",
                placeholder="e.g., stripe",
            )
        with conf_col2:
            conf_threshold = st.number_input(
                "Threshold (show below)",
                min_value=0.0,
                max_value=1.0,
                value=1.0,
                step=0.1,
                key="conf_audit_threshold",
            )
        with conf_col3:
            conf_limit = st.number_input(
                "Limit",
                min_value=5,
                max_value=50,
                value=20,
                key="conf_audit_limit",
            )
        
        if st.button("📊 Load Confidence Scores", key="conf_audit_btn"):
            with st.spinner("Loading confidence scores..."):
                threshold_val = conf_threshold if conf_threshold < 1.0 else None
                conf_result = _get_kg_confidence_list(
                    provider=conf_provider if conf_provider else None,
                    threshold=threshold_val,
                    limit=int(conf_limit),
                )
            
            if conf_result.get("success"):
                templates = conf_result.get("templates", [])
                if templates:
                    st.caption(f"Showing {len(templates)} templates ordered by confidence (lowest first)")
                    
                    for tmpl in templates:
                        conf_score = tmpl.get("confidence_score", 0.5)
                        conf_bar = "🟢" if conf_score >= 0.7 else "🟡" if conf_score >= 0.4 else "🔴"
                        
                        with st.expander(f"{conf_bar} {tmpl.get('key', 'unknown')} ({conf_score:.3f})", expanded=False):
                            st.write(f"**Name:** {tmpl.get('name', 'unknown')}")
                            st.write(f"**Provider:** {tmpl.get('provider_code', 'unknown')}")
                            st.write(f"**Usage Count:** {tmpl.get('usage_count', 0)}")
                            
                            # Show history
                            history = _get_confidence_history(tmpl.get("key", ""), limit=5)
                            if history:
                                st.caption("📈 Recent History:")
                                for h in history:
                                    old_s = h.get("old_score", 0)
                                    new_s = h.get("new_score", 0)
                                    delta = new_s - old_s if old_s else 0
                                    delta_str = f"+{delta:.3f}" if delta >= 0 else f"{delta:.3f}"
                                    st.text(f"  {h.get('timestamp', 'unknown')[:19]}: {old_s:.3f} → {new_s:.3f} ({delta_str})")
                else:
                    st.info("No templates found matching criteria")
            else:
                st.error(f"❌ {conf_result.get('error', 'Failed to load')}")
        
        # -----------------------------------------------------------------
        # P2: Full Config Summary Panel (V5.2)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("⚙️ Full Configuration Summary")
        
        if st.button("📋 Load Full Config", key="full_config_btn"):
            with st.spinner("Loading configuration..."):
                config_result = _get_full_config_summary()
            
            if config_result.get("success"):
                config = config_result.get("config", {})
                
                # Database
                db_config = config.get("database", {})
                db_icon = "✅" if db_config.get("connected", False) or db_config.get("engine") == "SQLite" else "❌"
                st.markdown(f"**Database:** {db_icon} {db_config.get('engine', 'Unknown')}")
                if db_config.get("url"):
                    st.caption(f"  URL: {db_config['url']}")
                if db_config.get("path"):
                    st.caption(f"  Path: {db_config['path']}")
                if db_config.get("pgvector") is not None:
                    pgv_icon = "✅" if db_config["pgvector"] else "⚠️"
                    st.caption(f"  pgvector: {pgv_icon}")
                
                # LLM
                llm_config = config.get("llm", {})
                llm_icon = "✅" if llm_config.get("mode") == "Real" else "⚠️"
                st.markdown(f"**LLM:** {llm_icon} {llm_config.get('mode', 'Unknown')}")
                if llm_config.get("model"):
                    st.caption(f"  Model: {llm_config['model']}")
                if llm_config.get("api_key"):
                    st.caption(f"  API Key: {llm_config['api_key']}")
                
                # Embeddings
                embed_config = config.get("embeddings", {})
                st.markdown(f"**Embeddings:** {embed_config.get('model', 'N/A')}")
                st.caption(f"  Dimensions: {embed_config.get('dimensions', 'N/A')}, Batch: {embed_config.get('batch_size', 'N/A')}")
                
                # Redis
                redis_config = config.get("redis", {})
                redis_icon = "✅" if redis_config.get("configured") else "⚠️"
                st.markdown(f"**Redis:** {redis_icon} {'Configured' if redis_config.get('configured') else 'Not configured'}")
                
                # LangSmith
                ls_config = config.get("langsmith", {})
                ls_icon = "✅" if ls_config.get("tracing_enabled") and ls_config.get("has_key") else "⚠️"
                st.markdown(f"**LangSmith:** {ls_icon} {'Enabled' if ls_config.get('tracing_enabled') else 'Disabled'}")
                if ls_config.get("project"):
                    st.caption(f"  Project: {ls_config['project']}")
                
                # Warnings
                warnings = config.get("warnings", [])
                if warnings:
                    with st.expander(f"⚠️ {len(warnings)} Warnings", expanded=True):
                        for w in warnings:
                            st.warning(w)
                
                # Download config as JSON
                import json
                config_json = json.dumps(config, indent=2, default=str)
                st.download_button(
                    label="💾 Download Config JSON",
                    data=config_json,
                    file_name="config_summary.json",
                    mime="application/json",
                    key="download_config_json",
                )
            else:
                st.error(f"❌ {config_result.get('error', 'Failed')}")
        
        # -----------------------------------------------------------------
        # P3: Package Versions & Performance Panel (V5.2)
        # -----------------------------------------------------------------
        st.divider()
        st.subheader("📦 Package Versions & Performance")
        
        pkg_col, perf_col = st.columns(2)
        
        with pkg_col:
            if st.button("📋 Show Package Versions", key="pkg_versions_btn"):
                versions = _get_package_versions()
                st.caption("Installed Packages:")
                for pkg, ver in versions.items():
                    icon = "✅" if ver and ver != "not installed" else "⚠️"
                    st.text(f"  {icon} {pkg}: {ver or 'unknown'}")
        
        with perf_col:
            if st.button("⚡ Run Performance Benchmark", key="perf_benchmark_btn"):
                with st.spinner("Running benchmarks..."):
                    perf_result = _run_performance_benchmark()
                
                if perf_result.get("success"):
                    status = perf_result.get("status", "unknown")
                    status_icon = "✅" if status == "ok" else "⚠️"
                    st.write(f"**Status:** {status_icon} {status}")
                    
                    metrics = perf_result.get("metrics", {})
                    for metric, value in metrics.items():
                        if isinstance(value, float):
                            icon = "✅" if value < 10.0 else "⚠️"
                            st.text(f"  {icon} {metric}: {value:.2f}")
                        else:
                            st.text(f"  ⚠️ {metric}: {value}")


# -----------------------------------------------------------------
# Sidebar Inputs
# -----------------------------------------------------------------
def _layout_sidebar_inputs() -> None:
    """Render the sidebar input controls."""
    st.header("Configuration")

    # Spec reference(s)
    st.subheader("API Specification")
    spec_input_type = st.radio(
        "Spec Source",
        options=["URL", "File Path", "Upload", "Sample (Stripe)"],
        horizontal=True,
        key="spec_input_type",
    )

    spec_refs: List[str] = []
    if spec_input_type == "URL":
        spec_url = st.text_input(
            "OpenAPI Spec URL",
            placeholder="https://api.example.com/openapi.yaml",
            key="spec_url",
        )
        if spec_url:
            spec_refs = [spec_url]
    elif spec_input_type == "File Path":
        spec_path = st.text_input(
            "OpenAPI Spec Path",
            placeholder="/path/to/openapi.yaml",
            key="spec_path",
        )
        if spec_path:
            spec_refs = [spec_path]
    elif spec_input_type == "Upload":
        import tempfile
        uploaded = st.file_uploader(
            "Upload OpenAPI spec",
            type=["json", "yaml", "yml"],
            help=f"Max size: {MAX_UPLOAD_MB} MB"
        )
        if uploaded:
            size_mb = len(uploaded.getvalue()) / 1e6
            if size_mb > MAX_UPLOAD_MB:
                st.error(f"File too large: {size_mb:.1f} MB (max {MAX_UPLOAD_MB} MB)")
            else:
                suffix = Path(uploaded.name).suffix or ".json"
                # Use NamedTemporaryFile to avoid cross-session collisions
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="spec_") as f:
                    f.write(uploaded.getvalue())
                    spec_refs = [f.name]
                st.success(f"Uploaded: {uploaded.name} ({size_mb:.2f} MB)")
    elif spec_input_type == "Sample (Stripe)":
        # Use bundled Stripe OpenAPI spec for testing
        if STRIPE_SPEC.exists():
            spec_refs = [str(STRIPE_SPEC)]
            size_mb = STRIPE_SPEC.stat().st_size / 1e6
            st.info(f"📦 Using Stripe spec: {STRIPE_SPEC.name} ({size_mb:.1f} MB)")
        else:
            st.error(f"Stripe spec not found at {STRIPE_SPEC}")

    # Task description with presets
    st.subheader("Task")
    task_preset = st.selectbox(
        "Task Preset",
        options=list(TASK_PRESETS.keys()),
        index=0,
        key="task_preset",
        help="Choose a pre-defined task or select 'Custom' to write your own",
    )
    
    # Use preset value as default, allow override
    default_task = TASK_PRESETS.get(task_preset, "")
    task_description = st.text_area(
        "Task Description",
        value=default_task,
        placeholder="Create a checkout session for payment processing",
        height=100,
        key="task_description",
    )

    # Options
    st.subheader("Options")

    col1, col2 = st.columns(2)
    with col1:
        dry_run = st.checkbox("Dry Run", value=True, key="dry_run",
                              help="Run without persisting to database")
    with col2:
        verbose = st.checkbox("Verbose Logs", value=False, key="verbose",
                              help="Show detailed debug logging")

    provider_code = st.text_input(
        "Provider Code (optional)",
        placeholder="Auto-detected from spec",
        key="provider_code",
    )

    repo_root = st.text_input(
        "Repository Root (optional)",
        placeholder="/path/to/repo",
        key="repo_root",
        help="Target repo for file integration",
    )

    # V3.1: Advanced Options
    with st.expander("⚙️ Advanced Options", expanded=False):
        st.session_state.timeout_seconds = st.slider(
            "Timeout (seconds)",
            min_value=60,
            max_value=1800,
            value=st.session_state.timeout_seconds,
            step=60,
            help="Maximum time for pipeline execution. Uses subprocess isolation to kill stuck runs.",
        )
        
        st.session_state.sandbox_gates = st.multiselect(
            "Sandbox Validation Gates",
            options=ALL_SANDBOX_GATES,
            default=st.session_state.sandbox_gates,
            help="Code quality gates to run on generated code",
        )
        
        st.session_state.enable_live_tests = st.checkbox(
            "Enable Live Tests",
            value=st.session_state.enable_live_tests,
            help="⚠️ Makes REAL API calls to test endpoints (requires valid API keys)",
        )
        
        if st.session_state.enable_live_tests:
            st.warning("🔴 Live tests enabled - will make real API calls!")
            
            # V6: Live host allowlist for safety
            default_allowlist = st.session_state.get("live_host_allowlist", "")
            st.session_state.live_host_allowlist = st.text_input(
                "Live Host Allowlist",
                value=default_allowlist,
                key="live_host_allowlist_input",
                placeholder="api.stripe.com,api.twilio.com",
                help="Comma-separated list of allowed API hosts. Extracted from spec if empty.",
            )
            
            if not st.session_state.live_host_allowlist:
                st.caption("💡 Hosts will be auto-extracted from the OpenAPI spec")

    # V4.1: Batch Run section
    st.divider()
    st.subheader("🔁 Batch Run")
    
    showcase_mode = st.selectbox(
        "Batch Preset",
        options=["Off", "Quick (3 specs)", "Minimal (1 spec)"],
        index=0,
        key="showcase_mode",
        help="Run multiple specs sequentially for testing",
    )
    
    # Show showcase specs if mode selected
    if showcase_mode != "Off":
        from integration_coworker.harness import get_showcase_specs
        mode_key = "quick" if "Quick" in showcase_mode else "minimal"
        showcase_specs = get_showcase_specs(mode_key)
        
        st.caption(f"Will run {len(showcase_specs)} specs:")
        for i, spec in enumerate(showcase_specs, 1):
            st.text(f"  {i}. {spec.spec_file}: {spec.task_description[:40]}...")
        
        col1, col2 = st.columns(2)
        with col1:
            showcase_fresh_reset = st.checkbox(
                "Fresh reset before run",
                value=True,
                key="showcase_fresh_reset",
                help="Clear DB/cache before running batch",
            )
        
        if st.button(
            "🎪 Run Batch" if not st.session_state.is_running else "⏳ Running...",
            disabled=st.session_state.is_running,
            type="primary",
            use_container_width=True,
            key="run_showcase_btn",
        ):
            _run_showcase(
                mode=mode_key,
                fresh_reset=showcase_fresh_reset,
                dry_run=dry_run,
            )

    # -----------------------------------------------------------------
    # V5: HITL (Human-in-the-Loop) Approval Gate
    # Uses st.dialog for proper modal UX per Streamlit best practices
    # -----------------------------------------------------------------
    st.divider()
    run_disabled = not spec_refs or not task_description or st.session_state.is_running
    
    # Check if we need HITL approval (repo writes or live tests)
    needs_hitl = bool(repo_root) or st.session_state.enable_live_tests
    
    # Define the approval dialog
    @st.dialog("🛑 Human Approval Required", width="large")
    def _show_approval_dialog():
        pending = st.session_state.pending_approval
        if pending is None:
            st.error("No pending approval found")
            return
        
        st.write("**This run requires human approval before proceeding.**")
        
        # Show structured review of what will happen
        st.subheader("📋 Run Configuration")
        
        review_data = {
            "spec_refs": pending.get("spec_refs", []),
            "timeout_seconds": st.session_state.get("timeout_seconds", 600),
            "sandbox_gates": st.session_state.get("sandbox_gates", []),
        }
        
        # Repo changes section
        if pending.get("repo_root"):
            st.markdown("---")
            st.markdown("### 📂 Repository Changes")
            st.warning(f"""
            **Target Repository:** `{pending['repo_root']}`
            
            - A work branch will be created (e.g., `coworker-20251226-143052`)
            - Generated code will be written to the repo
            - Changes will be committed automatically
            - Original branch will be restored after run
            """)
            review_data["repo_root"] = pending["repo_root"]
        
        # Live tests section
        if pending.get("enable_live_tests"):
            st.markdown("---")
            st.markdown("### 🔴 Live API Tests")
            allowlist = st.session_state.get("live_host_allowlist", "")
            if not allowlist:
                st.error("⚠️ Live tests enabled but no host allowlist configured!")
                st.info("Set 'Live Host Allowlist' in Advanced Options before proceeding.")
            else:
                st.warning(f"""
                **REAL API calls will be made!**
                
                - Allowed hosts: `{allowlist}`
                - This may incur costs or rate limits
                - Ensure API keys are configured correctly
                """)
            review_data["enable_live_tests"] = True
            review_data["live_host_allowlist"] = allowlist
        
        # Task summary
        st.markdown("---")
        st.markdown("### 📝 Task Description")
        task = pending.get('task_description', '')
        st.code(task[:500] + ('...' if len(task) > 500 else ''), language=None)
        
        # Full config in expander
        with st.expander("View full configuration"):
            st.json(review_data)
        
        st.markdown("---")
        
        # Approval buttons
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Approve & Run", type="primary", use_container_width=True, key="approve_btn"):
                # Clear pending and set approved flag
                run_params = st.session_state.pending_approval
                st.session_state.pending_approval = None
                st.session_state.hitl_approved = True
                st.session_state.approved_run_params = run_params
                st.rerun()
        
        with col2:
            if st.button("❌ Cancel", use_container_width=True, key="cancel_btn"):
                st.session_state.pending_approval = None
                st.session_state.hitl_approved = False
                st.rerun()
    
    # Check if we have a pending approval to show
    if st.session_state.pending_approval is not None:
        _show_approval_dialog()
        st.stop()  # Hard stop until user approves or cancels
    
    # Check if user just approved - execute the run
    if st.session_state.get("hitl_approved") and st.session_state.get("approved_run_params"):
        run_params = st.session_state.approved_run_params
        st.session_state.hitl_approved = False
        st.session_state.approved_run_params = None
        
        _run_integration(
            spec_refs=run_params["spec_refs"],
            task_description=run_params["task_description"],
            provider_code=run_params.get("provider_code"),
            repo_root=run_params.get("repo_root"),
            dry_run=run_params.get("dry_run", False),
            verbose=run_params.get("verbose", False),
        )
    
    # Run single integration button
    if st.button(
        "🚀 Run Integration" if not st.session_state.is_running else "⏳ Running...",
        disabled=run_disabled,
        type="primary",
        use_container_width=True,
    ):
        if needs_hitl:
            # Store params and request approval - dialog will show on rerun
            st.session_state.pending_approval = {
                "spec_refs": spec_refs,
                "task_description": task_description,
                "provider_code": provider_code if provider_code else None,
                "repo_root": repo_root if repo_root else None,
                "dry_run": dry_run,
                "verbose": verbose,
                "enable_live_tests": st.session_state.enable_live_tests,
            }
            st.rerun()
        else:
            # No HITL needed, run directly
            _run_integration(
                spec_refs=spec_refs,
                task_description=task_description,
                provider_code=provider_code if provider_code else None,
                repo_root=repo_root if repo_root else None,
                dry_run=dry_run,
                verbose=verbose,
            )

    # V3.1: Fresh Reset button
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Reset Session", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()
    
    with col2:
        if st.button("🗑️ Fresh Reset", use_container_width=True, 
                     help="Clear database tables and Redis cache for a clean state"):
            _handle_fresh_reset()

    # Show input validation hints
    if not spec_refs:
        st.warning("Please provide an API specification")
    if not task_description:
        st.warning("Please describe the integration task")


# -----------------------------------------------------------------
# Fresh Reset Handler
# -----------------------------------------------------------------
def _handle_fresh_reset() -> None:
    """
    Handle Fresh Reset button click.
    
    Uses harness module for dynamic schema discovery.
    Truncates all managed tables and flushes Redis LLM cache.
    """
    try:
        from integration_coworker.harness.fresh_reset import fresh_reset_all
        
        with st.spinner("Resetting database and cache..."):
            result = fresh_reset_all()
        
        if result.success:
            st.success(
                f"✅ Fresh reset complete: "
                f"{result.tables_truncated} tables truncated, "
                f"{result.redis_keys_deleted} cache keys deleted"
            )
        else:
            st.error(f"❌ Fresh reset had errors: {', '.join(result.errors)}")
            
    except ImportError:
        st.error("Fresh reset requires harness module. Run `pip install -e .`")
    except Exception as e:
        st.error(f"❌ Fresh reset failed: {e}")


# -----------------------------------------------------------------
# Phase 2: Repo Integration Proof Panel
# -----------------------------------------------------------------
def _render_repo_proof(repo_info: Dict[str, Any]) -> None:
    """
    Render the repo integration proof panel.
    
    Shows:
    - Base branch and work branch
    - Git diff stat in code block (from working-tree comparison)
    - Files changed list for ungameable proof
    - Warning if changes were stashed
    - "No changes detected" only if genuinely empty
    
    Args:
        repo_info: Dict with repo_root, base_branch, work_branch (or demo_branch), diff_stat, 
                   files_changed, status, commit_hash, stashed
    """
    st.divider()
    st.subheader("📂 Repo Integration Proof")
    
    # Show branch info
    col1, col2 = st.columns(2)
    with col1:
        st.write(f"**Base Branch:** `{repo_info.get('base_branch', 'unknown')}`")
    with col2:
        # Support both 'work_branch' and legacy 'demo_branch' keys
        work_branch = repo_info.get('work_branch') or repo_info.get('demo_branch', 'unknown')
        st.write(f"**Work Branch:** `{work_branch}`")
    
    # Show repo root
    st.caption(f"Repository: {repo_info.get('repo_root', 'unknown')}")
    
    # Show commit hash if available
    if repo_info.get("commit_hash"):
        st.success(f"✅ Changes committed: `{repo_info['commit_hash'][:12]}`")
    
    # Show stash warning
    if repo_info.get("stashed"):
        st.warning("⚠️ Some uncommitted changes were stashed during cleanup. Check `git stash list`.")
    
    # Show diff stat - this is the key proof
    diff_stat = repo_info.get("diff_stat", "")
    files_changed = repo_info.get("files_changed", "")
    
    if diff_stat and diff_stat.strip():
        st.write("**Changes (diff stat):**")
        st.code(diff_stat, language="diff")
        
        # Also show file list for ungameable proof
        if files_changed and files_changed.strip():
            with st.expander("📄 Files changed", expanded=False):
                st.code(files_changed, language="text")
    elif files_changed and files_changed.strip():
        # Fallback: show file list if diff_stat failed
        st.write("**Files changed:**")
        st.code(files_changed, language="text")
    else:
        # Genuinely no changes - this is unusual for a pipeline run
        st.warning(
            "⚠️ No changes detected. This may indicate:\n"
            "- The pipeline didn't write any files\n"
            "- Files were written outside the repo\n"
            "- Git diff failed (check logs)"
        )
    
    # Show status if there are uncommitted changes still present
    status = repo_info.get("status", "")
    if status and status.strip():
        with st.expander("📋 Working tree status", expanded=False):
            st.code(status, language="text")
    
    # Show error if any
    if repo_info.get("error"):
        st.error(f"❌ {repo_info['error']}")


# -----------------------------------------------------------------
# Run Integration (with Error Capture)
# -----------------------------------------------------------------
def _run_integration(
    spec_refs: List[str],
    task_description: str,
    provider_code: Optional[str],
    repo_root: Optional[str],
    dry_run: bool,
    verbose: bool,
) -> None:
    """
    Execute the integration workflow with error capture.
    
    V4.0 Production Hardening:
    - Uses harness runner for subprocess isolation (reliable timeout via SIGKILL)
    - Uses st.status for progress UI (not just spinner)
    - Respects Advanced Options: timeout, sandbox_gates, enable_live_tests
    """
    from integration_coworker.harness import run_with_timeout

    # Clear previous state
    st.session_state.is_running = True
    st.session_state.last_error = None
    st.session_state.last_result = None
    st.session_state.last_result_dict = None
    st.session_state.pending_recovery_action = None

    # Get advanced options from session state
    timeout_seconds = st.session_state.get("timeout_seconds", 600)
    sandbox_gates = st.session_state.get("sandbox_gates", ["ruff", "mypy", "bandit", "pytest", "coverage"])
    enable_live_tests = st.session_state.get("enable_live_tests", False)
    live_host_allowlist = st.session_state.get("live_host_allowlist", "") if enable_live_tests else ""

    try:
        # V4: Use st.status for long-running operations (Streamlit best practice)
        with st.status("Running integration workflow...", expanded=True) as status:
            status.update(label=f"⏳ Starting pipeline (timeout: {timeout_seconds}s)...")
            st.write(f"**Spec(s):** {', '.join(spec_refs)}")
            st.write(f"**Task:** {task_description[:100]}...")
            st.write(f"**Sandbox gates:** {', '.join(sandbox_gates)}")
            if enable_live_tests:
                st.write("🔴 **Live tests:** Enabled")
            
            # V6: Create placeholder for live progress updates
            progress_placeholder = st.empty()
            current_events = []
            
            def on_event(event):
                """Callback for real-time event streaming (V6)."""
                current_events.append(event)
                phase = event.get("phase", "unknown")
                message = event.get("message", "")
                # Update placeholder with latest events
                with progress_placeholder.container():
                    st.caption("📋 **Live Progress:**")
                    for e in current_events[-5:]:  # Show last 5 events
                        p = e.get("phase", "?")
                        m = e.get("message", "")
                        st.text(f"  • [{p}] {m}")
            
            # V4: Use harness runner for subprocess isolation
            # This enables reliable timeout (can kill stuck LLM/DB calls)
            # V5: Added repo_root for repo integration proof
            # V6: Added event_callback for live progress streaming
            # V6.1: Added live_host_allowlist for safety
            harness_result = run_with_timeout(
                spec_refs=spec_refs,
                task_description=task_description,
                timeout_seconds=timeout_seconds,
                dry_run=dry_run,
                enable_live_tests=enable_live_tests,
                sandbox_gates=sandbox_gates,
                repo_root=repo_root,
                live_host_allowlist=live_host_allowlist if live_host_allowlist else None,
                event_callback=on_event,
            )
            
            # Handle result
            if harness_result.timed_out:
                status.update(label=f"⏱️ Pipeline timed out after {timeout_seconds}s", state="error")
                raise TimeoutError(f"Pipeline timed out after {timeout_seconds}s. Subprocess was terminated.")
            
            if not harness_result.success:
                status.update(label="❌ Pipeline failed", state="error")
                error_msg = harness_result.error or "Unknown pipeline error"
                if harness_result.stderr:
                    st.code(harness_result.stderr[-2000:], language="text")
                raise RuntimeError(error_msg)
            
            # Success! Store result dict from harness
            result_dict = harness_result.result
            status.update(label="✅ Pipeline completed!", state="complete")
            
            # Display summary in status
            st.write(f"**Run ID:** {result_dict.get('run_id', 'N/A')}")
            st.write(f"**Completed steps:** {len(result_dict.get('completed_steps', []))}")
            st.write(f"**Workflow nodes:** {result_dict.get('workflow_nodes', 0)}")
            st.write(f"**Code artifacts:** {result_dict.get('code_artifacts', 0)}")
            
            # Show sandbox summary if available
            sandbox_result = result_dict.get("sandbox_result")
            if sandbox_result:
                summary = sandbox_result.get("summary", "No summary")
                if "FAILED" in summary:
                    st.write(f"🔒 **Sandbox:** ❌ {summary}")
                else:
                    st.write(f"🔒 **Sandbox:** ✅ {summary}")
            
            # Phase 2: Show repo integration proof
            if harness_result.repo:
                _render_repo_proof(harness_result.repo)

        # Store result for display tabs
        st.session_state.last_result_dict = result_dict
        st.session_state.last_repo_info = harness_result.repo  # Phase 2
        st.session_state.current_run_id = result_dict.get("run_id", "unknown")

        # V5.1: Check for pending HITL review (PR #4)
        # If the pipeline paused for review, store state and trigger dialog
        pending_review = result_dict.get("pending_review_kind")
        if pending_review:
            st.session_state.pending_review_kind = pending_review
            st.session_state.review_artifact_refs = result_dict.get("review_artifact_refs", {})
            st.info(f"⏸️ Pipeline paused for {pending_review} review. Please approve or reject.")
        else:
            # Clear any previous pending review state
            st.session_state.pending_review_kind = None
            st.session_state.review_artifact_refs = None

        # Add to history
        _snapshot_run(result_dict, None)

        st.success(f"✅ Integration completed! Run ID: {result_dict.get('run_id')}")

    except Exception as e:
        error_info = {
            "error": str(e),
            "traceback": traceback.format_exc(),
            "timestamp": datetime.now().isoformat(),
            "inputs": {
                "spec_refs": spec_refs,
                "task_description": task_description,
                "provider_code": provider_code,
                "repo_root": repo_root,
                "dry_run": dry_run,
                "timeout_seconds": timeout_seconds,
                "sandbox_gates": sandbox_gates,
                "enable_live_tests": enable_live_tests,
            },
        }
        st.session_state.last_error = error_info
        _snapshot_run(None, error_info)
        st.error(f"❌ Integration failed: {str(e)}")

    finally:
        st.session_state.is_running = False


# -----------------------------------------------------------------
# Showcase Mode Runner
# -----------------------------------------------------------------
def _run_showcase(
    mode: str,
    fresh_reset: bool,
    dry_run: bool,
) -> None:
    """
    Run multi-spec batch sequentially.
    
    Executes multiple specs in order:
    - Optional fresh reset before run
    - P2 (V5.1): Progress bar with real-time updates
    - Run each spec with st.status for progress
    - Show per-spec results and final summary
    """
    from integration_coworker.harness import (
        get_showcase_specs, 
        run_pipeline_with_timeout,
        fresh_reset_all,
    )
    
    st.session_state.is_running = True
    st.session_state.showcase_progress = []
    
    # Get showcase specs
    specs = get_showcase_specs(mode)
    
    try:
        # Optional fresh reset
        if fresh_reset:
            with st.status("🗑️ Fresh reset...", expanded=False) as reset_status:
                result = fresh_reset_all()
                if result.success:
                    reset_status.update(
                        label=f"✅ Reset: {result.tables_truncated} tables, {result.redis_keys_deleted} cache keys",
                        state="complete"
                    )
                else:
                    reset_status.update(label="⚠️ Reset had errors", state="error")
        
        # Get settings
        timeout_seconds = st.session_state.get("timeout_seconds", 600)
        sandbox_gates = st.session_state.get("sandbox_gates", ["ruff", "mypy", "bandit", "pytest", "coverage"])
        enable_live_tests = st.session_state.get("enable_live_tests", False)
        
        # Run each spec
        passed = 0
        failed = 0
        total_elapsed = 0.0
        
        # P2 (V5.1): Add overall progress bar
        progress_bar = st.progress(0, text=f"Starting batch run: 0/{len(specs)} specs...")
        progress_summary = st.empty()
        
        for idx, spec in enumerate(specs):
            spec_label = f"[{idx+1}/{len(specs)}] {spec.spec_file}"
            
            # Update progress bar
            progress = (idx) / len(specs)
            progress_bar.progress(progress, text=f"Running: {spec.spec_file} ({idx+1}/{len(specs)})")
            
            with st.status(f"🚀 {spec_label}", expanded=True) as spec_status:
                st.write(f"**Task:** {spec.task_description}")
                st.write(f"**Expected provider:** {spec.expected_provider}")
                
                # Resolve spec path
                spec_path = _resolve_showcase_spec_path(spec.spec_file)
                
                # Run pipeline
                result = run_pipeline_with_timeout(
                    spec_refs=[spec_path],
                    task_description=spec.task_description,
                    timeout_seconds=timeout_seconds,
                    dry_run=dry_run,
                    enable_live_tests=enable_live_tests,
                    sandbox_gates=sandbox_gates,
                )
                
                total_elapsed += result.elapsed_seconds
                
                # Record result
                spec_result = {
                    "spec": spec.spec_file,
                    "success": result.success,
                    "timed_out": result.timed_out,
                    "elapsed": result.elapsed_seconds,
                    "error": result.error,
                    "events": result.events,
                    "result": result.result,
                }
                st.session_state.showcase_progress.append(spec_result)
                
                # Show events timeline
                if result.events:
                    st.caption("📋 Events:")
                    for event in result.events:
                        phase = event.get("phase", "unknown")
                        msg = event.get("message", "")
                        st.text(f"  • {phase}: {msg}")
                
                # Update status
                if result.success:
                    passed += 1
                    spec_status.update(
                        label=f"✅ {spec_label} ({result.elapsed_seconds:.1f}s)",
                        state="complete"
                    )
                    
                    # Show result summary
                    if result.result:
                        st.write(f"**Steps:** {len(result.result.get('completed_steps', []))}")
                        st.write(f"**Artifacts:** {result.result.get('code_artifacts', 0)}")
                        sandbox = result.result.get("sandbox_result")
                        if sandbox:
                            st.write(f"**Sandbox:** {sandbox.get('summary', 'N/A')}")
                else:
                    failed += 1
                    if result.timed_out:
                        spec_status.update(
                            label=f"⏱️ {spec_label} TIMEOUT ({timeout_seconds}s)",
                            state="error"
                        )
                    else:
                        spec_status.update(
                            label=f"❌ {spec_label} FAILED",
                            state="error"
                        )
                    st.write(f"**Error:** {result.error or 'Unknown error'}")
            
            # P2 (V5.1): Update progress summary after each spec
            progress_summary.text(f"Progress: {passed} ✅ {failed} ❌ | Elapsed: {total_elapsed:.1f}s")
        
        # P2 (V5.1): Complete progress bar
        progress_bar.progress(1.0, text=f"Batch complete: {passed}/{len(specs)} passed")
        
        # Final summary
        st.divider()
        if failed == 0:
            st.success(f"🎉 Batch complete: {passed}/{len(specs)} passed in {total_elapsed:.1f}s")
        else:
            st.warning(f"⚠️ Batch done: {passed}/{len(specs)} passed, {failed} failed in {total_elapsed:.1f}s")
        
        # Store last result for tabs
        if st.session_state.showcase_progress:
            last_spec = st.session_state.showcase_progress[-1]
            if last_spec.get("result"):
                st.session_state.last_result_dict = last_spec["result"]
    
    except Exception as e:
        st.error(f"❌ Showcase failed: {e}")
        st.code(traceback.format_exc())
    
    finally:
        st.session_state.is_running = False


def _resolve_showcase_spec_path(spec_file: str) -> str:
    """Resolve showcase spec file name to full path."""
    # Try common locations
    candidates = [
        Path.cwd() / "specs" / spec_file,
        Path(__file__).parent.parent.parent.parent / "specs" / spec_file,
    ]
    
    for path in candidates:
        if path.exists():
            return str(path)
    
    # Return as-is, let the pipeline handle the error
    return spec_file


# -----------------------------------------------------------------
# Main Tabs Layout
# -----------------------------------------------------------------
def _layout_main_tabs() -> None:
    """Render the main content area with tabs."""
    tab_run, tab_artifacts, tab_graph, tab_kg, tab_errors = st.tabs([
        "📊 Run Status",
        "📁 Artifacts",
        "🔀 Graph Trace",
        "🧠 Knowledge Graph",
        "⚠️ Errors & Recovery",
    ])

    with tab_run:
        _render_run_status()

    with tab_artifacts:
        _render_artifacts()

    with tab_graph:
        _render_graph_trace()

    with tab_kg:
        _render_kg_dashboard()

    with tab_errors:
        _render_errors_and_recovery()


# -----------------------------------------------------------------
# Run Status Tab
# -----------------------------------------------------------------
def _render_run_status() -> None:
    """
    Render the run status view.
    
    V4.0: Handles both IntegrationResult objects (direct call) and
    dict results (from harness subprocess).
    """
    result = st.session_state.last_result
    result_dict = st.session_state.get('last_result_dict')

    if result is None and result_dict is None:
        st.info("No integration run yet. Configure inputs in the sidebar and click 'Run Integration'.")
        return

    # V4: Normalize to common format - prefer dict from harness
    if result_dict:
        run_id = result_dict.get("run_id", "N/A")
        provider_code = result_dict.get("provider_code", "N/A")
        completed_steps = result_dict.get("completed_steps", [])
        workflow_nodes_count = result_dict.get("workflow_nodes", 0)
        workflow_edges_count = result_dict.get("workflow_edges", 0)
        code_artifacts_count = result_dict.get("code_artifacts", 0)
        endpoints_count = result_dict.get("endpoints", 0)
        schemas_count = result_dict.get("schemas", 0)
        sandbox_result = result_dict.get("sandbox_result")
        errors = result_dict.get("errors", [])
        report_markdown = result_dict.get("report_markdown")
    else:
        run_id = result.run_id
        provider_code = result.task.provider_code if result.task else "N/A"
        completed_steps = result.completed_steps
        workflow_nodes_count = len(result.workflow_nodes)
        workflow_edges_count = len(result.workflow_edges)
        code_artifacts_count = len(result.code_artifacts)
        endpoints_count = len(result.endpoints)
        schemas_count = len(result.schemas)
        sandbox_result = getattr(result, 'sandbox_result', None)
        errors = result.errors
        report_markdown = result.report_markdown

    # Header with run info
    st.subheader(f"Run: {run_id}")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Provider", provider_code)
    with col2:
        st.metric("Pipeline Steps", len(completed_steps))
    with col3:
        st.metric("Artifacts", code_artifacts_count)

    # Silver Model summary
    st.subheader("📦 Silver API Model")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Endpoints", endpoints_count)
    with col2:
        st.metric("Schemas", schemas_count)
    with col3:
        st.metric("Entities", 0)  # Not in dict, would need to add

    # Gold Model summary
    st.subheader("🏆 Gold Integration Model")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Workflow Nodes", workflow_nodes_count)
    with col2:
        st.metric("Workflow Edges", workflow_edges_count)
    with col3:
        st.metric("Code Artifacts", code_artifacts_count)

    # Completed Steps
    if completed_steps:
        st.subheader("✅ Completed Pipeline Steps")
        steps_text = " → ".join(completed_steps)
        st.code(steps_text, language=None)
        
    # Errors (if any)
    if errors:
        st.subheader("⚠️ Warnings/Errors")
        for err in errors:
            st.warning(err)

    # V3.1/V4: Sandbox Validation Results (works with both dict and object)
    if sandbox_result:
        st.subheader("🔒 Sandbox Validation")
        
        # Handle the new sandbox_result format from harness
        if isinstance(sandbox_result, dict):
            summary = sandbox_result.get("summary", "")
            gates = sandbox_result.get("gates", [])
            
            # Display summary
            if "FAILED" in summary or not sandbox_result.get("success", True):
                st.error(f"❌ {summary}")
            else:
                st.success(f"✅ {summary}")
            
            # Per-gate results (new format)
            if gates:
                for gate in gates:
                    if isinstance(gate, dict):
                        name = gate.get("name", "unknown")
                        passed = gate.get("passed", False)
                        output = gate.get("output", "")
                        duration_ms = gate.get("duration_ms", 0)
                        
                        icon = "✅" if passed else "❌"
                        with st.expander(f"{icon} {name} ({duration_ms}ms)", expanded=not passed):
                            if output:
                                st.code(output[:2000], language="text")
            else:
                # Legacy format (dict of gate_name -> result)
                for gate_name, gate_result in sandbox_result.items():
                    if isinstance(gate_result, dict):
                        icon = "✅" if gate_result.get("passed") else "❌"
                        with st.expander(f"{icon} {gate_name}", expanded=not gate_result.get("passed")):
                            if gate_result.get("output"):
                                st.code(gate_result["output"][:2000], language="text")
                            if gate_result.get("error"):
                                st.error(gate_result["error"])

    # Report Markdown
    if report_markdown:
        with st.expander("📄 Full Report", expanded=False):
            st.markdown(report_markdown)

    # -----------------------------------------------------------------
    # P1: Feedback Interface (V5.0 Production Hardening)
    # -----------------------------------------------------------------
    st.divider()
    st.subheader("📝 Run Feedback")
    
    feedback_col1, feedback_col2, feedback_col3 = st.columns([1, 1, 3])
    
    with feedback_col1:
        if st.button("👍 Good", key=f"feedback_good_{run_id}", type="primary", use_container_width=True):
            comment = st.session_state.get("feedback_comment", "")
            if _create_feedback(run_id, is_positive=True, comment=comment if comment else None):
                st.success("✅ Positive feedback recorded!")
    
    with feedback_col2:
        if st.button("👎 Needs Work", key=f"feedback_bad_{run_id}", use_container_width=True):
            comment = st.session_state.get("feedback_comment", "")
            if _create_feedback(run_id, is_positive=False, comment=comment if comment else None):
                st.warning("📝 Feedback recorded - will improve!")
    
    with feedback_col3:
        st.text_input(
            "Optional comment",
            key="feedback_comment",
            placeholder="What worked well or needs improvement?",
            label_visibility="collapsed",
        )


# -----------------------------------------------------------------
# Artifacts Tab
# -----------------------------------------------------------------
def _render_artifacts() -> None:
    """Render the artifacts browser."""
    result = st.session_state.last_result

    if result is None or not result.code_artifacts:
        st.info("No artifacts to display. Run an integration first.")
        return

    st.subheader("Generated Code Artifacts")

    # Artifact selector
    artifact_options = [
        f"[{a.artifact_type}] {a.rel_path}"
        for a in result.code_artifacts
    ]
    selected_idx = st.selectbox(
        "Select Artifact",
        options=range(len(artifact_options)),
        format_func=lambda i: artifact_options[i],
        key="artifact_selector",
    )

    if selected_idx is not None:
        artifact = result.code_artifacts[selected_idx]

        # Metadata
        col1, col2, col3 = st.columns(3)
        with col1:
            st.caption(f"**Type:** {artifact.artifact_type}")
        with col2:
            st.caption(f"**Path:** {artifact.rel_path}")
        with col3:
            size_kb = len(artifact.content) / 1024
            st.caption(f"**Size:** {size_kb:.1f} KB")

        # Determine language for syntax highlighting
        language = _detect_language(artifact.rel_path)

        # Code display
        st.code(artifact.content, language=language, line_numbers=True)

        # Download button
        st.download_button(
            label="⬇️ Download",
            data=artifact.content,
            file_name=Path(artifact.rel_path).name,
            mime="text/plain",
        )

    # Repo changes (if any)
    if result.repo_changes and result.repo_changes.changes:
        st.divider()
        st.subheader("📁 Repository Changes")

        for change in result.repo_changes.changes:
            icon = "➕" if change.change_type == "create" else "📝"
            st.text(f"{icon} {change.rel_path}")


def _detect_language(file_path: str) -> str:
    """Detect programming language from file extension."""
    ext_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".md": "markdown",
        ".txt": "text",
    }
    ext = Path(file_path).suffix.lower()
    return ext_map.get(ext, "text")


# -----------------------------------------------------------------
# Graph Trace Tab
# -----------------------------------------------------------------
def _render_graph_trace() -> None:
    """Render the graph/workflow trace visualization."""
    result = st.session_state.last_result

    if result is None:
        st.info("No workflow trace to display. Run an integration first.")
        return

    st.subheader("Workflow Graph Trace")

    # Show completed steps as a flow
    if result.completed_steps:
        st.write("**Execution Order:**")

        # Create a simple flow visualization
        for i, step in enumerate(result.completed_steps, 1):
            col1, col2 = st.columns([1, 5])
            with col1:
                st.write(f"**{i}.**")
            with col2:
                st.success(f"✅ {step}")

    # Show workflow nodes
    if result.workflow_nodes:
        st.divider()
        st.write("**Workflow Nodes:**")
        for node in result.workflow_nodes:
            node_info = f"• {node.key if hasattr(node, 'key') else str(node)}"
            st.text(node_info)

    # Show workflow edges
    if result.workflow_edges:
        st.divider()
        st.write("**Workflow Edges:**")
        for edge in result.workflow_edges:
            if hasattr(edge, 'src_key') and hasattr(edge, 'dst_key'):
                edge_info = f"  {edge.src_key} → {edge.dst_key}"
            else:
                edge_info = f"  {str(edge)}"
            st.text(edge_info)


# -----------------------------------------------------------------
# Knowledge Graph Dashboard Tab
# -----------------------------------------------------------------
def _render_kg_dashboard() -> None:
    """
    Render KG state visualization.
    
    Shows node/edge counts, template reuse, step bindings,
    and parallel execution proof.
    
    V5.0 additions:
    - P2: KG Search (graph traversal queries)
    - P2: Confidence Display (confidence badges on templates)
    - P3: Enhanced Cache Stats (hit rate, bypass rate)
    """
    st.subheader("🧠 Knowledge Graph State")
    
    # Refresh button
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔄 Refresh KG", key="refresh_kg"):
            st.session_state.kg_after = _query_kg_state()
            st.session_state.kg_search_results = None
    
    # -----------------------------------------------------------------
    # P2: KG Search (V5.0 Production Hardening)
    # -----------------------------------------------------------------
    st.divider()
    st.subheader("🔍 KG Search (Graph Traversal)")
    
    search_col1, search_col2, search_col3 = st.columns(3)
    
    with search_col1:
        kg_entity = st.text_input(
            "Entity name",
            key="kg_search_entity",
            placeholder="e.g., Payment, Customer",
        )
    
    with search_col2:
        kg_endpoint = st.text_input(
            "Endpoint path",
            key="kg_search_endpoint",
            placeholder="e.g., /checkout/sessions",
        )
    
    with search_col3:
        kg_pattern = st.text_input(
            "Pattern key",
            key="kg_search_pattern",
            placeholder="e.g., pattern.crud_create",
        )
    
    search_opt_col1, search_opt_col2, search_opt_col3 = st.columns([2, 1, 1])
    
    with search_opt_col1:
        kg_provider = st.text_input(
            "Provider filter (optional)",
            key="kg_search_provider",
            placeholder="e.g., stripe",
        )
    
    with search_opt_col2:
        kg_depth = st.number_input(
            "Max depth",
            min_value=1,
            max_value=10,
            value=3,
            key="kg_search_depth",
        )
    
    with search_opt_col3:
        if st.button("🔎 Search", key="kg_do_search", type="primary"):
            if not kg_entity and not kg_endpoint and not kg_pattern:
                st.warning("⚠️ Enter at least one of: entity, endpoint, or pattern")
            else:
                with st.spinner("Searching KG..."):
                    search_result = _kg_search(
                        entity=kg_entity if kg_entity else None,
                        endpoint=kg_endpoint if kg_endpoint else None,
                        pattern=kg_pattern if kg_pattern else None,
                        provider=kg_provider if kg_provider else None,
                        max_depth=int(kg_depth),
                    )
                    st.session_state.kg_search_results = search_result
    
    # Display search results
    if st.session_state.get("kg_search_results"):
        search_result = st.session_state.kg_search_results
        
        if search_result.get("success"):
            results = search_result.get("results", [])
            kg_stats = search_result.get("kg_stats", {})
            
            if kg_stats:
                st.caption(f"📊 KG contains {sum(kg_stats.values())} nodes")
            
            if results:
                st.success(f"🔍 Found {len(results)} related tasks")
                
                for i, match in enumerate(results, 1):
                    with st.expander(
                        f"{i}. {match['task_key']} ({match.get('graph_distance', '?')} hops)",
                        expanded=i <= 3,
                    ):
                        if match.get('provider_code'):
                            st.text(f"Provider: {match['provider_code']}")
                        if match.get('task_description'):
                            desc = match['task_description']
                            st.text(f"Description: {desc[:100]}..." if len(desc) > 100 else f"Description: {desc}")
                        if match.get('associated_templates'):
                            st.text(f"Templates: {', '.join(match['associated_templates'][:5])}")
            else:
                st.info("No related tasks found. Try increasing depth or adjusting query.")
        else:
            st.error(f"❌ Search failed: {search_result.get('error', 'Unknown error')}")
    
    st.divider()
    
    # Query current KG state
    kg_state = _query_kg_state()
    
    if kg_state is None:
        st.warning("Could not connect to database. Ensure DATABASE_URL is set.")
        return
    
    # Summary metrics
    st.subheader("📊 KG Table Counts")
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Nodes", kg_state.get("nodes", 0))
    with col2:
        st.metric("Edges", kg_state.get("edges", 0))
    with col3:
        st.metric("Workflow Steps", kg_state.get("workflow_steps", 0))
    with col4:
        st.metric("Step Bindings", kg_state.get("step_bindings", 0))
    
    # Node types breakdown
    if kg_state.get("node_types"):
        st.subheader("🏷️ Node Types")
        for node_type, count in kg_state["node_types"].items():
            st.text(f"  {node_type}: {count}")
    
    # -----------------------------------------------------------------
    # P2: Confidence Display on Workflow Templates (V5.0)
    # -----------------------------------------------------------------
    if kg_state.get("templates"):
        st.subheader("📋 Workflow Templates (with Confidence)")
        for tmpl in kg_state["templates"]:
            # Get confidence score for this template
            confidence = _get_template_confidence(tmpl['key'])
            
            # Build title with confidence badge
            conf_badge = ""
            if confidence is not None:
                if confidence >= 0.8:
                    conf_badge = f" 🟢 {confidence:.0%}"
                elif confidence >= 0.5:
                    conf_badge = f" 🟡 {confidence:.0%}"
                else:
                    conf_badge = f" 🔴 {confidence:.0%}"
            
            with st.expander(f"📄 {tmpl['key']}{conf_badge}", expanded=False):
                st.write(f"**Name:** {tmpl.get('name', 'N/A')}")
                st.write(f"**Usage Count:** {tmpl.get('usage_count', 0)}")
                st.write(f"**Has Embedding:** {tmpl.get('has_embedding', False)}")
                if confidence is not None:
                    st.write(f"**Confidence Score:** {confidence:.1%}")
                    if confidence < 0.5:
                        st.caption("⚠️ Low confidence - needs more positive feedback")
    
    # Standard patterns
    if kg_state.get("patterns"):
        st.subheader("🎯 Standard Patterns")
        for pattern in kg_state["patterns"]:
            st.text(f"  {pattern['key']}: {pattern.get('name', 'N/A')}")
    
    # Parallel execution proof
    st.divider()
    st.subheader("⚡ Parallel Execution Proof")
    timing_data = _query_parallel_timings()
    
    if timing_data:
        col1, col2, col3 = st.columns(3)
        with col1:
            embed_ms = timing_data.get("embed_spec_chunks", 0)
            st.metric("embed_spec_chunks", f"{embed_ms:.0f}ms")
        with col2:
            task_ms = timing_data.get("understand_task", 0)
            st.metric("understand_task", f"{task_ms:.0f}ms")
        with col3:
            sync_ms = timing_data.get("sync_embed_task", 0)
            st.metric("sync_embed_task", f"{sync_ms:.0f}ms")
        
        # Calculate speedup
        sequential = embed_ms + task_ms
        parallel = max(embed_ms, task_ms) + sync_ms
        if parallel > 0:
            speedup = sequential / parallel
            if speedup > 1.1:
                st.success(f"✅ **Parallel Speedup: {speedup:.2f}x** (sequential would be {sequential:.0f}ms, parallel achieved {parallel:.0f}ms)")
            else:
                st.info(f"Speedup: {speedup:.2f}x (sequential: {sequential:.0f}ms, parallel: {parallel:.0f}ms)")
    else:
        st.info("No parallel timing data available yet. Run an integration to see metrics.")
    
    # -----------------------------------------------------------------
    # P3: Enhanced Cache Stats (V5.0)
    # -----------------------------------------------------------------
    st.divider()
    st.subheader("🗃️ LLM & Spec Cache Statistics")
    
    # Spec cache (DB-based)
    spec_cache_stats = _query_cache_stats()
    if spec_cache_stats:
        col1, col2 = st.columns(2)
        with col1:
            st.metric("Spec Cache Hits", spec_cache_stats.get("hits", 0))
        with col2:
            st.metric("Spec Cache Misses", spec_cache_stats.get("misses", 0))
        
        if spec_cache_stats.get("hits", 0) > 0:
            st.success("✅ Spec caching is working!")
    
    # LLM cache (Redis-based) - P3 Enhanced Stats
    llm_cache_stats = _get_llm_cache_stats()
    if llm_cache_stats and llm_cache_stats.get("available"):
        st.caption("**LLM Cache (Redis):**")
        
        metrics = llm_cache_stats.get("metrics", {})
        stats = llm_cache_stats.get("stats", {})
        
        llm_col1, llm_col2, llm_col3, llm_col4 = st.columns(4)
        with llm_col1:
            st.metric("LLM Hits", metrics.get("hits", stats.get("hits", 0)))
        with llm_col2:
            st.metric("LLM Misses", metrics.get("misses", stats.get("misses", 0)))
        with llm_col3:
            hit_rate = metrics.get("hit_rate", 0)
            st.metric("Hit Rate", f"{hit_rate:.1%}" if hit_rate else "N/A")
        with llm_col4:
            bypass_rate = metrics.get("bypass_rate", 0)
            st.metric("Bypass Rate", f"{bypass_rate:.1%}" if bypass_rate else "N/A")
        
        # Show additional stats if available
        if stats.get("entry_count") or stats.get("size_bytes"):
            st.caption(
                f"Entries: {stats.get('entry_count', 0)} | "
                f"Size: {stats.get('size_bytes', 0) / 1024:.1f} KB"
            )
    elif llm_cache_stats:
        st.info("ℹ️ LLM Cache not available (Redis not connected)")


def _query_kg_state() -> Optional[Dict[str, Any]]:
    """Query current KG state from database."""
    try:
        from integration_coworker.persistence.postgres import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            result = {}
            
            # Table counts
            for table in ['nodes', 'edges', 'workflow_steps', 'step_bindings']:
                try:
                    cur.execute(f'SELECT COUNT(*) FROM kg.{table}')
                    result[table] = cur.fetchone()[0]
                except Exception:
                    result[table] = 0
            
            # Node types
            try:
                cur.execute('SELECT node_type, COUNT(*) FROM kg.nodes GROUP BY node_type ORDER BY count DESC')
                result["node_types"] = {row[0]: row[1] for row in cur.fetchall()}
            except Exception:
                result["node_types"] = {}
            
            # Workflow templates
            try:
                cur.execute('''
                    SELECT key, name, usage_count, embedding IS NOT NULL as has_embedding
                    FROM kg.nodes 
                    WHERE node_type = 'workflow_template'
                    ORDER BY usage_count DESC
                    LIMIT 10
                ''')
                result["templates"] = [
                    {"key": row[0], "name": row[1], "usage_count": row[2], "has_embedding": row[3]}
                    for row in cur.fetchall()
                ]
            except Exception:
                result["templates"] = []
            
            # Patterns
            try:
                cur.execute("SELECT key, name FROM kg.nodes WHERE node_type = 'pattern' ORDER BY key")
                result["patterns"] = [{"key": row[0], "name": row[1]} for row in cur.fetchall()]
            except Exception:
                result["patterns"] = []
            
            return result
            
    except Exception as e:
        st.error(f"KG query failed: {e}")
        return None


def _query_parallel_timings() -> Optional[Dict[str, float]]:
    """Query node timing data from recent checkpoints."""
    try:
        from integration_coworker.persistence.postgres import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            cur.execute('''
                SELECT 
                    state_json->'node_timings'->'embed_spec_chunks' as embed_time,
                    state_json->'node_timings'->'understand_task' as task_time,
                    state_json->'node_timings'->'sync_embed_task' as sync_time
                FROM integration_gold.run_checkpoints
                WHERE state_json->'node_timings' ? 'embed_spec_chunks'
                  AND state_json->'node_timings' ? 'understand_task'
                ORDER BY created_at DESC
                LIMIT 1
            ''')
            
            row = cur.fetchone()
            if row:
                return {
                    "embed_spec_chunks": float(row[0]) if row[0] else 0,
                    "understand_task": float(row[1]) if row[1] else 0,
                    "sync_embed_task": float(row[2]) if row[2] else 0,
                }
            return None
            
    except Exception:
        return None


def _query_cache_stats() -> Optional[Dict[str, int]]:
    """Query spec cache hit/miss statistics."""
    try:
        from integration_coworker.persistence.postgres import get_connection
        
        with get_connection() as conn:
            cur = conn.cursor()
            
            # Count cache hits
            cur.execute('''
                SELECT COUNT(*) FROM integration_gold.run_checkpoints
                WHERE state_json->>'cache_hit' = 'true'
            ''')
            hits = cur.fetchone()[0]
            
            # Count cache misses
            cur.execute('''
                SELECT COUNT(*) FROM integration_gold.run_checkpoints
                WHERE state_json->>'cache_hit' = 'false'
                   OR state_json->>'cache_hit' IS NULL
            ''')
            misses = cur.fetchone()[0]
            
            return {"hits": hits, "misses": misses}
            
    except Exception:
        return None


# -----------------------------------------------------------------
# Errors & Recovery Tab
# -----------------------------------------------------------------
def _render_errors_and_recovery() -> None:
    """Render error information and recovery actions."""
    error = st.session_state.last_error
    result = st.session_state.last_result

    # Show result errors (non-fatal)
    if result and result.errors:
        st.subheader("⚠️ Warnings/Errors from Run")
        for err in result.errors:
            if "Warning:" in err:
                st.warning(err)
            else:
                st.error(err)
        st.divider()

    # Show fatal error (if any)
    if error:
        st.subheader("❌ Last Error")

        st.error(error["error"])

        if error.get("traceback"):
            with st.expander("🔍 Full Traceback", expanded=False):
                st.code(error["traceback"], language="python")

        st.caption(f"Occurred at: {error.get('timestamp', 'Unknown')}")

        # Recovery actions
        st.divider()
        st.subheader("🔄 Recovery Actions")

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("🔁 Retry", type="primary", use_container_width=True,
                         help="Re-run with the same inputs"):
                _handle_recovery_action("retry", error.get("inputs", {}))

        with col2:
            if st.button("⏭️ Skip", use_container_width=True,
                         help="Skip the failed step (if possible)"):
                _handle_recovery_action("skip", error.get("inputs", {}))

        with col3:
            if st.button("🔄 Restart", type="secondary", use_container_width=True,
                         help="Clear state and start fresh"):
                _handle_recovery_action("restart", {})

    elif not result:
        st.info("No errors to display. Run an integration to see results.")
    else:
        st.success("✅ Last run completed without errors.")


def _handle_recovery_action(action: str, context: Dict[str, Any]) -> None:
    """Handle a recovery action (retry, skip, restart)."""
    st.session_state.pending_recovery_action = action

    if action == "restart":
        # Clear all state
        st.session_state.last_result = None
        st.session_state.last_error = None
        st.session_state.pending_recovery_action = None
        st.session_state.current_run_id = None
        st.rerun()

    elif action == "retry" and context:
        # Re-run with same inputs
        _run_integration(
            spec_refs=context.get("spec_refs", []),
            task_description=context.get("task_description", ""),
            provider_code=context.get("provider_code"),
            repo_root=context.get("repo_root"),
            dry_run=context.get("dry_run", True),
            verbose=False,
        )
        st.rerun()

    elif action == "skip":
        # P1: Wire skip to skip_failing_step (V5.1)
        run_id = st.session_state.get("current_run_id")
        failed_step = context.get("failed_step") or context.get("current_node") or "unknown"
        
        if run_id:
            skip_result = _skip_failing_step(run_id, failed_step)
            
            if skip_result.get("success"):
                st.success(f"✅ Skipped step '{failed_step}' and its dependents")
                
                # Show skipped cascade info
                warnings = skip_result.get("warnings", [])
                if warnings:
                    with st.expander("⚠️ Skip Cascade Info", expanded=False):
                        for warn in warnings[:10]:
                            st.warning(warn)
                
                # Clear error state
                st.session_state.last_error = None
                st.session_state.pending_recovery_action = None
                st.rerun()
            else:
                st.error(f"❌ Skip failed: {skip_result.get('error', 'Unknown error')}")
        else:
            st.warning("⚠️ Skip requires a run ID. Use 'Restart' for a fresh run.")


# -----------------------------------------------------------------
# Helper Functions
# -----------------------------------------------------------------
def _snapshot_run(result: Any, error: Optional[Dict[str, Any]]) -> None:
    """Add a run snapshot to history."""
    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "run_id": result.run_id if result else None,
        "success": error is None,
        "provider_code": result.task.provider_code if result and result.task else None,
        "error_summary": error.get("error") if error else None,
    }
    st.session_state.run_history.append(snapshot)

    # Keep only last 10 runs
    if len(st.session_state.run_history) > 10:
        st.session_state.run_history = st.session_state.run_history[-10:]


# -----------------------------------------------------------------
# App Runner
# -----------------------------------------------------------------
if __name__ == "__main__":
    main()

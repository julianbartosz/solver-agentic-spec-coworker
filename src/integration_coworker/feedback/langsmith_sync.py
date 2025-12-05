"""
LangSmith Feedback Sync

Syncs feedback from LangSmith API to our KG feedback_records table.
This enables learning from human ratings and automated evaluations.

Usage:
    from integration_coworker.feedback import sync_langsmith_feedback
    
    # Sync recent feedback (last 24 hours)
    synced_count = sync_langsmith_feedback()
    
    # Sync specific run
    feedback = fetch_feedback_for_run("run_abc123")

LangSmith API:
    - client.list_feedback(): Get feedback for runs
    - client.create_feedback(): Submit feedback (for CLI)
    
Per docs/decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any

from integration_coworker.domain.models import (
    FeedbackRecord,
    FeedbackType,
    FeedbackSource,
)
from integration_coworker.persistence import db

logger = logging.getLogger(__name__)


# Check LangSmith availability
_langsmith_client = None


def _get_langsmith_client():
    """
    Get or create LangSmith client.
    
    Returns None if LangSmith is not configured.
    """
    global _langsmith_client
    
    if _langsmith_client is not None:
        return _langsmith_client
    
    # Check for API key
    api_key = os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
    if not api_key:
        logger.debug("LangSmith not configured: no API key found")
        return None
    
    try:
        from langsmith import Client
        _langsmith_client = Client()
        return _langsmith_client
    except ImportError:
        logger.warning("langsmith package not installed")
        return None
    except Exception as e:
        logger.warning(f"Failed to create LangSmith client: {e}")
        return None


def is_langsmith_available() -> bool:
    """Check if LangSmith is available and configured."""
    return _get_langsmith_client() is not None


def _normalize_score(score: Any, feedback_key: str) -> float:
    """
    Normalize feedback score to 0-1 range.
    
    Handles various feedback formats:
    - Boolean (True/False) → 1.0/0.0
    - Integer (0-1 or 0-5 or 0-10) → normalized to 0-1
    - Float (0.0-1.0) → as-is
    - String ("positive"/"negative") → 1.0/0.0
    """
    if score is None:
        return 0.5  # Neutral default
    
    if isinstance(score, bool):
        return 1.0 if score else 0.0
    
    if isinstance(score, (int, float)):
        if score <= 1:
            return float(max(0.0, min(1.0, score)))
        elif score <= 5:
            return score / 5.0
        elif score <= 10:
            return score / 10.0
        else:
            return 0.5  # Unknown scale
    
    if isinstance(score, str):
        lower = score.lower()
        if lower in ("positive", "good", "yes", "thumbs_up", "👍"):
            return 1.0
        elif lower in ("negative", "bad", "no", "thumbs_down", "👎"):
            return 0.0
        else:
            return 0.5
    
    return 0.5


def _infer_feedback_type(feedback_key: str, score: Any) -> FeedbackType:
    """
    Infer feedback type from LangSmith feedback key and score.
    
    Common keys: "user_feedback", "thumbs", "correctness", "quality"
    """
    key_lower = feedback_key.lower()
    
    if "thumb" in key_lower:
        return FeedbackType.THUMBS
    
    if isinstance(score, bool):
        return FeedbackType.THUMBS
    
    return FeedbackType.SCORE


def fetch_feedback_for_run(run_id: str) -> List[FeedbackRecord]:
    """
    Fetch all feedback for a specific run from LangSmith.
    
    Args:
        run_id: Our run_id (which is also the LangSmith trace_id)
        
    Returns:
        List of FeedbackRecord objects
    """
    client = _get_langsmith_client()
    if not client:
        logger.debug("LangSmith not available, returning empty feedback")
        return []
    
    try:
        # LangSmith uses trace_id/run_id interchangeably
        feedbacks = list(client.list_feedback(run_ids=[run_id]))
        
        records = []
        for fb in feedbacks:
            fb_type = _infer_feedback_type(fb.key, fb.score)
            normalized_score = _normalize_score(fb.score, fb.key)
            
            record = FeedbackRecord(
                run_id=run_id,
                feedback_type=fb_type,
                score=normalized_score,
                comment=fb.comment,
                source=FeedbackSource.LANGSMITH,
                langsmith_feedback_id=str(fb.id) if fb.id else None,
                created_at=fb.created_at.isoformat() if fb.created_at else None,
            )
            records.append(record)
        
        return records
        
    except Exception as e:
        logger.warning(f"Failed to fetch feedback for run {run_id}: {e}")
        return []


def _get_template_key_for_run(run_id: str) -> Optional[str]:
    """
    Look up which template was used for a given run.
    
    Queries persisted_ids from run_checkpoints or run_status.
    """
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        is_postgres = db.get_engine_type() == "postgres"
        
        if is_postgres:
            # Try run_checkpoints first (has state_json with persisted_ids)
            cur.execute("""
                SELECT state_json->'persisted_ids'->>'kg_template_node_id',
                       state_json->'plan'->>'matched_template_id'
                FROM integration_gold.run_checkpoints
                WHERE run_id = %s AND node_name = 'persist_kg_learning'
                LIMIT 1
            """, (run_id,))
        else:
            # SQLite JSON extraction
            cur.execute("""
                SELECT json_extract(state_json, '$.persisted_ids.kg_template_node_id'),
                       json_extract(state_json, '$.plan.matched_template_id')
                FROM run_checkpoints
                WHERE run_id = ? AND node_name = 'persist_kg_learning'
                LIMIT 1
            """, (run_id,))
        
        row = cur.fetchone()
        if row:
            # Prefer matched_template_id (the key), fall back to node_id
            if row[1]:
                return str(row[1])
            elif row[0]:
                # Need to look up the key by node_id
                if is_postgres:
                    cur.execute(
                        "SELECT key FROM kg.nodes WHERE id = %s",
                        (int(row[0]),)
                    )
                else:
                    cur.execute(
                        "SELECT key FROM kg_nodes WHERE id = ?",
                        (int(row[0]),)
                    )
                key_row = cur.fetchone()
                if key_row:
                    return key_row[0]
        
        return None
        
    except Exception as e:
        logger.debug(f"Could not find template for run {run_id}: {e}")
        return None


def _upsert_feedback_record(record: FeedbackRecord) -> Optional[int]:
    """
    Insert or update a feedback record in the database.
    
    Returns the record ID.
    """
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        is_postgres = db.get_engine_type() == "postgres"
        
        if is_postgres:
            # For records with langsmith_feedback_id, use ON CONFLICT
            # For implicit signals (langsmith_feedback_id is NULL), just insert
            # (they're one-off records that don't need upsert)
            if record.langsmith_feedback_id is not None:
                # Use partial unique index: kg_feedback_run_ls_idx
                # The WHERE clause matches the index filter (langsmith_feedback_id IS NOT NULL)
                cur.execute("""
                    INSERT INTO kg.feedback_records 
                        (run_id, template_key, pattern_key, feedback_type, score, 
                         comment, source, langsmith_feedback_id, created_at, synced_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (run_id, langsmith_feedback_id)
                    DO UPDATE SET
                        score = EXCLUDED.score,
                        comment = EXCLUDED.comment,
                        synced_at = NOW()
                    RETURNING id
                """, (
                    record.run_id,
                    record.template_key,
                    record.pattern_key,
                    record.feedback_type.value if isinstance(record.feedback_type, FeedbackType) else record.feedback_type,
                    record.score,
                    record.comment,
                    record.source.value if isinstance(record.source, FeedbackSource) else record.source,
                    record.langsmith_feedback_id,
                    record.created_at,
                ))
            else:
                # For implicit signals without langsmith_feedback_id, just insert
                cur.execute("""
                    INSERT INTO kg.feedback_records 
                        (run_id, template_key, pattern_key, feedback_type, score, 
                         comment, source, langsmith_feedback_id, created_at, synced_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    RETURNING id
                """, (
                    record.run_id,
                    record.template_key,
                    record.pattern_key,
                    record.feedback_type.value if isinstance(record.feedback_type, FeedbackType) else record.feedback_type,
                    record.score,
                    record.comment,
                    record.source.value if isinstance(record.source, FeedbackSource) else record.source,
                    record.langsmith_feedback_id,
                    record.created_at,
                ))
            conn.commit()
            row = cur.fetchone()
            return row[0] if row else None
        else:
            # SQLite version
            cur.execute("""
                INSERT INTO kg_feedback_records 
                    (run_id, template_key, pattern_key, feedback_type, score, 
                     comment, source, langsmith_feedback_id, created_at, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(run_id, COALESCE(langsmith_feedback_id, '')) 
                DO UPDATE SET
                    score = excluded.score,
                    comment = excluded.comment,
                    synced_at = datetime('now')
            """, (
                record.run_id,
                record.template_key,
                record.pattern_key,
                record.feedback_type.value if isinstance(record.feedback_type, FeedbackType) else record.feedback_type,
                record.score,
                record.comment,
                record.source.value if isinstance(record.source, FeedbackSource) else record.source,
                record.langsmith_feedback_id,
                record.created_at,
            ))
            conn.commit()
            return cur.lastrowid
            
    except Exception as e:
        logger.error(f"Failed to upsert feedback record: {e}")
        return None


def sync_langsmith_feedback(
    days: int = 1,
    project_name: Optional[str] = None,
    run_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Sync feedback from LangSmith to our database.
    
    Args:
        days: How many days back to sync (default: 1)
        project_name: LangSmith project name (default: from env)
        run_ids: Specific run IDs to sync (overrides days)
        
    Returns:
        Dict with sync statistics:
        {
            "synced": int,
            "skipped": int,
            "errors": int,
            "templates_updated": List[str],
        }
    """
    client = _get_langsmith_client()
    if not client:
        return {
            "synced": 0,
            "skipped": 0,
            "errors": 0,
            "templates_updated": [],
            "error": "LangSmith not available",
        }
    
    stats = {
        "synced": 0,
        "skipped": 0,
        "errors": 0,
        "templates_updated": set(),
    }
    
    try:
        # Get project name
        if not project_name:
            project_name = os.getenv("LANGCHAIN_PROJECT", "default")
        
        # Fetch runs to get feedback for
        if run_ids:
            target_run_ids = run_ids
        else:
            # Get recent runs from LangSmith
            start_time = datetime.now(timezone.utc) - timedelta(days=days)
            
            try:
                runs = list(client.list_runs(
                    project_name=project_name,
                    start_time=start_time,
                    is_root=True,  # Only top-level runs
                    limit=500,
                ))
                target_run_ids = [str(r.id) for r in runs]
            except Exception as e:
                logger.warning(f"Could not list runs: {e}")
                target_run_ids = []
        
        logger.info(f"Syncing feedback for {len(target_run_ids)} runs")
        
        # Fetch and store feedback for each run
        for run_id in target_run_ids:
            try:
                feedbacks = fetch_feedback_for_run(run_id)
                
                if not feedbacks:
                    stats["skipped"] += 1
                    continue
                
                # Get template key for this run
                template_key = _get_template_key_for_run(run_id)
                
                for fb in feedbacks:
                    fb.template_key = template_key
                    
                    record_id = _upsert_feedback_record(fb)
                    if record_id:
                        stats["synced"] += 1
                        if template_key:
                            stats["templates_updated"].add(template_key)
                    else:
                        stats["errors"] += 1
                        
            except Exception as e:
                logger.warning(f"Failed to sync feedback for run {run_id}: {e}")
                stats["errors"] += 1
        
        # Convert set to list for JSON serialization
        stats["templates_updated"] = list(stats["templates_updated"])
        
        logger.info(
            f"Feedback sync complete: {stats['synced']} synced, "
            f"{stats['skipped']} skipped, {stats['errors']} errors"
        )
        
        return stats
        
    except Exception as e:
        logger.error(f"Feedback sync failed: {e}")
        return {
            "synced": 0,
            "skipped": 0,
            "errors": 1,
            "templates_updated": [],
            "error": str(e),
        }


def create_feedback(
    run_id: str,
    score: float,
    feedback_type: FeedbackType = FeedbackType.THUMBS,
    comment: Optional[str] = None,
    also_langsmith: bool = True,
) -> Optional[int]:
    """
    Create a feedback record (manual submission).
    
    Args:
        run_id: The run to provide feedback for
        score: Score (0-1)
        feedback_type: Type of feedback
        comment: Optional comment
        also_langsmith: Also submit to LangSmith (default: True)
        
    Returns:
        Feedback record ID
    """
    # Get template key for this run
    template_key = _get_template_key_for_run(run_id)
    
    record = FeedbackRecord(
        run_id=run_id,
        template_key=template_key,
        feedback_type=feedback_type,
        score=score,
        comment=comment,
        source=FeedbackSource.CLI,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    
    record_id = _upsert_feedback_record(record)
    
    # Also submit to LangSmith
    if also_langsmith and is_langsmith_available():
        try:
            client = _get_langsmith_client()
            client.create_feedback(
                run_id=run_id,
                key="cli_feedback",
                score=score,
                comment=comment,
            )
            logger.debug(f"Also submitted feedback to LangSmith for run {run_id}")
        except Exception as e:
            logger.warning(f"Failed to submit to LangSmith: {e}")
    
    return record_id

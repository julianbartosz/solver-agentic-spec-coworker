"""
Confidence Score Computation

Aggregates feedback into confidence scores for KG nodes.
Updates kg.nodes.confidence_score to influence GraphRAG scoring.

Algorithm:
    - Weight by source (human > auto)
    - Weight by feedback type (thumbs/score > compile > lint)
    - Apply recency decay (half-life 30 days)
    - Clamp to [0, 1] range

Per docs/decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from integration_coworker.domain.models import (
    FeedbackRecord,
    FeedbackType,
    FeedbackSource,
    ConfidenceUpdate,
)
from integration_coworker.persistence import db

logger = logging.getLogger(__name__)


# Weight by feedback source
SOURCE_WEIGHTS = {
    FeedbackSource.LANGSMITH.value: 1.0,
    FeedbackSource.CLI.value: 1.0,
    FeedbackSource.API.value: 0.8,
    FeedbackSource.AUTO.value: 0.4,
    "langsmith": 1.0,
    "cli": 1.0,
    "api": 0.8,
    "auto": 0.4,
}

# Weight by feedback type
TYPE_WEIGHTS = {
    FeedbackType.THUMBS.value: 1.0,
    FeedbackType.SCORE.value: 1.0,
    FeedbackType.AUTO_COMPILE.value: 0.3,
    FeedbackType.AUTO_TEST.value: 0.5,
    FeedbackType.AUTO_LINT.value: 0.2,
    "thumbs": 1.0,
    "score": 1.0,
    "auto_compile": 0.3,
    "auto_test": 0.5,
    "auto_lint": 0.2,
}

# Recency half-life in days
RECENCY_HALF_LIFE_DAYS = 30

# Default confidence for nodes without feedback
DEFAULT_CONFIDENCE = 0.5


def _parse_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO datetime string to datetime object."""
    if not dt_str:
        return None
    try:
        # Handle various ISO formats
        if dt_str.endswith('Z'):
            dt_str = dt_str[:-1] + '+00:00'
        return datetime.fromisoformat(dt_str)
    except Exception:
        return None


def compute_confidence_score(feedbacks: List[FeedbackRecord]) -> float:
    """
    Aggregate feedback into a single confidence score (0-1).
    
    Uses weighted average with:
    - Source weight (human feedback weighted higher)
    - Type weight (direct scores weighted higher than auto signals)
    - Recency decay (exponential with 30-day half-life)
    
    Args:
        feedbacks: List of FeedbackRecord objects
        
    Returns:
        Confidence score in [0, 1] range
    """
    if not feedbacks:
        return DEFAULT_CONFIDENCE
    
    weighted_sum = 0.0
    total_weight = 0.0
    now = datetime.now(timezone.utc)
    
    for fb in feedbacks:
        # Get source and type weights
        source = fb.source.value if isinstance(fb.source, FeedbackSource) else fb.source
        fb_type = fb.feedback_type.value if isinstance(fb.feedback_type, FeedbackType) else fb.feedback_type
        
        source_weight = SOURCE_WEIGHTS.get(source, 0.5)
        type_weight = TYPE_WEIGHTS.get(fb_type, 0.5)
        
        # Compute recency weight
        created_at = _parse_datetime(fb.created_at)
        if created_at:
            # Ensure timezone-aware comparison
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_days = (now - created_at).total_seconds() / 86400
            recency_weight = 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS)
        else:
            recency_weight = 0.5  # Unknown age, use moderate weight
        
        # Combined weight
        combined_weight = source_weight * type_weight * recency_weight
        
        weighted_sum += fb.score * combined_weight
        total_weight += combined_weight
    
    if total_weight == 0:
        return DEFAULT_CONFIDENCE
    
    # Clamp to [0, 1]
    return max(0.0, min(1.0, weighted_sum / total_weight))


def get_feedback_for_template(template_key: str) -> List[FeedbackRecord]:
    """
    Get all feedback records for a template.
    
    Args:
        template_key: The kg.nodes key for the template
        
    Returns:
        List of FeedbackRecord objects
    """
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        is_postgres = db.get_engine_type() == "postgres"
        
        if is_postgres:
            cur.execute("""
                SELECT id, run_id, template_key, pattern_key, feedback_type,
                       score, comment, source, langsmith_feedback_id,
                       created_at, synced_at
                FROM kg.feedback_records
                WHERE template_key = %s
                ORDER BY created_at DESC
            """, (template_key,))
        else:
            cur.execute("""
                SELECT id, run_id, template_key, pattern_key, feedback_type,
                       score, comment, source, langsmith_feedback_id,
                       created_at, synced_at
                FROM kg_feedback_records
                WHERE template_key = ?
                ORDER BY created_at DESC
            """, (template_key,))
        
        rows = cur.fetchall()
        
        records = []
        for row in rows:
            records.append(FeedbackRecord(
                id=row[0],
                run_id=row[1],
                template_key=row[2],
                pattern_key=row[3],
                feedback_type=FeedbackType(row[4]) if row[4] else FeedbackType.THUMBS,
                score=row[5] or 0.0,
                comment=row[6],
                source=FeedbackSource(row[7]) if row[7] else FeedbackSource.LANGSMITH,
                langsmith_feedback_id=row[8],
                created_at=row[9],
                synced_at=row[10],
            ))
        
        return records
        
    except Exception as e:
        logger.warning(f"Failed to get feedback for template {template_key}: {e}")
        return []


def get_confidence_for_template(template_key: str) -> float:
    """
    Get current confidence score for a template.
    
    Fetches feedback and computes confidence.
    Returns DEFAULT_CONFIDENCE if no feedback.
    """
    feedbacks = get_feedback_for_template(template_key)
    return compute_confidence_score(feedbacks)


def update_node_confidence(
    node_key: str,
    reason: Optional[str] = None,
) -> Optional[float]:
    """
    Update confidence_score for a KG node based on its feedback.
    
    Args:
        node_key: The kg.nodes key to update
        reason: Optional reason for the update (for audit trail)
        
    Returns:
        New confidence score, or None if failed
    """
    try:
        # Get feedback for this node
        feedbacks = get_feedback_for_template(node_key)
        new_confidence = compute_confidence_score(feedbacks)
        
        conn = db.get_connection()
        cur = conn.cursor()
        is_postgres = db.get_engine_type() == "postgres"
        
        # Get current confidence
        if is_postgres:
            cur.execute(
                "SELECT confidence_score FROM kg.nodes WHERE key = %s",
                (node_key,)
            )
        else:
            cur.execute(
                "SELECT confidence_score FROM kg_nodes WHERE key = ?",
                (node_key,)
            )
        
        row = cur.fetchone()
        old_confidence = row[0] if row else None
        
        if old_confidence is None:
            logger.debug(f"Node {node_key} not found, skipping confidence update")
            return None
        
        # Update confidence score
        if is_postgres:
            cur.execute("""
                UPDATE kg.nodes
                SET confidence_score = %s,
                    updated_at = NOW()
                WHERE key = %s
            """, (new_confidence, node_key))
            
            # Log to history
            cur.execute("""
                INSERT INTO kg.confidence_history
                    (node_key, old_confidence, new_confidence, feedback_count, reason)
                VALUES (%s, %s, %s, %s, %s)
            """, (node_key, old_confidence, new_confidence, len(feedbacks), reason))
        else:
            cur.execute("""
                UPDATE kg_nodes
                SET confidence_score = ?,
                    updated_at = datetime('now')
                WHERE key = ?
            """, (new_confidence, node_key))
            
            # Log to history
            cur.execute("""
                INSERT INTO kg_confidence_history
                    (node_key, old_confidence, new_confidence, feedback_count, reason)
                VALUES (?, ?, ?, ?, ?)
            """, (node_key, old_confidence, new_confidence, len(feedbacks), reason))
        
        conn.commit()
        
        logger.debug(
            f"Updated confidence for {node_key}: {old_confidence:.2f} → {new_confidence:.2f} "
            f"(based on {len(feedbacks)} feedback records)"
        )
        
        return new_confidence
        
    except Exception as e:
        logger.error(f"Failed to update confidence for {node_key}: {e}")
        return None


def update_all_confidences(
    provider_code: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Update confidence scores for all templates with feedback.
    
    Args:
        provider_code: Optional filter by provider
        reason: Reason for bulk update (e.g., "sync_from_langsmith")
        
    Returns:
        Statistics dict
    """
    stats = {
        "updated": 0,
        "unchanged": 0,
        "errors": 0,
        "templates": [],
    }
    
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        is_postgres = db.get_engine_type() == "postgres"
        
        # Get all templates with feedback
        if is_postgres:
            if provider_code:
                cur.execute("""
                    SELECT DISTINCT template_key
                    FROM kg.feedback_records
                    WHERE template_key IS NOT NULL
                      AND template_key LIKE %s
                """, (f"template.{provider_code}.%",))
            else:
                cur.execute("""
                    SELECT DISTINCT template_key
                    FROM kg.feedback_records
                    WHERE template_key IS NOT NULL
                """)
        else:
            if provider_code:
                cur.execute("""
                    SELECT DISTINCT template_key
                    FROM kg_feedback_records
                    WHERE template_key IS NOT NULL
                      AND template_key LIKE ?
                """, (f"template.{provider_code}.%",))
            else:
                cur.execute("""
                    SELECT DISTINCT template_key
                    FROM kg_feedback_records
                    WHERE template_key IS NOT NULL
                """)
        
        template_keys = [row[0] for row in cur.fetchall()]
        
        for template_key in template_keys:
            try:
                new_confidence = update_node_confidence(template_key, reason)
                if new_confidence is not None:
                    stats["updated"] += 1
                    stats["templates"].append(template_key)
                else:
                    stats["unchanged"] += 1
            except Exception as e:
                logger.warning(f"Failed to update {template_key}: {e}")
                stats["errors"] += 1
        
        logger.info(
            f"Bulk confidence update: {stats['updated']} updated, "
            f"{stats['unchanged']} unchanged, {stats['errors']} errors"
        )
        
        return stats
        
    except Exception as e:
        logger.error(f"Bulk confidence update failed: {e}")
        stats["error"] = str(e)
        return stats

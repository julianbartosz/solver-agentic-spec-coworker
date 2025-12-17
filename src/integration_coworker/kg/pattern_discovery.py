"""
Pattern Discovery Module (PL-001)

Implements dynamic pattern learning via event-log analysis.
Per docs/PATTERN_LEARNING_DESIGN.md

Pipeline stages:
1. capture: Record run events to kg_run_events
2. canonicalize: Convert workflow steps to canonical form
3. discover: Find recurring sequences across runs
4. promote: Move high-support candidates to kg.nodes
5. match: Record pattern match decisions for explainability

Usage:
    from integration_coworker.kg.pattern_discovery import (
        capture_run_events,
        discover_pattern_candidates,
        promote_pattern_candidate,
        record_pattern_match,
    )
"""

import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from integration_coworker.config import get_settings
from integration_coworker.domain.models import (
    IntegrationFlowNode,
    KGNodeType,
)
from integration_coworker.persistence import db

logger = logging.getLogger(__name__)


# =============================================================================
# RFC 8785 JSON Canonicalization (JCS)
# =============================================================================

# Signature version for forward compatibility
SIGNATURE_VERSION = "v1"


class JCSEncodingError(ValueError):
    """Raised when a value cannot be canonicalized per RFC 8785 / I-JSON."""
    pass


def jcs_canonicalize(data: Any) -> bytes:
    """
    Canonicalize data per RFC 8785 JSON Canonicalization Scheme.
    
    Uses the `rfc8785` reference implementation for correctness, with
    pre-validation to reject NaN/Infinity per I-JSON (RFC 7493).
    
    Args:
        data: Python object to canonicalize
        
    Returns:
        UTF-8 bytes of canonical JSON representation
        
    Raises:
        JCSEncodingError: If value contains NaN or Infinity
    """
    # Pre-validate to reject NaN/Infinity with clear error messages
    _validate_ijson(data)
    
    try:
        import rfc8785
        return rfc8785.dumps(data)
    except ImportError:
        # Fallback to our implementation if rfc8785 not installed
        return _jcs_encode_value(data).encode('utf-8')
    except Exception as e:
        # rfc8785 package may reject large integers - fall back to our impl
        if "exceeds safe integer" in str(e):
            logger.debug(f"Large integer in data, using fallback encoder: {e}")
            return _jcs_encode_value(data).encode('utf-8')
        raise


def _validate_ijson(value: Any, path: str = "") -> None:
    """
    Validate that value conforms to I-JSON (RFC 7493) constraints.
    
    - Rejects NaN and Infinity floats
    - Warns about large integers outside IEEE 754 safe range
    """
    import math
    
    if isinstance(value, float):
        if math.isnan(value):
            raise JCSEncodingError(f"Cannot canonicalize NaN at {path or 'root'}: not valid in I-JSON")
        if math.isinf(value):
            raise JCSEncodingError(f"Cannot canonicalize Infinity at {path or 'root'}: not valid in I-JSON")
    elif isinstance(value, int):
        if abs(value) > 2**53:
            logger.warning(f"Integer {value} at {path or 'root'} exceeds IEEE 754 double safe range")
    elif isinstance(value, dict):
        for k, v in value.items():
            _validate_ijson(v, f"{path}.{k}" if path else k)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _validate_ijson(v, f"{path}[{i}]" if path else f"[{i}]")


def _jcs_encode_value(value: Any) -> str:
    """
    Fallback JCS encoder when rfc8785 package is not available.
    
    Note: This fallback may not produce byte-identical output to the
    reference implementation for edge-case float values. Use the
    rfc8785 package for full compliance.
    """
    if value is None:
        return "null"
    elif isinstance(value, bool):
        return "true" if value else "false"
    elif isinstance(value, int):
        return str(value)
    elif isinstance(value, float):
        import math
        if math.isnan(value) or math.isinf(value):
            raise JCSEncodingError(f"Cannot canonicalize {value}: NaN/Infinity not valid in I-JSON")
        if value == 0.0:
            return "0"
        if value == int(value) and abs(value) < 2**53:
            return str(int(value))
        # Python's repr is close to ES6 but may differ for edge cases
        return repr(value)
    elif isinstance(value, str):
        # RFC 8785: Minimal JSON string escaping
        # Must escape: " \ and control chars (0x00-0x1F)
        result = []
        for c in value:
            cp = ord(c)
            if c == '"':
                result.append('\\"')
            elif c == '\\':
                result.append('\\\\')
            elif cp == 0x08:  # backspace
                result.append('\\b')
            elif cp == 0x09:  # tab
                result.append('\\t')
            elif cp == 0x0A:  # newline
                result.append('\\n')
            elif cp == 0x0C:  # form feed
                result.append('\\f')
            elif cp == 0x0D:  # carriage return
                result.append('\\r')
            elif cp < 0x20:  # other control chars
                result.append(f'\\u{cp:04x}')
            else:
                result.append(c)
        return '"' + ''.join(result) + '"'
    elif isinstance(value, list):
        items = [_jcs_encode_value(item) for item in value]
        return "[" + ",".join(items) + "]"
    elif isinstance(value, dict):
        # RFC 8785: Sort keys lexicographically by UTF-16 code unit order
        # For BMP characters, this is the same as Python's default string sort
        sorted_keys = sorted(value.keys())
        pairs = []
        for k in sorted_keys:
            if not isinstance(k, str):
                raise JCSEncodingError(f"Object key must be string, got {type(k)}")
            pairs.append(f'{_jcs_encode_value(k)}:{_jcs_encode_value(value[k])}')
        return "{" + ",".join(pairs) + "}"
    else:
        raise JCSEncodingError(f"Cannot canonicalize type {type(value)}")


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class CanonicalStep:
    """A workflow step in canonical form for pattern matching."""
    method: Optional[str] = None  # HTTP method (GET, POST, etc.)
    action: Optional[str] = None  # Semantic action (create, read, update, delete, list)
    resource_type: Optional[str] = None  # Normalized resource type
    path_template: Optional[str] = None  # Normalized path (/{resource})
    
    # Legacy fields for backward compat
    step_type: str = "api_call"
    position: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dict with deterministic key ordering for hashing.
        
        Only includes non-None values to ensure consistent hashing.
        """
        d = {}
        # Include fields in stable alphabetical order
        if self.action:
            d["action"] = self.action
        if self.method:
            d["method"] = self.method
        if self.path_template:
            d["path_template"] = self.path_template
        d["position"] = self.position  # Always include position
        if self.resource_type:
            d["resource_type"] = self.resource_type
        d["type"] = self.step_type  # Always include type
        return d
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "CanonicalStep":
        """Create from dict."""
        return cls(
            method=d.get("method"),
            action=d.get("action"),
            resource_type=d.get("resource_type"),
            path_template=d.get("path_template"),
            step_type=d.get("type", "api_call"),
            position=d.get("position", 0),
        )


@dataclass
class PatternCandidate:
    """A candidate pattern discovered from run analysis."""
    candidate_key: str
    name: str
    description: Optional[str]
    canonical_sequence: List[CanonicalStep]
    support_count: int
    provider_examples: List[str]
    first_seen_run_id: Optional[str] = None
    last_seen_run_id: Optional[str] = None
    status: str = "pending"  # 'pending', 'promoted', 'rejected', 'merged'
    signature_version: str = SIGNATURE_VERSION  # For forward compat
    signature_hash: Optional[str] = None  # Full SHA256 hash
    
    def sequence_hash(self) -> str:
        """
        Compute deterministic hash using RFC 8785 canonicalization.
        
        Ensures identical sequences produce identical hashes across:
        - Dict ordering
        - JSON formatting  
        - Python versions
        """
        return hash_canonical_sequence(self.canonical_sequence)


def hash_canonical_sequence(canonical: List[CanonicalStep]) -> str:
    """
    Compute deterministic hash for a canonical sequence using RFC 8785.
    
    Args:
        canonical: List of canonical steps
        
    Returns:
        12-character hex hash (truncated SHA-256)
    """
    # Convert steps to dicts with stable ordering
    step_dicts = [s.to_dict() for s in canonical]
    
    # Canonicalize using JCS (RFC 8785)
    canonical_bytes = jcs_canonicalize(step_dicts)
    
    # SHA-256 and truncate to 12 chars
    full_hash = hashlib.sha256(canonical_bytes).hexdigest()
    return full_hash[:12]


def hash_canonical_sequence_full(canonical: List[CanonicalStep]) -> str:
    """
    Compute full SHA-256 hash for a canonical sequence.
    
    Use this for signature_hash storage (full 64 chars).
    """
    step_dicts = [s.to_dict() for s in canonical]
    canonical_bytes = jcs_canonicalize(step_dicts)
    return hashlib.sha256(canonical_bytes).hexdigest()


# =============================================================================
# Stage 1: Event Capture
# =============================================================================

def capture_run_events(
    run_id: str,
    workflow_nodes: List[IntegrationFlowNode],
    provider_code: Optional[str] = None,
    endpoints: Optional[List[Any]] = None,
) -> int:
    """
    Capture workflow events to kg_run_events table.
    
    Called at the end of persist_kg_learning to record what happened in this run.
    
    Args:
        run_id: The run identifier
        workflow_nodes: List of workflow nodes from state.workflow_nodes
        provider_code: Provider code for this run
        endpoints: Optional list of endpoints for enrichment
        
    Returns:
        Number of events captured
    """
    settings = get_settings()
    if not settings.pattern_learning_enabled:
        logger.debug("Pattern learning disabled, skipping event capture")
        return 0
    
    if not settings.pattern_capture_events:
        logger.debug("Event capture disabled via PATTERN_CAPTURE_EVENTS=false")
        return 0
    
    if not workflow_nodes:
        logger.debug("No workflow nodes to capture")
        return 0
    
    try:
        db.init_schema()
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        # Build endpoint lookup for enrichment
        endpoint_lookup = {}
        if endpoints:
            for ep in endpoints:
                if hasattr(ep, 'path') and hasattr(ep, 'method'):
                    endpoint_lookup[ep.path] = ep.method
        
        events_captured = 0
        
        for node in sorted(workflow_nodes, key=lambda n: n.position):
            # Extract endpoint info from config if present
            endpoint_path = None
            endpoint_method = None
            if node.config:
                endpoint_path = node.config.get("endpoint_path")
                endpoint_method = node.config.get("endpoint_method")
            
            # Build attributes JSON using OTEL semantic conventions
            # See: https://opentelemetry.io/docs/specs/semconv/http/
            attributes = {
                # Core node identifiers
                "node.key": node.node_key,
                "node.label": node.config.get("label") if node.config else None,
                
                # OTEL HTTP semantic conventions
                "http.request.method": endpoint_method.upper() if endpoint_method else None,
                "url.path": endpoint_path,
                
                # Additional context
                "workflow.action": node.config.get("action") if node.config else None,
                "workflow.depends_on": node.config.get("depends_on") if node.config else None,
            }
            
            # Remove None values to keep attributes clean
            attributes = {k: v for k, v in attributes.items() if v is not None}
            
            attributes_json = json.dumps(attributes)
            
            if is_postgres:
                # ON CONFLICT DO NOTHING: dedupe unique index prevents double-capture on retries
                cur.execute("""
                    INSERT INTO kg.run_events 
                        (run_id, event_type, activity, activity_type, position,
                         provider_code, endpoint_path, endpoint_method, attributes)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, position, activity, event_type) DO NOTHING
                """, (
                    run_id,
                    "step_complete",  # We're capturing after completion
                    node.node_key,
                    node.node_type,
                    node.position,
                    provider_code,
                    endpoint_path,
                    endpoint_method,
                    attributes_json,
                ))
            else:
                # INSERT OR IGNORE: SQLite equivalent for conflict-safe inserts
                cur.execute("""
                    INSERT OR IGNORE INTO kg_run_events 
                        (run_id, event_type, activity, activity_type, position,
                         provider_code, endpoint_path, endpoint_method, attributes)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_id,
                    "step_complete",
                    node.node_key,
                    node.node_type,
                    node.position,
                    provider_code,
                    endpoint_path,
                    endpoint_method,
                    attributes_json,
                ))
            
            events_captured += 1
        
        conn.commit()
        logger.info(f"Captured {events_captured} run events for run_id={run_id}")
        return events_captured
        
    except Exception as e:
        logger.error(f"Failed to capture run events: {e}")
        return 0


# =============================================================================
# Stage 2: Canonicalization
# =============================================================================

def canonicalize_workflow(workflow_nodes: List[IntegrationFlowNode]) -> List[CanonicalStep]:
    """
    Convert workflow nodes to canonical form for pattern matching.
    
    Normalization rules:
    - Path parameters like {user_id}, {id}, {order_id} become {resource}
    - Step types are preserved
    - Positions are renumbered 0..N
    
    Args:
        workflow_nodes: List of IntegrationFlowNode
        
    Returns:
        List of CanonicalStep in position order
    """
    canonical = []
    
    sorted_nodes = sorted(workflow_nodes, key=lambda n: n.position)
    
    for i, node in enumerate(sorted_nodes):
        step = CanonicalStep(
            step_type=node.node_type or "unknown",
            position=i,  # Renumber to 0..N
        )
        
        if node.config:
            if "endpoint_method" in node.config:
                step.method = node.config["endpoint_method"].upper()
            if "endpoint_path" in node.config:
                # Normalize path: replace all {param} with {resource}
                path = re.sub(r'\{[^}]+\}', '{resource}', node.config["endpoint_path"])
                step.path_template = path
        
        canonical.append(step)
    
    return canonical


# =============================================================================
# Stage 3: Pattern Discovery
# =============================================================================

def discover_pattern_candidates(min_support: int = 3) -> List[PatternCandidate]:
    """
    Discover recurring workflow patterns from run events.
    
    Algorithm:
    1. Group completed runs by their canonical sequence hash
    2. Sequences appearing in N+ runs become candidates
    3. Filter out sequences that match existing patterns or candidates
    
    Args:
        min_support: Minimum number of runs for a sequence to be a candidate
        
    Returns:
        List of new PatternCandidate objects (not yet in DB)
    """
    settings = get_settings()
    if not settings.pattern_learning_enabled:
        return []
    
    if not settings.pattern_discover_candidates:
        logger.debug("Candidate discovery disabled via PATTERN_DISCOVER_CANDIDATES=false")
        return []
    
    try:
        db.init_schema()
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        # Get all run_ids with events
        if is_postgres:
            cur.execute("""
                SELECT DISTINCT run_id, provider_code
                FROM kg.run_events
                ORDER BY run_id
            """)
        else:
            cur.execute("""
                SELECT DISTINCT run_id, provider_code
                FROM kg_run_events
                ORDER BY run_id
            """)
        
        runs = cur.fetchall()
        
        # Group by canonical sequence hash
        sequence_groups: Dict[str, List[Dict]] = defaultdict(list)
        
        for run_row in runs:
            run_id = run_row[0]
            provider = run_row[1]
            
            # Get events for this run
            if is_postgres:
                cur.execute("""
                    SELECT activity, activity_type, position, endpoint_path, endpoint_method
                    FROM kg.run_events
                    WHERE run_id = %s
                    ORDER BY position
                """, (run_id,))
            else:
                cur.execute("""
                    SELECT activity, activity_type, position, endpoint_path, endpoint_method
                    FROM kg_run_events
                    WHERE run_id = ?
                    ORDER BY position
                """, (run_id,))
            
            events = cur.fetchall()
            
            # Convert to canonical steps
            canonical = []
            for i, ev in enumerate(events):
                step = CanonicalStep(
                    step_type=ev[1] or "unknown",
                    position=i,
                    method=ev[4].upper() if ev[4] else None,
                    path_template=re.sub(r'\{[^}]+\}', '{resource}', ev[3]) if ev[3] else None,
                )
                canonical.append(step)
            
            if canonical:
                seq_hash = hash_canonical_sequence(canonical)
                sequence_groups[seq_hash].append({
                    "run_id": run_id,
                    "provider": provider,
                    "canonical": canonical,
                })
        
        # Find candidates with sufficient support
        candidates = []
        
        # Get existing patterns and candidates for filtering
        existing_hashes = _get_existing_pattern_hashes(cur, is_postgres)
        
        for seq_hash, runs_list in sequence_groups.items():
            if len(runs_list) >= min_support and seq_hash not in existing_hashes:
                # Generate candidate key and name
                canonical = runs_list[0]["canonical"]
                candidate_key = f"candidate.{seq_hash}"
                name = _generate_pattern_name(canonical)
                description = _generate_pattern_description(canonical, runs_list)
                
                provider_examples = list(set(r["provider"] for r in runs_list if r["provider"]))[:5]
                
                candidate = PatternCandidate(
                    candidate_key=candidate_key,
                    name=name,
                    description=description,
                    canonical_sequence=canonical,
                    support_count=len(runs_list),
                    provider_examples=provider_examples,
                    first_seen_run_id=runs_list[0]["run_id"],
                    last_seen_run_id=runs_list[-1]["run_id"],
                )
                candidates.append(candidate)
        
        logger.info(f"Discovered {len(candidates)} new pattern candidates (min_support={min_support})")
        return candidates
        
    except Exception as e:
        logger.error(f"Pattern discovery failed: {e}")
        return []


def _get_existing_pattern_hashes(cur, is_postgres: bool) -> set:
    """Get hashes of existing patterns and candidates."""
    hashes = set()
    
    # Get from kg.nodes (patterns)
    if is_postgres:
        cur.execute("""
            SELECT properties->>'canonical_hash'
            FROM kg.nodes
            WHERE node_type = 'pattern'
            AND properties->>'canonical_hash' IS NOT NULL
        """)
    else:
        cur.execute("""
            SELECT json_extract(properties, '$.canonical_hash')
            FROM kg_nodes
            WHERE node_type = 'pattern'
            AND json_extract(properties, '$.canonical_hash') IS NOT NULL
        """)
    
    for row in cur.fetchall():
        if row[0]:
            hashes.add(row[0])
    
    # Get from kg.pattern_candidates
    if is_postgres:
        cur.execute("""
            SELECT candidate_key
            FROM kg.pattern_candidates
            WHERE status IN ('pending', 'promoted')
        """)
    else:
        cur.execute("""
            SELECT candidate_key
            FROM kg_pattern_candidates
            WHERE status IN ('pending', 'promoted')
        """)
    
    for row in cur.fetchall():
        # Extract hash from candidate_key (format: candidate.<hash>)
        if row[0] and row[0].startswith("candidate."):
            hashes.add(row[0].replace("candidate.", ""))
    
    return hashes


def _generate_pattern_name(canonical: List[CanonicalStep]) -> str:
    """Generate a human-readable name from canonical sequence."""
    # Extract key characteristics
    methods = [s.method for s in canonical if s.method]
    types = [s.step_type for s in canonical]
    
    # Common pattern names
    if methods == ["POST"] and "validation" in types:
        return "Create with Validation"
    elif methods == ["GET"] and "pagination" in types:
        return "List with Pagination"
    elif len(methods) >= 2 and "POST" in methods and "GET" in methods:
        return "Multi-Step Operation"
    elif methods == ["DELETE"]:
        return "Delete Operation"
    elif methods == ["PUT"] or methods == ["PATCH"]:
        return "Update Operation"
    elif "POST" in methods:
        return "Create Operation"
    elif "GET" in methods:
        return "Read Operation"
    else:
        return f"{len(canonical)}-Step Workflow"


def _generate_pattern_description(canonical: List[CanonicalStep], runs: List[Dict]) -> str:
    """Generate description from canonical sequence and usage."""
    providers = list(set(r["provider"] for r in runs if r["provider"]))[:3]
    
    steps_desc = []
    for s in canonical:
        if s.method and s.path_template:
            steps_desc.append(f"{s.method} {s.path_template}")
        elif s.step_type:
            steps_desc.append(s.step_type)
    
    desc = f"Workflow pattern: {' → '.join(steps_desc)}"
    if providers:
        desc += f". Observed in: {', '.join(providers)}"
    
    return desc


def save_pattern_candidates(candidates: List[PatternCandidate]) -> int:
    """
    Save discovered candidates to kg_pattern_candidates table.
    
    Returns number of candidates saved.
    """
    if not candidates:
        return 0
    
    try:
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        saved = 0
        
        for candidate in candidates:
            canonical_json = json.dumps([s.to_dict() for s in candidate.canonical_sequence])
            
            try:
                if is_postgres:
                    cur.execute("""
                        INSERT INTO kg.pattern_candidates
                            (candidate_key, name, description, canonical_sequence,
                             support_count, first_seen_run_id, last_seen_run_id, status)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (candidate_key) DO UPDATE SET
                            support_count = kg.pattern_candidates.support_count + 1,
                            last_seen_run_id = EXCLUDED.last_seen_run_id,
                            updated_at = NOW()
                        RETURNING id
                    """, (
                        candidate.candidate_key,
                        candidate.name,
                        candidate.description,
                        canonical_json,
                        candidate.support_count,
                        candidate.first_seen_run_id,
                        candidate.last_seen_run_id,
                        candidate.status,
                    ))
                else:
                    cur.execute("""
                        INSERT INTO kg_pattern_candidates
                            (candidate_key, name, description, canonical_sequence,
                             support_count, first_seen_run_id, last_seen_run_id, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT (candidate_key) DO UPDATE SET
                            support_count = kg_pattern_candidates.support_count + 1,
                            last_seen_run_id = excluded.last_seen_run_id,
                            updated_at = datetime('now')
                    """, (
                        candidate.candidate_key,
                        candidate.name,
                        candidate.description,
                        canonical_json,
                        candidate.support_count,
                        candidate.first_seen_run_id,
                        candidate.last_seen_run_id,
                        candidate.status,
                    ))
                
                saved += 1
                
            except Exception as e:
                logger.warning(f"Failed to save candidate {candidate.candidate_key}: {e}")
        
        conn.commit()
        logger.info(f"Saved {saved} pattern candidates")
        return saved
        
    except Exception as e:
        logger.error(f"Failed to save pattern candidates: {e}")
        return 0


# =============================================================================
# Stage 4: Pattern Promotion
# =============================================================================

def promote_pattern_candidate(candidate_key: str) -> Optional[str]:
    """
    Promote a candidate to a full pattern in kg.nodes.
    
    Creates:
    - New kg.nodes entry with node_type='pattern', origin='learned'
    - Updates candidate status to 'promoted'
    
    Args:
        candidate_key: The candidate_key from kg_pattern_candidates
        
    Returns:
        The new pattern key (e.g., "pattern.learned_abc123") or None on failure
    """
    settings = get_settings()
    if not settings.pattern_learning_enabled:
        return None
    
    if not settings.pattern_auto_promote:
        logger.debug("Pattern promotion disabled via PATTERN_AUTO_PROMOTE=false")
        return None
    
    try:
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        # Get candidate details
        if is_postgres:
            cur.execute("""
                SELECT id, name, description, canonical_sequence, support_count
                FROM kg.pattern_candidates
                WHERE candidate_key = %s AND status = 'pending'
            """, (candidate_key,))
        else:
            cur.execute("""
                SELECT id, name, description, canonical_sequence, support_count
                FROM kg_pattern_candidates
                WHERE candidate_key = ? AND status = 'pending'
            """, (candidate_key,))
        
        row = cur.fetchone()
        if not row:
            logger.warning(f"Candidate {candidate_key} not found or not pending")
            return None
        
        candidate_id, name, description, canonical_json, support_count = row
        
        # Parse canonical sequence
        canonical_data = json.loads(canonical_json) if isinstance(canonical_json, str) else canonical_json
        canonical = [CanonicalStep.from_dict(s) for s in canonical_data]
        
        # Generate pattern key
        seq_hash = hash_canonical_sequence(canonical)
        pattern_key = f"pattern.learned_{seq_hash}"
        
        # Build properties
        properties = {
            "seeded": False,
            "origin": "learned",
            "canonical_hash": seq_hash,
            "steps": canonical_data,
            "support_count": support_count,
            "promoted_from": candidate_key,
            "http_methods": list(set(s.method for s in canonical if s.method)),
        }
        properties_json = json.dumps(properties)
        
        # Insert into kg.nodes
        if is_postgres:
            cur.execute("""
                INSERT INTO kg.nodes
                    (node_type, key, name, description, properties, confidence_score, 
                     usage_count, origin, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (node_type, key) DO UPDATE SET
                    properties = EXCLUDED.properties,
                    updated_at = NOW()
                RETURNING id
            """, (
                KGNodeType.PATTERN.value,
                pattern_key,
                name,
                description,
                properties_json,
                0.7,  # Start learned patterns at 0.7 confidence
                support_count,
                "learned",
            ))
        else:
            cur.execute("""
                INSERT INTO kg_nodes
                    (node_type, key, name, description, properties, confidence_score,
                     usage_count, origin, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT (node_type, key) DO UPDATE SET
                    properties = excluded.properties,
                    updated_at = datetime('now')
            """, (
                KGNodeType.PATTERN.value,
                pattern_key,
                name,
                description,
                properties_json,
                0.7,
                support_count,
                "learned",
            ))
        
        # Update candidate status
        if is_postgres:
            cur.execute("""
                UPDATE kg.pattern_candidates
                SET status = 'promoted', promoted_pattern_key = %s, updated_at = NOW()
                WHERE candidate_key = %s
            """, (pattern_key, candidate_key))
        else:
            cur.execute("""
                UPDATE kg_pattern_candidates
                SET status = 'promoted', promoted_pattern_key = ?, updated_at = datetime('now')
                WHERE candidate_key = ?
            """, (pattern_key, candidate_key))
        
        conn.commit()
        logger.info(f"Promoted candidate {candidate_key} to pattern {pattern_key}")
        return pattern_key
        
    except Exception as e:
        logger.error(f"Failed to promote candidate {candidate_key}: {e}")
        return None


def check_and_promote_candidates() -> List[str]:
    """
    Check all pending candidates and promote those meeting thresholds.
    
    Promotion criteria:
    1. support_count >= pattern_promotion_threshold
    2. Average feedback score >= pattern_min_feedback_score (if feedback exists)
    
    Returns:
        List of promoted pattern keys
    """
    settings = get_settings()
    if not settings.pattern_learning_enabled:
        return []
    
    try:
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        # Get pending candidates meeting support threshold
        threshold = settings.pattern_promotion_threshold
        
        if is_postgres:
            cur.execute("""
                SELECT candidate_key, support_count
                FROM kg.pattern_candidates
                WHERE status = 'pending' AND support_count >= %s
            """, (threshold,))
        else:
            cur.execute("""
                SELECT candidate_key, support_count
                FROM kg_pattern_candidates
                WHERE status = 'pending' AND support_count >= ?
            """, (threshold,))
        
        candidates = cur.fetchall()
        
        promoted = []
        for candidate_key, support_count in candidates:
            # TODO: Add feedback score check when feedback loop is wired
            pattern_key = promote_pattern_candidate(candidate_key)
            if pattern_key:
                promoted.append(pattern_key)
        
        if promoted:
            logger.info(f"Auto-promoted {len(promoted)} patterns: {promoted}")
        
        return promoted
        
    except Exception as e:
        logger.error(f"Failed to check/promote candidates: {e}")
        return []


# =============================================================================
# Stage 5: Match Recording
# =============================================================================

def record_pattern_match(
    run_id: str,
    pattern_key: str,
    match_score: float,
    match_method: str,
    explanation: Optional[Dict] = None,
) -> Optional[int]:
    """
    Record a pattern match decision for explainability.
    
    Args:
        run_id: The run that matched
        pattern_key: The kg.nodes key of the matched pattern
        match_score: The match confidence (0-1)
        match_method: How the match was made ('exact', 'semantic', 'rule')
        explanation: Optional dict with match details
        
    Returns:
        The match record ID or None on failure
    """
    try:
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        explanation_json = json.dumps(explanation) if explanation else None
        
        if is_postgres:
            cur.execute("""
                INSERT INTO kg.pattern_matches
                    (run_id, pattern_key, match_score, match_method, explanation)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id
            """, (run_id, pattern_key, match_score, match_method, explanation_json))
            row = cur.fetchone()
            match_id = row[0] if row else None
        else:
            cur.execute("""
                INSERT INTO kg_pattern_matches
                    (run_id, pattern_key, match_score, match_method, explanation)
                VALUES (?, ?, ?, ?, ?)
            """, (run_id, pattern_key, match_score, match_method, explanation_json))
            cur.execute("SELECT last_insert_rowid()")
            match_id = cur.fetchone()[0]
        
        conn.commit()
        logger.debug(f"Recorded pattern match: run={run_id}, pattern={pattern_key}, score={match_score}")
        return match_id
        
    except Exception as e:
        logger.error(f"Failed to record pattern match: {e}")
        return None


# =============================================================================
# Convenience Functions
# =============================================================================

def get_pattern_candidates(status: Optional[str] = None) -> List[Dict]:
    """
    Get pattern candidates from the database.
    
    Args:
        status: Optional filter ('pending', 'promoted', 'rejected')
        
    Returns:
        List of candidate dicts
    """
    try:
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        if status:
            if is_postgres:
                cur.execute("""
                    SELECT candidate_key, name, description, canonical_sequence,
                           support_count, status, promoted_pattern_key, created_at
                    FROM kg.pattern_candidates
                    WHERE status = %s
                    ORDER BY support_count DESC
                """, (status,))
            else:
                cur.execute("""
                    SELECT candidate_key, name, description, canonical_sequence,
                           support_count, status, promoted_pattern_key, created_at
                    FROM kg_pattern_candidates
                    WHERE status = ?
                    ORDER BY support_count DESC
                """, (status,))
        else:
            if is_postgres:
                cur.execute("""
                    SELECT candidate_key, name, description, canonical_sequence,
                           support_count, status, promoted_pattern_key, created_at
                    FROM kg.pattern_candidates
                    ORDER BY support_count DESC
                """)
            else:
                cur.execute("""
                    SELECT candidate_key, name, description, canonical_sequence,
                           support_count, status, promoted_pattern_key, created_at
                    FROM kg_pattern_candidates
                    ORDER BY support_count DESC
                """)
        
        candidates = []
        for row in cur.fetchall():
            candidates.append({
                "candidate_key": row[0],
                "name": row[1],
                "description": row[2],
                "canonical_sequence": json.loads(row[3]) if isinstance(row[3], str) else row[3],
                "support_count": row[4],
                "status": row[5],
                "promoted_pattern_key": row[6],
                "created_at": row[7],
            })
        
        return candidates
        
    except Exception as e:
        logger.error(f"Failed to get pattern candidates: {e}")
        return []


def get_learned_patterns() -> List[Dict]:
    """
    Get all learned patterns (origin='learned') from kg.nodes.
    
    Respects PATTERN_MATCH_LEARNED flag - returns empty list if disabled.
    
    Returns:
        List of pattern dicts
    """
    settings = get_settings()
    if not settings.pattern_learning_enabled:
        return []
    
    if not settings.pattern_match_learned:
        logger.debug("Learned pattern matching disabled via PATTERN_MATCH_LEARNED=false")
        return []
    
    try:
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        if is_postgres:
            cur.execute("""
                SELECT key, name, description, properties, confidence_score, usage_count
                FROM kg.nodes
                WHERE node_type = 'pattern' AND origin = 'learned'
                ORDER BY confidence_score DESC, usage_count DESC
            """)
        else:
            cur.execute("""
                SELECT key, name, description, properties, confidence_score, usage_count
                FROM kg_nodes
                WHERE node_type = 'pattern' AND origin = 'learned'
                ORDER BY confidence_score DESC, usage_count DESC
            """)
        
        patterns = []
        for row in cur.fetchall():
            patterns.append({
                "key": row[0],
                "name": row[1],
                "description": row[2],
                "properties": json.loads(row[3]) if isinstance(row[3], str) else row[3],
                "confidence_score": row[4],
                "usage_count": row[5],
            })
        
        return patterns
        
    except Exception as e:
        logger.error(f"Failed to get learned patterns: {e}")
        return []


# =============================================================================
# Retention / Cleanup
# =============================================================================

def purge_old_run_events(days: int = 90) -> int:
    """
    Delete run events older than `days` days.
    
    This is a retention maintenance function to prevent unbounded table growth.
    Default retention period: 90 days.
    
    Args:
        days: Number of days to retain (events older than this are deleted)
        
    Returns:
        Number of rows deleted
        
    Example:
        >>> deleted = purge_old_run_events(days=30)
        >>> print(f"Cleaned up {deleted} old events")
    """
    if days < 1:
        raise ValueError("Retention period must be at least 1 day")
    
    try:
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        if is_postgres:
            # Postgres: uses 'created_at' column with INTERVAL
            cur.execute("""
                DELETE FROM kg.run_events
                WHERE created_at < NOW() - INTERVAL '%s days'
            """, (days,))
        else:
            # SQLite: uses 'timestamp' column (schema difference)
            cur.execute("""
                DELETE FROM kg_run_events
                WHERE timestamp < datetime('now', '-' || ? || ' days')
            """, (days,))
        
        deleted_count = cur.rowcount
        conn.commit()
        
        if deleted_count > 0:
            logger.info(f"Purged {deleted_count} run events older than {days} days")
        
        return deleted_count
        
    except Exception as e:
        logger.error(f"Failed to purge old run events: {e}")
        return 0


def get_event_stats() -> Dict[str, Any]:
    """
    Get statistics about run events for monitoring.
    
    Returns:
        Dict with event counts, date ranges, storage info
    """
    try:
        conn = db.get_connection()
        is_postgres = db.get_engine_type() == "postgres"
        cur = conn.cursor()
        
        if is_postgres:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_events,
                    COUNT(DISTINCT run_id) as unique_runs,
                    MIN(created_at) as oldest_event,
                    MAX(created_at) as newest_event
                FROM kg.run_events
            """)
        else:
            cur.execute("""
                SELECT 
                    COUNT(*) as total_events,
                    COUNT(DISTINCT run_id) as unique_runs,
                    MIN(created_at) as oldest_event,
                    MAX(created_at) as newest_event
                FROM kg_run_events
            """)
        
        row = cur.fetchone()
        return {
            "total_events": row[0],
            "unique_runs": row[1],
            "oldest_event": row[2],
            "newest_event": row[3],
        }
        
    except Exception as e:
        logger.error(f"Failed to get event stats: {e}")
        return {"error": str(e)}


# =============================================================================
# Feedback API Wrappers (Stable Public API)
# =============================================================================

def update_pattern_confidence_from_feedback(
    pattern_key: str,
    min_feedback_count: int = 3,
) -> Optional[float]:
    """
    Update a pattern's confidence score based on accumulated feedback.
    
    This is a stable API wrapper that delegates to the feedback module.
    Uses EMA (Exponential Moving Average) to blend new feedback with
    existing confidence.
    
    NOTE: This function is documented in PATTERN_LEARNING_DESIGN.md as part
    of the pattern learning public API, but implementation lives in the
    feedback module for separation of concerns.
    
    Args:
        pattern_key: The kg.nodes key for the pattern
        min_feedback_count: Minimum feedback records required for update
        
    Returns:
        New confidence score, or None if no update was made
        
    See Also:
        integration_coworker.feedback.langsmith_sync.update_pattern_confidence_from_feedback
    """
    try:
        from integration_coworker.feedback.langsmith_sync import (
            update_pattern_confidence_from_feedback as _impl,
        )
        return _impl(pattern_key, min_feedback_count)
    except ImportError as e:
        logger.warning(f"Feedback module not available: {e}")
        return None


def apply_confidence_decay(
    decay_factor: float = 0.95,
    min_confidence: float = 0.1,
) -> int:
    """
    Apply confidence decay to unused patterns.
    
    This is a stable API wrapper that delegates to the feedback module.
    Patterns unused for 30+ days have their confidence reduced.
    
    Args:
        decay_factor: Multiply confidence by this factor (0.95 = 5% decay)
        min_confidence: Don't decay below this threshold
        
    Returns:
        Number of patterns decayed
        
    See Also:
        integration_coworker.feedback.langsmith_sync.apply_confidence_decay
    """
    try:
        from integration_coworker.feedback.langsmith_sync import (
            apply_confidence_decay as _impl,
        )
        return _impl(decay_factor, min_confidence)
    except ImportError as e:
        logger.warning(f"Feedback module not available: {e}")
        return 0

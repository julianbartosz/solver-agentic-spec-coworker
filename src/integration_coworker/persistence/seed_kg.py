"""
Knowledge Graph Auto-Seeding (KG-002/004)

Pre-populates the integration_gold database with curated workflow templates
and common API patterns. This gives the LLM prior knowledge of:
- Standard OAuth2 flows
- Pagination patterns (cursor, offset)
- Common error handling strategies
- Webhook processing patterns

Called during `init-db` command to ensure the KG starts with useful templates.

Per V2_IMPLEMENTATION_PLAN_SUPPLEMENT.md Section 3.
"""
import logging
from typing import List, Dict, Any, Optional, Tuple

from integration_coworker.persistence.db import get_connection, get_engine_type

logger = logging.getLogger(__name__)


# =============================================================================
# Seed Data Definitions
# =============================================================================

def get_seed_templates() -> List[Dict[str, Any]]:
    """
    Get the curated workflow templates to seed the Knowledge Graph.
    
    Each template defines:
    - code: Unique identifier
    - name: Human-readable name
    - description: Detailed description for LLM context
    - nodes: Workflow nodes (steps)
    - edges: Connections between nodes
    
    Returns:
        List of template dictionaries
    """
    return [
        {
            "code": "oauth2_authorization_code",
            "name": "OAuth2 Authorization Code Flow",
            "description": (
                "Standard OAuth2 authorization code flow for user delegation. "
                "Used when an application needs to act on behalf of a user. "
                "Includes authorization redirect, code exchange, token storage, and refresh handling."
            ),
            "nodes": [
                {"key": "start", "type": "start", "label": "Start OAuth Flow", "position": 0},
                {"key": "build_auth_url", "type": "transform", "label": "Build Authorization URL", "position": 1},
                {"key": "redirect_user", "type": "output", "label": "Redirect to Provider", "position": 2},
                {"key": "receive_callback", "type": "input", "label": "Receive Callback", "position": 3},
                {"key": "exchange_code", "type": "api_call", "label": "Exchange Code for Token", "position": 4},
                {"key": "store_tokens", "type": "transform", "label": "Store Access/Refresh Tokens", "position": 5},
                {"key": "end", "type": "end", "label": "Auth Complete", "position": 6},
            ],
            "edges": [
                {"from": "start", "to": "build_auth_url"},
                {"from": "build_auth_url", "to": "redirect_user"},
                {"from": "redirect_user", "to": "receive_callback"},
                {"from": "receive_callback", "to": "exchange_code"},
                {"from": "exchange_code", "to": "store_tokens"},
                {"from": "store_tokens", "to": "end"},
            ],
        },
        {
            "code": "oauth2_client_credentials",
            "name": "OAuth2 Client Credentials Flow",
            "description": (
                "OAuth2 client credentials flow for server-to-server authentication. "
                "Used when the application acts on its own behalf, not a user. "
                "Simpler than authorization code flow - just exchanges client ID/secret for token."
            ),
            "nodes": [
                {"key": "start", "type": "start", "label": "Start Auth", "position": 0},
                {"key": "request_token", "type": "api_call", "label": "Request Access Token", "position": 1},
                {"key": "store_token", "type": "transform", "label": "Store Token with Expiry", "position": 2},
                {"key": "end", "type": "end", "label": "Auth Complete", "position": 3},
            ],
            "edges": [
                {"from": "start", "to": "request_token"},
                {"from": "request_token", "to": "store_token"},
                {"from": "store_token", "to": "end"},
            ],
        },
        {
            "code": "cursor_pagination",
            "name": "Cursor-Based Pagination",
            "description": (
                "Iterate through paginated API responses using cursor tokens. "
                "Cursor pagination is more efficient than offset for large datasets. "
                "Each page response includes a 'next_cursor' to fetch the next page."
            ),
            "nodes": [
                {"key": "start", "type": "start", "label": "Start Pagination", "position": 0},
                {"key": "fetch_page", "type": "api_call", "label": "Fetch Page", "position": 1},
                {"key": "process_items", "type": "transform", "label": "Process Items", "position": 2},
                {"key": "check_next", "type": "decision", "label": "Has Next Page?", "position": 3},
                {"key": "update_cursor", "type": "transform", "label": "Update Cursor", "position": 4},
                {"key": "end", "type": "end", "label": "All Pages Processed", "position": 5},
            ],
            "edges": [
                {"from": "start", "to": "fetch_page"},
                {"from": "fetch_page", "to": "process_items"},
                {"from": "process_items", "to": "check_next"},
                {"from": "check_next", "to": "update_cursor", "condition": "has_next_cursor"},
                {"from": "check_next", "to": "end", "condition": "no_more_pages"},
                {"from": "update_cursor", "to": "fetch_page"},
            ],
        },
        {
            "code": "offset_pagination",
            "name": "Offset-Based Pagination",
            "description": (
                "Iterate through paginated API responses using offset and limit. "
                "Common in REST APIs. Each request specifies offset (skip) and limit (take). "
                "Less efficient for large datasets due to counting overhead."
            ),
            "nodes": [
                {"key": "start", "type": "start", "label": "Start Pagination", "position": 0},
                {"key": "fetch_page", "type": "api_call", "label": "Fetch Page", "position": 1},
                {"key": "process_items", "type": "transform", "label": "Process Items", "position": 2},
                {"key": "check_more", "type": "decision", "label": "More Items?", "position": 3},
                {"key": "increment_offset", "type": "transform", "label": "Increment Offset", "position": 4},
                {"key": "end", "type": "end", "label": "All Pages Processed", "position": 5},
            ],
            "edges": [
                {"from": "start", "to": "fetch_page"},
                {"from": "fetch_page", "to": "process_items"},
                {"from": "process_items", "to": "check_more"},
                {"from": "check_more", "to": "increment_offset", "condition": "items_returned == limit"},
                {"from": "check_more", "to": "end", "condition": "items_returned < limit"},
                {"from": "increment_offset", "to": "fetch_page"},
            ],
        },
        {
            "code": "webhook_processor",
            "name": "Webhook Event Processor",
            "description": (
                "Process incoming webhook events from external services. "
                "Includes signature verification, event parsing, idempotency handling, "
                "and routing to appropriate handlers based on event type."
            ),
            "nodes": [
                {"key": "receive", "type": "input", "label": "Receive Webhook", "position": 0},
                {"key": "verify_signature", "type": "validation", "label": "Verify Signature", "position": 1},
                {"key": "parse_event", "type": "transform", "label": "Parse Event Payload", "position": 2},
                {"key": "check_idempotency", "type": "decision", "label": "Already Processed?", "position": 3},
                {"key": "route_event", "type": "decision", "label": "Route by Event Type", "position": 4},
                {"key": "process_event", "type": "transform", "label": "Process Event", "position": 5},
                {"key": "mark_processed", "type": "transform", "label": "Mark as Processed", "position": 6},
                {"key": "ack", "type": "output", "label": "Acknowledge (200 OK)", "position": 7},
            ],
            "edges": [
                {"from": "receive", "to": "verify_signature"},
                {"from": "verify_signature", "to": "parse_event"},
                {"from": "parse_event", "to": "check_idempotency"},
                {"from": "check_idempotency", "to": "ack", "condition": "already_processed"},
                {"from": "check_idempotency", "to": "route_event", "condition": "new_event"},
                {"from": "route_event", "to": "process_event"},
                {"from": "process_event", "to": "mark_processed"},
                {"from": "mark_processed", "to": "ack"},
            ],
        },
        {
            "code": "retry_with_backoff",
            "name": "Retry with Exponential Backoff",
            "description": (
                "Pattern for handling transient failures with exponential backoff. "
                "Retries failed API calls with increasing delays: 1s, 2s, 4s, 8s... "
                "Includes jitter to prevent thundering herd. Gives up after max retries."
            ),
            "nodes": [
                {"key": "start", "type": "start", "label": "Start Request", "position": 0},
                {"key": "attempt", "type": "api_call", "label": "Attempt API Call", "position": 1},
                {"key": "check_result", "type": "decision", "label": "Success?", "position": 2},
                {"key": "check_retries", "type": "decision", "label": "Retries Left?", "position": 3},
                {"key": "calculate_delay", "type": "transform", "label": "Calculate Backoff", "position": 4},
                {"key": "wait", "type": "transform", "label": "Wait", "position": 5},
                {"key": "success", "type": "end", "label": "Success", "position": 6},
                {"key": "failure", "type": "end", "label": "Max Retries Exceeded", "position": 7},
            ],
            "edges": [
                {"from": "start", "to": "attempt"},
                {"from": "attempt", "to": "check_result"},
                {"from": "check_result", "to": "success", "condition": "success"},
                {"from": "check_result", "to": "check_retries", "condition": "retryable_error"},
                {"from": "check_retries", "to": "calculate_delay", "condition": "retries_remaining"},
                {"from": "check_retries", "to": "failure", "condition": "no_retries_left"},
                {"from": "calculate_delay", "to": "wait"},
                {"from": "wait", "to": "attempt"},
            ],
        },
    ]


# =============================================================================
# Database Seeding Logic
# =============================================================================

def _count_existing_templates(conn) -> int:
    """Count existing workflow templates in the database."""
    engine = get_engine_type()
    cur = conn.cursor()
    
    try:
        cur.execute("SELECT COUNT(*) FROM workflow_templates")
        result = cur.fetchone()
        return result[0] if result else 0
    except Exception:
        # Table might not exist yet
        return 0


def _insert_template_sqlite(conn, template: Dict[str, Any]) -> Optional[int]:
    """Insert a workflow template into SQLite."""
    import json
    
    cur = conn.cursor()
    
    # Insert template
    cur.execute("""
        INSERT INTO workflow_templates (code, name, description)
        VALUES (?, ?, ?)
    """, (template["code"], template["name"], template["description"]))
    
    template_id = cur.lastrowid
    
    # Insert nodes
    for node in template.get("nodes", []):
        cur.execute("""
            INSERT INTO kg_workflow_nodes (template_id, node_key, node_type, label, position, config)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            template_id,
            node["key"],
            node["type"],
            node.get("label"),
            node.get("position", 0),
            json.dumps(node.get("config", {})),
        ))
    
    # Insert edges
    for edge in template.get("edges", []):
        cur.execute("""
            INSERT INTO kg_workflow_edges (template_id, from_node_key, to_node_key, condition)
            VALUES (?, ?, ?, ?)
        """, (
            template_id,
            edge["from"],
            edge["to"],
            edge.get("condition"),
        ))
    
    return template_id


def _insert_template_postgres(conn, template: Dict[str, Any]) -> Optional[int]:
    """Insert a workflow template into Postgres."""
    import json
    
    cur = conn.cursor()
    
    # Insert template
    cur.execute("""
        INSERT INTO workflow_templates (code, name, description)
        VALUES (%s, %s, %s)
        RETURNING id
    """, (template["code"], template["name"], template["description"]))
    
    result = cur.fetchone()
    template_id = result[0] if result else None
    
    if not template_id:
        return None
    
    # Insert nodes
    for node in template.get("nodes", []):
        cur.execute("""
            INSERT INTO kg_workflow_nodes (template_id, node_key, node_type, label, position, config)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            template_id,
            node["key"],
            node["type"],
            node.get("label"),
            node.get("position", 0),
            json.dumps(node.get("config", {})),
        ))
    
    # Insert edges
    for edge in template.get("edges", []):
        cur.execute("""
            INSERT INTO kg_workflow_edges (template_id, from_node_key, to_node_key, condition)
            VALUES (%s, %s, %s, %s)
        """, (
            template_id,
            edge["from"],
            edge["to"],
            edge.get("condition"),
        ))
    
    return template_id


def seed_knowledge_graph(force: bool = False) -> Tuple[int, int]:
    """
    Seed the Knowledge Graph with curated workflow templates.
    
    Args:
        force: If True, skip the check for existing templates and add anyway.
               Note: This may create duplicates if templates already exist.
    
    Returns:
        Tuple of (templates_added, templates_skipped)
    
    Raises:
        Exception: If database operations fail
    """
    conn = get_connection()
    engine = get_engine_type()
    
    try:
        # Check if already seeded
        existing_count = _count_existing_templates(conn)
        if existing_count > 0 and not force:
            logger.info(f"Knowledge Graph already has {existing_count} templates. Skipping seed.")
            return (0, len(get_seed_templates()))
        
        templates = get_seed_templates()
        added = 0
        skipped = 0
        
        insert_fn = _insert_template_postgres if engine == "postgres" else _insert_template_sqlite
        
        for template in templates:
            try:
                template_id = insert_fn(conn, template)
                if template_id:
                    logger.debug(f"Seeded template: {template['code']} (id={template_id})")
                    added += 1
                else:
                    skipped += 1
            except Exception as e:
                logger.warning(f"Failed to seed template {template['code']}: {e}")
                skipped += 1
        
        conn.commit()
        logger.info(f"Knowledge Graph seeding complete: {added} added, {skipped} skipped")
        return (added, skipped)
    
    finally:
        conn.close()


def list_seeded_templates() -> List[Dict[str, Any]]:
    """
    List all workflow templates currently in the database.
    
    Returns:
        List of template info dictionaries with id, code, name
    """
    conn = get_connection()
    engine = get_engine_type()
    
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, code, name, description FROM workflow_templates ORDER BY id")
        rows = cur.fetchall()
        
        if engine == "sqlite":
            return [{"id": r[0], "code": r[1], "name": r[2], "description": r[3]} for r in rows]
        else:
            return [{"id": r[0], "code": r[1], "name": r[2], "description": r[3]} for r in rows]
    
    except Exception as e:
        logger.warning(f"Could not list templates: {e}")
        return []
    
    finally:
        conn.close()

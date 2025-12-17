"""
Templates page for Integration Co-Worker.

Shows:
- Reusable workflow templates from Knowledge Graph
- Template patterns and their usage counts
- Search by pattern type, entity, or provider
- Template details with node graph
"""
import streamlit as st
from typing import Dict, Any, List, Optional

st.set_page_config(
    page_title="Templates - Integration Co-Worker",
    page_icon="📋",
    layout="wide",
)


def get_workflow_templates() -> List[Dict[str, Any]]:
    """Fetch workflow templates from Knowledge Graph."""
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        engine = get_engine_type()
        
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            id,
                            provider_code,
                            task_slug,
                            pattern_key,
                            description,
                            confidence_score,
                            usage_count,
                            created_at,
                            updated_at
                        FROM integration_gold.workflow_templates
                        ORDER BY usage_count DESC, confidence_score DESC
                        LIMIT 50
                    """)
                    
                    rows = cur.fetchall()
                    return [
                        {
                            "id": row[0],
                            "provider_code": row[1],
                            "task_slug": row[2],
                            "pattern_key": row[3],
                            "description": row[4],
                            "confidence_score": row[5],
                            "usage_count": row[6],
                            "created_at": row[7],
                            "updated_at": row[8],
                        }
                        for row in rows
                    ]
        else:
            # SQLite fallback
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    id,
                    provider_code,
                    task_slug,
                    pattern_key,
                    description,
                    confidence_score,
                    usage_count,
                    created_at,
                    updated_at
                FROM workflow_templates
                ORDER BY usage_count DESC, confidence_score DESC
                LIMIT 50
            """)
            
            rows = cur.fetchall()
            conn.close()
            
            return [
                {
                    "id": row[0],
                    "provider_code": row[1],
                    "task_slug": row[2],
                    "pattern_key": row[3],
                    "description": row[4],
                    "confidence_score": row[5],
                    "usage_count": row[6],
                    "created_at": row[7],
                    "updated_at": row[8],
                }
                for row in rows
            ]
    except Exception as e:
        st.warning(f"Could not fetch templates: {e}")
        return []


def get_template_nodes(template_id: int) -> List[Dict[str, Any]]:
    """Fetch workflow nodes for a template."""
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        engine = get_engine_type()
        
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            id,
                            node_name,
                            node_type,
                            description,
                            execution_order
                        FROM integration_gold.workflow_nodes
                        WHERE template_id = %s
                        ORDER BY execution_order
                    """, (template_id,))
                    
                    rows = cur.fetchall()
                    return [
                        {
                            "id": row[0],
                            "name": row[1],
                            "type": row[2],
                            "description": row[3],
                            "order": row[4],
                        }
                        for row in rows
                    ]
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    id,
                    node_name,
                    node_type,
                    description,
                    execution_order
                FROM workflow_nodes
                WHERE template_id = ?
                ORDER BY execution_order
            """, (template_id,))
            
            rows = cur.fetchall()
            conn.close()
            
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "type": row[2],
                    "description": row[3],
                    "order": row[4],
                }
                for row in rows
            ]
    except Exception as e:
        return []


def get_pattern_stats() -> Dict[str, int]:
    """Get count of templates by pattern type."""
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        engine = get_engine_type()
        
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            pattern_key,
                            COUNT(*) as count
                        FROM integration_gold.workflow_templates
                        GROUP BY pattern_key
                        ORDER BY count DESC
                    """)
                    
                    rows = cur.fetchall()
                    return {row[0]: row[1] for row in rows}
        else:
            from integration_coworker.persistence.db import get_connection
            
            conn = get_connection()
            cur = conn.cursor()
            
            cur.execute("""
                SELECT 
                    pattern_key,
                    COUNT(*) as count
                FROM workflow_templates
                GROUP BY pattern_key
                ORDER BY count DESC
            """)
            
            rows = cur.fetchall()
            conn.close()
            
            return {row[0]: row[1] for row in rows}
    except Exception as e:
        return {}


def main():
    st.title("📋 Templates")
    st.caption("Reusable workflow templates from the Knowledge Graph")
    
    # Fetch data
    templates = get_workflow_templates()
    pattern_stats = get_pattern_stats()
    
    if not templates:
        st.info("No workflow templates have been learned yet.")
        
        st.markdown("""
        ### About Templates
        
        Templates are learned from successful integration runs. They capture:
        
        - **Workflow patterns** (CRUD, event handling, data sync, etc.)
        - **Node configurations** for similar tasks
        - **Endpoint bindings** that work well together
        - **Policy configurations** (auth, retry, rate limits)
        
        The more integrations you run, the smarter the system becomes!
        
        ### How Templates are Created
        
        After each successful run, the `persist_kg_learning` node:
        1. Analyzes the workflow pattern used
        2. Extracts reusable configurations
        3. Stores the template with embeddings for semantic search
        
        Future runs can then reuse these templates for similar tasks.
        """)
        return
    
    # Pattern distribution
    st.subheader("Pattern Distribution")
    
    if pattern_stats:
        col1, col2, col3, col4 = st.columns(4)
        
        patterns = list(pattern_stats.items())
        for i, (pattern, count) in enumerate(patterns[:4]):
            with [col1, col2, col3, col4][i]:
                st.metric(pattern or "unknown", count)
    
    st.divider()
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        search = st.text_input("🔍 Search", placeholder="Search templates...")
    
    with col2:
        providers = ["All"] + list(set(t.get("provider_code") for t in templates if t.get("provider_code")))
        provider_filter = st.selectbox("Provider", options=providers)
    
    with col3:
        patterns = ["All"] + list(pattern_stats.keys())
        pattern_filter = st.selectbox("Pattern", options=patterns)
    
    # Filter templates
    filtered = templates
    
    if search:
        search_lower = search.lower()
        filtered = [
            t for t in filtered
            if search_lower in (t.get("task_slug", "").lower()) or
               search_lower in (t.get("description", "").lower() if t.get("description") else "") or
               search_lower in (t.get("provider_code", "").lower() if t.get("provider_code") else "")
        ]
    
    if provider_filter != "All":
        filtered = [t for t in filtered if t.get("provider_code") == provider_filter]
    
    if pattern_filter != "All":
        filtered = [t for t in filtered if t.get("pattern_key") == pattern_filter]
    
    st.markdown(f"**{len(filtered)}** templates found")
    
    st.divider()
    
    # Template list
    for template in filtered:
        confidence = template.get("confidence_score", 0) or 0
        usage = template.get("usage_count", 0) or 0
        
        # Color code by confidence
        if confidence >= 0.8:
            badge = "🟢"
        elif confidence >= 0.5:
            badge = "🟡"
        else:
            badge = "🔴"
        
        with st.expander(
            f"{badge} {template.get('task_slug', 'Untitled')} | "
            f"{template.get('pattern_key', 'unknown')} | "
            f"Used {usage}x",
            expanded=False,
        ):
            # Metadata row
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.markdown(f"**Provider:** {template.get('provider_code', 'N/A')}")
            
            with col2:
                st.markdown(f"**Pattern:** {template.get('pattern_key', 'N/A')}")
            
            with col3:
                st.markdown(f"**Confidence:** {confidence:.1%}")
            
            with col4:
                st.markdown(f"**Usage:** {usage}")
            
            # Description
            if template.get("description"):
                st.markdown(f"**Description:** {template['description']}")
            
            st.divider()
            
            # Workflow nodes
            nodes = get_template_nodes(template['id'])
            
            if nodes:
                st.markdown("**Workflow Nodes**")
                
                # Simple visual representation
                node_display = []
                for node in nodes:
                    node_type = node.get("type", "action")
                    if node_type == "api_call":
                        icon = "🌐"
                    elif node_type == "transform":
                        icon = "🔄"
                    elif node_type == "condition":
                        icon = "❓"
                    else:
                        icon = "⚙️"
                    
                    node_display.append(f"{icon} {node['name']}")
                
                st.text(" → ".join(node_display))
            else:
                st.caption("No nodes recorded")
            
            # Timestamps
            st.caption(
                f"Created: {template.get('created_at', 'N/A')} | "
                f"Updated: {template.get('updated_at', 'N/A')}"
            )


if __name__ == "__main__":
    main()

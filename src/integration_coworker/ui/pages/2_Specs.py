"""
Spec Library page for Integration Co-Worker.

Shows:
- List of ingested specs from database
- Spec details (endpoints, schemas, entities)
- Search/filter capabilities
- Spec info and metadata
"""
import streamlit as st
from typing import Dict, Any, List, Optional

st.set_page_config(
    page_title="Spec Library - Integration Co-Worker",
    page_icon="📚",
    layout="wide",
)


def get_spec_documents() -> List[Dict[str, Any]]:
    """Fetch all spec documents from database."""
    try:
        from integration_coworker.persistence.db import get_engine_type
        
        engine = get_engine_type()
        
        if engine == "postgres":
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT 
                            d.id,
                            d.uri,
                            d.doc_type,
                            d.spec_version,
                            d.doc_hash,
                            s.source_system_id,
                            s.system_name,
                            d.created_at
                        FROM spec_silver.spec_documents d
                        LEFT JOIN spec_silver.source_systems s 
                            ON d.source_system_id = s.id
                        ORDER BY d.created_at DESC
                        LIMIT 50
                    """)
                    
                    rows = cur.fetchall()
                    return [
                        {
                            "id": row[0],
                            "uri": row[1],
                            "doc_type": row[2],
                            "spec_version": row[3],
                            "doc_hash": row[4][:16] + "..." if row[4] else None,
                            "provider_code": row[5],
                            "system_name": row[6],
                            "created_at": row[7],
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
                    uri,
                    doc_type,
                    spec_version,
                    doc_hash,
                    source_system_id,
                    created_at
                FROM spec_documents
                ORDER BY created_at DESC
                LIMIT 50
            """)
            
            rows = cur.fetchall()
            conn.close()
            
            return [
                {
                    "id": row[0],
                    "uri": row[1],
                    "doc_type": row[2],
                    "spec_version": row[3],
                    "doc_hash": row[4][:16] + "..." if row[4] else None,
                    "provider_code": row[5],
                    "system_name": None,
                    "created_at": row[6],
                }
                for row in rows
            ]
    except Exception as e:
        st.warning(f"Could not fetch specs: {e}")
        return []


def get_spec_endpoints(spec_doc_id: int) -> List[Dict[str, Any]]:
    """Fetch endpoints for a specific spec document."""
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
                            path,
                            method,
                            operation_id,
                            summary
                        FROM spec_silver.endpoints
                        WHERE spec_document_id = %s
                        ORDER BY path, method
                    """, (spec_doc_id,))
                    
                    rows = cur.fetchall()
                    return [
                        {
                            "id": row[0],
                            "path": row[1],
                            "method": row[2].upper() if row[2] else "GET",
                            "operation_id": row[3],
                            "summary": row[4],
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
                    path,
                    method,
                    operation_id,
                    summary
                FROM endpoints
                WHERE spec_document_id = ?
                ORDER BY path, method
            """, (spec_doc_id,))
            
            rows = cur.fetchall()
            conn.close()
            
            return [
                {
                    "id": row[0],
                    "path": row[1],
                    "method": row[2].upper() if row[2] else "GET",
                    "operation_id": row[3],
                    "summary": row[4],
                }
                for row in rows
            ]
    except Exception as e:
        st.warning(f"Could not fetch endpoints: {e}")
        return []


def get_spec_schemas(spec_doc_id: int) -> List[Dict[str, Any]]:
    """Fetch schemas for a specific spec document."""
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
                            name,
                            schema_type,
                            description
                        FROM spec_silver.schemas
                        WHERE spec_document_id = %s
                        ORDER BY name
                        LIMIT 100
                    """, (spec_doc_id,))
                    
                    rows = cur.fetchall()
                    return [
                        {
                            "id": row[0],
                            "name": row[1],
                            "type": row[2],
                            "description": row[3][:100] if row[3] else None,
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
                    name,
                    schema_type,
                    description
                FROM schemas
                WHERE spec_document_id = ?
                ORDER BY name
                LIMIT 100
            """, (spec_doc_id,))
            
            rows = cur.fetchall()
            conn.close()
            
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "type": row[2],
                    "description": row[3][:100] if row[3] else None,
                }
                for row in rows
            ]
    except Exception as e:
        st.warning(f"Could not fetch schemas: {e}")
        return []


def main():
    st.title("📚 Spec Library")
    st.caption("Browse and search ingested API specifications")
    
    # Fetch specs
    specs = get_spec_documents()
    
    if not specs:
        st.info("No specs have been ingested yet. Run an integration to add specs.")
        
        st.markdown("""
        ### Quick Start
        
        To add specs to the library, run:
        
        ```bash
        integration-coworker run --spec-ref /path/to/openapi.yaml --task "Your task"
        ```
        
        Or use the main app to run integrations interactively.
        """)
        return
    
    # Search/filter
    col1, col2 = st.columns([3, 1])
    
    with col1:
        search = st.text_input("🔍 Search specs", placeholder="Filter by URI, provider, or type...")
    
    with col2:
        doc_type_filter = st.selectbox(
            "Type",
            options=["All"] + list(set(s.get("doc_type", "unknown") for s in specs if s.get("doc_type"))),
        )
    
    # Filter specs
    filtered_specs = specs
    if search:
        search_lower = search.lower()
        filtered_specs = [
            s for s in filtered_specs
            if search_lower in (s.get("uri", "").lower()) or
               search_lower in (s.get("provider_code", "").lower() if s.get("provider_code") else "") or
               search_lower in (s.get("system_name", "").lower() if s.get("system_name") else "")
        ]
    
    if doc_type_filter != "All":
        filtered_specs = [s for s in filtered_specs if s.get("doc_type") == doc_type_filter]
    
    # Stats
    st.markdown(f"**{len(filtered_specs)}** specs found")
    
    st.divider()
    
    # Spec list
    for spec in filtered_specs:
        with st.expander(
            f"📄 {spec.get('provider_code') or spec.get('system_name') or 'Unknown'} - "
            f"{spec.get('doc_type', 'openapi')} v{spec.get('spec_version', '?')}",
            expanded=False,
        ):
            # Metadata
            col1, col2, col3 = st.columns(3)
            
            with col1:
                st.markdown(f"**ID:** {spec['id']}")
                st.markdown(f"**Type:** {spec.get('doc_type', 'unknown')}")
            
            with col2:
                st.markdown(f"**Version:** {spec.get('spec_version', 'unknown')}")
                st.markdown(f"**Hash:** `{spec.get('doc_hash', 'N/A')}`")
            
            with col3:
                st.markdown(f"**Created:** {spec.get('created_at', 'N/A')}")
            
            st.markdown(f"**URI:** `{spec.get('uri', 'N/A')}`")
            
            st.divider()
            
            # Endpoints
            endpoints = get_spec_endpoints(spec['id'])
            st.markdown(f"**Endpoints ({len(endpoints)})**")
            
            if endpoints:
                # Group by method
                method_colors = {
                    "GET": "🟢",
                    "POST": "🔵",
                    "PUT": "🟡",
                    "PATCH": "🟠",
                    "DELETE": "🔴",
                }
                
                for ep in endpoints[:20]:  # Limit display
                    method_icon = method_colors.get(ep['method'], "⚪")
                    st.text(f"{method_icon} {ep['method']} {ep['path']}")
                
                if len(endpoints) > 20:
                    st.caption(f"...and {len(endpoints) - 20} more endpoints")
            else:
                st.caption("No endpoints extracted")
            
            # Schemas
            schemas = get_spec_schemas(spec['id'])
            st.markdown(f"**Schemas ({len(schemas)})**")
            
            if schemas:
                schema_names = [s['name'] for s in schemas[:15]]
                st.text(", ".join(schema_names))
                if len(schemas) > 15:
                    st.caption(f"...and {len(schemas) - 15} more schemas")
            else:
                st.caption("No schemas extracted")


if __name__ == "__main__":
    main()

"""
Dashboard page for Integration Co-Worker.

Shows:
- Recent run history with status
- Quick stats (success rate, avg duration)
- Quick actions (resume, retry)
- System health indicators
"""
import streamlit as st
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import json

st.set_page_config(
    page_title="Dashboard - Integration Co-Worker",
    page_icon="📊",
    layout="wide",
)


def get_run_history() -> List[Dict[str, Any]]:
    """
    Fetch recent runs from database.
    
    Returns list of run info dictionaries.
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
                            run_id,
                            provider_code,
                            task_slug,
                            status,
                            started_at,
                            ended_at,
                            error_message
                        FROM integration_gold.run_status
                        ORDER BY started_at DESC
                        LIMIT 20
                    """)
                    
                    rows = cur.fetchall()
                    return [
                        {
                            "run_id": row[0],
                            "provider_code": row[1],
                            "task_slug": row[2],
                            "status": row[3],
                            "started_at": row[4],
                            "ended_at": row[5],
                            "error_message": row[6],
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
                    run_id,
                    provider_code,
                    task_slug,
                    status,
                    started_at,
                    ended_at,
                    error_message
                FROM run_status
                ORDER BY started_at DESC
                LIMIT 20
            """)
            
            rows = cur.fetchall()
            conn.close()
            
            return [
                {
                    "run_id": row[0],
                    "provider_code": row[1],
                    "task_slug": row[2],
                    "status": row[3],
                    "started_at": row[4],
                    "ended_at": row[5],
                    "error_message": row[6],
                }
                for row in rows
            ]
    except Exception as e:
        st.warning(f"Could not fetch run history: {e}")
        return []


def get_quick_stats(runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Calculate quick statistics from run history."""
    if not runs:
        return {
            "total_runs": 0,
            "success_count": 0,
            "failure_count": 0,
            "running_count": 0,
            "success_rate": 0.0,
        }
    
    total = len(runs)
    success = sum(1 for r in runs if r["status"] == "completed")
    failed = sum(1 for r in runs if r["status"] == "failed")
    running = sum(1 for r in runs if r["status"] == "running")
    
    return {
        "total_runs": total,
        "success_count": success,
        "failure_count": failed,
        "running_count": running,
        "success_rate": (success / total * 100) if total > 0 else 0.0,
    }


def main():
    st.title("📊 Dashboard")
    st.caption("Overview of integration runs and system status")
    
    # Fetch data
    runs = get_run_history()
    stats = get_quick_stats(runs)
    
    # Quick stats row
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Total Runs", stats["total_runs"])
    
    with col2:
        st.metric("Successful", stats["success_count"], 
                  delta=f"{stats['success_rate']:.1f}%" if stats["total_runs"] > 0 else None)
    
    with col3:
        st.metric("Failed", stats["failure_count"],
                  delta_color="inverse")
    
    with col4:
        st.metric("Running", stats["running_count"])
    
    st.divider()
    
    # Recent runs table
    st.subheader("Recent Runs")
    
    if not runs:
        st.info("No runs recorded yet. Start your first integration from the main page!")
    else:
        for run in runs[:10]:
            with st.expander(
                f"{'✅' if run['status'] == 'completed' else '❌' if run['status'] == 'failed' else '⏳'} "
                f"{run['provider_code'] or 'unknown'} - {run['task_slug'] or 'no task'}",
                expanded=False,
            ):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.text(f"Run ID: {run['run_id']}")
                    st.text(f"Status: {run['status']}")
                    if run['started_at']:
                        st.text(f"Started: {run['started_at']}")
                
                with col2:
                    if run['ended_at']:
                        st.text(f"Ended: {run['ended_at']}")
                    if run['error_message']:
                        st.error(f"Error: {run['error_message'][:200]}")
                
                # Quick actions
                action_col1, action_col2 = st.columns(2)
                with action_col1:
                    if run['status'] == 'running':
                        if st.button("🔄 Resume", key=f"resume_{run['run_id']}"):
                            st.info(f"Run: integration-coworker resume {run['run_id']}")
                with action_col2:
                    if run['status'] == 'failed':
                        if st.button("🔁 View Error", key=f"error_{run['run_id']}"):
                            if run['error_message']:
                                st.code(run['error_message'])
    
    st.divider()
    
    # System health
    st.subheader("System Health")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("**Database**")
        try:
            from integration_coworker.persistence.db import get_engine_type
            engine = get_engine_type()
            
            if engine == "postgres":
                from integration_coworker.persistence.postgres import check_connection
                if check_connection():
                    st.success("✅ PostgreSQL connected")
                else:
                    st.error("❌ PostgreSQL connection failed")
            else:
                st.info("📁 SQLite (local)")
        except Exception as e:
            st.warning(f"⚠️ {e}")
    
    with col2:
        st.markdown("**LLM**")
        try:
            from integration_coworker.config import get_settings
            settings = get_settings()
            
            if settings.llm.use_mock:
                st.info("🤖 Mock mode")
            elif settings.llm.api_key:
                st.success(f"✅ {settings.llm.default_model}")
            else:
                st.warning("⚠️ Not configured")
        except Exception as e:
            st.warning(f"⚠️ {e}")
    
    with col3:
        st.markdown("**Embeddings**")
        try:
            from integration_coworker.config import get_settings
            settings = get_settings()
            
            if settings.llm.api_key:
                st.success(f"✅ {settings.embedding.model}")
            else:
                st.warning("⚠️ Requires API key")
        except Exception as e:
            st.warning(f"⚠️ {e}")


if __name__ == "__main__":
    main()

"""
Dashboard page for Integration Co-Worker.

Shows:
- Recent run history with status
- Quick stats (success rate, avg duration)
- Quick actions (resume, retry)
- System health indicators
- P1: Resume Capability - Resume interrupted runs
- P1: Artifact Cleanup - Purge old artifacts

V5.0 Production Hardening additions:
- Resume button actually calls recovery.resume_run()
- Artifact cleanup with configurable days
- LangSmith sync controls
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


# -----------------------------------------------------------------
# P1: Resume Capability Helper Functions (V5.0)
# -----------------------------------------------------------------

def get_resumable_runs() -> List[Dict[str, Any]]:
    """
    Get list of runs that can be resumed.
    
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
                               (SELECT node_key FROM integration_gold.run_checkpoints 
                                WHERE run_id = rs.run_id 
                                ORDER BY created_at DESC LIMIT 1) as last_node
                        FROM integration_gold.run_status rs
                        WHERE rs.status IN ('running', 'failed', 'interrupted')
                          AND EXISTS (SELECT 1 FROM integration_gold.run_checkpoints 
                                      WHERE run_id = rs.run_id)
                        ORDER BY rs.started_at DESC
                        LIMIT 10
                    """)
                    return [
                        {"run_id": r[0], "provider_code": r[1], "status": r[2], 
                         "started_at": r[3], "last_node": r[4]}
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


def resume_run(run_id: str) -> Dict[str, Any]:
    """
    Resume an interrupted run.
    
    Returns result dict from recovery.resume_run().
    """
    try:
        from integration_coworker.api.recovery import resume_run as api_resume_run
        result = api_resume_run(run_id)
        return {"success": True, "result": result}
    except ValueError as e:
        return {"success": False, "error": f"No checkpoint found: {e}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


# -----------------------------------------------------------------
# P1: Artifact Cleanup Helper Functions (V5.0)
# -----------------------------------------------------------------

def purge_old_artifacts(days: int, dry_run: bool = False) -> Dict[str, Any]:
    """
    Purge artifacts older than N days.
    
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


# -----------------------------------------------------------------
# P3: LangSmith Sync Helper Functions (V5.0)
# -----------------------------------------------------------------

def sync_langsmith_feedback(days: int = 7) -> Dict[str, Any]:
    """
    Sync feedback with LangSmith.
    
    Note: API returns 'synced' (pulled from LangSmith), 'skipped', 'errors'.
    Push happens automatically when creating feedback via create_feedback(also_langsmith=True).
    """
    try:
        from integration_coworker.feedback.langsmith_sync import (
            sync_langsmith_feedback as api_sync,
            is_langsmith_available,
        )
        
        if not is_langsmith_available():
            return {"success": False, "error": "LangSmith not configured (set LANGSMITH_API_KEY)"}
        
        result = api_sync(days=days)
        
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
                    if run['status'] in ('running', 'failed', 'interrupted'):
                        if st.button("🔄 Resume", key=f"resume_{run['run_id']}", type="primary"):
                            with st.spinner(f"Resuming run {run['run_id']}..."):
                                result = resume_run(run['run_id'])
                            if result.get("success"):
                                st.success(f"✅ Run resumed successfully!")
                                st.rerun()
                            else:
                                st.error(f"❌ {result.get('error', 'Unknown error')}")
                with action_col2:
                    if run['status'] == 'failed':
                        if st.button("🔁 View Error", key=f"error_{run['run_id']}"):
                            if run['error_message']:
                                st.code(run['error_message'])
    
    # -----------------------------------------------------------------
    # P3: Run History Export (V5.1)
    # -----------------------------------------------------------------
    st.divider()
    st.subheader("📥 Run History Export")
    
    export_col1, export_col2, export_col3 = st.columns([2, 1, 1])
    
    with export_col1:
        export_limit = st.number_input(
            "Export last N runs",
            min_value=10,
            max_value=1000,
            value=100,
            key="run_export_limit",
        )
    
    with export_col2:
        if runs:
            import csv
            import io
            
            # Prepare CSV
            csv_buffer = io.StringIO()
            fieldnames = ["run_id", "provider_code", "task_slug", "status", "started_at", "ended_at", "error_message"]
            writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
            writer.writeheader()
            
            for run in runs[:int(export_limit)]:
                writer.writerow({
                    "run_id": run.get("run_id", ""),
                    "provider_code": run.get("provider_code", ""),
                    "task_slug": run.get("task_slug", ""),
                    "status": run.get("status", ""),
                    "started_at": str(run.get("started_at", "")) if run.get("started_at") else "",
                    "ended_at": str(run.get("ended_at", "")) if run.get("ended_at") else "",
                    "error_message": run.get("error_message", "") or "",
                })
            
            csv_data = csv_buffer.getvalue()
            
            st.download_button(
                label="📄 Download CSV",
                data=csv_data,
                file_name="run_history.csv",
                mime="text/csv",
                key="download_run_history_csv",
            )
        else:
            st.info("No runs to export")
    
    with export_col3:
        if runs:
            import json
            
            # Prepare JSON
            export_runs = []
            for run in runs[:int(export_limit)]:
                export_runs.append({
                    "run_id": run.get("run_id", ""),
                    "provider_code": run.get("provider_code", ""),
                    "task_slug": run.get("task_slug", ""),
                    "status": run.get("status", ""),
                    "started_at": str(run.get("started_at", "")) if run.get("started_at") else "",
                    "ended_at": str(run.get("ended_at", "")) if run.get("ended_at") else "",
                    "error_message": run.get("error_message", "") or "",
                })
            
            json_data = json.dumps(export_runs, indent=2)
            
            st.download_button(
                label="📦 Download JSON",
                data=json_data,
                file_name="run_history.json",
                mime="application/json",
                key="download_run_history_json",
            )
    
    # -----------------------------------------------------------------
    # P1: Resume Capability - Resumable Runs Panel (V5.0)
    # -----------------------------------------------------------------
    st.divider()
    st.subheader("🔄 Resumable Runs")
    
    resumable = get_resumable_runs()
    
    if not resumable:
        st.info("No interrupted runs with checkpoints available.")
    else:
        for run in resumable:
            col1, col2, col3 = st.columns([3, 2, 1])
            with col1:
                status_icon = "⏳" if run['status'] == 'running' else "❌" if run['status'] == 'failed' else "⚠️"
                st.text(f"{status_icon} {run['run_id'][:20]}... ({run.get('provider_code', 'unknown')})")
            with col2:
                if run.get('last_node'):
                    st.caption(f"Last: {run['last_node']}")
            with col3:
                if st.button("Resume", key=f"resume_panel_{run['run_id']}", type="primary"):
                    with st.spinner("Resuming..."):
                        result = resume_run(run['run_id'])
                    if result.get("success"):
                        st.success("✅ Resumed!")
                        st.rerun()
                    else:
                        st.error(result.get("error", "Failed"))
    
    # -----------------------------------------------------------------
    # P1: Artifact Cleanup Panel (V5.0)
    # -----------------------------------------------------------------
    st.divider()
    st.subheader("🗑️ Artifact Cleanup")
    
    cleanup_col1, cleanup_col2, cleanup_col3 = st.columns([2, 1, 1])
    
    with cleanup_col1:
        purge_days = st.number_input(
            "Purge artifacts older than (days)",
            min_value=1,
            max_value=365,
            value=30,
            key="artifact_purge_days",
        )
    
    with cleanup_col2:
        if st.button("🔍 Preview", key="artifact_preview"):
            with st.spinner("Scanning artifacts..."):
                preview_result = purge_old_artifacts(int(purge_days), dry_run=True)
            if preview_result.get("success"):
                count = preview_result.get("purged_count", 0)
                if count > 0:
                    st.warning(f"⚠️ Would delete {count} run(s)")
                    with st.expander("Runs to delete", expanded=False):
                        for run_id in preview_result.get("purged_runs", []):
                            st.text(f"  • {run_id}")
                else:
                    st.success("✅ No artifacts to clean up")
            else:
                st.error(preview_result.get("error", "Unknown error"))
    
    with cleanup_col3:
        if st.button("🗑️ Purge Now", key="artifact_purge", type="secondary"):
            with st.spinner("Purging old artifacts..."):
                purge_result = purge_old_artifacts(int(purge_days), dry_run=False)
            if purge_result.get("success"):
                st.success(f"✅ Purged {purge_result.get('purged_count', 0)} run(s)")
            else:
                st.error(purge_result.get("error", "Unknown error"))
    
    # -----------------------------------------------------------------
    # P3: LangSmith Sync Panel (V5.0)
    # -----------------------------------------------------------------
    st.divider()
    st.subheader("📊 LangSmith Feedback Sync")
    
    sync_col1, sync_col2 = st.columns([2, 1])
    
    with sync_col1:
        sync_days = st.number_input(
            "Sync feedback from last (days)",
            min_value=1,
            max_value=90,
            value=7,
            key="langsmith_sync_days",
        )
    
    with sync_col2:
        if st.button("🔄 Sync Now", key="langsmith_sync"):
            with st.spinner("Syncing with LangSmith..."):
                sync_result = sync_langsmith_feedback(int(sync_days))
            if sync_result.get("success"):
                synced = sync_result.get("synced", 0)
                skipped = sync_result.get("skipped", 0)
                errors = sync_result.get("errors", 0)
                templates = sync_result.get("templates_updated", [])
                st.success(f"✅ Synced: {synced} | Skipped: {skipped} | Errors: {errors}")
                if templates:
                    st.caption(f"Templates updated: {', '.join(templates[:5])}")
            else:
                st.error(sync_result.get("error", "Unknown error"))
    
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

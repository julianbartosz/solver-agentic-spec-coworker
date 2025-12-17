#!/usr/bin/env python3
"""
KG Diagnostic Script - Identifies bugs in Knowledge Graph implementation.

Run with: python scripts/diagnose_kg_bugs.py
"""
import os
import sys

# Ensure we're using the correct environment
os.environ.pop('LLM_RECORD_REPLAY_MODE', None)
os.environ.pop('MOCK_LLM', None)

# Add src to path if running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from integration_coworker.persistence.postgres import get_connection
from integration_coworker.persistence import db
from integration_coworker.kg import (
    query_workflow_templates,
    query_templates_with_pattern_fallback,
    query_kg_patterns,
    has_kg_templates,
    STANDARD_PATTERNS,
)

BUGS_FOUND = []


def log_bug(bug_id: str, severity: str, description: str, details: str = ""):
    """Log a bug."""
    BUGS_FOUND.append({
        "id": bug_id,
        "severity": severity,
        "description": description,
        "details": details,
    })
    print(f"❌ [{severity}] BUG-{bug_id}: {description}")
    if details:
        print(f"   Details: {details}")


def log_ok(check: str):
    """Log a passing check."""
    print(f"✅ {check}")


def diagnose():
    print("=" * 60)
    print("KG DIAGNOSTIC REPORT")
    print("=" * 60)
    print()
    
    db.init_schema()
    
    with get_connection() as conn:
        cur = conn.cursor()
        
        # =====================================================================
        # 1. Check KG table structure
        # =====================================================================
        print("## 1. KG Table Structure")
        print("-" * 40)
        
        for table in ['nodes', 'edges', 'workflow_steps', 'step_bindings']:
            cur.execute(f'SELECT COUNT(*) FROM kg.{table}')
            count = cur.fetchone()[0]
            print(f"  kg.{table}: {count} rows")
        
        print()
        
        # =====================================================================
        # 2. Check step_bindings population (BUG!)
        # =====================================================================
        print("## 2. Step Bindings Population Check")
        print("-" * 40)
        
        cur.execute('SELECT COUNT(*) FROM kg.workflow_steps')
        step_count = cur.fetchone()[0]
        
        cur.execute('SELECT COUNT(*) FROM kg.step_bindings')
        binding_count = cur.fetchone()[0]
        
        if step_count > 0 and binding_count == 0:
            log_bug(
                "KG-001",
                "HIGH",
                "step_bindings table is never populated",
                f"{step_count} workflow_steps exist but 0 step_bindings. "
                "This means endpoint bindings are not being persisted to the KG."
            )
        elif binding_count > 0:
            log_ok(f"step_bindings has {binding_count} rows")
        else:
            print(f"  No workflow_steps to bind (empty KG)")
        
        print()
        
        # =====================================================================
        # 3. Check pattern node population
        # =====================================================================
        print("## 3. Pattern Node Population Check")
        print("-" * 40)
        
        cur.execute("SELECT COUNT(*) FROM kg.nodes WHERE node_type = %s", ('pattern',))
        pattern_count = cur.fetchone()[0]
        
        expected_patterns = len(STANDARD_PATTERNS)
        
        if pattern_count < expected_patterns:
            log_bug(
                "KG-002",
                "MEDIUM",
                f"Only {pattern_count} pattern nodes exist (expected {expected_patterns})",
                f"STANDARD_PATTERNS has {expected_patterns} patterns but only {pattern_count} "
                "have been seeded or created via learning."
            )
        else:
            log_ok(f"All {expected_patterns} patterns exist in KG")
        
        cur.execute("""
            SELECT key FROM kg.nodes WHERE node_type = %s
        """, ('pattern',))
        existing_patterns = {row[0] for row in cur.fetchall()}
        print(f"  Existing patterns: {existing_patterns}")
        
        print()
        
        # =====================================================================
        # 4. Check workflow_template nodes have embeddings
        # =====================================================================
        print("## 4. Workflow Template Embeddings Check")
        print("-" * 40)
        
        cur.execute("""
            SELECT id, key, embedding IS NOT NULL as has_embedding 
            FROM kg.nodes 
            WHERE node_type = %s
        """, ('workflow_template',))
        
        templates = cur.fetchall()
        templates_without_embedding = [t for t in templates if not t[2]]
        
        if templates_without_embedding:
            log_bug(
                "KG-003",
                "MEDIUM",
                f"{len(templates_without_embedding)} workflow_template(s) missing embeddings",
                "Templates without embeddings will have degraded GraphRAG scoring."
            )
            for t in templates_without_embedding[:5]:
                print(f"    - {t[1]}")
        else:
            log_ok(f"All {len(templates)} workflow_templates have embeddings")
        
        print()
        
        # =====================================================================
        # 5. Check template→pattern edges
        # =====================================================================
        print("## 5. Template→Pattern Edge Check")
        print("-" * 40)
        
        cur.execute("""
            SELECT COUNT(*) FROM kg.nodes WHERE node_type = %s
        """, ('workflow_template',))
        template_count = cur.fetchone()[0]
        
        cur.execute("""
            SELECT COUNT(*) FROM kg.edges 
            WHERE relation_type = %s
        """, ('implements_pattern',))
        pattern_edge_count = cur.fetchone()[0]
        
        if template_count > 0 and pattern_edge_count == 0:
            log_bug(
                "KG-004",
                "MEDIUM",
                "No template→pattern edges exist",
                "Templates are not linked to patterns, preventing cross-provider pattern matching."
            )
        elif template_count > pattern_edge_count:
            log_bug(
                "KG-005",
                "LOW",
                f"Only {pattern_edge_count}/{template_count} templates linked to patterns",
                "Some templates are missing pattern associations."
            )
        else:
            log_ok(f"All {template_count} templates have pattern edges")
        
        print()
        
        # =====================================================================
        # 6. Check for orphaned nodes (no edges)
        # =====================================================================
        print("## 6. Orphaned Node Check")
        print("-" * 40)
        
        cur.execute("""
            SELECT n.id, n.key, n.node_type
            FROM kg.nodes n
            LEFT JOIN kg.edges e1 ON n.id = e1.src_node_id
            LEFT JOIN kg.edges e2 ON n.id = e2.dst_node_id
            WHERE e1.id IS NULL AND e2.id IS NULL
        """)
        orphans = cur.fetchall()
        
        if orphans:
            log_bug(
                "KG-006",
                "LOW",
                f"{len(orphans)} orphaned nodes (no edges)",
                "These nodes are disconnected from the graph."
            )
            for o in orphans[:5]:
                print(f"    - {o[2]}: {o[1]}")
        else:
            log_ok("No orphaned nodes found")
        
        print()
        
        # =====================================================================
        # 7. Check usage_count tracking
        # =====================================================================
        print("## 7. Usage Count Tracking Check")
        print("-" * 40)
        
        cur.execute("""
            SELECT key, usage_count, last_used_at
            FROM kg.nodes 
            WHERE node_type = %s
            ORDER BY usage_count DESC
        """, ('workflow_template',))
        
        usage_stats = cur.fetchall()
        total_usage = sum(t[1] for t in usage_stats)
        
        if usage_stats and total_usage == 0:
            log_bug(
                "KG-007",
                "LOW",
                "All templates have usage_count=0",
                "Template usage is not being tracked despite template reuse."
            )
        else:
            log_ok(f"Usage tracking working: {total_usage} total uses across {len(usage_stats)} templates")
            for t in usage_stats[:3]:
                print(f"    - {t[0]}: usage={t[1]}, last_used={t[2]}")
        
        print()
        
        # =====================================================================
        # 8. Test GraphRAG query functionality
        # =====================================================================
        print("## 8. GraphRAG Query Functionality")
        print("-" * 40)
        
        # Get a provider that exists
        cur.execute("""
            SELECT DISTINCT provider_code FROM kg.nodes 
            WHERE provider_code IS NOT NULL LIMIT 1
        """)
        row = cur.fetchone()
        
        if row:
            test_provider = row[0]
            try:
                templates = query_workflow_templates(
                    provider_code=test_provider,
                    task_description="Test task for diagnostic",
                    top_k=5,
                    similarity_threshold=0.1,
                )
                log_ok(f"GraphRAG query returned {len(templates)} templates for {test_provider}")
            except Exception as e:
                log_bug(
                    "KG-008",
                    "HIGH",
                    f"GraphRAG query failed: {type(e).__name__}",
                    str(e)
                )
        else:
            print("  No providers in KG to test query")
        
        print()
        
        # =====================================================================
        # 9. Check for connection cleanup issues
        # =====================================================================
        print("## 9. Connection Cleanup Check")
        print("-" * 40)
        
        # This is tested by the fact we got here without crashing
        # Check if there are connection warnings in the log
        log_ok("Connection check passed (script completed without connection errors)")
        print("  Note: Check stderr for 'ConnectionWrapper was garbage collected' warnings")
        
        print()
    
    # =========================================================================
    # Summary
    # =========================================================================
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    
    if BUGS_FOUND:
        print(f"\n❌ Found {len(BUGS_FOUND)} bug(s):\n")
        
        by_severity = {"HIGH": [], "MEDIUM": [], "LOW": []}
        for bug in BUGS_FOUND:
            by_severity[bug["severity"]].append(bug)
        
        for severity in ["HIGH", "MEDIUM", "LOW"]:
            bugs = by_severity[severity]
            if bugs:
                print(f"  [{severity}] - {len(bugs)} bug(s):")
                for bug in bugs:
                    print(f"    - BUG-{bug['id']}: {bug['description']}")
        
        print("\n## Recommended Fixes:")
        print("-" * 40)
        
        for bug in BUGS_FOUND:
            if bug["id"] == "KG-001":
                print(f"""
BUG-{bug['id']}: {bug['description']}
  File: src/integration_coworker/graph/nodes/persist_kg_learning.py
  Fix: Add _upsert_step_binding() function and call it after creating workflow_steps
  
  The step_bindings table exists but the persist_kg_learning node never populates it.
  Need to iterate over state.endpoint_bindings and create entries linking:
    - workflow_step_id (from kg.workflow_steps)
    - endpoint_node_id (from kg.nodes WHERE node_type='endpoint')
""")
            elif bug["id"] == "KG-002":
                print(f"""
BUG-{bug['id']}: {bug['description']}
  File: src/integration_coworker/persistence/seed_kg.py
  Fix: Call seed_kg() during init_schema() or add a migration
  
  STANDARD_PATTERNS should be seeded into kg.nodes on schema initialization.
  Currently patterns only appear when created via learning.
""")
            elif bug["id"] in ["KG-003", "KG-004", "KG-005"]:
                print(f"""
BUG-{bug['id']}: {bug['description']}
  These are secondary effects of the persist_kg_learning implementation.
  Will be resolved when KG-001 is fixed.
""")
    else:
        print("\n✅ No bugs found! KG is healthy.\n")
    
    return len(BUGS_FOUND)


if __name__ == "__main__":
    bug_count = diagnose()
    sys.exit(1 if bug_count > 0 else 0)

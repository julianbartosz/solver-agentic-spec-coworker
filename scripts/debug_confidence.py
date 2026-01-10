#!/usr/bin/env python
"""Debug script to test feedback confidence updates."""

import os
import sys

# Force postgres
os.environ["USE_SQLITE"] = "false"

from integration_coworker.persistence import db
from integration_coworker.feedback.confidence import (
    update_node_confidence, 
    get_feedback_for_template,
    update_all_confidences,
)

def main():
    import psycopg
    
    db._engine_type = None
    print(f"Engine type: {db.get_engine_type()}")
    
    db_url = os.environ["DATABASE_URL"]
    test_key = "template.test_debug.task_a"
    provider = "test_debug"
    
    try:
        # Setup test data
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                # Clean first
                cur.execute(
                    "DELETE FROM kg.feedback_records WHERE template_key LIKE %s",
                    (f"template.{provider}.%",)
                )
                cur.execute(
                    "DELETE FROM kg.confidence_history WHERE node_key LIKE %s",
                    (f"template.{provider}.%",)
                )
                cur.execute(
                    "DELETE FROM kg.nodes WHERE key LIKE %s",
                    (f"template.{provider}.%",)
                )
                
                # Create test node
                cur.execute("""
                    INSERT INTO kg.nodes 
                    (key, node_type, name, provider_code, confidence_score)
                    VALUES (%s, 'workflow_template', 'Test', %s, 1.0)
                """, (test_key, provider))
                
                # Insert feedback
                cur.execute("""
                    INSERT INTO kg.feedback_records
                    (run_id, template_key, feedback_type, score, source, created_at, synced_at)
                    VALUES ('run-debug', %s, 'thumbs', 0.0, 'cli', NOW(), NOW())
                """, (test_key,))
                
                conn.commit()
                
                # Verify
                cur.execute(
                    "SELECT COUNT(*) FROM kg.feedback_records WHERE template_key = %s",
                    (test_key,)
                )
                print(f"Feedback count in DB: {cur.fetchone()[0]}")
                
                cur.execute(
                    "SELECT key, confidence_score FROM kg.nodes WHERE key = %s",
                    (test_key,)
                )
                row = cur.fetchone()
                print(f"Node in DB: key={row[0]}, confidence={row[1]}")
        
        # Test get_feedback_for_template
        feedbacks = get_feedback_for_template(test_key)
        print(f"Feedbacks retrieved via function: {len(feedbacks)}")
        for f in feedbacks:
            print(f"  - type={f.feedback_type.value}, score={f.score}, source={f.source.value}")
        
        # Test update_node_confidence
        print("\nCalling update_node_confidence...")
        result = update_node_confidence(test_key, reason="debug_test")
        print(f"Result: {result}")
        
        # Test update_all_confidences
        print("\nCalling update_all_confidences...")
        stats = update_all_confidences(provider_code=provider, reason="debug_batch")
        print(f"Stats: {stats}")
        
        # Check final state
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT confidence_score FROM kg.nodes WHERE key = %s",
                    (test_key,)
                )
                final = cur.fetchone()
                print(f"\nFinal confidence in DB: {final[0] if final else 'NOT FOUND'}")
                
                cur.execute(
                    "SELECT COUNT(*) FROM kg.confidence_history WHERE node_key = %s",
                    (test_key,)
                )
                hist_count = cur.fetchone()[0]
                print(f"History records: {hist_count}")
        
    finally:
        # Cleanup
        with psycopg.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM kg.feedback_records WHERE template_key LIKE %s",
                    (f"template.{provider}.%",)
                )
                cur.execute(
                    "DELETE FROM kg.confidence_history WHERE node_key LIKE %s",
                    (f"template.{provider}.%",)
                )
                cur.execute(
                    "DELETE FROM kg.nodes WHERE key LIKE %s",
                    (f"template.{provider}.%",)
                )
                conn.commit()
        print("\nCleanup done.")

if __name__ == "__main__":
    main()

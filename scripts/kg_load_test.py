#!/usr/bin/env python3
"""KG Load Test Script.

Tests the scalability of KG operations including:
- Node persistence
- Edge persistence
- Vector similarity search (with pgvector)

Usage:
    python scripts/kg_load_test.py [--fields N] [--guides M] [--run-all]
    
Environment:
    DATABASE_URL: PostgreSQL connection string (required)
    OPENAI_API_KEY: Optional, enables real embeddings (otherwise uses deterministic fallback)
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class LoadTestResult:
    """Results from a load test run."""
    test_name: str
    num_items: int
    total_time_ms: float
    avg_time_ms: float
    p95_time_ms: float
    items_per_second: float
    success: bool
    error: Optional[str] = None


def deterministic_embedding(text: str, dim: int = 1536) -> List[float]:
    """Generate deterministic embedding from text hash.
    
    Used when real embeddings are not available.
    """
    hash_bytes = hashlib.sha256(text.encode()).digest()
    embedding = []
    for i in range(dim):
        byte_idx = i % len(hash_bytes)
        embedding.append((hash_bytes[byte_idx] - 128) / 128.0)
    return embedding


def measure_operation(
    operation: Callable[[], Any],
    num_iterations: int,
    warmup: int = 2,
) -> tuple[float, float, float, List[float]]:
    """Measure operation timing statistics.
    
    Returns:
        (total_ms, avg_ms, p95_ms, all_times)
    """
    # Warmup
    for _ in range(warmup):
        try:
            operation()
        except Exception:
            pass
    
    times = []
    for _ in range(num_iterations):
        start = time.perf_counter()
        operation()
        elapsed = (time.perf_counter() - start) * 1000  # ms
        times.append(elapsed)
    
    times.sort()
    total = sum(times)
    avg = total / len(times) if times else 0
    p95_idx = int(len(times) * 0.95)
    p95 = times[p95_idx] if times else 0
    
    return total, avg, p95, times


def run_node_persistence_test(
    conn,
    num_fields: int,
    embedding_fn: Callable[[str], List[float]],
) -> LoadTestResult:
    """Test node persistence performance."""
    from integration_coworker.kg.persist import upsert_node
    
    logger.info(f"Running node persistence test with {num_fields} fields...")
    
    times = []
    errors = []
    
    for i in range(num_fields):
        field_name = f"test_field_{i}"
        embedding = embedding_fn(field_name)
        
        start = time.perf_counter()
        try:
            upsert_node(
                conn=conn,
                node_type="file_field",
                natural_key=f"load_test.spec.{field_name}",
                name=field_name,
                properties={"data_type": "string", "position": i},
                embedding=embedding,
            )
            conn.commit()
        except Exception as e:
            errors.append(str(e))
            conn.rollback()
        
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    
    times.sort()
    total = sum(times)
    avg = total / len(times) if times else 0
    p95_idx = int(len(times) * 0.95)
    p95 = times[p95_idx] if times else 0
    
    return LoadTestResult(
        test_name="node_persistence",
        num_items=num_fields,
        total_time_ms=total,
        avg_time_ms=avg,
        p95_time_ms=p95,
        items_per_second=(num_fields / (total / 1000)) if total > 0 else 0,
        success=len(errors) == 0,
        error=errors[0] if errors else None,
    )


def run_edge_persistence_test(
    conn,
    num_edges: int,
) -> LoadTestResult:
    """Test edge persistence performance."""
    from integration_coworker.kg.persist import upsert_node, upsert_edge
    
    logger.info(f"Running edge persistence test with {num_edges} edges...")
    
    # Create source and target nodes first
    src_ids = []
    dst_ids = []
    
    for i in range(num_edges):
        src_id = upsert_node(
            conn=conn,
            node_type="file_field",
            natural_key=f"edge_test.src.{i}",
            name=f"src_field_{i}",
        )
        dst_id = upsert_node(
            conn=conn,
            node_type="guide_field",
            natural_key=f"edge_test.dst.{i}",
            name=f"dst_field_{i}",
        )
        src_ids.append(src_id)
        dst_ids.append(dst_id)
    conn.commit()
    
    times = []
    errors = []
    
    for i, (src_id, dst_id) in enumerate(zip(src_ids, dst_ids)):
        start = time.perf_counter()
        try:
            upsert_edge(
                conn=conn,
                src_node_id=src_id,
                dst_node_id=dst_id,
                relation_type="maps_to",
                weight=0.9,
                properties={"method": "load_test"},
            )
            conn.commit()
        except Exception as e:
            errors.append(str(e))
            conn.rollback()
        
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    
    times.sort()
    total = sum(times)
    avg = total / len(times) if times else 0
    p95_idx = int(len(times) * 0.95)
    p95 = times[p95_idx] if times else 0
    
    return LoadTestResult(
        test_name="edge_persistence",
        num_items=num_edges,
        total_time_ms=total,
        avg_time_ms=avg,
        p95_time_ms=p95,
        items_per_second=(num_edges / (total / 1000)) if total > 0 else 0,
        success=len(errors) == 0,
        error=errors[0] if errors else None,
    )


def run_vector_search_test(
    conn,
    num_fields: int,
    num_queries: int,
    embedding_fn: Callable[[str], List[float]],
) -> LoadTestResult:
    """Test vector similarity search performance."""
    from integration_coworker.kg.persist import upsert_node, find_similar_fields
    
    logger.info(f"Running vector search test: {num_fields} fields, {num_queries} queries...")
    
    # First, populate fields with embeddings
    for i in range(num_fields):
        field_name = f"search_test_field_{i}"
        embedding = embedding_fn(field_name)
        upsert_node(
            conn=conn,
            node_type="file_field",
            natural_key=f"search_test.spec.{field_name}",
            name=field_name,
            properties={"data_type": "string"},
            embedding=embedding,
        )
    conn.commit()
    
    logger.info(f"Populated {num_fields} fields, running {num_queries} queries...")
    
    times = []
    errors = []
    
    for i in range(num_queries):
        query_text = f"query_field_{i % 100}"  # Reuse some queries
        query_embedding = embedding_fn(query_text)
        
        start = time.perf_counter()
        try:
            results = find_similar_fields(
                conn=conn,
                query_embedding=query_embedding,
                limit=10,
                min_similarity=0.5,
            )
        except Exception as e:
            errors.append(str(e))
        
        elapsed = (time.perf_counter() - start) * 1000
        times.append(elapsed)
    
    times.sort()
    total = sum(times)
    avg = total / len(times) if times else 0
    p95_idx = int(len(times) * 0.95)
    p95 = times[p95_idx] if times else 0
    
    return LoadTestResult(
        test_name="vector_search",
        num_items=num_queries,
        total_time_ms=total,
        avg_time_ms=avg,
        p95_time_ms=p95,
        items_per_second=(num_queries / (total / 1000)) if total > 0 else 0,
        success=len(errors) == 0,
        error=errors[0] if errors else None,
    )


def cleanup_test_data(conn):
    """Remove test data from KG tables."""
    logger.info("Cleaning up test data...")
    with conn.cursor() as cur:
        cur.execute("""
            DELETE FROM kg.edges 
            WHERE src_node_id IN (
                SELECT id FROM kg.nodes WHERE key LIKE 'load_test.%' OR key LIKE 'edge_test.%' OR key LIKE 'search_test.%'
            )
        """)
        cur.execute("""
            DELETE FROM kg.nodes 
            WHERE key LIKE 'load_test.%' OR key LIKE 'edge_test.%' OR key LIKE 'search_test.%'
        """)
    conn.commit()


def print_results(results: List[LoadTestResult]):
    """Print formatted results table."""
    print("\n" + "=" * 80)
    print("KG LOAD TEST RESULTS")
    print("=" * 80)
    print(f"{'Test':<20} {'Items':<10} {'Total(ms)':<12} {'Avg(ms)':<10} {'P95(ms)':<10} {'Items/s':<12} {'Status'}")
    print("-" * 80)
    
    for r in results:
        status = "✅ PASS" if r.success else f"❌ FAIL: {r.error}"
        print(f"{r.test_name:<20} {r.num_items:<10} {r.total_time_ms:<12.2f} {r.avg_time_ms:<10.2f} {r.p95_time_ms:<10.2f} {r.items_per_second:<12.1f} {status}")
    
    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="KG Load Test")
    parser.add_argument("--fields", type=int, default=100, help="Number of fields to test")
    parser.add_argument("--guides", type=int, default=50, help="Number of guide fields")
    parser.add_argument("--queries", type=int, default=100, help="Number of search queries")
    parser.add_argument("--run-all", action="store_true", help="Run with multiple scale levels")
    parser.add_argument("--cleanup", action="store_true", help="Cleanup test data after run")
    args = parser.parse_args()
    
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("DATABASE_URL environment variable is required")
        sys.exit(1)
    
    # Check for real embeddings
    use_real_embeddings = bool(os.environ.get("OPENAI_API_KEY"))
    if use_real_embeddings:
        logger.info("Using real OpenAI embeddings")
        # Import real embedding function
        try:
            from integration_coworker.kg import _compute_embedding
            embedding_fn = lambda text: _compute_embedding(text)
        except ImportError:
            logger.warning("Could not import real embedding function, using deterministic fallback")
            embedding_fn = deterministic_embedding
    else:
        logger.info("Using deterministic embedding fallback (no OPENAI_API_KEY)")
        embedding_fn = deterministic_embedding
    
    # Connect to database
    try:
        import psycopg
        conn = psycopg.connect(database_url)
        logger.info("Connected to database")
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        sys.exit(1)
    
    results = []
    
    try:
        if args.run_all:
            # Run multiple scale levels
            scale_levels = [
                (100, 50, 100),    # Small
                (1000, 100, 500),  # Medium
                (10000, 500, 1000), # Large
            ]
            
            for num_fields, num_guides, num_queries in scale_levels:
                logger.info(f"\n--- Scale level: {num_fields} fields, {num_guides} guides, {num_queries} queries ---")
                
                cleanup_test_data(conn)
                
                results.append(run_node_persistence_test(conn, num_fields, embedding_fn))
                results.append(run_edge_persistence_test(conn, num_guides))
                results.append(run_vector_search_test(conn, num_fields, num_queries, embedding_fn))
        else:
            # Single run
            cleanup_test_data(conn)
            
            results.append(run_node_persistence_test(conn, args.fields, embedding_fn))
            results.append(run_edge_persistence_test(conn, args.guides))
            results.append(run_vector_search_test(conn, args.fields, args.queries, embedding_fn))
        
        print_results(results)
        
        if args.cleanup:
            cleanup_test_data(conn)
            logger.info("Test data cleaned up")
        
    finally:
        conn.close()
    
    # Exit with error if any test failed
    if any(not r.success for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()

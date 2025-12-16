#!/usr/bin/env python3
"""
Postgres + pgvector database initialization script.

This script:
1. Connects to Postgres using DATABASE_URL or component env vars
2. Creates the pgvector extension if not present
3. Creates all schemas (spec_silver, integration_gold, repo_meta, kg)
4. Creates all tables with proper vector columns
5. Creates ivfflat indexes for fast similarity search

Usage:
    python scripts/init_db_postgres.py [--verify] [--drop-existing]

Options:
    --verify        Only check if schema exists, don't create
    --drop-existing Drop existing schemas before creating (DANGEROUS)
"""

import argparse
import logging
import os
import sys
from pathlib import Path

# Add src to path for imports
src_path = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_path))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def get_connection_params() -> dict:
    """Get Postgres connection parameters from environment."""
    database_url = os.environ.get("DATABASE_URL")
    
    if database_url:
        # Parse DATABASE_URL
        # Format: postgresql://user:password@host:port/dbname
        from urllib.parse import urlparse
        parsed = urlparse(database_url)
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "dbname": parsed.path.lstrip("/") or "integration_coworker",
            "user": parsed.username or "postgres",
            "password": parsed.password or "",
        }
    
    # Use component env vars
    return {
        "host": os.environ.get("POSTGRES_HOST", "localhost"),
        "port": int(os.environ.get("POSTGRES_PORT", "5432")),
        "dbname": os.environ.get("POSTGRES_DB", "integration_coworker"),
        "user": os.environ.get("POSTGRES_USER", "postgres"),
        "password": os.environ.get("POSTGRES_PASSWORD", ""),
    }


def check_pgvector(conn) -> bool:
    """Check if pgvector extension is installed."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT extversion 
            FROM pg_extension 
            WHERE extname = 'vector'
        """)
        result = cur.fetchone()
        if result:
            logger.info(f"pgvector extension version: {result[0]}")
            return True
        return False


def install_pgvector(conn) -> bool:
    """Install pgvector extension."""
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.commit()
        logger.info("pgvector extension installed successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to install pgvector: {e}")
        logger.error("You may need to install pgvector manually. See docs/GETTING_STARTED.md")
        return False


def verify_schemas(conn) -> dict:
    """Verify which schemas exist."""
    schemas = ["spec_silver", "integration_gold", "repo_meta", "kg"]
    status = {}
    
    with conn.cursor() as cur:
        for schema in schemas:
            cur.execute("""
                SELECT EXISTS(
                    SELECT 1 FROM information_schema.schemata 
                    WHERE schema_name = %s
                )
            """, (schema,))
            exists = cur.fetchone()[0]
            status[schema] = exists
            logger.info(f"Schema {schema}: {'✓ exists' if exists else '✗ missing'}")
    
    return status


def verify_tables(conn) -> dict:
    """Verify which tables exist in each schema."""
    expected_tables = {
        "spec_silver": [
            "spec_documents", "spec_sections", "spec_chunks",
            "endpoints", "schemas", "schema_fields", "entities", "events",
        ],
        "integration_gold": [
            "integration_tasks", "workflow_templates", "workflow_nodes",
            "workflow_edges", "endpoint_bindings", "policies", "code_artifacts",
        ],
        "repo_meta": [
            "repo_profiles", "repo_files", "integration_hooks",
        ],
        "kg": [
            "kg_nodes", "kg_edges",
        ],
    }
    
    status = {}
    with conn.cursor() as cur:
        for schema, tables in expected_tables.items():
            status[schema] = {}
            for table in tables:
                cur.execute("""
                    SELECT EXISTS(
                        SELECT 1 FROM information_schema.tables 
                        WHERE table_schema = %s AND table_name = %s
                    )
                """, (schema, table))
                exists = cur.fetchone()[0]
                status[schema][table] = exists
                
    return status


def verify_vector_columns(conn) -> dict:
    """Verify vector columns exist with correct dimensions."""
    expected_vectors = [
        ("spec_silver", "spec_chunks", "embedding"),
        ("kg", "kg_nodes", "embedding"),
    ]
    
    status = {}
    with conn.cursor() as cur:
        for schema, table, column in expected_vectors:
            cur.execute("""
                SELECT udt_name, character_maximum_length
                FROM information_schema.columns
                WHERE table_schema = %s 
                  AND table_name = %s 
                  AND column_name = %s
            """, (schema, table, column))
            result = cur.fetchone()
            key = f"{schema}.{table}.{column}"
            if result and result[0] == "vector":
                status[key] = "✓ vector column exists"
            elif result:
                status[key] = f"✗ wrong type: {result[0]}"
            else:
                status[key] = "✗ column missing"
            logger.info(f"Vector column {key}: {status[key]}")
    
    return status


def drop_schemas(conn) -> None:
    """Drop existing schemas (DANGEROUS)."""
    schemas = ["spec_silver", "integration_gold", "repo_meta", "kg"]
    
    with conn.cursor() as cur:
        for schema in schemas:
            logger.warning(f"Dropping schema {schema}...")
            cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    
    conn.commit()
    logger.info("All schemas dropped")


def init_schemas(conn) -> None:
    """Initialize all database schemas."""
    try:
        from integration_coworker.persistence.postgres import (
            SPEC_SILVER_DDL,
            INTEGRATION_GOLD_DDL,
            REPO_META_DDL,
            KG_DDL,
        )
    except ImportError:
        logger.error("Could not import DDL from postgres.py")
        raise
    
    ddl_blocks = [
        ("spec_silver", SPEC_SILVER_DDL),
        ("integration_gold", INTEGRATION_GOLD_DDL),
        ("repo_meta", REPO_META_DDL),
        ("kg", KG_DDL),
    ]
    
    with conn.cursor() as cur:
        for schema_name, ddl in ddl_blocks:
            logger.info(f"Creating schema {schema_name}...")
            try:
                cur.execute(ddl)
                logger.info(f"Schema {schema_name} created successfully")
            except Exception as e:
                logger.error(f"Error creating {schema_name}: {e}")
                raise
    
    conn.commit()
    logger.info("All schemas created successfully")


def verify_indexes(conn) -> dict:
    """Verify vector indexes exist."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT indexrelname, pg_size_pretty(pg_relation_size(indexrelid))
            FROM pg_stat_user_indexes
            WHERE indexrelname LIKE '%embedding%'
        """)
        indexes = cur.fetchall()
        
    status = {}
    for name, size in indexes:
        status[name] = f"✓ exists ({size})"
        logger.info(f"Index {name}: {size}")
    
    if not indexes:
        logger.warning("No embedding indexes found. They will be created when data is inserted.")
    
    return status


def main():
    parser = argparse.ArgumentParser(
        description="Initialize Postgres + pgvector database for Agentic Integration Designer"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Only verify schema exists, don't create",
    )
    parser.add_argument(
        "--drop-existing",
        action="store_true",
        help="Drop existing schemas before creating (DANGEROUS)",
    )
    args = parser.parse_args()
    
    # Get connection params
    params = get_connection_params()
    logger.info(f"Connecting to Postgres at {params['host']}:{params['port']}/{params['dbname']}")
    
    try:
        import psycopg
    except ImportError:
        logger.error("psycopg not installed. Run: pip install 'psycopg[binary]'")
        sys.exit(1)
    
    try:
        conn = psycopg.connect(**params)
        logger.info("Connected to Postgres successfully")
    except Exception as e:
        logger.error(f"Failed to connect to Postgres: {e}")
        sys.exit(1)
    
    try:
        # Check pgvector
        if not check_pgvector(conn):
            if args.verify:
                logger.error("pgvector extension not installed")
                sys.exit(1)
            else:
                if not install_pgvector(conn):
                    sys.exit(1)
        
        if args.verify:
            # Verify mode
            logger.info("\n=== Verifying Database Schema ===\n")
            
            schema_status = verify_schemas(conn)
            table_status = verify_tables(conn)
            vector_status = verify_vector_columns(conn)
            index_status = verify_indexes(conn)
            
            # Summary
            all_schemas_ok = all(schema_status.values())
            all_tables_ok = all(
                all(tables.values()) 
                for tables in table_status.values()
            )
            all_vectors_ok = all("✓" in v for v in vector_status.values())
            
            print("\n=== Summary ===")
            print(f"Schemas: {'✓ OK' if all_schemas_ok else '✗ MISSING'}")
            print(f"Tables: {'✓ OK' if all_tables_ok else '✗ MISSING'}")
            print(f"Vector columns: {'✓ OK' if all_vectors_ok else '✗ MISSING'}")
            print(f"Indexes: {len(index_status)} embedding indexes found")
            
            if not (all_schemas_ok and all_tables_ok and all_vectors_ok):
                logger.warning("\nRun without --verify to create missing objects")
                sys.exit(1)
            
        else:
            # Create mode
            if args.drop_existing:
                confirm = input("This will DROP all existing schemas. Type 'YES' to confirm: ")
                if confirm != "YES":
                    logger.info("Aborted")
                    sys.exit(0)
                drop_schemas(conn)
            
            logger.info("\n=== Initializing Database Schema ===\n")
            init_schemas(conn)
            
            # Verify after creation
            logger.info("\n=== Verifying Created Schema ===\n")
            verify_schemas(conn)
            verify_vector_columns(conn)
            
            logger.info("\n✓ Database initialization complete!")
            logger.info("You can now run the application with DATABASE_URL set.")
            
    finally:
        conn.close()


if __name__ == "__main__":
    main()

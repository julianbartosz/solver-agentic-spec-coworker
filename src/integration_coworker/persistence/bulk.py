"""
Bulk Write API for Postgres and SQLite

V22-007: Optimized persistence for checkpoint nodes.

APPROACH DEBATE (per user requirements):
=========================================

Approach A: Reduce Payload
- Pros: Reduces both DB size and wall clock time
- Cons: Requires state/checkpoint contract refactor
- Status: Already addressed in V22-001 (bounded state estimator)

Approach B: Batched Multi-Row INSERT
- Pros: Simple, good speedup (10-50x vs row-by-row)
- Cons: Still slower than COPY at scale (limited by SQL parsing)
- Implementation: Single INSERT with multiple VALUES tuples

Approach C: COPY FROM STDIN (psycopg3)
- Pros: Fastest bulk-ingest path in Postgres (bypasses SQL parsing)
- Cons: psycopg3-specific, requires different code path for SQLite
- Reference: https://pganalyze.com/blog/5mins-postgres-optimizing-bulk-loads-copy-vs-insert
- Reference: https://www.psycopg.org/psycopg3/docs/basic/copy.html

DECISION RULE:
==============
- If rows <= BATCH_THRESHOLD (500): Use multi-row INSERT (Approach B)
- If rows > BATCH_THRESHOLD: Use COPY FROM STDIN (Approach C)

This module provides:
- write_rows_insert(conn, table, columns, rows) - Multi-row INSERT
- write_rows_copy(conn, table, columns, rows) - COPY FROM STDIN
- write_rows(conn, table, columns, rows) - Auto-selects based on row count
"""
import json
import logging
from io import StringIO
from typing import Any, List, Optional, Tuple, Sequence

from integration_coworker.persistence.sql_helpers import get_engine_type

logger = logging.getLogger(__name__)

# V22-007: Threshold for switching from INSERT to COPY
BATCH_THRESHOLD = 500


def write_rows_insert(
    conn,
    table: str,
    columns: List[str],
    rows: Sequence[Tuple[Any, ...]],
    schema: Optional[str] = None,
    on_conflict: Optional[str] = None,
) -> int:
    """
    Write rows using multi-row INSERT.
    
    Args:
        conn: Database connection (psycopg3 or sqlite3)
        table: Table name
        columns: Column names
        rows: Sequence of tuples (values for each row)
        schema: Schema name for Postgres (e.g., "spec_silver")
        on_conflict: Optional conflict handling:
            - "ignore": ON CONFLICT DO NOTHING
            - "update": ON CONFLICT DO UPDATE SET (all columns)
            - None: No conflict handling
    
    Returns:
        Number of rows affected
    
    Example:
        write_rows_insert(conn, "endpoints", ["method", "path"], [
            ("GET", "/api/v1"),
            ("POST", "/api/v1"),
        ])
    """
    if not rows:
        return 0
    
    engine = get_engine_type()
    is_postgres = engine == "postgres"
    
    full_table = f"{schema}.{table}" if schema and is_postgres else table
    col_list = ", ".join(columns)
    
    if is_postgres:
        # psycopg3 multi-row INSERT with %s placeholders
        placeholder_row = f"({', '.join(['%s'] * len(columns))})"
        placeholders = ", ".join([placeholder_row] * len(rows))
        
        sql = f"INSERT INTO {full_table} ({col_list}) VALUES {placeholders}"
        
        if on_conflict == "ignore":
            sql += " ON CONFLICT DO NOTHING"
        elif on_conflict == "update":
            update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
            sql += f" ON CONFLICT DO UPDATE SET {update_set}"
        
        # Flatten rows into single tuple
        flat_values = tuple(val for row in rows for val in row)
        
        cur = conn.cursor()
        cur.execute(sql, flat_values)
        rowcount = cur.rowcount
        conn.commit()
        return rowcount
    else:
        # SQLite: executemany is efficient for multi-row
        placeholder_row = f"({', '.join(['?'] * len(columns))})"
        
        if on_conflict == "ignore":
            sql = f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES {placeholder_row}"
        elif on_conflict == "update":
            sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES {placeholder_row}"
        else:
            sql = f"INSERT INTO {table} ({col_list}) VALUES {placeholder_row}"
        
        cur = conn.cursor()
        cur.executemany(sql, rows)
        conn.commit()
        return cur.rowcount if hasattr(cur, 'rowcount') else len(rows)


def write_rows_copy(
    conn,
    table: str,
    columns: List[str],
    rows: Sequence[Tuple[Any, ...]],
    schema: Optional[str] = None,
) -> int:
    """
    Write rows using COPY FROM STDIN (Postgres only).
    
    Falls back to write_rows_insert for SQLite.
    
    Args:
        conn: Postgres connection (psycopg3)
        table: Table name
        columns: Column names
        rows: Sequence of tuples (values for each row)
        schema: Schema name for Postgres
    
    Returns:
        Number of rows written
    
    Reference:
        https://www.psycopg.org/psycopg3/docs/basic/copy.html
    """
    if not rows:
        return 0
    
    engine = get_engine_type()
    
    if engine != "postgres":
        # SQLite doesn't support COPY, fall back to INSERT
        logger.debug(f"COPY not available on {engine}, using INSERT")
        return write_rows_insert(conn, table, columns, rows, schema)
    
    full_table = f"{schema}.{table}" if schema else table
    col_list = ", ".join(columns)
    
    # Build COPY command
    copy_sql = f"COPY {full_table} ({col_list}) FROM STDIN"
    
    cur = conn.cursor()
    
    try:
        # Use psycopg3 copy protocol
        with cur.copy(copy_sql) as copy:
            for row in rows:
                # Convert values to strings for COPY
                str_values = []
                for val in row:
                    if val is None:
                        str_values.append(r"\N")  # NULL in COPY format
                    elif isinstance(val, bool):
                        str_values.append("t" if val else "f")
                    elif isinstance(val, (dict, list)):
                        str_values.append(json.dumps(val))
                    else:
                        # Escape special characters
                        s = str(val)
                        s = s.replace("\\", "\\\\")
                        s = s.replace("\t", "\\t")
                        s = s.replace("\n", "\\n")
                        s = s.replace("\r", "\\r")
                        str_values.append(s)
                
                copy.write_row(str_values)
        
        conn.commit()
        return len(rows)
    except Exception as e:
        logger.warning(f"COPY failed, falling back to INSERT: {e}")
        conn.rollback()
        return write_rows_insert(conn, table, columns, rows, schema)


def write_rows(
    conn,
    table: str,
    columns: List[str],
    rows: Sequence[Tuple[Any, ...]],
    schema: Optional[str] = None,
    on_conflict: Optional[str] = None,
) -> int:
    """
    Automatically select best write strategy based on row count.
    
    Decision rule (V22-007):
    - rows <= 500: Multi-row INSERT
    - rows > 500: COPY FROM STDIN (Postgres) or executemany (SQLite)
    
    Args:
        conn: Database connection
        table: Table name
        columns: Column names
        rows: Sequence of tuples
        schema: Schema name for Postgres
        on_conflict: Conflict handling ("ignore" or "update")
    
    Returns:
        Number of rows written
    """
    if not rows:
        return 0
    
    row_count = len(rows)
    
    if row_count <= BATCH_THRESHOLD:
        logger.debug(f"write_rows: Using INSERT for {row_count} rows to {table}")
        return write_rows_insert(conn, table, columns, rows, schema, on_conflict)
    else:
        # COPY doesn't support on_conflict, so if we need conflict handling,
        # we must use a different approach
        if on_conflict:
            # For upsert with many rows, use temp table + COPY + merge
            logger.debug(f"write_rows: Using INSERT with conflict for {row_count} rows to {table}")
            return write_rows_insert(conn, table, columns, rows, schema, on_conflict)
        else:
            logger.debug(f"write_rows: Using COPY for {row_count} rows to {table}")
            return write_rows_copy(conn, table, columns, rows, schema)


def write_rows_upsert(
    conn,
    table: str,
    columns: List[str],
    rows: Sequence[Tuple[Any, ...]],
    conflict_columns: List[str],
    schema: Optional[str] = None,
) -> int:
    """
    Write rows with upsert semantics (INSERT ON CONFLICT DO UPDATE).
    
    For large datasets (> BATCH_THRESHOLD), uses temp table + COPY + merge.
    
    Args:
        conn: Database connection
        table: Table name
        columns: All column names
        rows: Sequence of tuples
        conflict_columns: Columns that form the unique constraint
        schema: Schema name for Postgres
    
    Returns:
        Number of rows affected
    """
    if not rows:
        return 0
    
    engine = get_engine_type()
    is_postgres = engine == "postgres"
    row_count = len(rows)
    
    if row_count <= BATCH_THRESHOLD or not is_postgres:
        # Use standard multi-row INSERT with ON CONFLICT
        full_table = f"{schema}.{table}" if schema and is_postgres else table
        col_list = ", ".join(columns)
        conflict_list = ", ".join(conflict_columns)
        update_cols = [c for c in columns if c not in conflict_columns]
        update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
        
        if is_postgres:
            placeholder_row = f"({', '.join(['%s'] * len(columns))})"
            placeholders = ", ".join([placeholder_row] * len(rows))
            sql = f"""
                INSERT INTO {full_table} ({col_list}) 
                VALUES {placeholders}
                ON CONFLICT ({conflict_list}) DO UPDATE SET {update_set}
            """
            flat_values = tuple(val for row in rows for val in row)
            cur = conn.cursor()
            cur.execute(sql, flat_values)
            conn.commit()
            return cur.rowcount
        else:
            # SQLite: INSERT OR REPLACE
            placeholder_row = f"({', '.join(['?'] * len(columns))})"
            sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES {placeholder_row}"
            cur = conn.cursor()
            cur.executemany(sql, rows)
            conn.commit()
            return len(rows)
    
    # Large upsert in Postgres: Use temp table + COPY + merge
    full_table = f"{schema}.{table}" if schema else table
    temp_table = f"_bulk_upsert_{table}"
    col_list = ", ".join(columns)
    conflict_list = ", ".join(conflict_columns)
    update_cols = [c for c in columns if c not in conflict_columns]
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    
    cur = conn.cursor()
    
    try:
        # 1. Create temp table (inherits structure)
        cur.execute(f"""
            CREATE TEMP TABLE {temp_table} (LIKE {full_table} INCLUDING ALL)
            ON COMMIT DROP
        """)
        
        # 2. COPY into temp table
        copy_sql = f"COPY {temp_table} ({col_list}) FROM STDIN"
        with cur.copy(copy_sql) as copy:
            for row in rows:
                str_values = []
                for val in row:
                    if val is None:
                        str_values.append(r"\N")
                    elif isinstance(val, bool):
                        str_values.append("t" if val else "f")
                    elif isinstance(val, (dict, list)):
                        str_values.append(json.dumps(val))
                    else:
                        s = str(val)
                        s = s.replace("\\", "\\\\")
                        s = s.replace("\t", "\\t")
                        s = s.replace("\n", "\\n")
                        s = s.replace("\r", "\\r")
                        str_values.append(s)
                copy.write_row(str_values)
        
        # 3. Merge into target table
        cur.execute(f"""
            INSERT INTO {full_table} ({col_list})
            SELECT {col_list} FROM {temp_table}
            ON CONFLICT ({conflict_list}) DO UPDATE SET {update_set}
        """)
        
        conn.commit()
        return len(rows)
    except Exception as e:
        logger.error(f"Bulk upsert failed: {e}")
        conn.rollback()
        raise


# Metrics for benchmarking
_bulk_write_stats = {
    "insert_calls": 0,
    "insert_rows": 0,
    "copy_calls": 0,
    "copy_rows": 0,
}


def get_bulk_write_stats() -> dict:
    """Get bulk write statistics for benchmarking."""
    return _bulk_write_stats.copy()


def reset_bulk_write_stats():
    """Reset bulk write statistics."""
    global _bulk_write_stats
    _bulk_write_stats = {
        "insert_calls": 0,
        "insert_rows": 0,
        "copy_calls": 0,
        "copy_rows": 0,
    }

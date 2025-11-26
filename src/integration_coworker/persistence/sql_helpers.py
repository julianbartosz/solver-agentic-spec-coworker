"""
SQL Helpers for Database-Agnostic Operations.

Provides utilities to generate correct SQL syntax for both PostgreSQL and SQLite,
avoiding the need to hardcode engine-specific syntax in persist nodes.

Per design doc Section 4.3:
- PRIMARY PATH: Postgres 15 with %s placeholders and ON CONFLICT
- FALLBACK: SQLite with ? placeholders and INSERT OR IGNORE/REPLACE

Note: Postgres connections use dict_row factory, so rows are dicts.
      SQLite connections use Row factory, so rows are tuple-like.
      Use get_row_value() to access row values consistently.
"""
from typing import Any, List, Tuple, Optional, Union
from ..config import get_settings


def get_engine_type() -> str:
    """Get current database engine type: 'postgres' or 'sqlite'."""
    settings = get_settings()
    return settings.database.engine_type


def get_row_value(row: Any, column: Union[str, int]) -> Any:
    """
    Get a value from a row returned by cursor.fetchone().
    
    Handles both:
    - Postgres dict rows: {'id': 1, 'name': 'foo'}
    - SQLite tuple rows: (1, 'foo')
    
    Args:
        row: The row returned by fetchone()
        column: Column name (str) or index (int)
        
    Returns:
        The value at the specified column
    """
    if row is None:
        return None
    
    if isinstance(row, dict):
        # Postgres dict_row
        if isinstance(column, int):
            # Access by index - need to get keys in order
            keys = list(row.keys())
            if column < len(keys):
                return row[keys[column]]
            raise IndexError(f"Column index {column} out of range")
        return row.get(column)
    else:
        # SQLite Row or tuple
        if isinstance(column, str):
            # Access by name if the row supports it
            if hasattr(row, 'keys'):
                return row[column]
            raise ValueError(f"Cannot access by name on tuple row: {column}")
        return row[column]


def placeholder(count: int = 1) -> str:
    """
    Get placeholder string for parameterized queries.
    
    Args:
        count: Number of placeholders needed
        
    Returns:
        Postgres: '%s, %s, %s' (for count=3)
        SQLite: '?, ?, ?' (for count=3)
    """
    engine = get_engine_type()
    ph = "%s" if engine == "postgres" else "?"
    return ", ".join([ph] * count)


def upsert_ignore(
    table: str,
    columns: List[str],
    conflict_columns: Optional[List[str]] = None,
    schema: Optional[str] = None
) -> str:
    """
    Generate INSERT ... ON CONFLICT DO NOTHING (Postgres) or INSERT OR IGNORE (SQLite).
    
    Args:
        table: Table name
        columns: List of column names to insert
        conflict_columns: Columns that define uniqueness (for Postgres ON CONFLICT)
        schema: Schema name (for Postgres, e.g., 'spec_silver')
        
    Returns:
        Complete INSERT statement
    """
    engine = get_engine_type()
    full_table = f"{schema}.{table}" if schema and engine == "postgres" else table
    col_list = ", ".join(columns)
    placeholders = placeholder(len(columns))
    
    if engine == "postgres":
        if conflict_columns:
            conflict_list = ", ".join(conflict_columns)
            return f"INSERT INTO {full_table} ({col_list}) VALUES ({placeholders}) ON CONFLICT ({conflict_list}) DO NOTHING"
        else:
            return f"INSERT INTO {full_table} ({col_list}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    else:
        return f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"


def upsert_update(
    table: str,
    columns: List[str],
    conflict_columns: List[str],
    update_columns: List[str],
    schema: Optional[str] = None
) -> str:
    """
    Generate INSERT ... ON CONFLICT DO UPDATE (Postgres) or INSERT OR REPLACE (SQLite).
    
    Args:
        table: Table name
        columns: List of column names to insert
        conflict_columns: Columns that define uniqueness
        update_columns: Columns to update on conflict
        schema: Schema name (for Postgres)
        
    Returns:
        Complete INSERT statement with upsert behavior
    """
    engine = get_engine_type()
    full_table = f"{schema}.{table}" if schema and engine == "postgres" else table
    col_list = ", ".join(columns)
    placeholders = placeholder(len(columns))
    
    if engine == "postgres":
        conflict_list = ", ".join(conflict_columns)
        update_clauses = ", ".join([f"{col} = EXCLUDED.{col}" for col in update_columns])
        return f"INSERT INTO {full_table} ({col_list}) VALUES ({placeholders}) ON CONFLICT ({conflict_list}) DO UPDATE SET {update_clauses}"
    else:
        # SQLite INSERT OR REPLACE replaces entire row on conflict
        return f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"


def select_by_columns(
    table: str,
    select_columns: List[str],
    where_columns: List[str],
    schema: Optional[str] = None
) -> str:
    """
    Generate SELECT statement with WHERE clause.
    
    Args:
        table: Table name
        select_columns: Columns to select
        where_columns: Columns for WHERE clause
        schema: Schema name (for Postgres)
        
    Returns:
        Complete SELECT statement
    """
    engine = get_engine_type()
    full_table = f"{schema}.{table}" if schema and engine == "postgres" else table
    col_list = ", ".join(select_columns)
    
    ph = "%s" if engine == "postgres" else "?"
    where_clauses = " AND ".join([f"{col} = {ph}" for col in where_columns])
    
    return f"SELECT {col_list} FROM {full_table} WHERE {where_clauses}"


def table_name(table: str, schema: Optional[str] = None) -> str:
    """
    Get fully qualified table name.
    
    Args:
        table: Base table name
        schema: Schema name (for Postgres)
        
    Returns:
        Postgres: 'schema.table'
        SQLite: 'table'
    """
    engine = get_engine_type()
    if schema and engine == "postgres":
        return f"{schema}.{table}"
    return table


def datetime_now() -> str:
    """
    Get SQL expression for current timestamp.
    
    Returns:
        Postgres: "NOW()"
        SQLite: "datetime('now')"
    """
    engine = get_engine_type()
    return "NOW()" if engine == "postgres" else "datetime('now')"

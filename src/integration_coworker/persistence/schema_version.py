"""
Schema version tracking for migration safety.

This module implements Option B from the migration strategy decision:
- Keep idempotent DDL strings
- Add schema_version table
- Implement drift detection that fails fast
- STRUCTURAL VERIFICATION: verify tables/columns exist with expected types

The approach:
1. Each schema change increments EXPECTED_SCHEMA_VERSION
2. init_schema() checks DB version against expected
3. If DB is behind, DDL runs (idempotent - safe to re-run)
4. If DB is ahead, fail fast (indicates drift)
5. STRUCTURAL CHECK: verify required tables/columns exist

Documented failure modes (accepted limitations):
- Column TYPE changes: IF EXISTS DDL doesn't enforce type changes on existing columns.
  Resolution: Manual ALTER COLUMN or drop/recreate table.
- Column RENAMES: IF EXISTS can't detect renamed columns.
  Resolution: Manual migration script.
- Constraint changes: May silently fail if data violates new constraint.
  Resolution: Fix data first, then add constraint.
- False confidence: IF NOT EXISTS succeeds even if schema is corrupted.
  Mitigation: Structural verification catches missing tables/columns.

Run: pytest tests/test_schema_version.py -v
"""
import logging
from typing import List, Optional, Tuple, Dict, Set

logger = logging.getLogger(__name__)

# Increment this when changing schema
# Current version reflects: File Integration V1 (Bucket 1 complete)
EXPECTED_SCHEMA_VERSION = 1

# Schema version history:
# 0 - Initial schema (pre-file integration)
# 1 - File Integration V1: file_specs, file_fields, record_layouts, file_validation_rules


# =============================================================================
# STRUCTURAL SCHEMA REQUIREMENTS
# =============================================================================
# These define the MINIMUM required tables and columns for each version.
# Used for structural verification beyond just version number checking.

# Format: {table_name: {column_name: expected_type}}
# Types are normalized (e.g., "bigint", "text", "boolean")
REQUIRED_SCHEMA_V1_POSTGRES: Dict[str, Dict[str, str]] = {
    "spec_silver.source_systems": {
        "id": "bigint",
        "code": "text",
        "display_name": "text",
    },
    "spec_silver.file_specs": {
        "id": "bigint",
        "source_system_id": "bigint",
        "name": "text",
        "file_type": "text",
        "delimiter": "text",
        "encoding": "text",
        "header_row": "boolean",
    },
    "spec_silver.file_fields": {
        "id": "bigint",
        "file_spec_id": "bigint",
        "name": "text",
        "field_type": "text",
        "position": "integer",
    },
    "spec_silver.record_layouts": {
        "id": "bigint",
        "file_spec_id": "bigint",
        "record_type": "text",
    },
    "spec_silver.file_validation_rules": {
        "id": "bigint",
        "file_spec_id": "bigint",
        "rule_type": "text",
        "rule_config": "jsonb",
    },
}

# SQLite equivalent (uses TEXT, INTEGER)
REQUIRED_SCHEMA_V1_SQLITE: Dict[str, Dict[str, str]] = {
    "source_systems": {
        "id": "integer",
        "code": "text",
        "display_name": "text",
    },
    "file_specs": {
        "id": "integer",
        "source_system_id": "integer",
        "name": "text",
        "file_type": "text",
    },
    "file_fields": {
        "id": "integer",
        "file_spec_id": "integer",
        "name": "text",
        "field_type": "text",
        "position": "integer",
    },
    "record_layouts": {
        "id": "integer",
        "file_spec_id": "integer",
        "record_type": "text",
    },
    "file_validation_rules": {
        "id": "integer",
        "file_spec_id": "integer",
        "rule_type": "text",
        "rule_config": "text",
    },
}


SCHEMA_VERSION_DDL_POSTGRES = """
-- Schema version tracking for drift detection
CREATE TABLE IF NOT EXISTS spec_silver.schema_version (
    version INT NOT NULL,
    description TEXT,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (version)
);

-- Insert initial version if not exists
INSERT INTO spec_silver.schema_version (version, description)
VALUES (0, 'Initial schema')
ON CONFLICT (version) DO NOTHING;
"""

SCHEMA_VERSION_DDL_SQLITE = """
-- Schema version tracking for drift detection
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL PRIMARY KEY,
    description TEXT,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert initial version if not exists (SQLite doesn't have ON CONFLICT for non-unique)
INSERT OR IGNORE INTO schema_version (version, description)
VALUES (0, 'Initial schema');
"""


def get_current_schema_version_postgres(conn) -> int:
    """Get current schema version from Postgres."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT MAX(version) FROM spec_silver.schema_version")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 0
    except Exception:
        # Table doesn't exist yet
        return -1


def get_current_schema_version_sqlite(conn) -> int:
    """Get current schema version from SQLite."""
    cur = conn.cursor()
    try:
        cur.execute("SELECT MAX(version) FROM schema_version")
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 0
    except Exception:
        # Table doesn't exist yet
        return -1


def update_schema_version_postgres(conn, version: int, description: str) -> None:
    """Record a schema version update in Postgres."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO spec_silver.schema_version (version, description)
        VALUES (%s, %s)
        ON CONFLICT (version) DO UPDATE SET description = EXCLUDED.description
    """, (version, description))
    conn.commit()


def update_schema_version_sqlite(conn, version: int, description: str) -> None:
    """Record a schema version update in SQLite."""
    cur = conn.cursor()
    cur.execute("""
        INSERT OR REPLACE INTO schema_version (version, description)
        VALUES (?, ?)
    """, (version, description))
    conn.commit()


def check_schema_drift(current_version: int, expected_version: int) -> Tuple[bool, str]:
    """
    Check for schema drift.
    
    Returns:
        (is_ok, message) tuple
        
    Failure modes:
    - DB behind expected: OK (will upgrade)
    - DB matches expected: OK
    - DB ahead of expected: FAIL (drift detected)
    """
    if current_version < 0:
        return True, "Schema version table not found, will be created"
    
    if current_version < expected_version:
        return True, f"Schema will be upgraded from v{current_version} to v{expected_version}"
    
    if current_version == expected_version:
        return True, f"Schema is at expected version {expected_version}"
    
    # current_version > expected_version
    return False, (
        f"SCHEMA DRIFT DETECTED: Database is at v{current_version} "
        f"but code expects v{expected_version}. "
        "This may indicate: "
        "(1) Running older code against newer DB, or "
        "(2) Manual schema changes. "
        "Resolution: Update code to match DB version or migrate DB."
    )


# =============================================================================
# STRUCTURAL VERIFICATION
# =============================================================================

def verify_postgres_structure(conn, version: int = 1) -> List[str]:
    """
    Verify Postgres schema structure matches expected requirements.
    
    Returns list of violations (empty if valid).
    This catches issues that version number alone can't detect:
    - Missing tables (DDL failed silently)
    - Missing columns (column dropped manually)
    - Wrong column types (type changed manually)
    """
    violations = []
    
    if version >= 1:
        required = REQUIRED_SCHEMA_V1_POSTGRES
    else:
        return violations  # V0 has no structural requirements
    
    cur = conn.cursor()
    
    for table_full, columns in required.items():
        # Split schema.table
        if "." in table_full:
            schema, table = table_full.split(".", 1)
        else:
            schema, table = "public", table_full
        
        # Check table exists
        cur.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = %s AND table_name = %s
            )
        """, (schema, table))
        exists = cur.fetchone()[0]
        
        if not exists:
            violations.append(f"Table {table_full} does not exist")
            continue
        
        # Check columns exist with expected types
        cur.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
        """, (schema, table))
        actual_columns = {row[0]: row[1].lower() for row in cur.fetchall()}
        
        for col_name, expected_type in columns.items():
            if col_name not in actual_columns:
                violations.append(f"Column {table_full}.{col_name} does not exist")
            elif not _type_matches(actual_columns[col_name], expected_type):
                violations.append(
                    f"Column {table_full}.{col_name} has type '{actual_columns[col_name]}' "
                    f"but expected '{expected_type}'"
                )
    
    return violations


def verify_sqlite_structure(conn, version: int = 1) -> List[str]:
    """
    Verify SQLite schema structure matches expected requirements.
    
    Returns list of violations (empty if valid).
    """
    violations = []
    
    if version >= 1:
        required = REQUIRED_SCHEMA_V1_SQLITE
    else:
        return violations
    
    cur = conn.cursor()
    
    for table, columns in required.items():
        # Check table exists
        cur.execute("""
            SELECT name FROM sqlite_master 
            WHERE type='table' AND name=?
        """, (table,))
        if not cur.fetchone():
            violations.append(f"Table {table} does not exist")
            continue
        
        # Get actual columns via PRAGMA
        cur.execute(f"PRAGMA table_info({table})")
        actual_columns = {row[1].lower(): row[2].lower() for row in cur.fetchall()}
        
        for col_name, expected_type in columns.items():
            col_lower = col_name.lower()
            if col_lower not in actual_columns:
                violations.append(f"Column {table}.{col_name} does not exist")
            elif not _type_matches(actual_columns[col_lower], expected_type, is_sqlite=True):
                violations.append(
                    f"Column {table}.{col_name} has type '{actual_columns[col_lower]}' "
                    f"but expected '{expected_type}'"
                )
    
    return violations


def _type_matches(actual: str, expected: str, is_sqlite: bool = False) -> bool:
    """
    Check if actual column type matches expected type.
    
    Handles type aliases and variations (e.g., "int8" == "bigint").
    """
    actual = actual.lower()
    expected = expected.lower()
    
    if actual == expected:
        return True
    
    # Postgres type aliases
    pg_aliases = {
        "int8": "bigint",
        "int4": "integer",
        "int2": "smallint",
        "bool": "boolean",
        "character varying": "text",
        "varchar": "text",
    }
    
    # SQLite uses affinity types
    sqlite_integer = {"int", "integer", "tinyint", "smallint", "mediumint", "bigint", "int2", "int8"}
    sqlite_text = {"text", "character", "varchar", "clob", "nchar", "nvarchar"}
    sqlite_real = {"real", "double", "float", "numeric", "decimal"}
    
    if is_sqlite:
        if expected in ("integer", "int") and actual in sqlite_integer:
            return True
        if expected == "text" and actual in sqlite_text:
            return True
        if expected in ("real", "decimal") and actual in sqlite_real:
            return True
    else:
        # Postgres
        actual_normalized = pg_aliases.get(actual, actual)
        expected_normalized = pg_aliases.get(expected, expected)
        if actual_normalized == expected_normalized:
            return True
    
    return False


def validate_schema_version(engine_type: str, conn) -> bool:
    """
    Validate schema version and fail fast on drift.
    
    Args:
        engine_type: "postgres" or "sqlite"
        conn: Database connection
        
    Returns:
        True if schema is valid or upgradeable, False on drift
        
    Raises:
        RuntimeError on critical drift
    """
    if engine_type == "postgres":
        current = get_current_schema_version_postgres(conn)
    else:
        current = get_current_schema_version_sqlite(conn)
    
    is_ok, message = check_schema_drift(current, EXPECTED_SCHEMA_VERSION)
    
    if is_ok:
        logger.info(f"Schema check: {message}")
        return True
    else:
        logger.error(f"Schema check FAILED: {message}")
        raise RuntimeError(message)


def validate_schema_structure(engine_type: str, conn, version: int = None) -> List[str]:
    """
    Validate schema STRUCTURE (tables, columns, types).
    
    This goes beyond version number checking to verify the actual
    schema matches expectations. Use after init_schema() to catch:
    - Failed DDL due to existing conflicts
    - Manual schema modifications
    - Corrupted migrations
    
    Args:
        engine_type: "postgres" or "sqlite"
        conn: Database connection
        version: Schema version to check against (default: EXPECTED_SCHEMA_VERSION)
        
    Returns:
        List of violations (empty if valid)
    """
    if version is None:
        version = EXPECTED_SCHEMA_VERSION
    
    if engine_type == "postgres":
        return verify_postgres_structure(conn, version)
    else:
        return verify_sqlite_structure(conn, version)


# Version descriptions for tracking
VERSION_DESCRIPTIONS = {
    0: "Initial schema",
    1: "File Integration V1: file_specs, file_fields, record_layouts, file_validation_rules",
}


def get_version_description(version: int) -> str:
    """Get description for a schema version."""
    return VERSION_DESCRIPTIONS.get(version, f"Version {version}")

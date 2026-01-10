"""
Database migration runner for PostgreSQL.

Provides forward-only migrations using numbered SQL files.
Each migration runs in its own transaction - on failure, that migration
is rolled back and the process stops.

Usage:
    from integration_coworker.persistence.migrations import run_pending_migrations
    
    with get_connection() as conn:
        run_pending_migrations(conn)

Schema migrations table:
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        checksum TEXT NOT NULL
    );
"""
import hashlib
import logging
import re
from pathlib import Path
from typing import List, Tuple, Optional, NamedTuple, Set
from datetime import datetime

logger = logging.getLogger(__name__)

# Track checksums we've already warned about (per-session dedup)
_warned_checksums: Set[str] = set()

# Migrations directory relative to this file
MIGRATIONS_DIR = Path(__file__).parent.parent.parent.parent / "migrations"


class MigrationInfo(NamedTuple):
    """Information about a migration file."""
    version: str  # e.g., "001"
    name: str  # e.g., "baseline_v1"
    path: Path  # Full path to .sql file
    checksum: str  # SHA256 of file content


class MigrationStatus(NamedTuple):
    """Status of a migration."""
    version: str
    name: str
    applied: bool
    applied_at: Optional[datetime]
    checksum_match: bool  # True if file checksum matches DB record


def _compute_checksum(content: str) -> str:
    """Compute SHA256 checksum of migration content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _parse_migration_filename(filename: str) -> Optional[Tuple[str, str]]:
    """
    Parse migration filename into (version, name).
    
    Expected format: NNN_description.sql (e.g., 001_baseline_v1.sql)
    Returns None if filename doesn't match pattern.
    """
    match = re.match(r"^(\d{3})_(.+)\.sql$", filename)
    if match:
        return match.group(1), match.group(2)
    return None


def list_migration_files() -> List[MigrationInfo]:
    """
    List all migration files in the migrations directory, sorted by version.
    
    Returns:
        List of MigrationInfo tuples sorted by version number.
    """
    if not MIGRATIONS_DIR.exists():
        logger.warning(f"Migrations directory not found: {MIGRATIONS_DIR}")
        return []
    
    migrations = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        parsed = _parse_migration_filename(path.name)
        if parsed:
            version, name = parsed
            content = path.read_text(encoding="utf-8")
            checksum = _compute_checksum(content)
            migrations.append(MigrationInfo(version, name, path, checksum))
        else:
            logger.debug(f"Skipping non-migration file: {path.name}")
    
    return migrations


def _ensure_migrations_table(conn) -> None:
    """Create schema_migrations table if it doesn't exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                checksum TEXT NOT NULL
            )
        """)
    conn.commit()


def _get_applied_migrations(conn) -> dict:
    """
    Get dict of applied migrations: {version: (applied_at, checksum)}.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT version, applied_at, checksum 
            FROM schema_migrations 
            ORDER BY version
        """)
        return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def _record_migration(conn, migration: MigrationInfo) -> None:
    """Record a migration as applied."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO schema_migrations (version, name, checksum)
            VALUES (%s, %s, %s)
            ON CONFLICT (version) DO UPDATE SET
                checksum = EXCLUDED.checksum,
                applied_at = NOW()
            """,
            (migration.version, migration.name, migration.checksum)
        )


def run_pending_migrations(conn, *, sync_checksums: bool = False) -> Tuple[int, int]:
    """
    Run all pending migrations in order.
    
    Each migration runs in its own transaction. On failure, that migration
    is rolled back and the function raises an exception.
    
    Args:
        conn: Database connection (psycopg or compatible)
        sync_checksums: V31-004 - If True, update checksums for already-applied
                        migrations to match current file contents. This silences
                        checksum mismatch warnings for cosmetic changes.
        
    Returns:
        Tuple of (applied_count, skipped_count)
        
    Raises:
        RuntimeError: If a migration fails to apply
    """
    _ensure_migrations_table(conn)
    
    # V31-004: Optionally sync checksums before checking for pending migrations
    if sync_checksums:
        sync_migration_checksums(conn)
    
    migrations = list_migration_files()
    if not migrations:
        logger.info("No migration files found")
        return 0, 0
    
    applied = _get_applied_migrations(conn)
    
    applied_count = 0
    skipped_count = 0
    
    for migration in migrations:
        if migration.version in applied:
            existing_checksum = applied[migration.version][1]
            if existing_checksum != migration.checksum:
                # V22-009 Fix: Only warn once per session to reduce log spam
                warn_key = f"{migration.version}:{existing_checksum}"
                if warn_key not in _warned_checksums:
                    _warned_checksums.add(warn_key)
                    logger.warning(
                        f"Migration {migration.version}_{migration.name} checksum mismatch: "
                        f"file={migration.checksum}, db={existing_checksum}. "
                        f"Migration file may have been modified after application. "
                        f"(This warning appears once per session)"
                    )
            skipped_count += 1
            continue
        
        # Apply migration
        logger.info(f"Applying migration {migration.version}_{migration.name}...")
        content = migration.path.read_text(encoding="utf-8")
        
        try:
            # Each migration in its own transaction
            with conn.cursor() as cur:
                cur.execute(content)
            
            _record_migration(conn, migration)
            conn.commit()
            
            logger.info(f"Migration {migration.version}_{migration.name} applied successfully")
            applied_count += 1
            
        except Exception as e:
            conn.rollback()
            logger.error(f"Migration {migration.version}_{migration.name} failed: {e}")
            raise RuntimeError(
                f"Migration {migration.version}_{migration.name} failed: {e}"
            ) from e
    
    return applied_count, skipped_count


def get_migration_status(conn) -> List[MigrationStatus]:
    """
    Get status of all migrations.
    
    Returns:
        List of MigrationStatus for each migration file.
    """
    _ensure_migrations_table(conn)
    
    migrations = list_migration_files()
    applied = _get_applied_migrations(conn)
    
    result = []
    for migration in migrations:
        if migration.version in applied:
            applied_at, db_checksum = applied[migration.version]
            result.append(MigrationStatus(
                version=migration.version,
                name=migration.name,
                applied=True,
                applied_at=applied_at,
                checksum_match=(db_checksum == migration.checksum)
            ))
        else:
            result.append(MigrationStatus(
                version=migration.version,
                name=migration.name,
                applied=False,
                applied_at=None,
                checksum_match=True  # N/A for unapplied
            ))
    
    return result


def get_current_version(conn) -> Optional[str]:
    """
    Get the highest applied migration version.
    
    Returns:
        Version string (e.g., "001") or None if no migrations applied.
    """
    _ensure_migrations_table(conn)
    
    with conn.cursor() as cur:
        cur.execute("SELECT MAX(version) FROM schema_migrations")
        row = cur.fetchone()
        return row[0] if row else None


def sync_migration_checksums(conn) -> int:
    """
    V31-004 FIX: Update checksums for all applied migrations to match current file contents.
    
    This is useful when migration files are modified after initial application
    (e.g., formatting changes, comments, or harmless SQL tweaks) and you want
    to silence the checksum mismatch warnings.
    
    WARNING: Only use this if you're certain the migration changes are safe
    and have already been applied to all environments!
    
    Args:
        conn: Database connection
        
    Returns:
        Number of checksums updated
    """
    _ensure_migrations_table(conn)
    
    migrations = list_migration_files()
    applied = _get_applied_migrations(conn)
    updated_count = 0
    
    for migration in migrations:
        if migration.version in applied:
            _, db_checksum = applied[migration.version]
            if db_checksum != migration.checksum:
                logger.info(
                    f"[V31-004] Updating checksum for {migration.version}_{migration.name}: "
                    f"{db_checksum} -> {migration.checksum}"
                )
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE schema_migrations SET checksum = %s WHERE version = %s",
                        (migration.checksum, migration.version)
                    )
                updated_count += 1
    
    if updated_count > 0:
        conn.commit()
        logger.info(f"[V31-004] Updated {updated_count} migration checksum(s)")
    
    return updated_count

#!/usr/bin/env python3
"""
Database initialization script.

Initializes the Postgres or SQLite schema for the Integration Co-Worker.

Usage:
    PYTHONPATH=src python scripts/init_db.py

Environment Variables:
    DATABASE_URL - Postgres connection string (for Postgres mode)
    USE_SQLITE=true - Use SQLite instead of Postgres
"""
import os
import sys

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from integration_coworker.config import get_settings, reset_settings
from integration_coworker.persistence.db import init_schema, get_engine_type


def main():
    """Initialize the database schema."""
    # Reset settings to pick up fresh environment variables
    reset_settings()
    settings = get_settings()
    engine = get_engine_type()
    
    print("Integration Co-Worker Database Initialization")
    print("=" * 50)
    
    if engine == "sqlite":
        print(f"Mode: SQLite")
        print(f"Path: {settings.database.sqlite_path}")
    else:
        # Mask password for display
        display_url = settings.database.url
        if "@" in display_url and ":" in display_url.split("@")[0]:
            parts = display_url.split("@")
            user_pass = parts[0].split("://")[1]
            if ":" in user_pass:
                user = user_pass.split(":")[0]
                display_url = f"postgresql://{user}:***@{parts[1]}"
        print(f"Mode: PostgreSQL + pgvector")
        print(f"URL: {display_url}")
    
    print()
    print("Initializing schema...")
    
    try:
        init_schema()
        print("✓ Schema initialized successfully!")
        
        # For Postgres, also check pgvector
        if engine == "postgres":
            try:
                from integration_coworker.persistence.postgres import check_pgvector
                if check_pgvector():
                    print("✓ pgvector extension is available")
                else:
                    print("⚠ pgvector extension not found - embeddings will not work")
                    print("  Run: CREATE EXTENSION IF NOT EXISTS vector;")
            except Exception as e:
                print(f"⚠ Could not check pgvector: {e}")
        
    except RuntimeError as e:
        print(f"✗ Error: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"✗ Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

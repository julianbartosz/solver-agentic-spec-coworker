"""
Test spec_documents upsert behavior after migration 002_remove_uri_unique.sql.

Validates that:
1. Same URI with different source_system_id succeeds (multi-provider support)
2. Same (source_system_id, sha256) is properly deduplicated
3. Same URI re-run for same provider with same content is deduplicated
"""
import os
import tempfile
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def sqlite_db():
    """Create temporary SQLite database with spec_documents table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Create source_systems table
    cur.execute("""
        CREATE TABLE IF NOT EXISTS source_systems (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            display_name TEXT
        )
    """)
    
    # Create spec_documents table WITHOUT UNIQUE(uri) - matches migration 002
    cur.execute("""
        CREATE TABLE IF NOT EXISTS spec_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER NOT NULL,
            version TEXT DEFAULT '1.0',
            uri TEXT NOT NULL,
            content_type TEXT,
            sha256 TEXT NOT NULL,
            FOREIGN KEY (source_system_id) REFERENCES source_systems(id),
            UNIQUE(source_system_id, sha256)
        )
    """)
    
    # Insert test source systems
    cur.execute("INSERT INTO source_systems (code, display_name) VALUES ('stripe', 'Stripe')")
    cur.execute("INSERT INTO source_systems (code, display_name) VALUES ('twilio', 'Twilio')")
    conn.commit()
    
    yield conn
    
    conn.close()
    os.unlink(db_path)


class TestSpecDocumentsUpsert:
    """Tests for spec_documents upsert after UNIQUE(uri) removal."""
    
    def test_same_uri_different_provider_succeeds(self, sqlite_db):
        """
        Same URI can be used by different providers.
        
        This is the key fix for P0.1 - previously failed with:
        "duplicate key value violates unique constraint spec_documents_uri_key"
        """
        cur = sqlite_db.cursor()
        
        # Insert same URI for different providers
        uri = "https://api.example.com/openapi.yaml"
        sha256_stripe = "abc123"
        sha256_twilio = "def456"
        
        # First insert: Stripe
        cur.execute("""
            INSERT INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (1, ?, ?, 'application/yaml')
        """, (uri, sha256_stripe))
        
        # Second insert: Twilio (same URI, different provider)
        # This should succeed after removing UNIQUE(uri)
        cur.execute("""
            INSERT INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (2, ?, ?, 'application/yaml')
        """, (uri, sha256_twilio))
        
        sqlite_db.commit()
        
        # Verify both exist
        cur.execute("SELECT COUNT(*) FROM spec_documents WHERE uri = ?", (uri,))
        count = cur.fetchone()[0]
        assert count == 2, "Same URI should be allowed for different providers"
    
    def test_same_provider_same_content_deduplicated(self, sqlite_db):
        """
        Same provider with same content (sha256) is deduplicated.
        
        This is the correct behavior - (source_system_id, sha256) is the dedup key.
        """
        cur = sqlite_db.cursor()
        
        uri = "https://api.stripe.com/openapi.yaml"
        sha256 = "same_content_hash"
        
        # First insert
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (1, ?, ?, 'application/yaml')
        """, (uri, sha256))
        
        # Second insert with same (source_system_id, sha256) - should be ignored
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (1, ?, ?, 'application/yaml')
        """, (uri, sha256))
        
        sqlite_db.commit()
        
        # Verify only one exists
        cur.execute("SELECT COUNT(*) FROM spec_documents WHERE sha256 = ?", (sha256,))
        count = cur.fetchone()[0]
        assert count == 1, "Same content for same provider should be deduplicated"
    
    def test_same_provider_different_content_allowed(self, sqlite_db):
        """
        Same provider can have different versions of same spec (different sha256).
        """
        cur = sqlite_db.cursor()
        
        uri = "https://api.stripe.com/openapi.yaml"
        sha256_v1 = "version_1_hash"
        sha256_v2 = "version_2_hash"
        
        # Insert version 1
        cur.execute("""
            INSERT INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (1, ?, ?, 'application/yaml')
        """, (uri, sha256_v1))
        
        # Insert version 2 (same URI, different content)
        cur.execute("""
            INSERT INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (1, ?, ?, 'application/yaml')
        """, (uri, sha256_v2))
        
        sqlite_db.commit()
        
        # Verify both exist
        cur.execute("SELECT COUNT(*) FROM spec_documents WHERE source_system_id = 1")
        count = cur.fetchone()[0]
        assert count == 2, "Different content versions for same provider should be allowed"
    
    def test_multi_run_scenario(self, sqlite_db):
        """
        Simulate multiple demo runs with same spec.
        
        This is the exact failure scenario from P0.1:
        1. Run 1: Insert spec with URI A
        2. Run 2 (--fresh): Try to insert same spec with URI A
        3. Before fix: FAILS with duplicate key
        4. After fix: Succeeds (deduplicated by sha256 if same content)
        """
        cur = sqlite_db.cursor()
        
        # Run 1
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (1, 'https://api.stripe.com/spec', 'run1_hash', 'application/yaml')
        """)
        sqlite_db.commit()
        
        # Run 2 (same URI, same provider, but maybe different content)
        # This simulates --fresh re-fetch where spec may have changed
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (1, 'https://api.stripe.com/spec', 'run2_hash', 'application/yaml')
        """)
        sqlite_db.commit()
        
        # Verify both exist (different content)
        cur.execute("SELECT COUNT(*) FROM spec_documents WHERE source_system_id = 1")
        count = cur.fetchone()[0]
        assert count == 2, "Multiple runs with different content should create multiple records"
        
        # Run 3 (same content as Run 1)
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents (source_system_id, uri, sha256, content_type)
            VALUES (1, 'https://api.stripe.com/spec', 'run1_hash', 'application/yaml')
        """)
        sqlite_db.commit()
        
        # Verify still 2 (deduplicated by sha256)
        cur.execute("SELECT COUNT(*) FROM spec_documents WHERE source_system_id = 1")
        count = cur.fetchone()[0]
        assert count == 2, "Same content should be deduplicated by sha256"


class TestUpsertIgnorePattern:
    """Test the upsert_ignore SQL pattern used in persist nodes."""
    
    def test_upsert_ignore_with_correct_conflict_columns(self, sqlite_db):
        """
        Verify upsert_ignore pattern works with (source_system_id, sha256) conflict.
        
        This mirrors the actual usage in persist_silver_checkpoint.py:
        sql = upsert_ignore(
            "spec_documents",
            ["source_system_id", "uri", "sha256", "content_type"],
            ["source_system_id", "sha256"],  # <-- correct conflict columns
            schema
        )
        """
        cur = sqlite_db.cursor()
        
        # First insert
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents 
            (source_system_id, uri, sha256, content_type)
            VALUES (1, 'https://api.stripe.com/spec', 'hash1', 'application/yaml')
        """)
        
        # Upsert with same (source_system_id, sha256) - should be ignored
        cur.execute("""
            INSERT OR IGNORE INTO spec_documents 
            (source_system_id, uri, sha256, content_type)
            VALUES (1, 'https://api.stripe.com/spec', 'hash1', 'application/json')
        """)
        
        sqlite_db.commit()
        
        # Verify original content_type preserved (not updated)
        cur.execute("""
            SELECT content_type FROM spec_documents 
            WHERE source_system_id = 1 AND sha256 = 'hash1'
        """)
        content_type = cur.fetchone()[0]
        assert content_type == "application/yaml", "INSERT OR IGNORE should not update existing row"

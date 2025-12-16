"""
Tests for KG persistence module - idempotency and provenance edges.

These tests verify:
  1. Idempotent upsert behavior for nodes and edges
  2. DERIVES_FROM_GUIDE provenance edges
  3. HAS_FIELD hierarchical edges
  4. MAPS_TO field mapping edges
  5. Vector similarity search (requires pgvector)
"""

import json
import pytest
from unittest.mock import MagicMock, patch, call

from integration_coworker.kg.persist import (
    # Key builders
    build_file_spec_key,
    build_file_field_key,
    build_guide_field_key,
    build_record_layout_key,
    # Core upserts
    upsert_node,
    upsert_edge,
    get_node_id_by_key,
    # High-level functions
    persist_file_spec_to_kg,
    persist_file_field_to_kg,
    persist_field_mapping_to_kg,
    persist_file_spec_with_fields,
    _build_field_embedding_text,
    # Query functions
    find_similar_fields,
    get_field_mappings,
)
from integration_coworker.domain.models import (
    KGNodeType,
    KGEdgeRelation,
    FileSpec,
    FileField,
)


# ---------------------------------------------------------------------------
# Test Natural Key Builders
# ---------------------------------------------------------------------------

class TestNaturalKeyBuilders:
    """Test canonical key generation for KG nodes."""
    
    def test_build_file_spec_key_basic(self):
        """Basic FILE_SPEC key without sheet."""
        key = build_file_spec_key(1, "employee_data")
        assert key == "file_spec.1.employee_data"
    
    def test_build_file_spec_key_with_sheet(self):
        """FILE_SPEC key with Excel sheet name."""
        key = build_file_spec_key(42, "payroll", "Q1_2024")
        assert key == "file_spec.42.payroll.Q1_2024"
    
    def test_build_file_field_key(self):
        """FILE_FIELD key derived from spec key."""
        spec_key = "file_spec.1.employee_data"
        key = build_file_field_key(spec_key, "employee_id")
        assert key == "file_field.file_spec.1.employee_data.employee_id"
    
    def test_build_guide_field_key_deterministic(self):
        """GUIDE_FIELD key is deterministic for same URI."""
        uri = "https://example.com/data_guide.pdf"
        key1 = build_guide_field_key(uri, "account_number")
        key2 = build_guide_field_key(uri, "account_number")
        assert key1 == key2
        assert key1.startswith("guide_field.")
        assert key1.endswith(".account_number")
    
    def test_build_guide_field_key_different_uris(self):
        """Different URIs produce different keys."""
        key1 = build_guide_field_key("https://a.com/guide.pdf", "field")
        key2 = build_guide_field_key("https://b.com/guide.pdf", "field")
        assert key1 != key2
    
    def test_build_record_layout_key(self):
        """RECORD_LAYOUT key derived from spec key."""
        spec_key = "file_spec.1.transactions"
        key = build_record_layout_key(spec_key, "header")
        assert key == "record_layout.file_spec.1.transactions.header"


# ---------------------------------------------------------------------------
# Test Core Upsert Functions
# ---------------------------------------------------------------------------

class TestUpsertNode:
    """Test idempotent node upsert behavior."""
    
    def test_upsert_node_without_embedding(self):
        """Upsert node without embedding vector."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (123,)
        
        result = upsert_node(
            mock_conn,
            KGNodeType.FILE_SPEC,
            "file_spec.1.test",
            {"name": "test", "file_type": "csv"},
        )
        
        assert result == 123
        mock_cursor.execute.assert_called_once()
        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO kg.nodes" in sql
        assert "ON CONFLICT (node_type, key) DO UPDATE" in sql
        assert "embedding" not in sql.lower() or "COALESCE" not in sql  # No embedding clause
    
    def test_upsert_node_with_embedding(self):
        """Upsert node with embedding vector."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (456,)
        
        embedding = [0.1] * 1536  # 1536-dim vector
        result = upsert_node(
            mock_conn,
            KGNodeType.FILE_FIELD,
            "file_field.spec.field",
            {"name": "field"},
            embedding=embedding,
        )
        
        assert result == 456
        sql = mock_cursor.execute.call_args[0][0]
        assert "::vector" in sql
        assert "COALESCE(EXCLUDED.embedding" in sql
    
    def test_upsert_node_properties_merged(self):
        """Properties are merged with || operator."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)
        
        upsert_node(mock_conn, KGNodeType.FILE_SPEC, "key", {"a": 1})
        
        sql = mock_cursor.execute.call_args[0][0]
        assert "properties || EXCLUDED.properties" in sql


class TestUpsertEdge:
    """Test idempotent edge upsert behavior."""
    
    def test_upsert_edge_basic(self):
        """Basic edge upsert."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (789,)
        
        result = upsert_edge(
            mock_conn,
            src_node_id=1,
            dst_node_id=2,
            relation=KGEdgeRelation.HAS_FIELD,
        )
        
        assert result == 789
        sql = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO kg.edges" in sql
        assert "ON CONFLICT (src_node_id, dst_node_id, relation_type)" in sql
    
    def test_upsert_edge_weight_takes_max(self):
        """Weight uses GREATEST to keep highest."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)
        
        upsert_edge(mock_conn, 1, 2, KGEdgeRelation.MAPS_TO, weight=0.8)
        
        sql = mock_cursor.execute.call_args[0][0]
        assert "GREATEST(kg.edges.weight, EXCLUDED.weight)" in sql
    
    def test_upsert_edge_with_properties(self):
        """Edge properties are stored as JSON."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (1,)
        
        upsert_edge(
            mock_conn, 1, 2, 
            KGEdgeRelation.DERIVES_FROM_GUIDE,
            properties={"source_uri": "https://example.com"},
        )
        
        params = mock_cursor.execute.call_args[0][1]
        props_json = params[3]  # 4th param is properties
        assert "source_uri" in props_json


class TestGetNodeIdByKey:
    """Test node lookup by natural key."""
    
    def test_get_existing_node(self):
        """Found node returns ID."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (42,)
        
        result = get_node_id_by_key(mock_conn, KGNodeType.FILE_SPEC, "file_spec.1.test")
        
        assert result == 42
    
    def test_get_missing_node(self):
        """Missing node returns None."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = None
        
        result = get_node_id_by_key(mock_conn, KGNodeType.FILE_SPEC, "nonexistent")
        
        assert result is None


# ---------------------------------------------------------------------------
# Test High-Level Persistence Functions
# ---------------------------------------------------------------------------

class TestPersistFileSpecToKG:
    """Test FileSpec persistence with provenance."""
    
    def test_persist_without_guide(self):
        """Persist FILE_SPEC without guide URI."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (100,)
        
        file_spec = FileSpec(
            id=1,
            source_system_id=42,
            name="employee_data",
            file_type="csv",
            encoding="utf-8",
            delimiter=",",
            has_header=True,
        )
        
        result = persist_file_spec_to_kg(mock_conn, file_spec)
        
        assert result == 100
        # Only one call (no guide edge)
        assert mock_cursor.execute.call_count == 1
    
    def test_persist_with_guide_creates_edge(self):
        """Persist FILE_SPEC with guide URI creates DERIVES_FROM_GUIDE edge."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # Return different IDs for spec, guide, edge
        mock_cursor.fetchone.side_effect = [(100,), (200,), (300,)]
        
        file_spec = FileSpec(
            id=1,
            source_system_id=42,
            name="employee_data",
            file_type="csv",
        )
        
        result = persist_file_spec_to_kg(
            mock_conn, 
            file_spec, 
            guide_uri="https://example.com/data_guide.pdf"
        )
        
        assert result == 100
        # Three calls: spec node, guide node, edge
        assert mock_cursor.execute.call_count == 3
        
        # Verify edge creation
        edge_call = mock_cursor.execute.call_args_list[2]
        edge_sql = edge_call[0][0]
        edge_params = edge_call[0][1]
        assert "kg.edges" in edge_sql
        assert edge_params[2] == KGEdgeRelation.DERIVES_FROM_GUIDE.value


class TestPersistFileFieldToKG:
    """Test FileField persistence with HAS_FIELD edge."""
    
    def test_persist_field_creates_has_field_edge(self):
        """Persist FILE_FIELD creates HAS_FIELD edge to parent."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [(50,), (1,)]  # field node, edge
        
        field = FileField(
            id=1,
            file_spec_id=10,
            name="employee_id",
            field_type="integer",
            position=1,
            nullable=False,
        )
        
        result = persist_file_field_to_kg(
            mock_conn,
            field,
            file_spec_node_id=100,
            file_spec_key="file_spec.42.employee_data",
        )
        
        assert result == 50
        # Two calls: field node, has_field edge
        assert mock_cursor.execute.call_count == 2
        
        # Verify HAS_FIELD edge
        edge_call = mock_cursor.execute.call_args_list[1]
        edge_params = edge_call[0][1]
        assert edge_params[0] == 100  # src = spec
        assert edge_params[1] == 50   # dst = field
        assert edge_params[2] == KGEdgeRelation.HAS_FIELD.value


class TestPersistFieldMappingToKG:
    """Test MAPS_TO edge creation."""
    
    def test_persist_mapping_edge(self):
        """Create MAPS_TO edge with weight."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (999,)
        
        result = persist_field_mapping_to_kg(
            mock_conn,
            source_field_node_id=10,
            target_field_node_id=20,
            weight=0.85,
            mapping_source="vector_similarity",
        )
        
        assert result == 999
        
        params = mock_cursor.execute.call_args[0][1]
        assert params[0] == 10  # src
        assert params[1] == 20  # dst
        assert params[2] == KGEdgeRelation.MAPS_TO.value
        assert params[4] == 0.85  # weight


# ---------------------------------------------------------------------------
# Test Batch Operations
# ---------------------------------------------------------------------------

class TestPersistFileSpecWithFields:
    """Test batch persistence of spec + fields."""
    
    def test_persist_spec_with_fields_no_embeddings(self):
        """Persist spec and fields without computing embeddings."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        # IDs: spec=100, field1=201, field2=202, edges
        mock_cursor.fetchone.side_effect = [
            (100,),  # spec node
            (201,), (1,),  # field1 node, edge
            (202,), (2,),  # field2 node, edge
        ]
        
        file_spec = FileSpec(
            id=1,
            source_system_id=42,
            name="test_file",
            file_type="csv",
        )
        fields = [
            FileField(id=1, file_spec_id=1, name="col1", field_type="string", position=1),
            FileField(id=2, file_spec_id=1, name="col2", field_type="integer", position=2),
        ]
        
        spec_id, field_ids = persist_file_spec_with_fields(
            mock_conn, file_spec, fields, compute_embeddings=False
        )
        
        assert spec_id == 100
        assert field_ids == [201, 202]
        mock_conn.commit.assert_called_once()
    
    @patch('integration_coworker.kg._compute_embedding')
    def test_persist_spec_with_fields_with_embeddings(self, mock_embed):
        """Persist spec and fields with embeddings computed."""
        mock_embed.return_value = [0.1] * 1536
        
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.side_effect = [
            (100,),  # spec
            (201,), (1,),  # field1
        ]
        
        file_spec = FileSpec(id=1, source_system_id=42, name="test", file_type="csv")
        fields = [FileField(id=1, file_spec_id=1, name="col1", field_type="string", position=1)]
        
        persist_file_spec_with_fields(
            mock_conn, file_spec, fields, compute_embeddings=True
        )
        
        mock_embed.assert_called_once()


# ---------------------------------------------------------------------------
# Test Helper Functions
# ---------------------------------------------------------------------------

class TestBuildFieldEmbeddingText:
    """Test embedding text construction."""
    
    def test_minimal_field(self):
        """Field with only name."""
        field = FileField(id=1, file_spec_id=1, name="col1", field_type="string", position=1)
        text = _build_field_embedding_text(field)
        assert "col1" in text
    
    def test_field_with_description(self):
        """Field with description included."""
        field = FileField(
            id=1, file_spec_id=1, name="employee_id", field_type="integer", position=1,
            description="Unique identifier for employee"
        )
        text = _build_field_embedding_text(field)
        assert "employee_id" in text
        assert "Unique identifier" in text
    
    def test_field_with_samples(self):
        """Field with sample values (limited to 3)."""
        field = FileField(
            id=1, file_spec_id=1, name="status", position=1,
            field_type="string",
            sample_values=["active", "inactive", "pending", "archived", "deleted"]
        )
        text = _build_field_embedding_text(field)
        assert "active" in text
        assert "inactive" in text
        assert "pending" in text
        assert "archived" not in text  # Only first 3


# ---------------------------------------------------------------------------
# Test Query Functions
# ---------------------------------------------------------------------------

class TestFindSimilarFields:
    """Test vector similarity search."""
    
    def test_find_similar_fields_basic(self):
        """Basic similarity search."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            (1, "file_field.spec.col1", 0.95, {"name": "col1"}),
            (2, "file_field.spec.col2", 0.82, {"name": "col2"}),
        ]
        
        query_vec = [0.1] * 1536
        results = find_similar_fields(mock_conn, query_vec, limit=5, min_similarity=0.7)
        
        assert len(results) == 2
        assert results[0][2] == 0.95  # similarity
        assert results[1][3]["name"] == "col2"
    
    def test_find_similar_fields_empty(self):
        """No similar fields found."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []
        
        results = find_similar_fields(mock_conn, [0.1] * 1536)
        
        assert results == []


class TestGetFieldMappings:
    """Test MAPS_TO edge retrieval."""
    
    def test_get_field_mappings(self):
        """Get all mappings for a field."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = [
            (20, "guide_field.abc123.account_number", 0.92),
            (21, "file_field.other.acct_no", 0.78),
        ]
        
        results = get_field_mappings(mock_conn, field_node_id=10)
        
        assert len(results) == 2
        assert results[0][2] == 0.92
        assert "account_number" in results[0][1]


# ---------------------------------------------------------------------------
# Integration Test Markers (require postgres)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestKGPersistIntegration:
    """
    Integration tests for KG persistence (require PostgreSQL with pgvector).
    
    Run with: pytest -m integration tests/test_kg_provenance.py
    """
    
    @pytest.fixture
    def pg_connection(self):
        """Get PostgreSQL connection for integration tests."""
        import os
        if not os.getenv("DATABASE_URL"):
            pytest.skip("DATABASE_URL not set")
        
        from integration_coworker.persistence.db import get_connection
        with get_connection() as conn:
            yield conn
            conn.rollback()  # Cleanup
    
    def test_idempotent_node_upsert(self, pg_connection):
        """Verify node upsert is truly idempotent."""
        conn = pg_connection
        
        # First insert
        id1 = upsert_node(
            conn, KGNodeType.FILE_SPEC, 
            "test.idempotent.spec",
            {"name": "test", "version": "1"}
        )
        
        # Second insert with same key - should return same ID
        id2 = upsert_node(
            conn, KGNodeType.FILE_SPEC,
            "test.idempotent.spec",
            {"version": "2"}  # Different props
        )
        
        assert id1 == id2
        
        # Verify properties merged
        cur = conn.cursor()
        cur.execute("SELECT properties FROM kg.nodes WHERE id = %s", (id1,))
        props = cur.fetchone()[0]
        assert props["name"] == "test"
        assert props["version"] == "2"
    
    def test_idempotent_edge_upsert(self, pg_connection):
        """Verify edge upsert is truly idempotent."""
        conn = pg_connection
        
        # Create two nodes
        src = upsert_node(conn, KGNodeType.FILE_SPEC, "test.edge.src", {})
        dst = upsert_node(conn, KGNodeType.FILE_FIELD, "test.edge.dst", {})
        
        # First edge
        edge1 = upsert_edge(conn, src, dst, KGEdgeRelation.HAS_FIELD, weight=0.5)
        
        # Second edge - same triple
        edge2 = upsert_edge(conn, src, dst, KGEdgeRelation.HAS_FIELD, weight=0.9)
        
        assert edge1 == edge2
        
        # Verify weight took max
        cur = conn.cursor()
        cur.execute("SELECT weight FROM kg.edges WHERE id = %s", (edge1,))
        wt = cur.fetchone()[0]
        assert float(wt) == 0.9

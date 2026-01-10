"""
Tests for graph traversal task matching (WS2-T2).

Uses graph-specific algorithms (BFS, DFS) for knowledge graph queries,
NOT semantic/embedding similarity. The KG structure is what matters.

Graph traversal enables:
1. Finding related tasks via shared entities/endpoints/patterns
2. Cross-provider learning via pattern connections
3. Path discovery between KG nodes
"""
import pytest
from unittest.mock import patch, MagicMock

from integration_coworker.kg import (
    find_related_tasks_via_graph,
    find_cross_provider_tasks_via_pattern,
    get_shortest_path,
    get_kg_node_count,
    _bfs_find_related_nodes,
    _dfs_find_paths,
    GraphTaskMatch,
)


# Mark all tests in this module to skip database initialization
pytestmark = pytest.mark.no_db


class TestGraphTaskMatch:
    """Tests for the GraphTaskMatch dataclass."""
    
    def test_graph_match_structure(self):
        """GraphTaskMatch should have all required fields."""
        match = GraphTaskMatch(
            task_key="task.stripe.create_payment",
            task_description="Create a payment intent",
            provider_code="stripe",
            graph_distance=2,
            path=["entity.payment", "template.stripe.pay", "task.stripe.create_payment"],
            relation_types=["produces_entity", "implements"],
            associated_templates=["template.stripe.payment_flow"],
        )
        
        assert match.task_key == "task.stripe.create_payment"
        assert match.provider_code == "stripe"
        assert match.graph_distance == 2
        assert len(match.path) == 3
        assert len(match.relation_types) == 2


class TestBFSTraversal:
    """Tests for BFS graph traversal."""
    
    def test_bfs_finds_direct_neighbors(self):
        """BFS should find directly connected nodes."""
        mock_cur = MagicMock()
        
        # First call: get neighbors of start node (finds target task)
        # Need to return empty list for subsequent calls to avoid infinite loop
        mock_cur.fetchall.side_effect = [
            [(2, "task", "uses_endpoint")],  # Direct neighbor is a task
            [],  # No more neighbors from node 2
        ]
        
        results = _bfs_find_related_nodes(
            mock_cur,
            start_node_id=1,
            target_node_type="task",
            max_depth=3,
            is_postgres=False,
        )
        
        assert len(results) == 1
        assert results[0][0] == 2  # node_id
        assert results[0][1] == 1  # distance
    
    def test_bfs_respects_max_depth(self):
        """BFS should not traverse beyond max_depth."""
        mock_cur = MagicMock()
        
        # Simulate deep graph with no tasks until depth 4
        mock_cur.fetchall.return_value = []  # No neighbors
        
        results = _bfs_find_related_nodes(
            mock_cur,
            start_node_id=1,
            target_node_type="task",
            max_depth=2,
            is_postgres=False,
        )
        
        assert len(results) == 0
    
    def test_bfs_avoids_cycles(self):
        """BFS should not revisit nodes (cycle detection)."""
        mock_cur = MagicMock()
        
        # Simulate cycle: 1 -> 2 -> 1
        call_count = [0]
        def mock_fetchall():
            call_count[0] += 1
            if call_count[0] == 1:
                return [(2, "endpoint", "uses_endpoint")]
            elif call_count[0] == 2:
                return [(1, "endpoint", "uses_endpoint")]  # Back to start
            return []
        
        mock_cur.fetchall.side_effect = mock_fetchall
        
        results = _bfs_find_related_nodes(
            mock_cur,
            start_node_id=1,
            target_node_type="task",
            max_depth=5,
            is_postgres=False,
        )
        
        # Should terminate without infinite loop
        assert isinstance(results, list)


class TestDFSTraversal:
    """Tests for DFS path finding."""
    
    def test_dfs_finds_path(self):
        """DFS should find a path between nodes."""
        mock_cur = MagicMock()
        
        # Node 1 connects to Node 2, Node 2 connects to Node 3
        call_count = [0]
        def mock_fetchall():
            call_count[0] += 1
            if call_count[0] == 1:
                return [(2, "edge_type_1")]
            elif call_count[0] == 2:
                return [(3, "edge_type_2")]
            return []
        
        mock_cur.fetchall.side_effect = mock_fetchall
        
        paths = _dfs_find_paths(
            mock_cur,
            start_node_id=1,
            end_node_id=3,
            max_depth=5,
            is_postgres=False,
        )
        
        assert len(paths) >= 1
        path_ids, relations = paths[0]
        assert 1 in path_ids
        assert 3 in path_ids
    
    def test_dfs_respects_max_depth(self):
        """DFS should not search beyond max_depth."""
        mock_cur = MagicMock()
        mock_cur.fetchall.return_value = [(99, "edge")]  # Always has a neighbor
        
        paths = _dfs_find_paths(
            mock_cur,
            start_node_id=1,
            end_node_id=100,
            max_depth=2,
            is_postgres=False,
        )
        
        # Should not find path beyond depth 2
        for path, _ in paths:
            assert len(path) <= 3  # max_depth + 1


class TestFindRelatedTasksViaGraph:
    """Tests for finding related tasks through graph traversal."""
    
    @patch("integration_coworker.kg.db")
    @patch("integration_coworker.kg._bfs_find_related_nodes")
    def test_finds_tasks_from_entity(self, mock_bfs, mock_db):
        """Should find tasks connected to an entity."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db.get_connection.return_value = mock_conn
        mock_db.get_engine_type.return_value = "sqlite"
        
        # Entity node exists
        mock_cur.fetchone.side_effect = [
            (1,),  # Entity node ID
            ("task.stripe.pay", "Create Payment", "Create a payment", "stripe"),  # Task details
            ("entity.payment",),  # Path key
            ("task.stripe.pay",),  # Path key
        ]
        
        # BFS finds one task
        mock_bfs.return_value = [
            (2, 1, [1, 2], ["produces_entity"]),  # (node_id, distance, path, relations)
        ]
        
        # Templates for task
        mock_cur.fetchall.return_value = [("template.stripe.checkout",)]
        
        matches = find_related_tasks_via_graph(entity_name="Payment")
        
        # Should have called BFS
        mock_bfs.assert_called_once()
    
    @patch("integration_coworker.kg.db")
    def test_returns_empty_when_no_anchor(self, mock_db):
        """Should return empty list when no anchor node found."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db.get_connection.return_value = mock_conn
        mock_db.get_engine_type.return_value = "sqlite"
        
        mock_cur.fetchone.return_value = None  # No entity found
        
        matches = find_related_tasks_via_graph(entity_name="NonExistent")
        
        assert matches == []
    
    @patch("integration_coworker.kg.db")
    @patch("integration_coworker.kg._bfs_find_related_nodes")
    def test_filters_by_provider(self, mock_bfs, mock_db):
        """Should filter results by provider when specified."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db.get_connection.return_value = mock_conn
        mock_db.get_engine_type.return_value = "sqlite"
        
        mock_cur.fetchone.side_effect = [
            (1,),  # Entity node
            ("task.paypal.pay", "Pay", "Pay", "paypal"),  # Wrong provider
        ]
        mock_bfs.return_value = [(2, 1, [1, 2], ["edge"])]
        
        matches = find_related_tasks_via_graph(
            entity_name="Payment",
            provider_code="stripe",  # Filter to stripe only
        )
        
        # PayPal task should be filtered out
        assert len(matches) == 0


class TestCrossProviderPatternMatching:
    """Tests for cross-provider task discovery via patterns."""
    
    @patch("integration_coworker.kg.find_related_tasks_via_graph")
    def test_groups_by_provider(self, mock_find):
        """Should group tasks by provider."""
        mock_find.return_value = [
            GraphTaskMatch(
                task_key="task.stripe.pay",
                task_description="Payment",
                provider_code="stripe",
                graph_distance=2,
                path=[],
                relation_types=[],
                associated_templates=[],
            ),
            GraphTaskMatch(
                task_key="task.paypal.pay",
                task_description="Payment",
                provider_code="paypal",
                graph_distance=2,
                path=[],
                relation_types=[],
                associated_templates=[],
            ),
        ]
        
        result = find_cross_provider_tasks_via_pattern("pattern.crud_create")
        
        assert "stripe" in result
        assert "paypal" in result
    
    @patch("integration_coworker.kg.find_related_tasks_via_graph")
    def test_excludes_provider(self, mock_find):
        """Should exclude specified provider."""
        mock_find.return_value = [
            GraphTaskMatch(
                task_key="task.stripe.pay",
                task_description="Payment",
                provider_code="stripe",
                graph_distance=2,
                path=[],
                relation_types=[],
                associated_templates=[],
            ),
        ]
        
        result = find_cross_provider_tasks_via_pattern(
            "pattern.crud_create",
            exclude_provider="stripe",
        )
        
        assert "stripe" not in result


class TestShortestPath:
    """Tests for shortest path queries."""
    
    @patch("integration_coworker.kg.db")
    def test_finds_direct_path(self, mock_db):
        """Should find path between directly connected nodes."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db.get_connection.return_value = mock_conn
        mock_db.get_engine_type.return_value = "sqlite"
        
        # Node lookups
        mock_cur.fetchone.side_effect = [
            (1,),  # from_node
            (2,),  # to_node
            ("from_key",),  # path key 1
            ("to_key",),  # path key 2
        ]
        
        # Direct connection
        mock_cur.fetchall.return_value = [(2, "direct_edge")]
        
        result = get_shortest_path("from_key", "to_key")
        
        assert result is not None
        path_keys, relations = result
        assert len(path_keys) == 2
    
    @patch("integration_coworker.kg.db")
    def test_returns_none_when_no_path(self, mock_db):
        """Should return None when no path exists."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db.get_connection.return_value = mock_conn
        mock_db.get_engine_type.return_value = "sqlite"
        
        mock_cur.fetchone.side_effect = [
            (1,),  # from_node
            (2,),  # to_node
        ]
        mock_cur.fetchall.return_value = []  # No edges
        
        result = get_shortest_path("from_key", "to_key")
        
        assert result is None


class TestKGNodeCount:
    """Tests for KG node counting."""
    
    @patch("integration_coworker.kg.db")
    def test_returns_counts_by_type(self, mock_db):
        """Should return node counts grouped by type."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_db.get_connection.return_value = mock_conn
        mock_db.get_engine_type.return_value = "sqlite"
        
        mock_cur.fetchall.return_value = [
            ("task", 10),
            ("entity", 5),
            ("endpoint", 20),
        ]
        
        counts = get_kg_node_count()
        
        assert counts["task"] == 10
        assert counts["entity"] == 5
        assert counts["endpoint"] == 20
    
    @patch("integration_coworker.kg.db")
    def test_returns_empty_on_error(self, mock_db):
        """Should return empty dict on error."""
        mock_db.init_schema.side_effect = Exception("DB error")
        
        counts = get_kg_node_count()
        
        assert counts == {}


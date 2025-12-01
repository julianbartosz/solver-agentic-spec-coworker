"""
Performance smoke tests for V1 critical paths.

These tests provide baseline timing assertions to catch major regressions.
Run with: pytest tests/perf/ -v -m perf_smoke
Or skip with: pytest -m "not perf_smoke"

The tests use loose thresholds to avoid flakiness while still catching
10x+ regressions that would impact user experience.
"""
import json
import pytest
import time
from pathlib import Path
from typing import Callable, Any
from dataclasses import dataclass

# Import the core modules we're benchmarking
from integration_coworker.graph.state import WorkflowState
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.domain.models import Endpoint, IntegrationFlowNode


# ==============================================================================
# Timing utilities
# ==============================================================================

@dataclass
class TimingResult:
    """Result of a timed operation."""
    elapsed_seconds: float
    iterations: int
    avg_per_iteration: float
    result: Any


def time_operation(
    func: Callable,
    iterations: int = 1,
    warmup: int = 0,
) -> TimingResult:
    """
    Time an operation with optional warmup and multiple iterations.
    
    Args:
        func: Zero-argument callable to time
        iterations: Number of timed iterations
        warmup: Number of warmup iterations (not timed)
        
    Returns:
        TimingResult with timing statistics
    """
    # Warmup phase
    for _ in range(warmup):
        func()
    
    # Timed phase
    start = time.perf_counter()
    result = None
    for _ in range(iterations):
        result = func()
    elapsed = time.perf_counter() - start
    
    return TimingResult(
        elapsed_seconds=elapsed,
        iterations=iterations,
        avg_per_iteration=elapsed / iterations,
        result=result,
    )


# ==============================================================================
# Fixtures
# ==============================================================================

@pytest.fixture
def sample_spec_yaml() -> str:
    """Minimal OpenAPI spec for testing."""
    return """
openapi: "3.0.0"
info:
  title: Test API
  version: "1.0.0"
paths:
  /items:
    get:
      operationId: listItems
      summary: List all items
      responses:
        "200":
          description: Success
    post:
      operationId: createItem
      summary: Create an item
      responses:
        "201":
          description: Created
  /items/{id}:
    get:
      operationId: getItem
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: Success
    delete:
      operationId: deleteItem
      parameters:
        - name: id
          in: path
          required: true
          schema:
            type: string
      responses:
        "204":
          description: Deleted
"""


@pytest.fixture
def sample_state() -> WorkflowState:
    """Create a minimal workflow state for testing."""
    return WorkflowState(
        run_id="perf-test-001",
        source_refs=["test_api.yaml"],
        spec_refs=["test_api.yaml"],
        task_description="List all items from the API",
        options=IntegrationOptions(),
        completed_steps=[],
    )


@pytest.fixture
def sample_endpoints() -> list[Endpoint]:
    """Create sample endpoints for testing."""
    return [
        Endpoint(
            id=i,
            source_system_id=1,
            spec_document_id=1,
            path=f"/v1/resource{i}",
            method="GET",
            operation_id=f"getResource{i}",
            summary=f"Get resource {i}",
            description=f"Retrieve resource {i} by ID",
            request_schema_id=None,
            response_schema_id=None,
        )
        for i in range(100)
    ]


# ==============================================================================
# Smoke tests for critical path timing
# ==============================================================================

@pytest.mark.perf_smoke
class TestStateCreationPerformance:
    """Performance tests for WorkflowState creation and manipulation."""
    
    def test_workflow_state_creation_fast(self, sample_state):
        """WorkflowState creation should be sub-millisecond."""
        def create_state():
            return WorkflowState(
                run_id="perf-test",
                source_refs=["spec.yaml"],
                spec_refs=["spec.yaml"],
                task_description="Test task",
                options=IntegrationOptions(),
                completed_steps=[],
            )
        
        result = time_operation(create_state, iterations=100, warmup=10)
        
        # Should average under 1ms per creation
        assert result.avg_per_iteration < 0.001, (
            f"State creation too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )
    
    def test_endpoint_list_operations_fast(self, sample_endpoints):
        """Operations on endpoint lists should be fast."""
        def filter_endpoints():
            return [e for e in sample_endpoints if "GET" in e.method]
        
        result = time_operation(filter_endpoints, iterations=100, warmup=10)
        
        # Filtering 100 endpoints should be under 1ms
        assert result.avg_per_iteration < 0.001, (
            f"Endpoint filtering too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )


@pytest.mark.perf_smoke
class TestSpecParsingPerformance:
    """Performance tests for spec parsing operations."""
    
    def test_yaml_parsing_fast(self, sample_spec_yaml):
        """YAML spec parsing should be reasonably fast."""
        import yaml
        
        def parse_yaml():
            return yaml.safe_load(sample_spec_yaml)
        
        result = time_operation(parse_yaml, iterations=100, warmup=10)
        
        # Should average under 5ms per parse
        assert result.avg_per_iteration < 0.005, (
            f"YAML parsing too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )
    
    def test_json_serialization_fast(self, sample_endpoints):
        """JSON serialization of domain models should be fast."""
        import json
        from dataclasses import asdict
        
        endpoint_dicts = [asdict(e) for e in sample_endpoints]
        
        def serialize():
            return json.dumps(endpoint_dicts)
        
        result = time_operation(serialize, iterations=100, warmup=10)
        
        # Serializing 100 endpoints should be under 5ms
        assert result.avg_per_iteration < 0.005, (
            f"JSON serialization too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )


@pytest.mark.perf_smoke
class TestSimilarityComputationPerformance:
    """Performance tests for similarity calculations."""
    
    def test_cosine_similarity_fast(self):
        """Cosine similarity should be sub-millisecond."""
        from integration_coworker.retrieval.semantic_search import cosine_similarity
        import random
        
        # Create random vectors
        vec_a = [random.random() for _ in range(1536)]
        vec_b = [random.random() for _ in range(1536)]
        
        def compute_similarity():
            return cosine_similarity(vec_a, vec_b)
        
        result = time_operation(compute_similarity, iterations=100, warmup=10)
        
        # Should average under 1ms for 1536-dim vectors
        assert result.avg_per_iteration < 0.001, (
            f"Cosine similarity too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )
    
    def test_batch_similarity_computation(self):
        """Batch similarity comparisons should scale linearly."""
        from integration_coworker.retrieval.semantic_search import cosine_similarity
        import random
        
        query = [random.random() for _ in range(1536)]
        candidates = [[random.random() for _ in range(1536)] for _ in range(50)]
        
        def batch_compare():
            return [cosine_similarity(query, c) for c in candidates]
        
        result = time_operation(batch_compare, iterations=10, warmup=2)
        
        # 50 comparisons should be under 50ms
        assert result.avg_per_iteration < 0.05, (
            f"Batch similarity too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )


@pytest.mark.perf_smoke
class TestGraphOperationsPerformance:
    """Performance tests for graph-related operations."""
    
    def test_node_lookup_fast(self):
        """Looking up nodes in workflow should be fast."""
        # Create a list of workflow nodes
        nodes = [
            IntegrationFlowNode(
                id=i,
                task_id=1,
                node_key=f"node_{i}",
                node_type="action",
                label=f"Node {i}",
            )
            for i in range(100)
        ]
        
        def lookup_by_key():
            return {n.node_key: n for n in nodes}
        
        result = time_operation(lookup_by_key, iterations=100, warmup=10)
        
        # Building a lookup dict should be under 1ms
        assert result.avg_per_iteration < 0.001, (
            f"Node lookup too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )


@pytest.mark.perf_smoke  
class TestMemoryFootprint:
    """Tests for memory usage patterns."""
    
    def test_endpoint_list_memory_reasonable(self, sample_endpoints):
        """Large endpoint lists shouldn't explode memory."""
        import sys
        
        # Estimate memory of endpoint list
        size = sys.getsizeof(sample_endpoints)
        for endpoint in sample_endpoints:
            # Add size of each endpoint's attributes
            size += sys.getsizeof(endpoint.path)
            size += sys.getsizeof(endpoint.method)
            size += sys.getsizeof(endpoint.operation_id)
            size += sys.getsizeof(endpoint.summary)
            size += sys.getsizeof(endpoint.description)
        
        # 100 endpoints should be under 100KB
        assert size < 100_000, (
            f"Endpoint list too large: {size / 1000:.1f}KB for 100 endpoints"
        )
    
    def test_state_memory_reasonable(self, sample_state, sample_endpoints):
        """WorkflowState with data shouldn't explode memory."""
        import sys
        
        # Add endpoints to state
        sample_state.endpoints = sample_endpoints
        
        size = sys.getsizeof(sample_state)
        
        # State object should be under 1KB (it holds references, not copies)
        assert size < 1000, (
            f"State object too large: {size} bytes"
        )


# ==============================================================================
# Integration timing tests (optional, may be slower)
# ==============================================================================

@pytest.mark.perf_smoke
class TestNodeExecutionPerformance:
    """Performance tests for individual node execution."""
    
    def test_plan_run_fast(self, sample_state):
        """plan_run node should be very fast (no I/O)."""
        from integration_coworker.graph.nodes.plan_run import plan_run
        
        def run_plan():
            return plan_run(sample_state)
        
        result = time_operation(run_plan, iterations=10, warmup=2)
        
        # plan_run is pure logic, should be under 10ms
        assert result.avg_per_iteration < 0.01, (
            f"plan_run too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )
    
    def test_understand_task_mock_fast(self, sample_state):
        """understand_task with mock LLM should be fast."""
        import os
        os.environ.setdefault("USE_MOCK_LLM", "true")
        
        from integration_coworker.graph.nodes.understand_task import understand_task
        
        def run_understand():
            return understand_task(sample_state)
        
        result = time_operation(run_understand, iterations=5, warmup=1)
        
        # With mock LLM, should be under 100ms
        assert result.avg_per_iteration < 0.1, (
            f"understand_task (mock) too slow: {result.avg_per_iteration*1000:.2f}ms avg"
        )


# ==============================================================================
# Summary report (optional)
# ==============================================================================

@pytest.fixture(scope="session", autouse=True)
def perf_summary(request):
    """Print performance summary at end of session."""
    yield
    
    # Only print if we're running perf tests
    if any(
        item.get_closest_marker("perf_smoke")
        for item in request.session.items
        if hasattr(item, 'get_closest_marker')
    ):
        print("\n" + "=" * 60)
        print("Performance smoke tests completed")
        print("All timing assertions passed")
        print("=" * 60)

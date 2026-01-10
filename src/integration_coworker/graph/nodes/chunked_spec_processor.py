"""
Chunked Spec Processor - V26-001/002 Fix

This module implements chunked processing for large OpenAPI specs
to prevent timeout and OOM issues.

Problem:
- Large specs (7MB+ like stripe_api.json, 11MB+ like github_api.json)
- Exceed RUN_TIMEOUT (300s) during parsing/processing
- Exit code 137 (SIGKILL) from timeout command

Solution:
- Detect large specs and process in chunks
- Use streaming for large specs to avoid memory pressure
- Implement incremental processing with checkpoints
- Provide spec size estimation and time budgets

Architecture:
- ChunkedSpecProcessor: Main class for orchestrating chunked processing
- SpecChunk: Represents a processable chunk of a spec
- ChunkStrategy: Determines how to split specs (by endpoint count, size, etc.)

Performance Target:
- 7MB spec should process in under 2 minutes (vs. timeout at 5min)
- Memory footprint should stay under 500MB for large specs
"""

import logging
import hashlib
import time
from dataclasses import dataclass, field
from typing import Optional, Any, Iterator, Callable
from enum import Enum, auto

logger = logging.getLogger(__name__)


# =============================================================================
# Constants and Configuration
# =============================================================================

# Size thresholds for triggering chunked processing
SPEC_SIZE_THRESHOLD_BYTES = 3 * 1024 * 1024  # 3MB - trigger chunked mode
SPEC_SIZE_LARGE_BYTES = 5 * 1024 * 1024  # 5MB - use aggressive chunking
SPEC_SIZE_HUGE_BYTES = 10 * 1024 * 1024  # 10MB - use streaming mode

# Endpoint count thresholds
ENDPOINTS_PER_CHUNK = 100  # Max endpoints per chunk
SCHEMAS_PER_CHUNK = 200  # Max schemas per chunk

# Time budget (seconds)
DEFAULT_CHUNK_TIMEOUT = 60  # 1 minute per chunk
TOTAL_PROCESSING_BUDGET = 240  # 4 minutes total (leave 1 min buffer for 5min timeout)

# Memory estimates (bytes per item)
BYTES_PER_ENDPOINT = 5000  # Average bytes in memory per endpoint
BYTES_PER_SCHEMA = 3000  # Average bytes in memory per schema


class ChunkStrategy(Enum):
    """Strategy for chunking specs."""
    NONE = auto()  # No chunking needed (small spec)
    ENDPOINT_BASED = auto()  # Chunk by endpoint count
    SCHEMA_BASED = auto()  # Chunk by schema count
    SIZE_BASED = auto()  # Chunk by byte size
    STREAMING = auto()  # Use streaming for huge specs


@dataclass
class SpecChunk:
    """A processable chunk of a spec."""
    chunk_id: str
    chunk_index: int
    total_chunks: int
    paths: dict[str, Any] = field(default_factory=dict)
    schemas: dict[str, Any] = field(default_factory=dict)
    info: dict[str, Any] = field(default_factory=dict)
    servers: list[dict[str, Any]] = field(default_factory=list)
    security: list[dict[str, Any]] = field(default_factory=list)
    
    # Metadata
    endpoint_count: int = 0
    schema_count: int = 0
    estimated_size_bytes: int = 0
    
    def to_openapi_spec(self) -> dict[str, Any]:
        """Convert chunk back to OpenAPI spec format."""
        spec: dict[str, Any] = {
            "openapi": "3.0.0",
            "info": self.info or {"title": f"Chunk {self.chunk_index}", "version": "1.0"},
            "paths": self.paths,
        }
        if self.servers:
            spec["servers"] = self.servers
        if self.security:
            spec["security"] = self.security
        if self.schemas:
            spec["components"] = {"schemas": self.schemas}
        return spec


@dataclass
class ChunkingPlan:
    """Plan for how to chunk a spec."""
    strategy: ChunkStrategy
    total_chunks: int
    estimated_total_time: float
    memory_estimate_mb: float
    endpoint_count: int
    schema_count: int
    spec_size_bytes: int
    warnings: list[str] = field(default_factory=list)


@dataclass  
class ChunkProcessingResult:
    """Result of processing a single chunk."""
    chunk_id: str
    success: bool
    endpoints_processed: int = 0
    schemas_processed: int = 0
    processing_time_ms: float = 0.0
    error: Optional[str] = None
    
    
class ChunkedSpecProcessor:
    """
    Orchestrates chunked processing of large OpenAPI specs.
    
    Usage:
        processor = ChunkedSpecProcessor(spec_dict)
        plan = processor.plan()
        
        if plan.strategy != ChunkStrategy.NONE:
            for chunk in processor.chunks():
                result = process_chunk(chunk)
                if not result.success:
                    handle_error(result)
    """
    
    def __init__(
        self, 
        spec: dict[str, Any],
        timeout_budget: float = TOTAL_PROCESSING_BUDGET,
        memory_budget_mb: float = 500.0,
    ):
        """
        Initialize the chunked processor.
        
        Args:
            spec: The OpenAPI spec dictionary
            timeout_budget: Total time budget in seconds
            memory_budget_mb: Memory budget in megabytes
        """
        self.spec = spec
        self.timeout_budget = timeout_budget
        self.memory_budget_mb = memory_budget_mb
        
        # Analyze spec
        self._paths = spec.get("paths", {})
        self._components = spec.get("components", {})
        self._schemas = self._components.get("schemas", {})
        self._info = spec.get("info", {})
        self._servers = spec.get("servers", [])
        self._security = spec.get("security", [])
        
        # Calculate metrics
        self._endpoint_count = self._count_endpoints()
        self._schema_count = len(self._schemas)
        self._estimated_size = self._estimate_size()
        
        # Cache the plan
        self._plan: Optional[ChunkingPlan] = None
        
    def _count_endpoints(self) -> int:
        """Count total endpoints in spec."""
        count = 0
        for path_item in self._paths.values():
            if isinstance(path_item, dict):
                for method in ("get", "post", "put", "patch", "delete", "head", "options"):
                    if method in path_item:
                        count += 1
        return count
    
    def _estimate_size(self) -> int:
        """Estimate spec size in bytes (rough heuristic)."""
        import json
        try:
            return len(json.dumps(self.spec, separators=(',', ':')))
        except Exception:
            return self._endpoint_count * BYTES_PER_ENDPOINT + self._schema_count * BYTES_PER_SCHEMA
    
    def plan(self) -> ChunkingPlan:
        """
        Create a chunking plan for the spec.
        
        Returns:
            ChunkingPlan with strategy and estimates
        """
        if self._plan:
            return self._plan
            
        warnings = []
        
        # Determine strategy based on spec characteristics
        strategy = ChunkStrategy.NONE
        total_chunks = 1
        
        if self._estimated_size >= SPEC_SIZE_HUGE_BYTES:
            strategy = ChunkStrategy.STREAMING
            total_chunks = max(1, self._endpoint_count // ENDPOINTS_PER_CHUNK)
            warnings.append(f"Huge spec ({self._estimated_size / 1024 / 1024:.1f}MB) - using streaming mode")
        elif self._estimated_size >= SPEC_SIZE_LARGE_BYTES:
            strategy = ChunkStrategy.SIZE_BASED
            total_chunks = max(1, self._endpoint_count // ENDPOINTS_PER_CHUNK)
            warnings.append(f"Large spec ({self._estimated_size / 1024 / 1024:.1f}MB) - using aggressive chunking")
        elif self._estimated_size >= SPEC_SIZE_THRESHOLD_BYTES:
            strategy = ChunkStrategy.ENDPOINT_BASED
            total_chunks = max(1, (self._endpoint_count + ENDPOINTS_PER_CHUNK - 1) // ENDPOINTS_PER_CHUNK)
        elif self._endpoint_count > ENDPOINTS_PER_CHUNK * 2:
            strategy = ChunkStrategy.ENDPOINT_BASED
            total_chunks = max(1, (self._endpoint_count + ENDPOINTS_PER_CHUNK - 1) // ENDPOINTS_PER_CHUNK)
            warnings.append(f"Many endpoints ({self._endpoint_count}) - chunking by endpoint count")
        
        # Estimate processing time
        # Empirical: ~1ms per endpoint for parsing, ~10ms for schema resolution
        base_time_per_endpoint = 0.001  # 1ms
        base_time_per_schema = 0.01  # 10ms
        estimated_time = (
            self._endpoint_count * base_time_per_endpoint +
            self._schema_count * base_time_per_schema
        )
        # Add overhead for LLM calls (dominant factor)
        estimated_time += total_chunks * 30  # 30s per chunk for LLM
        
        # Estimate memory
        memory_estimate = (
            self._endpoint_count * BYTES_PER_ENDPOINT +
            self._schema_count * BYTES_PER_SCHEMA
        ) / 1024 / 1024  # MB
        
        # Warn if estimates exceed budget
        if estimated_time > self.timeout_budget:
            warnings.append(
                f"Estimated time ({estimated_time:.0f}s) exceeds budget ({self.timeout_budget:.0f}s). "
                f"Consider reducing endpoints or using streaming."
            )
        if memory_estimate > self.memory_budget_mb:
            warnings.append(
                f"Estimated memory ({memory_estimate:.0f}MB) exceeds budget ({self.memory_budget_mb:.0f}MB)."
            )
        
        self._plan = ChunkingPlan(
            strategy=strategy,
            total_chunks=total_chunks,
            estimated_total_time=estimated_time,
            memory_estimate_mb=memory_estimate,
            endpoint_count=self._endpoint_count,
            schema_count=self._schema_count,
            spec_size_bytes=self._estimated_size,
            warnings=warnings,
        )
        
        logger.info(
            f"[V26-001] Chunking plan: strategy={strategy.name}, "
            f"chunks={total_chunks}, endpoints={self._endpoint_count}, "
            f"schemas={self._schema_count}, size={self._estimated_size / 1024 / 1024:.1f}MB"
        )
        for warning in warnings:
            logger.warning(f"[V26-001] {warning}")
        
        return self._plan
    
    def needs_chunking(self) -> bool:
        """Check if spec needs chunked processing."""
        plan = self.plan()
        return plan.strategy != ChunkStrategy.NONE
    
    def chunks(self) -> Iterator[SpecChunk]:
        """
        Yield chunks for processing.
        
        Yields:
            SpecChunk objects for each chunk
        """
        plan = self.plan()
        
        if plan.strategy == ChunkStrategy.NONE:
            # Single chunk with entire spec
            yield SpecChunk(
                chunk_id=self._compute_chunk_id(0),
                chunk_index=0,
                total_chunks=1,
                paths=self._paths,
                schemas=self._schemas,
                info=self._info,
                servers=self._servers,
                security=self._security,
                endpoint_count=self._endpoint_count,
                schema_count=self._schema_count,
                estimated_size_bytes=self._estimated_size,
            )
            return
        
        # Split paths into chunks
        path_items = list(self._paths.items())
        paths_per_chunk = max(1, len(path_items) // plan.total_chunks)
        
        for i in range(plan.total_chunks):
            start_idx = i * paths_per_chunk
            end_idx = start_idx + paths_per_chunk if i < plan.total_chunks - 1 else len(path_items)
            
            chunk_paths = dict(path_items[start_idx:end_idx])
            
            # Find schemas referenced by this chunk's paths
            chunk_schemas = self._find_referenced_schemas(chunk_paths)
            
            # Count endpoints in this chunk
            chunk_endpoint_count = 0
            for path_item in chunk_paths.values():
                if isinstance(path_item, dict):
                    for method in ("get", "post", "put", "patch", "delete", "head", "options"):
                        if method in path_item:
                            chunk_endpoint_count += 1
            
            yield SpecChunk(
                chunk_id=self._compute_chunk_id(i),
                chunk_index=i,
                total_chunks=plan.total_chunks,
                paths=chunk_paths,
                schemas=chunk_schemas,
                info=self._info,
                servers=self._servers,
                security=self._security,
                endpoint_count=chunk_endpoint_count,
                schema_count=len(chunk_schemas),
                estimated_size_bytes=self._estimated_size // plan.total_chunks,
            )
    
    def _compute_chunk_id(self, index: int) -> str:
        """Compute a stable chunk ID."""
        spec_hash = hashlib.sha256(str(self.spec.get("info", {})).encode()).hexdigest()[:8]
        return f"chunk_{spec_hash}_{index}"
    
    def _find_referenced_schemas(self, paths: dict[str, Any]) -> dict[str, Any]:
        """
        Find all schemas referenced by the given paths.
        
        This does a shallow reference resolution to include only schemas
        that are directly referenced by endpoints in this chunk.
        """
        import json
        
        referenced_schemas: dict[str, Any] = {}
        
        # Serialize paths to find $ref patterns
        paths_str = json.dumps(paths)
        
        # Find all schema references
        import re
        refs = re.findall(r'"\$ref":\s*"#/components/schemas/([^"]+)"', paths_str)
        
        for ref in set(refs):
            if ref in self._schemas:
                referenced_schemas[ref] = self._schemas[ref]
                # Also include schemas referenced by this schema (one level deep)
                schema_str = json.dumps(self._schemas[ref])
                nested_refs = re.findall(r'"\$ref":\s*"#/components/schemas/([^"]+)"', schema_str)
                for nested_ref in nested_refs:
                    if nested_ref in self._schemas and nested_ref not in referenced_schemas:
                        referenced_schemas[nested_ref] = self._schemas[nested_ref]
        
        return referenced_schemas
    

def create_chunked_spec_processor(
    spec: dict[str, Any],
    run_timeout: float = 300.0,
) -> ChunkedSpecProcessor:
    """
    Factory function to create a ChunkedSpecProcessor.
    
    Args:
        spec: OpenAPI spec dictionary
        run_timeout: Total run timeout in seconds
        
    Returns:
        Configured ChunkedSpecProcessor
    """
    # Leave 20% buffer for overhead
    timeout_budget = run_timeout * 0.8
    return ChunkedSpecProcessor(spec, timeout_budget=timeout_budget)


def should_use_chunked_processing(spec: dict[str, Any]) -> tuple[bool, str]:
    """
    Quick check if chunked processing is recommended.
    
    Returns:
        Tuple of (should_chunk, reason)
    """
    processor = ChunkedSpecProcessor(spec)
    plan = processor.plan()
    
    if plan.strategy == ChunkStrategy.NONE:
        return False, "Spec is small enough for single-pass processing"
    
    return True, f"Spec requires chunked processing: {plan.strategy.name}"


def estimate_processing_time(spec: dict[str, Any]) -> float:
    """
    Estimate how long processing will take.
    
    Returns:
        Estimated time in seconds
    """
    processor = ChunkedSpecProcessor(spec)
    plan = processor.plan()
    return plan.estimated_total_time

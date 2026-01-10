"""
ArtifactStore protocol and ArtifactRef dataclass.

=============================================================================
NON-NEGOTIABLE INVARIANTS (V2 Harness Alignment Plan)
=============================================================================

1. NO DATA LOSS: Any field excluded from checkpoint JSON MUST be replaced
   with an ArtifactRef and be fully recoverable on resume.

2. ATOMIC WRITES: Artifact writes use tmp → fsync → rename pattern. A crash
   mid-write never leaves a corrupt artifact in the store.

3. CONTENT-ADDRESSED: sha256 hash in ArtifactRef must match content on read.
   Reads verify checksum and fail loudly if corrupted/missing.

4. RETENTION EXPLICIT: TTL or keep-last-N policies are explicit. The store
   does NOT silently delete artifacts for active threads.

5. URI IS THE CONTRACT: ArtifactRef.uri is the stable locator (file://... 
   today, obj://... for object storage later). Service deployments can
   change storage backends without rewriting graph logic.

=============================================================================

Per Agent Harness Alignment Plan:
- Large fields (openapi_spec, spec_documents, files) are spooled to artifact
  storage at checkpoint serialize time
- On resume, fields are rehydrated from ArtifactRef via the store
- The store is pluggable: FilesystemArtifactStore is the v2 default, but
  ObjectStoreBackend can be added for Azure Blob/S3 without graph changes

References:
- https://docs.langchain.com/oss/javascript/deepagents/harness (large result eviction)
- https://docs.langchain.com/oss/python/langgraph/persistence (checkpoint persistence)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, TypeVar, runtime_checkable


class ArtifactCodec(str, Enum):
    """
    Codec used to serialize artifact content.
    
    The codec determines how the value is encoded before storage
    and decoded after retrieval.
    """
    JSON = "json"           # JSON serialization (default for dicts/lists)
    PICKLE = "pickle"       # Python pickle (for complex objects, use with caution)
    MSGPACK = "msgpack"     # MessagePack (faster/smaller than JSON)
    RAW = "raw"             # Raw bytes (for already-serialized content)


@dataclass
class ArtifactRef:
    """
    Reference to an artifact in external storage.
    
    This is what gets stored in checkpoint JSON instead of the large field.
    The reference contains all information needed to retrieve and verify
    the artifact.
    
    Attributes:
        run_id: The workflow run this artifact belongs to
        key: Field name this artifact represents (e.g., "openapi_spec")
        uri: Backend-neutral locator (file://..., obj://..., s3://...)
        sha256: Content hash for integrity verification
        size_bytes: Size of serialized content
        content_type: MIME type hint (e.g., "application/json")
        codec: How the content was serialized
        created_at: When the artifact was stored
        metadata: Optional additional metadata (e.g., original field type)
    """
    run_id: str
    key: str
    uri: str
    sha256: str
    size_bytes: int
    content_type: str = "application/json"
    codec: ArtifactCodec = ArtifactCodec.JSON
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to JSON-compatible dict for checkpoint storage."""
        return {
            "__artifact_ref__": True,  # Marker for deserialization
            "run_id": self.run_id,
            "key": self.key,
            "uri": self.uri,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "content_type": self.content_type,
            "codec": self.codec.value,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArtifactRef":
        """Deserialize from checkpoint JSON dict."""
        return cls(
            run_id=data["run_id"],
            key=data["key"],
            uri=data["uri"],
            sha256=data["sha256"],
            size_bytes=data["size_bytes"],
            content_type=data.get("content_type", "application/json"),
            codec=ArtifactCodec(data.get("codec", "json")),
            created_at=datetime.fromisoformat(data["created_at"]) if "created_at" in data else datetime.now(timezone.utc),
            metadata=data.get("metadata", {}),
        )
    
    @classmethod
    def is_artifact_ref(cls, obj: Any) -> bool:
        """Check if a dict represents an ArtifactRef."""
        return isinstance(obj, dict) and obj.get("__artifact_ref__") is True


T = TypeVar("T")


@runtime_checkable
class ArtifactStore(Protocol):
    """
    Protocol for artifact storage backends.
    
    Implementations must satisfy the NON-NEGOTIABLE INVARIANTS:
    1. No data loss on checkpoint resume
    2. Atomic writes (tmp → fsync → rename)
    3. Content-addressed with sha256 verification
    4. Explicit retention policies
    5. URI is the stable contract
    
    The default implementation is FilesystemArtifactStore. Object storage
    backends (S3, Azure Blob) can be added by implementing this protocol.
    """
    
    @abstractmethod
    def put(
        self,
        run_id: str,
        key: str,
        value: Any,
        *,
        codec: ArtifactCodec = ArtifactCodec.JSON,
        content_type: str = "application/json",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRef:
        """
        Store an artifact and return a reference.
        
        This method MUST be atomic: either the artifact is fully stored
        with a valid reference, or nothing is written.
        
        Args:
            run_id: Workflow run ID (used for grouping/cleanup)
            key: Field name (e.g., "openapi_spec", "spec_documents")
            value: The value to store (will be serialized per codec)
            codec: Serialization codec to use
            content_type: MIME type hint
            metadata: Optional additional metadata
            
        Returns:
            ArtifactRef pointing to the stored artifact
            
        Raises:
            ArtifactStoreError: If storage fails
        """
        ...
    
    @abstractmethod
    def get(self, ref: ArtifactRef) -> Any:
        """
        Retrieve an artifact by reference.
        
        This method MUST verify the sha256 checksum and raise an error
        if the content is corrupted or missing.
        
        Args:
            ref: ArtifactRef from a previous put() call
            
        Returns:
            The deserialized value
            
        Raises:
            ArtifactNotFoundError: If artifact doesn't exist
            ArtifactCorruptedError: If checksum verification fails
        """
        ...
    
    @abstractmethod
    def delete(self, ref: ArtifactRef) -> bool:
        """
        Delete an artifact.
        
        Returns True if deleted, False if not found.
        
        Args:
            ref: ArtifactRef to delete
            
        Returns:
            True if artifact was deleted, False if not found
        """
        ...
    
    @abstractmethod
    def list_by_run(self, run_id: str) -> List[ArtifactRef]:
        """
        List all artifacts for a run.
        
        Args:
            run_id: Workflow run ID
            
        Returns:
            List of ArtifactRefs for the run
        """
        ...
    
    @abstractmethod
    def delete_by_run(self, run_id: str) -> int:
        """
        Delete all artifacts for a run.
        
        Used for cleanup after successful workflow completion.
        
        Args:
            run_id: Workflow run ID
            
        Returns:
            Number of artifacts deleted
        """
        ...
    
    @abstractmethod
    def exists(self, ref: ArtifactRef) -> bool:
        """
        Check if an artifact exists (without loading it).
        
        Args:
            ref: ArtifactRef to check
            
        Returns:
            True if artifact exists, False otherwise
        """
        ...


# =============================================================================
# Exceptions
# =============================================================================

class ArtifactStoreError(Exception):
    """Base exception for artifact store errors."""
    pass


class ArtifactNotFoundError(ArtifactStoreError):
    """Artifact not found at the expected location."""
    
    def __init__(self, ref: ArtifactRef):
        self.ref = ref
        super().__init__(f"Artifact not found: {ref.uri} (run={ref.run_id}, key={ref.key})")


class ArtifactCorruptedError(ArtifactStoreError):
    """Artifact content doesn't match expected checksum."""
    
    def __init__(self, ref: ArtifactRef, expected_sha256: str, actual_sha256: str):
        self.ref = ref
        self.expected_sha256 = expected_sha256
        self.actual_sha256 = actual_sha256
        super().__init__(
            f"Artifact corrupted: {ref.uri} - "
            f"expected sha256={expected_sha256[:16]}..., "
            f"got {actual_sha256[:16]}..."
        )


class ArtifactWriteError(ArtifactStoreError):
    """Failed to write artifact to storage."""
    
    def __init__(self, run_id: str, key: str, reason: str):
        self.run_id = run_id
        self.key = key
        super().__init__(f"Failed to write artifact {key} for run {run_id}: {reason}")

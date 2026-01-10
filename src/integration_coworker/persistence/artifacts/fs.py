"""
Filesystem-based artifact store implementation.

This is the v2 default backend for artifact storage. It stores artifacts as
content-addressed blobs on the filesystem with a Postgres index for metadata.

Features:
- Content-addressed storage (sha256 in filename)
- Atomic writes (tmp → fsync → rename)
- Checksum verification on read
- Configurable root path for service deployments
- Optional Postgres index for cross-process discovery

Architecture:
    artifacts_root/
    ├── {run_id}/
    │   ├── {key}-{sha256[:16]}.json.gz   # Compressed artifact blob
    │   └── manifest.json                  # Local manifest (fallback if DB down)
    └── ...

The FilesystemArtifactStore is suitable for:
- Single-node deployments
- Development and testing
- Deployments with shared filesystem (NFS, EFS)

For object storage (S3, Azure Blob), implement the ArtifactStore protocol
with the same URI scheme (obj://bucket/path) for seamless migration.
"""

import gzip
import hashlib
import json
import logging
import os
import pickle
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import (
    ArtifactCodec,
    ArtifactRef,
    ArtifactStore,
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    ArtifactWriteError,
)

logger = logging.getLogger(__name__)

# Default artifact root - can be overridden via config
DEFAULT_ARTIFACT_ROOT = ".artifacts"


def _get_default_artifact_root() -> Path:
    """
    Get the default artifact root path.
    
    Priority:
    1. ARTIFACT_STORE_ROOT environment variable
    2. Config setting (if available)
    3. .artifacts in current directory
    """
    # Check environment variable first
    env_root = os.environ.get("ARTIFACT_STORE_ROOT")
    if env_root:
        return Path(env_root)
    
    # Try to get from config
    try:
        from integration_coworker.config import get_settings
        settings = get_settings()
        if hasattr(settings, "artifact_root") and settings.artifact_root:
            return Path(settings.artifact_root)
    except Exception:
        pass
    
    # Default to .artifacts in current directory
    return Path(DEFAULT_ARTIFACT_ROOT)


class FilesystemArtifactStore(ArtifactStore):
    """
    Filesystem-based artifact store with atomic writes and checksum verification.
    
    Satisfies all NON-NEGOTIABLE INVARIANTS:
    1. No data loss: Artifacts are stored before checkpoint completes
    2. Atomic writes: Uses tmp → fsync → rename pattern
    3. Content-addressed: sha256 hash in filename, verified on read
    4. Explicit retention: Only delete_by_run removes artifacts
    5. URI contract: file://path URIs, swappable to obj:// later
    
    Args:
        root: Root directory for artifact storage (defaults to .artifacts)
        compress: Whether to gzip-compress artifacts (default True)
        use_index: Whether to record artifacts in Postgres index (default True)
    """
    
    def __init__(
        self,
        root: Optional[Path] = None,
        compress: bool = True,
        use_index: bool = True,
    ):
        self.root = Path(root) if root else _get_default_artifact_root()
        self.compress = compress
        self.use_index = use_index
        
        # Ensure root exists
        self.root.mkdir(parents=True, exist_ok=True)
        
        logger.debug(f"FilesystemArtifactStore initialized: root={self.root}, compress={compress}")
    
    def _get_run_dir(self, run_id: str) -> Path:
        """Get the directory for a specific run's artifacts."""
        # Sanitize run_id for filesystem safety
        safe_run_id = run_id.replace("/", "_").replace("\\", "_")
        run_dir = self.root / safe_run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir
    
    def _serialize(self, value: Any, codec: ArtifactCodec) -> bytes:
        """Serialize value according to codec."""
        if codec == ArtifactCodec.JSON:
            return json.dumps(value, default=str, ensure_ascii=False).encode("utf-8")
        elif codec == ArtifactCodec.PICKLE:
            return pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        elif codec == ArtifactCodec.MSGPACK:
            try:
                import msgpack
                return msgpack.packb(value, default=str, strict_types=False)
            except ImportError:
                logger.warning("msgpack not available, falling back to JSON")
                return json.dumps(value, default=str, ensure_ascii=False).encode("utf-8")
        elif codec == ArtifactCodec.RAW:
            if isinstance(value, bytes):
                return value
            elif isinstance(value, str):
                return value.encode("utf-8")
            else:
                raise ValueError(f"RAW codec requires bytes or str, got {type(value)}")
        else:
            raise ValueError(f"Unknown codec: {codec}")
    
    def _deserialize(self, data: bytes, codec: ArtifactCodec) -> Any:
        """Deserialize data according to codec."""
        if codec == ArtifactCodec.JSON:
            return json.loads(data.decode("utf-8"))
        elif codec == ArtifactCodec.PICKLE:
            return pickle.loads(data)
        elif codec == ArtifactCodec.MSGPACK:
            try:
                import msgpack
                return msgpack.unpackb(data, raw=False, strict_map_key=False)
            except ImportError:
                # Assume JSON fallback was used
                return json.loads(data.decode("utf-8"))
        elif codec == ArtifactCodec.RAW:
            return data
        else:
            raise ValueError(f"Unknown codec: {codec}")
    
    def _compute_sha256(self, data: bytes) -> str:
        """Compute sha256 hash of data."""
        return hashlib.sha256(data).hexdigest()
    
    def _make_blob_path(self, run_dir: Path, key: str, sha256: str) -> Path:
        """Generate the blob filename."""
        # Use first 16 chars of hash for reasonable filename length
        suffix = ".json.gz" if self.compress else ".json"
        return run_dir / f"{key}-{sha256[:16]}{suffix}"
    
    def _make_uri(self, blob_path: Path) -> str:
        """Generate a file:// URI for the artifact."""
        return f"file://{blob_path.absolute()}"
    
    def _parse_uri(self, uri: str) -> Path:
        """Parse a file:// URI back to a Path."""
        if uri.startswith("file://"):
            return Path(uri[7:])
        else:
            raise ValueError(f"Unsupported URI scheme: {uri}")
    
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
        Store an artifact atomically.
        
        Uses tmp file → fsync → rename pattern for crash safety.
        """
        run_dir = self._get_run_dir(run_id)
        
        try:
            # Serialize the value
            raw_data = self._serialize(value, codec)
            sha256 = self._compute_sha256(raw_data)
            size_bytes = len(raw_data)
            
            # Compress if enabled
            if self.compress:
                write_data = gzip.compress(raw_data, compresslevel=6)
            else:
                write_data = raw_data
            
            # Determine final path
            blob_path = self._make_blob_path(run_dir, key, sha256)
            
            # Skip write if content-addressed blob already exists
            if blob_path.exists():
                logger.debug(f"Artifact already exists: {blob_path}")
            else:
                # Atomic write: tmp → fsync → rename
                fd, tmp_path = tempfile.mkstemp(dir=run_dir, suffix=".tmp")
                try:
                    os.write(fd, write_data)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                
                # Atomic rename
                os.rename(tmp_path, blob_path)
                logger.debug(f"Artifact written: {blob_path} ({size_bytes} bytes, sha256={sha256[:16]}...)")
            
            # Create reference
            uri = self._make_uri(blob_path)
            ref = ArtifactRef(
                run_id=run_id,
                key=key,
                uri=uri,
                sha256=sha256,
                size_bytes=size_bytes,
                content_type=content_type,
                codec=codec,
                created_at=datetime.now(timezone.utc),
                metadata=metadata or {},
            )
            
            # Record in Postgres index (if enabled and available)
            if self.use_index:
                try:
                    from .index import insert_artifact_ref
                    insert_artifact_ref(ref)
                except Exception as e:
                    # Log but don't fail - filesystem is the source of truth
                    logger.warning(f"Failed to index artifact in Postgres: {e}")
            
            # Update local manifest (for recovery if DB is down)
            self._update_manifest(run_dir, ref)
            
            return ref
            
        except Exception as e:
            raise ArtifactWriteError(run_id, key, str(e)) from e
    
    def get(self, ref: ArtifactRef) -> Any:
        """
        Retrieve an artifact with checksum verification.
        """
        blob_path = self._parse_uri(ref.uri)
        
        if not blob_path.exists():
            raise ArtifactNotFoundError(ref)
        
        # Read the blob
        with open(blob_path, "rb") as f:
            stored_data = f.read()
        
        # Decompress if needed
        if self.compress or ref.uri.endswith(".gz"):
            try:
                raw_data = gzip.decompress(stored_data)
            except gzip.BadGzipFile:
                # Maybe it wasn't compressed
                raw_data = stored_data
        else:
            raw_data = stored_data
        
        # Verify checksum
        actual_sha256 = self._compute_sha256(raw_data)
        if actual_sha256 != ref.sha256:
            raise ArtifactCorruptedError(ref, ref.sha256, actual_sha256)
        
        # Deserialize
        return self._deserialize(raw_data, ref.codec)
    
    def delete(self, ref: ArtifactRef) -> bool:
        """Delete an artifact."""
        blob_path = self._parse_uri(ref.uri)
        
        if not blob_path.exists():
            return False
        
        blob_path.unlink()
        logger.debug(f"Artifact deleted: {blob_path}")
        
        # Remove from index
        if self.use_index:
            try:
                from .index import delete_artifact_ref
                delete_artifact_ref(ref)
            except Exception as e:
                logger.warning(f"Failed to remove artifact from index: {e}")
        
        return True
    
    def list_by_run(self, run_id: str) -> List[ArtifactRef]:
        """List all artifacts for a run."""
        # First try the Postgres index
        if self.use_index:
            try:
                from .index import list_artifacts_by_run
                return list_artifacts_by_run(run_id)
            except Exception as e:
                logger.warning(f"Failed to query artifact index, falling back to manifest: {e}")
        
        # Fall back to local manifest
        run_dir = self._get_run_dir(run_id)
        manifest_path = run_dir / "manifest.json"
        
        if manifest_path.exists():
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
            return [ArtifactRef.from_dict(entry) for entry in manifest.get("artifacts", [])]
        
        return []
    
    def delete_by_run(self, run_id: str) -> int:
        """Delete all artifacts for a run."""
        refs = self.list_by_run(run_id)
        deleted = 0
        
        for ref in refs:
            if self.delete(ref):
                deleted += 1
        
        # Remove the run directory if empty
        run_dir = self._get_run_dir(run_id)
        try:
            # Remove manifest
            manifest_path = run_dir / "manifest.json"
            if manifest_path.exists():
                manifest_path.unlink()
            # Remove directory if empty
            if run_dir.exists() and not any(run_dir.iterdir()):
                run_dir.rmdir()
        except Exception as e:
            logger.warning(f"Failed to clean up run directory: {e}")
        
        logger.info(f"Deleted {deleted} artifacts for run {run_id}")
        return deleted
    
    def purge_older_than(self, days: int, dry_run: bool = False) -> List[str]:
        """
        Delete all artifact runs older than N days.
        
        Item D Production Hardening: Provides automatic cleanup of old artifacts
        to prevent disk fill-up over time.
        
        Uses manifest.json 'created_at' timestamp to determine age. If manifest
        is missing or malformed, uses file modification time as fallback.
        
        Args:
            days: Delete runs older than this many days
            dry_run: If True, return list of runs that would be deleted without deleting
            
        Returns:
            List of run_ids that were deleted (or would be deleted in dry_run mode)
            
        Example:
            # Purge runs older than 30 days
            deleted_runs = store.purge_older_than(30)
            
            # Check what would be deleted without actually deleting
            would_delete = store.purge_older_than(7, dry_run=True)
        """
        from datetime import timedelta
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        purged_runs: List[str] = []
        
        if not self.root.exists():
            logger.debug("Artifact root directory doesn't exist, nothing to purge")
            return purged_runs
        
        for run_dir in self.root.iterdir():
            if not run_dir.is_dir():
                continue
            
            run_id = run_dir.name
            created_at = None
            
            # Try to get creation time from manifest
            manifest_path = run_dir / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, "r") as f:
                        manifest = json.load(f)
                    created_str = manifest.get("created_at")
                    if created_str:
                        created_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                except (json.JSONDecodeError, ValueError, KeyError) as e:
                    logger.warning(f"Failed to parse manifest for run {run_id}: {e}")
            
            # Fall back to directory modification time if manifest unavailable
            if created_at is None:
                mtime = run_dir.stat().st_mtime
                created_at = datetime.fromtimestamp(mtime, tz=timezone.utc)
            
            # Check if run is old enough to purge
            if created_at < cutoff:
                if dry_run:
                    logger.info(f"[dry_run] Would purge run {run_id} (created: {created_at.isoformat()})")
                else:
                    try:
                        self.delete_by_run(run_id)
                        logger.info(f"Purged old run {run_id} (created: {created_at.isoformat()})")
                    except Exception as e:
                        logger.error(f"Failed to purge run {run_id}: {e}")
                        continue
                purged_runs.append(run_id)
        
        if not dry_run:
            logger.info(f"Purged {len(purged_runs)} artifact runs older than {days} days")
        else:
            logger.info(f"[dry_run] Would purge {len(purged_runs)} artifact runs older than {days} days")
        
        return purged_runs
    
    def exists(self, ref: ArtifactRef) -> bool:
        """Check if an artifact exists."""
        blob_path = self._parse_uri(ref.uri)
        return blob_path.exists()
    
    def _update_manifest(self, run_dir: Path, ref: ArtifactRef) -> None:
        """
        Update the local manifest file.
        
        This provides a fallback for artifact discovery if the Postgres
        index is unavailable.
        """
        manifest_path = run_dir / "manifest.json"
        
        # Load existing manifest
        if manifest_path.exists():
            with open(manifest_path, "r") as f:
                manifest = json.load(f)
        else:
            manifest = {"artifacts": [], "created_at": datetime.now(timezone.utc).isoformat()}
        
        # Add or update artifact entry
        artifacts = manifest.get("artifacts", [])
        
        # Remove existing entry for same key (if any)
        artifacts = [a for a in artifacts if a.get("key") != ref.key]
        
        # Add new entry
        artifacts.append(ref.to_dict())
        manifest["artifacts"] = artifacts
        manifest["updated_at"] = datetime.now(timezone.utc).isoformat()
        
        # Atomic write
        fd, tmp_path = tempfile.mkstemp(dir=run_dir, suffix=".tmp")
        try:
            os.write(fd, json.dumps(manifest, indent=2).encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        
        os.rename(tmp_path, manifest_path)


# =============================================================================
# Factory function
# =============================================================================

_default_store: Optional[FilesystemArtifactStore] = None


def get_artifact_store(
    root: Optional[Path] = None,
    compress: bool = True,
    use_index: bool = True,
) -> ArtifactStore:
    """
    Get or create the default artifact store.
    
    This is the recommended way to obtain an ArtifactStore instance.
    The store is cached as a singleton unless explicit parameters are provided.
    
    Args:
        root: Custom artifact root (uses default if None)
        compress: Whether to compress artifacts
        use_index: Whether to use Postgres index
        
    Returns:
        ArtifactStore instance
    """
    global _default_store
    
    # If custom parameters, create a new store
    if root is not None:
        return FilesystemArtifactStore(root=root, compress=compress, use_index=use_index)
    
    # Otherwise use/create singleton
    if _default_store is None:
        _default_store = FilesystemArtifactStore(compress=compress, use_index=use_index)
    
    return _default_store


def reset_artifact_store() -> None:
    """Reset the default artifact store (for testing)."""
    global _default_store
    _default_store = None

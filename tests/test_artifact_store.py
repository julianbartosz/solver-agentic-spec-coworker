"""
Tests for artifact store (persistence/artifacts/).

Tests cover:
1. ArtifactRef serialization/deserialization
2. FilesystemArtifactStore put/get/delete operations
3. Atomic write guarantees
4. Checksum verification
5. Postgres index operations (when available)
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from integration_coworker.persistence.artifacts.base import (
    ArtifactRef,
    ArtifactCodec,
    ArtifactNotFoundError,
    ArtifactCorruptedError,
    ArtifactWriteError,
)
from integration_coworker.persistence.artifacts.fs import (
    FilesystemArtifactStore,
    get_artifact_store,
    reset_artifact_store,
)


# =============================================================================
# ArtifactRef Tests
# =============================================================================

class TestArtifactRef:
    """Tests for ArtifactRef dataclass."""
    
    def test_to_dict_includes_marker(self):
        """to_dict should include __artifact_ref__ marker for deserialization."""
        ref = ArtifactRef(
            run_id="run-123",
            key="openapi_spec",
            uri="file:///tmp/test.json",
            sha256="abc123",
            size_bytes=1024,
        )
        d = ref.to_dict()
        
        assert d["__artifact_ref__"] is True
        assert d["run_id"] == "run-123"
        assert d["key"] == "openapi_spec"
        assert d["uri"] == "file:///tmp/test.json"
        assert d["sha256"] == "abc123"
        assert d["size_bytes"] == 1024
    
    def test_from_dict_roundtrip(self):
        """from_dict should reconstruct the original ArtifactRef."""
        original = ArtifactRef(
            run_id="run-456",
            key="spec_documents",
            uri="file:///data/artifacts/run-456/spec_documents.json.gz",
            sha256="deadbeef" * 8,
            size_bytes=2048,
            content_type="application/json",
            codec=ArtifactCodec.JSON,
            metadata={"original_type": "list"},
        )
        
        d = original.to_dict()
        restored = ArtifactRef.from_dict(d)
        
        assert restored.run_id == original.run_id
        assert restored.key == original.key
        assert restored.uri == original.uri
        assert restored.sha256 == original.sha256
        assert restored.size_bytes == original.size_bytes
        assert restored.codec == original.codec
        assert restored.metadata == original.metadata
    
    def test_is_artifact_ref(self):
        """is_artifact_ref should detect artifact reference dicts."""
        ref = ArtifactRef(
            run_id="run-1",
            key="test",
            uri="file:///test",
            sha256="abc",
            size_bytes=100,
        )
        
        assert ArtifactRef.is_artifact_ref(ref.to_dict())
        assert not ArtifactRef.is_artifact_ref({"some": "dict"})
        assert not ArtifactRef.is_artifact_ref(None)
        assert not ArtifactRef.is_artifact_ref("string")


# =============================================================================
# FilesystemArtifactStore Tests
# =============================================================================

class TestFilesystemArtifactStore:
    """Tests for FilesystemArtifactStore."""
    
    @pytest.fixture
    def temp_store(self, tmp_path):
        """Create a store with temporary directory."""
        store = FilesystemArtifactStore(
            root=tmp_path / "artifacts",
            compress=True,
            use_index=False,  # Disable Postgres index for unit tests
        )
        return store
    
    @pytest.fixture
    def uncompressed_store(self, tmp_path):
        """Create a store without compression."""
        store = FilesystemArtifactStore(
            root=tmp_path / "artifacts",
            compress=False,
            use_index=False,
        )
        return store
    
    def test_put_get_simple_dict(self, temp_store):
        """Basic put/get roundtrip with a simple dict."""
        run_id = "test-run-1"
        key = "openapi_spec"
        value = {"openapi": "3.0.0", "info": {"title": "Test API"}}
        
        ref = temp_store.put(run_id, key, value)
        
        assert ref.run_id == run_id
        assert ref.key == key
        assert ref.uri.startswith("file://")
        assert ref.size_bytes > 0
        assert len(ref.sha256) == 64  # SHA256 hex length
        
        restored = temp_store.get(ref)
        assert restored == value
    
    def test_put_get_large_data(self, temp_store):
        """Put/get with large data (simulating real OpenAPI specs)."""
        run_id = "test-run-2"
        key = "spec_documents"
        # Create a ~1MB payload
        value = [{"content": "x" * 10000} for _ in range(100)]
        
        ref = temp_store.put(run_id, key, value)
        
        # Should be compressed significantly
        assert ref.size_bytes > 100000  # Original ~1MB uncompressed
        
        restored = temp_store.get(ref)
        assert restored == value
    
    def test_put_get_list(self, temp_store):
        """Put/get with list value."""
        ref = temp_store.put(
            "run-3",
            "endpoints",
            [{"path": "/users", "method": "GET"}, {"path": "/posts", "method": "POST"}],
        )
        
        restored = temp_store.get(ref)
        assert len(restored) == 2
        assert restored[0]["path"] == "/users"
    
    def test_put_idempotent_content_addressed(self, temp_store):
        """Putting the same content twice should use the same blob."""
        value = {"test": "data"}
        
        ref1 = temp_store.put("run-1", "key-a", value)
        ref2 = temp_store.put("run-1", "key-a", value)  # Same key
        
        # Same sha256
        assert ref1.sha256 == ref2.sha256
        
        # Same URI (content-addressed)
        assert ref1.uri == ref2.uri
    
    def test_get_not_found_raises(self, temp_store):
        """Get on non-existent artifact should raise ArtifactNotFoundError."""
        fake_ref = ArtifactRef(
            run_id="nonexistent",
            key="fake",
            uri="file:///nonexistent/path.json.gz",
            sha256="abc123",
            size_bytes=100,
        )
        
        with pytest.raises(ArtifactNotFoundError) as exc:
            temp_store.get(fake_ref)
        
        assert exc.value.ref == fake_ref
    
    def test_get_corrupted_raises(self, temp_store):
        """Get with corrupted content should raise ArtifactCorruptedError."""
        # First, store something
        ref = temp_store.put("run-corrupt", "test", {"valid": "data"})
        
        # Now corrupt the file by modifying the sha256 in the ref
        corrupted_ref = ArtifactRef(
            run_id=ref.run_id,
            key=ref.key,
            uri=ref.uri,
            sha256="0000000000000000000000000000000000000000000000000000000000000000",
            size_bytes=ref.size_bytes,
            codec=ref.codec,
        )
        
        with pytest.raises(ArtifactCorruptedError) as exc:
            temp_store.get(corrupted_ref)
        
        assert exc.value.expected_sha256 == corrupted_ref.sha256
    
    def test_delete_existing(self, temp_store):
        """Delete should remove artifact and return True."""
        ref = temp_store.put("run-del", "test", {"delete": "me"})
        
        assert temp_store.exists(ref)
        assert temp_store.delete(ref)
        assert not temp_store.exists(ref)
    
    def test_delete_nonexistent(self, temp_store):
        """Delete on non-existent artifact should return False."""
        fake_ref = ArtifactRef(
            run_id="fake",
            key="fake",
            uri="file:///fake/path.json.gz",
            sha256="abc",
            size_bytes=0,
        )
        
        assert not temp_store.delete(fake_ref)
    
    def test_list_by_run(self, temp_store):
        """list_by_run should return all artifacts for a run."""
        run_id = "run-list"
        
        temp_store.put(run_id, "field1", {"a": 1})
        temp_store.put(run_id, "field2", {"b": 2})
        temp_store.put(run_id, "field3", {"c": 3})
        
        refs = temp_store.list_by_run(run_id)
        
        assert len(refs) == 3
        keys = {ref.key for ref in refs}
        assert keys == {"field1", "field2", "field3"}
    
    def test_delete_by_run(self, temp_store):
        """delete_by_run should remove all artifacts for a run."""
        run_id = "run-cleanup"
        
        temp_store.put(run_id, "a", {"x": 1})
        temp_store.put(run_id, "b", {"y": 2})
        temp_store.put("other-run", "c", {"z": 3})
        
        deleted = temp_store.delete_by_run(run_id)
        
        assert deleted == 2
        assert len(temp_store.list_by_run(run_id)) == 0
        assert len(temp_store.list_by_run("other-run")) == 1
    
    def test_exists(self, temp_store):
        """exists should correctly detect artifact presence."""
        ref = temp_store.put("run-exists", "test", {"hello": "world"})
        
        assert temp_store.exists(ref)
        
        temp_store.delete(ref)
        assert not temp_store.exists(ref)
    
    def test_uncompressed_mode(self, uncompressed_store):
        """Store should work without compression."""
        ref = uncompressed_store.put("run-unc", "data", {"no": "compression"})
        
        # URI should end with .json (not .gz)
        assert ref.uri.endswith(".json")
        
        restored = uncompressed_store.get(ref)
        assert restored == {"no": "compression"}
    
    def test_metadata_preserved(self, temp_store):
        """Metadata should be preserved in artifact reference."""
        metadata = {"original_type": "dict", "source_field": "openapi_spec"}
        
        ref = temp_store.put(
            "run-meta",
            "spec",
            {"api": "data"},
            metadata=metadata,
        )
        
        assert ref.metadata == metadata
    
    def test_codec_pickle(self, temp_store):
        """Pickle codec should work for complex objects."""
        # Create an object that can't be JSON serialized
        from datetime import date
        value = {"date": date(2025, 1, 1), "set": {1, 2, 3}}
        
        ref = temp_store.put(
            "run-pickle",
            "complex",
            value,
            codec=ArtifactCodec.PICKLE,
        )
        
        assert ref.codec == ArtifactCodec.PICKLE
        
        restored = temp_store.get(ref)
        assert restored["date"] == date(2025, 1, 1)
        assert restored["set"] == {1, 2, 3}
    
    def test_atomic_write_survives_crash(self, tmp_path):
        """Atomic write should not leave partial files on failure."""
        store = FilesystemArtifactStore(
            root=tmp_path / "artifacts",
            compress=True,
            use_index=False,
        )
        
        # Mock os.rename to simulate crash after tmp write
        original_rename = os.rename
        
        def failing_rename(src, dst):
            raise IOError("Simulated disk failure")
        
        with patch("os.rename", side_effect=failing_rename):
            with pytest.raises(ArtifactWriteError):
                store.put("run-crash", "data", {"should": "fail"})
        
        # Verify no partial files exist
        run_dir = tmp_path / "artifacts" / "run-crash"
        if run_dir.exists():
            files = list(run_dir.glob("*.json*"))
            # Only manifest might exist
            assert all("manifest" in str(f) for f in files)
    
    def test_run_id_sanitization(self, temp_store):
        """Run IDs with special characters should be sanitized."""
        # Run IDs might contain slashes from LangSmith traces
        ref = temp_store.put("run/with/slashes", "test", {"ok": True})
        
        # Should work without errors
        restored = temp_store.get(ref)
        assert restored == {"ok": True}


# =============================================================================
# Factory Function Tests
# =============================================================================

class TestGetArtifactStore:
    """Tests for get_artifact_store factory."""
    
    def test_singleton_behavior(self, tmp_path):
        """Default store should be singleton."""
        reset_artifact_store()
        
        with patch.dict(os.environ, {"ARTIFACT_STORE_ROOT": str(tmp_path / "singleton")}):
            store1 = get_artifact_store()
            store2 = get_artifact_store()
            
            assert store1 is store2
        
        reset_artifact_store()
    
    def test_custom_root_creates_new(self, tmp_path):
        """Explicit root should create new store."""
        reset_artifact_store()
        
        store1 = get_artifact_store()
        store2 = get_artifact_store(root=tmp_path / "custom")
        
        assert store1 is not store2
        
        reset_artifact_store()


# =============================================================================
# Integration with Checkpoint System (Preview)
# =============================================================================

class TestCheckpointIntegration:
    """Preview tests for checkpoint integration (Step 2/4)."""
    
    @pytest.fixture
    def store(self, tmp_path):
        """Create store for integration tests."""
        return FilesystemArtifactStore(
            root=tmp_path / "checkpoint_artifacts",
            compress=True,
            use_index=False,
        )
    
    def test_simulated_checkpoint_exclude_and_replace(self, store):
        """
        Simulate the checkpoint serialize pattern:
        - Large field is excluded from checkpoint JSON
        - Replaced with ArtifactRef
        - Restored on resume
        """
        run_id = "integration-test-run"
        
        # Simulated large field (OpenAPI spec)
        large_spec = {
            "openapi": "3.0.0",
            "info": {"title": "Large API", "version": "1.0.0"},
            "paths": {f"/endpoint{i}": {"get": {"summary": f"Get {i}"}} for i in range(100)},
            "components": {"schemas": {f"Schema{i}": {"type": "object"} for i in range(50)}},
        }
        
        # Step 1: Store the large field and get reference
        ref = store.put(run_id, "openapi_spec", large_spec)
        
        # Step 2: Checkpoint JSON would contain the ArtifactRef instead
        checkpoint_data = {
            "run_id": run_id,
            "completed_steps": ["parse_spec", "extract_endpoints"],
            "openapi_spec": ref.to_dict(),  # Reference, not the full spec
        }
        
        # Verify checkpoint is small
        checkpoint_json = json.dumps(checkpoint_data)
        assert len(checkpoint_json) < 1000  # Much smaller than original spec
        
        # Step 3: On resume, detect and rehydrate
        if ArtifactRef.is_artifact_ref(checkpoint_data["openapi_spec"]):
            restored_ref = ArtifactRef.from_dict(checkpoint_data["openapi_spec"])
            restored_spec = store.get(restored_ref)
            
            # Verify full spec is restored
            assert restored_spec == large_spec
            assert len(restored_spec["paths"]) == 100
    
    def test_multiple_fields_excluded(self, store):
        """Test excluding multiple large fields."""
        run_id = "multi-field-run"
        
        fields_to_spool = {
            "openapi_spec": {"large": "spec" * 1000},
            "spec_documents": [{"doc": i} for i in range(100)],
            "files": {"file1.py": "content" * 500},
        }
        
        refs = {}
        for key, value in fields_to_spool.items():
            refs[key] = store.put(run_id, key, value)
        
        # All should be stored
        assert len(refs) == 3
        
        # All should be retrievable
        for key, ref in refs.items():
            restored = store.get(ref)
            assert restored == fields_to_spool[key]
        
        # Cleanup should remove all
        deleted = store.delete_by_run(run_id)
        assert deleted == 3


# =============================================================================
# Item D: Artifact Retention & Cleanup Tests
# =============================================================================

class TestArtifactPurge:
    """Tests for purge_older_than functionality (Item D Production Hardening)."""
    
    @pytest.fixture
    def store_with_old_runs(self, tmp_path):
        """Create store with runs of varying ages."""
        from datetime import timedelta
        
        store = FilesystemArtifactStore(
            root=tmp_path / "purge_test_artifacts",
            compress=True,
            use_index=False,
        )
        
        # Create recent run (should NOT be purged)
        recent_ref = store.put("recent-run", "data", {"recent": True})
        
        # Create old run by manipulating manifest timestamp
        old_ref = store.put("old-run", "data", {"old": True})
        old_run_dir = store.root / "old-run"
        manifest_path = old_run_dir / "manifest.json"
        
        # Backdate the manifest to 45 days ago
        old_time = datetime.now(timezone.utc) - timedelta(days=45)
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
        manifest["created_at"] = old_time.isoformat()
        with open(manifest_path, "w") as f:
            json.dump(manifest, f)
        
        # Create another old run (35 days ago)
        another_old_ref = store.put("another-old-run", "data", {"also_old": True})
        another_old_dir = store.root / "another-old-run"
        another_manifest_path = another_old_dir / "manifest.json"
        
        old_time_35 = datetime.now(timezone.utc) - timedelta(days=35)
        with open(another_manifest_path, "r") as f:
            manifest = json.load(f)
        manifest["created_at"] = old_time_35.isoformat()
        with open(another_manifest_path, "w") as f:
            json.dump(manifest, f)
        
        return store
    
    def test_purge_older_than_removes_old_runs(self, store_with_old_runs):
        """purge_older_than should delete runs older than specified days."""
        store = store_with_old_runs
        
        # Purge runs older than 30 days
        purged = store.purge_older_than(30)
        
        # Should have purged 2 old runs
        assert len(purged) == 2
        assert "old-run" in purged
        assert "another-old-run" in purged
        assert "recent-run" not in purged
        
        # Recent run should still exist
        assert (store.root / "recent-run").exists()
        
        # Old runs should be gone
        assert not (store.root / "old-run").exists()
        assert not (store.root / "another-old-run").exists()
    
    def test_purge_dry_run_doesnt_delete(self, store_with_old_runs):
        """dry_run=True should list but not delete."""
        store = store_with_old_runs
        
        # Dry run
        purged = store.purge_older_than(30, dry_run=True)
        
        # Should report 2 would be deleted
        assert len(purged) == 2
        
        # But all runs should still exist
        assert (store.root / "recent-run").exists()
        assert (store.root / "old-run").exists()
        assert (store.root / "another-old-run").exists()
    
    def test_purge_with_higher_threshold_keeps_more(self, store_with_old_runs):
        """Higher day threshold should keep more runs."""
        store = store_with_old_runs
        
        # Purge runs older than 40 days (only old-run is 45 days old)
        purged = store.purge_older_than(40)
        
        # Should only purge the 45-day-old run
        assert len(purged) == 1
        assert "old-run" in purged
        
        # 35-day-old run should still exist
        assert (store.root / "another-old-run").exists()
    
    def test_purge_empty_directory(self, tmp_path):
        """Purging an empty store should work without error."""
        store = FilesystemArtifactStore(
            root=tmp_path / "empty_artifacts",
            compress=True,
            use_index=False,
        )
        # Don't create any runs
        
        purged = store.purge_older_than(30)
        assert purged == []
    
    def test_purge_missing_manifest_uses_mtime(self, tmp_path):
        """When manifest is missing, should fall back to directory mtime."""
        from datetime import timedelta
        import time
        
        store = FilesystemArtifactStore(
            root=tmp_path / "no_manifest_artifacts",
            compress=True,
            use_index=False,
        )
        
        # Create a run and delete its manifest
        ref = store.put("no-manifest-run", "data", {"test": True})
        manifest_path = store.root / "no-manifest-run" / "manifest.json"
        manifest_path.unlink()
        
        # The run is recent, so shouldn't be purged
        purged = store.purge_older_than(30)
        assert "no-manifest-run" not in purged
        assert (store.root / "no-manifest-run").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

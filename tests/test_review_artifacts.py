"""
Tests for review_artifacts module.

These tests verify the refs-not-blobs discipline for HITL interrupt payloads:
- Large data (diffs, sandbox results, code snapshots) stored externally
- interrupt() payloads contain only refs + bounded summaries
- JSON codec only (no pickle)
- Roundtrip invariants hold: from_dict(ref.to_dict()) == ref
"""

import pytest
import sys
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

# Import module under test
from integration_coworker.graph.review_artifacts import (
    REVIEW_ARTIFACT_CODEC,
    MAX_FILES_IN_SUMMARY,
    MAX_ERROR_PREVIEW_CHARS,
    store_review_artifacts,
    compute_unified_diff_patches,
    load_review_artifact,
    load_review_artifact_from_dict,
    build_code_summary,
    build_sandbox_summary,
    refs_to_dicts,
    dicts_to_refs,
)
from integration_coworker.persistence.artifacts.base import ArtifactRef, ArtifactCodec


# =============================================================================
# Test Constants
# =============================================================================

class TestCodecConstraints:
    """Verify JSON-only codec constraint."""
    
    def test_review_artifact_codec_is_json(self):
        """CRITICAL: Review artifacts must use JSON codec only (no pickle)."""
        assert REVIEW_ARTIFACT_CODEC == ArtifactCodec.JSON
        assert REVIEW_ARTIFACT_CODEC != ArtifactCodec.PICKLE


class TestArtifactRefRoundtrip:
    """Verify ArtifactRef serialization roundtrip."""
    
    def test_artifact_ref_to_dict_from_dict_roundtrip(self):
        """ArtifactRef must survive JSON roundtrip."""
        ref = ArtifactRef(
            run_id="test-run",
            key="review_pre_write_snapshot",
            uri="file:///tmp/artifacts/abc123",
            sha256="deadbeef" * 8,
            size_bytes=1234,
            codec=ArtifactCodec.JSON,
        )
        
        # Serialize to dict
        ref_dict = ref.to_dict()
        assert isinstance(ref_dict, dict)
        
        # JSON roundtrip (simulates checkpoint storage)
        json_str = json.dumps(ref_dict)
        restored_dict = json.loads(json_str)
        
        # Deserialize back to ArtifactRef
        restored_ref = ArtifactRef.from_dict(restored_dict)
        
        # Verify equality
        assert restored_ref.run_id == ref.run_id
        assert restored_ref.key == ref.key
        assert restored_ref.codec == ref.codec
        assert restored_ref.sha256 == ref.sha256
        assert restored_ref.size_bytes == ref.size_bytes
    
    def test_refs_to_dicts_roundtrip(self):
        """Dict of refs must survive roundtrip."""
        refs = {
            "code_snapshot": ArtifactRef(
                run_id="run1",
                key="snapshot",
                uri="file:///tmp/a",
                sha256="a" * 64,
                size_bytes=100,
                codec=ArtifactCodec.JSON,
            ),
            "diff_patches": ArtifactRef(
                run_id="run1",
                key="patches",
                uri="file:///tmp/b",
                sha256="b" * 64,
                size_bytes=200,
                codec=ArtifactCodec.JSON,
            ),
        }
        
        # Convert to dicts
        ref_dicts = refs_to_dicts(refs)
        
        # JSON roundtrip
        json_str = json.dumps(ref_dicts)
        restored_dicts = json.loads(json_str)
        
        # Convert back to refs
        restored_refs = dicts_to_refs(restored_dicts)
        
        # Verify
        assert set(restored_refs.keys()) == set(refs.keys())
        for key in refs:
            assert restored_refs[key].run_id == refs[key].run_id
            assert restored_refs[key].key == refs[key].key
            assert restored_refs[key].codec == refs[key].codec


# =============================================================================
# Test Bounded Summaries
# =============================================================================

@dataclass
class MockCodeArtifact:
    """Mock CodeArtifact for testing."""
    rel_path: str
    content: str
    artifact_type: str = "client_code"


class TestBoundedCodeSummary:
    """Test build_code_summary produces bounded output."""
    
    def test_empty_artifacts(self):
        """Empty list produces minimal summary."""
        summary = build_code_summary(None)
        assert summary["file_count"] == 0
        assert summary["files"] == []
        assert summary["files_truncated"] is False
    
    def test_single_file(self):
        """Single file produces correct summary."""
        artifacts = [MockCodeArtifact("src/main.py", "print('hello')", "client_code")]
        summary = build_code_summary(artifacts)
        
        assert summary["file_count"] == 1
        assert summary["adds"] == 1
        assert summary["total_bytes"] == len("print('hello')")
        assert len(summary["files"]) == 1
        assert summary["files"][0]["path"] == "src/main.py"
    
    def test_truncates_large_file_list(self):
        """File list is truncated to MAX_FILES_IN_SUMMARY."""
        # Create more than MAX_FILES_IN_SUMMARY artifacts
        artifacts = [
            MockCodeArtifact(f"src/file_{i}.py", f"content {i}")
            for i in range(MAX_FILES_IN_SUMMARY + 10)
        ]
        
        summary = build_code_summary(artifacts)
        
        # Summary must be bounded
        assert summary["file_count"] == MAX_FILES_IN_SUMMARY + 10
        assert len(summary["files"]) == MAX_FILES_IN_SUMMARY
        assert summary["files_truncated"] is True
    
    def test_summary_json_serializable(self):
        """Summary must be JSON-serializable for payload."""
        artifacts = [
            MockCodeArtifact("src/main.py", "print('hello')" * 100),
            MockCodeArtifact("src/utils.py", "def foo(): pass"),
        ]
        summary = build_code_summary(artifacts)
        
        # Must not raise
        json_str = json.dumps(summary)
        restored = json.loads(json_str)
        assert restored["file_count"] == 2


class TestBoundedSandboxSummary:
    """Test build_sandbox_summary produces bounded output."""
    
    def test_empty_result(self):
        """Empty result produces success summary."""
        summary = build_sandbox_summary(None)
        assert summary["all_passed"] is True
        assert summary["gates_failed"] == []
        assert summary["error_previews"] == []
    
    def test_success_result(self):
        """Successful sandbox produces success summary."""
        result = {
            "all_gates_passed": True,
            "gate_results": [
                {"gate": "syntax", "passed": True},
                {"gate": "imports", "passed": True},
            ],
        }
        summary = build_sandbox_summary(result)
        
        assert summary["all_passed"] is True
        assert summary["gates_failed"] == []
    
    def test_failure_result(self):
        """Failed sandbox produces bounded error summary."""
        result = {
            "all_gates_passed": False,
            "gate_results": [
                {"gate": "syntax", "passed": True},
                {"gate": "types", "passed": False},
                {"gate": "tests", "passed": False},
            ],
            "errors": [
                {"gate": "types", "message": "Type error in main.py: " + "x" * 500},
                {"gate": "tests", "message": "AssertionError: expected True"},
            ],
        }
        summary = build_sandbox_summary(result)
        
        assert summary["all_passed"] is False
        assert "types" in summary["gates_failed"]
        assert "tests" in summary["gates_failed"]
        
        # Error previews must be bounded
        for preview in summary["error_previews"]:
            assert len(preview["message"]) <= MAX_ERROR_PREVIEW_CHARS
    
    def test_summary_json_serializable(self):
        """Summary must be JSON-serializable."""
        result = {
            "all_gates_passed": False,
            "gate_results": [{"gate": "syntax", "passed": False}],
            "errors": [{"gate": "syntax", "message": "error"}],
        }
        summary = build_sandbox_summary(result)
        
        # Must not raise
        json.dumps(summary)


# =============================================================================
# Test Unified Diff Patches
# =============================================================================

class TestUnifiedDiffPatches:
    """Test compute_unified_diff_patches."""
    
    def test_new_file(self):
        """New file produces correct diff."""
        artifacts = [MockCodeArtifact("src/new.py", "print('new')")]
        existing = {}
        
        patches = compute_unified_diff_patches(artifacts, existing)
        
        assert len(patches) == 1
        assert "--- a/src/new.py" in patches[0]
        assert "+++ b/src/new.py" in patches[0]
        assert "+print('new')" in patches[0]
    
    def test_modified_file(self):
        """Modified file produces unified diff."""
        artifacts = [MockCodeArtifact("src/main.py", "print('updated')")]
        existing = {"src/main.py": "print('original')"}
        
        patches = compute_unified_diff_patches(artifacts, existing)
        
        assert len(patches) == 1
        assert "-print('original')" in patches[0]
        assert "+print('updated')" in patches[0]
    
    def test_unchanged_file_no_patch(self):
        """Unchanged file produces no patch."""
        artifacts = [MockCodeArtifact("src/main.py", "print('same')")]
        existing = {"src/main.py": "print('same')"}
        
        patches = compute_unified_diff_patches(artifacts, existing)
        
        assert len(patches) == 0
    
    def test_patches_are_text_not_blobs(self):
        """Patches are text strings, not structured before/after blobs."""
        artifacts = [
            MockCodeArtifact("src/a.py", "a"),
            MockCodeArtifact("src/b.py", "b"),
        ]
        existing = {"src/a.py": "", "src/b.py": ""}
        
        patches = compute_unified_diff_patches(artifacts, existing)
        
        # All patches must be strings
        for patch in patches:
            assert isinstance(patch, str)
            assert "@@" in patch  # Unified diff hunk marker


# =============================================================================
# Test Store/Load Integration
# =============================================================================

class TestStoreLoadIntegration:
    """Test store_review_artifacts with mock artifact store."""
    
    @pytest.fixture
    def mock_store(self):
        """Create a mock artifact store."""
        store = MagicMock()
        stored_data = {}
        
        def put(run_id, name, data, codec):
            key = f"{run_id}/{name}"
            stored_data[key] = data
            return ArtifactRef(
                run_id=run_id,
                key=name,
                uri=f"file:///tmp/{key}",
                sha256="mock_hash" + "0" * 56,  # SHA256 is 64 hex chars
                size_bytes=len(json.dumps(data)),
                codec=codec,
            )
        
        def get(ref):
            key = f"{ref.run_id}/{ref.key}"
            return stored_data.get(key)
        
        store.put = MagicMock(side_effect=put)
        store.get = MagicMock(side_effect=get)
        store._stored = stored_data
        
        return store
    
    def test_store_and_load_roundtrip(self, mock_store):
        """Stored artifacts can be loaded back."""
        with patch(
            'integration_coworker.graph.review_artifacts.get_artifact_store',
            return_value=mock_store
        ):
            artifacts = [MockCodeArtifact("src/main.py", "content")]
            diff_patches = ["--- a/src/main.py\n+++ b/src/main.py\n+content"]
            
            # Store
            refs = store_review_artifacts(
                "run-123",
                "pre_write",
                code_artifacts=artifacts,
                diff_patches=diff_patches,
            )
            
            # Verify refs structure
            assert "code_snapshot" in refs
            assert "diff_patches" in refs
            
            # Load back via ref
            loaded_patches = load_review_artifact(refs["diff_patches"])
            assert loaded_patches == diff_patches
    
    def test_load_from_dict(self, mock_store):
        """Can load artifact from serialized ref dict."""
        with patch(
            'integration_coworker.graph.review_artifacts.get_artifact_store',
            return_value=mock_store
        ):
            diff_patches = ["patch content"]
            refs = store_review_artifacts(
                "run-456",
                "post_sandbox",
                diff_patches=diff_patches,
            )
            
            # Serialize ref to dict (as would happen in checkpoint)
            ref_dict = refs["diff_patches"].to_dict()
            
            # Load via dict
            loaded = load_review_artifact_from_dict(ref_dict)
            assert loaded == diff_patches


# =============================================================================
# Test Payload Size Invariants
# =============================================================================

class TestPayloadSizeInvariants:
    """Verify payload size stays bounded."""
    
    def test_code_summary_bounded_size(self):
        """Code summary stays under 2KB even with many files."""
        # Create 100 large files
        artifacts = [
            MockCodeArtifact(
                f"src/package/module/submodule/very_long_file_name_{i}.py",
                "x" * 10000,  # 10KB content each
            )
            for i in range(100)
        ]
        
        summary = build_code_summary(artifacts)
        summary_json = json.dumps(summary)
        
        # Summary must stay bounded (well under 2KB)
        assert len(summary_json) < 2000, f"Summary too large: {len(summary_json)} bytes"
    
    def test_sandbox_summary_bounded_size(self):
        """Sandbox summary stays under 2KB even with long errors."""
        result = {
            "all_gates_passed": False,
            "gate_results": [{"gate": f"gate_{i}", "passed": False} for i in range(20)],
            "errors": [
                {"gate": f"gate_{i}", "message": "x" * 10000}
                for i in range(50)
            ],
        }
        
        summary = build_sandbox_summary(result)
        summary_json = json.dumps(summary)
        
        # Summary must stay bounded
        assert len(summary_json) < 2000, f"Summary too large: {len(summary_json)} bytes"


# =============================================================================
# Test State Integration
# =============================================================================

class TestStateIntegration:
    """Test integration with WorkflowState fields."""
    
    def test_state_has_review_fields(self):
        """WorkflowState has required review artifact fields."""
        from integration_coworker.graph.state import WorkflowState
        
        # Create state
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
        )
        
        # Fields must exist with correct defaults
        assert hasattr(state, "review_artifact_refs")
        assert state.review_artifact_refs == {}
        
        assert hasattr(state, "pending_review_kind")
        assert state.pending_review_kind is None
        
        assert hasattr(state, "human_feedback")
        assert state.human_feedback is None
    
    def test_review_fields_json_serializable(self):
        """Review fields can be serialized for checkpoint."""
        from integration_coworker.graph.state import WorkflowState
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[],
            task_description="test",
        )
        
        # Populate review fields
        state.review_artifact_refs = {
            "pre_write": {
                "code_snapshot": {
                    "storage_key": "run/snapshot",
                    "codec": "json",
                    "sha256": "abc",
                    "size_bytes": 100,
                }
            }
        }
        state.pending_review_kind = "pre_write"
        state.human_feedback = "Please add error handling"
        
        # Must be JSON-serializable
        data = {
            "review_artifact_refs": state.review_artifact_refs,
            "pending_review_kind": state.pending_review_kind,
            "human_feedback": state.human_feedback,
        }
        json_str = json.dumps(data)
        restored = json.loads(json_str)
        
        assert restored["review_artifact_refs"] == state.review_artifact_refs
        assert restored["pending_review_kind"] == "pre_write"
        assert restored["human_feedback"] == "Please add error handling"

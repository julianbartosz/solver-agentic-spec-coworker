"""
Tests for Human Edit Models (PR #11)

Tests cover:
1. Patch parsing (unified diff)
2. Context validation (strict matching)
3. AST validation for Python
4. Bounded constraints
5. Audit entry generation
6. Edge cases and error handling
"""

import pytest
from datetime import datetime, timezone
from integration_coworker.graph.human_edit_models import (
    HumanEditPatch,
    PatchHunk,
    EditValidationResult,
    EditAuditEntry,
    HumanEditSummary,
    PatchParseError,
    PatchApplyError,
    EditValidationError,
    validate_patch,
    apply_patch,
    _parse_unified_diff,
    MAX_HUNKS_PER_PATCH,
    MAX_CHANGED_LINES,
    MAX_PATCH_BYTES,
    MAX_EDIT_REASON_LENGTH,
    # Decision schema validation
    DECISION_SCHEMA_VERSION,
    VALID_SANDBOX_ACTIONS,
    DecisionSchemaError,
    validate_sandbox_decision,
    create_sandbox_decision,
)


# =============================================================================
# Test Fixtures
# =============================================================================

@pytest.fixture
def simple_patch_text():
    """A minimal valid unified diff patch."""
    return """\
--- a/src/example.py
+++ b/src/example.py
@@ -1,5 +1,5 @@
 def hello():
-    return "hello"
+    return "world"
 
 def main():
     print(hello())
"""


@pytest.fixture
def simple_original():
    """Original content matching simple_patch_text."""
    return """\
def hello():
    return "hello"

def main():
    print(hello())
"""


@pytest.fixture
def multi_hunk_patch():
    """Patch with multiple hunks."""
    return """\
--- a/src/example.py
+++ b/src/example.py
@@ -1,5 +1,5 @@
 def hello():
-    return "hello"
+    return "world"
 
 def main():
     print(hello())
@@ -10,4 +10,5 @@ def goodbye():
 
 if __name__ == "__main__":
     main()
+    goodbye()
"""


@pytest.fixture
def multi_hunk_original():
    """Original content for multi_hunk_patch."""
    return """\
def hello():
    return "hello"

def main():
    print(hello())


def goodbye():
    return "goodbye"

if __name__ == "__main__":
    main()
"""


# =============================================================================
# Patch Parsing Tests
# =============================================================================

class TestPatchParsing:
    """Tests for unified diff parsing."""
    
    def test_parse_simple_patch(self, simple_patch_text):
        """Parse a minimal single-hunk patch."""
        hunks = _parse_unified_diff(simple_patch_text)
        
        assert len(hunks) == 1
        hunk = hunks[0]
        assert hunk.old_start == 1
        assert hunk.old_count == 5
        assert hunk.new_start == 1
        assert hunk.new_count == 5
        
        # Check change counts
        added, removed = hunk.count_changes()
        assert added == 1
        assert removed == 1
    
    def test_parse_multi_hunk_patch(self, multi_hunk_patch):
        """Parse a patch with multiple hunks."""
        hunks = _parse_unified_diff(multi_hunk_patch)
        
        assert len(hunks) == 2
        assert hunks[0].old_start == 1
        assert hunks[1].old_start == 10
    
    def test_parse_empty_patch_raises(self):
        """Empty patches should raise PatchParseError."""
        with pytest.raises(PatchParseError, match="No hunks found"):
            _parse_unified_diff("")
    
    def test_parse_header_only_raises(self):
        """Patch with only headers (no hunks) should raise."""
        patch = """\
--- a/file.py
+++ b/file.py
"""
        with pytest.raises(PatchParseError, match="No hunks found"):
            _parse_unified_diff(patch)
    
    def test_parse_no_newline_marker(self):
        """Handle '\ No newline at end of file' marker."""
        patch = """\
--- a/file.py
+++ b/file.py
@@ -1 +1 @@
-hello
\ No newline at end of file
+world
\ No newline at end of file
"""
        hunks = _parse_unified_diff(patch)
        assert len(hunks) == 1
        # The marker lines should be skipped
        content_without_markers = [l for l in hunks[0].content if not l.startswith('\\')]
        assert len(content_without_markers) == 2


# =============================================================================
# HumanEditPatch Tests
# =============================================================================

class TestHumanEditPatch:
    """Tests for HumanEditPatch model."""
    
    def test_create_valid_patch(self, simple_patch_text):
        """Create a valid patch."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Fix typo",
        )
        
        assert patch.file_path == "src/example.py"
        assert patch.reason == "Fix typo"
        assert patch.editor_id is None
        assert isinstance(patch.timestamp, datetime)
    
    def test_parse_updates_hunks(self, simple_patch_text):
        """parse() populates the hunks list."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        assert len(patch.hunks) == 0  # Not parsed yet
        patch.parse()
        assert len(patch.hunks) == 1
    
    def test_fingerprint_deterministic(self, simple_patch_text):
        """Fingerprint is deterministic for same content."""
        patch1 = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test 1",
        )
        patch2 = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test 2",  # Different reason
        )
        
        # Fingerprint based on file_path + patch_text only
        assert patch1.fingerprint() == patch2.fingerprint()
    
    def test_fingerprint_changes_with_content(self, simple_patch_text):
        """Fingerprint changes when content differs."""
        patch1 = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        patch2 = HumanEditPatch(
            file_path="src/other.py",  # Different file
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        assert patch1.fingerprint() != patch2.fingerprint()
    
    def test_path_traversal_rejected(self, simple_patch_text):
        """Path traversal attempts are rejected."""
        with pytest.raises(EditValidationError):
            HumanEditPatch(
                file_path="../../../etc/passwd",
                patch_text=simple_patch_text,
                reason="Test",
            )
    
    def test_reason_truncated(self, simple_patch_text):
        """Long reasons are truncated."""
        long_reason = "x" * (MAX_EDIT_REASON_LENGTH + 100)
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason=long_reason,
        )
        
        assert len(patch.reason) == MAX_EDIT_REASON_LENGTH
    
    def test_patch_bytes_limit(self):
        """Patches exceeding byte limit are rejected."""
        huge_patch = "+" + ("x" * MAX_PATCH_BYTES)
        
        with pytest.raises(EditValidationError, match="exceeds"):
            HumanEditPatch(
                file_path="src/example.py",
                patch_text=huge_patch,
                reason="Test",
            )
    
    def test_to_dict_roundtrip(self, simple_patch_text):
        """to_dict/from_dict roundtrip preserves data."""
        original = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test reason",
            editor_id="user123",
        )
        original.parse()
        
        data = original.to_dict()
        restored = HumanEditPatch.from_dict(data)
        
        assert restored.file_path == original.file_path
        assert restored.patch_text == original.patch_text
        assert restored.reason == original.reason
        assert restored.editor_id == original.editor_id
        assert len(restored.hunks) == len(original.hunks)


# =============================================================================
# Bounded Validation Tests
# =============================================================================

class TestBoundedValidation:
    """Tests for bounded constraints."""
    
    def test_max_hunks_enforced(self):
        """Patches with too many hunks are rejected."""
        # Create a patch with many hunks
        hunks = []
        for i in range(MAX_HUNKS_PER_PATCH + 1):
            hunks.append(f"""\
@@ -{i*10 + 1},1 +{i*10 + 1},1 @@
-line{i}
+LINE{i}
""")
        
        patch_text = """\
--- a/file.py
+++ b/file.py
""" + "\n".join(hunks)
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=patch_text,
            reason="Test",
        )
        
        with pytest.raises(PatchParseError, match="hunks"):
            patch.parse()
    
    def test_max_lines_enforced(self):
        """Patches changing too many lines are rejected."""
        # Create a patch that changes many lines
        changes = "\n".join([f"-line{i}\n+LINE{i}" for i in range(MAX_CHANGED_LINES)])
        patch_text = f"""\
--- a/file.py
+++ b/file.py
@@ -1,{MAX_CHANGED_LINES} +1,{MAX_CHANGED_LINES} @@
{changes}
"""
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=patch_text,
            reason="Test",
        )
        
        with pytest.raises(PatchParseError, match="lines"):
            patch.parse()


# =============================================================================
# Patch Validation Tests
# =============================================================================

class TestPatchValidation:
    """Tests for validate_patch()."""
    
    def test_validate_valid_patch(self, simple_patch_text, simple_original):
        """Valid patch should pass validation."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        result = validate_patch(patch, simple_original)
        
        assert result.valid is True
        assert len(result.errors) == 0
    
    def test_validate_context_mismatch_fails(self, simple_patch_text):
        """Context mismatch should fail validation."""
        wrong_original = """\
def hello():
    return "wrong"

def main():
    print(hello())
"""
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        result = validate_patch(patch, wrong_original)
        
        assert result.valid is False
        assert any("mismatch" in e.lower() or "context" in e.lower() for e in result.errors)
    
    def test_validate_python_syntax_error_fails(self):
        """Patch resulting in invalid Python syntax should fail."""
        patch_text = """\
--- a/src/example.py
+++ b/src/example.py
@@ -1,3 +1,3 @@
 def hello():
-    return "hello"
+    return "hello"  # missing closing paren will break
 
"""
        # But this won't actually cause a syntax error, let's make a real one
        patch_text_bad_syntax = """\
--- a/src/example.py
+++ b/src/example.py
@@ -1,3 +1,3 @@
 def hello():
-    return "hello"
+    return "hello
 
"""
        original = """\
def hello():
    return "hello"

"""
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=patch_text_bad_syntax,
            reason="Test",
        )
        
        result = validate_patch(patch, original)
        
        assert result.valid is False
        assert any("syntax" in e.lower() for e in result.errors)
    
    def test_validate_non_python_skips_ast(self):
        """Non-Python files skip AST validation."""
        patch_text = """\
--- a/README.md
+++ b/README.md
@@ -1,3 +1,3 @@
 # Title
-Old text
+New text
 
"""
        original = """\
# Title
Old text

"""
        
        patch = HumanEditPatch(
            file_path="docs/README.md",
            patch_text=patch_text,
            reason="Test",
        )
        
        result = validate_patch(patch, original)
        
        assert result.valid is True


# =============================================================================
# Optimistic Concurrency Tests (expected_base_sha256)
# =============================================================================

class TestOptimisticConcurrency:
    """Tests for expected_base_sha256 concurrency control."""
    
    def test_expected_base_sha256_valid_format(self, simple_patch_text):
        """Valid SHA256 hex string is accepted."""
        import hashlib
        content = "test content"
        sha256 = hashlib.sha256(content.encode('utf-8')).hexdigest()
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
            expected_base_sha256=sha256,
        )
        
        assert patch.expected_base_sha256 == sha256.lower()
    
    def test_expected_base_sha256_normalized_to_lowercase(self, simple_patch_text):
        """SHA256 is normalized to lowercase."""
        sha256_upper = "A" * 64
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
            expected_base_sha256=sha256_upper,
        )
        
        assert patch.expected_base_sha256 == "a" * 64
    
    def test_expected_base_sha256_invalid_length_rejected(self, simple_patch_text):
        """Invalid SHA256 length is rejected."""
        with pytest.raises(EditValidationError, match="64-character"):
            HumanEditPatch(
                file_path="src/example.py",
                patch_text=simple_patch_text,
                reason="Test",
                expected_base_sha256="abc123",  # Too short
            )
    
    def test_expected_base_sha256_invalid_chars_rejected(self, simple_patch_text):
        """Non-hex characters are rejected."""
        with pytest.raises(EditValidationError, match="hex string"):
            HumanEditPatch(
                file_path="src/example.py",
                patch_text=simple_patch_text,
                reason="Test",
                expected_base_sha256="z" * 64,  # Not hex
            )
    
    def test_validate_patch_concurrency_match(self, simple_patch_text, simple_original):
        """Validation passes when expected_base_sha256 matches."""
        import hashlib
        sha256 = hashlib.sha256(simple_original.encode('utf-8')).hexdigest()
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
            expected_base_sha256=sha256,
        )
        
        result = validate_patch(patch, simple_original)
        assert result.valid is True
    
    def test_validate_patch_concurrency_mismatch(self, simple_patch_text, simple_original):
        """Validation fails when file content changed (SHA256 mismatch)."""
        wrong_sha256 = "a" * 64  # Doesn't match simple_original
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
            expected_base_sha256=wrong_sha256,
        )
        
        result = validate_patch(patch, simple_original)
        
        assert result.valid is False
        assert any("concurrency" in e.lower() or "changed" in e.lower() for e in result.errors)
    
    def test_to_dict_includes_expected_base_sha256(self, simple_patch_text):
        """to_dict includes expected_base_sha256 when set."""
        sha256 = "a" * 64
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
            expected_base_sha256=sha256,
        )
        
        data = patch.to_dict()
        assert "expected_base_sha256" in data
        assert data["expected_base_sha256"] == sha256
    
    def test_to_dict_omits_expected_base_sha256_when_none(self, simple_patch_text):
        """to_dict omits expected_base_sha256 when not set."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        data = patch.to_dict()
        assert "expected_base_sha256" not in data
    
    def test_from_dict_restores_expected_base_sha256(self, simple_patch_text):
        """from_dict properly restores expected_base_sha256."""
        sha256 = "b" * 64
        
        original = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
            expected_base_sha256=sha256,
        )
        original.parse()
        
        data = original.to_dict()
        restored = HumanEditPatch.from_dict(data)
        
        assert restored.expected_base_sha256 == sha256


# =============================================================================
# Patch Application Tests
# =============================================================================

class TestPatchApplication:
    """Tests for apply_patch()."""
    
    def test_apply_simple_patch(self, simple_patch_text, simple_original):
        """Apply a simple single-line change."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        result = apply_patch(patch, simple_original)
        
        assert 'return "world"' in result
        assert 'return "hello"' not in result
    
    def test_apply_multi_hunk_patch(self, multi_hunk_patch, multi_hunk_original):
        """Apply a patch with multiple hunks."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=multi_hunk_patch,
            reason="Test",
        )
        
        result = apply_patch(patch, multi_hunk_original)
        
        assert 'return "world"' in result
        assert "goodbye()" in result.split("main()")[-1]  # goodbye() added at end
    
    def test_apply_preserves_trailing_newline(self, simple_patch_text, simple_original):
        """Trailing newline handling is consistent."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        # Original ends with newline
        assert simple_original.endswith('\n')
        result = apply_patch(patch, simple_original)
        assert result.endswith('\n')
    
    def test_apply_context_mismatch_raises(self, simple_patch_text):
        """Applying to wrong content raises PatchApplyError."""
        wrong_original = "completely different content"
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        # PatchApplyError raised for mismatch or bounds issues
        with pytest.raises(PatchApplyError, match="(mismatch|beyond)"):
            apply_patch(patch, wrong_original)
    
    def test_apply_out_of_bounds_raises(self):
        """Hunk beyond file end raises PatchApplyError."""
        patch_text = """\
--- a/file.py
+++ b/file.py
@@ -100,3 +100,3 @@
 line100
-line101
+LINE101
 line102
"""
        original = "just one line\n"
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=patch_text,
            reason="Test",
        )
        
        with pytest.raises(PatchApplyError, match="beyond"):
            apply_patch(patch, original)


# =============================================================================
# Audit Entry Tests
# =============================================================================

class TestAuditEntry:
    """Tests for EditAuditEntry."""
    
    def test_audit_from_patch(self, simple_patch_text, simple_original):
        """Create audit entry from patch and validation."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Fix typo",
            editor_id="user123",
        )
        patch.parse()
        
        validation = EditValidationResult(
            valid=True,
            warnings=["Size warning"],
        )
        
        audit = EditAuditEntry.from_patch(
            run_id="run-123",
            patch=patch,
            validation=validation,
            applied=True,
        )
        
        assert audit.run_id == "run-123"
        assert audit.file_path == "src/example.py"
        assert audit.patch_fingerprint == patch.fingerprint()
        assert audit.reason == "Fix typo"
        assert audit.editor_id == "user123"
        assert audit.applied is True
        assert audit.lines_added == 1
        assert audit.lines_removed == 1
        assert "Size warning" in audit.validation_warnings
    
    def test_audit_to_dict(self, simple_patch_text):
        """Audit entry serializes to dict."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        patch.parse()
        
        validation = EditValidationResult(valid=True)
        audit = EditAuditEntry.from_patch(
            run_id="run-123",
            patch=patch,
            validation=validation,
            applied=True,
        )
        
        data = audit.to_dict()
        
        assert data["run_id"] == "run-123"
        assert data["applied"] is True
        assert "timestamp" in data


# =============================================================================
# Summary Tests
# =============================================================================

class TestHumanEditSummary:
    """Tests for HumanEditSummary."""
    
    def test_summary_from_patches(self, simple_patch_text, multi_hunk_patch):
        """Build summary from multiple patches."""
        patch1 = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test 1",
        )
        patch1.parse()
        
        patch2 = HumanEditPatch(
            file_path="src/other.py",
            patch_text=simple_patch_text,
            reason="Test 2",
        )
        patch2.parse()
        
        summary = HumanEditSummary.from_patches([patch1, patch2])
        
        assert summary.edit_count == 2
        assert len(summary.files_edited) == 2
        assert "src/example.py" in summary.files_edited
        assert "src/other.py" in summary.files_edited
        assert summary.needs_static_recheck is True
    
    def test_summary_files_bounded(self, simple_patch_text):
        """Summary limits number of files listed."""
        patches = []
        for i in range(10):
            patch = HumanEditPatch(
                file_path=f"src/file{i}.py",
                patch_text=simple_patch_text,
                reason=f"Test {i}",
            )
            patch.parse()
            patches.append(patch)
        
        summary = HumanEditSummary.from_patches(patches)
        
        assert len(summary.files_edited) == HumanEditSummary.MAX_FILES_IN_SUMMARY
    
    def test_summary_roundtrip(self, simple_patch_text):
        """Summary serializes and deserializes correctly."""
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        patch.parse()
        
        original = HumanEditSummary.from_patches([patch])
        data = original.to_dict()
        restored = HumanEditSummary.from_dict(data)
        
        assert restored.edit_count == original.edit_count
        assert restored.files_edited == original.files_edited
        assert restored.needs_static_recheck == original.needs_static_recheck


# =============================================================================
# Edge Cases
# =============================================================================

class TestEdgeCases:
    """Edge cases and error handling."""
    
    def test_empty_file_patch(self):
        """Patch to empty file."""
        patch_text = """\
--- a/src/empty.py
+++ b/src/empty.py
@@ -0,0 +1,3 @@
+def new_function():
+    pass
+
"""
        # Note: @@ -0,0 means empty original
        # This is a special case for adding to empty files
        patch = HumanEditPatch(
            file_path="src/empty.py",
            patch_text=patch_text,
            reason="Add new function",
        )
        patch.parse()
        
        assert len(patch.hunks) == 1
        assert patch.hunks[0].old_count == 0
    
    def test_delete_only_patch(self):
        """Patch that only deletes lines."""
        patch_text = """\
--- a/src/example.py
+++ b/src/example.py
@@ -1,5 +1,3 @@
 def hello():
     return "hello"
-
-def unused():
-    pass
"""
        original = """\
def hello():
    return "hello"

def unused():
    pass
"""
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=patch_text,
            reason="Remove unused function",
        )
        
        result = apply_patch(patch, original)
        
        assert "unused" not in result
        assert "hello" in result
    
    def test_add_only_patch(self):
        """Patch that only adds lines."""
        patch_text = """\
--- a/src/example.py
+++ b/src/example.py
@@ -1,3 +1,6 @@
 def hello():
     return "hello"
+
+def world():
+    return "world"
 
"""
        original = """\
def hello():
    return "hello"

"""
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=patch_text,
            reason="Add new function",
        )
        
        result = apply_patch(patch, original)
        
        assert "world" in result
        assert "hello" in result
    
    def test_whitespace_only_change(self):
        """Patch that changes only whitespace."""
        patch_text = """\
--- a/src/example.py
+++ b/src/example.py
@@ -1,3 +1,3 @@
 def hello():
-    return "hello"
+        return "hello"
 
"""
        original = """\
def hello():
    return "hello"

"""
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=patch_text,
            reason="Fix indentation",
        )
        
        result = apply_patch(patch, original)
        
        assert "        return" in result  # 8 spaces


# =============================================================================
# Invariant Tests
# =============================================================================

class TestInvariants:
    """Tests enforcing non-negotiable invariants from ADR."""
    
    def test_single_file_patches_only(self):
        """Multi-file patches in single HumanEditPatch are not supported."""
        # The model only has one file_path field
        # This test documents that behavior
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text="some patch",
            reason="Test",
        )
        
        # Cannot have multiple file paths
        assert isinstance(patch.file_path, str)
        assert not isinstance(patch.file_path, list)
    
    def test_no_fuzzy_matching(self, simple_patch_text):
        """Context must match exactly - no fuzzy matching."""
        original_with_typo = """\
def hello():
    return "helllo"

def main():
    print(hello())
"""
        
        patch = HumanEditPatch(
            file_path="src/example.py",
            patch_text=simple_patch_text,
            reason="Test",
        )
        
        # Should fail because "helllo" != "hello"
        with pytest.raises(PatchApplyError, match="mismatch"):
            apply_patch(patch, original_with_typo)
    
    def test_path_validation_enforced(self):
        """All paths go through validation."""
        dangerous_paths = [
            "../secret.py",
            "/etc/passwd",
            "src/../../../root/.bashrc",
            "C:\\Windows\\System32\\config",
        ]
        
        for path in dangerous_paths:
            with pytest.raises(EditValidationError):
                HumanEditPatch(
                    file_path=path,
                    patch_text="irrelevant",
                    reason="Test",
                )

# =============================================================================
# Decision Schema Tests
# =============================================================================

class TestDecisionSchema:
    """Tests for sandbox decision schema validation."""
    
    def test_decision_schema_version_exists(self):
        """Schema version constant is defined."""
        assert DECISION_SCHEMA_VERSION == 1
    
    def test_valid_sandbox_actions_defined(self):
        """Valid actions are defined as frozenset."""
        assert "continue" in VALID_SANDBOX_ACTIONS
        assert "regenerate_targeted" in VALID_SANDBOX_ACTIONS
        assert "apply_human_edits" in VALID_SANDBOX_ACTIONS
        assert len(VALID_SANDBOX_ACTIONS) == 3
    
    def test_validate_decision_missing_action(self):
        """Decision without action is invalid."""
        decision = {
            "decided_at": 1234567890.0,
        }
        errors = validate_sandbox_decision(decision)
        assert any("action" in e.lower() for e in errors)
    
    def test_validate_decision_invalid_action(self):
        """Decision with invalid action is rejected."""
        decision = {
            "action": "invalid_action",
            "decided_at": 1234567890.0,
        }
        errors = validate_sandbox_decision(decision)
        assert any("invalid action" in e.lower() for e in errors)
    
    def test_validate_decision_missing_decided_at(self):
        """Decision without decided_at is invalid."""
        decision = {
            "action": "continue",
        }
        errors = validate_sandbox_decision(decision)
        assert any("decided_at" in e.lower() for e in errors)
    
    def test_validate_decision_valid_continue(self):
        """Valid continue decision passes."""
        decision = {
            "action": "continue",
            "decided_at": 1234567890.0,
        }
        errors = validate_sandbox_decision(decision)
        assert errors == []
    
    def test_validate_decision_valid_with_version(self):
        """Decision with explicit version passes."""
        decision = {
            "action": "continue",
            "decided_at": 1234567890.0,
            "decision_version": 1,
        }
        errors = validate_sandbox_decision(decision)
        assert errors == []
    
    def test_validate_decision_future_version_rejected(self):
        """Future schema versions are rejected."""
        decision = {
            "action": "continue",
            "decided_at": 1234567890.0,
            "decision_version": 999,
        }
        errors = validate_sandbox_decision(decision)
        assert any("version" in e.lower() for e in errors)
    
    def test_validate_decision_apply_edits_needs_patches(self):
        """apply_human_edits requires patches."""
        decision = {
            "action": "apply_human_edits",
            "decided_at": 1234567890.0,
        }
        errors = validate_sandbox_decision(decision)
        assert any("patches" in e.lower() for e in errors)
    
    def test_validate_decision_apply_edits_with_patches(self):
        """apply_human_edits with valid patches passes."""
        decision = {
            "action": "apply_human_edits",
            "decided_at": 1234567890.0,
            "patches": [{
                "file_path": "src/test.py",
                "patch_text": "--- a/src/test.py\n+++ b/src/test.py",
                "reason": "Fix bug",
            }]
        }
        errors = validate_sandbox_decision(decision)
        assert errors == []
    
    def test_validate_decision_patches_missing_fields(self):
        """Patches with missing required fields are caught."""
        decision = {
            "action": "apply_human_edits",
            "decided_at": 1234567890.0,
            "patches": [{
                "file_path": "src/test.py",
                # Missing patch_text and reason
            }]
        }
        errors = validate_sandbox_decision(decision)
        assert any("patch_text" in e for e in errors)
        assert any("reason" in e for e in errors)
    
    def test_create_sandbox_decision_continue(self):
        """create_sandbox_decision creates valid continue."""
        decision = create_sandbox_decision(action="continue")
        
        assert decision["action"] == "continue"
        assert decision["decision_version"] == DECISION_SCHEMA_VERSION
        assert "decided_at" in decision
    
    def test_create_sandbox_decision_with_patches(self):
        """create_sandbox_decision creates valid apply_human_edits."""
        patches = [{
            "file_path": "src/test.py",
            "patch_text": "diff here",
            "reason": "Fix bug",
        }]
        
        decision = create_sandbox_decision(
            action="apply_human_edits",
            patches=patches,
        )
        
        assert decision["action"] == "apply_human_edits"
        assert decision["patches"] == patches
    
    def test_create_sandbox_decision_invalid_raises(self):
        """create_sandbox_decision raises on invalid params."""
        with pytest.raises(DecisionSchemaError):
            create_sandbox_decision(
                action="apply_human_edits",
                # Missing patches!
            )
    
    def test_create_sandbox_decision_with_feedback(self):
        """create_sandbox_decision includes optional feedback."""
        decision = create_sandbox_decision(
            action="regenerate_targeted",
            feedback="Please fix the error handling",
            targets=["src/api/handler.py"],
        )
        
        assert decision["feedback"] == "Please fix the error handling"
        assert decision["targets"] == ["src/api/handler.py"]
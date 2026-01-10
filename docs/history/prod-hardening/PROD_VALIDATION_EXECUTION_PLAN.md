# Production Validation Execution Plan

**Date:** 2025-01-XX  
**Status:** Implementation Complete, Validation In Progress

---

## Summary

Implemented manifest-based scoped validation to fix non-deterministic per-spec validation failures.

---

## Changes Made

### 1. Manifest Generation

**File:** [apply_repo_integration_changes.py](../src/integration_coworker/graph/nodes/apply_repo_integration_changes.py)

**Change:** After writing files, write `.integration_manifest.json`:

```python
# Write integration manifest for scoped validation
# Only include user-generated files, not auto-generated __init__.py/pyproject.toml
if not is_dry_run and applied_changes:
    manifest_files = [
        {"path": change["path"], "action": change["action"]}
        for change in applied_changes
        if not change.get("auto_generated", False)
    ]
    
    manifest = {
        "run_id": state.run_id or "unknown",
        "provider_code": state.provider_code or "unknown",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "files": manifest_files,
    }
    
    manifest_path = repo_root / ".integration_manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
```

### 2. Manifest-Based Artifact Collection

**File:** [demo-final-showcase.sh](../scripts/demo-final-showcase.sh#L1375-L1410)

**Change:** Replace glob-based artifact collection with manifest reading:

```python
# Before (BAD): Scanned entire directory
for py_file in glob.glob(os.path.join(search_dir, "**", "*.py"), recursive=True):
    artifacts.append(ArtifactFile(path=rel_path, content=content))

# After (GOOD): Read from manifest
manifest_path = os.path.join(repo_root, ".integration_manifest.json")
with open(manifest_path, "r") as f:
    manifest = json.load(f)

for file_info in manifest.get("files", []):
    if file_path.endswith(".py"):
        artifacts.append(ArtifactFile(path=file_info["path"], content=content))
```

---

## Testing

### Quick Validation Test

Run a quick test with a single spec to verify manifest generation:

```bash
# Quick test
./scripts/demo-final-showcase.sh --quick

# Check manifest was created
cat /path/to/target/repo/.integration_manifest.json
```

Expected output:
```json
{
  "run_id": "abc123",
  "provider_code": "stripe-api",
  "timestamp": "2025-01-20T12:00:00Z",
  "files": [
    {"path": "/path/to/repo/src/integrations/clients/stripe.py", "action": "created"},
    {"path": "/path/to/repo/tests/integrations/test_stripe.py", "action": "created"}
  ]
}
```

### Full Production Test Matrix

Current test running: `./scripts/demo-final-showcase.sh`

**Configuration:**
- LLM: Real (gpt-4)
- Tracing: ON (LangSmith)
- Persistence: ON (PostgreSQL)
- Specs: 15
- Repo roots: 3

**Expected Results:**
- ✅ No LangSmith 422 errors (byte budget fix verified in v5)
- ✅ No bare_except failures on pre-existing files (manifest-based scoping)
- ✅ No mypy failures on pre-existing files (manifest-based scoping)
- ✅ Per-spec validation passes (only validates generated files)

---

## Validation Checkpoints

### Checkpoint 1: Manifest Generation

Look for log line:
```
INFO integration_coworker.graph.nodes.apply_repo_integration_changes: Wrote integration manifest: /path/to/.integration_manifest.json (N files)
```

### Checkpoint 2: Manifest Reading in Per-Spec Validation

Look for log line:
```
   📋 Manifest: run_id=abc123, files=2
   📦 Found 2 artifact(s) from manifest
```

### Checkpoint 3: Validation Success

Look for:
```
   ✅ ruff (Xms)
   ✅ mypy (Xms)
   ✅ bandit (Xms)
   ✅ bare_except_check (Xms)
   ✅ pytest (Xms)
```

---

## Rollback Plan

If issues arise, revert to glob-based approach:

1. In `apply_repo_integration_changes.py`: Remove manifest writing code
2. In `demo-final-showcase.sh`: Restore original glob-based artifact collection

---

## Success Criteria

| Criterion | Status |
|-----------|--------|
| Manifest written after file writes | ✅ Implemented |
| Per-spec validation reads manifest | ✅ Implemented |
| No false failures from pre-existing files | ⏳ Validating |
| Demo completes without validation errors | ⏳ Validating |

---

## Related Documents

- [PROD_VALIDATION_EVIDENCE.md](PROD_VALIDATION_EVIDENCE.md) - Root cause analysis
- [PROD_VALIDATION_DESIGN.md](PROD_VALIDATION_DESIGN.md) - Design decisions

# Production Validation Playbook

**Author:** AI Code Agent  
**Date:** 2024-12-15  
**Status:** READY  
**Purpose:** Step-by-step validation checklist for Bucket 2 production deployment

---

## 1. Pre-Deployment Checklist

### 1.1 Test Suite Validation

```bash
# Run full Bucket 2 test suite
pytest tests/test_fixed_width_source.py tests/test_fixed_width_adversarial.py \
       tests/test_excel_adversarial.py tests/test_pdf_guide_adversarial.py \
       tests/test_validation_codegen.py tests/test_safe_eval_security.py \
       tests/test_excel_formula_warnings.py -v

# Run KG integration tests
pytest tests/test_kg_provenance.py tests/test_kg_node_persistence.py -v

# Verify 0 warnings
pytest tests/ -W error::SyntaxWarning -W error::DeprecationWarning --tb=short
```

**Expected Results:**
- ✅ 234+ tests passing
- ✅ 0 SyntaxWarnings
- ✅ 0 DeprecationWarnings

### 1.2 Database Schema Validation

```sql
-- Verify kg.nodes table exists with correct schema
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_schema = 'kg' AND table_name = 'nodes'
ORDER BY ordinal_position;

-- Expected columns:
-- id, node_type, key, name, description, properties, embedding, 
-- confidence_score, usage_count, created_at, updated_at

-- Verify kg.edges table exists
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_schema = 'kg' AND table_name = 'edges'
ORDER BY ordinal_position;

-- Expected columns:
-- id, src_node_id, dst_node_id, relation_type, weight, properties, created_at

-- Verify IVFFlat index on embeddings
SELECT indexname, indexdef 
FROM pg_indexes 
WHERE tablename = 'nodes' AND schemaname = 'kg';
```

### 1.3 pgvector Extension Validation

```sql
-- Verify pgvector is installed
SELECT * FROM pg_extension WHERE extname = 'vector';

-- Test vector operations
SELECT '[1,2,3]'::vector <=> '[4,5,6]'::vector AS cosine_distance;
-- Should return: 0.02536...
```

---

## 2. Smoke Test Procedures

### 2.1 Fixed-Width File Detection

```python
from integration_coworker.sources import detect_and_route, ensure_sources_registered

ensure_sources_registered()

# Test fixed-width detection
fw_content = b"""NAME       AGE  CITY      
John Doe   025  New York  
Jane Smith 032  Boston    
"""
result = detect_and_route(fw_content, "test.txt", "")
assert result.confidence >= 0.85
assert "fixed" in result.source_uri.lower() or hasattr(result.data, 'file_spec')
print(f"✅ Fixed-width detection: confidence={result.confidence:.2f}")
```

### 2.2 Excel File Detection

```python
from integration_coworker.sources import detect_and_route, ensure_sources_registered
from openpyxl import Workbook
import io

ensure_sources_registered()

# Create test Excel file
wb = Workbook()
ws = wb.active
ws["A1"], ws["B1"] = "Name", "Value"
ws["A2"], ws["B2"] = "Test", 123
buf = io.BytesIO()
wb.save(buf)
content = buf.getvalue()

result = detect_and_route(content, "test.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
assert result.confidence >= 0.80
print(f"✅ Excel detection: confidence={result.confidence:.2f}")
```

### 2.3 KG Node Persistence

```python
from integration_coworker.persistence.postgres import get_connection
from integration_coworker.kg.persist import (
    upsert_node, 
    upsert_edge,
    build_file_spec_key,
    KGNodeType,
    KGEdgeRelation,
)

with get_connection() as conn:
    # Create test FILE_SPEC node
    spec_key = build_file_spec_key(1, "smoke_test_spec")
    node_id = upsert_node(
        conn, 
        KGNodeType.FILE_SPEC, 
        spec_key,
        properties={"file_type": "csv", "test": True},
    )
    conn.commit()
    
    # Verify node exists
    cur = conn.cursor()
    cur.execute("SELECT id, key FROM kg.nodes WHERE key = %s", (spec_key,))
    row = cur.fetchone()
    assert row is not None
    print(f"✅ KG node persisted: id={row[0]}, key={row[1]}")
    
    # Cleanup
    cur.execute("DELETE FROM kg.nodes WHERE key = %s", (spec_key,))
    conn.commit()
```

### 2.4 Safe Eval Security

```python
from integration_coworker.codegen.safe_eval import safe_eval_cross_field, CrossFieldEvaluationError

# Test safe operations
assert safe_eval_cross_field("amount > 100", {"amount": 150}) == True
assert safe_eval_cross_field("qty * price == total", {"qty": 10, "price": 5, "total": 50}) == True
print("✅ Safe eval: arithmetic and comparison work")

# Test blocked operations
blocked_expressions = [
    "__import__('os')",
    "().__class__.__bases__[0].__subclasses__()",
    "open('/etc/passwd').read()",
    "eval('1+1')",
]
for expr in blocked_expressions:
    try:
        safe_eval_cross_field(expr, {})
        raise AssertionError(f"Should have blocked: {expr}")
    except CrossFieldEvaluationError:
        pass  # Expected
print("✅ Safe eval: dangerous expressions blocked")
```

---

## 3. Performance Validation

### 3.1 Vector Search Latency

```python
import time
from integration_coworker.persistence.postgres import get_connection
from integration_coworker.kg.persist import find_similar_fields

# Generate test embedding
test_embedding = [0.1] * 1536

with get_connection() as conn:
    start = time.perf_counter()
    for _ in range(100):
        results = find_similar_fields(conn, test_embedding, limit=5)
    elapsed = time.perf_counter() - start
    
    avg_ms = (elapsed / 100) * 1000
    print(f"✅ Vector search: avg {avg_ms:.2f}ms per query")
    assert avg_ms < 100, f"Vector search too slow: {avg_ms}ms"
```

### 3.2 Source Detection Throughput

```python
import time
from integration_coworker.sources import detect_and_route, ensure_sources_registered

ensure_sources_registered()

# Generate test files
test_files = []
for i in range(100):
    content = f"COL1      COL2      COL3\n{'X' * 10 * 3}\n".encode() * 10
    test_files.append((content, f"test_{i}.txt"))

start = time.perf_counter()
for content, uri in test_files:
    detect_and_route(content, uri, "")
elapsed = time.perf_counter() - start

throughput = len(test_files) / elapsed
print(f"✅ Detection throughput: {throughput:.1f} files/sec")
assert throughput > 10, f"Detection too slow: {throughput} files/sec"
```

---

## 4. Log Verification

### 4.1 Expected Log Events

When processing a file, verify these structured log events appear:

```
INFO  source.detection.start   [uri=test.xlsx content_type=... content_length=...]
INFO  source.detection.score   [source_class=ExcelSource uri=test.xlsx score=0.95 priority=65]
INFO  source.detection.score   [source_class=FixedWidthSource uri=test.xlsx score=0.10 priority=60]
INFO  source.detection.selected [source_class=ExcelSource uri=test.xlsx score=0.95]
INFO  source.inference.complete [source_class=ExcelSource uri=test.xlsx confidence=0.92 warning_count=0 error_count=0]
```

### 4.2 Log Filtering Commands

```bash
# Filter for detection events
grep "source.detection" /var/log/app.log | jq -r '.source_class, .score'

# Find slow detections (>100ms implied by high warning_count)
grep "source.inference.complete" /var/log/app.log | jq 'select(.warning_count > 5)'

# Find failed detections
grep "source.detection.rejected" /var/log/app.log
```

---

## 5. Rollback Procedures

### 5.1 KG Rollback

If KG integration causes issues:

```sql
-- Disable KG persistence (application-level flag)
-- Set environment variable: DISABLE_KG_PERSISTENCE=1

-- Or truncate KG tables (destructive)
TRUNCATE kg.edges CASCADE;
TRUNCATE kg.nodes CASCADE;
```

### 5.2 Code Rollback

```bash
# Revert KG integration commits
git log --oneline | grep -E "KG|kg|provenance" | head -5
git revert <commit-hash>

# Or reset to pre-KG state
git reset --hard <pre-kg-tag>
```

---

## 6. Monitoring Checklist

### 6.1 Metrics to Monitor

| Metric | Threshold | Action |
|--------|-----------|--------|
| `source.detection.score` avg | > 0.85 | Investigate low scores |
| `source.inference.complete` confidence avg | > 0.80 | Check detection quality |
| `kg.nodes` row count growth | < 10K/hour | Monitor for runaway inserts |
| Vector search p99 latency | < 200ms | Tune IVFFlat `lists` param |

### 6.2 Alert Conditions

```yaml
alerts:
  - name: low_detection_confidence
    condition: avg(source.detection.score) < 0.70 over 5m
    severity: warning
    
  - name: kg_write_failures
    condition: count(kg.persist.error) > 10 over 1m
    severity: critical
    
  - name: safe_eval_blocked
    condition: count(safe_eval.blocked) > 100 over 1h
    severity: warning
```

---

## 7. Validation Sign-Off

| Item | Validator | Date | Status |
|------|-----------|------|--------|
| Test suite green | | | ☐ |
| DB schema verified | | | ☐ |
| Smoke tests pass | | | ☐ |
| Performance acceptable | | | ☐ |
| Logs structured correctly | | | ☐ |
| Rollback tested | | | ☐ |
| Monitoring configured | | | ☐ |

**Deployment Approved:** ☐ Yes / ☐ No

**Approver:** _________________________ **Date:** _____________

---

## 8. Post-Deployment Verification

### 8.1 First Hour Checks

1. Monitor `source.detection.rejected` count — should be < 5% of total
2. Verify `kg.nodes` count increasing (if using KG persistence)
3. Check for `CrossFieldEvaluationError` logs — should be rare
4. Verify no `SyntaxWarning` in application logs

### 8.2 First Day Checks

1. Review vector search latency percentiles
2. Audit `kg.edges` for `DERIVES_FROM_GUIDE` edges — confirms PDF enrichment working
3. Sample-check inferred field types against known good files
4. Verify structured log fields are queryable in log aggregator

### 8.3 First Week Checks

1. Run full regression test suite against production data
2. Review detection confidence distribution — should be bimodal (high for known, low for unknown)
3. Validate KG query performance hasn't degraded with data volume
4. Confirm no memory leaks in long-running processes

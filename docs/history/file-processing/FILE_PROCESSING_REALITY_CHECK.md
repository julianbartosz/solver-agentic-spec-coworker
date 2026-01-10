# File Processing Reality Check

**Date:** 2025-12-19  
**Last Updated:** 2025-12-19  
**Purpose:** Honest assessment of what each phase actually delivered vs. claimed

---

## Current Status (Phase 2 Complete, Phase 3 In Progress)

| Capability | Phase 1 | Phase 2 | Phase 3 (WIP) |
|------------|---------|---------|---------------|
| True Streaming | ❌ Not delivered | ❌ Infrastructure only | 🔄 In progress |
| Hard Timeout | ❌ Cooperative only | ✅ Subprocess worker exists | 🔄 Wiring to parse path |
| Pre-load Size Check | ❌ Checked after load | ✅ `check_size_bytes()` | ✅ Done |
| ContentHandle API | ❌ None | ✅ Protocol + 3 impls | 🔄 Wiring to parsers |

**Honest Summary:** Phase 2 delivered API scaffolding (ContentHandle, subprocess timeout helper) but did NOT deliver actual streaming parse. Parsers still call `handle.read_bytes()` which loads entire file.

---

## Phase 1 Reality (Original Assessment)

| Capability | Claimed | Reality | Evidence |
|------------|---------|---------|----------|
| True Streaming | ❌ Not claimed but implied | ❌ **NOT DELIVERED** | All parsers use `normalize_content()` which loads entire file |
| Hard Timeout | ✅ "TimeoutContext for deadline enforcement" | ❌ **NOT DELIVERED** | Only cooperative `check()` calls; cannot kill blocked I/O |
| Cooperative Cancel | ✅ Claimed | ✅ DELIVERED | `CancelToken` with thread-safe Event |
| Size Gating | ✅ Claimed | ⚠️ PARTIAL | Checks size but bytes already in memory |
| 45 Unit Tests | ✅ Claimed | ✅ DELIVERED | `pytest tests/test_processing_context.py` → 45 passed |

---

## Detailed Evidence

### 1. NO TRUE STREAMING (Still True After Phase 2)

**Question:** Does any parser actually stream data (process incrementally without loading all into memory)?

**Answer: NO** - Phase 2 added ContentHandle but parsers still call `read_bytes()`

**Evidence:**

```bash
# All parsers call normalize_content() or normalize_content_bytes() first
grep -n "normalize_content" src/integration_coworker/sources/*.py
```

Output:
- `csv_source.py:77:        content_str = normalize_content(content)`
- `csv_source.py:132:       content_str = normalize_content(content)`
- `excel.py:83:             content_bytes = normalize_content_bytes(content)`
- `excel.py:149:            content_bytes = normalize_content_bytes(content)`
- `fixed_width.py:73:       content_str = normalize_content(content)`
- `fixed_width.py:123:      content_str = normalize_content(content)`

**Root Cause:** The `detect_and_route()` function signature is:
```python
def detect_and_route(
    content: Union[bytes, str],  # <-- ENTIRE content already in memory
    ...
)
```

The API requires the full content to be passed in. There's no path for streaming.

### 2. NO HARD TIMEOUT ENFORCEMENT

**Question:** Can any timeout actually terminate a stuck parse operation (blocked on I/O, C-extension, etc.)?

**Answer: NO**

**Evidence:**

```bash
grep -n "subprocess\|terminate\|kill\|signal" src/integration_coworker/sources/processing_context.py
```

Output:
- Line 12: `"Low overhead (no subprocess unless opt-in)"`
- Line 198: `"Uses cooperative checking (not signals or processes)"`

**What TimeoutContext actually does:**
```python
def check(self) -> None:
    """Check deadline and raise if exceeded."""
    if self.is_expired:
        raise FileProcessingTimeout(...)
```

This is **cooperative only** - it requires the code to call `check()`. If code is blocked in:
- A slow `read()` syscall
- An openpyxl C-extension
- A regex backtracking catastrophically

...the timeout cannot interrupt it. Python threads cannot be forcibly killed (per `concurrent.futures` docs).

### 3. SIZE GATING IS HOLLOW

**Question:** Does size gating prevent large files from being loaded into memory?

**Answer: NO** - by the time we check, bytes are already loaded.

**Evidence:**
```python
# From __init__.py detect_and_route():
content_length = len(content) if isinstance(content, bytes) else len(content.encode('utf-8'))
```

The caller must pass `content: Union[bytes, str]` - the file is already fully loaded before we can check its size.

---

## What WAS Delivered (Correctly)

### ✅ CancelToken (cooperative)
- Thread-safe cancellation flag
- Works for interruptible loops
- 8 tests passing

### ✅ TimeoutContext (cooperative)
- Tracks elapsed time and deadline
- Works for check-based loops
- 7 tests passing

### ✅ FileSizeGate
- Can measure content size
- Returns warnings/errors based on thresholds
- 7 tests passing

### ✅ ProcessingContext
- Composes all three
- `check()` method for loop integration
- 13 tests passing

### ✅ FileProcessingConfig
- Environment-based configuration
- Reasonable defaults
- 5 tests passing

### ✅ Exception Hierarchy
- CancelledException, FileProcessingTimeout, FileTooLargeError
- Proper inheritance from FileProcessingError
- 3 tests passing

---

## What Must Be Implemented

### P0 (Required for "Enterprise Scale")

1. **ContentHandle Abstraction**
   - Protocol: `open_bytes() -> BinaryIO`, `open_text() -> TextIO`, `size_bytes() -> int`
   - Implementations: `PathContentHandle`, `StreamContentHandle`, `BytesContentHandle`
   - New entry point: `detect_and_route_handle(handle: ContentHandle, ...)`

2. **True Streaming Parsers**
   - CSV: Use `csv.reader()` on text stream or pandas `chunksize=`
   - Fixed-width: Stream line-by-line with sample-based inference
   - Excel: openpyxl `read_only=True` is already streaming-capable

3. **Process-Based Hard Timeout**
   - Subprocess worker that can be `terminate()`/`kill()`ed
   - Escalation: SIGTERM → wait 5s → SIGKILL
   - Configuration: `FILE_HARD_TIMEOUT_MODE=process|cooperative|off`

### P1 (Important)

4. **Two-Layer Cancellation**
   - Cooperative: existing CancelToken
   - Hard: subprocess terminate if stuck

5. **Pre-Load Size Gating**
   - Check `os.path.getsize()` or `Content-Length` before loading
   - Reject before memory allocation

### P2 (Nice to Have)

6. **Progress Callbacks**
   - Report bytes/rows processed during streaming
   - Allow external monitoring

---

## Files to Modify

| File | Current State | Changes Needed |
|------|--------------|----------------|
| `sources/base.py` | Defines `SpecSource` protocol with `content: Union[bytes, str]` | Add `ContentHandle` support |
| `sources/__init__.py` | `detect_and_route()` takes bytes | Add `detect_and_route_handle()` |
| `sources/csv_source.py` | `normalize_content()` then parse | Add `parse_streaming()` |
| `sources/excel.py` | Loads bytes then parses | Optimize for streaming |
| `sources/fixed_width.py` | `normalize_content()` then parse | Add streaming with sample |
| `sources/content_handle.py` | **EMPTY** | Implement ContentHandle protocol |
| `sources/processing_context.py` | Cooperative only | Add `HardTimeoutWorker` |

---

## Test Verification

```bash
.venv311/bin/python -m pytest tests/test_processing_context.py -v --tb=short
# Result: 45 passed ✅

.venv311/bin/python -m pytest tests/test_file_processing_integration.py -v --tb=short
# Expected: 19 passed ✅
```

---

## Conclusion

Phase 1 delivered **cooperative cancellation infrastructure** but did **NOT** deliver:
- True streaming (bytes-in API prevents it)
- Hard timeout enforcement (cooperative checks only)
- Meaningful size gating (bytes already in memory)

The path forward requires:
1. **ContentHandle abstraction** to replace bytes-in API
2. **Subprocess hard timeout worker** for true timeout enforcement
3. **Streaming parser implementations** that use ContentHandle

---

*This document is an honest assessment. The infrastructure in Phase 1 is useful for cooperative scenarios but insufficient for enterprise scale where files can be large and parsers can get stuck.*

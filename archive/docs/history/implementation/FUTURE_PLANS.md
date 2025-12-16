# Future Plans

> **Status**: Archived – roadmap now tracked via GitHub issues and ADRs.
>
> **Last Updated**: December 2025

---

## Table of Contents

1. [V2.2 Dynamic Capabilities (Completed)](#v22-dynamic-capabilities-completed)
2. [V3 Streaming & Persistence](#v3-streaming--persistence)
3. [V4 Observability](#v4-observability)
4. [Long-Term Vision](#long-term-vision)

---

## V2.2 Dynamic Capabilities (Completed)

### Summary

Seven dynamic capability fixes implemented and verified:

| Fix | Feature | Implementation |
|-----|---------|----------------|
| 1 | **Constrained Codegen Option** | `IntegrationOptions.constrained_codegen` |
| 2 | **Constrained Path Generation** | `build_constrained_body_prompt()` |
| 3 | **Test Fixture Injection** | `TEST_FIXTURE_SKELETON` constant |
| 4 | **Unified Semantic Index** | `UnifiedSemanticIndex` class |
| 5 | **Hybrid Policy Inference** | `_infer_policies_from_spec()` |
| 6 | **Async LLM Batcher** | `AsyncLLMBatcher` class |
| 7 | **Simplified Profile Resolution** | `_detect_python_framework()` |

**Status**: All 33 integration tests passing (919 total tests).

---

## V3 Streaming & Persistence

### Problem

Current `WorkflowState` carries all data in memory:

| Field | Typical Size | Large Spec Size |
|-------|--------------|-----------------|
| `openapi_spec` | 500KB | 10-50MB |
| `doc_chunks` | 2MB | 40MB+ |
| `spec_chunk_embeddings` | 6MB | 60MB+ |

For AWS-scale specs (10,000+ endpoints): **200MB+ RAM per run**.

### Solution: Streaming Persistence + Lazy Loading

- **Streaming**: Write large data to DB immediately, then clear from memory
- **Lazy Loading**: Load only what's needed from DB via indexed queries

### Target Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Memory per run (large spec) | 200MB+ | <20MB |
| Max spec size | ~5,000 endpoints | Unlimited |
| Max endpoints | ~1,000 (perf degrades) | 50,000+ |

### Key New Files

| File | Purpose |
|------|---------|
| `persistence/streaming.py` | Stream chunks/embeddings to DB |
| `persistence/lazy_loader.py` | Paginated access to large datasets |

### Implementation Phases

1. **Foundation** — Add streaming/lazy loading modules (non-breaking)
2. **Opt-In** — Add `STREAMING_PERSISTENCE=true` flag
3. **State Slimming** — Update WorkflowState to use IDs instead of content
4. **Default** — Make streaming the default

---

## V4 Observability

### Implemented Features

| Feature | Location | Description |
|---------|----------|-------------|
| **LangSmith URL in CLI** | `cli.py` | Shows trace URL at start of run |
| **Token Usage Tracking** | `llm/client.py`, `state.py` | Aggregates prompt/completion tokens |
| **LLM Usage in Report** | `build_report.py` | Token counts + estimated costs |
| **"What I Did" Section** | `build_report.py` | Plain-English explanations |
| **Journey Visualization** | `build_report.py` | Derived from completed_steps/errors |
| **LLM Fallbacks Section** | `build_report.py` | Shows fallback reasons |
| **Enriched Errors** | `build_report.py` | Numbered errors with severity |
| **Warnings Section** | `build_report.py` | Non-fatal issues with ⚠️ |

### Key Insight

LangSmith is the observability backend. The gap was in the **frontend** — surfacing this data without visiting LangSmith:

1. Show LangSmith URL immediately in CLI ✅
2. Summarize key metrics in the report ✅
3. Extract "What I Did" from existing state data ✅

### Deferred Features

| Feature | Reason |
|---------|--------|
| `debug-llm` CLI | Redundant with LangSmith |
| `debug-state` CLI | Redundant with LangSmith + checkpoints |
| Streamlit observability tab | LangSmith provides better visualization |
| Progress streaming | Use `--verbose` for now |

---

## Long-Term Vision

### Near-Term (V3)

- [ ] Streaming persistence for large specs
- [ ] Lazy loading for memory efficiency
- [ ] Resume from checkpoint

### Medium-Term (V4+)

- [ ] Multi-provider support (Stripe, GitHub, HubSpot)
- [ ] Advanced workflow patterns (polling, webhooks, pagination)
- [ ] Request/response field mapping DSL
- [ ] Policy customization per provider

### Long-Term

- [ ] Multi-file generation (models, types, enums)
- [ ] Circuit breakers and exponential backoff
- [ ] Team-shared knowledge graph
- [ ] Version-aware spec migrations

---

## Reference: Full Implementation Plans

For complete implementation details, see the archived plans:
- `docs/history/implementation/V3_STREAMING_PERSISTENCE_PLAN.md`
- `docs/history/implementation/V4_OBSERVABILITY_PLAN.md`
- `docs/history/implementation/DYNAMIC_CAPABILITY_FIXES_PLAN.md`


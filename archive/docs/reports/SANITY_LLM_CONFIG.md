# LLM Configuration Sanity Guide

This document captures configuration pitfalls discovered during M5 development and provides a checklist for debugging LLM-related issues.

## Node Execution Classification

Some nodes show **0.00s duration** in LangSmith traces. This is expected for non-LLM nodes that execute very fast. These nodes are **not no-ops** - they do meaningful work that is verified by tests.

### Node Classification Table

| Node | LangSmith Duration | Behavior Type | Verified By |
|------|-------------------|---------------|-------------|
| `plan_run` | 0.00s | Pure Python planning - generates run_id, infers provider | `test_node_execution_sanity.py` |
| `ingest_spec` | 0.00s | File I/O - reads spec file content | `test_node_execution_sanity.py` |
| `detect_and_parse_spec` | 0.00s | YAML/JSON parsing - validates OpenAPI structure | `test_node_execution_sanity.py` |
| `build_silver_api_model` | 0.00s | Model extraction - endpoints, schemas, entities | `test_node_execution_sanity.py` |
| `embed_spec_chunks` | ~200-500ms | **API call** - OpenAI embeddings | LangSmith trace |
| `persist_silver_checkpoint` | 0.00s | DB writes - SQLite/Postgres upserts | `test_node_execution_sanity.py` |
| `understand_task` | ~2-5s | **LLM call** - Anthropic task understanding | LangSmith trace |
| `align_task_with_kg` | 0.00s | KG query + inference - no LLM when templates found | `test_node_execution_sanity.py` |
| `plan_integration_flow` | ~3-8s | **LLM call** - Anthropic workflow planning | LangSmith trace |
| `attach_policies_and_patterns` | 0.00s | Policy inference - reads securitySchemes | `test_node_execution_sanity.py` |
| `attach_repo_context` | 0.00s | Repo detection - profile inference | `test_node_execution_sanity.py` |
| `generate_code_and_tests` | ~10-30s | **LLM call** - Anthropic code generation | LangSmith trace |
| `persist_gold_checkpoint` | 0.00s | DB writes - workflow templates, code artifacts | `test_node_execution_sanity.py` |
| `persist_kg_learning` | 0.00s | KG writes - nodes, edges, templates | `test_node_execution_sanity.py` |
| `analyze_repo_layout` | 0.00s | File system scan - finds integration hooks | `test_node_execution_sanity.py` |
| `apply_repo_integration_changes` | 0.00s | File operations - writes generated code | `test_node_execution_sanity.py` |
| `validate_integration_design` | 0.00s | Syntax checking - Python AST validation | `test_node_execution_sanity.py` |
| `build_report` | ~1-2s | **LLM call** - OpenAI summary + template rendering | LangSmith trace |
| `persist_run_outcome` | 0.00s | DB writes - run status and metrics | `test_node_execution_sanity.py` |

### Why 0.00s is OK

LangSmith rounds sub-millisecond durations to 0.00s. Non-LLM nodes often complete in <1ms, which appears as 0.00s. To prove these nodes do work:

1. **Node-level tests** in `tests/test_node_execution_sanity.py` verify state changes
2. **Internal timing** via `state.node_timings` (added in M5) records actual milliseconds
3. **Report section** "Node Timings (Internal)" shows per-node execution time

Run the sanity tests:
```bash
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_node_execution_sanity.py -v
```

### LangSmith Metadata for 0.00s Nodes

When `LANGCHAIN_TRACING_V2=true`, non-LLM nodes now include rich metadata in their LangSmith traces:

| Metadata Field | Description | Example |
|----------------|-------------|---------|
| `node_type` | Category of the node | `pure-python`, `db-write`, `api-call` |
| `responsibility` | Human-readable description | "Detect repo profile, build RepoProfile, attach repo context" |
| Tags | Node classification tags | `node_type:pure-python`, `non-llm-node` |

This metadata makes it clear that 0.00s nodes are intentional computation steps, not dead code. Example trace metadata:

```
Node: attach_repo_context
Duration: 0.00s (LangSmith UI)
Metadata:
  node_type: pure-python
  responsibility: Detect repo profile, build RepoProfile, attach repo context
Tags: [node_type:pure-python, non-llm-node]
```

The `NODE_METADATA` catalog in `graph/runtime.py` defines all node descriptions.

## Overview

The Integration Co-Worker uses a multi-provider LLM architecture:

| Node | Provider | Model | Purpose |
|------|----------|-------|---------|
| `understand_task` | Anthropic | claude-sonnet-4 | Task understanding & reasoning |
| `plan_integration_flow` | Anthropic | claude-sonnet-4 | Workflow planning |
| `generate_code_and_tests` | Anthropic | claude-sonnet-4 | Code generation |
| `build_report` | OpenAI | gpt-4o-mini | Report summarization |
| Embeddings | OpenAI | text-embedding-3-small | Spec chunk embeddings |

## Required Dependencies

```bash
# Core LangChain packages
pip install langchain langchain-core langchain-openai

# REQUIRED for Anthropic archetypes - not optional!
pip install langchain-anthropic

# For LangSmith tracing
pip install langsmith
```

**⚠️ Critical**: `langchain-anthropic` is a **hard dependency** when Anthropic archetypes are enabled. Without it, all planning and codegen nodes fall back to heuristics silently.

## Known-Good Environment Configuration

```env
# ====================
# LangSmith Tracing
# ====================
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=lsv2_pt_xxx...  # or LANGSMITH_API_KEY
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
LANGCHAIN_PROJECT=solver-agentic-coworker

# ====================
# OpenAI (required for embeddings + build_report)
# ====================
OPENAI_API_KEY=sk-xxx...

# ====================
# Anthropic (required for planning + codegen nodes)
# ====================
ANTHROPIC_API_KEY=sk-ant-xxx...

# ====================
# Optional: Override OpenAI models ONLY
# ====================
# This ONLY affects OpenAI-provider archetypes
# Does NOT change Anthropic archetypes (they keep claude-*)
LLM_MODEL=gpt-4o-mini

# ====================
# Testing: Force mock LLM
# ====================
# USE_MOCK_LLM=true  # Uncomment for testing without API calls
```

## Environment Variable Behavior

### `LLM_MODEL` is Provider-Aware

The `LLM_MODEL` environment variable respects provider boundaries:

| LLM_MODEL Value | OpenAI Archetypes | Anthropic Archetypes |
|-----------------|-------------------|----------------------|
| `gpt-4o-mini` | ✅ Uses gpt-4o-mini | ❌ Keeps claude-sonnet-4 |
| `claude-3-haiku` | ❌ Keeps gpt-4 | ✅ Uses claude-3-haiku |
| (not set) | Uses archetype default | Uses archetype default |

This prevents accidentally using an OpenAI model with Anthropic's API (or vice versa), which would result in 404 errors.

### `USE_MOCK_LLM=true`

When set, **all** LLM calls (both OpenAI and Anthropic) return mock responses. Nodes that support fallback will use heuristic-based logic instead.

## Troubleshooting Checklist

### "LangSmith traces show 0.00s and no LLM calls"

1. **Check `USE_MOCK_LLM`**
   ```bash
   echo $USE_MOCK_LLM  # Should be empty or "false"
   ```

2. **Check `LANGCHAIN_API_KEY` or `LANGSMITH_API_KEY`**
   ```bash
   # At least one must be set
   echo $LANGCHAIN_API_KEY
   echo $LANGSMITH_API_KEY
   ```

3. **Check `langchain-anthropic` is installed**
   ```bash
   pip show langchain-anthropic  # Should show version info
   ```

4. **Check `LLM_MODEL` isn't cross-provider**
   ```bash
   # If set, should match the provider:
   # - gpt-* for OpenAI archetypes
   # - claude-* for Anthropic archetypes
   echo $LLM_MODEL
   ```

5. **Run the health check**
   ```bash
   PYTHONPATH=src python -m integration_coworker.cli health --check-llm
   ```

### "Anthropic API returns 404"

This usually means:
- Wrong model name (outdated model version)
- Cross-provider model mismatch (`LLM_MODEL=gpt-4o-mini` applied to Anthropic)

**Fix**: Check archetype YAML files have valid model names:
```yaml
# Current valid Anthropic model names (as of Dec 2025)
model:
  provider: anthropic
  name: claude-sonnet-4-5-20250929  # ✅ Current (alias: claude-sonnet-4-5)
  # name: claude-3-5-sonnet-20241022  # ✅ Still supported
  # name: claude-3-sonnet-20240229  # ❌ Deprecated
```

### "Nodes fall back to heuristics"

Check logs for warnings like:
```
LLM call failed, using heuristics: ...
Falling back to template skeleton for client '...' because: ...
```

Common causes:
1. Missing API key for the provider
2. Network issues
3. Rate limiting
4. Model not found (404)

## Archetype Configuration

Each node uses an archetype YAML file that defines its LLM configuration:

```
src/integration_coworker/config/archetypes/
├── _base.archetype.yaml           # OpenAI defaults
├── understand_task.archetype.yaml # Anthropic
├── plan_integration_flow.archetype.yaml # Anthropic
├── generate_code_and_tests.archetype.yaml # Anthropic
├── build_report.archetype.yaml    # OpenAI (inherits _base)
└── ...
```

### Invariant: Provider-Model Consistency

```python
# This invariant is tested automatically:
if provider == "anthropic":
    assert model.startswith("claude-")
if provider == "openai":
    assert not model.startswith("claude-")
```

Run the test to verify:
```bash
pytest tests/test_llm_wiring.py::TestArchetypeConfigurationInvariants -v
```

## LangSmith Integration

LangChain automatically traces LLM calls when these conditions are met:

1. `LANGCHAIN_TRACING_V2=true`
2. `LANGCHAIN_API_KEY` or `LANGSMITH_API_KEY` is set
3. Using LangChain LLM classes (`ChatOpenAI`, `ChatAnthropic`)

Each node's LLM call appears as a child span under the workflow run in LangSmith.

### Verifying Traces

After running a demo:
1. Go to https://smith.langchain.com
2. Select your project (from `LANGCHAIN_PROJECT`)
3. Look for the workflow run
4. Each node should have:
   - Non-zero latency (not 0.00s)
   - Child LLM call span
   - Input/output tokens tracked

## Architecture Note

Repo profiling and archetypes are **orthogonal** to LLM provider configuration:

- **Archetypes** define: model, temperature, prompts, retrieval settings
- **Repo profiles** define: file layout, naming conventions
- **KG + graph traversal** decide: context, patterns, templates

The system remains "truly agentic" because:
1. Nodes still use archetype-driven prompts
2. KG retrieval provides context-aware patterns
3. LLM providers are an implementation detail, correctly wired through archetypes

## Quick Commands

```bash
# Check health
integration-coworker health

# Check with LLM connectivity test
integration-coworker health --check-llm

# Run demo with verbose logging
USE_SQLITE=true integration-coworker demo --verbose

# Run LLM wiring tests
pytest tests/test_llm_wiring.py -v
```

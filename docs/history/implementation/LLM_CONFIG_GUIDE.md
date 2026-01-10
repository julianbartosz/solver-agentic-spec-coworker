# LLM Configuration Guide

> **Status**: Archived – kept for historical context only.
>
> Current LLM configuration is in [`docs/GETTING_STARTED.md#llm-configuration`](../../GETTING_STARTED.md#llm-configuration).
>
> **Last Updated**: December 2025

---

## Table of Contents

1. [Overview](#overview)
2. [Supported Models](#supported-models)
3. [Environment Variables](#environment-variables)
4. [Node Classification](#node-classification)
5. [TOON Format](#toon-format)
6. [Archetype Configuration](#archetype-configuration)
7. [LangSmith Integration](#langsmith-integration)
8. [Troubleshooting](#troubleshooting)

---

## Overview

The Integration Co-Worker uses a multi-provider LLM architecture:

| Node | Provider | Model | Purpose |
|------|----------|-------|---------|
| `understand_task` | Anthropic | claude-sonnet-4 | Task understanding & reasoning |
| `plan_integration_flow` | Anthropic | claude-sonnet-4 | Workflow planning |
| `generate_code_and_tests` | Anthropic | claude-sonnet-4 | Code generation |
| `build_report` | OpenAI | gpt-4o-mini | Report summarization |
| Embeddings | OpenAI | text-embedding-3-small | Spec chunk embeddings |

---

## Supported Models

### OpenAI
- `gpt-4o` — Best for complex reasoning
- `gpt-4o-mini` — Fast, cost-effective
- `gpt-4-turbo` — Older, still supported

### Anthropic
- `claude-sonnet-4-5` — Latest, most capable
- `claude-3-5-sonnet` — Previous generation

---

## Environment Variables

### Required

```bash
# OpenAI (for embeddings + build_report)
export OPENAI_API_KEY="sk-..."

# Anthropic (for planning + codegen nodes)
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Optional

```bash
# Override OpenAI models ONLY (does NOT change Anthropic archetypes)
export LLM_MODEL="gpt-4o-mini"

# Force mock LLM (testing only)
export USE_MOCK_LLM="true"
```

### LangSmith Tracing

```bash
export LANGCHAIN_TRACING_V2="true"
export LANGCHAIN_API_KEY="lsv2_pt_..."
export LANGCHAIN_PROJECT="integration-coworker"
```

---

## Node Classification

Some nodes show **0.00s duration** in LangSmith. This is expected for non-LLM nodes that execute very fast.

| Node | LangSmith Duration | Behavior Type |
|------|-------------------|---------------|
| `plan_run` | 0.00s | Pure Python planning |
| `ingest_spec` | 0.00s | File I/O |
| `build_silver_api_model` | 0.00s | Model extraction |
| `embed_spec_chunks` | ~200-500ms | **API call** (OpenAI) |
| `understand_task` | ~2-5s | **LLM call** (Anthropic) |
| `plan_integration_flow` | ~3-8s | **LLM call** (Anthropic) |
| `generate_code_and_tests` | ~10-30s | **LLM call** (Anthropic) |
| `build_report` | ~1-2s | **LLM call** (OpenAI) |
| `persist_results` | 0.00s | DB writes |

Non-LLM nodes include rich metadata in LangSmith traces:
- `node_type`: `pure-python`, `db-write`, `api-call`
- `responsibility`: Human-readable description
- Tags: `node_type:pure-python`, `non-llm-node`

---

## TOON Format

The Integration Co-Worker uses **TOON (Token-Oriented Object Notation)** for LLM communication.

TOON is more compact than JSON and uses special tokens:
- `<|>` — Field separator
- `<|field_name|>` — Field marker

### Example

```
<|action|>create<|>
<|resource|>checkout_session<|>
<|endpoint|>POST /checkout/sessions<|>
```

### Implementation

```python
from integration_coworker.llm.toon import to_toon, from_toon, toon_response_format

# Convert dict to TOON
toon_str = to_toon({"action": "create", "resource": "checkout"})

# Parse TOON response
result = from_toon(toon_str)

# Get response format for LLM
format_spec = toon_response_format(["action", "resource", "endpoint"])
```

---

## Archetype Configuration

Each node uses an archetype YAML file:

```
src/integration_coworker/config/archetypes/
├── _base.archetype.yaml           # OpenAI defaults
├── understand_task.archetype.yaml # Anthropic
├── plan_integration_flow.archetype.yaml # Anthropic
├── generate_code_and_tests.archetype.yaml # Anthropic
└── build_report.archetype.yaml    # OpenAI
```

### Provider-Model Invariant

```python
# Automatically tested:
if provider == "anthropic":
    assert model.startswith("claude-")
if provider == "openai":
    assert not model.startswith("claude-")
```

---

## LangSmith Integration

LangChain automatically traces LLM calls when:
1. `LANGCHAIN_TRACING_V2=true`
2. `LANGCHAIN_API_KEY` is set
3. Using LangChain LLM classes

Each node's LLM call appears as a child span in LangSmith.

### Verify Traces

1. Run: `python -m integration_coworker.cli demo --persist`
2. Open [smith.langchain.com](https://smith.langchain.com/)
3. Select your project → look for the workflow run

---

## Troubleshooting

### "LangSmith traces show 0.00s and no LLM calls"

1. Check `USE_MOCK_LLM`:
   ```bash
   echo $USE_MOCK_LLM  # Should be empty or "false"
   ```

2. Check `LANGCHAIN_API_KEY` is set

3. Check `langchain-anthropic` is installed:
   ```bash
   pip show langchain-anthropic
   ```

### "Anthropic API returns 404"

- Wrong model name (outdated version)
- Cross-provider mismatch (`LLM_MODEL=gpt-4o-mini` applied to Anthropic)

### "Nodes fall back to heuristics"

Check logs for warnings:
```
LLM call failed, using heuristics: ...
Falling back to template skeleton for client '...' because: ...
```

Common causes:
1. Missing API key
2. Rate limiting
3. Network issues

### Quick Commands

```bash
# Check health
integration-coworker health --check-llm

# Run with verbose logging
USE_SQLITE=true integration-coworker demo --verbose

# Test LLM wiring
pytest tests/test_llm_wiring.py -v
```

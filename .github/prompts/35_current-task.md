# Current Task — Agentic Integration Designer

This file describes the **single most important active task** for this repo.

It is **managed by tools**:
- MCP: `memory_update_context`
- CLI: `npm run memory:update-context`

Agents should read this before doing multi-step work, and update it when the main focus changes.

---

<!-- mem:current-task:start -->
## 2025-11-20 — Active Focus

Area: Phase 0 — Design & Scaffolding  
Status: active  

Summary:
- Stand up the initial project scaffolding for the Agentic Integration Designer:
  - Python package layout
  - LangGraph workflow shell
  - Postgres/pgvector wiring
  - Prompt/memory system for Copilot

Key files:
- `.github/copilot-instructions.md`
- `.github/prompts/00_project-brief.md`
- `.github/prompts/10_system-patterns.md`
- `.github/prompts/30_development-status.md`
- `.github/prompts/90_decision-log.md`
- `src/agentic_integration/__init__.py`
- `src/agentic_integration/langgraph_graph.py`
- `src/agentic_integration/workflow_state.py`
- `src/agentic_integration/models_silver.py`
- `src/agentic_integration/models_gold.py`
- `src/agentic_integration/db.py`
- `src/agentic_integration/cli.py`

Next steps:
- Create minimal Python package structure and empty node stubs for all workflow steps.
- Define `WorkflowState` and `IntegrationResult` types aligned with the design doc.
- Add initial DB models for `spec_silver` and `integration_gold` (skeleton tables).
- Wire a CLI entrypoint that calls `design_and_generate_integration` with a stubbed implementation.
<!-- mem:current-task:end -->

---

## How to Update This File

Use one of:

- MCP tool:
  - `memory_update_context` with keys:
    - `area` — e.g. `Phase 1 - Silver ingestion`
    - `status` — e.g. `active`, `paused`, `blocked`
    - `summary` — 1–3 sentences describing the current push
    - `files` — comma/space-separated list of key paths
    - `next` — pipe-separated bullets for next steps
- CLI:
  - `npm run memory:update-context -- area="Phase 1" status="active" summary="…" files="…" next="…|…"`

Update when the **main focus changes** (e.g. new phase, new feature, major refactor).

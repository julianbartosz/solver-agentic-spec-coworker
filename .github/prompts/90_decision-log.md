# Decision Log — Agentic Integration Designer

Append-only record of **important technical and product decisions**.

This is managed by tools:
- MCP: `memory_log_decision`
- CLI: `npm run memory:log-decision`

Each line should capture:
- Date/time
- Decision
- Rationale
- Optional links (issue/PR/docs)

---

<!-- mem:decision-log:start -->
2025-11-20 15:00 — Decision: Use Postgres + pgvector as the single store for Silver and Gold layers — Rationale: Simplifies deployment, keeps vector + relational data co-located, and matches Subatomic’s GCP environment — Links: design doc §3.2, §4.3

2025-11-20 15:10 — Decision: Model the integration workflow as a LangGraph state machine with explicit nodes for planning, ingestion, Silver extraction, task understanding, planning, policy attachment, codegen, repo wiring, validation, persistence, and reporting — Rationale: Makes each concern testable in isolation and visible in Langsmith traces — Links: design doc §5.1–5.4

2025-11-20 15:20 — Decision: Use `.github/prompts/` prompt files + MCP tools as the canonical project memory instead of `memory-bank/` — Rationale: Aligns with native Copilot prompt file support and keeps memory first-class for chat modes and CI — Links: `.github/copilot-instructions.md`, VS Code prompt files docs
<!-- mem:decision-log:end -->

---

## How to Append New Decisions

Use one of:

- MCP tool:
  - `memory_log_decision` with keys:
    - `decision` — short description
    - `rationale` — why we chose this path
    - `links` — PRs/issues/docs (optional)
- CLI:
  - `npm run memory:log-decision -- decision="…" rationale="…" links="#123|design doc"`

Tools will append a new line **inside** the `mem:decision-log` block with a timestamp.

Capture decisions when you:

- Lock in a data model or schema change
- Choose a specific pattern (auth, retries, pagination, logging)
- Change how LangGraph nodes are wired or configured
- Introduce or remove major dependencies

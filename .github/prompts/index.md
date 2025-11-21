# Prompt Files Index — Agentic Integration Designer

This index explains how prompt files in `.github/prompts/` are used with GitHub Copilot
and custom chat modes. Prompt files are **on-demand, task-specific prompts**, not
global instructions.:contentReference[oaicite:1]{index=1}

---

## Files

- `00_project-brief.md`
  - High-level problem, goals, scope, success criteria, and main scenarios.
  - Use when you need the **big picture** or when scaffolding new components.

- `10_system-patterns.md`
  - Core architecture, medallion model, Silver/Gold schemas, LangGraph topology,
    RAG / GraphRAG patterns, and non-functional requirements.
  - Use when you need to reason about **architecture, DB models, or node design**.

- `30_development-status.md`
  - Living “Done / Doing / Next” snapshots.
  - Updated via tools:
    - `memory_update_progress`
    - `npm run memory:update-progress`
  - Use when deciding what to work on **today** or when summarizing progress.

- `35_current-task.md`
  - Single most important active task, key files, and next steps.
  - Updated via tools:
    - `memory_update_context`
    - `npm run memory:update-context`
  - Chat modes should usually include this file to stay aligned with the current focus.

- `90_decision-log.md`
  - Append-only log of important design decisions (date, decision, rationale, links).
  - Updated via tools:
    - `memory_log_decision`
    - `npm run memory:log-decision`
  - Use when you need to understand **why** something is the way it is.

---

## Usage with Chat Modes

Typical chat mode compositions:

- **Phase 0 / Scaffolding Mode**
  - Include:
    - `00_project-brief.md`
    - `10_system-patterns.md`
    - `30_development-status.md`
    - `35_current-task.md`
    - `90_decision-log.md`
    - `.github/copilot-instructions.md`
  - Goal: create or refine package layout, node stubs, DB models, and basic CLI.

- **Current Task Driver Mode**
  - Include:
    - `35_current-task.md`
    - `30_development-status.md`
    - `10_system-patterns.md` (for architectural guardrails)
    - `.github/copilot-instructions.md`
  - Goal: execute the next small steps for the active task, then:
    - Run / suggest tests
    - Update dev status and decision log via tools.

---

Keep this index short and stable.  
Dynamic details belong in the other prompt files.

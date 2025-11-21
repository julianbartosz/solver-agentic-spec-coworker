Below is a **fully structured, symbol-consistent, hierarchical rewrite** of your unstructured markdown.
All content is preserved.
No semantics changed.
Just clean, organized, professional Markdown.

---

# `.github/copilot-instructions.md`

## **Agentic Integration Designer — Copilot Ruleset**

---

## **1. 🎯 Purpose**

This file defines the **stable GitHub Copilot ruleset** for this repository.

### **1.1 Scope**

* **High-level rules & preferences** → defined here.
* **Project memory** → `.github/prompts/*.md` (updated via MCP tools + CLI).
* **Per-task behavior** → controlled by chat modes referencing prompt files.

### **1.2 Requirements for Copilot**

* ALWAYS read this file **and** relevant `.github/prompts/*` files before making a plan.
* Prefer **chat modes** or **prompt-file references** over duplicating content.

---

## **2. 📦 Project Overview (for Copilot)**

This repo implements the **Agentic API Integration Designer & Code Generator**.

### **2.1 Inputs**

* `spec_refs` (OpenAPI / HTML / PDF)
* `task_description` (natural language)
* optional: `provider_code`, `repo_root`, `repo_profile`

### **2.2 Outputs**

* Parsed spec + **Silver** API model
* Planned **Gold** integration workflow graph
* Generated:

  * client code
  * workflow code
  * tests
  * config
* Optional repo wiring into a target repo
* Persisted Silver/Gold records in Postgres + pgvector
* Human-readable run report

### **2.3 Primary Public API**

```python
design_and_generate_integration(
    spec_refs,
    task_description,
    provider_code: str | None = None,
    repo_root: str | None = None,
    repo_profile: str | None = None,
    options: dict | None = None,
) -> IntegrationResult
```

---

## **3. 🧱 Architecture & Tech Stack**

### **3.1 Language & Runtime**

* Python **3.11+**
* Package name: **agentic_integration**

### **3.2 Core Libraries**

* **LangGraph** (agent workflow)
* **Postgres** + **pgvector (1536 dim)**
* **SQLAlchemy** or typed DB layer for Silver/Gold persistence

### **3.3 Database Schemas**

* **spec_silver**

  * spec documents, sections, endpoints, schemas, fields, entities, events
* **integration_gold**

  * tasks, workflow templates, nodes, edges, endpoint bindings, policies, code artifacts

### **3.4 Workflow Graph (LangGraph Nodes)**

1. `plan_run`
2. `ingest_spec`
3. `detect_and_parse_spec`
4. `build_silver_api_model`
5. `understand_task`
6. `align_task_with_kg`
7. `plan_integration_flow`
8. `attach_policies_and_patterns`
9. `generate_code_and_tests`
10. `analyze_repo_layout` *(repo-aware only)*
11. `apply_repo_integration_changes` *(repo-aware only)*
12. `validate_integration_design`
13. `persist_results`
14. `build_report`

---

## **4. 📂 Copilot Memory Layout**

Treat `.github/prompts/` as the **canonical project memory**.

### **4.1 Key Prompt Files**

| File                       | Purpose                                       |
| -------------------------- | --------------------------------------------- |
| `00_project-brief.md`      | Project overview & success criteria           |
| `10_system-patterns.md`    | Architecture, medallion model, workflow nodes |
| `30_development-status.md` | Done / Doing / Next snapshots (living)        |
| `35_current-task.md`       | Current focus, key files, next steps (living) |
| `90_decision-log.md`       | Append-only decision log                      |
| `_index.md`                | Optional index / TOC                          |

### **4.2 Rules for Copilot**

* **Do NOT rewrite** `.github/prompts/*.md` manually.
* Update memory using **MCP tools** or **CLI wrapper scripts**:

#### MCP Tools

* `memory_update_context`
* `memory_update_progress`
* `memory_log_decision`

#### CLI Wrappers

* Example: `npm run memory:update-context`

### **4.3 Behavior in Agent Mode**

* Always load the relevant prompt files via chat modes rather than copying content.

---

## **5. 🧩 MCP & Tools**

This repo runs a **local MCP server** so Copilot can update memory files and access project context.

### **5.1 MCP Tools**

| Tool                     | Purpose                                            |
| ------------------------ | -------------------------------------------------- |
| `memory_update_context`  | Updates 35_current-task.md                         |
| `memory_update_progress` | Updates 30_development-status.md (Done/Doing/Next) |
| `memory_log_decision`    | Appends an entry to 90_decision-log.md             |

### **5.2 Guidance for Copilot**

After significant progress:

* Call **`memory_update_progress`**
* If architecture/patterns changed → call **`memory_log_decision`**
* When switching tasks → call **`memory_update_context`**

---

## **6. 💬 Chat Modes & Prompt Files**

Chat modes compose project memory into tailored workflows.

### **6.1 Typical Modes**

#### **Phase 0 — Scaffolder**

Includes the entire memory stack:

* project brief
* system patterns
* dev status
* current task
* decision log

Goal:
Generate or refine:

* Python package skeleton
* LangGraph stubs
* DB/persistence layer

#### **Current Task Driver**

Focus: contents of `35_current-task.md`.

Workflow for Copilot:

1. Read current task + dev status
2. Produce a small, reversible plan
3. Apply minimal safe edits
4. Suggest or run tests
5. Update memory via MCP tools

### **6.2 Guidance**

* Follow chat-mode workflow first
* Prefer:

  * small diffs
  * explicit TODOs
  * consistency with Silver/Gold terminology

---

## **7. 🧪 Coding Style & Conventions**

### **7.1 General Rules**

* Keep files **≤ 200–250 LOC**
* Extract helpers early
* Prefer typed, minimal APIs
* Use `@dataclass` or Pydantic for:

  * WorkflowState
  * IntegrationResult
  * Domain models

### **7.2 Project Structure**

* `config.py`
* `db.py`
* `models_*.py`
* `langgraph_graph.py`
* `cli.py`

### **7.3 LangGraph Nodes**

Node signature:

```python
def node_name(state: WorkflowState) -> WorkflowState:
    ...
```

Node docstrings must specify:

* Which design-doc section they implement
* Which Silver/Gold tables/fields they touch

### **7.4 DB Models**

* Mirror schema exactly
* Prefer explicit columns over JSON

### **7.5 Testing**

Prefer:

* Unit tests for parsing/normalization
* Node-level tests against a test DB
* Small E2E scenarios with stub spec + stub repo

---

## **8. ⚙️ Operational Practices**

### **8.1 Commits**

* Commit after each coherent step
* Keep PRs focused on one phase/task

### **8.2 Documentation**

When adding new behavior:

* Update prompt files via tools
* Optionally add a design note under `docs/`

### **8.3 CI**

* If core source changes but prompts do not, CI may request memory updates
* Use branch name `[*skip-memory*]` **only when correct**

---

## **9. 🧠 What Not To Do**

### ❌ Avoid:

* Dumping giant code/spec blobs into this file
* Editing `.github/prompts/*.md` manually (except when explicitly asked)
* Adding new MCP tools casually
* Excessive abstraction
* Diverging from Silver/Gold structure
* Ignoring DB schema terminology

---

## **End of `.github/copilot-instructions.md`**
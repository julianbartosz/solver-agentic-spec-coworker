# Project Brief — Agentic API Integration Designer & Code Generator

## 0. Metadata

- **Title:** Agentic API Integration Designer & Code Generator
- **Author:** Julian Bartosz
- **Reviewers:** Karl Simon, Aaron Sosa
- **Version / Status:** v1 (Design)
- **Date:** 2025-11-20

This prompt file gives Copilot the **high-level intent and goals** of the project.  
Implementation details, node contracts, and patterns live in system-pattern and other prompts.

---

## 1. Problem

For each new client or product integration, a senior engineer currently has to manually read API specs and design everything from scratch:

- Endpoint call order and orchestration
- Request / response mappings to internal models
- Auth, error handling, retries, pagination, and rate limits
- Code structure that matches team patterns and repo layout

This causes:

- Slow, inconsistent integrations
- Poor scalability as API and task volume grows
- Variability in security, logging, observability, error handling, and retries

We are missing a consistent, repeatable pipeline from:

- API specification → integration workflow design
- API specification → working code + tests
- API specification → reusable integration artifacts and patterns

This gap slows onboarding, delays project starts, and reduces consistency and quality.

---

## 2. Goal (v1)

Create a **runtime-callable, LangGraph-based “Integration Co-Worker”** that can ingest an integration task plus API spec(s) and produce:

- Parsed specification (OpenAPI / HTML / PDF)
- A normalized API model (Silver) with:
  - Endpoints, parameters, schemas, entities, events, relationships
- A planned integration workflow graph (Gold)
- Generated integration code, tests, and config files
- A structured “integration design” stored for reuse in Postgres
- Optional **repo-aware code updates** into a reference service repository

The system follows a medallion-style structure:

- **Bronze:** raw specs and prompts
- **Silver:** normalized API surface (endpoints, schemas, entities, events, relationships, embeddings)
- **Gold:** integration tasks, workflows, endpoint bindings, policies, and code artifacts

---

## 3. v1 Success Criteria

Across at least **10 diverse public APIs** (e.g. Stripe, GitHub, Twilio, Slack, Shopify):

- Generated code should **run with only minor manual edits**
- Developers can integrate generated modules into a repo and run smoke tests in **< 1 hour**
- For the reference repo:
  - The system can generate code **and** register new integrations into the repo automatically (routers, services, jobs, etc.)
- Generated flows must respect baseline patterns:
  - Authentication
  - Logging + PII redaction
  - Idempotency + transient retries
  - Pagination + rate limit handling

**Operational targets:**

- End-to-end run (spec → workflow → code → repo updates → report) in **< 5 minutes**
- Silver + Gold stored in **Postgres with pgvector**
- Single Python function + CLI entrypoint
- The system should provide **clear value over “raw LLM in a notebook”** via:
  - Consistent patterns and policies
  - Reusable knowledge graph / workflow templates
  - Automatic repo wiring for supported repos

---

## 4. Scope & Inputs (v1)

**Source systems / specs:**

- Public or semi-public HTTP APIs with:
  - OpenAPI / Swagger (JSON or YAML)
  - HTML / Markdown docs
  - PDFs

**Inputs to the coworker:**

- `spec_refs` (URLs or local file paths)
- `task_description` (natural language)
- Optional:
  - `provider_code` (short identifier)
  - `repo_root` (local path to reference service repo)
  - `repo_profile` (layout profile)
  - Sandbox / test credentials
  - System metadata

**Out of scope (v1 non-goals):**

- Full production deployment stack
- Full ETL/ELT pipelines
- File-based integration guides (CSV / EDI)
- Non-Python runtimes
- Custom LLM training or fine-tuning

---

## 5. Users & Main Scenarios

**Primary users:**

- Integration & backend engineers
- Solution / platform architects
- Platform engineers owning shared integration patterns

**Key scenarios:**

1. **New Integration Flow**

   - Input:
     - Spec URL(s)
     - Task, e.g. “Create Stripe subscription checkout flow”
   - Output:
     - Planned integration workflow graph
     - Provider client wrappers
     - Workflow modules
     - Config and tests
     - Human-readable design summary and run report

2. **Provider Upgrade / Refactor**

   - Compare old vs new Gold models and show:
     - Affected tasks, flows, endpoint bindings
     - Code artifacts that need updates

3. **Pattern Development & Reuse**

   - Knowledge graph captures:
     - Workflows and steps
     - Endpoint patterns (pagination, webhooks, retries, logging)
   - New tasks reuse existing patterns instead of starting from scratch.

---

## 6. Business Value & Context

This system shifts early integration work from manual engineering to a **semi-automatic, repeatable pipeline**:

- Faster path from spec → integration skeleton
- Higher consistency across integrations
- Standardized auth / logging / retry / pagination policies
- Reusable workflows and patterns across providers and teams

It is also a live application of:

- Medallion architecture (Bronze / Silver / Gold)
- RAG and GraphRAG over specs and workflows
- LangGraph-style orchestration and planning
- Knowledge graph–backed workflow and pattern reuse

The ultimate goal is to **reduce senior engineer time** on boilerplate and plumbing, and instead focus them on domain-specific edge cases and validation.

---

# V1.1 Ideas — Future Feature Roadmap

This document captures ideas for V1.1+ features that emerged during V1 hardening.
These are prioritized suggestions, not commitments.

---

## Priority 1: Quick Wins (V1.1)

### 1.1 Strict Codegen Mode with Real LLM
- Current state: Mock LLM produces template code with unused imports, long lines
- Improvement: Add `--strict-codegen` flag that runs ruff and py_compile before returning
- LLM prompt could include "ensure all imports are used" instruction

### 1.2 Repo Provider Completion
- Current state: GitHub provider tested directly but not wired through `attach_repo_context`
- Improvement: Add `repo_source` field to `IntegrationOptions` to enable GitHub/GitLab sources
- Support syntax: `--repo-source github:owner/repo@branch`

### 1.3 KG Template Seeding
- Current state: KG is empty on first run, always uses fallback workflow
- Improvement: Bundle pre-seeded templates for common patterns (CRUD, payment flows)
- Ship with `templates/stripe.json`, `templates/generic_crud.json`

### 1.4 Better Report Formatting
- Current state: Report markdown has raw timings, could be prettier
- Improvement: Add sparkline visualizations, color coding for timings
- Consider HTML report output option

---

## Priority 2: Reliability Enhancements (V1.2)

### 2.1 Retry Logic for LLM Calls
- Current state: Single attempt, failure = workflow failure
- Improvement: Add configurable retry with exponential backoff
- Track retry counts in report

### 2.2 Partial Run Recovery
- Current state: If run fails mid-workflow, no recovery possible
- Improvement: Checkpoint state after each node, enable `--resume` flag
- Store checkpoints in database with run_id

### 2.3 Rate Limit Awareness
- Current state: No awareness of API rate limits
- Improvement: Add rate limit tracking for both LLM and spec APIs
- Warn when approaching limits, auto-throttle

### 2.4 Spec Caching
- Current state: Re-fetches and parses spec on every run
- Improvement: Cache parsed specs with content hash
- `--no-cache` flag to force refresh

---

## Priority 3: Developer Experience (V1.3)

### 3.1 Interactive Mode
- Add `integration-coworker interactive` that opens a REPL
- Allow step-by-step workflow execution with inspection
- Useful for debugging and learning

### 3.2 VSCode Extension
- Task sidebar showing KG contents
- Run integration from command palette
- View generated code diff before applying

### 3.3 Watch Mode
- `integration-coworker watch --spec-ref ./api.yaml`
- Re-runs workflow on spec changes
- Useful for iterative development

### 3.4 Dry Run Preview
- `--preview` flag that shows what would be generated
- No database writes, no file changes
- Markdown diff output

---

## Priority 4: Multi-Provider Features (V1.4)

### 4.1 Cross-Provider Workflow Composition
- Current state: Single provider per task
- Improvement: Allow tasks that span multiple providers
- Example: "Create Stripe customer then sync to Salesforce"

### 4.2 Provider Orchestration Patterns
- Saga pattern support for multi-step workflows
- Compensation/rollback logic generation
- Circuit breaker pattern for unreliable APIs

### 4.3 API Gateway Integration
- Generate Kong/Envoy configuration
- Rate limiting, auth forwarding
- Service mesh awareness

---

## Priority 5: Advanced KG Features (V1.5)

### 5.1 KG Learning from Logs
- Analyze production logs to improve workflow templates
- Identify common error patterns, suggest fixes
- Update KG with real-world usage data

### 5.2 Semantic Search Improvements
- Fine-tuned embeddings for API domain
- Hybrid search: semantic + keyword + graph
- User feedback loop for relevance tuning

### 5.3 Template Versioning
- Version control for KG templates
- Rollback to previous template versions
- A/B testing for template effectiveness

### 5.4 Cross-Repo KG Sharing
- Share KG templates across projects
- Organization-level template library
- Community template marketplace

---

## Ideas Parking Lot

These ideas need more research/validation:

- **WebSocket API Support**: Real-time spec formats, event-driven workflows
- **GraphQL Support**: Schema introspection, codegen for queries/mutations  
- **gRPC/Protobuf**: Protocol buffer parsing, service definitions
- **AsyncAPI**: Event-driven architecture, message broker configs
- **OpenTelemetry Integration**: Automatic tracing instrumentation in generated code
- **Policy-as-Code**: OPA/Rego integration for runtime policy enforcement
- **Multi-Language Codegen**: TypeScript, Go, Rust client generation
- **AI Code Review**: LLM reviews generated code before committing

---

## Feedback Welcome

Add ideas here or create issues with tag `[v1.1-idea]`.

Last updated: 2025

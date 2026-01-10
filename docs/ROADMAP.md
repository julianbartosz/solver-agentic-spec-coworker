# Roadmap

> **Last updated**: January 2026

This roadmap prioritizes work for the next 90 days. Items are organized by time horizon with explicit non-goals to prevent scope creep.

---

## Now (0–30 days)

### Handoff Essentials
- [x] CODEOWNERS file for PR review routing
- [x] RELEASE.md with versioning and release process
- [x] First-week maintainer runbook
- [x] Access and ownership documentation
- [x] Live demo guide with before/after proof

### Documentation Cleanup
- [ ] Move historical audits to `docs/history/`
- [ ] Consolidate production readiness docs into single checklist
- [ ] Update mkdocs nav to reduce noise

### Demo Improvements
- [ ] Record 10-minute demo video (happy path)
- [ ] Create demo repo with WireMock stub server
- [ ] Document common failure modes with recovery steps

---

## Next (31–60 days)

### Database & Persistence
- [ ] ADR for migrations strategy (Alembic vs manual)
- [ ] Backup/restore procedure for Postgres mode
- [ ] Document data retention policy

### Environment Formalization
- [ ] Formalize environment matrix (dev, demo, staging, prod)
- [ ] Document environment-specific configuration
- [ ] Add environment validation to CI

### Testing Improvements
- [ ] Increase code coverage to 70%
- [ ] Add integration tests for all critical paths
- [ ] Document test pyramid and coverage expectations

---

## Later (61–90 days)

### Reliability & DR
- [ ] DR expectations (RTO/RPO) even if informal
- [ ] Failure testing / chaos engineering basics
- [ ] Load testing and documented performance baselines

### Security & Compliance
- [ ] Dependency update policy + vuln response workflow
- [ ] Security audit of prompt injection defenses
- [ ] Formalize audit log retention expectations

### Developer Experience
- [ ] VSCode extension for task sidebar (FT-010)
- [ ] Interactive REPL mode (FT-008)
- [ ] Watch mode for spec changes (FT-009)

---

## Explicit Non-Goals (This Phase)

These are **not** in scope for the next 90 days:

| Non-Goal | Reason |
|----------|--------|
| Multi-tenant hosted service | Focus on single-tenant/self-hosted first |
| Auto-apply code without HITL | Safety requires human-in-the-loop approval |
| Multi-language output beyond Python | Python-first; TS/Go/Java are future work |
| Real-time streaming UI | Batch processing is sufficient for v1 |
| Custom LLM fine-tuning | Use off-the-shelf models for now |

---

## Backlog Triage Notes

See [TECHNICAL_DEBT_REGISTER.md](decisions/TECHNICAL_DEBT_REGISTER.md) for the full debt inventory (36 items).

**Top 5 debt items by impact:**
1. `REC-002` - No checkpoint persistence (blocks resume across restarts)
2. `KG-001` - Hardcoded templates (blocks learning from production)
3. `SEC-001` - No system prompt hardening (security risk)
4. `REPO-007` - No remote repo support (limits CI integration)
5. `LLM-001` - In-memory embeddings (blocks cross-run retrieval)

---

## How to Update This Roadmap

1. Review monthly in team sync
2. Move completed items to CHANGELOG.md
3. Promote "Next" items to "Now" as capacity allows
4. Add new items to "Later" or backlog first

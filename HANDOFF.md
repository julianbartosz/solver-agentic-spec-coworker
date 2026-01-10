# Handoff Checklist

> **Purpose**: Definition of done for handing off this repository to a new team.  
> **Status**: Ready for review

---

## Quick Links

| Document | Purpose |
|----------|---------|
| [ROADMAP.md](docs/ROADMAP.md) | 30/60/90 day priorities and non-goals |
| [RELEASE.md](RELEASE.md) | Versioning, changelog, release process |
| [docs/operations/RUNBOOK.md](docs/operations/RUNBOOK.md) | Operational procedures and incident response |
| [docs/operations/FIRST_WEEK.md](docs/operations/FIRST_WEEK.md) | New maintainer onboarding checklist |
| [docs/operations/OWNERSHIP_AND_ACCESS.md](docs/operations/OWNERSHIP_AND_ACCESS.md) | Access, contacts, escalation |
| [docs/LIVE_FIRE_DEMO_GUIDE.md](docs/LIVE_FIRE_DEMO_GUIDE.md) | 10-minute demo script |
| [docs/development/architecture.md](docs/development/architecture.md) | System architecture reference |
| [docs/decisions/TECHNICAL_DEBT_REGISTER.md](docs/decisions/TECHNICAL_DEBT_REGISTER.md) | Known debt and sharp edges |

---

## Acceptance Criteria

### Code Quality
- [ ] CI is green on `main` branch
- [ ] Lint passes: `ruff check src/ tests/`
- [ ] Security scan passes: `bandit -c pyproject.toml -r src tests`
- [ ] Test coverage ≥ 60%

### Documentation
- [ ] README has working quickstart commands
- [ ] Architecture docs are current and accurate
- [ ] All ADRs are up to date
- [ ] Technical debt register is complete

### Demo
- [ ] Demo flow passes: `USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker demo`
- [ ] Demo guide can be followed by someone unfamiliar with the project
- [ ] Before/after proof is demonstrable

### Operations
- [ ] RUNBOOK covers common failure modes
- [ ] Health checks are documented
- [ ] Emergency switches are documented (USE_MOCK_LLM, USE_SQLITE)

### Access & Ownership
- [ ] CODEOWNERS file exists and is accurate
- [ ] Ownership doc has real names and contacts
- [ ] Access request process is documented
- [ ] Credential rotation process is documented

### Handoff Meeting
- [ ] Walkthrough of architecture (use Code Tour)
- [ ] Walkthrough of demo (use Live Fire Demo Guide)
- [ ] Q&A session completed
- [ ] New owner can run tests locally
- [ ] New owner can deploy (if applicable)

---

## Validation Commands

Run these to verify handoff readiness:

```bash
# 1. Environment
./scripts/setup_env.sh --check

# 2. Tests
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v

# 3. Lint
ruff check src/ tests/

# 4. Security
bandit -c pyproject.toml -r src tests

# 5. Docs
mkdocs build --strict

# 6. Demo
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker demo
```

---

## Sign-Off

| Role | Name | Date | Signature |
|------|------|------|-----------|
| Outgoing Owner | Julian Bartosz | | |
| Incoming Owner | | | |
| Engineering Manager | | | |

---

## Post-Handoff

After sign-off:

1. Transfer CODEOWNERS to new owner
2. Update GitHub repo settings (branch protection reviewers)
3. Transfer any cloud account ownership
4. Schedule 30-day check-in for questions
5. Archive this checklist as completed

---

*Last updated: January 2026*

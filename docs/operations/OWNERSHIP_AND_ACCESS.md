# Ownership and Access

> **Purpose**: Document who owns what and how to get access.  
> **Status**: Template - fill in before handoff

---

## Owners

| Role | Name | Contact | Backup |
|------|------|---------|--------|
| **Tech Owner** | Julian Bartosz | @julianbartosz | TBD |
| **On-Call Primary** | TBD | | |
| **On-Call Secondary** | TBD | | |
| **Product Owner** | TBD | | |

### Escalation Path

1. **L1**: On-call engineer (Slack: #integration-coworker-oncall)
2. **L2**: Tech owner
3. **L3**: Engineering manager

### Business Hours vs After-Hours

| Time | Response SLA | Escalation |
|------|--------------|------------|
| Business hours (9am-6pm PT) | 30 min | L1 → L2 if no response in 1h |
| After-hours | 2 hours | Page L1, auto-escalate to L2 after 30min |

---

## Systems Access

### GitHub

| Resource | Access Level | How to Request |
|----------|--------------|----------------|
| `solver-agentic-spec-coworker` repo | Write | Request from Tech Owner |
| GitHub Actions secrets | Admin | Request from Tech Owner |
| Branch protection bypass | Admin | Emergency only, requires approval |

### CI/CD

| System | URL | Access |
|--------|-----|--------|
| GitHub Actions | [Actions tab](https://github.com/julianbartosz/solver-agentic-spec-coworker/actions) | Repo access |
| CI secrets vault | GitHub Settings → Secrets | Admin only |

**Required CI Secrets**:
- `OPENAI_API_KEY` - For prod-smoke tests
- `ANTHROPIC_API_KEY` - For prod-smoke tests (optional)

### Container Registry

| Registry | URL | Access |
|----------|-----|--------|
| GitHub Packages | ghcr.io/julianbartosz/... | Repo access |
| Docker Hub | TBD | TBD |

### Cloud Accounts

| Provider | Account/Project | Access |
|----------|-----------------|--------|
| AWS | TBD | TBD |
| GCP | TBD | TBD |
| Azure | TBD | TBD |

### Databases

| Environment | Host | Access |
|-------------|------|--------|
| Local dev | localhost:15432 | Docker compose |
| Demo | TBD | TBD |
| Staging | TBD | TBD |
| Production | TBD | TBD |

### Observability

| System | URL | Access |
|--------|-----|--------|
| LangSmith | [smith.langchain.com](https://smith.langchain.com) | Team invite |
| Prometheus/Grafana | TBD | TBD |
| Error tracking | TBD | TBD |

---

## Credential Rotation

### API Keys

| Credential | Rotation Frequency | How to Rotate |
|------------|-------------------|---------------|
| `OPENAI_API_KEY` | 90 days | Regenerate in OpenAI dashboard, update secrets |
| `ANTHROPIC_API_KEY` | 90 days | Regenerate in Anthropic console, update secrets |
| `LANGSMITH_API_KEY` | 90 days | Regenerate in LangSmith, update secrets |

### Database Credentials

| Credential | Rotation Frequency | How to Rotate |
|------------|-------------------|---------------|
| Postgres password | 90 days | Update in cloud provider, update `DATABASE_URL` |
| Redis password | 90 days | Update in cloud provider, update `REDIS_URL` |

### Rotation Procedure

1. Generate new credential in provider dashboard
2. Update GitHub Actions secrets
3. Update any deployed environments
4. Verify health checks pass
5. Delete old credential after 24h grace period

⚠️ **Never share credentials via Slack/email** - use secrets manager or encrypted channel.

---

## Vendor Contacts

| Vendor | Purpose | Contact | Account |
|--------|---------|---------|---------|
| OpenAI | LLM API | support@openai.com | TBD |
| Anthropic | LLM API | support@anthropic.com | TBD |
| LangChain | LangSmith | support@langchain.dev | TBD |

---

## Access Request Process

### New Team Member

1. Tech Owner adds to GitHub repo with Write access
2. Tech Owner invites to LangSmith team
3. New member completes [First Week Checklist](FIRST_WEEK.md)
4. New member requests any additional access needed

### Offboarding

1. Remove from GitHub repo
2. Remove from LangSmith team
3. Remove from any cloud accounts
4. Rotate any shared credentials if necessary

---

## Environment Inventory

| Environment | Purpose | Database | LLM Mode |
|-------------|---------|----------|----------|
| Local | Development | SQLite | Mock or real |
| CI | Automated tests | SQLite/Testcontainers | Mock |
| Demo | Live demonstrations | SQLite | Real |
| Staging | Pre-prod testing | Postgres | Real |
| Production | Live service | Postgres | Real |

### Environment-Specific Notes

**Local**:
- Use `USE_SQLITE=true USE_MOCK_LLM=true` for fastest iteration
- Use `docker-compose up -d db` for Postgres testing

**CI**:
- SQLite for fast tests, Testcontainers for Postgres tests
- `USE_MOCK_LLM=true` by default
- Secrets only available in `prod-smoke` job

**Production**:
- Postgres with pgvector required
- Real LLM calls (Anthropic primary, OpenAI fallback)
- Redis for LLM response caching

---

## Audit Log

| Date | Change | Who |
|------|--------|-----|
| 2026-01-01 | Document created | @julianbartosz |
| | | |

---

*Last updated: January 2026*

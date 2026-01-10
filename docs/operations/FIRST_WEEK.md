# First Week Checklist

> For new maintainers joining the Integration Co-Worker project.

This checklist ensures you can run, debug, and contribute to the project within your first week.

---

## Day 1: Environment Setup

### Clone and Install
```bash
git clone https://github.com/julianbartosz/solver-agentic-spec-coworker.git
cd solver-agentic-spec-coworker

# Use the setup script (creates .venv311 with Python 3.11)
./scripts/setup_env.sh

# Activate
source .venv311/bin/activate

# Verify
./scripts/setup_env.sh --check
```

### Run the Demo
```bash
# Mock mode (no API keys needed)
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker demo

# Expected: Markdown report showing spec ingestion, silver model, code artifacts
```

- [ ] Environment setup completed
- [ ] Demo runs successfully
- [ ] Understand output: spec → silver model → gold model → code

---

## Day 2: Run the Test Suite

### Full Suite (SQLite, mocked)
```bash
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/ -v
```

### By Category
```bash
# Fast unit tests only
pytest tests/ -m "not (integration or e2e or slow)" -v

# Integration tests
pytest tests/ -m "integration" -v
```

### With Coverage
```bash
USE_SQLITE=true pytest tests/ --cov=src --cov-report=term-missing
```

- [ ] All tests pass locally
- [ ] Coverage report generated
- [ ] Understand test markers: `no_db`, `integration`, `e2e`, `postgres`

---

## Day 3: Architecture Deep Dive

### Read These Docs (in order)
1. [docs/index.md](../index.md) - Project overview
2. [docs/development/architecture.md](../development/architecture.md) - System design
3. [docs/development/code-tour.md](../development/code-tour.md) - 60-min walkthrough

### Key Concepts to Understand
- [ ] Bronze → Silver → Gold medallion model
- [ ] LangGraph workflow with 22 nodes
- [ ] Checkpoint and recovery mechanism
- [ ] RepoProfile and code generation

### Explore the Code
```bash
# Entry points
cat src/integration_coworker/cli.py | head -100
cat src/integration_coworker/api/entrypoint.py | head -100

# Workflow state
cat src/integration_coworker/graph/state.py | head -150

# A key node
cat src/integration_coworker/graph/nodes/build_silver_api_model.py | head -100
```

---

## Day 4: Technical Debt and Priorities

### Review Technical Debt
```bash
cat docs/decisions/TECHNICAL_DEBT_REGISTER.md
```

- [ ] Read TECHNICAL_DEBT_REGISTER.md
- [ ] Identify top 3 debt items relevant to your work
- [ ] Understand ADR structure and where decisions are documented

### Review ADRs
- [ADR-0001](../decisions/adr-0001-initial-architecture.md) - Initial architecture
- [ADR-0004](../decisions/adr-0004-hybrid-graphrag-scoring-strategy.md) - GraphRAG scoring
- [ADR-0009](../decisions/adr-0009-workflow-recovery-strategy.md) - Recovery strategy

---

## Day 5: Security and Secrets

### Verify Secrets Handling
```bash
# Check that secrets are redacted in logs
OPENAI_API_KEY=sk-test-1234 python -m integration_coworker.cli health --verbose 2>&1 | grep -i key

# Should show: sk-***...*** (redacted)
```

### Review Security Docs
- [ ] Read security section in architecture.md
- [ ] Understand `llm/sanitizer.py` - input sanitization
- [ ] Understand `codegen/security.py` - forbidden patterns
- [ ] Know where API keys come from (env vars only)

### Run Security Scan
```bash
bandit -c pyproject.toml -r src tests
```

---

## Day 6: Incident Simulation

### Simulate Failed LLM Call
```bash
# Set invalid API key
export OPENAI_API_KEY="invalid-key"
export ANTHROPIC_API_KEY="invalid-key"

# Run and observe error handling
python -m integration_coworker.cli demo 2>&1 | tail -50

# Check RUNBOOK for recovery steps
cat docs/operations/RUNBOOK.md | grep -A 20 "Circuit Breaker"
```

### Follow RUNBOOK
- [ ] Located RUNBOOK.md
- [ ] Understand health check endpoints
- [ ] Know emergency switches (USE_MOCK_LLM, USE_SQLITE)
- [ ] Can interpret circuit breaker metrics

---

## Day 7: Make a Contribution

### Find a Good First Issue
- Check GitHub Issues labeled `good-first-issue`
- Or pick a documentation improvement
- Or add a test for uncovered code path

### Development Workflow
```bash
# Create branch
git checkout -b feature/my-first-change

# Make changes
# ...

# Run tests
USE_SQLITE=true pytest tests/ -v

# Lint
ruff check src/ tests/
ruff format src/ tests/

# Commit
git add -A
git commit -m "feat: description of change"

# Push and create PR
git push origin feature/my-first-change
```

- [ ] Made at least one commit
- [ ] Tests pass
- [ ] Linting passes
- [ ] Created PR (or ready to)

---

## Resources

| Resource | Location |
|----------|----------|
| Architecture | [docs/development/architecture.md](../development/architecture.md) |
| Code Tour | [docs/development/code-tour.md](../development/code-tour.md) |
| Contributing | [docs/development/contributing.md](../development/contributing.md) |
| RUNBOOK | [docs/operations/RUNBOOK.md](RUNBOOK.md) |
| Tech Debt | [docs/decisions/TECHNICAL_DEBT_REGISTER.md](../decisions/TECHNICAL_DEBT_REGISTER.md) |

---

## Questions?

- Check existing docs first
- Review closed issues/PRs for context
- Ask in team channel with specific question + what you tried

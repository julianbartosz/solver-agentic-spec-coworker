# ADR-0005: Production-Grade Codegen Quality Gates

## Status
Accepted

## Date
2024-12-16

## Context

The integration coworker generates code via LLM that must be:
1. **Syntactically correct** (parseable)
2. **Semantically valid** (imports resolve, types check)
3. **Functionally correct** (tests pass)
4. **Deterministic** (same input → predictable quality)

### Current Gaps (from Gap Analysis)

| Gap | Evidence |
|-----|----------|
| Ruff is cosmetic only | `.github/workflows/ci.yml:38` uses `\|\| true` |
| No mypy anywhere | Not in CI, not in codegen validation |
| Semantic validation incomplete | Only checks imports, not types |
| Pattern learning default changed | Without ADR or production profile |
| No sandbox execution | `test_execution.py` incomplete (no venv, no deps) |

### Provider Compatibility Constraint

The codebase uses 3 LLM providers via archetypes:
- **OpenAI** (`langchain_openai.ChatOpenAI`)
- **Anthropic** (`langchain_anthropic.ChatAnthropic`) - Used for codegen!
- **Google** (`langchain_google_genai.ChatGoogleGenerativeAI`)

OpenAI's Structured Outputs (`response_format` with JSON schema) is **OpenAI-specific**
and would break archetype flexibility if required for codegen.

## Decision

**Alternative A: LLM-First + Strict Post-Validation Gates**

We chose provider-agnostic post-validation gates over schema-constrained outputs.

### Alternatives Considered

#### Alternative A: LLM-First + Strict Post-Validation Gates (CHOSEN)

Add hard validation gates after LLM output:
1. Syntax (AST/tree-sitter) - existing
2. Security (forbidden patterns) - existing
3. Semantic (imports resolve) - existing
4. **Ruff check (NEW - hard gate)**
5. **mypy check (NEW - hard gate)**
6. **pytest in sandbox (NEW - production profile)**

**Pros:**
- Works with ALL providers (OpenAI, Anthropic, Google)
- Preserves archetype model interchange
- Catches content errors (not just format)

**Cons:**
- Output format still varies
- Retries cost tokens

#### Alternative B: Schema-Constrained LLM Outputs (Structured Outputs)

Use OpenAI's `response_format` with JSON schema for guaranteed structure.

**Pros:**
- Guaranteed parseable structure
- No markdown fence stripping needed

**Cons:**
- **OpenAI only** - breaks Anthropic/Google support
- **Breaks archetype flexibility** - codegen locked to OpenAI
- Content still varies within schema

### Decision Rationale

1. **Provider Independence**: Alternative A works with ALL providers
2. **Archetype Compatibility**: Teams can swap models via YAML
3. **Content is the Real Problem**: Schema only catches format, not code quality
4. **Defense in Depth**: Multiple gates catch different error types

## Consequences

### Positive
- Codegen works with any LLM provider
- Archetypes remain flexible (model swap via YAML)
- Layered validation catches more error types
- Production profile enables strict mode explicitly

### Negative
- Must handle output format variability (markdown fences)
- Retry loop may be expensive for complex generation
- Gates add latency (~5-10s for ruff + mypy + pytest)

### Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Gate latency | Run in parallel where possible |
| Retry cost | Cache LLM responses (Plan 7 Redis cache) |
| False positives | Tune ruff/mypy strictness per profile |

## Implementation

### New Files
- `src/integration_coworker/config/profiles.py` - Profile configuration
- `src/integration_coworker/codegen/sandbox.py` - Sandbox execution

### Modified Files
- `src/integration_coworker/config/__init__.py` - Add profile support
- `src/integration_coworker/graph/nodes/generate_code_and_tests.py` - Add gates
- `.github/workflows/ci.yml` - Make ruff hard gate

### Configuration

```bash
# Development (default) - lenient, skeleton fallback
CODEGEN_PROFILE=development

# Production - strict gates, fail hard
CODEGEN_PROFILE=production
```

## References

- Gap Analysis: See implementation commit
- OpenAI Structured Outputs: https://platform.openai.com/docs/guides/structured-outputs
- Anthropic tool calling: https://docs.anthropic.com/en/docs/tool-use

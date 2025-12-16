````markdown
# Node Archetypes

This directory contains YAML archetype files that define the configuration for each LangGraph node.
These files follow an Anthropic-style pattern for agent configuration.

## Archetype Structure

Each archetype file defines:

1. **Model Configuration**: LLM provider, model name, temperature, max_tokens
2. **Prompting Strategy**: System prompt template, chain-of-thought settings
3. **Input/Output Schemas**: TOON schema definitions for structured I/O
4. **Retrieval Settings**: RAG configuration (top_k, graph_radius, token_budget)
5. **Parallelism**: Sampling and concurrency limits

## Supported Providers

- `openai`: OpenAI models (gpt-4o, gpt-4o-mini, o1, o1-mini, etc.)
- `anthropic`: Anthropic Claude models (claude-sonnet-4-5, claude-3-5-sonnet, etc.)
- `google`: Google Gemini models (gemini-2.5-flash, gemini-2.5-pro, gemini-2.0-flash) - ideal for large context tasks

## Model Assignment by Role

| Role | Provider | Model | Use Case |
|------|----------|-------|----------|
| Planning | OpenAI | gpt-4o | Task understanding, workflow design, KG alignment |
| Code Generation | Anthropic | claude-sonnet-4-5 | Client code, flow code, test generation |
| Extraction | OpenAI | gpt-4o-mini | Deterministic spec parsing, schema extraction |
| Large Context | Google | gemini-2.5-flash | Repo analysis (when needed) |

### Node-to-Model Mapping

**Planning Nodes (gpt-4o):**
- `understand_task` - Analyzes task description
- `plan_integration_flow` - Designs workflow graph
- `plan_run` - Initializes execution plan
- `align_task_with_kg` - Matches task to KG templates
- `attach_policies_and_patterns` - Attaches auth, retry, error policies

**Code Generation Nodes (Claude Sonnet 4.5):**
- `generate_code_and_tests` - Generates client, flow, and test code

**Extraction Nodes (gpt-4o-mini):**
- `build_silver_api_model` - Parses API specs into Silver model

**Pure Python Nodes (No LLM):**
- `analyze_repo_layout` - Filesystem analysis
- `apply_repo_integration_changes` - Applies file changes
- `validate_integration_design` - Static validation

## Changing Models

Models are fully configurable via YAML - no code changes needed. To swap a model:

1. Open the node's archetype file (e.g., `generate_code_and_tests.archetype.yaml`)
2. Change the `model` section:
   ```yaml
   model:
     provider: anthropic  # or openai, google
     name: claude-sonnet-4-5-20250929  # any model the provider supports
     temperature: 0.15
     max_tokens: 8192
   ```
3. Restart the application

## Provider Fallback

When a primary provider's API key is missing, the system automatically falls back:
1. OpenAI → Anthropic → Google → Mock
2. Anthropic → OpenAI → Google → Mock
3. Google → OpenAI → Anthropic → Mock

Set API keys via environment variables:
- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `GOOGLE_API_KEY`

## File Naming Convention

- `{node_name}.archetype.yaml`: Configuration for a specific node
- `_base.archetype.yaml`: Shared base configuration

## Example

```yaml
name: generate_code_and_tests
role: codegen

model:
  # Claude Sonnet 4.5 for code generation
  # Change provider/name here to swap models without code changes
  provider: anthropic
  name: claude-sonnet-4-5-20250929
  temperature: 0.15
  max_tokens: 8192

prompting:
  strategy: generation
  num_candidates: 2
  evaluator: static_checks

retrieval:
  top_k: 8
  graph_radius: 1
  token_budget: 8000
  sources:
    - spec_endpoints
    - spec_schemas
    - kg_templates
    - repo_profile

input_schema:
  task: IntegrationTask
  flow: IntegrationFlow
  endpoints: list[Endpoint]
  policies: list[Policy]

output_schema:
  artifacts: list[CodeArtifact]
```

````

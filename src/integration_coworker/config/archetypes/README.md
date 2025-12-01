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

- `openai`: OpenAI models (gpt-4, gpt-4o-mini, etc.)
- `anthropic`: Anthropic Claude models (claude-3-opus, claude-3-sonnet, etc.)

## File Naming Convention

- `{node_name}.archetype.yaml`: Configuration for a specific node
- `_base.archetype.yaml`: Shared base configuration

## Example

```yaml
name: understand_task
role: planning

model:
  provider: anthropic
  name: claude-3-sonnet-20240229
  temperature: 0.2
  max_tokens: 2048

prompting:
  strategy: chain_of_thought
  num_samples: 2
  max_parallel_samples: 2

retrieval:
  top_k: 8
  graph_radius: 1
  token_budget: 3000

input_schema:
  task_description: string
  spec_context: string

output_schema:
  task_slug: string
  input_entities: list[string]
  output_entities: list[string]
  target_operations: list[string]
```

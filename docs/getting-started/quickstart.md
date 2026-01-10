# Quickstart

Get up and running with Integration Co-Worker in 5 minutes.

## Prerequisites

Ensure you have:

- Python 3.11+ installed
- API keys for OpenAI and Anthropic (or use mock mode)

## Step 1: Install

```bash
pip install -e ".[postgres]"
```

## Step 2: Set Environment Variables

```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

Or for testing without API keys:

```bash
export USE_SQLITE=true
export USE_MOCK_LLM=true
```

## Step 3: Run the Demo

```bash
# Mock mode (no API keys needed)
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker.cli demo

# Real LLM mode
python -m integration_coworker.cli demo --persist
```

## Step 4: Run with Your Own Spec

```bash
python -m integration_coworker.cli run \
  --spec-ref path/to/openapi.yaml \
  --task "Create a checkout session" \
  --provider my_provider \
  --dry-run
```

## Expected Output

A successful run produces:

- **Client code**: `integrations/clients/<provider>.py`
- **Workflow code**: `integrations/flows/<task>.py`
- **Test code**: `tests/integrations/test_<task>.py`

## What's Next?

- [Configure your environment](configuration.md) for production use
- [Learn the CLI commands](../user-guide/cli-reference.md) in depth
- [Understand the architecture](../development/architecture.md) to customize behavior

---

[Next: Configuration →](configuration.md)

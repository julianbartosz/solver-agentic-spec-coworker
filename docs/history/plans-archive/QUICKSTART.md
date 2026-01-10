# Integration Co-Worker: Quick Start Guide

> **TL;DR:** You give it an API spec (like Stripe's OpenAPI file) + a task description ("Create a payment"), and it writes the Python code for you.

---

## What Does This Thing Do?

Imagine you need to integrate with Stripe's API. Normally you would:
1. Read Stripe's API documentation
2. Figure out which endpoints you need
3. Write a client class with HTTP calls
4. Write error handling, retries, authentication
5. Write tests

**This tool does steps 1-5 automatically.**

You provide:
- An **OpenAPI spec file** (the API's blueprint)
- A **task description** ("Create a checkout session")

It generates:
- **Client code** (`stripe_client.py`)
- **Workflow code** (`checkout_flow.py`)
- **Tests** (`test_checkout.py`)

---

## 5-Minute Setup

### Step 1: Clone and Install

```bash
git clone https://github.com/julianbartosz/solver-agentic-spec-coworker.git
cd solver-agentic-spec-coworker

# Run setup script (creates Python 3.11 environment)
./scripts/setup_env.sh

# Activate the environment
source .venv311/bin/activate
```

### Step 2: Get API Keys

You need API keys from these services:

| Service | What For | Get It At |
|---------|----------|-----------|
| **OpenAI** | Embeddings + reports | https://platform.openai.com/api-keys |
| **Anthropic** | Code generation | https://console.anthropic.com/ |

Set them in your terminal:
```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

### Step 3: Test It Works

```bash
# This runs a demo (no API keys needed for this)
USE_SQLITE=true USE_MOCK_LLM=true python -m integration_coworker.cli demo-v1
```

You should see output like:
```
✅ Integration completed!
   Endpoints: 2
   Generated Artifacts: 3
```

---

## Using It For Real

### Basic Usage

```bash
python -m integration_coworker.cli run \
  --spec-ref path/to/api-spec.yaml \
  --task "What you want to do with the API" \
  --dry-run
```

### Example: Integrate with Stripe

```bash
# Download Stripe's OpenAPI spec
curl -o stripe.json https://raw.githubusercontent.com/stripe/openapi/master/openapi/spec3.json

# Generate code for creating a checkout session
python -m integration_coworker.cli run \
  --spec-ref stripe.json \
  --task "Create a checkout session with line items and success/cancel URLs" \
  --provider stripe \
  --dry-run
```

**Output:**
```
📝 Generated Artifacts:
   [client] src/integrations/clients/stripe.py
   [flow  ] src/integrations/flows/stripe_checkout.py
   [test  ] tests/integrations/test_stripe_checkout.py
```

### Example: Integrate with Twilio

```bash
python -m integration_coworker.cli run \
  --spec-ref specs/twilio_messaging_v1.json \
  --task "Send an SMS message to a phone number" \
  --provider twilio \
  --dry-run
```

---

## Writing Code to Your Project

By default, `--dry-run` just shows what would be generated. To actually write files to your project:

```bash
python -m integration_coworker.cli run \
  --spec-ref stripe.json \
  --task "Create a checkout session" \
  --provider stripe \
  --repo-root /path/to/your/project
```

This creates files in your project:
```
your-project/
├── src/integrations/clients/stripe.py      # HTTP client
├── src/integrations/flows/stripe_checkout.py   # Business logic
└── tests/integrations/test_stripe_checkout.py  # Tests
```

---

## Visual Interface (Optional)

If you prefer a web UI instead of command line:

```bash
python -m integration_coworker.cli ui
```

Opens http://localhost:8501 where you can:
- Upload or paste API specs
- Describe your task
- See generated code with syntax highlighting
- Download files

---

## What API Specs Work?

| Format | Support |
|--------|---------|
| **OpenAPI 3.0/3.1** (YAML/JSON) | ✅ Full support |
| **Swagger 2.0** | ⚠️ Auto-converts |
| URLs to hosted specs | ✅ Works |
| Local files | ✅ Works |

**Where to find API specs:**
- Most APIs publish them: `https://api.example.com/openapi.json`
- GitHub search: "openapi.yaml" or "swagger.json"
- https://apis.guru/ - Directory of public API specs

---

## Common Commands

| I want to... | Command |
|--------------|---------|
| Test everything works | `python -m integration_coworker.cli health` |
| Run a demo | `python -m integration_coworker.cli demo` |
| Generate code (preview) | `python -m integration_coworker.cli run -s spec.yaml -t "task" --dry-run` |
| Generate code (write files) | `python -m integration_coworker.cli run -s spec.yaml -t "task" --repo-root ./myproject` |
| Use web interface | `python -m integration_coworker.cli ui` |
| See all options | `python -m integration_coworker.cli run --help` |

---

## Troubleshooting

### "No module named integration_coworker"
```bash
pip install -e .
```

### "API key not set"
```bash
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

### "Failed to parse spec"
Make sure your file is valid OpenAPI 3.x (not Swagger 1.x). Validate at https://editor.swagger.io/

### Want more detailed logs?
```bash
python -m integration_coworker.cli run ... --verbose
```

---

## What's Next?

- **Production setup**: See `docs/GETTING_STARTED.md` for database setup (Postgres)
- **Full testing guide**: See `docs/MANUAL_TESTING_GUIDE.md`
- **Architecture**: See `docs/ARCHITECTURE.md` for how it works under the hood

---

## One-Liner Summary

```bash
# Give it an API spec + task → Get working code
python -m integration_coworker.cli run -s stripe.json -t "Create payment" --dry-run
```

That's it. 🎉

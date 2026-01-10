# Implementation Plan: MkDocs Documentation Site

## 1. Overview

**Objective:** Create a professional, searchable documentation site using MkDocs with Material theme, auto-generated API reference, and GitHub Pages deployment.

**Priority:** Medium  
**Estimated Effort:** 3-4 hours initial setup + ongoing content  
**Risk Level:** Low (no code changes, additive only)

---

## 2. Documentation Structure

```
docs/
├── index.md                      # Home page
├── getting-started/
│   ├── installation.md           # Installation guide
│   ├── quickstart.md             # 5-minute quickstart
│   └── configuration.md          # Environment variables
├── user-guide/
│   ├── cli-reference.md          # CLI command reference
│   ├── web-ui.md                 # Streamlit UI guide
│   ├── specs.md                  # Supported spec formats
│   └── workflows.md              # Workflow concepts
├── api-reference/
│   ├── entrypoint.md             # Main API
│   ├── types.md                  # Data types
│   ├── workflow-state.md         # WorkflowState
│   └── nodes/                    # Per-node documentation
├── development/
│   ├── architecture.md           # System architecture
│   ├── contributing.md           # Contribution guide
│   ├── testing.md                # Test guide
│   └── changelog.md              # Version history
├── features/
│   ├── knowledge-graph.md        # KG documentation
│   ├── llm-cache.md              # LLM caching
│   ├── parallel-execution.md     # Parallel nodes
│   └── recovery.md               # Resume/recovery
└── plans/                        # Implementation plans
    ├── 07-llm-response-cache.md
    ├── 08-parallel-node-execution.md
    └── 09-mkdocs-documentation.md
```

---

## 3. MkDocs Configuration

### 3.1 `mkdocs.yml`

```yaml
# Project information
site_name: Integration Co-Worker
site_description: Agentic API Integration Designer & Code Generator
site_author: Julian Bartosz
site_url: https://julianbartosz.github.io/solver-agentic-spec-coworker/

# Repository
repo_name: solver-agentic-spec-coworker
repo_url: https://github.com/julianbartosz/solver-agentic-spec-coworker

# Copyright
copyright: Copyright &copy; 2024 Julian Bartosz

# Theme
theme:
  name: material
  language: en
  palette:
    # Light mode
    - media: "(prefers-color-scheme: light)"
      scheme: default
      primary: indigo
      accent: indigo
      toggle:
        icon: material/brightness-7
        name: Switch to dark mode
    # Dark mode
    - media: "(prefers-color-scheme: dark)"
      scheme: slate
      primary: indigo
      accent: indigo
      toggle:
        icon: material/brightness-4
        name: Switch to light mode
  features:
    - navigation.instant
    - navigation.tracking
    - navigation.tabs
    - navigation.sections
    - navigation.expand
    - navigation.top
    - search.suggest
    - search.highlight
    - content.code.copy
    - content.code.annotate
  icon:
    repo: fontawesome/brands/github

# Plugins
plugins:
  - search:
      separator: '[\s\-\_\.]+'
  - mkdocstrings:
      default_handler: python
      handlers:
        python:
          options:
            docstring_style: google
            show_source: true
            show_root_heading: true
            members_order: source
  - gen-files:
      scripts:
        - docs/gen_cli_reference.py
  - literate-nav:
      nav_file: SUMMARY.md

# Extensions
markdown_extensions:
  - admonition
  - codehilite:
      guess_lang: false
  - toc:
      permalink: true
  - pymdownx.highlight:
      anchor_linenums: true
  - pymdownx.superfences:
      custom_fences:
        - name: mermaid
          class: mermaid
          format: !!python/name:pymdownx.superfences.fence_code_format
  - pymdownx.tabbed:
      alternate_style: true
  - pymdownx.details
  - pymdownx.tasklist:
      custom_checkbox: true
  - attr_list
  - md_in_html
  - tables

# Navigation
nav:
  - Home: index.md
  - Getting Started:
    - Installation: getting-started/installation.md
    - Quickstart: getting-started/quickstart.md
    - Configuration: getting-started/configuration.md
  - User Guide:
    - CLI Reference: user-guide/cli-reference.md
    - Web UI: user-guide/web-ui.md
    - Supported Specs: user-guide/specs.md
    - Workflows: user-guide/workflows.md
  - API Reference:
    - Entrypoint: api-reference/entrypoint.md
    - Types: api-reference/types.md
    - WorkflowState: api-reference/workflow-state.md
  - Development:
    - Architecture: development/architecture.md
    - Contributing: development/contributing.md
    - Testing: development/testing.md
    - Changelog: development/changelog.md

# Extra
extra:
  social:
    - icon: fontawesome/brands/github
      link: https://github.com/julianbartosz
  analytics:
    provider: google
    property: G-XXXXXXXXXX  # Replace with actual tracking ID
  version:
    provider: mike

extra_css:
  - stylesheets/extra.css
```

---

## 4. Auto-Generated Content

### 4.1 CLI Reference Generator

Create `docs/gen_cli_reference.py`:

```python
"""Generate CLI reference documentation from --help output."""
import subprocess
import sys
from pathlib import Path

def generate_cli_docs():
    """Generate CLI reference markdown from typer --help."""
    output_path = Path("docs/user-guide/cli-reference.md")
    
    # Get list of commands
    commands = [
        "run", "demo", "resume", "status", "init-db", "health",
        "kg-dump", "kg-query", "feedback", "feedback-sync", "ui"
    ]
    
    content = ["# CLI Reference\n\n"]
    content.append("Auto-generated from `integration-coworker --help`.\n\n")
    
    # Main help
    result = subprocess.run(
        [sys.executable, "-m", "integration_coworker.cli", "--help"],
        capture_output=True, text=True
    )
    content.append("## Main Command\n\n```\n")
    content.append(result.stdout)
    content.append("```\n\n")
    
    # Each subcommand
    for cmd in commands:
        content.append(f"## `{cmd}`\n\n")
        result = subprocess.run(
            [sys.executable, "-m", "integration_coworker.cli", cmd, "--help"],
            capture_output=True, text=True
        )
        content.append("```\n")
        content.append(result.stdout or f"No help available for {cmd}")
        content.append("```\n\n")
    
    output_path.write_text("".join(content))
    print(f"Generated {output_path}")

if __name__ == "__main__":
    generate_cli_docs()
```

### 4.2 API Reference from Docstrings

Use `mkdocstrings` to auto-generate API docs:

```markdown
<!-- docs/api-reference/entrypoint.md -->
# Entrypoint API

::: integration_coworker.api.entrypoint
    options:
      show_root_heading: true
      show_source: true
      members:
        - design_and_generate_integration
```

```markdown
<!-- docs/api-reference/types.md -->
# Types

::: integration_coworker.api.types
    options:
      show_root_heading: true
      members:
        - IntegrationOptions
        - IntegrationResult
```

---

## 5. Key Documentation Pages

### 5.1 Home Page (`docs/index.md`)

```markdown
# Integration Co-Worker

**Agentic API Integration Designer & Code Generator**

Integration Co-Worker is a LangGraph-powered tool that transforms OpenAPI 
specifications into production-ready integration code.

## Features

- 🔍 **Multi-format Spec Support** - OpenAPI, AsyncAPI, HTML, PDF
- 🧠 **LLM-Powered Planning** - Understands your task, plans the workflow
- 💻 **Multi-Language Codegen** - Python, TypeScript, Go, Java
- 📊 **Knowledge Graph** - Learns and reuses patterns
- 🔄 **Resumable Runs** - Checkpoint-based recovery

## Quick Start

```bash
pip install integration-coworker

integration-coworker run \
  --spec-ref https://api.example.com/openapi.yaml \
  --task "Create a payment checkout flow"
```

[Get Started →](../getting-started/installation.md)
```

### 5.2 Installation (`docs/getting-started/installation.md`)

```markdown
# Installation

## Requirements

- Python 3.11+
- PostgreSQL 14+ with pgvector (recommended) or SQLite
- OpenAI API key

## Install from PyPI

```bash
pip install integration-coworker
```

## Install with extras

```bash
# Full installation with UI and all features
pip install "integration-coworker[full]"

# Development installation
pip install "integration-coworker[dev]"

# Just the UI
pip install "integration-coworker[ui]"
```

## Docker Setup

```bash
docker-compose up -d postgres
integration-coworker init-db
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | Required |
| `DATABASE_URL` | PostgreSQL connection | `postgresql://localhost/integration` |
| `USE_SQLITE` | Use SQLite instead | `false` |
| `USE_MOCK_LLM` | Mock LLM for testing | `false` |

[Next: Quickstart →](../getting-started/quickstart.md)
```

---

## 6. GitHub Actions Deployment

### 6.1 `.github/workflows/docs.yml`

```yaml
name: Deploy Documentation

on:
  push:
    branches: [main]
    paths:
      - 'docs/**'
      - 'mkdocs.yml'
      - 'src/integration_coworker/**'
  workflow_dispatch:

permissions:
  contents: write

jobs:
  deploy:
    runs-on: ubuntu-latest
    
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      
      - name: Install dependencies
        run: |
          pip install -e ".[dev]"
          pip install mkdocs-material mkdocstrings[python] mkdocs-gen-files mkdocs-literate-nav
      
      - name: Generate CLI docs
        run: python docs/gen_cli_reference.py
      
      - name: Deploy to GitHub Pages
        run: mkdocs gh-deploy --force
```

---

## 7. Dependencies

### New dev dependencies:

```toml
# pyproject.toml
[project.optional-dependencies]
docs = [
    "mkdocs>=1.5",
    "mkdocs-material>=9.4",
    "mkdocstrings[python]>=0.24",
    "mkdocs-gen-files>=0.5",
    "mkdocs-literate-nav>=0.6",
]
```

---

## 8. Local Development

```bash
# Install docs dependencies
pip install -e ".[docs]"

# Serve locally with hot reload
mkdocs serve

# Build static site
mkdocs build

# Deploy to GitHub Pages
mkdocs gh-deploy
```

---

## 9. Files to Create

| File | Purpose | LOC |
|------|---------|-----|
| `mkdocs.yml` | MkDocs configuration | ~120 |
| `docs/index.md` | Home page | ~50 |
| `docs/getting-started/installation.md` | Installation guide | ~100 |
| `docs/getting-started/quickstart.md` | 5-minute quickstart | ~80 |
| `docs/getting-started/configuration.md` | Config reference | ~120 |
| `docs/user-guide/cli-reference.md` | CLI docs (auto-gen) | Auto |
| `docs/user-guide/web-ui.md` | UI guide | ~60 |
| `docs/user-guide/specs.md` | Spec formats | ~80 |
| `docs/user-guide/workflows.md` | Workflow concepts | ~100 |
| `docs/api-reference/entrypoint.md` | API entry | ~30 |
| `docs/api-reference/types.md` | Types reference | ~30 |
| `docs/api-reference/workflow-state.md` | State docs | ~50 |
| `docs/development/architecture.md` | Architecture (from existing) | ~200 |
| `docs/development/contributing.md` | Contributing guide | ~100 |
| `docs/development/testing.md` | Test guide | ~80 |
| `docs/development/changelog.md` | Changelog (from existing) | Link |
| `docs/gen_cli_reference.py` | CLI doc generator | ~50 |
| `docs/stylesheets/extra.css` | Custom styles | ~30 |
| `.github/workflows/docs.yml` | Deploy action | ~40 |

**Total:** ~1300 LOC documentation + config

---

## 10. Content Migration

### Existing docs to migrate:

| Current Location | New Location |
|------------------|--------------|
| `docs/ARCHITECTURE.md` | `docs/development/architecture.md` |
| `docs/CODE_TOUR.md` | `docs/development/code-tour.md` |
| `docs/GETTING_STARTED.md` | `docs/getting-started/quickstart.md` |
| `CHANGELOG.md` | `docs/development/changelog.md` (symlink) |
| `README.md` | Merge into `docs/index.md` |

---

## 11. Search Configuration

MkDocs Material includes built-in search with:
- Full-text search
- Keyboard shortcuts (`/` to focus)
- Search suggestions
- Highlighting in results

No additional configuration needed.

---

## 12. Custom Styling

### `docs/stylesheets/extra.css`

```css
/* Custom code block styling */
.md-typeset code {
  background-color: var(--md-code-bg-color);
  border-radius: 3px;
}

/* Wider content area */
.md-grid {
  max-width: 1400px;
}

/* API reference styling */
.doc-heading {
  border-bottom: 1px solid var(--md-default-fg-color--lightest);
}

/* Admonition icons */
.md-typeset .admonition.tip {
  border-color: #4caf50;
}
```

---

## 13. Versioning (Optional)

For version-specific documentation, use `mike`:

```bash
# Install mike
pip install mike

# Deploy version
mike deploy 0.1.0 latest --push
mike set-default latest --push
```

---

## 14. Rollout Plan

### Phase 1: Basic Setup (Day 1)
1. Create `mkdocs.yml` configuration
2. Create directory structure
3. Add `docs.yml` GitHub Action
4. Initial deploy to GitHub Pages

### Phase 2: Core Content (Day 2-3)
1. Write installation guide
2. Write quickstart guide
3. Migrate existing docs (ARCHITECTURE, CODE_TOUR)
4. Add CLI reference generator

### Phase 3: API Reference (Day 4)
1. Configure mkdocstrings
2. Add API reference pages
3. Ensure all public APIs documented

### Phase 4: Polish (Day 5)
1. Add custom styling
2. Add diagrams (mermaid)
3. Review and edit all content
4. Add search optimization

---

## 15. Success Metrics

- [ ] Documentation site live at `julianbartosz.github.io/solver-agentic-spec-coworker/`
- [ ] All CLI commands documented with examples
- [ ] API reference auto-generated from docstrings
- [ ] Search functionality working
- [ ] Mobile-responsive layout
- [ ] GitHub Actions auto-deploying on push

---

## 16. Diagram Support

MkDocs Material supports Mermaid diagrams natively:

```markdown
```mermaid
graph LR
    A[Spec Input] --> B[Parse]
    B --> C[Silver Model]
    C --> D[Task Understanding]
    D --> E[Flow Planning]
    E --> F[Code Generation]
    F --> G[Output]
```
```

Renders as an interactive diagram in the documentation.

# Web UI

Integration Co-Worker includes a Streamlit-based web interface for visual exploration and interaction.

## Launching the UI

```bash
# Install UI dependencies
pip install -e ".[ui]"

# Launch the UI
integration-coworker ui
```

The UI will be available at `http://localhost:8501`.

## Features

### Run Builder

Create new integration runs through a visual interface:

1. **Spec Input**: Upload or paste OpenAPI specs
2. **Task Description**: Enter your integration task
3. **Provider Selection**: Choose or auto-detect the provider
4. **Options**: Configure dry-run, persistence, and other settings
5. **Execute**: Run the workflow and see results in real-time

### Results Viewer

View and explore completed runs:

- **Run History**: Browse past runs by date and status
- **Artifacts**: View generated code, workflows, and tests
- **Report**: Read the human-readable integration report
- **Timing**: Analyze per-node execution times

### Knowledge Graph Explorer

Visualize and query the knowledge graph:

- **Template Browser**: View available workflow templates
- **Pattern Search**: Find patterns by keyword or concept
- **Graph Visualization**: Interactive graph exploration

### Feedback Interface

Submit feedback on runs to improve the system:

- **Rating**: Rate run quality (1-5 stars)
- **Comments**: Add detailed feedback
- **Suggestions**: Propose improvements

## Configuration

The UI respects the same environment variables as the CLI:

```bash
export DATABASE_URL="postgresql://..."
export OPENAI_API_KEY="sk-..."
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Custom Port

To run on a different port:

```bash
integration-coworker ui --port 8080
```

## Embedding in Other Apps

The UI components can be imported and used in custom Streamlit applications:

```python
from integration_coworker.ui.components import RunBuilder, ResultsViewer

# Use in your own Streamlit app
run_builder = RunBuilder()
results_viewer = ResultsViewer()
```

---

[Back to CLI Reference](cli-reference.md) | [Supported Specs →](specs.md)

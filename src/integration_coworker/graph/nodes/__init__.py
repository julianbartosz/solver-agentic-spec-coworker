"""Workflow node implementations."""
"""Workflow node module exports.

This package intentionally provides a stable import surface for the runtime.

Why this exists:
- Without an __init__.py, `integration_coworker.graph.nodes` becomes a namespace
  package. Depending on environment/tooling, importing from that namespace can
  produce an empty module, which breaks runtime node imports.
- Tests and the LangGraph runtime rely on these node modules being importable.

Keep this file lightweight: it should only re-export the node modules.
"""

from . import plan_run
from . import ingest_spec
from . import detect_and_parse_spec
from . import build_silver_api_model
from . import embed_spec_chunks
from . import understand_task
from . import align_task_with_kg
from . import plan_integration_flow
from . import attach_policies_and_patterns
from . import attach_repo_context
from . import generate_code_and_tests
from . import analyze_repo_layout
from . import apply_repo_integration_changes
from . import validate_integration_design
from . import persist_results
from . import build_report
from . import handle_error
from . import persist_silver_checkpoint
from . import persist_gold_checkpoint
from . import persist_run_outcome
from . import persist_kg_learning

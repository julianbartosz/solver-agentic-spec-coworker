"""
detect_and_parse_spec node — parses all spec_documents into structured dicts.

Implements: Design Doc §3.3 Detect and Parse Spec
Touches: openapi_spec, openapi_specs (list for multi-spec)
"""
import yaml
import json
from integration_coworker.graph.state import WorkflowState


def _parse_spec_content(content: str, content_type: str) -> dict:
    """
    Parse spec content (YAML or JSON) into a dict.
    Raises on parse failure.
    """
    ct = content_type.lower()
    if "yaml" in ct or "yml" in ct:
        return yaml.safe_load(content)
    elif "json" in ct:
        return json.loads(content)
    else:
        # Try YAML first (covers JSON too), fall back to JSON
        try:
            return yaml.safe_load(content)
        except Exception:
            return json.loads(content)


def detect_and_parse_spec(state: WorkflowState) -> WorkflowState:
    """
    Parse all spec_documents into openapi_spec(s).

    Reads: spec_documents
    Writes: openapi_spec (primary), openapi_specs (list of all parsed specs with uri)
    """
    if not state.spec_documents:
        state.errors.append("No spec_documents to parse")
        state.completed_steps.append("detect_and_parse_spec")
        return state

    # List to hold all parsed specs with their URIs
    parsed_specs: list[dict] = []

    for spec_doc in state.spec_documents:
        content = spec_doc.content
        content_type = spec_doc.content_type or ""
        uri = spec_doc.uri

        try:
            parsed = _parse_spec_content(content, content_type)

            if not isinstance(parsed, dict):
                state.errors.append(f"Parsed spec from {uri} is not a dictionary")
                continue

            if "openapi" not in parsed and "swagger" not in parsed:
                state.errors.append(f"Parsed spec from {uri} does not appear to be OpenAPI/Swagger format")
                continue

            # Tag with source URI for downstream tracking
            parsed["_source_uri"] = uri
            parsed_specs.append(parsed)

        except Exception as e:
            state.errors.append(f"Failed to parse spec from {uri}: {str(e)}")

    # Primary spec = first successfully parsed
    if parsed_specs:
        state.openapi_spec = parsed_specs[0]

    # Store all parsed specs in plan for multi-spec processing
    if state.plan is not None and parsed_specs:
        state.plan["openapi_specs"] = parsed_specs

    state.completed_steps.append("detect_and_parse_spec")
    return state

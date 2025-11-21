import yaml
import json
from integration_coworker.graph.state import WorkflowState

def detect_and_parse_spec(state: WorkflowState) -> WorkflowState:
    """
    Reads: spec_documents
    Writes: openapi_spec
    """
    if not state.spec_documents:
        state.errors.append("No spec_documents to parse")
        state.completed_steps.append("detect_and_parse_spec")
        return state
    
    # Parse the first document
    spec_doc = state.spec_documents[0]
    content = spec_doc.content
    content_type = spec_doc.content_type or ""
    
    try:
        # Try parsing as YAML first (covers JSON too)
        if "yaml" in content_type.lower() or "yml" in content_type.lower():
            parsed = yaml.safe_load(content)
        elif "json" in content_type.lower():
            parsed = json.loads(content)
        else:
            # Try YAML as default
            try:
                parsed = yaml.safe_load(content)
            except:
                parsed = json.loads(content)
        
        # Check if it's OpenAPI/Swagger
        if isinstance(parsed, dict):
            if "openapi" in parsed or "swagger" in parsed:
                state.openapi_spec = parsed
            else:
                state.errors.append("Parsed spec does not appear to be OpenAPI/Swagger format")
        else:
            state.errors.append("Parsed spec is not a dictionary")
            
    except Exception as e:
        state.errors.append(f"Failed to parse spec: {str(e)}")
    
    state.completed_steps.append("detect_and_parse_spec")
    return state

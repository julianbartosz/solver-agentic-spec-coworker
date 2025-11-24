import re
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationTask
from integration_coworker.config import get_llm_config


def understand_task(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, provider_code, endpoints, entities
    Writes: integration_task
    
    Contract per Appendix C.3.6:
    - Produces IntegrationTask with normalized task_slug
    - Derives input_entities / output_entities from known Entity names
    - Populates constraints dict with idempotency_required, max_latency_ms, requires_webhooks, extra
    """
    if not state.task_description:
        state.errors.append("No task_description provided")
        state.completed_steps.append("understand_task")
        return state
    
    config = get_llm_config("extraction")
    
    # For M3: use keyword matching for task understanding
    task_lower = state.task_description.lower()
    
    # 1. Derive task_slug: normalize to [a-z0-9_]+
    # Extract key action words
    action_words = []
    if "create" in task_lower:
        action_words.append("create")
    elif "update" in task_lower:
        action_words.append("update")
    elif "delete" in task_lower:
        action_words.append("delete")
    elif "get" in task_lower or "fetch" in task_lower or "retrieve" in task_lower:
        action_words.append("get")
    
    # Extract entity/resource words
    resource_words = []
    if "checkout" in task_lower:
        resource_words.append("checkout")
    if "session" in task_lower:
        resource_words.append("session")
    if "payment" in task_lower:
        resource_words.append("payment")
    if "customer" in task_lower:
        resource_words.append("customer")
    
    # Combine to form task_slug
    slug_parts = action_words + resource_words
    if not slug_parts:
        # Fallback: use first 3 words from task description
        slug_parts = task_lower.split()[:3]
    
    task_slug = "_".join(slug_parts)
    # Normalize to [a-z0-9_]+
    task_slug = re.sub(r'[^a-z0-9_]+', '_', task_slug.lower()).strip('_')
    
    # 2. Derive input_entities / output_entities
    # Match against known entity names (case-insensitive)
    known_entity_names = {e.name.lower(): e.name for e in state.entities}
    
    input_entities = []
    output_entities = []
    
    # Heuristic: words in task_description that match entity names
    for word in task_lower.split():
        clean_word = re.sub(r'[^a-z]+', '', word)
        if clean_word in known_entity_names:
            canonical_name = known_entity_names[clean_word]
            if "create" in task_lower or "new" in task_lower:
                output_entities.append(canonical_name)
            else:
                input_entities.append(canonical_name)
    
    # Remove duplicates
    input_entities = list(dict.fromkeys(input_entities))
    output_entities = list(dict.fromkeys(output_entities))
    
    # 3. Populate constraints
    constraints = {
        "idempotency_required": False,
        "max_latency_ms": None,
        "requires_webhooks": False,
        "extra": {},
    }
    
    # Heuristic detection
    if "create" in task_lower or "post" in task_lower:
        constraints["idempotency_required"] = True
    
    if "webhook" in task_lower or "callback" in task_lower:
        constraints["requires_webhooks"] = True
    
    # Store target operations for downstream use
    target_operations = []
    for endpoint in state.endpoints:
        operation_id = endpoint.operation_id or ""
        path = endpoint.path or ""
        
        # Keyword matching
        if any(word in operation_id.lower() or word in path.lower() 
               for word in action_words + resource_words):
            target_operations.append({
                "operation_id": operation_id,
                "method": endpoint.method,
                "path": endpoint.path,
                "summary": endpoint.summary,
            })
    
    constraints["extra"]["target_operations"] = target_operations
    
    # 4. Create IntegrationTask
    state.integration_task = IntegrationTask(
        id=None,
        source_system_id=None,
        task_slug=task_slug,
        provider_code=state.provider_code or "unknown",
        description=state.task_description,
        target_spec_document_id=None,
        input_entities=input_entities,
        output_entities=output_entities,
        constraints=constraints,
    )
    
    state.completed_steps.append("understand_task")
    return state

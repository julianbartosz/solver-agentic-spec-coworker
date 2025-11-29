"""
understand_task node - Extract structured task understanding from natural language.

Uses LLM when available (USE_MOCK_LLM=false + API key), falls back to heuristics
for mock mode or when LLM response is invalid.

Uses TOON (Token-Oriented Object Notation) for token-efficient LLM prompts.
"""
import re
import logging
from typing import Dict, Any, List

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationTask
from integration_coworker.llm import call_llm
from integration_coworker.llm.toon import from_toon

logger = logging.getLogger(__name__)


def _build_understand_task_prompt(state: WorkflowState) -> str:
    """
    Build a TOON-formatted prompt for task understanding.
    
    Uses Token-Oriented Object Notation for ~30-40% token savings vs JSON.
    """
    # Summarize available endpoints
    endpoint_summaries = []
    for ep in state.endpoints[:10]:  # Limit to first 10
        endpoint_summaries.append(f"- {ep.method} {ep.path}: {ep.summary or ep.operation_id or 'No summary'}")
    
    # Summarize available entities
    entity_names = [e.name for e in state.entities[:10]]
    
    prompt = f"""Analyze this integration task and extract structured information.

TASK DESCRIPTION:
{state.task_description}

AVAILABLE API ENDPOINTS:
{chr(10).join(endpoint_summaries) if endpoint_summaries else "(none found)"}

KNOWN ENTITIES:
{', '.join(entity_names) if entity_names else "(none found)"}

PROVIDER:
{state.provider_code or "unknown"}

Respond in TOON format (key=value notation, one per line):

task_slug=lowercase_snake_case_name
input_entities=[entity1,entity2]
output_entities=[entity1,entity2]
constraints.idempotency_required=true or false
constraints.requires_webhooks=true or false
target_operations=[{{operation_id:str,method:str,path:str,reason:str}}]

Rules:
- task_slug should be descriptive (e.g. create_checkout_session, get_customer)
- input_entities are entities needed as input
- output_entities are entities produced as output
- Set idempotency_required=true for create/write operations
- target_operations lists API endpoints needed for the task
- Use exact TOON format, no JSON, no markdown
"""
    return prompt


def _parse_llm_response(response: Dict[str, Any], state: WorkflowState) -> IntegrationTask:
    """
    Parse LLM response (from TOON) into IntegrationTask.
    
    Handles both TOON-parsed dicts and legacy JSON dicts for compatibility.
    """
    task_slug = response.get("task_slug", "unknown_task")
    # Normalize task_slug
    if isinstance(task_slug, str):
        task_slug = re.sub(r'[^a-z0-9_]+', '_', task_slug.lower()).strip('_')
    
    input_entities = response.get("input_entities", [])
    output_entities = response.get("output_entities", [])
    
    # Handle constraints - may be nested dict from TOON parsing
    constraints_raw = response.get("constraints", {})
    if not isinstance(constraints_raw, dict):
        constraints_raw = {}
    
    target_operations = response.get("target_operations", [])
    
    constraints = {
        "idempotency_required": constraints_raw.get("idempotency_required", False),
        "max_latency_ms": constraints_raw.get("max_latency_ms"),
        "requires_webhooks": constraints_raw.get("requires_webhooks", False),
        "extra": {"target_operations": target_operations},
    }
    
    # Ensure entities are lists
    if isinstance(input_entities, str):
        input_entities = [e.strip() for e in input_entities.split(",") if e.strip()]
    if isinstance(output_entities, str):
        output_entities = [e.strip() for e in output_entities.split(",") if e.strip()]
    
    return IntegrationTask(
        id=None,
        source_system_id=None,
        task_slug=task_slug,
        provider_code=state.provider_code or "unknown",
        description=state.task_description,
        target_spec_document_id=None,
        input_entities=input_entities if isinstance(input_entities, list) else [],
        output_entities=output_entities if isinstance(output_entities, list) else [],
        constraints=constraints,
    )


def _fallback_heuristic_understanding(state: WorkflowState) -> IntegrationTask:
    """
    Fallback heuristic-based task understanding.
    
    Used when LLM is not available or returns invalid response.
    """
    task_lower = state.task_description.lower()
    
    # Extract action words
    action_words = []
    if "create" in task_lower:
        action_words.append("create")
    elif "update" in task_lower:
        action_words.append("update")
    elif "delete" in task_lower:
        action_words.append("delete")
    elif "get" in task_lower or "fetch" in task_lower or "retrieve" in task_lower:
        action_words.append("get")
    
    # Extract resource words
    resource_words = []
    for word in ["checkout", "session", "payment", "customer", "subscription", "invoice"]:
        if word in task_lower:
            resource_words.append(word)
    
    # Build task_slug
    slug_parts = action_words + resource_words
    if not slug_parts:
        slug_parts = task_lower.split()[:3]
    
    task_slug = "_".join(slug_parts)
    task_slug = re.sub(r'[^a-z0-9_]+', '_', task_slug.lower()).strip('_')
    
    # Derive entities
    known_entity_names = {e.name.lower(): e.name for e in state.entities}
    input_entities = []
    output_entities = []
    
    for word in task_lower.split():
        clean_word = re.sub(r'[^a-z]+', '', word)
        if clean_word in known_entity_names:
            canonical_name = known_entity_names[clean_word]
            if "create" in task_lower or "new" in task_lower:
                output_entities.append(canonical_name)
            else:
                input_entities.append(canonical_name)
    
    input_entities = list(dict.fromkeys(input_entities))
    output_entities = list(dict.fromkeys(output_entities))
    
    # Constraints
    constraints = {
        "idempotency_required": "create" in task_lower or "post" in task_lower,
        "max_latency_ms": None,
        "requires_webhooks": "webhook" in task_lower or "callback" in task_lower,
        "extra": {},
    }
    
    # Target operations
    target_operations = []
    for endpoint in state.endpoints:
        operation_id = endpoint.operation_id or ""
        path = endpoint.path or ""
        if any(word in operation_id.lower() or word in path.lower() 
               for word in action_words + resource_words):
            target_operations.append({
                "operation_id": operation_id,
                "method": endpoint.method,
                "path": endpoint.path,
                "summary": endpoint.summary,
            })
    
    constraints["extra"]["target_operations"] = target_operations
    
    return IntegrationTask(
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


def understand_task(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, provider_code, endpoints, entities
    Writes: integration_task
    
    Contract per Appendix C.3.6:
    - Produces IntegrationTask with normalized task_slug
    - Derives input_entities / output_entities from known Entity names
    - Populates constraints dict with idempotency_required, max_latency_ms, requires_webhooks, extra
    
    Uses LLM with TOON format when available, falls back to heuristics otherwise.
    """
    if not state.task_description:
        state.errors.append("No task_description provided")
        state.completed_steps.append("understand_task")
        return state
    
    try:
        # Try LLM-based understanding with TOON format
        prompt = _build_understand_task_prompt(state)
        llm_response_text = call_llm(prompt, task_type="understand_task")
        
        # Parse TOON response
        if llm_response_text and not llm_response_text.startswith("Mock response"):
            try:
                llm_response = from_toon(llm_response_text)
                
                if llm_response.get("task_slug"):
                    logger.info(f"Using LLM TOON response for task understanding: {llm_response.get('task_slug')}")
                    state.integration_task = _parse_llm_response(llm_response, state)
                else:
                    logger.info("TOON response missing task_slug, using heuristics")
                    state.integration_task = _fallback_heuristic_understanding(state)
            except Exception as parse_error:
                logger.warning(f"Failed to parse TOON response: {parse_error}")
                state.integration_task = _fallback_heuristic_understanding(state)
        else:
            # LLM returned mock or empty response, use heuristics
            logger.info("Using heuristic fallback for task understanding")
            state.integration_task = _fallback_heuristic_understanding(state)
            
    except Exception as e:
        logger.warning(f"LLM call failed, using heuristics: {e}")
        state.integration_task = _fallback_heuristic_understanding(state)
    
    state.completed_steps.append("understand_task")
    return state

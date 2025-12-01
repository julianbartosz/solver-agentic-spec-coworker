"""
understand_task node - Extract structured task understanding from natural language.

Uses LLM when available (USE_MOCK_LLM=false + API key), falls back to heuristics
for mock mode or when LLM response is invalid.

V1 Enhancement: Uses semantic search over spec chunks to provide relevant context
to the task understanding prompt. This is **pure semantic retrieval** - no KG
BFS/DFS here; its job is to supply rich spec-grounded context for downstream reasoning.
"""
import re
import logging
from typing import Dict, Any

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationTask
from integration_coworker.llm import call_llm_json

logger = logging.getLogger(__name__)


def _get_relevant_spec_context(task_description: str, state: WorkflowState) -> str:
    """
    Retrieve relevant spec chunks for task understanding context.
    
    This is **purely semantic**: uses embedding similarity to find spec chunks
    that match the task description. No KG traversal here.
    
    V1 Enhancement per V1_GAP_CLOSURE_PLAN.md P0.
    """
    try:
        from integration_coworker.retrieval.semantic_search import search_spec_chunks

        # Get the first spec document ID if available
        spec_document_id = None
        if state.spec_documents:
            spec_document_id = state.spec_documents[0].id

        chunks = search_spec_chunks(
            task_description,
            top_k=3,
            spec_document_id=spec_document_id,
        )

        if not chunks:
            return ""

        context_lines = ["## Relevant API Documentation"]
        for chunk in chunks:
            # Truncate each chunk to avoid prompt bloat
            content = chunk.content[:500] if chunk.content else ""
            if content:
                context_lines.append(f"---\n{content}\n---")

        return "\n".join(context_lines)
    except Exception as e:
        logger.debug(f"Failed to get spec context: {e}")
        return ""


def _build_understand_task_prompt(state: WorkflowState) -> str:
    """Build a prompt for task understanding."""
    # Get semantic spec context (V1 enhancement)
    spec_context = _get_relevant_spec_context(state.task_description, state)

    # Summarize available endpoints
    endpoint_summaries = []
    for ep in state.endpoints[:10]:  # Limit to first 10
        endpoint_summaries.append(f"- {ep.method} {ep.path}: {ep.summary or ep.operation_id or 'No summary'}")

    # Summarize available entities
    entity_names = [e.name for e in state.entities[:10]]

    prompt = f"""Analyze this integration task and extract structured information.

TASK DESCRIPTION:
{state.task_description}

{spec_context}

AVAILABLE API ENDPOINTS:
{chr(10).join(endpoint_summaries) if endpoint_summaries else "(none found)"}

KNOWN ENTITIES:
{', '.join(entity_names) if entity_names else "(none found)"}

PROVIDER:
{state.provider_code or "unknown"}

Return a JSON object with:
{{
    "task_slug": "lowercase_snake_case_name",
    "input_entities": ["list", "of", "input", "entity", "names"],
    "output_entities": ["list", "of", "output", "entity", "names"],
    "constraints": {{
        "idempotency_required": true/false,
        "requires_webhooks": true/false
    }},
    "target_operations": [
        {{"operation_id": "...", "method": "...", "path": "...", "reason": "why this endpoint"}}
    ]
}}

Rules:
- task_slug should be descriptive (e.g., "create_checkout_session", "get_customer")
- input_entities are entities needed as input
- output_entities are entities produced as output
- Set idempotency_required=true for create/write operations
- target_operations should list the API endpoints needed to fulfill the task
"""
    return prompt


def _parse_llm_response(response: Dict[str, Any], state: WorkflowState) -> IntegrationTask:
    """Parse LLM response into IntegrationTask."""
    task_slug = response.get("task_slug", "unknown_task")
    # Normalize task_slug
    task_slug = re.sub(r'[^a-z0-9_]+', '_', task_slug.lower()).strip('_')

    input_entities = response.get("input_entities", [])
    output_entities = response.get("output_entities", [])

    constraints_raw = response.get("constraints", {})
    target_operations = response.get("target_operations", [])

    constraints = {
        "idempotency_required": constraints_raw.get("idempotency_required", False),
        "max_latency_ms": constraints_raw.get("max_latency_ms"),
        "requires_webhooks": constraints_raw.get("requires_webhooks", False),
        "extra": {"target_operations": target_operations},
    }

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
    
    Uses LLM when available, falls back to heuristics otherwise.
    """
    if not state.task_description:
        state.errors.append("No task_description provided")
        state.completed_steps.append("understand_task")
        return state

    try:
        # Try LLM-based understanding
        prompt = _build_understand_task_prompt(state)
        llm_response = call_llm_json(prompt, task_type="understand_task")

        # Check for valid response (not an error dict from mock or parse failure)
        if llm_response and not llm_response.get("error") and llm_response.get("task_slug"):
            logger.info(f"Using LLM response for task understanding: {llm_response.get('task_slug')}")
            state.integration_task = _parse_llm_response(llm_response, state)
        else:
            # LLM returned mock or invalid response, use heuristics
            logger.info("Using heuristic fallback for task understanding")
            state.integration_task = _fallback_heuristic_understanding(state)

    except Exception as e:
        logger.warning(f"LLM call failed, using heuristics: {e}")
        state.integration_task = _fallback_heuristic_understanding(state)

    state.completed_steps.append("understand_task")
    return state

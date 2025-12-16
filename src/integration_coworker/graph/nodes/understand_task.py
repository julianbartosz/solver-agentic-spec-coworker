"""
understand_task node - Extract structured task understanding from natural language.

Uses LLM when available (USE_MOCK_LLM=false + API key), falls back to heuristics
for mock mode or when LLM response is invalid.

V1 Enhancement: Uses semantic search over spec chunks to provide relevant context
to the task understanding prompt. This is **pure semantic retrieval** - no KG
BFS/DFS here; its job is to supply rich spec-grounded context for downstream reasoning.

V2 Enhancement (Section 3.13): Uses tenacity for exponential backoff retry.
Sets degraded_mode flag when falling back to heuristics.

V2.1 Enhancement (Section 13.4): Applies input sanitization to task descriptions
before LLM processing to prevent prompt injection attacks (SEC-002).

V2.2 Enhancement: Migrated from call_llm_json to TOON format for 30-40% token savings.
"""
import re
import logging
from typing import Dict, Any

from tenacity import retry, stop_after_attempt, wait_exponential, RetryError

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationTask
from integration_coworker.llm import call_llm_for_node, call_llm_async_for_node
from integration_coworker.llm.toon import from_toon, to_toon, toon_response_format
from integration_coworker.llm.sanitizer import (
    sanitize_task_description,
    detect_injection_attempt,
)

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
    """Build a prompt for task understanding using TOON format."""
    # Get semantic spec context (V1 enhancement)
    spec_context = _get_relevant_spec_context(state.task_description, state)

    # Summarize available endpoints
    endpoint_summaries = []
    for ep in state.endpoints[:10]:  # Limit to first 10
        endpoint_summaries.append(f"- {ep.method} {ep.path}: {ep.summary or ep.operation_id or 'No summary'}")

    # Summarize available entities
    entity_names = [e.name for e in state.entities[:10]]

    # Build TOON response format specification
    response_format = toon_response_format(
        fields={
            "task_slug": "lowercase_snake_case_name",
            "input_entities": "[entity1,entity2,...]",
            "output_entities": "[entity1,entity2,...]",
        },
        nested={
            "constraints": {
                "idempotency_required": "true|false",
                "requires_webhooks": "true|false",
            },
        },
    )

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

Return response in TOON format (key=value, one per line):
{response_format}
target_operations=[{{operation_id:string,method:string,path:string,reason:string}}]

Rules:
- task_slug should be descriptive (e.g., create_checkout_session, get_customer)
- input_entities are entities needed as input
- output_entities are entities produced as output
- Set idempotency_required=true for create/write operations
- target_operations should list the API endpoints needed to fulfill the task

Example response:
task_slug=create_checkout_session
input_entities=[Customer,Price]
output_entities=[Session]
constraints.idempotency_required=true
constraints.requires_webhooks=false
target_operations=[{{operation_id:CreateSession,method:POST,path:/v1/checkout/sessions,reason:creates new checkout}}]
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


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    reraise=True,
)
async def _call_llm_with_retry(prompt: str) -> Dict[str, Any]:
    """
    LLM call with exponential backoff (V2 Section 3.13).
    
    V2.2: Uses TOON format for 30-40% token savings.
    V3.0: Uses call_llm_for_node for archetype-based config.
    V3.1: Async implementation using call_llm_async_for_node.
    
    Retries up to 3 times with exponential backoff:
    - Attempt 1: immediate
    - Attempt 2: wait 1s
    - Attempt 3: wait 2s
    """
    # Call LLM using archetype-based async function
    response_text = await call_llm_async_for_node("understand_task", prompt)
    
    # Parse TOON response to dict
    if not response_text:
        raise ValueError("Empty LLM response")
    
    try:
        response = from_toon(response_text)
    except Exception as e:
        # If TOON parsing fails, try to extract what we can
        logger.warning(f"TOON parsing failed, attempting fallback: {e}")
        response = _parse_toon_fallback(response_text)
    
    # Validate response
    if response.get("error"):
        raise ValueError(f"LLM error response: {response.get('error')}")
    if not response.get("task_slug"):
        raise ValueError("LLM response missing task_slug")
    
    return response


def _parse_toon_fallback(text: str) -> Dict[str, Any]:
    """
    Fallback parser for malformed TOON responses.
    
    Attempts to extract key-value pairs even if format is slightly off.
    """
    result = {}
    
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        
        # Try to find key=value pattern
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            
            # Handle nested keys (dot notation)
            if "." in key:
                parts = key.split(".")
                current = result
                for part in parts[:-1]:
                    if part not in current:
                        current[part] = {}
                    current = current[part]
                current[parts[-1]] = _parse_simple_value(value)
            else:
                result[key] = _parse_simple_value(value)
    
    return result


def _parse_simple_value(value: str) -> Any:
    """Parse a simple value from TOON."""
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("[") and value.endswith("]"):
        # Simple array parsing
        inner = value[1:-1]
        if not inner:
            return []
        # Handle array of objects or simple values
        if "{" in inner:
            # Skip complex array parsing, return raw string for now
            return inner
        return [v.strip() for v in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _extract_action_verb(task_description: str) -> str:
    """Extract primary action verb from task description."""
    task_lower = task_description.lower()
    
    for verb in ["create", "update", "delete", "get", "fetch", "retrieve", "list", "search"]:
        if verb in task_lower:
            return verb
    
    # Try to extract first word as verb
    words = task_lower.split()
    if words:
        return words[0][:20]  # Limit length
    return "unknown"


def _extract_resource_noun(task_description: str) -> str:
    """Extract primary resource noun from task description."""
    task_lower = task_description.lower()
    
    # Common API resources
    for noun in ["user", "customer", "order", "payment", "session", "invoice", "subscription", "product"]:
        if noun in task_lower:
            return noun
    
    # Try to extract second word as noun
    words = task_lower.split()
    if len(words) >= 2:
        return words[1][:20]  # Limit length
    return "resource"


async def understand_task(state: WorkflowState) -> WorkflowState:
    """
    Reads: task_description, provider_code, endpoints, entities
    Writes: integration_task, task_source, degraded_mode, degraded_reason
    
    Contract per Appendix C.3.6:
    - Produces IntegrationTask with normalized task_slug
    - Derives input_entities / output_entities from known Entity names
    - Populates constraints dict with idempotency_required, max_latency_ms, requires_webhooks, extra
    
    V2 Enhancement (Section 3.13):
    - Uses tenacity for exponential backoff retry (3 attempts)
    - Sets degraded_mode flag when falling back to heuristics
    - Tracks task_source ("llm" or "heuristic") for observability
    
    V2.1 Enhancement (Section 13.4):
    - Applies input sanitization to detect and neutralize prompt injection (SEC-002)
    - Logs warning if suspicious patterns detected in task description
    
    V3.1: Async implementation for concurrent execution.
    """
    if not state.task_description:
        state.errors.append("No task_description provided")
        state.completed_steps.append("understand_task")
        return state

    # V2.1: Sanitize task description before LLM processing (SEC-002)
    raw_task = state.task_description
    
    # Detect potential injection attempt for logging/monitoring
    if detect_injection_attempt(raw_task):
        logger.warning(
            "Potential prompt injection detected in task description. "
            "Sanitizing before LLM processing."
        )
    
    # Sanitize the task description
    sanitized_task = sanitize_task_description(raw_task)
    
    # Temporarily use sanitized task for LLM processing
    # (keep original in state for downstream compatibility)
    original_task = state.task_description
    state.task_description = sanitized_task

    try:
        # V2: Try LLM-based understanding with retry
        prompt = _build_understand_task_prompt(state)
        llm_response = await _call_llm_with_retry(prompt)
        
        # Success - LLM returned valid response
        logger.info(f"Using LLM response for task understanding: {llm_response.get('task_slug')}")
        state.integration_task = _parse_llm_response(llm_response, state)
        
        # Track source for observability
        if hasattr(state.integration_task, "__dict__"):
            state.integration_task._task_source = "llm"
        
    except (RetryError, Exception) as e:
        # V2: Explicit degraded mode flag
        logger.error(f"LLM failed after retries: {e}")
        
        state.degraded_mode = True
        state.degraded_reason = f"Task understanding failed: {str(e)}"
        
        # Minimal heuristic extraction
        state.integration_task = IntegrationTask(
            id=None,
            source_system_id=None,
            task_slug=f"{_extract_action_verb(state.task_description)}_{_extract_resource_noun(state.task_description)}",
            provider_code=state.provider_code or "unknown",
            description=state.task_description,
            target_spec_document_id=None,
            input_entities=[],
            output_entities=[],
            constraints={
                "idempotency_required": False,
                "max_latency_ms": None,
                "requires_webhooks": False,
                "extra": {},
            },
        )
        
        # Track source for observability
        if hasattr(state.integration_task, "__dict__"):
            state.integration_task._task_source = "heuristic"
        
        # Also use full heuristic fallback for richer extraction
        logger.info("Using heuristic fallback for task understanding")
        state.integration_task = _fallback_heuristic_understanding(state)
        state.integration_task._task_source = "heuristic"
    
    finally:
        # V2.1: Restore original task description for downstream compatibility
        state.task_description = original_task

    state.completed_steps.append("understand_task")
    return state

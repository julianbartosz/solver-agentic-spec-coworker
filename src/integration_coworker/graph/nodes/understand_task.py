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

from tenacity import retry, stop_after_attempt, wait_exponential, RetryError, retry_if_not_exception_type

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import IntegrationTask
from integration_coworker.llm import call_llm_for_node, call_llm_async_for_node
from integration_coworker.llm.toon import from_toon, to_toon, toon_response_format
from integration_coworker.llm.sanitizer import (
    sanitize_task_description,
    detect_injection_attempt,
)
from integration_coworker.llm.exceptions import LLMAuthError
from integration_coworker.domain.ir import ProtocolType

logger = logging.getLogger(__name__)


# =============================================================================
# File-Based Task Detection (V3 FILE Enhancement)
# =============================================================================

# Indicators that a task involves file processing
FILE_TASK_INDICATORS = {
    # File actions
    "parse", "import", "ingest", "read", "load", "extract",
    # File types
    "csv", "excel", "xlsx", "xls", "tsv", "fixed", "fixedwidth", "fixed-width",
    "delimited", "spreadsheet", "flatfile", "flat-file", "flat file",
    # File concepts
    "file", "column", "row", "record", "field", "header", "data file",
}


def _is_file_based_task(task_description: str, state: WorkflowState) -> bool:
    """
    Detect if task involves file-based data integration.
    
    Returns True if:
    1. Task description contains file-related keywords, OR
    2. Workflow state has file_specs populated (file guide was ingested)
    
    V3 FILE Enhancement: Enable file-based codegen path.
    """
    task_lower = task_description.lower()
    
    # Check if file specs are available in state
    has_file_specs = bool(state.file_specs)
    
    # Check for file-related keywords in task
    has_file_keywords = any(indicator in task_lower for indicator in FILE_TASK_INDICATORS)
    
    # If we have file specs and the task seems file-related, it's a file task
    if has_file_specs and has_file_keywords:
        return True
    
    # Strong file indicators even without file specs (e.g., "parse csv file")
    strong_indicators = {"csv", "excel", "xlsx", "xls", "tsv", "fixed-width", "fixedwidth"}
    if any(ind in task_lower for ind in strong_indicators):
        return True
    
    return False


def _get_relevant_file_specs(task_description: str, state: WorkflowState) -> list:
    """
    Find file specs relevant to the task description.
    
    Scores each FileSpec by how well it matches the task keywords.
    Returns list of (score, FileSpec) tuples sorted by relevance.
    
    V3 FILE Enhancement.
    """
    if not state.file_specs:
        return []
    
    task_lower = task_description.lower()
    task_words = set(re.sub(r'[^a-z0-9\s]', '', task_lower).split())
    
    scored_specs = []
    for fs in state.file_specs:
        score = 0
        
        # Score based on name matching
        name_words = set(re.sub(r'[^a-z0-9\s]', '', fs.name.lower()).split())
        matching_name_words = task_words & name_words
        score += len(matching_name_words) * 5
        
        # Score based on file type matching
        file_type_lower = (fs.file_type or "").lower()
        if file_type_lower in task_lower:
            score += 10
        
        # Score based on description matching (if available)
        if fs.description:
            desc_words = set(re.sub(r'[^a-z0-9\s]', '', fs.description.lower()).split())
            matching_desc_words = task_words & desc_words
            score += len(matching_desc_words) * 2
        
        if score > 0 or len(state.file_specs) == 1:
            # Include single file spec even if no keyword match
            scored_specs.append((score, fs))
    
    # Sort by score descending
    scored_specs.sort(key=lambda x: -x[0])
    return scored_specs


def _build_file_spec_context(state: WorkflowState) -> str:
    """
    Build context string describing available file specs.
    
    V3 FILE Enhancement.
    """
    if not state.file_specs:
        return ""
    
    lines = ["## Available File Specifications"]
    
    for fs in state.file_specs:
        # Get fields for this file spec
        fields = [f for f in state.file_fields if f.file_spec_id == fs.id]
        field_names = [f.name for f in sorted(fields, key=lambda x: x.position)[:10]]
        
        lines.append(f"- **{fs.name}** ({fs.file_type})")
        lines.append(f"  - Encoding: {fs.encoding or 'utf-8'}")
        if fs.delimiter:
            lines.append(f"  - Delimiter: {repr(fs.delimiter)}")
        lines.append(f"  - Has Header: {fs.has_header}")
        if field_names:
            lines.append(f"  - Fields: {', '.join(field_names)}")
        if fs.description:
            lines.append(f"  - Description: {fs.description[:200]}")
    
    return "\n".join(lines)


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
    """Build a prompt for task understanding using TOON format.
    
    V3 FILE Enhancement: Supports both API and file-based tasks.
    """
    # V3 FILE: Check if this is a file-based task
    is_file_task = _is_file_based_task(state.task_description, state)
    
    # Get semantic spec context (V1 enhancement)
    spec_context = _get_relevant_spec_context(state.task_description, state)
    
    # V3 FILE: Add file spec context if available
    file_spec_context = _build_file_spec_context(state) if state.file_specs else ""

    # V38-006: Dynamic endpoint selection using task words and spec metadata
    # No hardcoded resource lists - let the spec's own content guide selection
    task_lower = state.task_description.lower()
    
    # Extract meaningful words from task (length > 2, alphanumeric)
    task_words = set()
    for word in re.sub(r'[^a-z0-9\s]', '', task_lower).split():
        if len(word) > 2:
            task_words.add(word)
    
    # Detect task intent from action verbs (generic HTTP-action mapping)
    # These are standard REST semantics, not spec-specific
    inferred_method = None
    create_verbs = {"create", "add", "new", "make", "generate", "send", "post", "submit"}
    read_verbs = {"get", "list", "fetch", "retrieve", "read", "find", "search", "query", "show"}
    update_verbs = {"update", "modify", "edit", "change", "patch", "set"}
    delete_verbs = {"delete", "remove", "cancel", "revoke"}
    
    for verb in create_verbs:
        if verb in task_words:
            inferred_method = "POST"
            break
    if not inferred_method:
        for verb in read_verbs:
            if verb in task_words:
                inferred_method = "GET"
                break
    if not inferred_method:
        for verb in update_verbs:
            if verb in task_words:
                inferred_method = "PUT"  # Could be PATCH too
                break
    if not inferred_method:
        for verb in delete_verbs:
            if verb in task_words:
                inferred_method = "DELETE"
                break
    
    # V38-007: Semantic intent detection for endpoint preference
    # Detect what KIND of task this is to prefer appropriate endpoint categories
    # This maps task semantics to API capabilities without hardcoding specific endpoints
    
    # V38-007 + V40-001: Improved intent detection with resource awareness
    # Priority: If task explicitly mentions management resources (assistant, thread, run, file),
    # treat as management task REGARDLESS of action verbs like "create" or "generate"
    
    # Management resource indicators - these take priority in classification
    # If the task mentions these explicitly, it's a management task
    management_resource_indicators = {
        "assistant", "assistants", "thread", "threads", "run", "runs",
        "file", "files", "vector", "vectors", "store", "stores",
        "batch", "batches", "fine", "tune", "finetune", "finetuning",
    }
    
    # Check if task explicitly mentions management resources
    explicit_management_resource = bool(task_words & management_resource_indicators)
    
    # Text generation indicators - actions that produce generated text/content
    # These ONLY indicate text generation if NOT combined with management resources
    text_gen_action_indicators = {
        "generate", "write", "enhance", "improve", "summarize",
        "translate", "explain", "describe", "document", "docstring",
        "completion", "complete", "respond",
    }
    
    # Text generation context indicators - what kind of output is expected
    text_gen_context_indicators = {
        "text", "content", "response", "answer", "message", "chat",
        "prompt", "conversation", "query",
    }
    
    # Determine task type with priority:
    # 1. Explicit management resource → management task (even with "create")
    # 2. Text generation action + context → text generation task  
    # 3. Management action → management task
    # 4. Text generation action alone → text generation task
    # 5. Default: text generation (most common use case for LLM APIs)
    
    has_text_gen_action = bool(task_words & text_gen_action_indicators)
    has_text_gen_context = bool(task_words & text_gen_context_indicators)
    
    # V40-001: Priority-based classification
    if explicit_management_resource:
        # Task mentions specific management resources - definitely management
        is_text_generation_task = False
        is_management_task = True
    elif has_text_gen_action and has_text_gen_context:
        # Strong text generation signal: action + context
        is_text_generation_task = True
        is_management_task = False
    elif has_text_gen_action:
        # Weak text generation signal: just action
        # Check for management indicators
        management_action_indicators = {
            "list", "get", "fetch", "retrieve", "delete", "update", "configure",
            "settings", "manage", "admin", "setup",
        }
        is_management_task = bool(task_words & management_action_indicators)
        is_text_generation_task = not is_management_task
    else:
        # No clear signal - check for management actions
        management_action_indicators = {
            "list", "get", "fetch", "retrieve", "delete", "update", "configure",
            "settings", "manage", "admin", "setup", "create",
        }
        is_management_task = bool(task_words & management_action_indicators)
        # Default to text generation for LLM-centric tasks
        is_text_generation_task = not is_management_task
    
    # V38-007: Define endpoint type indicators (used for semantic matching)
    # Chat/Completion endpoints are for text generation
    chat_completion_indicators = {"chat", "completion", "completions", "message", "messages"}
    # Management endpoints are for resource CRUD
    management_endpoint_indicators = {"assistant", "assistants", "thread", "threads", "run", "runs", "file", "files"}
    
    # V38-008: Modern vs Legacy API preference
    # When equivalent APIs exist (e.g. /chat/completions vs /completions),
    # prefer the modern version. This is based on industry patterns:
    # - Chat Completions API is the successor to Completions API for most providers
    # - Responses API or similar may be provider-specific modern alternatives
    # Pattern: APIs containing "chat" or "responses" are typically modern
    modern_api_indicators = {"chat", "responses"}
    # Pattern: Standalone "completions" without chat prefix is typically legacy
    # We detect this by checking the path pattern
    
    # Score endpoints dynamically based on spec metadata matching task words
    scored_endpoints = []
    for ep in state.endpoints:
        score = 0
        
        # CRITICAL: Define path_lower FIRST before any usage in this loop iteration
        # Bug fix: path_lower was being used in the is_text_generation_task block
        # before being defined in the V38-008 section, causing "cannot access free
        # variable 'path_lower'" NameError when endpoints match management patterns.
        path_lower = ep.path.lower()
        
        # Build searchable text from endpoint's own metadata
        ep_path_words = set(re.sub(r'[^a-z0-9\s]', ' ', path_lower).split())
        ep_summary_words = set(re.sub(r'[^a-z0-9\s]', ' ', (ep.summary or '').lower()).split())
        ep_op_id_words = set(re.sub(r'[^a-z0-9\s]', ' ', (ep.operation_id or '').lower()).split())
        
        # Score: how many task words appear in endpoint metadata?
        all_ep_words = ep_path_words | ep_summary_words | ep_op_id_words
        matching_words = task_words & all_ep_words
        score += len(matching_words) * 3  # Each matching word adds 3 points
        
        # Boost if HTTP method matches inferred intent
        if inferred_method:
            if ep.method == inferred_method:
                score += 5
            # Also accept PATCH for update intent
            if inferred_method == "PUT" and ep.method == "PATCH":
                score += 4
        
        # V38-007 + V40-001: Semantic intent boosting with stronger penalties
        # For text generation tasks, boost chat/completion endpoints over management endpoints
        if is_text_generation_task:
            if ep_path_words & chat_completion_indicators:
                score += 25  # Very strong boost for completion endpoints
            if ep_path_words & management_endpoint_indicators:
                score -= 25  # Very strong penalty for management endpoints
                # Additional penalty if path contains version-specific management patterns
                if any(x in path_lower for x in ['/assistants', '/threads', '/runs', '/files']):
                    score -= 15  # Extra penalty for specific management paths
        
        # For management tasks, boost management endpoints
        if is_management_task:
            if ep_path_words & management_endpoint_indicators:
                score += 20  # Strong boost for management endpoints
            if ep_path_words & chat_completion_indicators:
                score -= 10  # Penalize completion endpoints for management tasks
        
        # V38-008: Modern vs Legacy API preference
        # When there are competing completion-style endpoints, prefer modern APIs
        # Pattern: /chat/completions > /completions (Chat API is newer and preferred)
        # Pattern: /responses > /completions (Responses API is newer for some providers)
        # Note: path_lower already defined at start of loop iteration
        has_completion_indicator = "completion" in path_lower
        has_modern_indicator = bool(ep_path_words & modern_api_indicators)
        
        if has_completion_indicator:
            if has_modern_indicator:
                # Modern API: /chat/completions, /responses - strong preference
                score += 20
            else:
                # Legacy API: standalone /completions - penalize
                score -= 15
        
        # Slight boost for shorter, more specific paths (avoid overly nested endpoints)
        path_depth = ep.path.count('/')
        if path_depth <= 3:
            score += 1
            
        if score > 0:
            scored_endpoints.append((score, ep))
    
    # Sort by score descending, then by path length (prefer simpler paths)
    scored_endpoints.sort(key=lambda x: (-x[0], len(x[1].path)))
    relevant_endpoints = [ep for _, ep in scored_endpoints[:15]]
    
    # If no relevant endpoints found, fall back to first 10
    if not relevant_endpoints:
        relevant_endpoints = state.endpoints[:10]
    
    # Build endpoint summaries with operation_id for better matching
    endpoint_summaries = []
    for ep in relevant_endpoints:
        op_id = f" [{ep.operation_id}]" if ep.operation_id else ""
        summary = ep.summary or "No description"
        endpoint_summaries.append(f"- {ep.method} {ep.path}{op_id}: {summary}")

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

    # V3 FILE: Build file spec summaries for file-based tasks
    file_spec_summaries = []
    if state.file_specs:
        for fs in state.file_specs:
            fields = [f for f in state.file_fields if f.file_spec_id == fs.id]
            field_count = len(fields)
            file_spec_summaries.append(
                f"- {fs.name} [id:{fs.id}] ({fs.file_type}, {field_count} fields)"
            )
    
    # V3 FILE: Choose appropriate prompt based on task type
    if is_file_task and state.file_specs:
        # File-based task prompt
        prompt = f"""Analyze this file integration task and extract structured information.

TASK DESCRIPTION:
{state.task_description}

{spec_context}

{file_spec_context}

AVAILABLE FILE SPECIFICATIONS:
{chr(10).join(file_spec_summaries) if file_spec_summaries else "(none found)"}

KNOWN ENTITIES:
{', '.join(entity_names) if entity_names else "(none found)"}

PROVIDER:
{state.provider_code or "unknown"}

Return response in TOON format (key=value, one per line):
{response_format}
target_operations=[{{file_spec_id:int,file_spec_name:string,op:string,reason:string}}]
protocol_type=file

Rules:
- task_slug should be descriptive (e.g., parse_customer_data, import_sales_report)
- input_entities are entities needed as input (typically empty for file parsing)
- output_entities are entities produced (e.g., the record type from parsing)
- Set idempotency_required=true for parse operations (same file = same output)
- **CRITICAL**: target_operations MUST contain the file_spec_id from the list above
- op should be "parse" for most file operations
- protocol_type MUST be "file" for file-based tasks

Example response:
task_slug=parse_customer_data
input_entities=[]
output_entities=[CustomerData]
constraints.idempotency_required=true
constraints.requires_webhooks=false
target_operations=[{{file_spec_id:1,file_spec_name:Customer Data,op:parse,reason:parses customer CSV file}}]
protocol_type=file
"""
    else:
        # API-based task prompt (original)
        prompt = f"""Analyze this integration task and extract structured information.

TASK DESCRIPTION:
{state.task_description}

{spec_context}

AVAILABLE API ENDPOINTS (most relevant to task):
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
- **CRITICAL**: target_operations MUST contain the specific API endpoint(s) from the list above that implement this task
- Match operation_id exactly as shown in brackets [OperationId] above
- If unsure, pick the endpoint whose path and method best match the task action

Example response:
task_slug=create_checkout_session
input_entities=[Customer,Price]
output_entities=[Session]
constraints.idempotency_required=true
constraints.requires_webhooks=false
target_operations=[{{operation_id:CreateCheckoutSession,method:POST,path:/v1/checkout/sessions,reason:creates new checkout session for payment}}]
"""
    return prompt


def _parse_llm_response(response: Dict[str, Any], state: WorkflowState) -> IntegrationTask:
    """Parse LLM response into IntegrationTask.
    
    Bug #16 fix (extended): Defensive handling for malformed LLM responses where
    fields may be strings instead of expected dicts/lists.
    
    V3 FILE Enhancement: Includes protocol_type detection for file vs API tasks.
    """
    # Bug #16 fix: Ensure response is a dict
    if not isinstance(response, dict):
        logger.warning(f"LLM response is not a dict: {type(response).__name__}")
        response = {}
    
    task_slug = response.get("task_slug", "unknown_task")
    # Normalize task_slug
    if isinstance(task_slug, str):
        task_slug = re.sub(r'[^a-z0-9_]+', '_', task_slug.lower()).strip('_')
    else:
        task_slug = "unknown_task"

    input_entities = response.get("input_entities", [])
    output_entities = response.get("output_entities", [])

    constraints_raw = response.get("constraints", {})
    target_operations = response.get("target_operations", [])
    
    # V3 FILE: Detect protocol type from LLM response
    protocol_type_str = response.get("protocol_type", "rest")
    if protocol_type_str == "file":
        protocol_type = ProtocolType.FILE.value
    else:
        protocol_type = ProtocolType.REST.value
    
    # Debug logging: See what LLM returned for target_operations
    logger.info(f"[DEBUG] understand_task LLM response keys: {list(response.keys())}")
    logger.info(f"[DEBUG] understand_task target_operations raw: {target_operations}")
    logger.info(f"[DEBUG] understand_task target_operations type: {type(target_operations).__name__}")
    logger.info(f"[DEBUG] understand_task protocol_type: {protocol_type}")
    
    # Bug #16 fix: Handle case where constraints is a string instead of dict
    if not isinstance(constraints_raw, dict):
        logger.warning(f"constraints is not a dict: {type(constraints_raw).__name__}")
        constraints_raw = {}

    constraints = {
        "idempotency_required": constraints_raw.get("idempotency_required", False) if isinstance(constraints_raw, dict) else False,
        "max_latency_ms": constraints_raw.get("max_latency_ms") if isinstance(constraints_raw, dict) else None,
        "requires_webhooks": constraints_raw.get("requires_webhooks", False) if isinstance(constraints_raw, dict) else False,
        "extra": {
            "target_operations": target_operations if isinstance(target_operations, list) else [],
            "protocol_type": protocol_type,  # V3 FILE: Include protocol type
        },
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
    
    V3 FILE Enhancement: Supports file-based task detection.
    """
    task_lower = state.task_description.lower()
    
    # V3 FILE: Check if this is a file-based task
    is_file_task = _is_file_based_task(state.task_description, state)

    # Extract action words
    action_words = []
    if "parse" in task_lower or "import" in task_lower or "ingest" in task_lower:
        action_words.append("parse")  # V3 FILE: Add file action
    elif "create" in task_lower:
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
    
    # V3 FILE: Extract resource words from file spec names
    if state.file_specs:
        for fs in state.file_specs:
            name_words = re.sub(r'[^a-z0-9\s]', '', fs.name.lower()).split()
            for word in name_words:
                if word in task_lower and len(word) > 2:
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
        "idempotency_required": "create" in task_lower or "post" in task_lower or "parse" in task_lower,
        "max_latency_ms": None,
        "requires_webhooks": "webhook" in task_lower or "callback" in task_lower,
        "extra": {},
    }

    # V3 FILE: Build target operations based on task type
    target_operations = []
    
    if is_file_task and state.file_specs:
        # File-based target operations
        relevant_specs = _get_relevant_file_specs(state.task_description, state)
        for score, fs in relevant_specs[:3]:  # Top 3 matches
            target_operations.append({
                "file_spec_id": fs.id,
                "file_spec_name": fs.name,
                "op": "parse",
                "reason": f"parses {fs.file_type} file",
            })
        constraints["extra"]["protocol_type"] = ProtocolType.FILE.value
    else:
        # API-based target operations
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
        constraints["extra"]["protocol_type"] = ProtocolType.REST.value

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
    retry=retry_if_not_exception_type(LLMAuthError),  # Auth errors fail-fast (P0-2)
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
    
    # Debug logging: See raw LLM response before parsing
    logger.info(f"[DEBUG] understand_task raw LLM response (first 500 chars): {response_text[:500] if response_text else 'EMPTY'}")
    
    # Parse TOON response to dict
    if not response_text:
        raise ValueError("Empty LLM response")
    
    try:
        response = from_toon(response_text)
        # Debug: Log if target_operations was in raw text but not parsed
        if "target_operations" in response_text and not response.get("target_operations"):
            logger.warning(f"[DEBUG] target_operations in raw text but NOT in parsed response!")
            logger.info(f"[DEBUG] Raw text around target_operations: {response_text[response_text.find('target_operations'):response_text.find('target_operations')+200]}")
        del response_text  # V22: Release raw LLM response after parsing
    except Exception as e:
        # If TOON parsing fails, try to extract what we can
        logger.warning(f"TOON parsing failed, attempting fallback: {e}")
        response = _parse_toon_fallback(response_text)
        del response_text  # V22: Release raw LLM response after parsing
    
    # Bug #16 fix: Ensure response is a dict before calling .get()
    if not isinstance(response, dict):
        logger.warning(f"Response is not a dict after parsing: {type(response).__name__}")
        raise ValueError(f"LLM response is not a dict: {response[:100] if isinstance(response, str) else response}")
    
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
        
        # Bug #16 fix: Final safety check before using response
        task_slug_for_log = llm_response.get('task_slug', 'unknown') if isinstance(llm_response, dict) else 'unknown'
        logger.info(f"Using LLM response for task understanding: {task_slug_for_log}")
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

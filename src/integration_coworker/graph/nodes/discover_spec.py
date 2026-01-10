"""
discover_spec Node - Spec Auto-Discovery (Slice 1)

LangGraph node that resolves natural language task descriptions to
OpenAPI specification URLs using a multi-source discovery strategy.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md Section 1D:
- No-op if spec_refs is already populated
- Error if spec_refs empty and discovery disabled
- Resolution: intent → local filesystem → catalog → APIs.guru → validate → set spec_refs

Discovery Sources (in priority order):
1. Local Filesystem: Searches repo_root for OpenAPI/Swagger spec files
2. Local Catalog: Pre-curated specs database
3. APIs.guru Registry: Public OpenAPI spec registry
4. Web Search (optional): Tavily/SerpApi fallback

Contract:
    READS: task_description, options.discovery_enabled, repo_root
    WRITES: spec_refs, discovered_specs, discovery_confidence, discovery_source
    SIDE EFFECTS: HTTP to APIs.guru (cached), HTTP to validate spec URL
"""

import asyncio
import logging
from typing import Any, Dict

from integration_coworker.config import get_settings
from integration_coworker.discovery import resolve_spec_from_task
from integration_coworker.graph.state import WorkflowState

logger = logging.getLogger(__name__)


def _is_discovery_enabled(state: WorkflowState) -> bool:
    """Check if discovery is enabled via settings or options."""
    settings = get_settings()
    
    # Check options override first
    if state.options is not None:
        # Options can explicitly enable/disable discovery
        discovery_opt = getattr(state.options, "discovery_enabled", None)
        if discovery_opt is not None:
            return discovery_opt
    
    # Fall back to global setting
    return settings.discovery_enabled


async def discover_spec_async(state: WorkflowState) -> WorkflowState:
    """
    Async implementation of discover_spec node.
    
    Resolution strategy (Slice 1):
    1. Skip if spec_refs already populated
    2. Error if discovery disabled
    3. Analyze intent from task_description
    4. Search APIs.guru for matching specs
    5. Validate top candidate
    6. Set spec_refs with discovered URL
    
    Args:
        state: Current workflow state
        
    Returns:
        Updated state with spec_refs populated (or error)
    """
    settings = get_settings()
    
    # =========================================================================
    # Step 1: Check if discovery is needed
    # =========================================================================
    if state.spec_refs and len(state.spec_refs) > 0:
        # Spec already provided, mark as user-provided and skip
        logger.info(
            f"Spec already provided ({len(state.spec_refs)} refs), skipping discovery"
        )
        state.discovery_source = "user_provided"
        state.discovery_confidence = 1.0
        state.completed_steps.append("discover_spec")
        return state
    
    # =========================================================================
    # Step 2: Check if discovery is enabled
    # =========================================================================
    discovery_enabled = _is_discovery_enabled(state)
    
    if not discovery_enabled:
        error_msg = (
            "No spec_refs provided and discovery is disabled. "
            "Provide --spec-ref or enable discovery with DISCOVERY_ENABLED=true"
        )
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.completed_steps.append("discover_spec")
        raise ValueError(error_msg)
    
    # =========================================================================
    # Step 3: Validate we have a task description
    # =========================================================================
    if not state.task_description or not state.task_description.strip():
        error_msg = (
            "Cannot discover spec: task_description is empty. "
            "Provide --task with a description of what you want to accomplish."
        )
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.completed_steps.append("discover_spec")
        raise ValueError(error_msg)
    
    logger.info(f"Starting spec discovery for task: {state.task_description[:100]}...")
    
    # =========================================================================
    # Step 4: Run discovery resolver
    # =========================================================================
    try:
        result = await resolve_spec_from_task(
            task=state.task_description,
            max_candidates=settings.discovery_max_candidates,
            repo_root=state.repo_root,  # Search local filesystem first
        )
    except Exception as e:
        error_msg = f"Discovery failed: {str(e)[:200]}"
        logger.error(error_msg, exc_info=True)
        state.errors.append(error_msg)
        state.completed_steps.append("discover_spec")
        raise ValueError(error_msg) from e
    
    # =========================================================================
    # Step 5: Handle discovery result
    # =========================================================================
    if not result.success:
        error_msg = f"Could not discover spec: {result.error}"
        logger.error(error_msg)
        state.errors.append(error_msg)
        state.completed_steps.append("discover_spec")
        raise ValueError(error_msg)
    
    # =========================================================================
    # Step 6: Update state with discovered spec
    # =========================================================================
    state.spec_refs = [result.spec_url]
    state.discovery_source = result.source
    state.discovery_confidence = result.confidence
    state.discovered_specs = result.to_dict()
    
    logger.info(
        f"Discovery success: {result.api_name} from {result.provider} "
        f"(confidence={result.confidence:.2f}, source={result.source})"
    )
    logger.debug(f"Discovered spec URL: {result.spec_url}")
    
    state.completed_steps.append("discover_spec")
    return state


def discover_spec(state: WorkflowState) -> WorkflowState:
    """
    Synchronous wrapper for discover_spec_async.
    
    LangGraph nodes can be sync or async, but we use sync wrapper
    for consistency with other nodes in the graph.
    """
    # Run async function in event loop
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in async context (e.g., pytest-asyncio)
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(discover_spec_async(state))
        else:
            return loop.run_until_complete(discover_spec_async(state))
    except RuntimeError:
        # No event loop, create one
        return asyncio.run(discover_spec_async(state))

"""
Spec Resolver for Discovery (Slice 2/3/4)

Orchestrates intent analysis, local catalog, APIs.guru search, optional web search,
and validation to resolve a natural language task to a validated spec URL.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md:
- Slice 2: Local-first resolution with APIs.guru fallback
- Slice 3: LLM-enhanced intent + HITL candidate selection
- Slice 4: Optional web search (Tavily/SerpApi) for low-confidence cases
- Resolution cascade: intent → local catalog → APIs.guru → web_search → validate
- HITL triggers when confidence < threshold (deterministic ordering)

Resolution Cascade (Slice 4):
    1. Analyze intent (heuristic or LLM-enhanced)
    2. Search local catalog first (fast, offline-capable)
    3. Fall back to APIs.guru if local catalog misses
    4. Fall back to web search if enabled AND no confident candidate found
    5. Validate top candidates until one passes
    6. Return first valid candidate
    
Web Search (Slice 4):
    - Disabled by default (DISCOVERY_WEB_SEARCH_ENABLED=false)
    - Only runs when earlier sources have no high-confidence match
    - Uses Tavily (preferred) or SerpApi (behind stricter flags)
    - All URLs from web search are untrusted and must pass validation
"""

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

from integration_coworker.config import get_settings
from integration_coworker.discovery.intent import IntentAnalysis, analyze_discovery_intent
from integration_coworker.discovery.apis_guru import SpecCandidate, search_apis_guru
from integration_coworker.discovery.local_filesystem import search_local_filesystem
from integration_coworker.discovery.validator import ValidationResult, validate_spec_url
from integration_coworker.discovery.metrics import (
    record_discovery_attempt,
    record_discovery_success,
    record_discovery_failure,
    record_hitl_triggered,
    record_hitl_completed,
    record_validation_result,
    record_candidate_count,
    discovery_timer,
)

logger = logging.getLogger(__name__)

# Maximum candidates to try before giving up
MAX_CANDIDATES_TO_TRY = 5

# Enable/disable local catalog (for gradual rollout)
LOCAL_CATALOG_ENABLED = True

# Web search confidence threshold - only search if best candidate is below this
WEB_SEARCH_CONFIDENCE_THRESHOLD = 0.7


@dataclass
class HITLCandidateSelection:
    """
    HITL candidate selection request.
    
    Used when confidence is below threshold and user confirmation is needed.
    Candidates are ordered deterministically for reproducible behavior.
    """
    candidates: List[SpecCandidate]
    intent: IntentAnalysis
    confidence: float
    reason: str
    
    def get_display_options(self) -> List[Dict[str, Any]]:
        """Format candidates for UI display."""
        return [
            {
                "index": i,
                "provider": c.provider,
                "api_name": c.api_name,
                "spec_url": c.spec_url,
                "score": c.score,
            }
            for i, c in enumerate(self.candidates)
        ]


@dataclass
class DiscoveryResult:
    """
    Result of spec discovery from a task description.
    
    Attributes:
        success: Whether a valid spec was found
        spec_url: Validated spec URL (if success)
        provider: Provider domain (e.g., "stripe.com")
        api_name: Human-readable API name
        confidence: Overall confidence score (0.0-1.0)
        source: Discovery source ("local_catalog", "apis_guru", "user_provided", etc.)
        intent: Original intent analysis
        candidates: All candidates considered (bounded)
        validation: Validation result for selected spec
        error: Error message if discovery failed
        requires_hitl: True if HITL confirmation is needed
        hitl_request: HITL selection request (if requires_hitl)
    """
    
    success: bool
    spec_url: Optional[str] = None
    provider: Optional[str] = None
    api_name: Optional[str] = None
    confidence: float = 0.0
    source: str = "apis_guru"
    intent: Optional[IntentAnalysis] = None
    candidates: List[SpecCandidate] = field(default_factory=list)
    validation: Optional[ValidationResult] = None
    error: Optional[str] = None
    requires_hitl: bool = False
    hitl_request: Optional[HITLCandidateSelection] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for state serialization."""
        return {
            "success": self.success,
            "spec_url": self.spec_url,
            "provider": self.provider,
            "api_name": self.api_name,
            "confidence": self.confidence,
            "source": self.source,
            "candidates": [c.to_dict() for c in self.candidates[:MAX_CANDIDATES_TO_TRY]],
            "validation": self.validation.to_dict() if self.validation else None,
            "error": self.error,
            "requires_hitl": self.requires_hitl,
            "hitl_options": self.hitl_request.get_display_options() if self.hitl_request else None,
        }


def _sort_candidates_deterministic(candidates: List[SpecCandidate]) -> List[SpecCandidate]:
    """
    Sort candidates deterministically for reproducible HITL behavior.
    
    Same inputs always produce same ordering:
    1. Score (descending)
    2. Provider name (alphabetical)
    3. API name (alphabetical)
    """
    return sorted(
        candidates,
        key=lambda c: (-c.score, c.provider.lower(), c.api_name.lower()),
    )


async def _search_local_catalog(
    intent: IntentAnalysis,
    max_results: int,
) -> List[SpecCandidate]:
    """
    Search local catalog for candidates.
    
    Returns empty list if catalog unavailable or no matches.
    """
    if not LOCAL_CATALOG_ENABLED:
        return []
    
    try:
        from integration_coworker.discovery.catalog import (
            search_local_catalog,
            is_catalog_available,
        )
        
        if not is_catalog_available():
            logger.debug("Local catalog not available or empty")
            return []
        
        # Build query from intent
        query = " ".join(filter(None, [
            intent.best_provider_hint,
            " ".join(intent.keywords[:5]),
        ]))
        
        matches = await search_local_catalog(
            query=query,
            provider_hint=intent.best_provider_hint,
            keywords=intent.keywords,
            max_results=max_results,
        )
        
        return [m.to_spec_candidate() for m in matches]
        
    except ImportError:
        logger.debug("Catalog module not available")
        return []
    except Exception as e:
        logger.warning(f"Local catalog search failed: {e}")
        return []


async def _search_web_for_candidates(
    intent: IntentAnalysis,
    max_results: int,
) -> List[SpecCandidate]:
    """
    Search web for spec candidates using Tavily or SerpApi (Slice 4).
    
    This is a fallback when local catalog and APIs.guru don't return
    high-confidence candidates. Web search is disabled by default.
    
    Args:
        intent: Analyzed intent from task
        max_results: Maximum candidates to return
        
    Returns:
        List of SpecCandidate from web search hits.
        URLs are UNTRUSTED and must be validated before use.
    """
    try:
        from integration_coworker.discovery.web_search import (
            search_web_for_specs,
            get_web_search_config,
            score_spec_url_heuristic,
        )
        
        config = get_web_search_config()
        if not config.is_available:
            logger.debug("Web search not available (disabled or no API key)")
            return []
        
        # Search web
        hits = await search_web_for_specs(
            explicit_provider=intent.best_provider_hint,
            keywords=intent.keywords,
            config=config,
        )
        
        if not hits:
            logger.debug("Web search returned no hits")
            return []
        
        logger.info(f"Web search: found {len(hits)} potential spec URLs")
        
        # Convert hits to SpecCandidates with heuristic scoring
        candidates = []
        for hit in hits[:max_results * 2]:  # Get extra for filtering
            # Score the URL heuristically
            score = score_spec_url_heuristic(
                url=hit.url,
                title=hit.title,
                snippet=hit.snippet,
                provider_hint=intent.best_provider_hint,
            )
            
            # Skip low-scoring hits
            if score < 0.3:
                continue
            
            # Derive provider from URL
            from urllib.parse import urlparse
            try:
                parsed = urlparse(hit.url)
                provider = parsed.hostname or "unknown"
            except Exception:
                provider = "unknown"
            
            candidates.append(SpecCandidate(
                provider=provider,
                api_name=hit.title[:100] if hit.title else f"Spec from {provider}",
                spec_url=hit.url,
                spec_format="unknown",  # Will be determined by validation
                score=score,
            ))
        
        # Sort by score and return top results
        candidates.sort(key=lambda c: -c.score)
        return candidates[:max_results]
        
    except ImportError:
        logger.debug("Web search module not available")
        return []
    except Exception as e:
        logger.warning(f"Web search failed: {e}")
        return []
        return []


async def _analyze_intent_with_llm_if_enabled(task: str) -> IntentAnalysis:
    """
    Analyze intent, using LLM if enabled.
    
    Uses hybrid analysis when DISCOVERY_LLM_INTENT_ENABLED=true.
    """
    settings = get_settings()
    
    if getattr(settings, 'discovery_llm_intent_enabled', False):
        try:
            from integration_coworker.discovery.intent_llm import analyze_intent_hybrid
            return await analyze_intent_hybrid(task)
        except ImportError:
            logger.debug("LLM intent module not available")
        except Exception as e:
            logger.warning(f"LLM intent analysis failed, using heuristic: {e}")
    
    return analyze_discovery_intent(task)


async def resolve_spec_from_task(
    task: str,
    max_candidates: int = MAX_CANDIDATES_TO_TRY,
    skip_local_catalog: bool = False,
    hitl_callback: Optional[Callable[[HITLCandidateSelection], int]] = None,
    force_hitl: bool = False,
    require_confirmation: bool = False,
    repo_root: Optional[Union[Path, str]] = None,
) -> DiscoveryResult:
    """
    Resolve a natural language task to a validated spec URL.
    
    Slice 2/3/4 resolution strategy (local-first + web search + HITL):
    1. Analyze intent (heuristic or LLM-enhanced)
    2. Search local filesystem first if repo_root is provided (NEW)
    3. Search local catalog (pre-curated specs)
    4. Fall back to APIs.guru if local catalog misses
    5. Fall back to web search if enabled AND no confident candidate (Slice 4)
    6. Check confidence threshold for HITL (if enabled)
    7. Validate top candidates until one passes
    8. Return first valid candidate
    
    Web Search (Slice 4):
        Web search is DISABLED by default and must be explicitly enabled via:
        - DISCOVERY_WEB_SEARCH_ENABLED=true
        - TAVILY_API_KEY or SERPAPI_API_KEY
        
        It only runs when earlier sources produce no confident match
        (best candidate score < WEB_SEARCH_CONFIDENCE_THRESHOLD = 0.7).
        
        PRIVACY: Web search sends task-derived queries to third parties.
    
    Args:
        task: Natural language task description
        max_candidates: Maximum candidates to try validating
        skip_local_catalog: Skip local catalog (for testing/fallback)
        hitl_callback: Optional callback for HITL selection (returns index)
        force_hitl: Force HITL even if confidence is high
        require_confirmation: If True and HITL needed but no callback,
                             returns requires_hitl=True. If False (default),
                             proceeds with best-effort selection.
        repo_root: Optional path to the repository root to search for local specs
        
    Returns:
        DiscoveryResult with spec URL if found, HITL request if needed, or error details
        
    HITL Behavior:
        HITL is triggered when confidence < DISCOVERY_CONFIRMATION_THRESHOLD and
        either force_hitl=True or require_confirmation=True.
        
        If hitl_callback is provided, it's called synchronously to get user selection.
        
        If require_confirmation=True but no callback: returns requires_hitl=True.
        If require_confirmation=False (default): proceeds with best candidate.
        
        This ensures non-interactive code paths don't unexpectedly block.
        
    Examples:
        # Interactive CLI with HITL
        >>> result = await resolve_spec_from_task(
        ...     "Process payment with Stripe",
        ...     require_confirmation=True,
        ...     hitl_callback=my_ui_callback
        ... )
        
        # Non-interactive automation (best-effort, no blocking)
        >>> result = await resolve_spec_from_task("Process payment with Stripe")
        >>> assert not result.requires_hitl  # Never blocks
    """
    settings = get_settings()
    confirmation_threshold = settings.discovery_confirmation_threshold
    
    logger.info(f"Resolving spec for task: {task[:100]}...")
    
    # Record discovery attempt
    record_discovery_attempt(source="hybrid")
    
    # Step 1: Analyze intent (with LLM if enabled)
    intent = await _analyze_intent_with_llm_if_enabled(task)
    logger.debug(
        f"Intent: provider={intent.best_provider_hint}, "
        f"keywords={intent.keywords[:3]}, confidence={intent.confidence:.2f}"
    )
    
    if intent.confidence < 0.1:
        record_discovery_failure(source="hybrid", reason="weak_intent")
        return DiscoveryResult(
            success=False,
            intent=intent,
            error="Could not extract meaningful search signals from task description",
        )
    
    # Step 2: Search local filesystem first (if repo_root provided)
    candidates = []
    source = "apis_guru"
    
    if repo_root:
        # Convert to Path if string
        repo_path = Path(repo_root) if isinstance(repo_root, str) else repo_root
        
        local_fs_candidates = search_local_filesystem(
            repo_root=repo_path,
            provider_hint=intent.best_provider_hint,
            keywords=intent.keywords,
            max_results=max_candidates * 2,
        )
        
        if local_fs_candidates:
            logger.info(f"Local filesystem: found {len(local_fs_candidates)} candidates")
            candidates = local_fs_candidates
            source = "local_filesystem"
    
    # Step 3: Search local catalog (pre-curated specs)
    if not skip_local_catalog and len(candidates) < max_candidates:
        local_candidates = await _search_local_catalog(intent, max_candidates * 2)
        if local_candidates:
            logger.info(f"Local catalog: found {len(local_candidates)} candidates")
            # Merge, avoiding duplicates
            seen_urls = {c.spec_url for c in candidates}
            for c in local_candidates:
                if c.spec_url not in seen_urls:
                    candidates.append(c)
                    seen_urls.add(c.spec_url)
            if not candidates:
                candidates = local_candidates
                source = "local_catalog"
    
    # Step 4: Fall back to APIs.guru if earlier sources missed or insufficient
    if len(candidates) < max_candidates:
        try:
            apis_guru_candidates = await search_apis_guru(
                provider_hint=intent.best_provider_hint,
                keywords=intent.keywords,
                max_results=max_candidates * 2 - len(candidates),
            )
            
            # Merge, avoiding duplicates
            seen_urls = {c.spec_url for c in candidates}
            for c in apis_guru_candidates:
                if c.spec_url not in seen_urls:
                    candidates.append(c)
                    seen_urls.add(c.spec_url)
            
            if not candidates and apis_guru_candidates:
                source = "apis_guru"
                
        except Exception as e:
            logger.warning(f"APIs.guru search failed: {e}")
            if not candidates:
                # Don't fail yet - web search might help
                logger.debug("APIs.guru failed but will try web search")
    
    # Step 5: Fall back to web search if enabled AND no confident candidate (Slice 4)
    # Web search only runs when:
    # 1. Web search is enabled (DISCOVERY_WEB_SEARCH_ENABLED=true)
    # 2. No candidates found, OR best candidate score below threshold
    best_score = candidates[0].score if candidates else 0.0
    should_try_web_search = (
        len(candidates) == 0 or 
        best_score < WEB_SEARCH_CONFIDENCE_THRESHOLD
    )
    
    if should_try_web_search:
        web_candidates = await _search_web_for_candidates(
            intent, 
            max_results=max_candidates,
        )
        
        if web_candidates:
            logger.info(f"Web search: found {len(web_candidates)} additional candidates")
            
            # Merge web candidates, avoiding duplicates
            seen_urls = {c.spec_url for c in candidates}
            for c in web_candidates:
                if c.spec_url not in seen_urls:
                    candidates.append(c)
                    seen_urls.add(c.spec_url)
            
            # If web search was only source, update source
            if not source or (source != "local_catalog" and not any(
                c.score >= WEB_SEARCH_CONFIDENCE_THRESHOLD 
                for c in candidates if c not in web_candidates
            )):
                source = "web_search"
    
    if not candidates:
        record_discovery_failure(source=source, reason="no_candidates")
        return DiscoveryResult(
            success=False,
            intent=intent,
            error=f"No matching APIs found for: {intent.best_provider_hint or intent.keywords}",
        )
    
    # Sort candidates deterministically for reproducible HITL
    candidates = _sort_candidates_deterministic(candidates)
    
    # Record candidate count
    record_candidate_count(source=source, count=len(candidates))
    
    logger.info(
        f"Found {len(candidates)} candidates from {source}, "
        f"top match: {candidates[0].provider} (score={candidates[0].score:.2f})"
    )
    
    # Step 4: Check if HITL is needed
    top_score = candidates[0].score if candidates else 0.0
    combined_confidence = (intent.confidence + top_score) / 2
    
    # HITL is only triggered if:
    # 1. force_hitl=True, OR
    # 2. require_confirmation=True AND confidence is below threshold
    # Default (require_confirmation=False) NEVER triggers HITL - proceeds with best candidate
    needs_hitl = force_hitl or (
        require_confirmation and
        combined_confidence < confirmation_threshold and
        len(candidates) > 1
    )
    
    if needs_hitl:
        hitl_reason = (
            f"Confidence {combined_confidence:.2f} below threshold {confirmation_threshold:.2f}"
            if not force_hitl else "Forced HITL confirmation"
        )
        
        # Record HITL triggered
        record_hitl_triggered(reason="low_confidence" if not force_hitl else "forced")
        
        hitl_request = HITLCandidateSelection(
            candidates=candidates[:max_candidates],
            intent=intent,
            confidence=combined_confidence,
            reason=hitl_reason,
        )
        
        if hitl_callback:
            # Synchronous HITL callback
            logger.info("Triggering HITL callback for candidate selection")
            try:
                selected_index = hitl_callback(hitl_request)
                if 0 <= selected_index < len(candidates):
                    # User selected a candidate, proceed with validation
                    candidates = [candidates[selected_index]] + [
                        c for i, c in enumerate(candidates) if i != selected_index
                    ]
                    logger.info(f"User selected candidate {selected_index}: {candidates[0].provider}")
                    record_hitl_completed(outcome="selected")
            except Exception as e:
                logger.warning(f"HITL callback failed: {e}")
        else:
            # No callback - return HITL request for external handling
            # (only reached if require_confirmation=True or force_hitl=True)
            logger.info(f"HITL required: {hitl_reason}")
            return DiscoveryResult(
                success=False,
                confidence=combined_confidence,
                source=source,
                intent=intent,
                candidates=candidates[:max_candidates],
                requires_hitl=True,
                hitl_request=hitl_request,
            )
    
    # Step 5: Validate candidates until one passes
    validated_candidates = []
    for i, candidate in enumerate(candidates[:max_candidates]):
        logger.debug(f"Validating candidate {i+1}/{max_candidates}: {candidate.provider}")
        
        try:
            validation = await validate_spec_url(candidate.spec_url)
            validated_candidates.append(candidate)
            
            if validation.valid:
                logger.info(
                    f"Discovery success: {candidate.api_name} from {candidate.provider} "
                    f"(confidence={candidate.score:.2f}, source={source})"
                )
                
                # Calculate overall confidence
                # Combines intent confidence with search score
                overall_confidence = (intent.confidence + candidate.score) / 2
                
                # Record metrics
                record_validation_result(valid=True)
                record_discovery_success(source=source, provider=candidate.provider, confidence=overall_confidence)
                
                return DiscoveryResult(
                    success=True,
                    spec_url=candidate.spec_url,
                    provider=candidate.provider,
                    api_name=candidate.api_name,
                    confidence=overall_confidence,
                    source=source,
                    intent=intent,
                    candidates=candidates[:max_candidates],
                    validation=validation,
                )
            else:
                record_validation_result(valid=False)
                logger.warning(
                    f"Candidate {candidate.provider} failed validation: {validation.error}"
                )
                
        except Exception as e:
            logger.warning(f"Validation error for {candidate.provider}: {e}")
    
    # All candidates failed validation
    record_discovery_failure(source=source, reason="validation_failed")
    return DiscoveryResult(
        success=False,
        intent=intent,
        candidates=validated_candidates,
        error=f"All {len(validated_candidates)} candidates failed validation",
    )


async def resolve_with_user_selection(
    task: str,
    selected_index: int,
    candidates: List[SpecCandidate],
) -> DiscoveryResult:
    """
    Complete resolution after user selects a candidate from HITL.
    
    Args:
        task: Original task description
        selected_index: Index of user-selected candidate
        candidates: Original candidate list from HITL request
        
    Returns:
        DiscoveryResult with validated spec or error
    """
    if not 0 <= selected_index < len(candidates):
        return DiscoveryResult(
            success=False,
            error=f"Invalid selection index: {selected_index}",
        )
    
    candidate = candidates[selected_index]
    
    validation = await validate_spec_url(candidate.spec_url)
    record_validation_result(valid=validation.valid)
    
    if validation.valid:
        record_hitl_completed(outcome="selected")
        return DiscoveryResult(
            success=True,
            spec_url=candidate.spec_url,
            provider=candidate.provider,
            api_name=candidate.api_name,
            confidence=1.0,  # User confirmed
            source="user_selected",
            candidates=candidates,
            validation=validation,
        )
    else:
        return DiscoveryResult(
            success=False,
            candidates=candidates,
            validation=validation,
            error=f"Selected spec failed validation: {validation.error}",
        )


def resolve_spec_from_task_sync(
    task: str,
    max_candidates: int = MAX_CANDIDATES_TO_TRY,
) -> DiscoveryResult:
    """
    Synchronous wrapper for resolve_spec_from_task.
    
    For use in sync contexts (e.g., tests, CLI).
    """
    return asyncio.run(resolve_spec_from_task(task, max_candidates))

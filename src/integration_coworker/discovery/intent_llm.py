"""
LLM-Based Intent Extraction for Discovery (Slice 3)

Uses structured LLM output to extract discovery intent with higher accuracy
than heuristic-only approaches. Feature-flagged behind DISCOVERY_LLM_INTENT_ENABLED.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md Section Slice 3:
- LLM output is ADVISORY ONLY - never trust URLs from LLM
- Used to rank/search, not as direct source of spec URLs
- JSON schema enforced for deterministic parsing
- Falls back to heuristic intent on LLM failure

Usage:
    from integration_coworker.discovery.intent_llm import analyze_intent_with_llm
    
    # Only use if DISCOVERY_LLM_INTENT_ENABLED=true
    result = await analyze_intent_with_llm("Process payment with Stripe")
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from integration_coworker.config import get_settings
from integration_coworker.discovery.intent import IntentAnalysis, analyze_discovery_intent

logger = logging.getLogger(__name__)

# JSON schema for LLM structured output
# Per design: "unknown" is allowed to prevent hallucination
LLM_INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "provider_candidates": {
            "type": "array",
            "description": "Ranked list of likely API providers. Use 'unknown' if unsure.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Provider name (e.g., 'Stripe', 'OpenAI', 'unknown')"},
                    "domain": {"type": ["string", "null"], "description": "Known domain (e.g., 'stripe.com') or null"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1, "description": "Confidence 0-1"},
                    "reasoning": {"type": "string", "description": "Brief explanation for this candidate"},
                },
                "required": ["name", "confidence"],
            },
            "maxItems": 5,
        },
        "action_words": {
            "type": "array",
            "description": "Verbs describing the integration action",
            "items": {"type": "string"},
            "maxItems": 5,
        },
        "domain_keywords": {
            "type": "array",
            "description": "Domain-specific keywords for search",
            "items": {"type": "string"},
            "maxItems": 10,
        },
        "category_hint": {
            "type": ["string", "null"],
            "description": "API category: payments, messaging, storage, ai, social, etc. Null if unknown.",
        },
        "overall_confidence": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "Overall confidence in understanding the task",
        },
        "ambiguity_reason": {
            "type": ["string", "null"],
            "description": "If confidence < 0.7, explain why the task is ambiguous",
        },
    },
    "required": ["provider_candidates", "action_words", "domain_keywords", "overall_confidence"],
}

# System prompt for intent extraction
INTENT_EXTRACTION_PROMPT = """You are an API discovery assistant. Your job is to analyze task descriptions and identify which public API(s) would be needed.

RULES:
1. Only suggest APIs that exist publicly (not internal/proprietary)
2. Use "unknown" if you're not confident about the provider
3. Be conservative with confidence scores
4. Never make up URLs - just identify providers/keywords for searching

CATEGORIES (use these for category_hint):
- payments: Stripe, Square, PayPal, Adyen
- messaging: Twilio, SendGrid, MessageBird
- ai: OpenAI, Anthropic, Google AI
- storage: AWS S3, Dropbox, Box
- social: Twitter/X, Facebook, LinkedIn
- crm: Salesforce, HubSpot
- ecommerce: Shopify, WooCommerce
- communication: Zoom, Slack
- other: anything not fitting above

Analyze the following task and return structured JSON."""


@dataclass
class LLMIntentResult:
    """
    Result from LLM-based intent extraction.
    
    Attributes:
        provider_candidates: Ranked list of (name, domain, confidence, reasoning)
        action_words: Extracted action verbs
        domain_keywords: Domain-specific keywords
        category_hint: API category if identified
        overall_confidence: Confidence in the analysis
        ambiguity_reason: Why confidence is low (if applicable)
        raw_response: Original LLM response for debugging
    """
    provider_candidates: List[Dict[str, Any]] = field(default_factory=list)
    action_words: List[str] = field(default_factory=list)
    domain_keywords: List[str] = field(default_factory=list)
    category_hint: Optional[str] = None
    overall_confidence: float = 0.0
    ambiguity_reason: Optional[str] = None
    raw_response: Optional[str] = None
    
    def to_intent_analysis(self, raw_task: str = "") -> IntentAnalysis:
        """
        Convert to IntentAnalysis for compatibility with existing resolver.
        
        This merges LLM output with heuristic format.
        """
        # Get top provider if any
        explicit_provider = None
        inferred_providers = []
        
        for candidate in self.provider_candidates:
            name = candidate.get("name", "")
            if name.lower() == "unknown":
                continue
            
            domain = candidate.get("domain", "")
            
            # If domain looks like a real domain, use it
            if domain and "." in domain:
                if candidate.get("confidence", 0) >= 0.8 and not explicit_provider:
                    explicit_provider = domain
                inferred_providers.append(domain.lower())
            else:
                # Use name as fallback
                if candidate.get("confidence", 0) >= 0.8 and not explicit_provider:
                    explicit_provider = name.lower()
                inferred_providers.append(name.lower())
        
        # Build keywords from action words + domain keywords
        keywords = list(set(self.action_words + self.domain_keywords))[:15]
        
        return IntentAnalysis(
            explicit_provider=explicit_provider,
            inferred_providers=inferred_providers[:5],
            keywords=keywords,
            confidence=self.overall_confidence,
            raw_task=raw_task,
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize for logging/tracing."""
        return asdict(self)


async def analyze_intent_with_llm(
    task_description: str,
    model: Optional[str] = None,
    timeout_seconds: float = 15.0,
) -> Optional[LLMIntentResult]:
    """
    Analyze task description using LLM for structured intent extraction.
    
    IMPORTANT: LLM output is ADVISORY ONLY. Never use URLs from LLM directly.
    The output is used to rank/search, not as a source of spec URLs.
    
    Args:
        task_description: Natural language task description
        model: Override model (default from config)
        timeout_seconds: LLM call timeout
        
    Returns:
        LLMIntentResult or None if LLM call fails
        
    Feature Flag:
        Only call this if DISCOVERY_LLM_INTENT_ENABLED=true
    """
    settings = get_settings()
    
    # Check feature flag
    if not getattr(settings, 'discovery_llm_intent_enabled', False):
        logger.debug("LLM intent disabled by feature flag")
        return None
    
    try:
        from integration_coworker.llm.client import call_llm_async
    except ImportError:
        logger.warning("LLM client not available")
        return None
    
    # Build prompt
    messages = [
        {"role": "system", "content": INTENT_EXTRACTION_PROMPT},
        {"role": "user", "content": f"Task: {task_description}"},
    ]
    
    try:
        # Call LLM with JSON mode
        response = await call_llm_async(
            messages=messages,
            model=model or settings.llm.model,
            response_format={"type": "json_object"},
            max_tokens=500,
            temperature=0.0,  # Deterministic for consistent results
        )
        
        # Parse response
        content = response.get("content", "") if isinstance(response, dict) else str(response)
        
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON: {e}")
            return LLMIntentResult(raw_response=content)
        
        # Extract fields with defaults
        result = LLMIntentResult(
            provider_candidates=parsed.get("provider_candidates", []),
            action_words=parsed.get("action_words", []),
            domain_keywords=parsed.get("domain_keywords", []),
            category_hint=parsed.get("category_hint"),
            overall_confidence=float(parsed.get("overall_confidence", 0.0)),
            ambiguity_reason=parsed.get("ambiguity_reason"),
            raw_response=content,
        )
        
        logger.info(
            f"LLM intent extracted",
            extra={
                "task": task_description[:100],
                "confidence": result.overall_confidence,
                "provider_count": len(result.provider_candidates),
            }
        )
        
        return result
        
    except asyncio.TimeoutError:
        logger.warning(f"LLM intent extraction timed out after {timeout_seconds}s")
        return None
    except Exception as e:
        logger.warning(f"LLM intent extraction failed: {e}")
        return None


async def analyze_intent_hybrid(
    task_description: str,
    use_llm: Optional[bool] = None,
) -> IntentAnalysis:
    """
    Hybrid intent analysis: LLM-enhanced when available, heuristic fallback.
    
    This provides a single entry point that:
    1. Checks if LLM intent is enabled
    2. Tries LLM extraction if enabled
    3. Falls back to heuristic analysis
    4. Merges results if both succeed
    
    Args:
        task_description: Natural language task description
        use_llm: Override flag (None = use config)
        
    Returns:
        IntentAnalysis (always succeeds, may be heuristic-only)
    """
    settings = get_settings()
    
    # Determine if we should try LLM
    should_use_llm = use_llm if use_llm is not None else getattr(settings, 'discovery_llm_intent_enabled', False)
    
    # Get heuristic analysis (always works)
    heuristic_result = analyze_discovery_intent(task_description)
    
    if not should_use_llm:
        return heuristic_result
    
    # Try LLM enhancement
    llm_result = await analyze_intent_with_llm(task_description)
    
    if not llm_result or llm_result.overall_confidence < 0.3:
        # LLM failed or very low confidence - use heuristic only
        return heuristic_result
    
    # Convert LLM result to IntentAnalysis and merge using stable contract
    llm_analysis = llm_result.to_intent_analysis(task_description)
    
    return merge_intent(heuristic_result, llm_analysis)


# Threshold for heuristic authority - when heuristic is this confident, it wins
HEURISTIC_AUTHORITY_THRESHOLD = 0.8

# Known provider aliases for validation (subset - full list in catalog)
# Used to validate LLM-proposed providers when catalog unavailable
KNOWN_PROVIDER_ALIASES: Dict[str, str] = {
    "stripe": "stripe.com",
    "paypal": "paypal.com",
    "twilio": "twilio.com",
    "sendgrid": "sendgrid.com",
    "openai": "openai.com",
    "anthropic": "anthropic.com",
    "github": "github.com",
    "slack": "slack.com",
    "dropbox": "dropbox.com",
    "shopify": "shopify.com",
    "square": "squareup.com",
    "salesforce": "salesforce.com",
    "hubspot": "hubspot.com",
    "zoom": "zoom.us",
    "box": "box.com",
}


def _normalize_provider(provider: Optional[str]) -> Optional[str]:
    """
    Normalize provider string for consistent comparison.
    
    Applies: lowercase, strip whitespace, Unicode NFC normalization.
    """
    if not provider:
        return None
    import unicodedata
    normalized = unicodedata.normalize("NFC", provider.strip().lower())
    return normalized if normalized else None


def _validate_llm_provider(provider: str, known_aliases: Dict[str, str]) -> Optional[str]:
    """
    Validate an LLM-proposed provider against known sources.
    
    Returns the normalized provider if valid, None if should be rejected.
    LLM providers are only accepted if they match:
    - A known alias
    - Look like a valid domain (contains dot, reasonable length)
    """
    normalized = _normalize_provider(provider)
    if not normalized:
        return None
    
    # Remove common suffixes for matching
    base_name = normalized.replace(".com", "").replace(".io", "").replace(".ai", "")
    
    # Check against known aliases
    if base_name in known_aliases:
        return known_aliases[base_name]
    
    # Check if it looks like a domain
    if "." in normalized and 3 <= len(normalized) <= 100:
        return normalized
    
    # Check if full provider string matches an alias value
    if normalized in known_aliases.values():
        return normalized
    
    # Reject unknown providers from LLM
    return None


def merge_intent(
    heuristic: IntentAnalysis,
    llm: IntentAnalysis,
    known_aliases: Optional[Dict[str, str]] = None,
) -> IntentAnalysis:
    """
    Merge heuristic and LLM intent analyses with safe precedence rules.
    
    This is the SINGLE source of truth for how to combine intent signals.
    
    KEY PRINCIPLE: Heuristics are AUTHORITATIVE for high-confidence signals.
    LLM output is ADVISORY and must be validated before acceptance.
    
    Precedence rules (stable contract):
    
    1. explicit_provider:
       - If heuristic confidence >= 0.8, heuristic wins unconditionally
       - Otherwise, LLM wins ONLY if it passes validation (known alias or domain)
       - If LLM provider fails validation, fall back to heuristic
    
    2. inferred_providers:
       - Heuristic providers included unconditionally
       - LLM providers included only if they pass validation
       - Deduplicated with stable ordering (heuristic first, then validated LLM)
    
    3. keywords: Heuristic first, then LLM (deduplicated, normalized)
    
    4. confidence: max(heuristic, llm)
    
    5. raw_task: preserved from heuristic (original source)
    
    Args:
        heuristic: IntentAnalysis from heuristic analysis (always present)
        llm: IntentAnalysis from LLM analysis (may have partial data)
        known_aliases: Optional provider alias map (defaults to KNOWN_PROVIDER_ALIASES)
        
    Returns:
        Merged IntentAnalysis with deterministic, normalized field values
    """
    aliases = known_aliases if known_aliases is not None else KNOWN_PROVIDER_ALIASES
    
    # Rule 1: explicit_provider - Heuristic is authoritative when confident
    heuristic_provider = _normalize_provider(heuristic.explicit_provider)
    llm_provider_raw = llm.explicit_provider
    
    if heuristic.confidence >= HEURISTIC_AUTHORITY_THRESHOLD and heuristic_provider:
        # Heuristic is authoritative - don't let LLM override
        merged_explicit = heuristic_provider
        logger.debug(f"Heuristic authoritative (conf={heuristic.confidence:.2f}), using {merged_explicit}")
    elif llm_provider_raw:
        # LLM proposed a provider - validate it
        validated_llm = _validate_llm_provider(llm_provider_raw, aliases)
        if validated_llm:
            merged_explicit = validated_llm
            logger.debug(f"LLM provider validated: {llm_provider_raw} -> {validated_llm}")
        else:
            # LLM provider failed validation, fall back to heuristic
            merged_explicit = heuristic_provider
            logger.debug(f"LLM provider rejected (unknown): {llm_provider_raw}, using heuristic")
    else:
        merged_explicit = heuristic_provider
    
    # Rule 2: inferred_providers - Heuristic first (authoritative), then validated LLM
    seen_providers: set[str] = set()
    merged_providers: List[str] = []
    
    # Add heuristic providers first (always trusted)
    for p in heuristic.inferred_providers:
        p_norm = _normalize_provider(p)
        if p_norm and p_norm not in seen_providers:
            seen_providers.add(p_norm)
            merged_providers.append(p_norm)
    
    # Add validated LLM providers
    for p in llm.inferred_providers:
        validated = _validate_llm_provider(p, aliases)
        if validated and validated not in seen_providers:
            seen_providers.add(validated)
            merged_providers.append(validated)
    
    # Rule 3: keywords - Heuristic first, then LLM (all normalized, deduplicated)
    seen_keywords: set[str] = set()
    merged_keywords: List[str] = []
    
    for kw in heuristic.keywords + llm.keywords:
        kw_norm = _normalize_provider(kw)  # Same normalization
        if kw_norm and kw_norm not in seen_keywords:
            seen_keywords.add(kw_norm)
            merged_keywords.append(kw_norm)
    
    # Rule 4: confidence - max of both
    merged_confidence = max(heuristic.confidence, llm.confidence)
    
    # Rule 5: raw_task - from heuristic (original)
    merged_raw_task = heuristic.raw_task or llm.raw_task
    
    return IntentAnalysis(
        explicit_provider=merged_explicit,
        inferred_providers=merged_providers[:5],
        keywords=merged_keywords[:10],
        confidence=merged_confidence,
        raw_task=merged_raw_task,
    )


# For sync contexts
def analyze_intent_with_llm_sync(task_description: str) -> Optional[LLMIntentResult]:
    """Synchronous wrapper for analyze_intent_with_llm."""
    return asyncio.run(analyze_intent_with_llm(task_description))


def analyze_intent_hybrid_sync(task_description: str) -> IntentAnalysis:
    """Synchronous wrapper for analyze_intent_hybrid."""
    return asyncio.run(analyze_intent_hybrid(task_description))

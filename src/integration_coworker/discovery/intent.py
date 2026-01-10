"""
Intent Analysis for Spec Discovery (Slice 1: Heuristic only)

Extracts provider signals and keywords from natural language task descriptions
using simple pattern matching and tokenization. No LLM required.

Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md Section 1C:
- Slice 1: Deterministic heuristics only
- Slice 2: Add LLM structured output for ambiguous cases
"""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

# Common English stopwords to filter from keywords
STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare", "ought", "used",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "up", "about", "into",
    "through", "during", "before", "after", "above", "below", "between", "under",
    "again", "further", "then", "once", "here", "there", "when", "where", "why", "how",
    "all", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not",
    "only", "own", "same", "so", "than", "too", "very", "just", "also", "now", "i",
    "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours",
    "yourself", "yourselves", "he", "him", "his", "himself", "she", "her", "hers",
    "herself", "it", "its", "itself", "they", "them", "their", "theirs", "themselves",
    "what", "which", "who", "whom", "this", "that", "these", "those", "am", "as", "if",
    # Task-specific stopwords
    "want", "like", "please", "help", "create", "make", "build", "generate", "write",
    "give", "show", "get", "use", "using", "api", "code", "integration", "implement",
}

# Patterns to extract explicit provider mentions
# "from X", "using X", "with X", "via X"
# NOTE: These patterns are matched case-insensitively, but the match must
# be validated against KNOWN_PROVIDERS or PROVIDER_ALIASES to avoid false positives
# like "with subscription" or "from some"
PROVIDER_PATTERNS = [
    r"\bfrom\s+([A-Z][A-Za-z0-9_.-]+(?:\s+[A-Z][A-Za-z0-9_.-]*)?)",  # "from OpenAI"
    r"\busing\s+([A-Z][A-Za-z0-9_.-]+(?:\s+[A-Z][A-Za-z0-9_.-]*)?)",  # "using Stripe"
    r"\bwith\s+([A-Z][A-Za-z0-9_.-]+)(?:'s)?\s+(?:API|api)\b",        # "with Stripe's API" (requires "API")
    r"\bwith\s+([A-Z][A-Za-z0-9_.-]+)\b",                             # "with Twilio" (validated against KNOWN)
    r"\bvia\s+([A-Z][A-Za-z0-9_.-]+)",                                # "via Twilio"
    r"\b([A-Z][A-Za-z0-9_.-]+(?:\s+[A-Z][A-Za-z0-9_.-]*)?)\s+API\b",  # "OpenAI API"
    r"\bto\s+([A-Z][A-Za-z0-9_.-]+)\b",                               # "to Dropbox"
]

# Known provider names - used to validate pattern matches
# Matches must either be in KNOWN_PROVIDERS or PROVIDER_ALIASES to be accepted
KNOWN_PROVIDERS: Set[str] = {
    # AI
    "openai", "anthropic", "cohere", "huggingface", "stability", "runway", "assemblyai", "elevenlabs",
    # Payments
    "stripe", "paypal", "square", "chargebee", "freshbooks", "braintree",
    # Communications
    "twilio", "sendgrid", "mailchimp", "mailgun", "messagebird", "vonage", "pushover",
    # Cloud/Storage
    "dropbox", "box", "cloudinary", "aws", "azure", "gcp", "google", "s3",
    # Version Control
    "github", "gitlab", "bitbucket", "jira",
    # Social
    "slack", "discord", "twitter", "facebook", "linkedin",
    # Other
    "shopify", "salesforce", "hubspot", "zendesk", "intercom", "segment",
}

# Known provider aliases (normalize to canonical names)
PROVIDER_ALIASES = {
    "chatgpt": "openai",
    "gpt": "openai",
    "gpt-4": "openai",
    "gpt-3": "openai",
    "dalle": "openai",
    "dall-e": "openai",
    "sora": "openai",
    "whisper": "openai",
    "github": "github.com",
    "sendgrid": "sendgrid.com",
    "twilio": "twilio.com",
    "stripe": "stripe.com",
    "mailchimp": "mailchimp.com",
    "slack": "slack.com",
    "discord": "discord.com",
    "aws": "amazonaws.com",
    "azure": "azure.com",
    "google": "googleapis.com",
    "dropbox": "dropbox.com",
    "box": "box.com",
}


@dataclass
class IntentAnalysis:
    """
    Result of analyzing a task description for discovery signals.
    
    Attributes:
        explicit_provider: Provider explicitly mentioned (e.g., "OpenAI" from "using OpenAI")
        inferred_providers: Providers inferred from keywords (e.g., "stripe" from "payment")
        keywords: Domain keywords extracted from task (stopwords removed)
        confidence: Heuristic confidence score (0.0-1.0)
        raw_task: Original task description
    """
    
    explicit_provider: Optional[str] = None
    inferred_providers: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    confidence: float = 0.0
    raw_task: str = ""
    
    @property
    def has_explicit_provider(self) -> bool:
        """Check if an explicit provider was detected."""
        return self.explicit_provider is not None and len(self.explicit_provider) > 0
    
    @property
    def best_provider_hint(self) -> Optional[str]:
        """Get the best provider hint (explicit > inferred)."""
        if self.explicit_provider:
            return self.explicit_provider
        if self.inferred_providers:
            return self.inferred_providers[0]
        return None


def _extract_explicit_provider(task: str) -> Optional[str]:
    """
    Extract explicitly mentioned provider from task description.
    
    Uses pattern matching for phrases like "from OpenAI", "using Stripe", etc.
    Returns normalized provider name.
    
    Bug #1 Fix: Expanded to accept unknown providers that match the pattern,
    enabling discovery of providers not in the static KNOWN_PROVIDERS list.
    """
    for pattern in PROVIDER_PATTERNS:
        match = re.search(pattern, task)
        if match:
            provider = match.group(1).strip()
            # Remove trailing "API" or "'s"
            provider = re.sub(r"(?:'s|'s|\s+API|\s+api)$", "", provider)
            # Normalize via aliases
            provider_lower = provider.lower().replace(" ", "").replace("-", "")
            
            # Check if it's a known alias
            if provider_lower in PROVIDER_ALIASES:
                return PROVIDER_ALIASES[provider_lower]
            
            # Check if it's a known provider name
            if provider_lower in KNOWN_PROVIDERS:
                # Return with domain suffix if we have an alias mapping
                if provider_lower in PROVIDER_ALIASES:
                    return PROVIDER_ALIASES[provider_lower]
                return provider_lower
            
            # Bug #1 Fix: Accept unknown providers that look legitimate
            # Must be at least 3 chars and look like a proper name/domain
            if len(provider_lower) >= 3:
                logger.debug(f"Detected potential provider: {provider}")
                # Use the original casing or normalized?
                # Normalized is safer for comparisons
                return provider_lower
            
            # Not a known provider and too short/invalid - skip this match
            logger.debug(f"Pattern matched '{provider}' but rejected (too short/invalid)")
            continue
    
    return None


def _extract_keywords(task: str) -> List[str]:
    """
    Extract meaningful keywords from task description.
    
    Tokenizes, lowercases, removes stopwords, and deduplicates.
    """
    # Tokenize on word boundaries, keeping alphanumeric and hyphens
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9-]*", task)
    
    # Lowercase and filter stopwords
    keywords = []
    seen = set()
    for token in tokens:
        lower = token.lower()
        if lower not in STOPWORDS and lower not in seen and len(lower) > 2:
            keywords.append(lower)
            seen.add(lower)
    
    return keywords


def _infer_providers_from_keywords(keywords: List[str]) -> List[str]:
    """
    Infer possible providers from domain keywords.
    
    Maps common domain terms to likely API providers.
    """
    # Keyword to provider mappings
    KEYWORD_PROVIDERS = {
        # Payments
        "payment": ["stripe.com", "paypal.com", "square.com"],
        "payments": ["stripe.com", "paypal.com", "square.com"],
        "checkout": ["stripe.com", "paypal.com"],
        "subscription": ["stripe.com", "chargebee.com"],
        "billing": ["stripe.com", "chargebee.com"],
        "invoice": ["stripe.com", "freshbooks.com"],
        
        # Communications
        "sms": ["twilio.com", "messagebird.com", "vonage.com"],
        "text": ["twilio.com", "messagebird.com"],
        "email": ["sendgrid.com", "mailchimp.com", "mailgun.com"],
        "mail": ["sendgrid.com", "mailchimp.com"],
        "notification": ["twilio.com", "pushover.net"],
        "call": ["twilio.com", "vonage.com"],
        "voice": ["twilio.com", "vonage.com"],
        
        # AI/ML
        "ai": ["openai.com", "anthropic.com", "cohere.com"],
        "llm": ["openai.com", "anthropic.com", "cohere.com"],
        "gpt": ["openai.com"],
        "chat": ["openai.com", "anthropic.com"],
        "completion": ["openai.com", "anthropic.com"],
        "embedding": ["openai.com", "cohere.com"],
        "image": ["openai.com", "stability.ai"],
        "video": ["openai.com", "runway.com"],
        "transcription": ["openai.com", "assemblyai.com"],
        "speech": ["openai.com", "elevenlabs.com"],
        
        # Cloud storage
        "file": ["dropbox.com", "box.com", "google.com"],
        "storage": ["amazonaws.com", "azure.com", "google.com"],
        "upload": ["dropbox.com", "box.com", "cloudinary.com"],
        "document": ["dropbox.com", "box.com", "google.com"],
        
        # Version control
        "repository": ["github.com", "gitlab.com", "bitbucket.org"],
        "repo": ["github.com", "gitlab.com", "bitbucket.org"],
        "git": ["github.com", "gitlab.com", "bitbucket.org"],
        "pr": ["github.com", "gitlab.com", "bitbucket.org"],
        "issue": ["github.com", "gitlab.com", "jira.com"],
        
        # Social/collaboration
        "slack": ["slack.com"],
        "discord": ["discord.com"],
        "team": ["slack.com", "microsoft.com"],
    }
    
    inferred = []
    seen = set()
    
    for keyword in keywords:
        if keyword in KEYWORD_PROVIDERS:
            for provider in KEYWORD_PROVIDERS[keyword]:
                if provider not in seen:
                    inferred.append(provider)
                    seen.add(provider)
    
    return inferred


def _calculate_confidence(
    explicit_provider: Optional[str],
    inferred_providers: List[str],
    keywords: List[str],
) -> float:
    """
    Calculate heuristic confidence score for discovery.
    
    Confidence is higher when:
    - Explicit provider is mentioned (0.9 base)
    - Multiple keywords match same provider (0.7 base)
    - Single keyword match (0.5 base)
    - No matches (0.2 base)
    """
    if explicit_provider:
        # Explicit mention is high confidence
        return 0.9
    
    if inferred_providers:
        # Multiple inferences pointing to same provider = higher confidence
        if len(inferred_providers) == 1:
            return 0.7
        elif len(inferred_providers) <= 3:
            return 0.6
        else:
            # Many candidates = less confident
            return 0.4
    
    if keywords:
        # Have keywords but no provider match
        return 0.3
    
    # No useful signals
    return 0.1


def analyze_discovery_intent(task: str) -> IntentAnalysis:
    """
    Analyze a task description to extract discovery signals.
    
    Slice 1: Uses heuristic pattern matching only (no LLM).
    
    Args:
        task: Natural language task description
        
    Returns:
        IntentAnalysis with extracted provider and keywords
        
    Examples:
        >>> analyze_discovery_intent("Create a video using Sora from OpenAI")
        IntentAnalysis(explicit_provider="openai.com", keywords=["video", "sora"], ...)
        
        >>> analyze_discovery_intent("Send SMS notification")
        IntentAnalysis(explicit_provider=None, inferred_providers=["twilio.com", ...], ...)
    """
    if not task or not task.strip():
        logger.warning("Empty task description provided for discovery intent analysis")
        return IntentAnalysis(confidence=0.0, raw_task=task)
    
    # Step 1: Extract explicit provider mention
    explicit_provider = _extract_explicit_provider(task)
    
    # Step 2: Extract keywords
    keywords = _extract_keywords(task)
    
    # Step 3: Infer providers from keywords (only if no explicit provider)
    inferred_providers = []
    if not explicit_provider:
        inferred_providers = _infer_providers_from_keywords(keywords)
    
    # Step 4: Calculate confidence
    confidence = _calculate_confidence(explicit_provider, inferred_providers, keywords)
    
    result = IntentAnalysis(
        explicit_provider=explicit_provider,
        inferred_providers=inferred_providers,
        keywords=keywords,
        confidence=confidence,
        raw_task=task,
    )
    
    logger.debug(
        f"Intent analysis: provider={result.explicit_provider or result.inferred_providers}, "
        f"keywords={result.keywords[:5]}, confidence={result.confidence:.2f}"
    )
    
    return result

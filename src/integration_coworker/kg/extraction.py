"""
Entity and endpoint extraction from OpenAPI specs for KG scoring.

This module provides spec-first deterministic extraction of entities and endpoints
from task descriptions by matching against the OpenAPI spec vocabulary.

Design Decision: Approach 1.1 (Spec-First Deterministic) was chosen because:
- Works offline (no LLM call required)
- Deterministic (same input = same output)
- Fast (<10ms for large specs)
- No new dependencies

See docs/PROD_GAP_CLOSURE_PLAN_KG_DB_STREAMING.md for alternatives considered.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any

logger = logging.getLogger(__name__)

# Common API action synonyms for matching
API_SYNONYMS: Dict[str, Set[str]] = {
    "create": {"post", "add", "insert", "new", "make"},
    "get": {"read", "fetch", "retrieve", "list", "show", "find"},
    "update": {"put", "patch", "modify", "edit", "change"},
    "delete": {"remove", "destroy", "drop", "cancel"},
    "send": {"post", "submit", "transmit"},
    "search": {"query", "find", "lookup", "filter"},
}

# Reverse lookup: synonym -> canonical
SYNONYM_TO_CANONICAL: Dict[str, str] = {}
for canonical, synonyms in API_SYNONYMS.items():
    SYNONYM_TO_CANONICAL[canonical] = canonical
    for syn in synonyms:
        SYNONYM_TO_CANONICAL[syn] = canonical

# Stopwords to exclude from matching
STOPWORDS: Set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "must", "shall", "can",
    "this", "that", "these", "those", "it", "its", "i", "we", "you", "they",
    "using", "via", "into", "use", "make", "then",
}


@dataclass
class SpecCandidates:
    """
    Candidate vocabulary extracted from an OpenAPI spec.
    
    Contains normalized forms of entities and endpoints for matching.
    """
    # Entity candidates: schema names, tag names, operationId prefixes
    entities: Set[str] = field(default_factory=set)
    # Normalized versions (lowercase, singular)
    entities_normalized: Dict[str, str] = field(default_factory=dict)
    
    # Endpoint candidates: paths, summaries
    endpoints: Set[str] = field(default_factory=set)
    # Path patterns for fuzzy matching
    endpoint_patterns: Dict[str, str] = field(default_factory=dict)
    
    # Operation summaries/descriptions for keyword matching
    operation_keywords: Dict[str, Set[str]] = field(default_factory=dict)


def _normalize_word(word: str) -> str:
    """
    Normalize a word: lowercase, remove common plural suffixes.
    
    Args:
        word: Word to normalize
        
    Returns:
        Normalized form of the word
    """
    word = word.lower().strip()
    
    # Handle common plural forms
    if word.endswith("ies"):
        return word[:-3] + "y"
    elif word.endswith("es") and len(word) > 3:
        # messages -> message, but not "uses"
        if word[-3] in "sxzh":
            return word[:-2]
    elif word.endswith("s") and len(word) > 2 and not word.endswith("ss"):
        return word[:-1]
    
    return word


def _tokenize(text: str) -> Set[str]:
    """
    Tokenize text into normalized words, excluding stopwords.
    
    Args:
        text: Text to tokenize
        
    Returns:
        Set of normalized, non-stopword tokens
    """
    # Split on whitespace and common delimiters
    raw_tokens = re.split(r'[\s_\-/.,;:!?()"\'\[\]{}]+', text.lower())
    
    # Normalize and filter
    tokens = set()
    for token in raw_tokens:
        if not token or len(token) < 2:
            continue
        if token in STOPWORDS:
            continue
        normalized = _normalize_word(token)
        if normalized and len(normalized) >= 2:
            tokens.add(normalized)
    
    return tokens


def _camel_to_words(name: str) -> List[str]:
    """
    Split camelCase or PascalCase into words.
    
    Examples:
        createPaymentIntent -> ['create', 'payment', 'intent']
        HTTPResponse -> ['http', 'response']
    """
    # Insert space before uppercase letters (but not consecutive)
    words = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    # Handle sequences of uppercase (e.g., HTTP -> HTTP)
    words = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', words)
    return [w.lower() for w in words.split()]


def _extract_path_segments(path: str) -> Set[str]:
    """
    Extract meaningful segments from an API path.
    
    Args:
        path: API path like /v1/customers/{id}/subscriptions
        
    Returns:
        Set of normalized segments: {'customer', 'subscription'}
    """
    segments = set()
    parts = path.split("/")
    
    for part in parts:
        # Skip version prefixes and path parameters
        if not part or part.startswith("{") or re.match(r'^v\d+$', part):
            continue
        
        # Normalize and add
        normalized = _normalize_word(part)
        if normalized and len(normalized) >= 2 and normalized not in STOPWORDS:
            segments.add(normalized)
    
    return segments


def build_spec_candidates(openapi_spec: Dict[str, Any]) -> SpecCandidates:
    """
    Build entity/endpoint candidates from OpenAPI spec.
    
    Extracts:
    - Schema names from components/schemas (entities)
    - Tag names (entities)
    - OperationId prefixes (entities)
    - Path patterns (endpoints)
    - Operation summaries (keywords)
    
    Args:
        openapi_spec: Parsed OpenAPI specification dict
        
    Returns:
        SpecCandidates with populated candidate sets
    """
    candidates = SpecCandidates()
    
    if not openapi_spec:
        return candidates
    
    # Extract from components/schemas
    schemas = openapi_spec.get("components", {}).get("schemas", {})
    for schema_name in schemas:
        candidates.entities.add(schema_name)
        normalized = _normalize_word(schema_name)
        candidates.entities_normalized[normalized] = schema_name
        
        # Also add individual words from camelCase names
        for word in _camel_to_words(schema_name):
            if len(word) >= 3 and word not in STOPWORDS:
                candidates.entities_normalized[word] = schema_name
    
    # Extract from tags
    tags = openapi_spec.get("tags", [])
    for tag in tags:
        tag_name = tag.get("name", "") if isinstance(tag, dict) else str(tag)
        if tag_name:
            candidates.entities.add(tag_name)
            normalized = _normalize_word(tag_name)
            candidates.entities_normalized[normalized] = tag_name
    
    # Extract from paths and operations
    paths = openapi_spec.get("paths", {})
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
            
        # Add path itself
        candidates.endpoints.add(path)
        
        # Extract path segments for matching
        segments = _extract_path_segments(path)
        for segment in segments:
            candidates.endpoint_patterns[segment] = path
        
        # Process each operation (get, post, put, delete, etc.)
        for method in ["get", "post", "put", "patch", "delete", "head", "options"]:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            
            # Extract from operationId
            operation_id = operation.get("operationId", "")
            if operation_id:
                # Split operationId into words
                words = _camel_to_words(operation_id)
                for word in words:
                    if len(word) >= 3 and word not in STOPWORDS:
                        candidates.entities_normalized[word] = operation_id
            
            # Extract keywords from summary
            summary = operation.get("summary", "")
            if summary:
                keywords = _tokenize(summary)
                candidates.operation_keywords[f"{method.upper()} {path}"] = keywords
            
            # Extract keywords from description
            description = operation.get("description", "")
            if description:
                keywords = _tokenize(description[:500])  # Limit length
                key = f"{method.upper()} {path}"
                existing = candidates.operation_keywords.get(key, set())
                candidates.operation_keywords[key] = existing | keywords
    
    logger.debug(
        f"Built spec candidates: {len(candidates.entities)} entities, "
        f"{len(candidates.endpoints)} endpoints"
    )
    
    return candidates


def extract_entities_from_task(
    task_description: str,
    candidates: SpecCandidates,
) -> List[str]:
    """
    Extract entity names from task description that match the spec.
    
    Uses tokenization + normalization to match task words against
    spec schema names, tag names, and operationId prefixes.
    
    Args:
        task_description: Natural language task description
        candidates: Pre-built spec candidates
        
    Returns:
        List of entity names found in the spec
    """
    if not task_description or not candidates.entities_normalized:
        return []
    
    task_tokens = _tokenize(task_description)
    matched_entities: Set[str] = set()
    
    for token in task_tokens:
        # Direct match
        if token in candidates.entities_normalized:
            matched_entities.add(candidates.entities_normalized[token])
            continue
        
        # Synonym match (e.g., "charge" for "create payment")
        canonical = SYNONYM_TO_CANONICAL.get(token)
        if canonical and canonical in candidates.entities_normalized:
            matched_entities.add(candidates.entities_normalized[canonical])
            continue
        
        # Partial match for compound words (e.g., "customer" matches "CustomerData")
        for normalized, original in candidates.entities_normalized.items():
            if token in normalized or normalized in token:
                matched_entities.add(original)
                break
    
    result = list(matched_entities)
    logger.debug(f"Extracted entities from task: {result}")
    return result


def extract_endpoints_from_task(
    task_description: str,
    candidates: SpecCandidates,
) -> List[str]:
    """
    Extract endpoint paths from task description that match the spec.
    
    Uses tokenization + normalization to match task words against
    path segments and operation keywords.
    
    Args:
        task_description: Natural language task description
        candidates: Pre-built spec candidates
        
    Returns:
        List of endpoint paths found in the spec
    """
    if not task_description or not candidates.endpoints:
        return []
    
    task_tokens = _tokenize(task_description)
    matched_endpoints: Set[str] = set()
    
    # Match against path segments
    for token in task_tokens:
        if token in candidates.endpoint_patterns:
            matched_endpoints.add(candidates.endpoint_patterns[token])
            continue
        
        # Synonym match
        canonical = SYNONYM_TO_CANONICAL.get(token)
        if canonical and canonical in candidates.endpoint_patterns:
            matched_endpoints.add(candidates.endpoint_patterns[canonical])
    
    # Match against operation keywords (higher confidence)
    for endpoint_key, keywords in candidates.operation_keywords.items():
        overlap = task_tokens & keywords
        if len(overlap) >= 2:  # Require at least 2 keyword matches
            # Extract path from key (e.g., "GET /v1/customers" -> "/v1/customers")
            parts = endpoint_key.split(" ", 1)
            if len(parts) == 2:
                matched_endpoints.add(parts[1])
    
    result = list(matched_endpoints)
    logger.debug(f"Extracted endpoints from task: {result}")
    return result


def extract_entities_endpoints_from_spec(
    openapi_spec: Dict[str, Any],
    task_description: str,
) -> tuple[List[str], List[str]]:
    """
    Extract entities and endpoints from spec that match task description.
    
    Convenience function that combines build_spec_candidates,
    extract_entities_from_task, and extract_endpoints_from_task.
    
    Args:
        openapi_spec: Parsed OpenAPI specification dict
        task_description: Natural language task description
        
    Returns:
        Tuple of (entity_names, endpoint_paths) for KG scoring
    """
    if not openapi_spec or not task_description:
        return [], []
    
    candidates = build_spec_candidates(openapi_spec)
    entities = extract_entities_from_task(task_description, candidates)
    endpoints = extract_endpoints_from_task(task_description, candidates)
    
    return entities, endpoints

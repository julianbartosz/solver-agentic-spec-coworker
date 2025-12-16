"""
SpecSource plugin registry and detection.

This module implements the SpecSource plugin pattern for unified handling of
API specs, file specs, and document guides. All spec sources implement a common
protocol (detect, parse) and can be dynamically registered.

Usage:
    from integration_coworker.sources import detect_and_route, SOURCE_REGISTRY
    
    # Detect and parse content
    result = detect_and_route(content, uri, content_type)
    
    # Check result type
    if result.source_type == SourceType.API:
        openapi_spec = result.data
    elif result.source_type == SourceType.FILE:
        file_spec, fields = result.data["file_spec"], result.data["fields"]
"""

import logging
from typing import List, Optional, Tuple, Union

from .base import ParsedSpec, SourceType, SpecSource

# Use structured logging with extra fields for filtering/alerting
# See docs/BUCKET_2_TECH_DEBT_AND_SCALING.md TD-OBS-001
logger = logging.getLogger(__name__)

# Registry of all source handlers in detection priority order
# Higher priority sources are checked first
# OpenAPI has highest priority (we want explicit API specs to match first)
# File sources come next
# PDF guide source is last (most permissive matcher)
SOURCE_REGISTRY: List[SpecSource] = []


def register_source(source: SpecSource, priority: int = 50) -> None:
    """
    Register a source handler in the registry.
    
    Args:
        source: A SpecSource implementation
        priority: Higher = checked earlier (0-100)
    """
    SOURCE_REGISTRY.append((priority, source))
    SOURCE_REGISTRY.sort(key=lambda x: x[0], reverse=True)


def get_registered_sources() -> List[SpecSource]:
    """Get all registered sources in priority order."""
    return [source for _, source in SOURCE_REGISTRY]


def detect_and_route(
    content: Union[bytes, str],
    uri: str,
    content_type: str = "",
) -> ParsedSpec:
    """
    Detect content type and route to appropriate source handler.
    
    This is the main entry point for the unified detection system.
    It scores each registered source and uses the highest-confidence match.
    
    Args:
        content: Raw content (bytes or string)
        uri: Source URI (file path or URL)
        content_type: MIME type if known (optional)
        
    Returns:
        ParsedSpec with source_type indicating 'api' or 'file'
        
    Raises:
        ValueError: If no source handler matched the content
    """
    if not SOURCE_REGISTRY:
        raise RuntimeError(
            "No source handlers registered. Import sources to register them."
        )
    
    # Determine content length for logging
    content_length = len(content) if isinstance(content, bytes) else len(content.encode('utf-8'))
    
    logger.debug(
        "source.detection.start",
        extra={
            "uri": uri,
            "content_type": content_type or "(none)",
            "content_length": content_length,
        }
    )
    
    # Score each source
    scores: List[Tuple[float, int, SpecSource]] = []
    all_scores: dict = {}  # For logging
    
    for priority, source in SOURCE_REGISTRY:
        source_name = source.__class__.__name__
        try:
            score = source.detect(content, uri, content_type)
            all_scores[source_name] = round(score, 3)
            if score > 0:
                scores.append((score, priority, source))
                logger.info(
                    "source.detection.score",
                    extra={
                        "source_class": source_name,
                        "uri": uri,
                        "score": round(score, 3),
                        "priority": priority,
                    }
                )
        except Exception as e:
            logger.warning(
                "source.detection.error",
                extra={
                    "source_class": source_name,
                    "uri": uri,
                    "error": str(e),
                }
            )
    
    if not scores:
        logger.warning(
            "source.detection.rejected",
            extra={
                "uri": uri,
                "all_scores": all_scores,
                "best_score": 0.0,
            }
        )
        raise ValueError(
            f"No source handler matched content from {uri}. "
            f"Registered sources: {[s.__class__.__name__ for _, s in SOURCE_REGISTRY]}"
        )
    
    # Use highest-scoring source (break ties by priority)
    scores.sort(key=lambda x: (x[0], x[1]), reverse=True)
    best_score, best_priority, best_source = scores[0]
    
    logger.info(
        "source.detection.selected",
        extra={
            "source_class": best_source.__class__.__name__,
            "uri": uri,
            "score": round(best_score, 3),
        }
    )
    
    # Parse with selected source
    result = best_source.parse(content, uri)
    
    logger.info(
        "source.inference.complete",
        extra={
            "source_class": best_source.__class__.__name__,
            "uri": uri,
            "confidence": round(result.confidence, 3),
            "warning_count": len(result.warnings),
            "error_count": len(result.errors),
        }
    )
    
    return result


def detect_source_type(
    content: Union[bytes, str],
    uri: str,
    content_type: str = "",
) -> Optional[SourceType]:
    """
    Detect only the source type without parsing.
    
    Useful for routing decisions without full parsing overhead.
    
    Returns:
        SourceType.API, SourceType.FILE, or None if no match
    """
    if not SOURCE_REGISTRY:
        return None
    
    best_score = 0.0
    best_source = None
    
    for _, source in SOURCE_REGISTRY:
        try:
            score = source.detect(content, uri, content_type)
            if score > best_score:
                best_score = score
                best_source = source
        except Exception:
            pass
    
    if best_source is None:
        return None
    
    # Determine source type based on source class
    # This is a heuristic - sources should set this in their parsed output
    source_name = best_source.__class__.__name__.lower()
    if "openapi" in source_name or "api" in source_name:
        return SourceType.API
    return SourceType.FILE


# Import and register sources on module load
# This is done at the bottom to avoid circular imports
def _register_default_sources() -> None:
    """Register default source handlers."""
    try:
        from .openapi import OpenAPISource
        register_source(OpenAPISource(), priority=90)
    except ImportError:
        logger.debug("OpenAPISource not available")
    
    try:
        from .csv_source import CSVSource
        register_source(CSVSource(), priority=70)
    except ImportError:
        logger.debug("CSVSource not available")
    
    try:
        from .excel import ExcelSource
        register_source(ExcelSource(), priority=65)
    except ImportError:
        logger.debug("ExcelSource not available")
    
    try:
        from .fixed_width import FixedWidthSource
        register_source(FixedWidthSource(), priority=60)
    except ImportError:
        logger.debug("FixedWidthSource not available")
    
    try:
        from .pdf_guide import PDFGuideSource
        register_source(PDFGuideSource(), priority=40)
    except ImportError:
        logger.debug("PDFGuideSource not available")


# Defer registration until first use to avoid import errors during development
_sources_registered = False


def ensure_sources_registered() -> None:
    """Ensure default sources are registered."""
    global _sources_registered
    if not _sources_registered:
        _register_default_sources()
        _sources_registered = True


__all__ = [
    "ParsedSpec",
    "SourceType", 
    "SpecSource",
    "SOURCE_REGISTRY",
    "register_source",
    "get_registered_sources",
    "detect_and_route",
    "detect_source_type",
    "ensure_sources_registered",
]

"""
Base protocol adapter interface and registry.

NO hidden global singleton — callers must explicitly create a registry.
This makes testing predictable and avoids import-time side effects.

SINGLE ENTRY POINT CONTRACT:
    registry.convert_spec(parsed_spec: ParsedSpec) -> List[Operation]

This is the ONLY public conversion method. Pipeline code should never
branch on find_adapter() or call adapter.convert() directly.

Design: docs/PROTOCOL_SUPPORT_VNEXT.md Option B
Date: 2025-12-22
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Type, Protocol, TYPE_CHECKING
import logging

from integration_coworker.domain.ir import Operation, ProtocolType

if TYPE_CHECKING:
    from integration_coworker.sources.base import ParsedSpec

logger = logging.getLogger(__name__)


class ProtocolAdapter(ABC):
    """
    Abstract base class for protocol adapters.
    
    Each adapter converts a protocol-specific specification
    to a list of protocol-agnostic Operation objects.
    
    CRITICAL: Adapters must parse from NATIVE spec formats,
    NOT from lossy pseudo-OpenAPI conversions.
    """
    
    @property
    @abstractmethod
    def protocol_type(self) -> ProtocolType:
        """Return the protocol type this adapter handles."""
        pass
    
    @abstractmethod
    def can_handle(self, spec_data: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
        """
        Check if this adapter can handle the given spec.
        
        Args:
            spec_data: The parsed specification data (native format)
            metadata: Metadata including _parsed_from, _conversion_quality, etc.
            
        Returns:
            True if this adapter can convert the spec
        """
        pass
    
    @abstractmethod
    def convert(
        self,
        spec_data: Dict[str, Any],
        metadata: Dict[str, Any],
        source_uri: str,
    ) -> List[Operation]:
        """
        Convert a spec to Operation IR objects.
        
        Args:
            spec_data: The parsed specification data (NATIVE format, not pseudo-OpenAPI)
            metadata: Metadata including _parsed_from, _conversion_quality
            source_uri: Source URI for traceability
            
        Returns:
            List of Operation objects extracted from the spec
        """
        pass


class AdapterRegistry:
    """
    Registry for protocol adapters.
    
    NOT a singleton — create explicitly for test isolation.
    Use create_default_registry() to get a pre-populated instance.
    
    SINGLE ENTRY POINT CONTRACT:
        registry.convert_spec(parsed_spec) -> List[Operation]
    
    This is the ONLY public conversion method. Pipeline code should never
    branch on find_adapter() or call adapter.convert() directly.
    
    Usage:
        registry = create_default_registry()
        operations = registry.convert_spec(parsed_spec)  # <-- THE ONE METHOD
    """
    
    def __init__(self):
        self._adapters: Dict[ProtocolType, ProtocolAdapter] = {}
    
    def register(self, adapter: ProtocolAdapter) -> "AdapterRegistry":
        """
        Register an adapter for its protocol type.
        
        Returns self for chaining.
        """
        self._adapters[adapter.protocol_type] = adapter
        logger.debug(f"Registered adapter for {adapter.protocol_type.value}")
        return self
    
    def get_adapter(self, protocol: ProtocolType) -> Optional[ProtocolAdapter]:
        """Get adapter for a specific protocol type."""
        return self._adapters.get(protocol)
    
    def _find_adapter(
        self,
        spec_data: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Optional[ProtocolAdapter]:
        """
        Find the appropriate adapter for a spec (INTERNAL).
        
        Checks each registered adapter's can_handle() method.
        Returns the first matching adapter.
        """
        for adapter in self._adapters.values():
            if adapter.can_handle(spec_data, metadata):
                return adapter
        return None
    
    def convert_spec(self, parsed_spec: "ParsedSpec") -> List[Operation]:
        """
        Convert a ParsedSpec to Operations — THE SINGLE ENTRY POINT.
        
        This is the ONLY method pipeline code should call.
        Handles all metadata extraction and adapter dispatch internally.
        
        Args:
            parsed_spec: A ParsedSpec object from the parsing step
            
        Returns:
            List of Operation objects (may be empty if no adapter matches)
        """
        from integration_coworker.sources.base import ParsedSpec
        
        if not isinstance(parsed_spec, ParsedSpec):
            logger.warning(f"convert_spec expects ParsedSpec, got {type(parsed_spec)}")
            return []
        
        # Extract spec data
        spec_data = parsed_spec.data if isinstance(parsed_spec.data, dict) else {}
        
        # Build metadata including raw content for native parsing
        metadata = dict(parsed_spec.metadata) if parsed_spec.metadata else {}
        if parsed_spec.raw_content:
            if isinstance(parsed_spec.raw_content, bytes):
                metadata["_raw_content"] = parsed_spec.raw_content.decode("utf-8", errors="replace")
            else:
                metadata["_raw_content"] = parsed_spec.raw_content
        
        source_uri = parsed_spec.source_uri or "unknown"
        
        # Find and invoke adapter
        adapter = self._find_adapter(spec_data, metadata)
        if adapter is None:
            logger.debug(f"No adapter for {source_uri}, returning empty operations")
            return []
        
        logger.info(f"Converting {source_uri} with {adapter.protocol_type.value} adapter")
        return adapter.convert(spec_data, metadata, source_uri)
    
    # Legacy method kept for backward compatibility in unit tests
    def find_adapter(
        self,
        spec_data: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> Optional[ProtocolAdapter]:
        """Find adapter (prefer convert_spec for pipeline code)."""
        return self._find_adapter(spec_data, metadata)
    
    @property
    def registered_protocols(self) -> List[ProtocolType]:
        """List all registered protocol types."""
        return list(self._adapters.keys())


def create_default_registry() -> AdapterRegistry:
    """
    Create an AdapterRegistry with all built-in adapters registered.
    
    Call this explicitly — no hidden import-time side effects.
    
    Returns:
        A fully-populated AdapterRegistry
    """
    registry = AdapterRegistry()
    
    # Import adapters lazily to avoid circular imports
    from .rest import RestAdapter
    from .graphql import GraphQLAdapter
    from .asyncapi import AsyncAPIAdapter
    
    registry.register(RestAdapter())
    registry.register(GraphQLAdapter())
    registry.register(AsyncAPIAdapter())
    
    logger.info(f"Created default registry with {len(registry.registered_protocols)} adapters")
    return registry

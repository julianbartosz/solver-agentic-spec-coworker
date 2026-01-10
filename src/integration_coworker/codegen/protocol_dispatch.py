"""
Protocol-aware codegen dispatch.

This module implements the Strategy Pattern for protocol-specific code generation.
Each protocol (REST, GraphQL, AsyncAPI) has its own codegen strategy that knows how
to generate appropriate client code, flows, and tests.

Per Phase 2 design:
- REST: HTTP client with request/response
- GraphQL: Query/Mutation clients + Subscription handlers (WebSocket-aware)
- AsyncAPI: Pub/Sub handlers with broker-specific semantics

CRITICAL SEMANTICS (per project requirements):
- AsyncAPI v2: "publish" = app RECEIVES, "subscribe" = app SENDS
- GraphQL: Subscriptions require WebSocket transport (graphql-transport-ws)
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Protocol, Type

from integration_coworker.domain.ir import (
    CommunicationPattern,
    Operation,
    ProtocolType,
)


# =============================================================================
# Code Generation Artifacts
# =============================================================================

class ArtifactType(Enum):
    """Types of code artifacts that can be generated."""
    CLIENT = "client"          # API client class/function
    FLOW = "flow"              # Integration flow/orchestration
    TEST = "test"              # Unit/integration tests
    HANDLER = "handler"        # Event handler (for async protocols)
    SUBSCRIBER = "subscriber"  # Subscription client (GraphQL/AsyncAPI)
    PUBLISHER = "publisher"    # Publisher client (AsyncAPI)


@dataclass
class GeneratedArtifact:
    """
    A generated code artifact.
    
    Attributes:
        artifact_type: Type of artifact (client, flow, test, etc.)
        filename: Suggested filename
        code: Generated source code
        language: Programming language
        imports: Required import statements
        dependencies: Package dependencies to install
        protocol: Source protocol
        operation_id: Source operation ID
        metadata: Protocol-specific metadata
    """
    artifact_type: ArtifactType
    filename: str
    code: str
    language: str = "python"
    imports: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    protocol: ProtocolType = ProtocolType.REST
    operation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CodegenContext:
    """
    Context for code generation.
    
    Contains all the information needed to generate code for an operation.
    """
    # Target language
    language: str = "python"
    
    # Naming conventions
    provider_code: str = "unknown"
    class_name_prefix: str = ""
    
    # Style preferences
    use_async: bool = True
    use_type_hints: bool = True
    docstring_style: str = "google"
    
    # Protocol-specific options
    graphql_transport: str = "websocket"  # websocket, sse, http
    graphql_ws_subprotocol: str = "graphql-transport-ws"
    asyncapi_broker: str = "generic"  # kafka, amqp, mqtt, generic
    
    # Output preferences
    include_tests: bool = True
    test_framework: str = "pytest"


# =============================================================================
# Codegen Strategy Protocol
# =============================================================================

class CodegenStrategy(Protocol):
    """
    Protocol for protocol-specific code generation strategies.
    
    Each protocol (REST, GraphQL, AsyncAPI) implements this interface
    to generate appropriate code artifacts.
    """
    
    @property
    def protocol_type(self) -> ProtocolType:
        """Return the protocol type this strategy handles."""
        ...
    
    def generate_client(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """
        Generate a client artifact for the operation.
        
        For REST: HTTP client method
        For GraphQL: Query/Mutation function or Subscription handler
        For AsyncAPI: Publisher or Subscriber handler
        """
        ...
    
    def generate_flow(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate an integration flow artifact."""
        ...
    
    def generate_test(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate a test artifact."""
        ...
    
    def build_prompt(
        self,
        operation: Operation,
        context: CodegenContext,
        artifact_type: ArtifactType,
        skeleton_code: str,
    ) -> str:
        """
        Build an LLM prompt for code generation.
        
        Returns a protocol-aware prompt that includes:
        - Operation semantics (not just HTTP method/path)
        - Protocol-specific patterns
        - Correct transport/communication patterns
        """
        ...


# =============================================================================
# Strategy Dispatcher
# =============================================================================

class StrategyDispatcher:
    """
    Dispatches operations to the appropriate codegen strategy.
    
    Usage:
        dispatcher = StrategyDispatcher()
        dispatcher.register(RESTCodegenStrategy())
        dispatcher.register(GraphQLCodegenStrategy())
        
        artifact = dispatcher.generate(operation, context, ArtifactType.CLIENT)
    """
    
    def __init__(self) -> None:
        self._strategies: Dict[ProtocolType, CodegenStrategy] = {}
    
    def register(self, strategy: CodegenStrategy) -> "StrategyDispatcher":
        """Register a codegen strategy."""
        self._strategies[strategy.protocol_type] = strategy
        return self
    
    def get_strategy(self, protocol: ProtocolType) -> Optional[CodegenStrategy]:
        """Get the strategy for a protocol."""
        return self._strategies.get(protocol)
    
    def generate(
        self,
        operation: Operation,
        context: CodegenContext,
        artifact_type: ArtifactType,
    ) -> Optional[GeneratedArtifact]:
        """
        Generate a code artifact for an operation.
        
        Dispatches to the appropriate strategy based on operation.protocol.
        """
        strategy = self.get_strategy(operation.protocol)
        if not strategy:
            return None
        
        if artifact_type == ArtifactType.CLIENT:
            return strategy.generate_client(operation, context)
        elif artifact_type == ArtifactType.FLOW:
            return strategy.generate_flow(operation, context)
        elif artifact_type == ArtifactType.TEST:
            return strategy.generate_test(operation, context)
        else:
            # For protocol-specific types, delegate to client generation
            return strategy.generate_client(operation, context)
    
    def build_prompt(
        self,
        operation: Operation,
        context: CodegenContext,
        artifact_type: ArtifactType,
        skeleton_code: str,
    ) -> Optional[str]:
        """Build a protocol-aware LLM prompt."""
        strategy = self.get_strategy(operation.protocol)
        if not strategy:
            return None
        return strategy.build_prompt(operation, context, artifact_type, skeleton_code)
    
    @property
    def supported_protocols(self) -> List[ProtocolType]:
        """List of supported protocols."""
        return list(self._strategies.keys())


# =============================================================================
# Factory
# =============================================================================

def create_default_dispatcher(
    file_specs: Optional[List[Any]] = None,
    file_fields: Optional[List[Any]] = None,
) -> StrategyDispatcher:
    """
    Create a dispatcher with all built-in strategies registered.
    
    Args:
        file_specs: Optional list of FileSpec objects for file-based operations.
                   Required for ProtocolType.FILE operations.
        file_fields: Optional list of FileField objects for file-based operations.
                    Required for ProtocolType.FILE operations.
    
    Returns:
        A fully-configured StrategyDispatcher
    """
    from integration_coworker.codegen.strategies import (
        AsyncAPICodegenStrategy,
        FileCodegenStrategy,
        GraphQLCodegenStrategy,
        RESTCodegenStrategy,
    )
    
    dispatcher = (
        StrategyDispatcher()
        .register(RESTCodegenStrategy())
        .register(GraphQLCodegenStrategy())
        .register(AsyncAPICodegenStrategy())
    )
    
    # Register FileCodegenStrategy if file specs are provided
    # or always register with empty lists for protocol support detection
    dispatcher.register(FileCodegenStrategy(
        file_specs=file_specs or [],
        file_fields=file_fields or [],
    ))
    
    return dispatcher


# =============================================================================
# Exports
# =============================================================================

__all__ = [
    "ArtifactType",
    "GeneratedArtifact",
    "CodegenContext",
    "CodegenStrategy",
    "StrategyDispatcher",
    "create_default_dispatcher",
]

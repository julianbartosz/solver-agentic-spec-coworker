"""
Protocol adapters for converting spec formats to unified IR.

Each adapter handles a specific protocol (REST, GraphQL, gRPC, AsyncAPI)
and converts its native representation to the protocol-agnostic Operation model.

Design: docs/PROTOCOL_SUPPORT_VNEXT.md Option B
Date: 2025-12-22
"""
from .base import ProtocolAdapter, AdapterRegistry, create_default_registry

__all__ = [
    "ProtocolAdapter",
    "AdapterRegistry",
    "create_default_registry",
]

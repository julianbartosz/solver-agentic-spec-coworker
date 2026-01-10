"""
Protocol-aware codegen strategies.

This module provides strategy pattern implementations for generating code
artifacts specific to each protocol type (REST, GraphQL, AsyncAPI).

ARCHITECTURE:
The StrategyDispatcher in protocol_dispatch.py routes operations to the
appropriate strategy based on the operation's protocol_type field.

SEMANTIC REFERENCES:

1. REST/OpenAPI:
   - OpenAPI 3.0 Specification: https://spec.openapis.org/oas/v3.0.3
   - Standard HTTP request-response pattern

2. GraphQL:
   - GraphQL Spec: https://spec.graphql.org/October2021/
   - Subscription transport: graphql-transport-ws protocol
   - Reference: https://github.com/enisdenjo/graphql-ws/blob/master/PROTOCOL.md

3. AsyncAPI:
   - AsyncAPI v2 Specification: https://v2.asyncapi.com/docs/reference/specification/v2.6.0
   - CRITICAL: AsyncAPI uses app-centric terminology!
     - "publish" = APP RECEIVES (inbound)
     - "subscribe" = APP SENDS (outbound)
   - Tutorial clarification: https://www.asyncapi.com/docs/tutorials/getting-started/coming-from-openapi
"""

from integration_coworker.codegen.strategies.rest import RESTCodegenStrategy
from integration_coworker.codegen.strategies.graphql import GraphQLCodegenStrategy
from integration_coworker.codegen.strategies.asyncapi import AsyncAPICodegenStrategy
from integration_coworker.codegen.strategies.file import FileCodegenStrategy

__all__ = [
    "RESTCodegenStrategy",
    "GraphQLCodegenStrategy", 
    "AsyncAPICodegenStrategy",
    "FileCodegenStrategy",
]

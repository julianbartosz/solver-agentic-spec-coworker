"""
GraphQL schema parser.

Thin wrapper around graphql-core for parsing GraphQL SDL.
This provides the parse_graphql_schema function that tests expect.

Design: docs/PROTOCOL_SUPPORT_VNEXT.md
Date: 2025-12-22
"""
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# Check if graphql-core is available
try:
    from graphql import parse, build_ast_schema
    from graphql.type import GraphQLSchema
    HAS_GRAPHQL_CORE = True
except ImportError:
    HAS_GRAPHQL_CORE = False
    logger.warning("graphql-core not installed. GraphQL parsing will fail.")


def parse_graphql_schema(sdl_content: str) -> Dict[str, Any]:
    """
    Parse GraphQL SDL and convert to a pseudo-OpenAPI structure.
    
    This is a DETERMINISTIC conversion (no LLM involved).
    
    Args:
        sdl_content: GraphQL SDL string
        
    Returns:
        Dict with pseudo-OpenAPI structure + quality markers:
        - _parsed_from: "graphql"
        - _conversion_quality: "deterministic"
        - paths: Extracted operations as pseudo-endpoints
        
    Raises:
        ImportError: If graphql-core is not installed
        Exception: If SDL parsing fails
    """
    if not HAS_GRAPHQL_CORE:
        raise ImportError("graphql-core is required for GraphQL parsing")
    
    # Parse SDL
    document = parse(sdl_content)
    schema = build_ast_schema(document)
    
    # Convert to pseudo-OpenAPI structure
    result: Dict[str, Any] = {
        "_parsed_from": "graphql",
        "_conversion_quality": "deterministic",
        "openapi": "3.0.0",
        "info": {"title": "GraphQL API", "version": "1.0.0"},
        "paths": {},
    }
    
    # Extract Query operations
    if schema.query_type:
        for field_name in schema.query_type.fields:
            result["paths"][f"/graphql/query/{field_name}"] = {
                "post": {
                    "operationId": f"query_{field_name}",
                    "tags": ["query"],
                }
            }
    
    # Extract Mutation operations
    if schema.mutation_type:
        for field_name in schema.mutation_type.fields:
            result["paths"][f"/graphql/mutation/{field_name}"] = {
                "post": {
                    "operationId": f"mutation_{field_name}",
                    "tags": ["mutation"],
                }
            }
    
    # Extract Subscription operations
    if schema.subscription_type:
        for field_name in schema.subscription_type.fields:
            result["paths"][f"/graphql/subscription/{field_name}"] = {
                "post": {
                    "operationId": f"subscription_{field_name}",
                    "tags": ["subscription"],
                }
            }
    
    return result


def is_graphql_sdl(content: str) -> bool:
    """
    Check if content looks like GraphQL SDL.
    
    Args:
        content: String to check
        
    Returns:
        True if content appears to be GraphQL SDL
    """
    if not content or not isinstance(content, str):
        return False
    
    sample = content[:2000].lower()
    indicators = ["type query", "type mutation", "type subscription", "schema {"]
    return any(ind in sample for ind in indicators)

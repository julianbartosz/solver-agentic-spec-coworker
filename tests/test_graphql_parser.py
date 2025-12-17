"""
Test the hybrid GraphQL parser (Fix SPEC-001).

Tests:
1. Deterministic parsing with graphql-core
2. Type conversion (scalars, objects, enums, inputs)
3. Query/Mutation/Subscription extraction
4. Parameter handling
"""
import json
import pytest

pytestmark = pytest.mark.requires_graphql


SAMPLE_GRAPHQL_SCHEMA = '''
type Query {
  users(limit: Int, offset: Int): [User!]!
  user(id: ID!): User
  posts(authorId: ID): [Post]
}

type Mutation {
  createUser(input: CreateUserInput!): User!
  updateUser(id: ID!, input: UpdateUserInput!): User
  deleteUser(id: ID!): Boolean!
}

type User {
  id: ID!
  name: String!
  email: String!
  age: Int
  posts: [Post!]
}

type Post {
  id: ID!
  title: String!
  content: String
  author: User!
  createdAt: DateTime
}

input CreateUserInput {
  name: String!
  email: String!
  age: Int
}

input UpdateUserInput {
  name: String
  email: String
  age: Int
}

enum Status {
  ACTIVE
  INACTIVE
  PENDING
}

scalar DateTime
'''


def test_deterministic_graphql_parsing():
    """Test that GraphQL schema is parsed deterministically without LLM."""
    from integration_coworker.graph.nodes.detect_and_parse_spec import _parse_graphql_to_pseudo_openapi
    
    result = _parse_graphql_to_pseudo_openapi(SAMPLE_GRAPHQL_SCHEMA, 'test://graphql-schema')
    
    assert result is not None, "Parser returned None"
    assert result.get("_parse_method") == "deterministic", f"Expected deterministic, got {result.get('_parse_method')}"
    assert result.get("_parsed_from") == "graphql"
    
    # Check paths exist
    paths = result.get("paths", {})
    assert len(paths) >= 6, f"Expected at least 6 paths (3 query + 3 mutation), got {len(paths)}"
    
    # Check query endpoints
    assert "/graphql/query/users" in paths
    assert "/graphql/query/user" in paths
    assert "/graphql/query/posts" in paths
    
    # Check mutation endpoints
    assert "/graphql/mutation/createUser" in paths
    assert "/graphql/mutation/updateUser" in paths
    assert "/graphql/mutation/deleteUser" in paths
    
    print(f"\n✅ Deterministic parsing: {len(paths)} paths extracted")


def test_schema_extraction():
    """Test that component schemas are correctly extracted."""
    from integration_coworker.graph.nodes.detect_and_parse_spec import _parse_graphql_to_pseudo_openapi
    
    result = _parse_graphql_to_pseudo_openapi(SAMPLE_GRAPHQL_SCHEMA, 'test://graphql-schema')
    schemas = result.get("components", {}).get("schemas", {})
    
    # Check types exist
    assert "User" in schemas
    assert "Post" in schemas
    assert "CreateUserInput" in schemas
    assert "UpdateUserInput" in schemas
    assert "Status" in schemas
    
    # Check User schema structure
    user_schema = schemas["User"]
    assert user_schema["type"] == "object"
    assert "id" in user_schema["properties"]
    assert "name" in user_schema["properties"]
    assert "email" in user_schema["properties"]
    
    # Check required fields
    assert "id" in user_schema.get("required", [])
    assert "name" in user_schema.get("required", [])
    
    # Check enum
    status_schema = schemas["Status"]
    assert status_schema["type"] == "string"
    assert set(status_schema["enum"]) == {"ACTIVE", "INACTIVE", "PENDING"}
    
    print(f"\n✅ Schema extraction: {len(schemas)} schemas extracted")
    for name in schemas:
        print(f"   - {name}")


def test_type_conversion():
    """Test that GraphQL types are correctly converted to OpenAPI types."""
    from integration_coworker.graph.nodes.detect_and_parse_spec import _graphql_type_to_openapi
    from graphql import GraphQLString, GraphQLInt, GraphQLFloat, GraphQLBoolean, GraphQLID
    from graphql import GraphQLList, GraphQLNonNull, GraphQLObjectType
    
    # Test scalars
    assert _graphql_type_to_openapi(GraphQLString)["type"] == "string"
    assert _graphql_type_to_openapi(GraphQLInt)["type"] == "integer"
    assert _graphql_type_to_openapi(GraphQLFloat)["type"] == "number"
    assert _graphql_type_to_openapi(GraphQLBoolean)["type"] == "boolean"
    assert _graphql_type_to_openapi(GraphQLID)["type"] == "string"
    
    # Test NonNull
    non_null_result = _graphql_type_to_openapi(GraphQLNonNull(GraphQLString))
    assert non_null_result["type"] == "string"
    assert non_null_result["_nullable"] == False
    
    # Test List
    list_result = _graphql_type_to_openapi(GraphQLList(GraphQLString))
    assert list_result["type"] == "array"
    assert list_result["items"]["type"] == "string"
    
    print("\n✅ Type conversion: All scalar and wrapper types converted correctly")


def test_query_parameters():
    """Test that query field arguments are correctly converted to parameters."""
    from integration_coworker.graph.nodes.detect_and_parse_spec import _parse_graphql_to_pseudo_openapi
    
    result = _parse_graphql_to_pseudo_openapi(SAMPLE_GRAPHQL_SCHEMA, 'test://graphql-schema')
    
    # Check users query has parameters
    users_get = result["paths"]["/graphql/query/users"]["get"]
    params = users_get.get("parameters", [])
    
    param_names = {p["name"] for p in params}
    assert "limit" in param_names
    assert "offset" in param_names
    
    # Check user query has required id parameter
    user_get = result["paths"]["/graphql/query/user"]["get"]
    params = user_get.get("parameters", [])
    
    id_param = next((p for p in params if p["name"] == "id"), None)
    assert id_param is not None
    assert id_param["required"] == True
    
    print("\n✅ Query parameters: Arguments correctly converted to query parameters")


def test_mutation_request_body():
    """Test that mutation arguments are correctly converted to request body."""
    from integration_coworker.graph.nodes.detect_and_parse_spec import _parse_graphql_to_pseudo_openapi
    
    result = _parse_graphql_to_pseudo_openapi(SAMPLE_GRAPHQL_SCHEMA, 'test://graphql-schema')
    
    # Check createUser mutation has request body
    create_post = result["paths"]["/graphql/mutation/createUser"]["post"]
    request_body = create_post.get("requestBody", {})
    
    assert request_body.get("required") == True
    schema = request_body.get("content", {}).get("application/json", {}).get("schema", {})
    assert "input" in schema.get("properties", {})
    
    print("\n✅ Mutation request body: Arguments correctly converted to request body")


def test_reproducibility():
    """Test that parsing is reproducible (same input = same output)."""
    from integration_coworker.graph.nodes.detect_and_parse_spec import _parse_graphql_to_pseudo_openapi
    
    result1 = _parse_graphql_to_pseudo_openapi(SAMPLE_GRAPHQL_SCHEMA, 'test://graphql-schema')
    result2 = _parse_graphql_to_pseudo_openapi(SAMPLE_GRAPHQL_SCHEMA, 'test://graphql-schema')
    
    # Remove any timestamp or random fields
    for r in [result1, result2]:
        r.pop("_source_uri", None)
    
    # Paths should be identical
    assert set(result1["paths"].keys()) == set(result2["paths"].keys())
    
    # Schemas should be identical
    schemas1 = set(result1["components"]["schemas"].keys())
    schemas2 = set(result2["components"]["schemas"].keys())
    assert schemas1 == schemas2
    
    # Full comparison (structure)
    assert json.dumps(result1, sort_keys=True) == json.dumps(result2, sort_keys=True)
    
    print("\n✅ Reproducibility: Two parses of same schema produce identical output")


if __name__ == "__main__":
    print("=" * 60)
    print("HYBRID GraphQL PARSER TESTS (Fix SPEC-001)")
    print("=" * 60)
    
    test_deterministic_graphql_parsing()
    test_schema_extraction()
    test_type_conversion()
    test_query_parameters()
    test_mutation_request_body()
    test_reproducibility()
    
    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)

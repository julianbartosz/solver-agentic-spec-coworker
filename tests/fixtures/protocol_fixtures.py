"""Shared test fixtures for protocol adapter tests."""

# GraphQL SDL fixtures
GRAPHQL_SDL_SIMPLE = """
type Query {
    users: [User]
    user(id: ID!): User
}

type User {
    id: ID!
    name: String!
}
"""

GRAPHQL_SDL_WITH_MUTATIONS = """
type Query {
    users: [User]
}

type Mutation {
    createUser(name: String!): User
}

type User {
    id: ID!
    name: String!
}
"""

GRAPHQL_SDL_WITH_SUBSCRIPTIONS = """
type Query {
    users: [User]
}

type Subscription {
    userCreated: User
}

type User {
    id: ID!
    name: String!
}
"""

GRAPHQL_SDL_FULL = """
type Query {
    users: [User!]!
    user(id: ID!): User
}

type User {
    id: ID!
    name: String!
}

type Mutation {
    createUser(name: String!): User!
}

type Subscription {
    userCreated: User!
}
"""

# AsyncAPI fixtures
ASYNCAPI_V2_SIMPLE = {
    "asyncapi": "2.6.0",
    "info": {"title": "Test", "version": "1.0.0"},
    "channels": {
        "events": {
            "publish": {"operationId": "onEvent"},
        }
    }
}

ASYNCAPI_V2_PUB_SUB = {
    "asyncapi": "2.6.0",
    "channels": {
        "events": {
            "publish": {"operationId": "pub"},
            "subscribe": {"operationId": "sub"},
        }
    }
}

ASYNCAPI_YAML_SIMPLE = """
asyncapi: 2.6.0
info:
  title: User Events
  version: 1.0.0
channels:
  user/created:
    publish:
      operationId: onUserCreated
      message:
        payload:
          type: object
          properties:
            id:
              type: string
            name:
              type: string
"""

# OpenAPI fixtures
OPENAPI_30_SIMPLE = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/users": {
            "get": {
                "operationId": "getUsers",
                "responses": {"200": {"description": "OK"}},
            }
        }
    },
}

OPENAPI_30_WITH_POST = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0.0"},
    "paths": {
        "/users": {
            "get": {"operationId": "getUsers", "responses": {"200": {"description": "OK"}}},
            "post": {"operationId": "createUser", "responses": {"201": {"description": "Created"}}},
        }
    },
}

SWAGGER_20_SIMPLE = {
    "swagger": "2.0",
    "info": {"title": "Legacy API", "version": "1.0.0"},
    "paths": {
        "/items": {
            "get": {"operationId": "listItems", "responses": {"200": {"description": "OK"}}},
        }
    },
}

"""
Domain models for Silver and Gold layers.

These dataclasses mirror the database schema but are used in-memory
during workflow execution before persistence.
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
from enum import Enum


class PolicyType(str, Enum):
    """Enum for policy types."""
    AUTH = "auth"
    RETRY = "retry"
    RATE_LIMIT = "rate_limit"
    LOGGING = "logging"
    IDEMPOTENCY = "idempotency"
    VALIDATION = "validation"
    TRANSFORM = "transform"


# ============================================================================
# Silver Layer Models (API Spec Model)
# ============================================================================

@dataclass
class SourceSystem:
    """A third-party API or service we're integrating with."""
    id: Optional[int]
    code: str  # e.g., "stripe", "twilio"
    name: str
    base_url: Optional[str] = None
    auth_type: Optional[str] = None  # e.g., "bearer", "api_key", "oauth2"
    documentation_url: Optional[str] = None


@dataclass
class SpecDocument:
    """A raw API specification document (OpenAPI, AsyncAPI, etc.)."""
    id: Optional[int]
    source_system_id: Optional[int]
    version: str
    uri: str  # URL or file path
    content_type: str  # e.g., "application/yaml", "application/json"
    sha256: str
    content: str  # Full document content


@dataclass
class Endpoint:
    """An API endpoint extracted from a specification."""
    id: Optional[int]
    source_system_id: Optional[int]
    spec_document_id: Optional[int]
    path: str  # e.g., "/v1/checkout/sessions"
    method: str  # GET, POST, PUT, DELETE, etc.
    operation_id: Optional[str]
    summary: Optional[str]
    description: Optional[str]
    request_schema_id: Optional[int]
    response_schema_id: Optional[int]
    auth_required: bool = True
    pagination_style: Optional[str] = None  # e.g., "offset", "cursor"
    rate_limit_bucket: Optional[str] = None


@dataclass
class EndpointParameter:
    """A parameter for an API endpoint (path, query, header, etc.)."""
    id: Optional[int]
    endpoint_id: Optional[int]
    name: str
    location: str  # "path", "query", "header", "cookie"
    required: bool
    schema_ref: str  # Type or schema reference
    description: Optional[str] = None


@dataclass
class Schema:
    """A data schema (request/response body, component schema)."""
    id: Optional[int]
    source_system_id: Optional[int]
    name: str
    ref: str  # JSON Schema $ref or OpenAPI component path


@dataclass
class SchemaField:
    """A field within a schema."""
    id: Optional[int]
    schema_id: Optional[int]
    name: str
    field_type: Optional[str] = None  # e.g., "string", "integer", "object"
    type: Optional[str] = None  # Alias for field_type
    json_path: Optional[str] = None  # JSON path to the field
    format: Optional[str] = None  # e.g., "date-time", "email"
    required: bool = False
    description: Optional[str] = None


@dataclass
class Entity:
    """A domain entity extracted from schemas (e.g., "Customer", "Payment")."""
    id: Optional[int]
    source_system_id: Optional[int]
    name: str
    schema_id: Optional[int]  # Link to the schema it was derived from
    description: Optional[str] = None


@dataclass
class EntityRelationship:
    """A relationship between two entities."""
    id: Optional[int]
    source_entity_id: int
    target_entity_id: int
    relationship_type: str  # e.g., "has_many", "belongs_to"


@dataclass
class Event:
    """An event or webhook defined in the API."""
    id: Optional[int]
    source_system_id: Optional[int]
    name: str
    description: Optional[str] = None
    payload_schema_id: Optional[int] = None


@dataclass
class SpecChunkEmbedding:
    """Vector embedding of a spec document chunk for semantic search."""
    id: Optional[int]
    spec_document_id: int
    chunk_index: int
    content: str
    embedding: Optional[list] = None  # Will be list of floats (1536 dim for OpenAI)


# ============================================================================
# Gold Layer Models (Integration Workflow Model)
# ============================================================================

@dataclass
class WorkflowTemplate:
    """A reusable workflow template."""
    id: Optional[int]
    source_system_id: Optional[int]
    code: str  # Unique code for the template
    name: str
    description: Optional[str] = None


@dataclass
class IntegrationTask:
    """A specific integration task (e.g., 'Create Stripe checkout session')."""
    id: Optional[int]
    source_system_id: Optional[int]
    task_slug: str  # Unique identifier, e.g., "stripe_create_checkout_session"
    provider_code: str  # e.g., "stripe"
    description: str
    workflow_template_id: Optional[int] = None
    target_spec_document_id: Optional[int] = None
    input_entities: Optional[list] = None
    output_entities: Optional[list] = None
    constraints: Optional[Dict[str, Any]] = None


@dataclass
class IntegrationFlowNode:
    """A node (step) in an integration workflow."""
    id: Optional[int]
    task_id: Optional[int]
    node_key: str  # Unique within the workflow, e.g., "validate_input"
    node_type: str  # e.g., "validation", "api_call", "transform", "decision"
    label: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    endpoint_id: Optional[int] = None
    entity_id: Optional[int] = None
    position: int = 0


@dataclass
class IntegrationFlowEdge:
    """An edge (connection) between workflow nodes."""
    id: Optional[int]
    task_id: Optional[int]
    from_node_key: str
    to_node_key: str
    condition: Optional[str] = None  # For conditional branches


@dataclass
class EndpointBinding:
    """Binds a workflow node to a specific API endpoint."""
    id: Optional[int]
    task_id: Optional[int]
    flow_node_key: str
    endpoint_id: Optional[int]
    params_mapping: Optional[Dict[str, Any]] = None
    request_mapping: Optional[Dict[str, Any]] = None
    response_mapping: Optional[Dict[str, Any]] = None


@dataclass
class Policy:
    """A policy applied to an integration (auth, retry, rate limiting, etc.)."""
    id: Optional[int]
    task_id: Optional[int]
    policy_type: str  # e.g., "auth", "retry", "rate_limit", "logging"
    scope: str  # e.g., "workflow", "flow_node", "endpoint"
    scope_ref: Optional[str] = None  # Reference to what it applies to (e.g., node_key)
    config: Optional[Dict[str, Any]] = None


@dataclass
class CodeArtifact:
    """Generated code artifact (client, workflow, test, config)."""
    id: Optional[int]
    task_id: Optional[int]
    artifact_type: str  # "client", "flow", "test", "config"
    language: str  # "python", "typescript", etc.
    module_name: str
    rel_path: str  # Relative path in the target repo
    content: str  # The actual code

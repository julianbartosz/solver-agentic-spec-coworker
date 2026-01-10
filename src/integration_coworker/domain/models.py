"""
Domain models for Silver and Gold layers.

These dataclasses mirror the database schema but are used in-memory
during workflow execution before persistence.
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
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


class SourceRefType(str, Enum):
    """Type of source reference (API-002)."""
    URL = "url"
    FILE = "file"


# ============================================================================
# Source Reference Model (API-002: Multi-Spec Traceability)
# ============================================================================

@dataclass
class SourceRef:
    """
    A reference to a source specification file or URL.
    
    Enables traceability: every Silver entity (Endpoint, Schema) can be
    traced back to its specific source. Critical for multi-provider
    integrations where entities come from different specs.
    
    Per V2_IMPLEMENTATION_PLAN_SUPPLEMENT.md Section 4.
    """
    id: Optional[int]
    uri: str  # The URL or file path
    ref_type: SourceRefType  # URL or FILE
    provider_code: Optional[str] = None  # e.g., "stripe", "github"
    spec_document_id: Optional[int] = None  # Link to SpecDocument once persisted
    
    @classmethod
    def from_ref(cls, ref: str, provider_code: Optional[str] = None) -> "SourceRef":
        """Create a SourceRef from a reference string."""
        ref_type = SourceRefType.URL if ref.startswith(("http://", "https://")) else SourceRefType.FILE
        return cls(
            id=None,
            uri=ref,
            ref_type=ref_type,
            provider_code=provider_code,
        )


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
class SpecSection:
    """
    A logical section within a spec document.
    
    Per design doc Appendix B.2: spec_sections tracks sections like
    paths, schemas, info, security for finer-grained RAG retrieval.
    """
    id: Optional[int]
    spec_document_id: Optional[int]
    section_type: str  # "info", "paths", "schemas", "security", etc.
    title: Optional[str]
    path: Optional[str]  # JSON path to the section
    start_offset: Optional[int]
    end_offset: Optional[int]
    content: str


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
    # V38-005: Required headers for beta/special endpoints (e.g., OpenAI Assistants API)
    # Dict mapping header name to header value, e.g., {"OpenAI-Beta": "assistants=v2"}
    required_headers: Optional[dict] = None


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
    source_system_id: Optional[int]  # Per Appendix A spec
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


# ============================================================================
# Silver Layer Models (File Spec Model)
# ============================================================================

class FileType(str, Enum):
    """Type of file specification."""
    CSV = "csv"
    TSV = "tsv"
    FIXED_WIDTH = "fixed_width"
    XLSX = "xlsx"
    XLS = "xls"
    PIPE_DELIMITED = "pipe_delimited"
    EDI_X12 = "edi_x12"
    EDIFACT = "edifact"
    OTHER_DELIMITED = "other_delimited"


class FileFieldType(str, Enum):
    """Data type for file fields."""
    STRING = "string"
    INTEGER = "integer"
    DECIMAL = "decimal"
    DATE = "date"
    DATETIME = "datetime"
    BOOLEAN = "boolean"
    EMAIL = "email"
    UUID = "uuid"
    JSON = "json"
    BINARY = "binary"


class ValidationRuleType(str, Enum):
    """Types of validation rules for file data."""
    REQUIRED = "required"
    RANGE = "range"
    REGEX = "regex"
    LOOKUP = "lookup"
    CROSS_FIELD = "cross_field"
    LENGTH = "length"
    ENUM = "enum"
    UNIQUE = "unique"
    FORMAT = "format"


@dataclass
class FileSpec:
    """
    A file-based data specification (CSV, fixed-width, Excel, etc.).
    
    This is the Silver layer model for file integrations, parallel to
    Endpoint for API integrations. It captures file format metadata
    and links to the fields within the file.
    
    Per docs/FILE_INTEGRATION_V1_PLAN.md Section 5.1
    """
    id: Optional[int]
    source_system_id: Optional[int]
    name: str  # e.g., "daily_transactions", "customer_export"
    file_type: str  # FileType enum value
    spec_document_id: Optional[int] = None  # Link to source guide if applicable
    encoding: str = "utf-8"
    delimiter: Optional[str] = None  # For delimited files (,|\t|;|etc.)
    has_header: bool = True
    line_terminator: str = "\n"
    quote_char: Optional[str] = '"'
    escape_char: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None  # Spec/guide version
    sample_uri: Optional[str] = None  # Link to sample file if available
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            "id": self.id,
            "source_system_id": self.source_system_id,
            "spec_document_id": self.spec_document_id,
            "name": self.name,
            "file_type": self.file_type,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "has_header": self.has_header,
            "line_terminator": self.line_terminator,
            "quote_char": self.quote_char,
            "escape_char": self.escape_char,
            "description": self.description,
            "version": self.version,
            "sample_uri": self.sample_uri,
        }


@dataclass 
class FileField:
    """
    A field within a file specification.
    
    Represents a column (delimited) or field (fixed-width) in a file.
    Contains type information, position, and validation constraints.
    
    Per docs/FILE_INTEGRATION_V1_PLAN.md Section 5.1
    """
    id: Optional[int]
    file_spec_id: Optional[int]
    name: str  # Column/field name
    field_type: str  # FileFieldType enum value
    position: int  # 0-indexed column position (for delimited)
    start_position: Optional[int] = None  # For fixed-width: start byte (1-indexed)
    length: Optional[int] = None  # For fixed-width: field length
    format_mask: Optional[str] = None  # e.g., "YYYYMMDD", "###.##"
    nullable: bool = True
    default_value: Optional[str] = None
    validation_regex: Optional[str] = None
    description: Optional[str] = None
    sample_values: Optional[List[str]] = None  # Sample values for inference
    inference_confidence: float = 1.0  # How confident are we in this inference
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            "id": self.id,
            "file_spec_id": self.file_spec_id,
            "name": self.name,
            "field_type": self.field_type,
            "position": self.position,
            "start_position": self.start_position,
            "length": self.length,
            "format_mask": self.format_mask,
            "nullable": self.nullable,
            "default_value": self.default_value,
            "validation_regex": self.validation_regex,
            "description": self.description,
            "sample_values": self.sample_values,
            "inference_confidence": self.inference_confidence,
        }


@dataclass
class RecordLayout:
    """
    Record layout for fixed-width or multi-record files.
    
    Supports files with multiple record types (e.g., header, detail, trailer).
    Each record type can have different field layouts.
    
    Per docs/FILE_INTEGRATION_V1_PLAN.md Section 5.1
    """
    id: Optional[int]
    file_spec_id: Optional[int]
    record_type: str  # e.g., "header", "detail", "trailer", "H", "D", "T"
    identifier_field: Optional[str] = None  # Field name that identifies record type
    identifier_value: Optional[str] = None  # Value that identifies this type
    record_length: Optional[int] = None  # For fixed-width files
    position: int = 0  # Order in file (0=any position, 1=first, -1=last)
    min_occurrences: int = 0  # Minimum required occurrences
    max_occurrences: Optional[int] = None  # Maximum allowed (None = unlimited)
    description: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            "id": self.id,
            "file_spec_id": self.file_spec_id,
            "record_type": self.record_type,
            "identifier_field": self.identifier_field,
            "identifier_value": self.identifier_value,
            "record_length": self.record_length,
            "position": self.position,
            "min_occurrences": self.min_occurrences,
            "max_occurrences": self.max_occurrences,
            "description": self.description,
        }


@dataclass
class FileValidationRule:
    """
    Validation rule for file data.
    
    Can apply to a specific field or the entire file (field_name=None).
    Supports various rule types from simple required checks to complex
    cross-field validations.
    
    Per docs/FILE_INTEGRATION_V1_PLAN.md Section 5.1
    """
    id: Optional[int]
    file_spec_id: Optional[int]
    field_name: Optional[str] = None  # None means file-level rule
    rule_type: str = ValidationRuleType.REQUIRED  # ValidationRuleType enum value
    rule_config: Dict[str, Any] = None  # Rule-specific configuration
    error_message: Optional[str] = None
    severity: str = "error"  # "error", "warning", "info"
    
    def __post_init__(self):
        if self.rule_config is None:
            self.rule_config = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            "id": self.id,
            "file_spec_id": self.file_spec_id,
            "field_name": self.field_name,
            "rule_type": self.rule_type,
            "rule_config": self.rule_config,
            "error_message": self.error_message,
            "severity": self.severity,
        }


@dataclass
class FileFieldMapping:
    """
    Mapping between a file field and an entity field.
    
    Used to connect file fields to domain entities for transformation
    and data loading purposes.
    """
    id: Optional[int]
    file_field_id: Optional[int]
    entity_id: Optional[int]
    entity_field_name: str
    transform_expression: Optional[str] = None  # e.g., "UPPER(value)", "DATE(value, '%Y%m%d')"
    description: Optional[str] = None


@dataclass
class SpecChunkEmbedding:
    """Vector embedding of a spec document chunk for semantic search."""
    id: Optional[int]
    spec_document_id: int
    chunk_index: int
    content: str
    embedding: Optional[list] = None  # Will be list of floats (1536 dim for OpenAI)


# ============================================================================
# Workflow Node Type Constants
# ============================================================================

class NodeType:
    """
    Constants for IntegrationFlowNode.node_type values.
    
    Use these instead of string literals to prevent drift between
    modules. When adding new node types, add them here first.
    
    Usage:
        node = IntegrationFlowNode(node_type=NodeType.API_CALL, ...)
        if node.node_type == NodeType.PARSE_FILE: ...
    """
    START = "start"
    END = "end"
    API_CALL = "api_call"
    PARSE_FILE = "parse_file"  # File parsing operations (CSV, Excel, fixed-width)
    TRANSFORM = "transform"
    VALIDATION = "validation"
    DECISION = "decision"


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


# ============================================================================
# Knowledge Graph Models (for GraphRAG)
# ============================================================================

class KGNodeType(str, Enum):
    """Types of nodes in the knowledge graph."""
    PROVIDER = "provider"
    ENTITY = "entity"
    ENDPOINT = "endpoint"
    WORKFLOW_TEMPLATE = "workflow_template"
    TASK = "task"
    # Pattern nodes are provider-agnostic workflow patterns
    # e.g., "pattern.crud_create", "pattern.list_pagination"
    PATTERN = "pattern"
    # File integration nodes
    FILE_SPEC = "file_spec"
    FILE_FIELD = "file_field"
    GUIDE_FIELD = "guide_field"
    RECORD_LAYOUT = "record_layout"
    FILE_PATTERN = "file_pattern"  # e.g., "pattern.file.header_detail_trailer"


class KGEdgeRelation(str, Enum):
    """Types of relationships between KG nodes."""
    USES_ENDPOINT = "uses_endpoint"
    PRODUCES_ENTITY = "produces_entity"
    CONSUMES_ENTITY = "consumes_entity"
    SIMILAR_TO = "similar_to"
    COMPOSED_OF = "composed_of"
    PRECEDES = "precedes"
    BELONGS_TO_PROVIDER = "belongs_to_provider"
    # Pattern relationships
    IMPLEMENTS_PATTERN = "implements_pattern"  # workflow_template -> pattern
    DERIVED_FROM = "derived_from"  # pattern -> workflow_template (learning)
    # File integration relationships
    HAS_FIELD = "has_field"           # file_spec -> file_field
    MAPS_TO = "maps_to"               # file_field -> entity_field
    VALIDATED_BY = "validated_by"     # file_field -> validation_rule
    DERIVES_FROM_GUIDE = "derives_from_guide"  # file_spec -> pdf_guide (provenance)
    FILE_FLOWS_TO = "file_flows_to"   # file_spec -> endpoint (ETL target)


@dataclass
class KGNode:
    """A node in the knowledge graph."""
    id: Optional[int]
    node_type: str  # See KGNodeType
    provider_code: Optional[str]
    key: str  # Unique key like "stripe.create_checkout_session"
    name: str
    description: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None  # Flexible properties (e.g., steps for templates)
    embedding: Optional[list] = None  # Vector embedding (1536 dim)
    confidence_score: float = 1.0
    usage_count: int = 0
    source_run_id: Optional[str] = None


@dataclass
class KGEdge:
    """An edge (relationship) between two KG nodes."""
    id: Optional[int]
    src_node_id: int
    dst_node_id: int
    relation_type: str  # See KGEdgeRelation
    weight: float = 1.0
    properties: Optional[Dict[str, Any]] = None
    source_run_id: Optional[str] = None


@dataclass
class KGWorkflowStep:
    """A step within a workflow template in the KG."""
    id: Optional[int]
    template_id: Optional[str]  # Template key like "template.stripe.create_checkout"
    step_key: str
    step_type: str  # 'start', 'validation', 'api_call', 'transform', 'end'
    position: int
    label: Optional[str] = None
    description: Optional[str] = None
    config: Optional[Dict[str, Any]] = None
    endpoint_path: Optional[str] = None  # For api_call steps


@dataclass
class KGStepBinding:
    """Binding from a workflow step to an endpoint."""
    id: Optional[int]
    step_id: int
    endpoint_node_id: Optional[int] = None
    endpoint_path: Optional[str] = None
    endpoint_method: Optional[str] = None
    request_mapping: Optional[Dict[str, Any]] = None
    response_mapping: Optional[Dict[str, Any]] = None


@dataclass
class KGWorkflowTemplate:
    """
    A workflow template from the Knowledge Graph.
    
    This is the domain model returned by GraphRAG queries.
    Contains the template metadata and its steps.
    """
    template_id: str
    name: str
    description: Optional[str] = None
    provider_code: Optional[str] = None
    task_type: Optional[str] = None
    steps: Optional[List["KGWorkflowStep"]] = None
    metadata: Optional[Dict[str, Any]] = None


# ============================================================================
# Feedback and Learning Models
# ============================================================================

class FeedbackType(str, Enum):
    """Types of feedback that can be recorded for KG learning."""
    THUMBS = "thumbs"  # Binary: 0 or 1 (thumbs down/up)
    SCORE = "score"  # Numeric: 0.0 to 1.0
    AUTO_COMPILE = "auto_compile"  # 0 (fail) or 1 (success)
    AUTO_TEST = "auto_test"  # 0 (fail) or 1 (success)
    AUTO_LINT = "auto_lint"  # 0-1 based on lint score


class FeedbackSource(str, Enum):
    """Source of feedback for attribution."""
    LANGSMITH = "langsmith"  # Synced from LangSmith feedback API
    CLI = "cli"  # Submitted via CLI command
    AUTO = "auto"  # Automatic signals (compile, test, lint)
    API = "api"  # Submitted via API


@dataclass
class FeedbackRecord:
    """
    A feedback record for quality-based KG learning.
    
    Links run outcomes to template quality scores, enabling the KG to
    learn which templates produce good outputs vs bad outputs.
    
    Per docs/decisions/FEEDBACK_LEARNING_IMPLEMENTATION.md
    """
    id: Optional[int] = None
    run_id: str = ""  # Our run_id (maps to LangSmith trace_id)
    template_key: Optional[str] = None  # kg.nodes key for template used
    pattern_key: Optional[str] = None  # kg.nodes key for pattern used
    feedback_type: FeedbackType = FeedbackType.THUMBS
    score: float = 0.0  # Normalized 0-1
    comment: Optional[str] = None
    source: FeedbackSource = FeedbackSource.LANGSMITH
    langsmith_feedback_id: Optional[str] = None  # Original LangSmith ID
    created_at: Optional[str] = None
    synced_at: Optional[str] = None


@dataclass
class ConfidenceUpdate:
    """
    Tracks a confidence score update for audit trail.
    
    Stored in kg.confidence_history for analysis of how
    templates improve or degrade over time.
    """
    id: Optional[int] = None
    node_key: str = ""
    old_confidence: Optional[float] = None
    new_confidence: float = 0.5
    feedback_count: int = 0
    reason: Optional[str] = None
    created_at: Optional[str] = None
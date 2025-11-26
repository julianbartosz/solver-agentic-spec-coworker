 
Appendix A — Core Python Domain Models
A.1 Overview
This appendix defines the concrete Python domain models used by the Agentic API Integration Designer & Code Generator.

These models:
•	Map 1:1 to the Silver (spec_silver) and Gold (integration_gold) schemas, as well as repo-integration types
•	Provide the in-memory types passed through LangGraph nodes
•	Do not perform any database I/O
•	Use field names and types that match the Postgres DDL (for example BIGINT maps to int | None in Python before persistence)
•	Follow a uniform ID convention:
o	All id fields and corresponding foreign keys are Optional[int]
o	Before persistence nodes run, ids may be None
o	The persistence checkpoints are responsible for:
	Upserting rows
	Filling the corresponding Python object id fields
	Updating foreign key fields (for example schema_id, endpoint_id) to match database ids

The persistence checkpoints are:
•	persist_silver_checkpoint for Silver-level tables and spec_documents
•	persist_gold_checkpoint for Gold-level tables
•	persist_run_outcome for run_status, RAG metrics, KG learning rows, and repo_meta tables

All models below reside in:
•	domain/models.py
•	repo/models.py
•	api/types.py

and are imported into the LangGraph nodes and persistence layer.
 
A.2 Domain Model Definitions
File: domain/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class PolicyType(str, Enum):
    AUTH = "auth"
    RETRY = "retry"
    PAGINATION = "pagination"
    LOGGING = "logging"
    RATE_LIMIT = "rate_limit"
    IDEMPOTENCY = "idempotency"

 

A.2.1 Silver-Layer Domain Models
@dataclass
class SourceSystem:
    """
    In-memory representation of spec_silver.source_systems.

    ID semantics:
    - id is None before persistence.
    - persist_silver_checkpoint sets id after upsert.
    """
    id: Optional[int]
    code: str
    display_name: str
    base_url: Optional[str] = None


@dataclass
class SpecDocument:
    """
    In-memory representation of spec_silver.spec_documents.

    ID semantics:
    - id is None before persistence.
    - spec_document_id foreign keys in other tables refer to this id after persistence.
    """
    id: Optional[int]
    source_system_id: Optional[int]
    version: str
    uri: str
    content_type: str
    sha256: str


@dataclass
class SpecSection:
    """
    In-memory representation of spec_silver.spec_sections.

    Sections group related parts of a specification, such as "Authentication"
    or "Customer APIs", with stable offsets into the original document.

    ID semantics:
    - id is None before persistence.
    - spec_document_id links to the owning SpecDocument.
    - persist_silver_checkpoint assigns id and spec_document_id and upserts the row.

    In v1 every SpecSection object in WorkflowState is written to spec_silver.spec_sections and can be queried later for section-level retrieval and diagnostics.
    """
    id: Optional[int]
    spec_document_id: Optional[int]
    section_type: str
    title: Optional[str]
    path: Optional[str]
    start_offset: Optional[int]
    end_offset: Optional[int]
    content: str


@dataclass
class EndpointParameter:
    """
    In-memory representation of spec_silver.endpoint_parameters.

    ID semantics:
    - id and endpoint_id may be None before persistence.
    - persist_silver_checkpoint assigns ids and endpoint_id based on the owning Endpoint.
    """
    id: Optional[int]
    endpoint_id: Optional[int]
    name: str
    location: Literal["path", "query", "header", "cookie", "body"]
    required: bool
    schema_ref: Optional[str] = None
    description: Optional[str] = None


@dataclass
class SchemaField:
    """
    In-memory representation of spec_silver.fields.
    """
    id: Optional[int]
    schema_id: Optional[int]
    name: str
    json_path: str
    type: str
    format: Optional[str] = None
    required: bool = False
    description: Optional[str] = None


@dataclass
class Schema:
    """
    In-memory representation of spec_silver.schemas (includes fields).

    ID semantics:
    - id is None before persistence.
    - fields[].schema_id is populated by persist_silver_checkpoint.
    """
    id: Optional[int]
    source_system_id: Optional[int]
    name: str
    ref: Optional[str]
    description: Optional[str] = None
    fields: List[SchemaField] = field(default_factory=list)


@dataclass
class Entity:
    """
    In-memory representation of spec_silver.entities.
    """
    id: Optional[int]
    source_system_id: Optional[int]
    name: str
    schema_id: Optional[int]
    description: Optional[str] = None


@dataclass
class EntityRelationship:
    """
    In-memory representation of spec_silver.entity_relationships.
    """
    id: Optional[int]
    source_system_id: Optional[int]
    from_entity_id: Optional[int]
    to_entity_id: Optional[int]
    relationship_type: str
    description: Optional[str] = None


@dataclass
class Event:
    """
    In-memory representation of spec_silver.events.
    """
    id: Optional[int]
    source_system_id: Optional[int]
    name: str
    description: Optional[str] = None
    payload_schema_id: Optional[int] = None
    entity_id: Optional[int] = None


@dataclass
class FileSpecDocument:
    """
    In-memory representation of a non-HTTP specification, such as CSV or EDI.

    Rows are stored in spec_silver.file_specs and link back to source_systems.
    """
    id: Optional[int]
    source_system_id: Optional[int]
    uri: str
    format: str  # e.g. "csv", "edi"
    sha256: str


@dataclass
class Endpoint:
    """
    In-memory representation of spec_silver.endpoints (includes parameters).
    """
    id: Optional[int]
    source_system_id: Optional[int]
    spec_document_id: Optional[int]
    path: str
    method: HttpMethod
    operation_id: Optional[str]
    summary: Optional[str]
    description: Optional[str]
    request_schema_id: Optional[int]
    response_schema_id: Optional[int]
    auth_required: bool
    pagination_style: Optional[str] = None
    rate_limit_bucket: Optional[str] = None
    parameters: List[EndpointParameter] = field(default_factory=list)

 

A.2.2 Gold-Layer Domain Models
@dataclass
class WorkflowTemplate:
    """
    In-memory representation of integration_gold.workflow_templates.
    """
    id: Optional[int]
    source_system_id: Optional[int]
    code: str
    name: str
    description: Optional[str] = None


@dataclass
class IntegrationTask:
    """
    In-memory representation of integration_gold.integration_tasks.
    
    constraints: Optional dict with user-provided hints. Known keys:
        - business_domain (str): e.g., "payments", "subscriptions".
          Used by align_task_with_kg to filter workflow templates.
          If omitted, LLM infers domain from task description.
    """
    id: Optional[int]
    provider_code: str
    task_slug: str
    description: str
    source_system_id: Optional[int] = None
    target_spec_document_id: Optional[int] = None
    input_entities: List[str] = field(default_factory=list)
    output_entities: List[str] = field(default_factory=list)
    constraints: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrationFlowNode:
    """
    In-memory representation of integration_gold.integration_flow_nodes.
    """
    id: Optional[int]
    task_id: Optional[int]
    node_key: str
    node_type: str
    endpoint_id: Optional[int]
    entity_id: Optional[int]
    position: int
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class IntegrationFlowEdge:
    """
    In-memory representation of integration_gold.integration_flow_edges.
    """
    id: Optional[int]
    task_id: Optional[int]
    from_node_key: str
    to_node_key: str
    condition: Optional[str] = None


@dataclass
class EndpointBinding:
    """
    In-memory representation of integration_gold.endpoint_bindings.
    """
    id: Optional[int]
    task_id: Optional[int]
    flow_node_key: str
    endpoint_id: Optional[int]
    request_mapping: Dict[str, str]
    response_mapping: Dict[str, str]


@dataclass
class Policy:
    """
    In-memory representation of integration_gold.policies.
    """
    id: Optional[int]
    task_id: Optional[int]
    policy_type: PolicyType
    scope: str
    scope_ref: Optional[str]
    config: Dict[str, Any]


@dataclass
class CodeArtifact:
    """
    In-memory representation of integration_gold.code_artifacts.
    """
    id: Optional[int]
    task_id: Optional[int]
    artifact_type: str
    rel_path: str
    language: str
    module_name: Optional[str]
    content: str
    sha256: Optional[str] = None

 

A.2.3 Embedding Models
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class SpecChunkEmbedding:
    """
    In-memory representation of a spec_silver.spec_chunks row
    before persistence.

    The association between chunk_index and doc_chunks is:
    - chunk_index is the 0-based index in WorkflowState.doc_chunks, which holds chunks from all spec_documents in a stable global order.
    - plan["chunk_index_to_spec_document_uri"] records which spec_document each index belongs to.
    - spec_document_id is None before persistence and set by persist_silver_checkpoint using this mapping.
    - embedding is a dense vector with EMBEDDING_DIM components.
    """
    id: Optional[int]
    spec_document_id: Optional[int]
    chunk_index: int
    content: str
    embedding: List[float] 
A.2.4 API Result Types
File: api/types.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class IntegrationOptions:
    """
    Optional configuration flags that influence a single run.

    All fields are optional and have safe defaults for v1.

    Semantics:
    - repo_integration_enabled:
        * If False, the graph MUST skip analyze_repo_layout and
          apply_repo_integration_changes even if repo_root is set.
    - dry_run:
        * If True, the persistence checkpoints (persist_silver_checkpoint,
          persist_gold_checkpoint, persist_run_outcome) MUST NOT write to any
          database tables, including run_status and KG learning tables; all
          persistence stays in memory only.
    - override_provider_code:
        * If set, this value (normalized) is used as provider_code
          instead of any inferred provider.
    - override_task_slug:
        * If set, this value (normalized) is used as the IntegrationTask
          task_slug instead of the LLM-derived slug.
    """
    repo_integration_enabled: bool = True
    dry_run: bool = False
    override_provider_code: Optional[str] = None
    override_task_slug: Optional[str] = None

 

A.3 OpenAPI →  Silver Mapping Rules
The build_silver_api_model node MUST apply the following deterministic rules when an OpenAPI/Swagger spec is available.
A.3.1 Schemas (spec_silver.schemas + fields)
•	For any OpenAPI document with components.schemas:
o	Create one Schema per entry components.schemas[<schema_name>]:
	Schema.name = <schema_name>
	Schema.ref = "#/components/schemas/<schema_name>"
	Schema.description = OpenAPI schema.description if present, else NULL.
o	Inline schemas that are only referenced via $ref MUST NOT create separate Schema rows; only the target of the $ref is modeled as a Schema.
•	For each JSON field in a schema (including nested objects/arrays):
o	SchemaField.name: the property’s local name.
o	SchemaField.json_path: dot-notation path from the schema root:
	Example object field: customer.address.line1
	Example array field: items[].id (use [] for array elements).
o	SchemaField.type: OpenAPI type (e.g. string, integer). If missing:
	If format implies a type (e.g. date-time → string), infer it.
	Else default to "string".
o	SchemaField.format: OpenAPI format if present, else NULL.
o	SchemaField.required: TRUE if the field is listed in the containing object’s required array, otherwise FALSE.
A.3.2 Entities (spec_silver.entities)
•	Treat all components.schemas as candidates, then mark a subset as entities using these deterministic heuristics:
A schema becomes an Entity if any of the following hold:
1.	It is used as a request or response body in ≥ 3 distinct endpoints.
2.	It has a top-level field named id (case-insensitive) with type string or integer.
3.	It appears in a curated allowlist per provider (e.g. Stripe: Customer, Charge, Subscription, Invoice, etc.), configured in code as a static mapping.
•	Mapping:
o	Entity.name = Schema.name
o	Entity.schema_id = Schema.id
o	Entity.description:
	If Schema.description is present, start from that text.
	Then call a small extraction prompt that reads the schema fields and the surrounding spec_sections and writes a short summary (two or three sentences) in plain language.
	The summary must describe the main purpose of the entity and the key identifiers.
The extraction prompt runs with a fixed token budget and a fixed output schema and records any parsing failure in state.errors. The description is stored in spec_silver.entities.description.
A.3.3 Entity Relationships (spec_silver.entity_relationships)
Infer relationships only from fields that clearly reference other entities:
•	If Entity A has a field with type object whose $ref points to a schema that is an Entity B:
o	Create EntityRelationship with:
	from_entity_id = A.id
	to_entity_id = B.id
	relationship_type = "embeds"
•	If Entity A has an array field of objects with $ref to Entity B:
o	relationship_type = "has_many"
•	If Entity A has a top-level scalar field named <other_entity_name>_id where <other_entity_name> matches the name of an entity (case-insensitive):
o	relationship_type = "references"
A.3.4 Events (spec_silver.events)
v1 models events from three sources:
1.	OpenAPI callbacks sections.
2.	Paths or operations marked as webhooks:
	•	Path contains "/webhooks" or "/events", or
	•	Operation has a tag "webhook" or "event".
3.	Paths or operations with event-related tags matching a provider-specific allowlist (e.g., "notification", "subscription", "push").

Each unique webhook/callback operation becomes an Event:
•	Event.name: operationId if present, else a slug of "<METHOD> <PATH>" (uppercased method, original path, then lowercased and converted to snake_case).
•	Event.payload_schema_id: the schema id of the webhook request body (if any).
•	Event.entity_id: if the webhook payload schema maps to an existing Entity (by schema_id), set to that entity’s id; else NULL.
A.3.5 Endpoints (spec_silver.endpoints)
For every paths[<path>][<method>] operation:
•	Endpoint.path = <path> exactly as in OpenAPI.
•	Endpoint.method = <METHOD> uppercased.
•	Endpoint.operation_id = operationId if present, else NULL.
•	Endpoint.summary / Endpoint.description: copied directly from the operation (or NULL if missing).
•	Endpoint.request_schema_id:
o	If there is a request body, map its schema to an existing Schema.id.
o	Else NULL.
•	Endpoint.response_schema_id:
o	Use the primary success response:
	First pick 2xx responses, preferring 200, then 201, then smallest remaining 2xx.
	Map the body schema to an existing Schema.id.
o	Else NULL.
•	Endpoint.auth_required:
o	TRUE if:
	Operation-level security is non-empty, OR
	Global security is non-empty and the operation does not explicitly override it with an empty list.
o	FALSE otherwise.
•	Endpoint.pagination_style (string enum or NULL):
o	"cursor" if either:
	Response schema contains fields like next_cursor, starting_after, ending_before, cursor, OR
	Response schema contains an object page_info with cursor-like fields.
o	"offset" if query parameters include page or offset (with or without limit).
o	"page_size" if query parameters include page and either per_page or page_size.
o	NULL otherwise.
•	Endpoint.rate_limit_bucket:
o	If operation or path contains a vendor extension x-rate-limit-bucket, set to that value.
o	Else NULL in v1.
A.3.6 Endpoint Parameters (spec_silver.endpoint_parameters)
For each endpoint:
•	Combine path-level and operation-level parameters:
o	Operation-level parameters override path-level ones with the same (name, in) combination.
•	Map fields:
o	EndpointParameter.location = in ("path", "query", "header", "cookie", "body").
o	EndpointParameter.required = required from OpenAPI (default FALSE).
o	EndpointParameter.schema_ref:
	If the parameter uses $ref, store the $ref string.
	Else NULL for v1.
o	EndpointParameter.description = description from OpenAPI (if any).
A.3.7 Text-Only Specs (HTML/Markdown/PDF)
When detect_and_parse_spec does not detect an OpenAPI document (openapi_spec is None), build_silver_api_model MUST perform a best-effort extraction of Silver drafts from doc_chunks using the rules below.
A.3.7.1 Endpoint Detection
• A “candidate endpoint” is any line matching:
•	^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+/[^\s]+
• Each candidate endpoint creates an Endpoint:
•	Endpoint.path: the path portion (/v1/customers, /api/charges, etc.) exactly as parsed.
•	Endpoint.method: the HTTP method, uppercased.
•	Endpoint.summary: first non-empty line of text following the candidate line, up to a blank line or next heading.
•	Endpoint.description: subsequent lines in the same section up to:
o	The next endpoint candidate, or
o	The next top-level heading (e.g. markdown #/##), whichever comes first.
•	Endpoint.auth_required:
o	TRUE if the section contains phrases like “API key”, “Authorization header”, “Bearer token”.
o	FALSE otherwise.
•	Endpoint.request_schema_id / Endpoint.response_schema_id:
o	Linked via A.3.7.2 schema inference; may be NULL if no JSON example is detected.
If no candidate endpoints are found, build_silver_api_model MUST:
• Leave endpoints empty, and
• Append a human-readable warning to state.errors.
A.3.7.2 Schema and Field Inference From Examples
For each endpoint section:
• Search for fenced code blocks (markdown or similar) labeled as JSON (e.g.json) or that parse successfully as JSON.
• For each JSON object example:
•	Create a Schema with:
o	Schema.name: a slug based on <METHOD>_<PATH>_<request|response>, normalized to [a-z0-9_]+.
o	Schema.ref: NULL (text-only specs do not use $ref).
•	For each field in the JSON, recursively:
o	SchemaField.name: property’s local name.
o	SchemaField.json_path: dot-notation with [] for arrays (items[].id).
o	SchemaField.type: inferred from JSON value type:
	string, number, boolean, object, array.
o	SchemaField.format: NULL.
o	SchemaField.required: TRUE for fields present in the example.
• The first JSON example immediately preceding text like “Request body” or “Request” SHOULD be treated as the request schema; the first near “Response body” or “Response” as the response schema.
• Map these to Endpoint.request_schema_id / Endpoint.response_schema_id accordingly.
If parsing fails for a block that appears to be JSON, build_silver_api_model MUST log a warning in state.errorsand continue.
A.3.7.3 Entity Detection in Text-Only Specs
In addition to A.3.2, for text-only specs build_silver_api_model MUST also:
• Treat headings ending in “object” (case-insensitive), e.g. “Customer object”, “Invoice Object”, as entity candidates.
• If a JSON example or a table of fields follows such a heading:
•	Create a Schema (if not already created via A.3.7.2).
•	Create an Entity with:
o	Entity.name: the base name (for example "Customer", "Invoice").
o	Entity.schema_id: the associated Schema.id.
o	Entity.description: a short summary derived from the heading, the first descriptive paragraph, and any field table or JSON example in the section. The extraction prompt follows the same pattern as A.3.2 and writes a compact description that mentions the main identifiers and relationships.
A.3.7.4 Relationships
Entity relationships are inferred using the same rules as A.3.3, but applied to any inferred schemas from JSON examples:
• Object-valued fields referencing another entity’s schema → relationship_type="embeds".
• Array of objects referencing another entity’s schema → relationship_type="has_many".
• Top-level scalar <other_entity_name>_id fields → relationship_type="references".
A.3.7.5 Partial Silver and Reporting
For text-only specs, build_silver_api_model may not find full schema information for every endpoint. It must still create a consistent Silver surface:
•	For each detected endpoint, create an Endpoint row.
•	For each endpoint without a JSON example, create placeholder request and response Schema rows with type metadata when it can infer basic shapes from prose.
•	For entities without clear relationships, create Entity rows and leave relationships empty.

The node writes a structured summary for missing fields and relationships into state.errors and into a machine-readable diagnostics object that is persisted with the run. The summary includes counts of endpoints without schemas, entities without relationships, and failed JSON parses. This gives downstream nodes a stable view of what is missing and lets future runs reprocess the same spec with improved rules.
 
Appendix B — Database Schemas (DDL)
B.1 Overview
This appendix defines the authoritative relational schemas for:
•	Silver layer (spec_silver)
•	Gold layer (integration_gold)
•	Vector chunks (spec_silver.spec_chunks)
•	Run status tracking (integration_gold.run_status)
All uniqueness keys defined here must be used by the persistence checkpoints (persist_silver_checkpoint and persist_gold_checkpoint).
All tables follow Postgres 15 conventions and use BIGSERIAL / BIGINT for primary keys.
 
B.1.1 Authoritative Upsert Keys
Silver Layer
Table	Upsert Key
source_systems	(code)
spec_documents	(source_system_id, sha256)
spec_sections	(spec_document_id, section_type, title, path, start_offset)
schemas	(source_system_id, name)
fields	(schema_id, json_path)
entities	(source_system_id, name)
entity_relationships	(source_system_id, from_entity_id, to_entity_id, relationship_type)
events	(source_system_id, name)
endpoints	(source_system_id, spec_document_id, path, method)
endpoint_parameters	(endpoint_id, name, location)
spec_chunks	(spec_document_id, chunk_index)
 
Gold Layer
Table	Upsert Key
integration_tasks	(provider_code, task_slug)
workflow_templates	(source_system_id, code)
integration_flow_nodes	(task_id, node_key)
integration_flow_edges	(task_id, from_node_key, to_node_key)
endpoint_bindings	(task_id, flow_node_key, endpoint_id)
policies	(task_id, policy_type, scope, scope_ref)
code_artifacts	(task_id, rel_path, artifact_type)
 
Run Status
Table	Key
run_status	run_id (unique)
 
B.2 Silver Schema (DDL)
The Silver schema models the normalized view of all supported sources for a provider. It covers:
•	HTTP APIs described by OpenAPI or structured HTML/Markdown/PDF specs
•	File-based schemas such as CSV, fixed-width, or EDI layouts
•	Message-based or event-driven interfaces that describe topics, queues, and payloads

For HTTP sources, build_silver_api_model applies the OpenAPI and text-only mapping rules from Appendix A.3 and writes into the core tables: spec_documents, schemas, fields, entities, entity_relationships, events, endpoints, and endpoint_parameters.

Additional Silver tables cover non-HTTP source types:
•	file_specs for CSV and EDI schema metadata
•	message_specs for message-based or event-driven interfaces

These tables follow the same primary key and foreign key patterns as the HTTP tables, keyed by source_system_id and a logical name or hash. The ingestion and extraction path for non-HTTP specs is:
•	ingest_spec reads the spec documents into spec_documents and doc_chunks
•	build_silver_api_model detects file-based and message-based schema formats and constructs Schema, SchemaField, Entity, Event, and the corresponding file_specs or message_specs rows
•	persist_silver_checkpoint writes all of these tables in one transaction and backfills ids and foreign keys on the in-memory objects

Text-only guides for CSV/EDI or message interfaces are treated as partial sources. The node still writes file_specs or message_specs rows when it can infer stable names and basic payload shapes, and records missing detail in diagnostics fields for later refinement.

Below is the complete, reformatted Silver schema.
Schema Definition
CREATE SCHEMA IF NOT EXISTS spec_silver;
 
source_systems
CREATE TABLE spec_silver.source_systems (
    id           BIGSERIAL PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    base_url     TEXT
);
 
spec_documents
CREATE TABLE spec_silver.spec_documents (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    version          TEXT NOT NULL,
    uri              TEXT NOT NULL,
    content_type     TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    UNIQUE (source_system_id, sha256)
);
 
spec_sections
CREATE TABLE spec_silver.spec_sections (
    id               BIGSERIAL PRIMARY KEY,
    spec_document_id BIGINT NOT NULL REFERENCES spec_silver.spec_documents(id) ON DELETE CASCADE,
    section_type     TEXT NOT NULL,
    title            TEXT,
    path             TEXT,
    start_offset     INT,
    end_offset       INT,
    content          TEXT NOT NULL
);
 
schemas
CREATE TABLE spec_silver.schemas (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    ref              TEXT,
    description      TEXT,
    UNIQUE (source_system_id, name)
);
 
fields
CREATE TABLE spec_silver.fields (
    id         BIGSERIAL PRIMARY KEY,
    schema_id  BIGINT NOT NULL REFERENCES spec_silver.schemas(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    json_path  TEXT NOT NULL,
    type       TEXT NOT NULL,
    format     TEXT,
    required   BOOLEAN NOT NULL DEFAULT FALSE,
    description TEXT,
    UNIQUE (schema_id, json_path)
);
 
entities
CREATE TABLE spec_silver.entities (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    schema_id        BIGINT REFERENCES spec_silver.schemas(id),
    description      TEXT,
    UNIQUE (source_system_id, name)
);
 
entity_relationships
CREATE TABLE spec_silver.entity_relationships (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    from_entity_id   BIGINT NOT NULL REFERENCES spec_silver.entities(id) ON DELETE CASCADE,
    to_entity_id     BIGINT NOT NULL REFERENCES spec_silver.entities(id) ON DELETE CASCADE,
    relationship_type TEXT NOT NULL,
    description       TEXT,
    UNIQUE (source_system_id, from_entity_id, to_entity_id, relationship_type)
);
 
events
CREATE TABLE spec_silver.events (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    description      TEXT,
    payload_schema_id BIGINT REFERENCES spec_silver.schemas(id),
    entity_id        BIGINT REFERENCES spec_silver.entities(id),
    UNIQUE (source_system_id, name)
);
 
endpoints
CREATE TABLE spec_silver.endpoints (
    id                BIGSERIAL PRIMARY KEY,
    source_system_id  BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    spec_document_id  BIGINT NOT NULL REFERENCES spec_silver.spec_documents(id) ON DELETE CASCADE,
    path              TEXT NOT NULL,
    method            TEXT NOT NULL,
    operation_id      TEXT,
    summary           TEXT,
    description       TEXT,
    request_schema_id BIGINT REFERENCES spec_silver.schemas(id),
    response_schema_id BIGINT REFERENCES spec_silver.schemas(id),
    auth_required     BOOLEAN NOT NULL DEFAULT FALSE,
    pagination_style  TEXT,
    rate_limit_bucket TEXT,
    UNIQUE (source_system_id, spec_document_id, path, method)
);
 
endpoint_parameters
CREATE TABLE spec_silver.endpoint_parameters (
    id          BIGSERIAL PRIMARY KEY,
    endpoint_id BIGINT NOT NULL REFERENCES spec_silver.endpoints(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    location    TEXT NOT NULL,
    required    BOOLEAN NOT NULL DEFAULT FALSE,
    schema_ref  TEXT,
    description TEXT,
    UNIQUE (endpoint_id, name, location)
);
 
spec_chunks (vector storage)
CREATE TABLE spec_silver.spec_chunks (
    id               BIGSERIAL PRIMARY KEY,
    spec_document_id BIGINT NOT NULL REFERENCES spec_silver.spec_documents(id) ON DELETE CASCADE,
    chunk_index      INT NOT NULL,
    content          TEXT NOT NULL,
    embedding        VECTOR(1536),
    UNIQUE (spec_document_id, chunk_index)
);
 
file_specs (CSV/EDI metadata)
CREATE TABLE spec_silver.file_specs (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    file_type        TEXT NOT NULL,  -- 'csv', 'edi', 'fixed-width', etc.
    delimiter        TEXT,
    encoding         TEXT DEFAULT 'utf-8',
    header_row       BOOLEAN DEFAULT TRUE,
    schema_id        BIGINT REFERENCES spec_silver.schemas(id),
    description      TEXT,
    UNIQUE (source_system_id, name)
);
 
message_specs (event/message interfaces)
CREATE TABLE spec_silver.message_specs (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id) ON DELETE CASCADE,
    name             TEXT NOT NULL,
    protocol         TEXT NOT NULL,  -- 'amqp', 'kafka', 'sqs', 'pubsub', etc.
    topic_pattern    TEXT,
    payload_schema_id BIGINT REFERENCES spec_silver.schemas(id),
    direction        TEXT NOT NULL DEFAULT 'inbound',  -- 'inbound', 'outbound', 'bidirectional'
    description      TEXT,
    UNIQUE (source_system_id, name)
);
 
B.3 Gold Schema (DDL)
Schema Definition
CREATE SCHEMA IF NOT EXISTS integration_gold;
 
integration_tasks
CREATE TABLE integration_gold.integration_tasks (
    id                     BIGSERIAL PRIMARY KEY,
    provider_code          TEXT NOT NULL,
    task_slug              TEXT NOT NULL,
    description            TEXT NOT NULL,
    source_system_id       BIGINT REFERENCES spec_silver.source_systems(id),
    target_spec_document_id BIGINT REFERENCES spec_silver.spec_documents(id),
    input_entities         JSONB NOT NULL DEFAULT '[]',
    output_entities        JSONB NOT NULL DEFAULT '[]',
    constraints_json       JSONB NOT NULL DEFAULT '{}',
    UNIQUE (provider_code, task_slug)
);
 
workflow_templates
CREATE TABLE integration_gold.workflow_templates (
    id               BIGSERIAL PRIMARY KEY,
    source_system_id BIGINT NOT NULL REFERENCES spec_silver.source_systems(id),
    code             TEXT NOT NULL,
    name             TEXT NOT NULL,
    description      TEXT,
    UNIQUE (source_system_id, code)
);
 
integration_flow_nodes
CREATE TABLE integration_gold.integration_flow_nodes (
    id          BIGSERIAL PRIMARY KEY,
    task_id     BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    node_key    TEXT NOT NULL,
    node_type   TEXT NOT NULL,
    endpoint_id BIGINT REFERENCES spec_silver.endpoints(id),
    entity_id   BIGINT REFERENCES spec_silver.entities(id),
    position    INT NOT NULL,
    config      JSONB NOT NULL DEFAULT '{}',
    UNIQUE (task_id, node_key)
);
 
integration_flow_edges
CREATE TABLE integration_gold.integration_flow_edges (
    id            BIGSERIAL PRIMARY KEY,
    task_id       BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    from_node_key TEXT NOT NULL,
    to_node_key   TEXT NOT NULL,
    condition     TEXT,
    UNIQUE (task_id, from_node_key, to_node_key)
);
 
endpoint_bindings
CREATE TABLE integration_gold.endpoint_bindings (
    id             BIGSERIAL PRIMARY KEY,
    task_id        BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    flow_node_key  TEXT NOT NULL,
    endpoint_id    BIGINT NOT NULL REFERENCES spec_silver.endpoints(id),
    request_mapping  JSONB NOT NULL,
    response_mapping JSONB NOT NULL,
    UNIQUE (task_id, flow_node_key, endpoint_id)
);
 
policies
CREATE TABLE integration_gold.policies (
    id         BIGSERIAL PRIMARY KEY,
    task_id    BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    policy_type TEXT NOT NULL,
    scope       TEXT NOT NULL,
    scope_ref   TEXT,
    config      JSONB NOT NULL,
    UNIQUE (task_id, policy_type, scope, scope_ref)
);
 
code_artifacts
CREATE TABLE integration_gold.code_artifacts (
    id          BIGSERIAL PRIMARY KEY,
    task_id     BIGINT NOT NULL REFERENCES integration_gold.integration_tasks(id) ON DELETE CASCADE,
    artifact_type TEXT NOT NULL,
    rel_path    TEXT NOT NULL,
    language    TEXT NOT NULL,
    module_name TEXT,
    content     TEXT NOT NULL,
    sha256      TEXT,
    UNIQUE (task_id, rel_path, artifact_type)
);
 
run_status
CREATE TABLE integration_gold.run_status (
    run_id          TEXT PRIMARY KEY,
    task_id         BIGINT REFERENCES integration_gold.integration_tasks(id),
    status          TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    error_summary   TEXT,
    langsmith_run_id TEXT
);
 
rag_eval_metrics
```sql
CREATE TABLE integration_gold.rag_eval_metrics (
    id              BIGSERIAL PRIMARY KEY,
    run_id          TEXT NOT NULL REFERENCES integration_gold.run_status(run_id) ON DELETE CASCADE,
    provider_code   TEXT NOT NULL,
    task_slug       TEXT NOT NULL,
    node_name       TEXT NOT NULL,
    metric_scope    TEXT NOT NULL, -- e.g., retrieval, planning, codegen
    retrieved_chunk_count INT,
    used_chunk_count      INT,
    est_context_tokens    INT,
    graph_radius          INT,
    top_k                 INT,
    coverage_score        DOUBLE PRECISION,
    precision_score       DOUBLE PRECISION,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Metric definitions:
•	coverage_score: Fraction of expected KG entities that were actually retrieved by the RAG query.
•	precision_score: Fraction of retrieved entities that were relevant to the task.
Both scores are computed by LLM-based evaluation (prompt asks the model to classify each retrieved item as relevant or not) or, in test mode, by intersection with ground-truth entity lists.

 
B.4 Repository Integration Metadata (DDL)
A separate schema repo_meta records integration wiring across repositories.

Schema Definition
```sql
CREATE SCHEMA IF NOT EXISTS repo_meta;

CREATE TABLE repo_meta.integrations (
    id            BIGSERIAL PRIMARY KEY,
    provider_code TEXT NOT NULL,
    task_slug     TEXT NOT NULL,
    repo_name     TEXT NOT NULL,
    repo_root     TEXT NOT NULL,
    profile_name  TEXT NOT NULL,
    first_run_id  TEXT,
    last_run_id   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (provider_code, task_slug, repo_name)
);

CREATE TABLE repo_meta.files (
    id               BIGSERIAL PRIMARY KEY,
    integration_id   BIGINT NOT NULL REFERENCES repo_meta.integrations(id) ON DELETE CASCADE,
    rel_path         TEXT NOT NULL,
    artifact_type    TEXT NOT NULL,
    last_run_id      TEXT,
    last_change_type TEXT, -- create or update
    last_sha256      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (integration_id, rel_path, artifact_type)
);
```

This schema is part of the v1 deployment. For any run where repo_integration_enabled is True, persist_run_outcome writes new or updated integration records. These records track the link between an integration task, a provider, and a concrete repository, and they support inspection and tooling across runs.
 
Appendix C — WorkflowState and Node Contracts
C.1 Overview
This appendix defines:
•	The authoritative WorkflowState  object used by all LangGraph nodes.
•	The full contract for every node in the pipeline.
•	The rule that only persist_silver_checkpoint, persist_gold_checkpoint, and persist_run_outcome may write to the database, and only apply_repo_integration_changes may write to disk.
•	The semantics of spec_refs and source_refs.
Consistency across all nodes produces deterministic flows and reproducible runs.
C.1.1 Spec References and Source References
Spec references (spec_refs)
•	spec_refs is a list of string references to specification sources.
•	Each element must be either:
o	an http:// or https:// URL pointing to an OpenAPI, HTML, Markdown, or PDF resource, or
o	a local filesystem path (absolute or relative) to a spec file.
•	A run must have at least one spec reference.
•	plan_run treats the first entry as the primary spec and sets
o	plan["primary_spec_ref"] = spec_refs[0].
•	Additional entries in spec_refs[1:] represent supporting specs. In v1, all spec_refs follow the same ingestion, chunking, embedding, and Silver extraction path. The planner may draw endpoints, entities, and events from any of these specifications when it builds an IntegrationTask and a workflow. The primary spec still acts as the anchor for provider inference and task focus, but flows can span endpoints drawn from several spec documents.
Source references (source_refs)
•	source_refs is a list of high-level identifiers for known providers or specs (for example "stripe", "github", "stripe_test").
•	Every run must have at least one source reference after plan_run completes. If the caller does not supply source_refs, plan_run derives a primary value from provider_code or the primary spec host and sets source_refs = [<derived_code>].
•	The first entry in source_refs is the canonical provider code for persistence and KG usage. It maps to spec_silver.source_systems.code and to kg.workflow_templates.provider_code.
•	Additional entries may describe logical variants for the same provider or related sources in the run and are available to nodes that need them.
•	Nodes that write Silver, Gold, or KG rows treat an empty source_refs list at graph start as a valid input, but plan_run must populate it before any persistence node runs.
 
C.2 WorkflowState Definition
File: graph/state.py
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from domain.models import (
    SpecDocument,
    Endpoint,
    Schema,
    Entity,
    EntityRelationship,
    Event,
    IntegrationTask,
    IntegrationFlowNode,
    IntegrationFlowEdge,
    EndpointBinding,
    Policy,
    CodeArtifact,
    SpecChunkEmbedding,
    SpecSection,
)
from repo.models import RepoProfile, RepoChangeSet
from api.types import IntegrationOptions


@dataclass
class WorkflowState:
    """
    Single authoritative state object passed between LangGraph nodes.
    Nodes may modify only the fields they are contracted to write.

    ID semantics:
    - Domain model ids and foreign keys may be None before a persistence node runs.
    - Persistence nodes write to the database and backfill ids on in-memory objects.

    Options semantics:
    - options is attached once at graph start and treated as read-only by nodes.
    - Nodes use options to control branching but do not mutate options.
    """

    # Inputs
    source_refs: List[str]
    spec_refs: List[str]
    task_description: str
    provider_code: Optional[str] = None
    options: Optional[IntegrationOptions] = None

    # Bronze-level spec content
    spec_documents: List[SpecDocument] = field(default_factory=list)
    doc_chunks: List[str] = field(default_factory=list)
    openapi_spec: Optional[Dict[str, Any]] = None

    # Silver drafts
    spec_sections: List[SpecSection] = field(default_factory=list)
    endpoints: List[Endpoint] = field(default_factory=list)
    schemas: List[Schema] = field(default_factory=list)
    entities: List[Entity] = field(default_factory=list)
    relationships: List[EntityRelationship] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)

    # Embeddings (Silver-adjacent)
    spec_chunk_embeddings: List[SpecChunkEmbedding] = field(default_factory=list)

    # Gold drafts
    integration_task: Optional[IntegrationTask] = None
    workflow_nodes: List[IntegrationFlowNode] = field(default_factory=list)
    workflow_edges: List[IntegrationFlowEdge] = field(default_factory=list)
    endpoint_bindings: List[EndpointBinding] = field(default_factory=list)
    policies: List[Policy] = field(default_factory=list)
    code_artifacts: List[CodeArtifact] = field(default_factory=list)

    # Repo integration
    repo_root: Optional[Path] = None
    repo_profile: Optional[RepoProfile] = None
    repo_changes: Optional[RepoChangeSet] = None
    repo_markdown_context: Optional[str] = None

    # Control / bookkeeping
    plan: Dict[str, Any] = field(default_factory=dict)
    completed_steps: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    persisted_ids: Dict[str, Any] = field(default_factory=dict)

    # Outputs
    report_markdown: Optional[str] = None
    run_id: Optional[str] = None
Key Rules
•	Only persist_silver_checkpoint, persist_gold_checkpoint, and persist_run_outcome may write to the database.
•	Only apply_repo_integration_changes may write to the filesystem.
•	All other nodes mutate in-memory drafts only.
•	Every successful node must append its own name (as a string) to completed_steps.
•	On failure, a node must:
o	Append a human-readable error message to errors.
o	Route control to the handle_error node (see below).
 
C.3 Node Contracts (Authoritative List)
Each node:
•	Accepts a WorkflowState
•	Mutates only its allowed fields
•	Appends its name to completed_steps

Only three nodes write to the database in v1:
•	persist_silver_checkpoint
•	persist_gold_checkpoint
•	persist_run_outcome

Each node writes a structured log entry for a run. The entry contains node name, run_id, and a compact JSON snapshot of the fields it reads and the fields it writes. The log does not contain full prompt text. LangSmith remains the primary trace for prompts and model responses.

Nodes may raise only recoverable exceptions inside tests; in production runs they should record errors in state.errors and let the graph route to handle_error.
1. plan_run
Reads
•	task_description
•	spec_refs
•	provider_code (optional)
•	options (optional)
Behavior
•	Validate inputs:
o	Assert that spec_refs has at least one element.
•	Primary spec planning:
o	Set plan["primary_spec_ref"] = spec_refs[0].
o	Set plan["supporting_spec_refs"] = spec_refs[1:] (may be empty).
•	Repo usage flag:
o	Set plan["use_repo"] to:
	True if state.repo_root is not None and options.repo_integration_enabled is True.
	False in all other cases.
•	Provider code resolution:
o	If IntegrationOptions.override_provider_code is set:
	Normalize it: lowercase, replace non-alphanumeric characters with _.
	Use this value as both state.provider_code and plan["provider_code"].
o	Else if state.provider_code is already set:
	Keep that value and set plan["provider_code"] to the same value.
o	Else, infer a provider code from the primary spec host when possible.
	Example: api.stripe.com → "stripe", api.github.com → "github".
	If no special mapping exists, take the last component of the hostname, lowercase it, drop non-alphanumeric characters, and replace - with _.
	Set both state.provider_code and plan["provider_code"] to that inferred value.
Writes
•	plan["use_repo"]
•	plan["provider_code"]
•	plan["primary_spec_ref"]
•	plan["supporting_spec_refs"]
•	Normalized state.provider_code
•	Append "plan_run" to completed_steps
 
2. ingest_spec
Reads
•	spec_refs
•	plan["primary_spec_ref"]
Behavior
• For each entry in spec_refs, load raw spec content:
o If URL: perform HTTP GET.
o If filesystem path: read file contents.
• For each loaded spec, compute the SHA-256 hash of the raw content.
• For each loaded spec, create a SpecDocument draft with:
o id = None
o source_system_id = None (to be filled by persist_silver_checkpoint)
o version = "unknown" by default or a parsed version string if available
o uri equal to the spec reference string
o content_type as a best-effort guess
o sha256 set to the computed hash
• Append each SpecDocument to state.spec_documents in the same order as spec_refs.
• For each SpecDocument, split raw content into text chunks and append these to doc_chunks. Keep the global order stable so chunk_index can reference a specific (spec_document, local_index) pair.
• Record a mapping from chunk_index to the owning SpecDocument in
plan["chunk_index_to_spec_document_uri"].
• plan["primary_spec_ref"] still points at the main spec for task focus and provider inference, but all spec_documents contribute chunks and Silver drafts.
Writes
•	spec_documents (length equals len(spec_refs))
•	doc_chunks containing chunks from all spec_documents, in a stable global order
•	plan["chunk_index_to_spec_document_uri"]
•	Append "ingest_spec" to completed_steps
 
3. detect_and_parse_spec
Reads
•	doc_chunks
•	spec_documents
•	plan["primary_spec_ref"]
Behavior
•	Find the primary SpecDocument whose uri equals plan["primary_spec_ref"].
o	If no match, treat spec_documents[0] as the primary document.
•	For the primary spec only:
o	Detect whether the content is OpenAPI/Swagger JSON or YAML.
o	If OpenAPI is detected:
	Parse into openapi_spec (Python dict).
	Update the primary SpecDocument.content_type to an OpenAPI-specific type, such as "application/vnd.oai.openapi+json".
o	If OpenAPI is not detected:
	Leave openapi_spec = None.
•	Supporting SpecDocument entries remain unparsed in this node. They are available as text sources for retrieval and future multi-spec flows.
•	After parsing the primary spec, the node may detect and parse OpenAPI for supporting specs as well. Parsed content for supporting specs is stored in per-spec structures or in openapi_spec extensions. Supporting specs that are not OpenAPI remain available through doc_chunks and SpecSection rows.
Writes
•	openapi_spec for the primary spec (or None)
•	Possibly refined spec_documents[primary] .content_type
•	Append "detect_and_parse_spec" to completed_steps
 
4. build_silver_api_model
Reads:
•	openapi_spec or (doc_chunks, spec_documents)
Behavior:
Build Silver drafts.
Writes:
•	schemas, entities, relationships, events, endpoints
•	Appends "build_silver_api_model"
 
5. embed_spec_chunks
Reads
•	spec_documents
•	doc_chunks
Behavior
•	Treat doc_chunks as the ordered list of text chunks across all spec_documents in the run.
•	For each index i and chunk text in doc_chunks:
o	Compute an embedding vector with the configured embedding model (see Appendix E).
o	Create a SpecChunkEmbedding with:
	id = None
	spec_document_id = None (to be filled later by persist_silver_checkpoint using plan["chunk_index_to_spec_document_uri"])
	chunk_index = i
	content equal to the chunk text
	embedding as the list of floats
•	Replace spec_chunk_embeddings with the resulting list.
•	Optionally record summary metadata in plan["embeddings"] (for example total chunk count and embedding model name).
Writes
•	spec_chunk_embeddings
•	Optional plan["embeddings"]
•	Append "embed_spec_chunks" to completed_steps
 
6. understand_task
Reads:
•	task_description
•	provider_code
•	Silver drafts (entities, endpoints, events, etc.)
Behavior (IntegrationTask Construction):
•	Derive task_slug
o	Ask the LLM to produce a short imperative phrase (≤ 6 words)
representing the task (e.g. "create checkout session").
o	Normalize according to Naming Conventions:
	Lowercase.
	Replace spaces and punctuation with _.
	Collapse multiple _ into a single _.
	Verify that final task_slug matches [a-z0-9_]+.
•	Derive input_entities / output_entities
o	Use the LLM to extract two sets from task_description:
	input_entities: names of entities the workflow reads or requires as inputs.
	output_entities: names of entities the workflow creates or updates.
o	Canonicalization step:
	Valid entity names are those present in state.entities (by Entity.name).
	If the model suggests entities that do not match any known Entity.name
(case-insensitive), ignore them and append a human-readable warning
string to state.errors.
•	Populate constraints
o	IntegrationTask.constraints MUST be a JSON object with:
	idempotency_required: bool
	max_latency_ms: int | None
	requires_webhooks: bool
	extra: dict (free-form additional constraints)
o	Defaults if not specified by the model:
	idempotency_required = False
	max_latency_ms = None
	requires_webhooks = False
	extra = {}
•	IDs and foreign keys
o	source_system_id and target_spec_document_id MUST be left as None.
o	the persistence checkpoints are responsible for wiring these to actual database ids.
Writes:
•	Fully-populated integration_task (with task_slug, input_entities,
output_entities, constraints per above).
•	Appends "understand_task".
 
7. align_task_with_kg
Reads:
•	integration_task
•	Silver drafts
•	KG + vector index
Writes:
•	plan["candidate_templates"]
•	Updated integration_task.constraints
•	Appends "align_task_with_kg"
 
8. plan_integration_flow
Reads:
•	integration_task
•	Silver drafts
•	plan["candidate_templates"]
Behavior:
The resulting flow MUST satisfy the Integration Flow Node Semantics:
•	Exactly one start node.
•	At least one end node.
•	All nodes except the start node have at least one incoming edge.
•	All nodes except end nodes have at least one outgoing edge.
•	position is a non-negative integer representing a topological order, and
values MUST be strictly increasing along any valid path.
•	For any node with node_type="api_call" and a known endpoint_id, plan_integration_flow MUST create a corresponding EndpointBinding scaffold with:
o	task_id=None (to be filled in persist_gold_checkpoint)
o	flow_node_key equal to the node’s node_key
o	endpoint_id equal to the node’s endpoint_id
o	request_mapping and response_mapping initialized to empty dicts ({}).
•	It MUST NOT attempt to infer detailed field-level mappings; that is delegated to generate_code_and_tests.
Writes:
•	workflow_nodes
•	workflow_edges
•	Appends "plan_integration_flow"
 
9. attach_policies_and_patterns
Reads:
•	workflow_nodes
•	workflow_edges
•	endpoints
Writes:
•	policies
•	Appends "attach_policies_and_patterns"
 
10. attach_repo_context
Reads:
•	repo_root
•	repo_profile (optional)
•	Optional CLI / options-supplied repo_markdown_path (if configured externally)
Behavior:
•	If state.repo_root is None and no repo_markdown_path is provided:
o	Do nothing except append "attach_repo_context" to completed_steps.
•	If repo_markdown_path is provided (testing / offline mode):
o	Read the markdown file from disk.
o	Set state.repo_markdown_context to its content.
o	Do not traverse the filesystem or build a RepoSnapshot.
o	Do not modify state.repo_profile.
•	Otherwise (repo_root set, no repo_markdown_path):
o	Use the configured RepoContextProvider (filesystem-based by default) to:
	Walk repo_root and construct a RepoSnapshot.
	Build RepoSnapshot.full_markdown via MockedGithubRepoRetriever.export_markdown().
o	Set state.repo_markdown_context = snapshot.full_markdown.
o	If state.repo_profile is None:
	Call detect_repo_profile(snapshot) to infer a RepoProfile.
	If the repo matches a known Subatomic FastAPI-style service, this
SHOULD return SUBATOMIC_MOCK_PROFILE.
	Otherwise, it MUST return a generic profile with
archetype="generic_python" and reasonable defaults.
•	attach_repo_context MUST NOT override an explicitly provided state.repo_profile.
Writes:
•	repo_markdown_context
•	repo_profile (if needed)
•	Appends "attach_repo_context"
 
11. generate_code_and_tests
Reads:
•	Silver drafts
•	integration_task
•	workflow_nodes
•	workflow_edges
•	endpoint_bindings
•	policies
•	repo_profile
•	repo_markdown_context
•	Silver drafts (endpoints, schemas, entities, events)
•	Plan/config stored in state.plan
Behavior:
•	Generate CodeArtifact instances for:
o	Clients (artifact_type="client")
o	Workflows (artifact_type="workflow")
o	Tests (artifact_type="test")
•	Generated artifacts do not directly modify router or settings files. Router and settings updates are computed later in analyze_repo_layout based on RepoProfile.
•	For each EndpointBinding scaffold produced by plan_integration_flow, generate_code_and_tests SHOULD:
•	Populate request_mapping and response_mapping with field-level mappings derived from:
o	Silver schemas and entities
o	The planned IntegrationFlow
o	The task description and policies
•	MAY adjust or enrich existing bindings but MUST NOT delete bindings for any "api_call" node.
•	If it cannot determine a reasonable mapping, it MUST leave a partial mapping and record a human-readable warning in state.errors.
Writes:
•	code_artifacts
•	Optional updates to endpoint_bindings if codegen refines them.
•	Appends "generate_code_and_tests"
 
12. analyze_repo_layout
Reads:
•	repo_root
•	repo_profile
•	code_artifacts
•	provider_code
•	integration_task (for task_slug / integration_slug)
Behavior:
•	Assert state.repo_root is not None and state.repo_profile is not None.
•	Behavior depends on state.repo_profile.archetype:
o	If archetype == "fastapi_service":
	Use:
	repo_profile.layout_hints["clients_dir"]
	repo_profile.layout_hints["workflows_dir"]
	repo_profile.layout_hints["tests_dir"]
to place artifact_type="client" | "workflow" | "test" artifacts.
	Also apply router and settings insertion rules (Appendix G) using:
	repo_profile.integration_hooks["router_file"]
	repo_profile.integration_hooks["router_registration_marker"]
	repo_profile.integration_hooks["settings_file"]
	repo_profile.integration_hooks["settings_marker"]
if present.
o	If archetype == "generic_python" or any unknown archetype:
	If layout_hints provides explicit dirs, use them.
	Else, default to:
	Clients: integrations/clients/<provider_code>.py
	Workflows: integrations/flows/<integration_slug>.py
	Tests: tests/test_<integration_slug>.py
	Do not attempt router or settings insertion.
•	For all archetypes:
o	Construct RepoChangeSet from state.code_artifacts with rel_path equal to:
	<dir>/<CodeArtifact.rel_path> where <dir> is chosen per rules above.
o	MUST NOT write to disk.
•	After building RepoChangeSet, the node prepares in-memory RepoIntegrationRecord entries that describe the linkage between the task, provider_code, and repo_root. These records are passed to persist_run_outcome for storage in the repo_meta schema.
Writes:
•	repo_changes populated with a RepoChangeSet instance.
•	Appends "analyze_repo_layout".
 
13. apply_repo_integration_changes
Reads:
•	repo_root
•	repo_changes
Writes (filesystem only):
•	Updated repo_changes.before and change_type
•	Writes change.after to disk
•	Appends "apply_repo_integration_changes"
 
14. validate_integration_design
Reads:
•	All Silver and Gold drafts
•	repo_changes
Behavior:
•	Validate:
o	Silver drafts are internally consistent.
o	Gold drafts reference existing Silver ids where required.
o	Integration flow validity per Integration Flow Node Semantics:
	Exactly one start node.
	At least one end node.
	Connectivity and position constraints as in Section H.5 (Flow Node Semantics).
	For every node with node_type="api_call":
	endpoint_id is set and references a valid Endpoint.
	Exactly one matching EndpointBinding exists with:
	Matching task_id.
	flow_node_key equal to the node’s node_key.
	endpoint_id equal to the node’s endpoint_id.
•	On failure, append human-readable error messages to errors and allow the graph to route to handle_error.
Writes:
•	errors (append on validation failures)
•	Appends "validate_integration_design".
 
15. persist_silver_checkpoint
Reads:
•	Silver drafts: SpecDocument, SpecSection, Schema, SchemaField, Entity, EntityRelationship, Event, Endpoint, EndpointParameter
•	spec_chunk_embeddings (for spec_document_id backfill when present)
Behavior:
•	Start a transaction.
•	Upsert Silver and spec_documents tables using the keys in Appendix B.
•	Backfill ids and foreign keys in in-memory Silver objects.
•	Commit and write a compact summary into plan["silver_persist_summary"].
Writes:
•	Database rows for spec_silver.* tables and spec_silver.spec_documents
•	state.persisted_ids["silver"] with primary ids
•	Appends "persist_silver_checkpoint" to completed_steps
 
16. persist_gold_checkpoint
Reads:
•	integration_task
•	workflow_nodes, workflow_edges, endpoint_bindings, policies
•	SourceSystem ids and Endpoint ids written by persist_silver_checkpoint
Behavior:
•	Start a transaction.
•	Upsert integration_gold.integration_tasks and related Gold tables using the keys in Appendix B.
•	Backfill ids for IntegrationTask and Gold objects.
•	Commit and write plan["gold_persist_summary"].
Writes:
•	Database rows for integration_gold.* tables except run_status and metrics tables
•	state.persisted_ids["gold"]["task_id"] and related ids
•	Appends "persist_gold_checkpoint" to completed_steps
 
17. persist_run_outcome
Reads:
•	All persisted ids
•	errors
•	plan, including plan["failed"] and summaries
•	RAG metrics and KG updates collected during the run
Behavior:
•	Write run_status row.
•	Write RAG evaluation metrics rows.
•	Write KG learning and governance rows as described in Appendix H.4.
•	In dry_run mode, skip all database writes and compute the same structures in memory for the report.
•	Upsert repo_meta integration records derived from RepoIntegrationRecord objects, when repo_integration_enabled is True and repo_changes is not None.
Writes:
•	Database rows for integration_gold.run_status and integration_gold.rag_eval_metrics
•	KG learning tables
•	state.run_id if not set
•	Appends "persist_run_outcome" to completed_steps
 
18. build_report
Reads:
•	Everything generated so far
Writes:
•	report_markdown
•	Appends "build_report"
 
19. handle_error
Reads:
•	errors
•	completed_steps
•	Optional partial drafts (Silver / Gold / repo fields)

Behavior:
•	Does not write to disk or database
•	Aggregates errors into a short summary string that persist_run_outcome later uses to set run_status.error_summary
•	Leaves run_id unchanged; if run_id is None, persist_run_outcome is responsible for generating a final run_id even for failed runs
•	Sets plan["failed"] = True

Writes:
•	plan["failed"] = True
•	Appends "handle_error" to completed_steps

The graph routes to handle_error whenever a node cannot complete its contract but can record a recoverable error.
 
Appendix D — Repo Profile, Repo Context, and MockedGithubRepoRetriever
D.1 Overview
This appendix defines the optional repository-integration layer. When repo_root and repo_profile are supplied, three LangGraph nodes activate:
•	attach_repo_context
•	analyze_repo_layout
•	apply_repo_integration_changes (skipped when IntegrationOptions.dry_run is True)

The layer standardizes:
•	RepoProfile — describes where generated clients, workflows, tests, router entries, and settings blocks belong.
•	MockedGithubRepoRetriever — produces a markdown summary of an existing repository, useful for test fixtures.
•	Helper utilities for constructing repository context.

When both repo_root and repo_profile are None, these nodes become no-ops and the workflow produces in-memory artifacts only.
 
D.2 Repo Profile
File: repo/profiles.py
from repo.models import RepoProfile

SUBATOMIC_MOCK_PROFILE = RepoProfile(
    name="subatomic_mock_service",
    archetype="fastapi_service",
    layout_hints={
        "clients_dir": "src/integrations/clients",
        "workflows_dir": "src/integrations/flows",
        "tests_dir": "tests/integrations",
    },
    integration_hooks={
        "router_file": "src/app/router.py",
        "router_registration_marker": "# <AUTO_INTEGRATION_MARKER>",
        "settings_file": "src/app/settings.py",
        "settings_marker": "# <AUTO_INTEGRATION_SETTINGS_MARKER>",
    },
)
This is one concrete profile instance for a FastAPI-style service; other repos may use different archetypes and hints.
 
D.3 Repo Models
File: repo/models.py
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from mocked_github_repo import MockFile


@dataclass
class RepoProfile:
    """
    Generic description of how to integrate with a target repository.

    Semantics:
    - archetype: high-level style of the repo
      (for example "fastapi_service", "generic_python", "library").

      For v1:
        * "fastapi_service":
          - Matches a service with FastAPI routers.
          - Uses layout_hints and integration_hooks.
          - Supports router and settings insertion as in Appendix G when
            router_file and settings_file markers are present.

        * "generic_python":
          - Matches a general Python application repo without a strict web
            framework contract.
          - Uses layout_hints when present.
          - Falls back to standard integrations/ directories for clients and
            flows and tests/ for tests.
          - Does not touch router or settings files.

        * "library":
          - Matches a pure library repo.
          - Prefers layout_hints that point under a library package such as
            src/<package>/integrations.
          - Falls back to placing artifacts under integrations/ and tests/
            when hints are not present.
          - Does not touch router or settings files.

    - layout_hints: placement hints for artifacts (clients, workflows, tests, etc.).
      Typical keys:
        - "clients_dir"
        - "workflows_dir"
        - "tests_dir"

    - integration_hooks: hook locations for wiring (routes, settings, etc.).
      Typical keys:
        - "router_file"
        - "router_registration_marker"
        - "settings_file"
        - "settings_marker"

    This abstraction keeps repo handling pluggable. Concrete repos,
    including the Subatomic mock FastAPI service and internal libraries,
    are modeled by supplying specific layout_hints and integration_hooks
    rather than by introducing repo-specific subclasses.
    """
    name: str
    archetype: str
    layout_hints: Dict[str, Any] = field(default_factory=dict)
    integration_hooks: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RepoSnapshot:
    """
    In-memory snapshot of a repository's contents and basic stats.

    Used by RepoContextProvider implementations and attach_repo_context_node.

    - files: mapping from POSIX-style relative paths to MockFile.
    - stats: implementation-defined repo statistics (e.g., extension counts).
    - tree_markdown: optional directory tree representation.
    - full_markdown: rich markdown export used as RAG context.

    The source of this snapshot is pluggable:
    - filesystem_repo_context_provider (local repo_root)
    - mock/subatomic loaders that simulate real repos via MockedGithubRepoRetriever
    - future GitHub API-based providers
    """
    repo_name: str
    owner: str
    files: Dict[str, MockFile]
    stats: Dict[str, Any]
    tree_markdown: str
    full_markdown: str  # used as RAG context


@dataclass
class RepoIntegrationRecord:
    """
    In-memory representation of a persisted integration record
    that links a task, provider, and repo.

    The persistence layer maps this to a repo_meta.integrations table.
    """
    id: Optional[int]
    repo_name: str
    provider_code: str
    task_slug: str
    repo_root: Path
    profile_name: str


@dataclass
class FileChange:
    """
    Represents a single file change to be applied to the repo.

    Semantics:
    - analyze_repo_layout computes rel_path, after and sets change_type="create".
    - apply_repo_integration_changes:
        * reads any existing file contents into before,
        * sets change_type to "create" or "update",
        * writes after to disk.

    Fields:
    - rel_path: POSIX-style relative path under repo_root.
    - change_type: 'create' or 'update' (filled by graph nodes).
    - before: previous file contents (if any).
    - after: new file contents.
    """
    rel_path: str
    change_type: str
    before: Optional[str]
    after: str


@dataclass
class RepoChangeSet:
    """
    Collection of file changes that will be applied under repo_root.

    This structure is computed in-memory by analyze_repo_layout_node and only
    written to disk by apply_repo_integration_changes_node.
    """
    repo_root: Path
    changes: List[FileChange] = field(default_factory=list)

    def files_created(self) -> List[FileChange]:
        return [c for c in self.changes if c.change_type == "create"]

    def files_updated(self) -> List[FileChange]:
        return [c for c in self.changes if c.change_type == "update"]

 
D.4 MockedGithubRepoRetriever
File: mocked_github_repo.py
from dataclasses import dataclass
from typing import Dict


@dataclass
class MockFile:
    path: str
    content: str


class MockedGithubRepoRetriever:
    """
    Extended mocked GitHub repo retriever.

    Behavior in v1:
    - Stores the repo contents in memory via add_file.
    - export_markdown() builds a deterministic markdown summary with:
        • A repo header.
        • A flat file list.
        • Basic extension statistics.
        • A content section for each file.

    Canonical role:
    - This is the canonical utility that converts a set of (path, content)
      pairs into a rich, deterministic markdown export for RAG.
    - All RepoContextProvider implementations SHOULD:
        • construct a MockedGithubRepoRetriever,
        • call add_file for each file,
        • and use export_markdown() as RepoSnapshot.full_markdown.

    This class is used only to construct RAG context; it never writes to disk.
    """

    def __init__(self, repo_name: str, owner: str = "mocked-user") -> None:
        self.repo_name = repo_name
        self.owner = owner
        self._files: Dict[str, MockFile] = {}

    def add_file(self, path: str, content: str) -> None:
        """
        Add or replace a file in the in-memory structure.

        path: POSIX-style relative file path.
        content: file text.
        """
        self._files[path] = MockFile(path=path, content=content)

    def export_markdown(self) -> str:
        """
        Produce a markdown summary of all files.

        v1 format sketch (must be deterministic for a given file set):

        # Repo: {owner}/{repo_name}

        ## Files
        - {path_1}
        - {path_2}
        - ...

        ## Stats
        - total_files: {N}
        - by_extension:
          - .py: {count_py}
          - .md: {count_md}
          - ...

        ## File contents
        ### {path_1}
        ```text
        <file_1_content_or_truncated_snippet>
        ```

        ### {path_2}
        ```text
        <file_2_content_or_truncated_snippet>
        ```

        File order and extension statistics must depend only on the sorted paths and contents so that repeated runs with the same file set produce identical markdown.

        The concrete implementation in the codebase should:
        - Sort file paths lexicographically.
        - Derive extension counts from these paths.
        - Truncate very large files to a fixed number of characters or lines with a clear marker.
        """
        raise NotImplementedError("export_markdown must be implemented to match the v1 format sketch")

    def calculate_stats(self) -> Dict[str, int]:
        """
        Compute simple statistics:

        - total_files: count of files in _files.
        - by_extension: mapping from file extension (".py", ".md", "") to file count.

        v1 code in filesystem_repo_context_provider uses this for RepoSnapshot.stats.
        """
        ...

    def generate_tree(self) -> str:
        """
        Produce a simple directory tree in markdown, for example:

        .
        ├─ src/
        │  ├─ app/
        │  └─ integrations/
        └─ tests/

        v1 code in filesystem_repo_context_provider uses this string for RepoSnapshot.tree_markdown.
        """
        ...
 
D.5 Building Repo Context
filesystem_repo_context_provider is invoked by attach_repo_context when both repo_root and repo_profile are present. The function walks a local checkout and produces a RepoSnapshot for use in downstream nodes.

File: repo/context.py
from pathlib import Path
from typing import Protocol

from repo.models import RepoProfile, RepoSnapshot
from mocked_github_repo import MockedGithubRepoRetriever, MockFile


class RepoContextProvider(Protocol):
    """
    Conceptual interface for building repo context.

    Any implementation that:
    - Takes a repo_root: Path (or a logical repo handle),
    - Returns a RepoSnapshot.

    Examples:
    - filesystem_repo_context_provider: walks a local filesystem checkout.
    - mocked_repo_context_provider: uses MockedGithubRepoRetriever and
      in-memory MockFile objects (e.g., for the Subatomic mock repo).
    - github_api_repo_context_provider: fetches files via GitHub APIs.
    """
    def __call__(self, repo_root: Path, profile: RepoProfile) -> RepoSnapshot: ...


def filesystem_repo_context_provider(
    repo_root: Path,
    profile: RepoProfile,
) -> RepoSnapshot:
    """
    Default v1 RepoContextProvider implementation.

    Walks repo_root and captures all file contents into a RepoSnapshot,
    using MockedGithubRepoRetriever.export_markdown() to generate full_markdown.

    This treats MockedGithubRepoRetriever as a pluggable utility: any future
    provider that can construct (path, content) pairs can reuse the same
    markdown export logic.
    """
    retriever = MockedGithubRepoRetriever(repo_name=repo_root.name, owner="local-filesystem")
    files: dict[str, MockFile] = {}

    for path in repo_root.rglob("*"):
        if path.is_file():
            rel_path = path.relative_to(repo_root).as_posix()
            content = path.read_text(encoding="utf-8", errors="ignore")
            retriever.add_file(rel_path, content)
            files[rel_path] = MockFile(path=rel_path, content=content)

    full_markdown = retriever.export_markdown()

    # stats and tree_markdown can be derived from retriever or left simple for v1
    stats = retriever.calculate_stats()
    tree_markdown = retriever.generate_tree()

    return RepoSnapshot(
        repo_name=repo_root.name,
        owner="local-filesystem",
        files=files,
        stats=stats,
        tree_markdown=tree_markdown,
        full_markdown=full_markdown,
    )

 
D.6 Node — attach_repo_context
File: graph/nodes/repo_context.py (spec-level pseudocode)
from graph.state import WorkflowState
from repo.context import filesystem_repo_context_provider
from repo.models import RepoProfile, RepoSnapshot
from repo.profiles import SUBATOMIC_MOCK_PROFILE
from typing import Callable

RepoContextProviderFn = Callable[[Path, RepoProfile], RepoSnapshot]

# In configuration/module scope:
DEFAULT_REPO_CONTEXT_PROVIDER: RepoContextProviderFn = filesystem_repo_context_provider


def attach_repo_context_node(
    state: WorkflowState,
    provider: RepoContextProviderFn = DEFAULT_REPO_CONTEXT_PROVIDER,
    repo_markdown_path: Optional[Path] = None,
) -> WorkflowState:
    """
    Populate repo_markdown_context and repo_profile.

    Behavior:
    - If neither repo_root nor repo_markdown_path is provided:
        * No-op other than appending 'attach_repo_context' to completed_steps.
    - If repo_markdown_path is provided:
        * Read markdown from that file into state.repo_markdown_context.
        * Do not construct a RepoSnapshot or modify repo_profile.
    - If repo_root is provided:
        * Use the given RepoContextProvider (filesystem-based by default) to
          construct a RepoSnapshot.
        * Set state.repo_markdown_context = snapshot.full_markdown.
        * If state.repo_profile is None:
            - Call detect_repo_profile(snapshot) to infer a RepoProfile.
            - For the Subatomic mock repo, this SHOULD return SUBATOMIC_MOCK_PROFILE.
            - Otherwise, it MUST return a generic profile with
              archetype="generic_python" and reasonable defaults.
        * MUST NOT override an explicitly provided state.repo_profile.
    """
    if state.repo_root is None and repo_markdown_path is None:
        state.completed_steps.append("attach_repo_context")
        return state

    if repo_markdown_path is not None:
        state.repo_markdown_context = repo_markdown_path.read_text(encoding="utf-8")
        state.completed_steps.append("attach_repo_context")
        return state

    # repo_root is set
    snapshot = provider(state.repo_root, state.repo_profile or RepoProfile(
        name=state.repo_root.name,
        archetype="generic_python",
    ))
    state.repo_markdown_context = snapshot.full_markdown

    if state.repo_profile is None:
        state.repo_profile = detect_repo_profile(snapshot)

    state.completed_steps.append("attach_repo_context")
    return state

 
D.7 Node — analyze_repo_layout
File: graph/nodes/analyze_repo_layout.py
from graph.state import WorkflowState
from repo.models import RepoChangeSet, FileChange


def _dir_for_artifact(profile, artifact_type: str, provider_code: str, integration_slug: str) -> str:
    """
    Resolve the target directory for a given artifact type based on RepoProfile.

    Resolution order:
    1. layout_hints[<type>_dir] if present (e.g., "clients_dir").
    2. Archetype-specific defaults.
    3. Generic fallbacks under integrations/.
    """
    hints = profile.layout_hints or {}
    key = f"{artifact_type}s_dir"  # client -> clients_dir, workflow -> workflows_dir, test -> tests_dir

    if key in hints:
        return hints[key]

    if profile.archetype == "fastapi_service":
        # Opinionated defaults for FastAPI-style services
        if artifact_type == "client":
            return "src/integrations/clients"
        if artifact_type == "workflow":
            return "src/integrations/flows"
        if artifact_type == "test":
            return "tests/integrations"

    # Generic fallback:
    if artifact_type == "client":
        return f"integrations/clients"
    if artifact_type == "workflow":
        return f"integrations/flows"
    if artifact_type == "test":
        return "tests"

    return "."


def analyze_repo_layout_node(state: WorkflowState) -> WorkflowState:
    """
    Build a RepoChangeSet from code_artifacts.

    NOTE:
    - Does not write to disk.
    - Only computes the intended file placement and any router/settings changes.
    - Uses RepoProfile.layout_hints and integration_hooks so that profiles
      for very different repo shapes remain pluggable.
    """

    assert state.repo_root is not None, "repo_root must be set for repo runs"
    assert state.repo_profile is not None, "repo_profile must be set for repo runs"
    assert state.integration_task is not None, "integration_task must be set before repo analysis"

    provider_code = state.provider_code or state.integration_task.provider_code
    integration_slug = state.integration_task.task_slug

    changes: list[FileChange] = []

    for artifact in state.code_artifacts:
        rel_dir = _dir_for_artifact(
            state.repo_profile,
            artifact_type=artifact.artifact_type,
            provider_code=provider_code,
            integration_slug=integration_slug,
        )
        rel_path = f"{rel_dir}/{artifact.rel_path}" if rel_dir != "." else artifact.rel_path

        changes.append(
            FileChange(
                rel_path=rel_path,
                change_type="create",
                before=None,
                after=artifact.content,
            )
        )

    # Router/settings insertion for FastAPI-style repos uses integration_hooks.
    if state.repo_profile.archetype == "fastapi_service":
        hooks = state.repo_profile.integration_hooks or {}

        router_file = hooks.get("router_file")
        router_marker = hooks.get("router_registration_marker")
        settings_file = hooks.get("settings_file")
        settings_marker = hooks.get("settings_marker")

        # If provided, compute FileChange entries that insert deterministic
        # blocks between BEGIN/END markers as per Appendix G. These changes
        # are added to the same RepoChangeSet and will be written by
        # apply_repo_integration_changes_node.
        #
        # (Implementation of _compute_router_change and _compute_settings_change
        # is left to the codebase; the spec requires that they be idempotent and
        # fully regenerate the regions.)

        if router_file and router_marker:
            router_change = _compute_router_change(
                state.repo_root, router_file, router_marker, provider_code, integration_slug
            )
            if router_change is not None:
                changes.append(router_change)

        if settings_file and settings_marker:
            settings_change = _compute_settings_change(
                state.repo_root, settings_file, settings_marker, provider_code
            )
            if settings_change is not None:
                changes.append(settings_change)

    state.repo_changes = RepoChangeSet(
        repo_root=state.repo_root,
        changes=changes,
    )

    state.completed_steps.append("analyze_repo_layout")
    return state

 
D.8 Node — apply_repo_integration_changes
File: graph/nodes/apply_repo_integration_changes.py
from graph.state import WorkflowState


def apply_repo_integration_changes_node(state: WorkflowState) -> WorkflowState:
    """
    Apply RepoChangeSet to disk.

    Behavior:
    - No-op if repo_changes is None.
    - Creates parent directories if missing.
    - Reads existing files (if any) into change.before.
    - Writes after-content to disk.
    """

    if state.repo_changes is None:
        state.completed_steps.append("apply_repo_integration_changes")
        return state

    root = state.repo_changes.repo_root

    for change in state.repo_changes.changes:
        target_path = (root / change.rel_path).resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if target_path.exists():
            change.before = target_path.read_text(encoding="utf-8", errors="ignore")
            change.change_type = "update"
        else:
            change.before = None
            change.change_type = "create"

        target_path.write_text(change.after, encoding="utf-8")

    state.completed_steps.append("apply_repo_integration_changes")
    return state
 
Appendix E — Model & Embedding Defaults
E.1 Overview
This appendix defines the default LLM and embedding configuration for v1 of the system.
These values are intended to be explicit, stable defaults that can later be tuned.
 
E.2 YAML Configuration
File: config/models.yaml

The workflow is LLM-agnostic. Each of planning, extraction, and codegen can bind to a different provider and model family. The YAML below shows v1 defaults using OpenAI, but the same structure works for Anthropic or other LangChain-compatible backends.

llm:
  planning:
    model: gpt-4.1
    temperature: 0.2
    max_tokens: 2048
    num_samples: 2
    max_parallel_samples: 2

  extraction:
    model: gpt-4o-mini
    temperature: 0.0
    max_tokens: 1024

  codegen:
    model: gpt-4.1
    temperature: 0.15
    max_tokens: 4096
    num_candidates: 2
    max_parallel_modules: 3

embeddings:
  model: text-embedding-3-small
  dimensions: 1536
  max_parallel_embeddings: 4

The listed models are default choices for v1. The same configuration structure can point to Anthropic models or other providers for each role. A deployment can mix providers by editing this file without changes to node code.
 
E.3 Usage Notes
1.	Embedding dimensions must match:
o	The embedding model definition
o	The Postgres VECTOR(1536) field in spec_silver.spec_chunks.embedding
2.	Node-level code loads these values at startup:
o	LLM initialization (model, temperature, max_tokens)
o	RAG retrieval (max_parallel_embeddings)
o	LangGraph execution rules (num_samples, max_parallel_modules)
3.	Each node writes a compact JSON log record that includes the model family and temperature in use. LangSmith runs should tag:
o	llm.model, llm.role (planning/extraction/codegen)
o	embeddings.model, embeddings.dimensions
to correlate quality, latency, and token usage with configuration.
4.	Different roles can use different providers. Planning, extraction, and code generation may each bind to distinct model families. The workflow reads these bindings from the archetype YAML at runtime.
 
E.4 Context Retrieval Defaults
Global Embedding Settings
•	EMBEDDING_MODEL = "text-embedding-3-small"
•	EMBEDDING_DIM = 1536
•	MAX_CHUNK_TOKENS = 512
Node-Specific Retrieval Defaults
If not overridden by YAML config, nodes MUST use:
Node	top_k	graph_radius
build_silver_api_model	5	0
understand_task	8	1
align_task_with_kg	10	2
plan_integration_flow	12	2
attach_policies_and_patterns	8	1
 
E.5 Token Budgets
Maximum total prompt tokens (retrieved context + instructions + schema) per node:
•	build_silver_api_model: 2000
•	understand_task: 3000
•	align_task_with_kg: 3000
•	plan_integration_flow: 4000
•	generate_code_and_tests: 8000 per module
 
E.6 Chunk Selection Policy
For any retrieval:
•	Sort candidate chunks by:
1.	Similarity score (descending).
2.	Section type preference: endpoint > schema > overview.
•Include chunks until the estimated token count would exceed the node's token budget, then stop.
 
Appendix F — Persistence and Transaction Model
F.1 Overview
The design uses three persistence checkpoints for clarity and controlled durability. Intermediate results stay in memory and in LangSmith until a checkpoint runs. This appendix defines the authoritative persistence model.

Persistent writes in v1 occur only in:
•persist_silver_checkpoint
•persist_gold_checkpoint
•persist_run_outcome

Other nodes change in-memory drafts only. This pattern keeps clear boundaries between inference steps and durable state while still writing usable state after each major phase.

The design has these goals:
•Atomicity within each checkpoint
•Deterministic, idempotent behavior for repeated runs
•Clear separation between inference (LLM-heavy) and durable state
•A direct mapping from pre-persistence domain models with id=None to database rows with assigned ids

F.1.1 ID and Foreign-Key Semantics
•All domain models use Optional[int] for id and foreign-key fields (for example schema_id, endpoint_id).
•Before a persistence node runs:
oid fields may be None
oForeign-key fields may be None
•The persistence nodes are responsible for:
oCreating or updating the corresponding database rows
oUpdating the object's own id
oUpdating dependent objects' foreign-key fields (for example SchemaField.schema_id, EndpointParameter.endpoint_id)
oKeeping the in-memory graph of objects consistent so that each foreign key points to a valid row that exists in the database after the checkpoint
 
F.2 Silver-Layer Writes
1. Upsert spec_silver.source_systems
•Matching key: (code).
•Source for code and display_name:
o	The primary source system code is state.source_refs[0] after plan_run. If source_refs was empty at graph start, plan_run derives this code from provider_code or the primary spec host and writes it into source_refs.
o	display_name is a human-readable label derived from this code or provided by configuration.
•	v1 requirement: persist_silver_checkpoint creates exactly one SourceSystem row for the provider in state.source_refs[0] and uses that id for all Silver objects in the run.
•	Behavior:
o	If a matching row exists, update display_name and base_url if they differ.
o	Otherwise, insert a new row.
•	After commit:
o	Set persisted_ids["source_system_id"] = <id>.
o	Update all in-memory SourceSystem objects to have this id.
o	For all Silver objects (SpecDocument, Schema, Entity, Endpoint, etc.), set source_system_id to this value if currently None.
 
2. Upsert spec_silver.spec_documents
•	Matching key: (source_system_id, sha256).
•	Behavior:
o	For each in-memory SpecDocument :
	Assert source_system_id is set to persisted_ids["source_system_id"].
	Upsert by (source_system_id, sha256); update uri, version, content_type on conflict.
•	After commit:
o	Set persisted_ids["spec_document_id"] = <id> for the primary spec document (the first one, or the one chosen for task alignment).
o	Update in-memory SpecDocument.id and spec_document_id fields on dependent objects (e.g., Endpoint.spec_document_id).
 
3. Upsert all Silver tables
Keys (from Appendix B):
•	schemas → (source_system_id, name)
•	fields → (schema_id, json_path)
•	entities → (source_system_id, name)
•	entity_relationships → (source_system_id, from_entity_id, to_entity_id, relationship_type)
•	events → (source_system_id, name)
•	endpoints → (source_system_id, spec_document_id, path, method)
•	endpoint_parameters → (endpoint_id, name, location)
Behavior:
•	For each Schema:
o	Assert source_system_id is set.
o	Upsert row using (source_system_id, name).
o	On insert, set Schema.id to the new ID.
•	For each SchemaField:
o	Assert schema_id is the ID of its parent Schema.
o	Upsert row using (schema_id, json_path).
•	For each Entity:
o	Assert source_system_id is set.
o	Assert schema_id (if any) references an existing Schema row.
o	Upsert row using (source_system_id, name).
•	For each EntityRelationship:
o	Assert source_system_id, from_entity_id, to_entity_id are set and refer to existing Entity rows.
o	Upsert row using (source_system_id, from_entity_id, to_entity_id, relationship_type).
•	For each Event:
o	Assert source_system_id is set.
o	Assert payload_schema_id and entity_id (if any) refer to existing rows.
o	Upsert row using (source_system_id, name).
•	For each Endpoint:
o	Assert source_system_id and spec_document_id are set.
o	Upsert row using (source_system_id, spec_document_id, path, method).
o	Set Endpoint.id to the resulting row ID.
•	For each EndpointParameter:
o	Assert endpoint_id is set to the ID of its owning Endpoint.
o	Upsert row using (endpoint_id, name, location).
 
4. Upsert spec_silver.spec_chunks (vector storage)
•	Only if spec_chunk_embeddings is non-empty.
•	For each SpecChunkEmbedding in state.spec_chunk_embeddings:
o	Set spec_document_id to persisted_ids["spec_document_id"].
o	Upsert row using (spec_document_id, chunk_index).
•	Assert that embedding matches the dimension from Appendix E.
 
F.3 Gold-Layer Writes
5. Upsert integration_gold.integration_tasks
•	Key: (provider_code, task_slug).
•	Behavior:
o	Assert provider_code is not empty.
o	Assert task_slug is not empty and stable across reruns for the same logical task.
o	Upsert row; update all non-key fields (description, constraints_json, etc.) on conflict.
•	After commit:
o	Set persisted_ids["task_id"] = <id>.
o	Set IntegrationTask.id = <id> in memory.
 
6. Upsert remaining Gold tables
Keys:
•	workflow_templates → (source_system_id, code)
•	integration_flow_nodes → (task_id, node_key)
•	integration_flow_edges → (task_id, from_node_key, to_node_key)
•	endpoint_bindings → (task_id, flow_node_key, endpoint_id)
•	policies → (task_id, policy_type, scope, scope_ref)
•	code_artifacts → (task_id, rel_path, artifact_type)
Behavior:
•	For each WorkflowTemplate:
o	Assert source_system_id is set.
o	Upsert by (source_system_id, code).
•	For each IntegrationFlowNode:
o	Set task_id to persisted_ids["task_id"].
o	Upsert by (task_id, node_key).
•	For each IntegrationFlowEdge:
o	Set task_id as above.
o	Upsert by (task_id, from_node_key, to_node_key).
•	For each EndpointBinding:
o	Set task_id as above.
o	Assert endpoint_id matches a persisted Endpoint.id.
o	Upsert by (task_id, flow_node_key, endpoint_id).
•	For each Policy:
o	Set task_id as above.
o	Upsert by (task_id, policy_type, scope, scope_ref).
•	For each CodeArtifact:
o	Set task_id as above.
o	Upsert by (task_id, rel_path, artifact_type).
 
F.4 Writing integration_gold.run_status
•	If run_id is missing, generate one.
•	Insert or update with:
o	run_id
o	task_id
o	status:
	"SUCCESS" if plan.get("failed") is false and there are no errors.
	"FAILED" if plan.get("failed") is true and no artifacts were persisted.
	"PARTIAL" if some writes succeeded but errors were recorded.
o	Timestamps
o	error_summary
o	langsmith_run_id (optional)
run_status may be written in a separate transaction if the main transaction fails.

After run_status is written, persist_run_outcome inserts one or more rows into integration_gold.rag_eval_metrics for nodes that collect RAG metrics. Metrics are keyed by run_id and node_name.
 
F.5 Transaction Boundaries
Silver writes run in a transaction inside persist_silver_checkpoint.
Gold writes run in a transaction inside persist_gold_checkpoint.
run_status, RAG metrics, and KG learning writes run in a transaction inside persist_run_outcome.

If a transaction fails in any of these nodes, the node rolls back and records the failure in errors and in the node summary inside plan. Later nodes see the last successful committed state.
 
F.6 Interaction With Repo Writes
•	apply_repo_integration_changes writes to disk before DB writes.
•	In the same run, persist_run_outcome writes repo_meta.integrations rows for every repo-aware integration and updates existing rows when the same (provider_code, task_slug, repo_name) combination appears again.
•	If DB fails:
o	Files on disk represent a proposed integration
o	run_status marks failure
o	report notes divergence
 
F.7 Dry Run and Repo Integration Semantics
Dry Run (IntegrationOptions.dry_run)
•	If dry_run=True:
o	persist_silver_checkpoint, persist_gold_checkpoint, and persist_run_outcome do not perform database writes.
o	apply_repo_integration_changes does not perform filesystem writes.
o	Each node still computes its summaries and metrics in memory and attaches them to plan and report_markdown.
o	state.persisted_ids stays empty.
Repo Integration Disabled (IntegrationOptions.repo_integration_enabled)
•	If repo_integration_enabled=False:
o	The graph MUST skip:
	analyze_repo_layout
	apply_repo_integration_changes
o	This is true even if state.repo_root is set.
Graph branching is governed by plan["use_repo"] as set in plan_run:
•	True → invoke repo nodes.
•	False → skip repo nodes.
 
Appendix G — Router and Settings Insertion Patterns
G.1 Overview
The repo-integration layer enforces deterministic, idempotent insertion of:
•	Router include blocks
•	Provider settings blocks
All modifications occur strictly within marker-defined regions in the target files and are computed in analyze_repo_layout_node as FileChange entries. Actual file writes occur only in apply_repo_integration_changes_node.
•	Marker-based router and settings insertion applies only when:
o	state.repo_profile.archetype == "fastapi_service", and
o	The corresponding router_file / router_registration_marker and
settings_file / settings_marker are present in
repo_profile.integration_hooks.
For other archetypes, no router/settings regions are created or modified.
 
G.2 Marker Conventions
Example markers:
# <AUTO_INTEGRATION_MARKER>
# <AUTO_INTEGRATION_SETTINGS_MARKER>
Generated blocks appear as:
# BEGIN AUTO-GENERATED INTEGRATION ROUTES
...
# END AUTO-GENERATED INTEGRATION ROUTES
And for settings:
# BEGIN AUTO-GENERATED INTEGRATION SETTINGS
...
# END AUTO-GENERATED INTEGRATION SETTINGS
If the block does not exist, it is created immediately after the marker.
Node responsibility:
•	analyze_repo_layout_node:
o	Reads existing router_file and settings_file.
o	Computes the complete contents for those files with regenerated blocks.
o	Emits FileChange entries with after equal to the new file contents and before=None.
•	apply_repo_integration_changes_node:
o	Sets before to the previously existing file contents (if any).
o	Sets change_type to "create" or "update".
o	Writes after contents to disk.
 
G.3 Route Insertion Rules
Inside the routes region for FastAPI archetypes:
# BEGIN AUTO-GENERATED INTEGRATION ROUTES
from .flows import <integration_slug_1>, <integration_slug_2>, ...

router.include_router(
    <integration_slug_1>.router,
    prefix=f"/integrations/{provider_code}/{integration_slug_1}",
)
...
# END AUTO-GENERATED INTEGRATION ROUTES
Rules:
•	provider_code = state.provider_code.
•	integration_slug = state.integration_task.task_slug.
•	Unique per (provider_code, integration_slug).
•	No duplicates.
•	Deterministic ordering (sorted lexicographically by (provider_code, integration_slug)).
•	The entire region is regenerated on each run; manual edits inside the region are overwritten.
analyze_repo_layout_node is responsible for regenerating the block; apply_repo_integration_changes_node only applies the computed FileChange.
Source of truth:
•	analyze_repo_layout_node derives:
o	provider_code from state.provider_code / integration_task.provider_code.
o	integration_slug from the relevant workflow CodeArtifact.rel_path and/or task slug.
 
G.4 Settings Insertion Rules
Inside the settings region (FastAPI archetype only):
# BEGIN AUTO-GENERATED INTEGRATION SETTINGS
INTEGRATIONS["stripe"] = ProviderSettings(
    api_base_url="https://api.stripe.com",
    timeout_s=30,
    retries=3,
)
...
# END AUTO-GENERATED INTEGRATION SETTINGS
Rules:
•	PROVIDER_CODE key is state.provider_code.
•	api_base_url resolution:
1.	If SourceSystem.base_url is set, use that.
2.	Else, if a known provider mapping exists, use it.
3.	Else, default to the scheme + host of SpecDocument.uri and log a warning.
•	Unique per provider code.
•	Deterministic ordering (sorted by provider code).
•	Region is fully regenerated every run.
For archetypes other than "fastapi_service", analyze_repo_layout_node MUST NOT attempt to read or write settings regions.
Source of truth:
•	analyze_repo_layout_node uses:
o	RepoProfile.settings_file
o	state.provider_code
o	Optionally values from configuration or state.plan.
 
G.5 Idempotency Guarantees
Idempotency is enforced through:
1. Full Block Regeneration
•	analyze_repo_layout_node rewrites the entire region between BEGIN/END tags for both routes and settings on every run.
2. Deterministic Sorting
•	Route entries sorted by (provider_code, integration_slug)
•	Settings entries sorted by provider_code
3. Uniqueness Keys
•	Internal sets keyed by (provider_code, integration_slug) and provider_code prevent duplicates.
4. Failure Isolation
•	If apply_repo_integration_changes fails, the filesystem remains unchanged.
•	If repo writes succeed but DB writes fail, run_status marks failure/partial and the report notes the divergence.
 
Appendix H — Knowledge Graph and Workflow Template Schema
H.1 Overview
The Knowledge Graph (KG) contains reusable templates that describe domain patterns for API integrations.

The KG is stored in the kg schema and is both read and written in v1. The workflow uses it as a shared store of provider workflows, steps, and endpoint bindings, and it updates this store after each run based on the integration flow and validation results. Governance fields on KG tables control which templates guide planning.

Used by:
•	align_task_with_kg
•	plan_integration_flow
•	persist_run_outcome (for KG learning and governance)
 
H.2 DDL (Authoritative Schema)
```sql
CREATE SCHEMA IF NOT EXISTS kg;

CREATE TABLE kg.workflow_templates (
    id              BIGSERIAL PRIMARY KEY,
    provider_code   TEXT NOT NULL,
    business_domain TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    summary_text    TEXT,
    summary_embedding VECTOR(1536),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_run_id TEXT,
    last_updated_by_run_id TEXT,
    review_status   TEXT NOT NULL DEFAULT 'pending', -- pending, approved, rejected
    review_notes    TEXT
);

CREATE UNIQUE INDEX uq_kg_workflow_templates_provider_domain_name
    ON kg.workflow_templates(provider_code, business_domain, name);

CREATE TABLE kg.workflow_steps (
    id                   BIGSERIAL PRIMARY KEY,
    workflow_template_id BIGINT NOT NULL REFERENCES kg.workflow_templates(id) ON DELETE CASCADE,
    step_order           INT NOT NULL,
    step_type            TEXT NOT NULL,
    description          TEXT,
    required             BOOLEAN NOT NULL DEFAULT TRUE,
    summary_text         TEXT,
    summary_embedding    VECTOR(1536)
);

CREATE INDEX ix_kg_workflow_steps_template_order
    ON kg.workflow_steps(workflow_template_id, step_order);

CREATE TABLE kg.step_bindings (
    id               BIGSERIAL PRIMARY KEY,
    workflow_step_id BIGINT NOT NULL REFERENCES kg.workflow_steps(id) ON DELETE CASCADE,
    spec_endpoint_id BIGINT NOT NULL REFERENCES spec_silver.endpoints(id),
    binding_confidence DOUBLE PRECISION NOT NULL,
    notes            TEXT,
    summary_embedding VECTOR(1536)
);

CREATE INDEX ix_kg_step_bindings_step
    ON kg.step_bindings(workflow_step_id);
```

This keeps embedding dimensions aligned with `text-embedding-3-small` and makes KG objects first-class retrieval targets.
 
H.3 Node Usage
align_task_with_kg
Inputs:
•	integration_task
•	Silver drafts (entities, endpoints)
•	Vector search across spec_chunks and KG text.
•	KG tables (kg.workflow_templates, kg.workflow_steps, kg.step_bindings).
Behavior:
1.	Filter templates by provider_code and business_domain. business_domain is derived from integration_task.constraints["business_domain"] if present; otherwise, the LLM infers it from the task description.
2.	Traverse the graph from those anchors using bounded breadth- or depth-first search (1–2 hops) to pull related workflow_templates, workflow_steps, and step_bindings.
3.	Within the reached neighborhood, rank candidates by description similarity using embeddings.
4.	Load workflow_steps and step_bindings for top templates.
5.	Write selected templates into plan["candidate_templates"].
6.	When the run leads to a refined or new workflow template that passes validation, the workflow writes or updates the corresponding KG rows and embeddings through persist_run_outcome so later tasks can reuse this pattern.
Fallback rules:
•	If no templates exist for the provider:
o	Set plan["candidate_templates"] = [].
o	Do not treat this as an error.
•	If templates exist but none meet a similarity threshold:
o	Also set plan["candidate_templates"] = [].
•	In both cases, append "align_task_with_kg" to completed_steps and rely on plan_integration_flow to perform heuristic-only planning.
 
plan_integration_flow
Inputs:
•	integration_task
•	Silver drafts
•	plan["candidate_templates"]
•	step_bindings from KG, if any.
Behavior:
•	If plan["candidate_templates"] is non-empty:
o	Turn workflow_steps → workflow_nodes (sorted by step_order).
o	Connect nodes sequentially via edges.
o	Use step_bindings to prebind endpoints.
o	Merge constraints from KG + task.
•	If plan["candidate_templates"] is empty:
o	Perform heuristic planning using only:
	The Silver model (endpoints, entities, events).
	Task description.
o	Produce a valid but KG-free integration flow.
In both cases, plan_integration_flow must:
•	Produce at least one start and terminal node or record a clear error in state.errors.
•	Append "plan_integration_flow" on success.
 
H.4 Lifecycle and Ownership
The KG is shared infrastructure across LangGraph runs in v1. Each successful run may refine or add workflow templates and bindings and record embeddings for them.

For each successful run:
•	align_task_with_kg proposes candidate templates and scores.
•	plan_integration_flow writes a normalized flow graph in the Gold tables.
•	validate_integration_design writes quality flags.

persist_gold_checkpoint writes integration-specific flows into integration_gold.* tables.

persist_run_outcome then writes or updates KG rows as follows:
•	If no template in kg.workflow_templates matches (provider_code, business_domain, name), insert a new row with review_status='pending', created_by_run_id=<run_id>, and summary_embedding filled from summary_text.
•	If a template matches and the new run has higher coverage or a better validation score, update description, summary_text, and summary_embedding, set last_updated_by_run_id=<run_id>, and keep review_status unchanged if it is 'approved' or 'rejected'.
•	For each template with review_status='pending', reviewers can change review_status to 'approved' or 'rejected' and add review_notes.

align_task_with_kg reads only templates with is_active=TRUE and review_status in ('approved','pending'), with a higher weight for approved templates. Templates with review_status='rejected' remain in the schema for audit but do not guide planning.
 
H.5 Integration Flow Node Semantics
Allowed node_type values
IntegrationFlowNode.node_type MUST be one of:
•	"start"
•	"end"
•	"api_call"
•	"decision"
•	"transform"
•	"webhook_wait"
plan_integration_flow MUST NOT emit any other values in v1.
Valid flow requirements
A valid integration flow MUST satisfy:
•	Exactly one start node.
•	At least one end node.
•	All nodes except the start node have at least one incoming edge.
•	All nodes except end nodes have at least one outgoing edge.
•	position:
o	Non-negative integer representing a topological order.
o	Strictly increasing along any path from start to end.
Endpoint binding expectations
•	Every node with node_type="api_call" MUST:
o	Have endpoint_id set to a valid spec_silver.endpoints.id.
o	Have exactly one EndpointBinding row with:
	Matching task_id.
	flow_node_key equal to the node’s node_key.
	endpoint_id equal to the node’s endpoint_id.
validate_integration_design is responsible for enforcing these constraints.
EndpointBindings are initially created as scaffolds by plan_integration_flow and later completed/refined by generate_code_and_tests.
 
 
Appendix I — Runtime Stack for Generated Code and Tests
I.1 Overview
Generated integrations use a uniform runtime stack.
This ensures predictable behavior across repos, consistent testing, and maintainable generated code.
 
I.2 HTTP Client Layer
All outbound calls MUST use httpx via a shared IntegrationHttpClient defined at:

# src/integrations/clients/http_client.py
import httpx

class IntegrationHttpClient:
    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout_s: int = 30,
        retries: int = 3,
    ):
        ...

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        ...

Generated code MUST import it as:
from integrations.clients.http_client import IntegrationHttpClient
No other HTTP client libraries may be used in generated code.
 
I.3 Error Handling Model
Generated code raises only the shared exception hierarchy:
•	IntegrationError
•	TransientIntegrationError
•	AuthIntegrationError
Endpoint-specific exceptions may wrap these base types, but new standalone exception trees are not allowed.
 
I.4 Logging Requirements
•	Use Python’s standard logging library.
•	Use module-level loggers.
•	Emit structured fields such as:
o	provider_code
o	workflow_name
o	request_id
o	status_code
•	Do not configure global logging in generated code.
 
I.5 Test Generation Requirements
All generated tests must:
•	Use pytest
•	Use a shared mock fixture for:
o	IntegrationHttpClient
o	or a mocked httpx transport
•	Run offline and deterministically
•	Include per-workflow:
o	at least one happy-path test
o	at least one negative-path test
This ensures behavioral coverage without external dependencies.
 
I.6 Enforcement
The generate_code_and_tests node:
•	Applies strict prompt templates
•	Validates generated artifacts
•	Rejects outputs that violate mandatory stack constraints
This establishes a consistent, enforceable standard across all integrations.
 
I.7 Generated Provider Clients
For each provider_code, generate a client class in integrations/clients/<provider_code>.py:
class <ProviderCodeCamelCase>Client:
    def __init__(self, http: IntegrationHttpClient):
        self.http = http
•	Example: provider_code="stripe" → StripeClient in integrations/clients/stripe.py.
All provider-specific HTTP calls MUST go through methods on this client, which delegate to IntegrationHttpClient.request.
 
I.8 Workflow Modules
Each workflow module integrations/flows/<integration_slug>.py MUST expose:
from fastapi import APIRouter
from integrations.clients.<provider_code> import <ProviderCodeCamelCase>Client

def build_router() -> APIRouter:
    router = APIRouter()

    @router.post("/")
    def main_endpoint(...):
        ...
    return router

router = build_router()
Requirements:
•	There MUST be at least one route with path "/" relative to the router prefix.
•	Router prefix is determined by the router insertion rules (Appendix G) using
provider_code and integration_slug (task_slug).
 
I.9 Test Modules
Each test module tests/integrations/test_<integration_slug>.py MUST:
•	Import:
•	import pytest
•	from integrations.flows.<integration_slug> import router
•	from integrations.clients.http_client import IntegrationHttpClient
•	Define at least:
o	test_happy_path_<integration_slug>()
o	test_error_path_<integration_slug>()
•	Use a pytest fixture named mock_http_client that returns an
IntegrationHttpClient instance backed by a mocked httpx transport (or
equivalent), so tests are offline and deterministic.
Existing error-handling and logging requirements in I.3–I.5 still apply and
must be enforced by generate_code_and_tests.

Appendix J — TOON and YAML Format

J.1 Overview
TOON (Token-Oriented Object Notation) is the shared structured format for communication between nodes and models. The system encodes TOON as YAML. YAML is chosen for clarity, stable token patterns, and easy diff/review of generated payloads.

J.2 Structural Rules
•	TOON objects map directly to the domain dataclasses for Silver, Gold, and repo types.
•	Field names match the Python attribute names and the database column names.
•	Optional fields use explicit null or omission.
•	Lists appear as YAML sequences.

J.3 Usage in Nodes
•	Extraction nodes produce TOON records that represent schemas, entities, events, and endpoints.
•	Planning nodes read TOON snapshots of Silver, KG entries, and repo context and write TOON plans for workflows and policies.
•	Code generation nodes receive TOON graphs and bindings and write TOON summaries of generated artifacts for validation and reporting.

The parser maps TOON YAML into dataclasses at the edge of each node. Node code does not parse free-form JSON.
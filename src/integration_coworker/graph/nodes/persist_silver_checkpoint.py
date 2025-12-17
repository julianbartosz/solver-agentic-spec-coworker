"""
Persist Silver Checkpoint Node

Per design doc Section 5.4 and Appendix C.3:
This checkpoint persists all Silver-layer data after build_silver_api_model and embed_spec_chunks.

Writes to:
- source_systems
- spec_documents
- spec_sections
- schemas, fields
- endpoints, endpoint_parameters
- entities, entity_relationships
- events
- spec_chunks (with embeddings)

Backfills IDs into state objects for downstream use.

Supports both Postgres (primary) and SQLite (fallback) using sql_helpers.

V3 Streaming Mode:
When STREAMING_PERSISTENCE=true, chunks and embeddings are already persisted
by ingest_spec and embed_spec_chunks. This node skips chunk persistence
and only handles metadata (endpoints, schemas, entities, etc.).
"""
from datetime import datetime, timezone
import json
import logging

from integration_coworker.graph.state import WorkflowState
from integration_coworker.persistence import db
from integration_coworker.persistence.sql_helpers import (
    upsert_ignore, select_by_columns, get_engine_type
)
from integration_coworker.graph.nodes.build_silver_file_model import (
    _persist_file_specs,
    _persist_to_kg,
)
from integration_coworker.config import is_streaming_persistence_enabled

logger = logging.getLogger(__name__)

# Schema prefix for Postgres tables
SILVER_SCHEMA = "spec_silver"


def persist_silver_checkpoint(state: WorkflowState) -> WorkflowState:
    """
    Persist Silver layer data to database.
    
    Per design doc Appendix C.3:
    - Runs after build_silver_api_model and embed_spec_chunks
    - Only persist_* nodes may write to the database
    - Backfills IDs on in-memory objects
    
    V3 Streaming Mode:
    When chunks are already streamed (persisted_ids["chunks_streamed"] == True),
    this node skips chunk persistence and only handles metadata.
    
    Reads: source_system, spec_documents, spec_sections, endpoints, schemas,
           entities, relationships, events, spec_chunk_embeddings
    Writes: persisted_ids (silver subset), backfills IDs in state objects
    """
    is_dry_run = state.options.dry_run if state.options else False
    chunks_already_streamed = state.persisted_ids.get("chunks_streamed", False)
    embeddings_already_streamed = state.persisted_ids.get("embeddings_streamed", False)

    if is_dry_run:
        # Don't write to DB, just log what would be persisted
        chunk_count = state.chunk_count if chunks_already_streamed else len(state.spec_chunk_embeddings)
        state.persisted_ids.update({
            "silver_dry_run": True,
            "would_persist_silver": {
                "source_system": 1 if state.source_system else 0,
                "spec_documents": len(state.spec_documents),
                "spec_sections": len(state.spec_sections),
                "endpoints": len(state.endpoints),
                "schemas": len(state.schemas),
                "entities": len(state.entities),
                "file_specs": len(state.file_specs),
                "file_fields": len(state.file_fields),
                "spec_chunks": len(state.spec_chunk_embeddings),
            },
        })
        state.completed_steps.append("persist_silver_checkpoint")
        return state

    try:
        # Initialize schema if needed
        db.init_schema()
        conn = db.get_connection()  # Uses Postgres or SQLite based on config
        cur = conn.cursor()

        # Determine engine type for schema prefixes
        engine = get_engine_type()
        schema = SILVER_SCHEMA if engine == "postgres" else None

        # 1. Upsert SourceSystem
        provider_code = state.provider_code or "unknown"
        sql = upsert_ignore("source_systems", ["code", "display_name"], ["code"], schema)
        cur.execute(sql, (provider_code, provider_code.replace("_", " ").title()))

        sql = select_by_columns("source_systems", ["id"], ["code"], schema)
        cur.execute(sql, (provider_code,))
        source_system_id = cur.fetchone()[0]

        # Backfill into state.source_system if exists
        if state.source_system:
            state.source_system.id = source_system_id

        # 2. Insert SpecDocuments
        spec_document_ids = {}
        for spec_doc in state.spec_documents:
            sql = upsert_ignore(
                "spec_documents",
                ["source_system_id", "uri", "sha256", "content_type"],
                ["source_system_id", "sha256"],
                schema
            )
            cur.execute(sql, (source_system_id, spec_doc.uri, spec_doc.sha256, spec_doc.content_type))

            sql = select_by_columns("spec_documents", ["id"], ["source_system_id", "sha256"], schema)
            cur.execute(sql, (source_system_id, spec_doc.sha256))
            spec_document_id = cur.fetchone()[0]
            spec_doc.id = spec_document_id
            spec_doc.source_system_id = source_system_id
            spec_document_ids[spec_doc.uri] = spec_document_id

        # Use first spec_document_id as primary for backward compat
        primary_spec_document_id = state.spec_documents[0].id if state.spec_documents else None

        # 3. Insert SpecSections
        for section in state.spec_sections:
            doc_id = section.spec_document_id or primary_spec_document_id
            sql = upsert_ignore(
                "spec_sections",
                ["spec_document_id", "section_type", "title", "path", "start_offset", "end_offset", "content"],
                ["spec_document_id", "section_type", "path"],
                schema
            )
            cur.execute(sql, (doc_id, section.section_type, section.title, section.path,
                 section.start_offset, section.end_offset, section.content))

            # Backfill ID
            sql = select_by_columns("spec_sections", ["id"], ["spec_document_id", "section_type", "path"], schema)
            cur.execute(sql, (doc_id, section.section_type, section.path or ""))
            row = cur.fetchone()
            if row:
                section.id = row[0]

        # 4. Insert Schemas
        schema_ids_by_name = {}
        for schema_obj in state.schemas:
            sql = upsert_ignore("schemas", ["source_system_id", "name", "ref"], ["source_system_id", "name"], schema)
            cur.execute(sql, (source_system_id, schema_obj.name, schema_obj.ref))

            sql = select_by_columns("schemas", ["id"], ["source_system_id", "name"], schema)
            cur.execute(sql, (source_system_id, schema_obj.name))
            schema_id = cur.fetchone()[0]
            schema_obj.id = schema_id
            schema_obj.source_system_id = source_system_id
            schema_ids_by_name[schema_obj.name] = schema_id

        # 5. Insert SchemaFields
        for field in state.schema_fields:
            if field.schema_id is None and hasattr(field, 'schema_name'):
                field.schema_id = schema_ids_by_name.get(field.schema_name)
            if field.schema_id:
                json_path = field.json_path or f"$.{field.name}"
                sql = upsert_ignore(
                    "fields",
                    ["schema_id", "name", "json_path", "type", "format", "required", "description"],
                    ["schema_id", "json_path"],
                    schema
                )
                cur.execute(sql, (field.schema_id, field.name, json_path,
                     field.field_type or field.type, field.format,
                     1 if field.required else 0, field.description))

        # 6. Insert Endpoints
        for endpoint in state.endpoints:
            # Determine spec_document_id from endpoint source URI
            doc_id = endpoint.spec_document_id or primary_spec_document_id
            if hasattr(endpoint, '_source_uri') and endpoint._source_uri:
                doc_id = spec_document_ids.get(endpoint._source_uri, doc_id)

            sql = upsert_ignore(
                "endpoints",
                ["source_system_id", "spec_document_id", "method", "path", "operation_id", "summary", "description"],
                ["source_system_id", "spec_document_id", "path", "method"],
                schema
            )
            cur.execute(sql, (source_system_id, doc_id, endpoint.method, endpoint.path,
                 endpoint.operation_id, endpoint.summary, endpoint.description))

            sql = select_by_columns("endpoints", ["id"], ["source_system_id", "spec_document_id", "method", "path"], schema)
            cur.execute(sql, (source_system_id, doc_id, endpoint.method, endpoint.path))
            endpoint_id = cur.fetchone()[0]
            endpoint.id = endpoint_id
            endpoint.source_system_id = source_system_id
            endpoint.spec_document_id = doc_id

        # 7. Insert EndpointParameters
        endpoint_ids_by_key = {(e.method, e.path): e.id for e in state.endpoints}
        for param in state.endpoint_parameters:
            if param.endpoint_id is None and hasattr(param, 'endpoint_method') and hasattr(param, 'endpoint_path'):
                param.endpoint_id = endpoint_ids_by_key.get((param.endpoint_method, param.endpoint_path))
            if param.endpoint_id:
                sql = upsert_ignore(
                    "endpoint_parameters",
                    ["endpoint_id", "name", "location", "required", "schema_ref", "description"],
                    ["endpoint_id", "name", "location"],
                    schema
                )
                cur.execute(sql, (param.endpoint_id, param.name, param.location,
                     1 if param.required else 0, param.schema_ref, param.description))

        # 8. Insert Entities
        entity_ids_by_name = {}
        for entity in state.entities:
            sql = upsert_ignore("entities", ["source_system_id", "name", "description"], ["source_system_id", "name"], schema)
            cur.execute(sql, (source_system_id, entity.name, entity.description))

            sql = select_by_columns("entities", ["id"], ["source_system_id", "name"], schema)
            cur.execute(sql, (source_system_id, entity.name))
            entity_id = cur.fetchone()[0]
            entity.id = entity_id
            entity.source_system_id = source_system_id
            entity_ids_by_name[entity.name] = entity_id

        # 9. Insert EntityRelationships
        for rel in state.relationships:
            if rel.source_entity_id and rel.target_entity_id:
                sql = upsert_ignore(
                    "entity_relationships",
                    ["source_system_id", "from_entity_id", "to_entity_id", "relationship_type"],
                    ["source_system_id", "from_entity_id", "to_entity_id", "relationship_type"],
                    schema
                )
                cur.execute(sql, (source_system_id, rel.source_entity_id, rel.target_entity_id, rel.relationship_type))

        # 10. Insert Events
        for event in state.events:
            sql = upsert_ignore("events", ["source_system_id", "name", "description"], ["source_system_id", "name"], schema)
            cur.execute(sql, (source_system_id, event.name, event.description))

            sql = select_by_columns("events", ["id"], ["source_system_id", "name"], schema)
            cur.execute(sql, (source_system_id, event.name))
            event_id = cur.fetchone()[0]
            event.id = event_id

        # 11. Insert File Specs and Fields (Silver file model)
        if state.file_specs:
            for spec in state.file_specs:
                if getattr(spec, "source_system_id", None) is None:
                    spec.source_system_id = source_system_id
            try:
                _persist_file_specs(state, state.file_specs, state.file_fields)
            except Exception as e:
                logger.error(f"Failed to persist file specs: {e}", exc_info=True)
                raise
            try:
                _persist_to_kg(state, state.file_specs, state.file_fields, state.parsed_specs)
            except Exception as e:
                logger.warning(f"KG persistence failed (non-blocking): {e}")

        # 12. Insert SpecChunks with embeddings
        # V3 Streaming Mode: Skip if chunks were already streamed by ingest_spec
        if chunks_already_streamed:
            logger.info(f"Skipping chunk persistence - already streamed ({state.chunk_count} chunks)")
            chunk_count = state.chunk_count
        else:
            # Legacy mode: persist chunks from state.spec_chunk_embeddings
            # Build URI -> spec_document_id mapping for multi-spec support
            chunk_to_uri = {}
            if state.plan and "chunk_index_to_spec_document_uri" in state.plan:
                chunk_to_uri = state.plan["chunk_index_to_spec_document_uri"]

            for chunk in state.spec_chunk_embeddings:
                # Resolve spec_document_id
                doc_id = chunk.spec_document_id
                if doc_id is None:
                    # Try to get from mapping
                    chunk_uri = chunk_to_uri.get(chunk.chunk_index)
                    if chunk_uri:
                        doc_id = spec_document_ids.get(chunk_uri)
                    if doc_id is None:
                        doc_id = primary_spec_document_id

                # Use full content if available, otherwise use preview
                content = getattr(chunk, '_full_content', chunk.content)
                embedding_json = json.dumps(chunk.embedding) if chunk.embedding else None

                sql = upsert_ignore(
                    "spec_chunks",
                    ["spec_document_id", "chunk_index", "content", "embedding"],
                    ["spec_document_id", "chunk_index"],
                    schema
                )
                cur.execute(sql, (doc_id, chunk.chunk_index, content, embedding_json))

                sql = select_by_columns("spec_chunks", ["id"], ["spec_document_id", "chunk_index"], schema)
                cur.execute(sql, (doc_id, chunk.chunk_index))
                row = cur.fetchone()
                if row:
                    chunk.id = row[0]
                    chunk.spec_document_id = doc_id
            
            chunk_count = len(state.spec_chunk_embeddings)

        conn.commit()
        conn.close()

        # Update persisted_ids
        # V3: Use chunk_count variable which accounts for streaming mode
        embedding_count = state.embedding_count if embeddings_already_streamed else len(state.spec_chunk_embeddings)
        
        state.persisted_ids.update({
            "silver_checkpoint": "completed",
            "source_system_id": source_system_id,
            "spec_document_id": primary_spec_document_id,
            "spec_document_ids": spec_document_ids,  # URI -> ID mapping for multi-spec
            "spec_document_count": len(state.spec_documents),
            "endpoint_count": len(state.endpoints),
            "schema_count": len(state.schemas),
            "entity_count": len(state.entities),
            "file_spec_count": len(state.file_specs),
            "file_field_count": len(state.file_fields),
            "spec_chunk_count": chunk_count,
            "embedding_count": embedding_count,
            "streaming_mode": chunks_already_streamed,
            "silver_timestamp": datetime.now(timezone.utc).isoformat(),
        })

        state.completed_steps.append("persist_silver_checkpoint")
        mode_str = " (streaming mode)" if chunks_already_streamed else ""
        logger.info(f"Silver checkpoint persisted{mode_str}: {len(state.endpoints)} endpoints, {len(state.schemas)} schemas, {chunk_count} chunks")
        return state

    except Exception as e:
        state.errors.append(f"Silver checkpoint failed: {str(e)}")
        state.persisted_ids.update({
            "silver_checkpoint": "failed",
            "silver_error": str(e),
        })
        logger.error(f"Silver checkpoint failed: {e}")
        return state

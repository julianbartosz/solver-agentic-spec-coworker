"""
build_silver_file_model node — constructs Silver File Model from parsed file specs.

Implements: File Integration V1 - parallel to build_silver_api_model.py
Touches: file_specs, file_fields, record_layouts, file_validation_rules

This node processes file specifications from sources like CSV, Excel, and PDF
data guides, and persists them to the database. It operates in parallel with
the API model building pipeline.

V1 Integration Changes:
  - Consumes state.parsed_specs (List[ParsedSpec] from detect_and_parse_spec)
  - Only processes ParsedSpec objects with source_type == FILE
  - Does NOT handle dict fallbacks - that was a bug mask
  - Produces typed FileSpec/FileField objects only
  
KG Integration (Bucket 2):
  - Persists FILE_SPEC and FILE_FIELD nodes to kg.nodes
  - Creates HAS_FIELD edges from spec to fields
  - Creates DERIVES_FROM_GUIDE edges if PDF guide was used
  - Stores embeddings on field nodes for similarity search (optional)
"""

import json
import logging
from typing import List, Optional, Dict, Any

from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import (
    FileSpec,
    FileField,
    RecordLayout,
    FileValidationRule,
)
from integration_coworker.sources.base import ParsedSpec, SourceType

logger = logging.getLogger(__name__)


def build_silver_file_model(state: WorkflowState) -> WorkflowState:
    """
    Build Silver File Model from parsed file specs.
    
    This node:
    1. Reads state.parsed_specs (populated by detect_and_parse_spec from CSV/Excel/PDF sources)
    2. Filters for SourceType.FILE only (ignores API specs)
    3. Assigns source_system_id from state.source_system
    4. Produces FileSpec/FileField objects in memory (DB persistence happens in persist_silver_checkpoint)
    5. Updates state.file_specs and state.file_fields with typed objects
    
    The node is idempotent and side-effect free with respect to persistence.
    
    Args:
        state: WorkflowState with parsed_specs potentially populated
        
    Returns:
        Updated WorkflowState with file_specs and file_fields
    """
    # Filter for FILE type ParsedSpecs only
    file_parsed_specs = [
        ps for ps in state.parsed_specs 
        if isinstance(ps, ParsedSpec) and ps.source_type == SourceType.FILE
    ]
    
    if not file_parsed_specs:
        logger.debug("No file specs in parsed_specs to process")
        state.completed_steps.append("build_silver_file_model")
        return state
    
    logger.info(f"Processing {len(file_parsed_specs)} file spec(s) from parsed_specs")
    
    # Ensure source_system exists
    if not state.source_system or not state.source_system.id:
        state.errors.append(
            "build_silver_file_model: No source_system available. "
            "Ensure source system is created before processing file specs."
        )
        state.completed_steps.append("build_silver_file_model")
        return state
    
    source_system_id = state.source_system.id
    
    # Extract FileSpec and FileField from ParsedSpec.data
    # ParsedSpec.data for FILE sources is: {"file_spec": FileSpec, "fields": List[FileField]}
    processed_specs: List[FileSpec] = []
    all_fields: List[FileField] = []
    
    for parsed_spec in file_parsed_specs:
        if not parsed_spec.is_valid():
            logger.warning(f"Skipping invalid ParsedSpec from {parsed_spec.source_uri}: {parsed_spec.errors}")
            continue
        
        data = parsed_spec.data
        if not isinstance(data, dict):
            logger.error(f"ParsedSpec.data is not a dict for {parsed_spec.source_uri}")
            continue
        
        file_spec = data.get("file_spec")
        fields = data.get("fields", [])
        
        if not isinstance(file_spec, FileSpec):
            logger.error(f"ParsedSpec.data['file_spec'] is not a FileSpec for {parsed_spec.source_uri}")
            continue
        
        # Assign source_system_id
        file_spec.source_system_id = source_system_id
        processed_specs.append(file_spec)
        
        # Validate and collect fields
        for f in fields:
            if isinstance(f, FileField):
                all_fields.append(f)
            else:
                logger.warning(f"Field {f} is not a FileField, skipping")
    
    if not processed_specs:
        logger.debug("No valid file specs after extraction")
        state.completed_steps.append("build_silver_file_model")
        return state
    
    # Update state with NEW lists (avoid mutation of input lists)
    state.file_specs = list(processed_specs)
    state.file_fields = list(all_fields)

    # Persist to primary store (and optionally KG) so downstream steps and
    # observability have durable state. Keep idempotent semantics via ON CONFLICT.
    try:
        _persist_file_specs(state, state.file_specs, state.file_fields)
    except Exception as e:  # pragma: no cover - surfaced to tests via state.errors
        logger.exception("Failed to persist file specs")
        state.errors.append(f"build_silver_file_model persist failed: {e}")
        state.completed_steps.append("build_silver_file_model")
        return state

    try:
        _persist_to_kg(state, state.file_specs, state.file_fields, file_parsed_specs)
    except Exception as e:  # pragma: no cover
        logger.warning(f"KG persistence failed: {e}")

    logger.info(
        f"build_silver_file_model completed: "
        f"{len(processed_specs)} specs, {len(all_fields)} fields"
    )
    state.completed_steps.append("build_silver_file_model")
    return state


def _persist_file_specs(
    state: WorkflowState,
    file_specs: List[FileSpec],
    file_fields: List[FileField],
) -> None:
    """
    Persist file specs and fields to database.
    
    Uses the same persistence layer as the API model.
    """
    from integration_coworker.persistence.db import get_connection, get_engine_type
    
    engine = get_engine_type()
    
    with get_connection() as conn:
        cur = conn.cursor()
        
        for spec in file_specs:
            # Insert file_spec
            if engine == "postgres":
                cur.execute("""
                    INSERT INTO spec_silver.file_specs 
                    (source_system_id, name, file_type, spec_document_id, encoding,
                     delimiter, header_row, line_terminator, quote_char, escape_char,
                     description, version, sample_uri)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (source_system_id, name) 
                    DO UPDATE SET 
                        file_type = EXCLUDED.file_type,
                        encoding = EXCLUDED.encoding,
                        delimiter = EXCLUDED.delimiter,
                        header_row = EXCLUDED.header_row,
                        description = EXCLUDED.description
                    RETURNING id
                """, (
                    spec.source_system_id,
                    spec.name,
                    spec.file_type,
                    spec.spec_document_id,
                    spec.encoding,
                    spec.delimiter,
                    spec.has_header,
                    spec.line_terminator,
                    spec.quote_char,
                    spec.escape_char,
                    spec.description,
                    spec.version,
                    spec.sample_uri,
                ))
                row = cur.fetchone()
                spec.id = row[0] if row else None
            else:
                # SQLite
                cur.execute("""
                    INSERT OR REPLACE INTO file_specs 
                    (source_system_id, name, file_type, spec_document_id, encoding,
                     delimiter, has_header, line_terminator, quote_char, escape_char,
                     description, version, sample_uri)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    spec.source_system_id,
                    spec.name,
                    spec.file_type,
                    spec.spec_document_id,
                    spec.encoding,
                    spec.delimiter,
                    1 if spec.has_header else 0,
                    spec.line_terminator,
                    spec.quote_char,
                    spec.escape_char,
                    spec.description,
                    spec.version,
                    spec.sample_uri,
                ))
                spec.id = cur.lastrowid
            
            # Insert fields for this spec
            spec_fields = [f for f in file_fields if f.file_spec_id is None or f.file_spec_id == spec.id]
            for field in spec_fields:
                field.file_spec_id = spec.id
                
                if engine == "postgres":
                    cur.execute("""
                        INSERT INTO spec_silver.file_fields 
                        (file_spec_id, name, field_type, position, start_position,
                         length, format_mask, nullable, default_value, validation_regex,
                         description, sample_values, inference_confidence)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (file_spec_id, name) 
                        DO UPDATE SET 
                            field_type = EXCLUDED.field_type,
                            position = EXCLUDED.position
                        RETURNING id
                    """, (
                        field.file_spec_id,
                        field.name,
                        field.field_type,
                        field.position,
                        field.start_position,
                        field.length,
                        field.format_mask,
                        field.nullable,
                        field.default_value,
                        field.validation_regex,
                        field.description,
                        json.dumps(field.sample_values) if field.sample_values else '[]',
                        field.inference_confidence,
                    ))
                    row = cur.fetchone()
                    field.id = row[0] if row else None
                else:
                    # SQLite
                    cur.execute("""
                        INSERT OR REPLACE INTO file_fields 
                        (file_spec_id, name, field_type, position, start_position,
                         length, format_mask, nullable, default_value, validation_regex,
                         description, sample_values, inference_confidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        field.file_spec_id,
                        field.name,
                        field.field_type,
                        field.position,
                        field.start_position,
                        field.length,
                        field.format_mask,
                        1 if field.nullable else 0,
                        field.default_value,
                        field.validation_regex,
                        field.description,
                        json.dumps(field.sample_values) if field.sample_values else '[]',
                        field.inference_confidence,
                    ))
                    field.id = cur.lastrowid
        
        conn.commit()
        
        # Track persisted IDs
        state.persisted_ids["file_spec_ids"] = [s.id for s in file_specs]
        state.persisted_ids["file_field_ids"] = [f.id for f in file_fields]
        
        logger.debug(
            f"Persisted {len(file_specs)} file specs, "
            f"{len(file_fields)} file fields"
        )


def _persist_to_kg(
    state: WorkflowState,
    file_specs: List[FileSpec],
    file_fields: List[FileField],
    parsed_specs: List[ParsedSpec],
) -> None:
    """
    Persist file specs and fields to Knowledge Graph (PostgreSQL only).
    
    Creates:
    - FILE_SPEC nodes for each file spec
    - FILE_FIELD nodes for each field
    - HAS_FIELD edges (FILE_SPEC -> FILE_FIELD)
    - DERIVES_FROM_GUIDE edges if guide_uri is available in ParsedSpec metadata
    
    This is a non-critical operation. Failures are logged but do not block
    the main workflow. The KG provides provenance tracking and enables
    field similarity search via pgvector embeddings.
    
    Args:
        state: Workflow state containing connection info
        file_specs: Persisted FileSpec instances with IDs
        file_fields: Persisted FileField instances with IDs
        parsed_specs: Original ParsedSpec instances (may contain guide_uri in metadata)
    """
    from integration_coworker.persistence.db import get_connection, get_engine_type
    
    # KG requires PostgreSQL with pgvector
    if get_engine_type() != "postgres":
        logger.debug("KG persistence skipped: requires PostgreSQL")
        return
    
    try:
        from integration_coworker.kg.persist import persist_file_spec_with_fields
    except ImportError as e:
        logger.warning(f"KG persist module not available: {e}")
        return
    
    # Build mapping from spec name to guide_uri (from parsed spec metadata)
    spec_to_guide_uri: dict[str, str | None] = {}
    spec_to_guide_fields: dict[str, list] = {}
    for parsed_spec in parsed_specs:
        if parsed_spec.data and "file_spec" in parsed_spec.data:
            fs = parsed_spec.data["file_spec"]
            if hasattr(fs, "name"):
                # Check for guide_uri in metadata
                guide_uri = None
                if parsed_spec.metadata:
                    guide_uri = parsed_spec.metadata.get("guide_uri")
                spec_to_guide_uri[fs.name] = guide_uri
                spec_to_guide_fields[fs.name] = parsed_spec.data.get("guide_fields", []) if isinstance(parsed_spec.data, dict) else []

    # Optional: enable embeddings via env flag (defaults to off to avoid external calls)
    from integration_coworker.config import get_settings

    compute_embeddings = get_settings().kg_compute_field_embeddings
    
    # Persist each file spec with its fields to KG
    with get_connection() as conn:
        kg_nodes_created = 0
        
        for spec in file_specs:
            # Get fields for this spec
            spec_fields = [f for f in file_fields if f.file_spec_id == spec.id]
            
            # Get guide_uri if available
            guide_uri = spec_to_guide_uri.get(spec.name)
            
            try:
                spec_node_id, field_node_ids = persist_file_spec_with_fields(
                    conn=conn,
                    file_spec=spec,
                    fields=spec_fields,
                    guide_uri=guide_uri,
                    guide_fields=spec_to_guide_fields.get(spec.name, []),
                    compute_embeddings=compute_embeddings,
                )
                kg_nodes_created += 1 + len(field_node_ids)
                
                logger.debug(
                    f"KG: Persisted spec '{spec.name}' (node={spec_node_id}) "
                    f"with {len(field_node_ids)} fields"
                )
            except Exception as e:
                # Log per-spec errors but continue with others
                logger.warning(f"KG: Failed to persist spec '{spec.name}': {e}")
        
        conn.commit()
        
        # Track KG node counts in state
        if "kg_nodes_created" not in state.persisted_ids:
            state.persisted_ids["kg_nodes_created"] = 0
        state.persisted_ids["kg_nodes_created"] += kg_nodes_created
        
        logger.info(f"KG persistence completed: {kg_nodes_created} nodes created/updated")

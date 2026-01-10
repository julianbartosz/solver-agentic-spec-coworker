"""
KG Persistence Module - Idempotent upsert functions for Knowledge Graph nodes and edges.

This module provides low-level primitives for persisting FileSpec/FileField entities
to the KG layer with provenance edges (DERIVES_FROM_GUIDE, MAPS_TO, HAS_FIELD).

Design Decisions:
  - Natural keys ensure idempotency: (node_type, key) for nodes, (src, dst, rel) for edges
  - Embeddings are computed lazily only when needed for similarity search
  - PostgreSQL-only (KG features require pgvector)
  - All upserts use ON CONFLICT DO UPDATE for true idempotency

Natural Key Conventions:
  - FILE_SPEC:    file_spec.{source_system_id}.{spec_name}[.{sheet_name}]
  - FILE_FIELD:   file_field.{file_spec_key}.{field_name}
  - GUIDE_FIELD:  guide_field.{guide_uri_hash}.{field_name}
  - RECORD_LAYOUT: record_layout.{file_spec_key}.{layout_name}
"""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from integration_coworker.domain.models import (
    KGNode,
    KGEdge,
    KGNodeType,
    KGEdgeRelation,
    FileSpec,
    FileField,
)
from integration_coworker.persistence import db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Natural Key Builders
# ---------------------------------------------------------------------------

def build_file_spec_key(source_system_id: int, spec_name: str, sheet_name: Optional[str] = None) -> str:
    """
    Build canonical natural key for a FILE_SPEC node.
    
    Format: file_spec.{source_system_id}.{spec_name}[.{sheet_name}]
    """
    base = f"file_spec.{source_system_id}.{spec_name}"
    if sheet_name:
        return f"{base}.{sheet_name}"
    return base


def build_file_field_key(file_spec_key: str, field_name: str) -> str:
    """
    Build canonical natural key for a FILE_FIELD node.
    
    Format: file_field.{file_spec_key}.{field_name}
    """
    return f"file_field.{file_spec_key}.{field_name}"


def build_guide_field_key(guide_uri: str, field_name: str) -> str:
    """
    Build canonical natural key for a GUIDE_FIELD node (source of truth).
    
    Format: guide_field.{uri_hash8}.{field_name}
    """
    uri_hash = hashlib.sha256(guide_uri.encode()).hexdigest()[:8]
    return f"guide_field.{uri_hash}.{field_name}"


def build_record_layout_key(file_spec_key: str, layout_name: str) -> str:
    """
    Build canonical natural key for a RECORD_LAYOUT node.
    
    Format: record_layout.{file_spec_key}.{layout_name}
    """
    return f"record_layout.{file_spec_key}.{layout_name}"


# ---------------------------------------------------------------------------
# Core Upsert Functions
# ---------------------------------------------------------------------------

def upsert_node(
    conn,
    node_type: KGNodeType,
    natural_key: str,
    properties: Optional[Dict[str, Any]] = None,
    embedding: Optional[List[float]] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> int:
    """
    Upsert a KG node with idempotent ON CONFLICT behavior.
    
    Args:
        conn: Database connection (psycopg2)
        node_type: KGNodeType enum value (FILE_SPEC, FILE_FIELD, etc.)
        natural_key: Unique key for this node type (see build_*_key functions)
        properties: JSON-serializable dict of node properties
        embedding: Optional 1536-dim vector for similarity search
        name: Human-readable name (defaults to last segment of natural_key)
        description: Optional description text
        
    Returns:
        node_id: The ID of the upserted node
        
    Note:
        Uses ON CONFLICT (node_type, key) DO UPDATE to ensure idempotency.
        Properties are merged (new values overwrite old), not replaced.
    """
    cur = conn.cursor()
    
    props_json = json.dumps(properties) if properties else '{}'
    # Default name to last segment of natural key if not provided
    node_name = name or natural_key.split(".")[-1]
    
    if embedding:
        # With embedding vector
        cur.execute("""
            INSERT INTO kg.nodes (node_type, key, name, description, properties, embedding)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::vector)
            ON CONFLICT (node_type, key) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, kg.nodes.name),
                description = COALESCE(EXCLUDED.description, kg.nodes.description),
                properties = kg.nodes.properties || EXCLUDED.properties,
                embedding = COALESCE(EXCLUDED.embedding, kg.nodes.embedding),
                updated_at = NOW()
            RETURNING id
        """, (node_type.value, natural_key, node_name, description, props_json, embedding))
    else:
        # Without embedding
        cur.execute("""
            INSERT INTO kg.nodes (node_type, key, name, description, properties)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (node_type, key) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, kg.nodes.name),
                description = COALESCE(EXCLUDED.description, kg.nodes.description),
                properties = kg.nodes.properties || EXCLUDED.properties,
                updated_at = NOW()
            RETURNING id
        """, (node_type.value, natural_key, node_name, description, props_json))
    
    row = cur.fetchone()
    node_id = row[0]
    
    logger.debug(f"Upserted KG node: type={node_type.value}, key={natural_key}, id={node_id}")
    return node_id


def upsert_edge(
    conn,
    src_node_id: int,
    dst_node_id: int,
    relation: KGEdgeRelation,
    properties: Optional[Dict[str, Any]] = None,
    weight: float = 1.0,
) -> int:
    """
    Upsert a KG edge with idempotent ON CONFLICT behavior.
    
    Args:
        conn: Database connection (psycopg2)
        src_node_id: Source node ID
        dst_node_id: Destination node ID
        relation: KGEdgeRelation enum value (HAS_FIELD, MAPS_TO, DERIVES_FROM_GUIDE)
        properties: JSON-serializable dict of edge properties
        weight: Edge weight (0.0-1.0) for graph traversal and confidence
        
    Returns:
        edge_id: The ID of the upserted edge
        
    Note:
        Uses ON CONFLICT (src_node_id, dst_node_id, relation_type) DO UPDATE.
        Weight is updated to MAX of existing and new values.
    """
    cur = conn.cursor()
    
    props_json = json.dumps(properties) if properties else '{}'
    
    cur.execute("""
        INSERT INTO kg.edges (src_node_id, dst_node_id, relation_type, properties, weight)
        VALUES (%s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (src_node_id, dst_node_id, relation_type) DO UPDATE SET
            properties = kg.edges.properties || EXCLUDED.properties,
            weight = GREATEST(kg.edges.weight, EXCLUDED.weight)
        RETURNING id
    """, (src_node_id, dst_node_id, relation.value, props_json, weight))
    
    row = cur.fetchone()
    edge_id = row[0]
    
    logger.debug(
        f"Upserted KG edge: {src_node_id} --[{relation.value}]--> {dst_node_id}, id={edge_id}"
    )
    return edge_id


def get_node_id_by_key(conn, node_type: KGNodeType, natural_key: str) -> Optional[int]:
    """
    Lookup a node ID by its natural key.
    
    Args:
        conn: Database connection
        node_type: KGNodeType enum value
        natural_key: The natural key to lookup
        
    Returns:
        node_id if found, None otherwise
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id FROM kg.nodes WHERE node_type = %s AND key = %s
    """, (node_type.value, natural_key))
    row = cur.fetchone()
    return row[0] if row else None


# ---------------------------------------------------------------------------
# High-Level Persistence Functions
# ---------------------------------------------------------------------------

def persist_file_spec_to_kg(
    conn,
    file_spec: FileSpec,
    guide_uri: Optional[str] = None,
) -> int:
    """
    Persist a FileSpec as a KG node with optional DERIVES_FROM_GUIDE edge.
    
    Args:
        conn: Database connection
        file_spec: FileSpec domain object with source_system_id and name
        guide_uri: Optional URI of the source guide document
        
    Returns:
        node_id: The ID of the FILE_SPEC node
    """
    # Build natural key
    natural_key = build_file_spec_key(
        file_spec.source_system_id,
        file_spec.name,
        getattr(file_spec, 'sheet_name', None),
    )
    
    # Build properties
    props = {
        "name": file_spec.name,
        "file_type": file_spec.file_type,
        "source_system_id": file_spec.source_system_id,
        "encoding": file_spec.encoding,
        "delimiter": file_spec.delimiter,
        "has_header": file_spec.has_header,
    }
    if file_spec.description:
        props["description"] = file_spec.description
    if file_spec.version:
        props["version"] = file_spec.version
    
    # Upsert the FILE_SPEC node
    node_id = upsert_node(conn, KGNodeType.FILE_SPEC, natural_key, props)
    
    # Create DERIVES_FROM_GUIDE edge if guide_uri provided
    if guide_uri:
        # Create or get the guide document node
        guide_key = f"guide.{hashlib.sha256(guide_uri.encode()).hexdigest()[:16]}"
        guide_node_id = upsert_node(
            conn, 
            KGNodeType.FILE_PATTERN,  # Using FILE_PATTERN for guide documents
            guide_key,
            {"uri": guide_uri, "type": "data_guide"},
        )
        
        # Create provenance edge
        upsert_edge(
            conn,
            src_node_id=node_id,
            dst_node_id=guide_node_id,
            relation=KGEdgeRelation.DERIVES_FROM_GUIDE,
            properties={"source_uri": guide_uri},
            weight=1.0,
        )
        logger.info(f"Created DERIVES_FROM_GUIDE edge: {natural_key} -> {guide_key}")
    
    return node_id


def persist_file_field_to_kg(
    conn,
    field: FileField,
    file_spec_node_id: int,
    file_spec_key: str,
    embedding: Optional[List[float]] = None,
) -> int:
    """
    Persist a FileField as a KG node with HAS_FIELD edge to parent spec.
    
    Args:
        conn: Database connection
        field: FileField domain object
        file_spec_node_id: Parent FILE_SPEC node ID
        file_spec_key: Parent FILE_SPEC natural key (for building field key)
        embedding: Optional pre-computed embedding vector
        
    Returns:
        node_id: The ID of the FILE_FIELD node
    """
    # Build natural key
    natural_key = build_file_field_key(file_spec_key, field.name)
    
    # Build properties
    props = {
        "name": field.name,
        "field_type": field.field_type,
        "position": field.position,
        "nullable": field.nullable,
        "file_spec_key": file_spec_key,
    }
    if field.description:
        props["description"] = field.description
    if field.format_mask:
        props["format_mask"] = field.format_mask
    if field.length:
        props["length"] = field.length
    if field.sample_values:
        props["sample_values"] = field.sample_values[:5]  # Limit to 5 samples
    if field.inference_confidence:
        props["inference_confidence"] = field.inference_confidence
    
    # Upsert the FILE_FIELD node
    node_id = upsert_node(conn, KGNodeType.FILE_FIELD, natural_key, props, embedding)
    
    # Create HAS_FIELD edge from spec to field
    upsert_edge(
        conn,
        src_node_id=file_spec_node_id,
        dst_node_id=node_id,
        relation=KGEdgeRelation.HAS_FIELD,
        properties={"position": field.position},
        weight=1.0,
    )
    
    return node_id


def persist_field_mapping_to_kg(
    conn,
    source_field_node_id: int,
    target_field_node_id: int,
    weight: float,
    mapping_source: str = "vector_similarity",
) -> int:
    """
    Persist a field-to-field mapping as a MAPS_TO edge.
    
    Args:
        conn: Database connection
        source_field_node_id: Source FILE_FIELD node ID
        target_field_node_id: Target FILE_FIELD or GUIDE_FIELD node ID
        weight: Similarity/confidence score (0.0-1.0)
        mapping_source: How the mapping was derived (vector_similarity, exact_match, llm)
        
    Returns:
        edge_id: The ID of the MAPS_TO edge
    """
    return upsert_edge(
        conn,
        src_node_id=source_field_node_id,
        dst_node_id=target_field_node_id,
        relation=KGEdgeRelation.MAPS_TO,
        properties={"mapping_source": mapping_source},
        weight=weight,
    )


# ---------------------------------------------------------------------------
# Batch Operations
# ---------------------------------------------------------------------------

def persist_file_spec_with_fields(
    conn,
    file_spec: FileSpec,
    fields: List[FileField],
    guide_uri: Optional[str] = None,
    guide_fields: Optional[List[Dict[str, Any]]] = None,
    compute_embeddings: bool = False,
) -> Tuple[int, List[int]]:
    """
    Persist a FileSpec and all its fields in a single transaction.
    
    Args:
        conn: Database connection
        file_spec: FileSpec domain object
        fields: List of FileField objects for this spec
    guide_uri: Optional source guide URI for provenance
    guide_fields: Optional list of guide field dicts (from PDF guide extraction)
        compute_embeddings: Whether to compute embeddings for fields (requires OpenAI)
        
    Returns:
        Tuple of (file_spec_node_id, list_of_field_node_ids)
    """
    from integration_coworker.kg import _compute_embedding

    normalized_guide_fields: List[Dict[str, Any]] = []
    if guide_fields:
        normalized_guide_fields = [gf for gf in guide_fields if isinstance(gf, dict) and gf.get("name")]
    
    # Persist the FILE_SPEC node
    spec_node_id = persist_file_spec_to_kg(conn, file_spec, guide_uri)
    
    # Build the spec key for field key construction
    spec_key = build_file_spec_key(
        file_spec.source_system_id,
        file_spec.name,
        getattr(file_spec, 'sheet_name', None),
    )
    
    # Persist each field
    field_node_ids: List[int] = []
    field_nodes_by_name: Dict[str, int] = {}

    for field in fields:
        embedding = None
        if compute_embeddings and field.name:
            # Build embedding text from field metadata
            embed_text = _build_field_embedding_text(field)
            try:
                embedding = _compute_embedding(embed_text)
            except Exception as e:
                logger.warning(f"Failed to compute embedding for field {field.name}: {e}")
        
        field_node_id = persist_file_field_to_kg(
            conn, field, spec_node_id, spec_key, embedding
        )
        field_node_ids.append(field_node_id)
        if field.name:
            field_nodes_by_name[field.name.lower()] = field_node_id

    # Create GUIDE_FIELD nodes and connect provenance edges when guide fields are provided
    if normalized_guide_fields and guide_uri:
        for guide_field in normalized_guide_fields:
            guide_embedding = None
            guide_embed_text = _build_guide_field_embedding_text(guide_field)
            if compute_embeddings and guide_embed_text:
                try:
                    guide_embedding = _compute_embedding(guide_embed_text)
                except Exception as e:
                    logger.warning(f"Failed to compute embedding for guide field {guide_field.get('name')}: {e}")

            guide_field_key = build_guide_field_key(guide_uri, guide_field["name"])
            guide_props = {
                "name": guide_field.get("name"),
                "field_type": guide_field.get("field_type"),
                "position": guide_field.get("position"),
                "start_position": guide_field.get("start_position"),
                "length": guide_field.get("length"),
                "description": guide_field.get("description"),
                "nullable": guide_field.get("nullable"),
                "confidence": guide_field.get("confidence"),
                "source_uri": guide_uri,
            }

            guide_field_node_id = upsert_node(
                conn,
                KGNodeType.GUIDE_FIELD,
                guide_field_key,
                guide_props,
                guide_embedding,
                name=guide_field.get("name"),
                description=guide_field.get("description"),
            )

            matched_node_id: Optional[int] = None
            matched_key: Optional[str] = None
            similarity: Optional[float] = None
            mapping_source: Optional[str] = None

            # Prefer deterministic name match first
            name_key = guide_field.get("name", "").lower()
            if name_key and name_key in field_nodes_by_name:
                matched_node_id = field_nodes_by_name[name_key]
                matched_key = build_file_field_key(spec_key, guide_field["name"])
                similarity = 1.0
                mapping_source = "name_match"
            if not matched_node_id and guide_embedding:
                matches = find_matching_file_fields(
                    conn,
                    guide_field,
                    limit=3,
                    min_similarity=0.6,
                    query_embedding=guide_embedding,
                    file_spec_key=spec_key,
                )
                for match in matches:
                    props = match.get("properties", {}) or {}
                    spec_key_prop = props.get("file_spec_key")
                    if spec_key_prop and spec_key_prop != spec_key:
                        continue
                    matched_node_id = match.get("file_field_node_id") or match.get("node_id")
                    matched_key = match.get("file_field_key") or match.get("key")
                    similarity = match["similarity"]
                    mapping_source = match.get("method", "vector_similarity")
                    break

            if matched_node_id:
                edge_props = {
                    "guide_uri": guide_uri,
                    "source_uri": guide_uri,
                    "confidence": guide_field.get("confidence"),
                    "mapping_source": mapping_source,
                    "match_method": mapping_source,
                }

                if guide_field.get("enriched_fields") is not None:
                    edge_props["enriched_fields"] = guide_field.get("enriched_fields")
                if guide_field.get("description"):
                    edge_props["description"] = guide_field.get("description")
                if guide_field.get("field_type"):
                    edge_props["field_type"] = guide_field.get("field_type")
                if guide_field.get("page_number") is not None:
                    edge_props["page_number"] = guide_field.get("page_number")
                elif guide_field.get("page") is not None:
                    edge_props["page_number"] = guide_field.get("page")

                weight = similarity if similarity is not None else guide_field.get("confidence") or 1.0

                upsert_edge(
                    conn,
                    src_node_id=guide_field_node_id,
                    dst_node_id=matched_node_id,
                    relation=KGEdgeRelation.DERIVES_FROM_GUIDE,
                    properties=edge_props,
                    weight=weight,
                )
    
    conn.commit()
    
    logger.info(
        f"Persisted FILE_SPEC {spec_key} with {len(field_node_ids)} fields to KG"
    )
    return spec_node_id, field_node_ids


def _build_field_embedding_text(field: FileField) -> str:
    """
    Build text representation of a field for embedding computation.
    
    Combines field name, description, type, and sample values into
    a single string optimized for semantic similarity matching.
    """
    parts = [field.name]
    
    if field.description:
        parts.append(field.description)
    
    if field.field_type:
        parts.append(f"type: {field.field_type}")
    
    if field.sample_values:
        samples = field.sample_values[:3]  # Max 3 samples
        parts.append(f"samples: {', '.join(str(s) for s in samples)}")
    
    return " | ".join(parts)


def _build_guide_field_embedding_text(guide_field: Dict[str, Any]) -> str:
    """
    Build embedding text for a guide field dict.
    
    Uses the same heuristic as FILE_FIELD embedding but operates on
    the dictionary produced by PDF guide extraction.
    """
    parts: List[str] = []

    name = guide_field.get("name")
    if name:
        parts.append(str(name))

    description = guide_field.get("description")
    if description:
        parts.append(str(description))

    field_type = guide_field.get("field_type")
    if field_type:
        parts.append(f"type: {field_type}")

    samples = guide_field.get("sample_values") or []
    if isinstance(samples, list) and samples:
        parts.append("samples: " + ", ".join(str(s) for s in samples[:3]))

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Query Functions for KG Field Matching
# ---------------------------------------------------------------------------

def find_similar_fields(
    conn,
    query_embedding: List[float],
    limit: int = 5,
    min_similarity: float = 0.7,
    node_type: KGNodeType = KGNodeType.FILE_FIELD,
) -> List[Tuple[int, str, float, Dict[str, Any]]]:
    """
    Find KG nodes similar to a query embedding using pgvector cosine similarity.
    
    Args:
        conn: Database connection
        query_embedding: 1536-dim query vector
        limit: Maximum results to return
        min_similarity: Minimum cosine similarity threshold (0.0-1.0)
        node_type: Filter by node type (default FILE_FIELD)
        
    Returns:
        List of tuples: (node_id, key, similarity_score, properties)
    """
    cur = conn.cursor()
    
    # pgvector uses <=> for cosine distance (1 - similarity)
    # So we compute similarity as 1 - distance
    cur.execute("""
        SELECT 
            id, 
            key, 
            1 - (embedding <=> %s::vector) as similarity,
            properties
        FROM kg.nodes
        WHERE node_type = %s
          AND embedding IS NOT NULL
          AND 1 - (embedding <=> %s::vector) >= %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """, (query_embedding, node_type.value, query_embedding, min_similarity, query_embedding, limit))
    
    results = []
    for row in cur.fetchall():
        node_id, key, similarity, props = row
        props_dict = props if isinstance(props, dict) else json.loads(props) if props else {}
        results.append((node_id, key, float(similarity), props_dict))
    
    return results


def find_matching_file_fields(
    conn,
    guide_field: Dict[str, Any],
    limit: int = 5,
    min_similarity: float = 0.7,
    query_embedding: Optional[List[float]] = None,
    file_spec_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Find FILE_FIELD nodes similar to a guide field using embeddings.
    
    Args:
        conn: Database connection
        guide_field: Guide field dictionary (from PDF extraction)
        limit: Maximum results to return
        min_similarity: Minimum similarity threshold
        query_embedding: Optional precomputed embedding for the guide field
    
    Returns:
        List of dicts with node_id, key, similarity, properties.
    """
    from integration_coworker.kg import _compute_embedding

    results: List[Dict[str, Any]] = []

    # Try deterministic name match if file_spec_key is provided
    guide_name = (guide_field.get("name") or "").strip()
    if guide_name and file_spec_key:
        candidate_key = build_file_field_key(file_spec_key, guide_name)
        node_id = get_node_id_by_key(conn, KGNodeType.FILE_FIELD, candidate_key)
        if node_id:
            results.append({
                "file_field_node_id": node_id,
                "file_field_key": candidate_key,
                "guide_field_key": build_guide_field_key(guide_field.get("source_uri", ""), guide_name),
                "similarity": 1.0,
                "method": "name_match",
                "properties": {},
            })
            return results

    # Vector matching is Postgres-only; skip if running on SQLite or unknown engines
    engine_type = db.get_engine_type() if hasattr(db, "get_engine_type") else "postgres"
    if engine_type != "postgres":
        logger.debug("Skipping vector field matching: engine_type=%s", engine_type)
        return results

    # Build embedding for the guide field if not provided
    embedding = query_embedding
    if embedding is None:
        embed_text = _build_guide_field_embedding_text(guide_field)
        if not embed_text:
            return []
        embedding = _compute_embedding(embed_text)

    if not embedding:
        return []

    matches = find_similar_fields(
        conn,
        query_embedding=embedding,
        limit=limit,
        min_similarity=min_similarity,
        node_type=KGNodeType.FILE_FIELD,
    )

    guide_uri = guide_field.get("source_uri", "")
    guide_key = build_guide_field_key(guide_uri, guide_field.get("name", "")) if guide_field.get("name") else None

    for node_id, key, similarity, props in matches:
        results.append({
            "file_field_node_id": node_id,
            "file_field_key": key,
            "guide_field_key": guide_key,
            "similarity": similarity,
            "method": "vector_similarity",
            "properties": props,
        })

    return results


def get_field_mappings(conn, field_node_id: int) -> List[Tuple[int, str, float]]:
    """
    Get all MAPS_TO edges from a field node.
    
    Args:
        conn: Database connection
        field_node_id: Source field node ID
        
    Returns:
        List of tuples: (target_node_id, target_key, weight)
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT 
            e.dst_node_id,
            n.key,
            e.weight
        FROM kg.edges e
        JOIN kg.nodes n ON n.id = e.dst_node_id
        WHERE e.src_node_id = %s
          AND e.relation_type = %s
        ORDER BY e.weight DESC
    """, (field_node_id, KGEdgeRelation.MAPS_TO.value))
    
    return [(row[0], row[1], float(row[2])) for row in cur.fetchall()]
    
    return [(row[0], row[1], float(row[2])) for row in cur.fetchall()]

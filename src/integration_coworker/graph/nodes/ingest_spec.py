"""
ingest_spec node — fetches and chunks all spec_refs (primary + supporting).

Implements: Design Doc §3.2 Ingest Spec
Touches: spec_documents (Silver layer)
"""
from pathlib import Path
import httpx
import hashlib
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecDocument


def _fetch_spec_content(ref: str) -> tuple[str, str]:
    """
    Fetch content from a spec ref (HTTP URL or local file path).
    Returns (content, content_type).
    Raises on failure.
    """
    if ref.startswith("http://") or ref.startswith("https://"):
        response = httpx.get(ref, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        content = response.text
        content_type = response.headers.get("content-type", "application/octet-stream")
        return content, content_type
    else:
        file_path = Path(ref)
        if not file_path.exists():
            raise FileNotFoundError(f"Spec file not found: {ref}")
        content = file_path.read_text(encoding="utf-8")
        suffix = file_path.suffix.lower()
        if suffix in [".yaml", ".yml"]:
            content_type = "application/yaml"
        elif suffix == ".json":
            content_type = "application/json"
        else:
            content_type = "text/plain"
        return content, content_type


def _chunk_content(content: str, chunk_size: int = 1000) -> list[str]:
    """
    Split content into chunks on section boundaries or size limit.
    """
    chunks = []
    lines = content.split("\n")
    current_chunk = []
    current_size = 0

    for line in lines:
        current_chunk.append(line)
        current_size += len(line) + 1  # +1 for newline

        # Chunk on section markers or size limit
        if current_size >= chunk_size or line.strip().startswith("paths:") or line.strip().startswith("components:"):
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_size = 0

    # Add remaining content
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks if chunks else [content]


def ingest_spec(state: WorkflowState) -> WorkflowState:
    """
    Ingest all spec_refs (primary + supporting) into spec_documents and doc_chunks.

    Reads: spec_refs, plan
    Writes: spec_documents, doc_chunks, plan["chunk_index_to_spec_document_uri"]
    """
    if not state.spec_refs:
        state.errors.append("No spec_refs provided")
        state.completed_steps.append("ingest_spec")
        return state

    all_chunks: list[str] = []
    chunk_index_to_uri: dict[int, str] = {}

    for ref in state.spec_refs:
        try:
            content, content_type = _fetch_spec_content(ref)
            sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

            # Create SpecDocument for this ref
            spec_doc = SpecDocument(
                id=None,
                source_system_id=None,
                version="1.0",
                uri=ref,
                content_type=content_type,
                sha256=sha256,
                content=content,
            )
            state.spec_documents.append(spec_doc)

            # Chunk this document
            doc_chunks = _chunk_content(content)

            # Track which chunks belong to which spec document
            start_idx = len(all_chunks)
            for i, chunk in enumerate(doc_chunks):
                chunk_index_to_uri[start_idx + i] = ref
            all_chunks.extend(doc_chunks)

        except Exception as e:
            state.errors.append(f"Failed to ingest spec from {ref}: {str(e)}")

    state.doc_chunks = all_chunks

    # Store mapping in plan for downstream nodes (embed_spec_chunks, persist)
    if state.plan is not None:
        state.plan["chunk_index_to_spec_document_uri"] = chunk_index_to_uri

    state.completed_steps.append("ingest_spec")
    return state

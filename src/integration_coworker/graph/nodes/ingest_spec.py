from pathlib import Path
import httpx
import hashlib
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecDocument

def ingest_spec(state: WorkflowState) -> WorkflowState:
    """
    Reads: spec_refs
    Writes: spec_documents, doc_chunks
    """
    if not state.spec_refs:
        state.errors.append("No spec_refs provided")
        state.completed_steps.append("ingest_spec")
        return state
    
    # For Phase 2, focus on the first spec_ref
    primary_ref = state.spec_refs[0]
    
    try:
        # Determine if HTTP(S) or local file
        if primary_ref.startswith("http://") or primary_ref.startswith("https://"):
            # Fetch from URL
            response = httpx.get(primary_ref, timeout=30.0, follow_redirects=True)
            response.raise_for_status()
            content = response.text
            content_type = response.headers.get("content-type", "application/octet-stream")
        else:
            # Read from local filesystem
            file_path = Path(primary_ref)
            if not file_path.exists():
                state.errors.append(f"Spec file not found: {primary_ref}")
                state.completed_steps.append("ingest_spec")
                return state
            
            content = file_path.read_text(encoding="utf-8")
            # Guess content type from suffix
            suffix = file_path.suffix.lower()
            if suffix in [".yaml", ".yml"]:
                content_type = "application/yaml"
            elif suffix == ".json":
                content_type = "application/json"
            else:
                content_type = "text/plain"
        
        # Calculate SHA256
        sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        
        # Create SpecDocument
        spec_doc = SpecDocument(
            id=None,
            source_system_id=None,
            version="1.0",  # Default version
            uri=primary_ref,
            content_type=content_type,
            sha256=sha256,
            content=content,
        )
        state.spec_documents.append(spec_doc)
        
        # Simple chunking: split on double newlines or fixed size
        # For Phase 2, keep it simple - split on section boundaries or every 1000 chars
        chunks = []
        lines = content.split("\n")
        current_chunk = []
        current_size = 0
        
        for line in lines:
            current_chunk.append(line)
            current_size += len(line) + 1  # +1 for newline
            
            # Chunk on section markers or size limit
            if current_size >= 1000 or line.strip().startswith("paths:") or line.strip().startswith("components:"):
                if current_chunk:
                    chunks.append("\n".join(current_chunk))
                    current_chunk = []
                    current_size = 0
        
        # Add remaining content
        if current_chunk:
            chunks.append("\n".join(current_chunk))
        
        state.doc_chunks = chunks if chunks else [content]
        
    except Exception as e:
        state.errors.append(f"Failed to ingest spec from {primary_ref}: {str(e)}")
    
    state.completed_steps.append("ingest_spec")
    return state

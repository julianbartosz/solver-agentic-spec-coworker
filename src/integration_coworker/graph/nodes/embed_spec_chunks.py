import os
from integration_coworker.graph.state import WorkflowState
from integration_coworker.domain.models import SpecChunkEmbedding
from integration_coworker.config import get_embedding_config

def embed_spec_chunks(state: WorkflowState) -> WorkflowState:
    """
    Reads: doc_chunks, spec_documents
    Writes: spec_chunk_embeddings
    """
    if not state.doc_chunks:
        state.completed_steps.append("embed_spec_chunks")
        return state
    
    config = get_embedding_config()
    model = config.get("model", "text-embedding-3-small")
    dimensions = config.get("dimensions", 1536)
    
    # For Phase 2: use fake embeddings unless real API key is configured
    # This keeps the interface correct for later real implementation
    use_fake = not os.getenv("OPENAI_API_KEY")
    
    try:
        for idx, chunk in enumerate(state.doc_chunks):
            if use_fake:
                # Generate deterministic fake vector
                embedding_vector = [0.0] * dimensions
                # Add some variation based on chunk index
                if dimensions > 0:
                    embedding_vector[0] = float(idx) / max(len(state.doc_chunks), 1)
            else:
                # TODO: Real OpenAI embedding call would go here
                # from openai import OpenAI
                # client = OpenAI()
                # response = client.embeddings.create(input=chunk, model=model)
                # embedding_vector = response.data[0].embedding
                embedding_vector = [0.0] * dimensions
            
            chunk_embedding = SpecChunkEmbedding(
                id=None,
                spec_document_id=None,
                chunk_index=idx,
                content=chunk[:500],  # Store truncated preview
                embedding=embedding_vector,
            )
            state.spec_chunk_embeddings.append(chunk_embedding)
            
    except Exception as e:
        state.errors.append(f"Failed to embed spec chunks: {str(e)}")
    
    state.completed_steps.append("embed_spec_chunks")
    return state

"""
Artifact storage for large workflow state fields.

This module provides pluggable artifact storage with a filesystem default
backend and Postgres index. The key abstraction is the ArtifactStore protocol,
which enables swapping backends (filesystem → object storage) without
changing graph logic.

Usage:
    from integration_coworker.persistence.artifacts import (
        ArtifactStore,
        ArtifactRef,
        get_artifact_store,
    )
    
    store = get_artifact_store()
    ref = store.put(run_id="run-123", key="openapi_spec", value=large_dict)
    restored = store.get(ref)
"""

from .base import ArtifactStore, ArtifactRef, ArtifactCodec
from .fs import FilesystemArtifactStore, get_artifact_store, reset_artifact_store

__all__ = [
    "ArtifactStore",
    "ArtifactRef",
    "ArtifactCodec",
    "FilesystemArtifactStore",
    "get_artifact_store",
    "reset_artifact_store",
]

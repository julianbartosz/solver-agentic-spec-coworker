import types
import sqlite3
from contextlib import contextmanager

import pytest

from integration_coworker.graph import runtime
from integration_coworker.graph.nodes import persist_silver_checkpoint
from integration_coworker.graph.nodes import build_silver_file_model
from integration_coworker.graph.state import WorkflowState
from integration_coworker.api.types import IntegrationOptions
from integration_coworker.persistence import db
from integration_coworker.persistence import sql_helpers


pytestmark = pytest.mark.no_db


def test_file_node_in_order_and_dependencies():
    order = runtime.WORKFLOW_NODE_ORDER
    assert "build_silver_file_model" in order
    idx = order.index("build_silver_file_model")
    assert order[idx - 1] == "build_silver_api_model"
    assert order[idx + 1] == "embed_spec_chunks"

    deps = runtime.NODE_DEPENDENCIES.get("build_silver_file_model")
    assert deps == ["build_silver_api_model"]

    embed_deps = runtime.NODE_DEPENDENCIES.get("embed_spec_chunks")
    assert embed_deps is not None
    assert "build_silver_file_model" in embed_deps


def test_parallel_graph_contains_file_node_and_edges():
    graph = runtime.build_parallel_graph().get_graph()

    assert "build_silver_file_model" in graph.nodes

    edges = {(edge.source, edge.target) for edge in graph.edges}
    assert ("build_silver_api_model", "build_silver_file_model") in edges
    assert ("build_silver_file_model", "embed_spec_chunks") in edges
    assert ("build_silver_file_model", "understand_task") in edges


def test_silver_checkpoint_reports_file_specs_in_dry_run():
    state = WorkflowState(
        source_refs=[],
        spec_refs=[],
        task_description="t",
        options=IntegrationOptions(dry_run=True),
    )
    # Dry run path only inspects counts
    state.file_specs = [types.SimpleNamespace()]
    state.file_fields = [types.SimpleNamespace()]

    result = persist_silver_checkpoint.persist_silver_checkpoint(state)

    would_persist = result.persisted_ids.get("would_persist_silver", {})
    assert would_persist.get("file_specs") == 1
    assert would_persist.get("file_fields") == 1


def _setup_sqlite(monkeypatch):
    conn = sqlite3.connect(":memory:")

    conn.executescript(
        """
        CREATE TABLE source_systems (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            display_name TEXT
        );
        CREATE TABLE file_specs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER,
            name TEXT,
            file_type TEXT,
            spec_document_id INTEGER,
            encoding TEXT,
            delimiter TEXT,
            has_header INTEGER,
            line_terminator TEXT,
            quote_char TEXT,
            escape_char TEXT,
            description TEXT,
            version TEXT,
            sample_uri TEXT,
            UNIQUE(source_system_id, name)
        );
        CREATE TABLE file_fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_spec_id INTEGER,
            name TEXT,
            field_type TEXT,
            position INTEGER,
            start_position INTEGER,
            length INTEGER,
            format_mask TEXT,
            nullable INTEGER,
            default_value TEXT,
            validation_regex TEXT,
            description TEXT,
            sample_values TEXT,
            inference_confidence REAL,
            UNIQUE(file_spec_id, name)
        );
        CREATE TABLE endpoints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER,
            spec_document_id INTEGER,
            method TEXT,
            path TEXT,
            operation_id TEXT,
            summary TEXT,
            description TEXT,
            UNIQUE(source_system_id, spec_document_id, path, method)
        );
        CREATE TABLE schemas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER,
            name TEXT,
            ref TEXT,
            UNIQUE(source_system_id, name)
        );
        CREATE TABLE spec_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER,
            uri TEXT,
            sha256 TEXT,
            content_type TEXT,
            UNIQUE(source_system_id, sha256)
        );
        CREATE TABLE spec_sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_document_id INTEGER,
            section_type TEXT,
            title TEXT,
            path TEXT,
            start_offset INTEGER,
            end_offset INTEGER,
            content TEXT,
            UNIQUE(spec_document_id, section_type, path)
        );
        CREATE TABLE fields (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schema_id INTEGER,
            name TEXT,
            json_path TEXT,
            type TEXT,
            format TEXT,
            required INTEGER,
            description TEXT,
            UNIQUE(schema_id, json_path)
        );
        CREATE TABLE entity_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER,
            from_entity_id INTEGER,
            to_entity_id INTEGER,
            relationship_type TEXT,
            UNIQUE(source_system_id, from_entity_id, to_entity_id, relationship_type)
        );
        CREATE TABLE entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER,
            name TEXT,
            description TEXT,
            UNIQUE(source_system_id, name)
        );
        CREATE TABLE endpoint_parameters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint_id INTEGER,
            name TEXT,
            location TEXT,
            required INTEGER,
            schema_ref TEXT,
            description TEXT,
            UNIQUE(endpoint_id, name, location)
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_system_id INTEGER,
            name TEXT,
            description TEXT,
            UNIQUE(source_system_id, name)
        );
        CREATE TABLE spec_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            spec_document_id INTEGER,
            chunk_index INTEGER,
            content TEXT,
            embedding TEXT,
            UNIQUE(spec_document_id, chunk_index)
        );
        """
    )

    class _ConnWrapper:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def close(self):
            # Keep underlying connection open for inspection
            return None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    wrapper = _ConnWrapper(conn)

    monkeypatch.setattr(persist_silver_checkpoint.db, "get_connection", lambda: wrapper)
    monkeypatch.setattr(db, "get_connection", lambda: wrapper)
    monkeypatch.setattr(persist_silver_checkpoint, "get_engine_type", lambda: "sqlite")
    monkeypatch.setattr(db, "get_engine_type", lambda: "sqlite")
    monkeypatch.setattr(sql_helpers, "get_engine_type", lambda: "sqlite")
    monkeypatch.setattr(persist_silver_checkpoint.db, "init_schema", lambda: None)
    return conn


def _make_file_spec(name="orders"):
    return types.SimpleNamespace(
        id=None,
        source_system_id=None,
        name=name,
        file_type="csv",
        spec_document_id=None,
        encoding="utf-8",
        delimiter=",",
        has_header=True,
        line_terminator="\n",
        quote_char='"',
        escape_char="\\",
        description=None,
        version=None,
        sample_uri=None,
    )


def _make_file_field(name="id"):
    return types.SimpleNamespace(
        id=None,
        file_spec_id=None,
        name=name,
        field_type="string",
        position=0,
        start_position=None,
        length=None,
        format_mask=None,
        nullable=True,
        default_value=None,
        validation_regex=None,
        description=None,
        sample_values=[],
        inference_confidence=1.0,
    )


def test_persist_silver_checkpoint_file_specs_idempotent(monkeypatch):
    conn = _setup_sqlite(monkeypatch)

    state = WorkflowState(
        source_refs=[],
        spec_refs=[],
        task_description="t",
        provider_code="p",
        options=IntegrationOptions(dry_run=False),
    )
    state.file_specs = [_make_file_spec()]
    state.file_fields = [_make_file_field()]

    persist_silver_checkpoint.persist_silver_checkpoint(state)
    first_spec_count = conn.execute("select count(*) from file_specs").fetchone()[0]
    first_field_count = conn.execute("select count(*) from file_fields").fetchone()[0]

    persist_silver_checkpoint.persist_silver_checkpoint(state)
    second_spec_count = conn.execute("select count(*) from file_specs").fetchone()[0]
    second_field_count = conn.execute("select count(*) from file_fields").fetchone()[0]

    assert first_spec_count == 1
    assert first_field_count == 1
    assert second_spec_count == 1
    assert second_field_count == 1


def test_persist_silver_checkpoint_sets_file_ids(monkeypatch):
    _setup_sqlite(monkeypatch)

    state = WorkflowState(
        source_refs=[],
        spec_refs=[],
        task_description="t",
        provider_code="p",
        options=IntegrationOptions(dry_run=False),
    )
    state.file_specs = [_make_file_spec()]
    state.file_fields = [_make_file_field()]

    result = persist_silver_checkpoint.persist_silver_checkpoint(state)

    assert result.persisted_ids.get("file_spec_ids")
    assert result.persisted_ids.get("file_field_ids")

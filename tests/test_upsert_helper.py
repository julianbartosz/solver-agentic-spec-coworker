import os

import pytest

from integration_coworker.config import reset_settings
from integration_coworker.persistence.upsert import InsertDoNothingBuilder


@pytest.mark.parametrize("engine", ["sqlite", "postgres"])
def test_insert_do_nothing_sql_contains_on_conflict(engine, monkeypatch):
    monkeypatch.setenv("USE_SQLITE", "true" if engine == "sqlite" else "false")
    reset_settings()

    builder = InsertDoNothingBuilder(
        "example",
        ["id", "payload"],
        conflict_columns=["id"],
        schema="public" if engine == "postgres" else None,
        engine_type=engine,
    )

    assert "ON CONFLICT" in builder.sql
    assert "DO NOTHING" in builder.sql
    assert "INSERT OR IGNORE" not in builder.sql.upper()


def test_insert_do_nothing_renders_params(monkeypatch):
    monkeypatch.setenv("USE_SQLITE", "true")
    reset_settings()

    builder = InsertDoNothingBuilder(
        "example",
        ["id", "payload"],
        conflict_columns=["id"],
    )

    sql, params = builder.render({"id": 1, "payload": "abc"})
    assert "INSERT" in sql
    assert params == {"id": 1, "payload": "abc"}

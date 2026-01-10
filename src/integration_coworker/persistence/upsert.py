"""
Dialect-aware insert/do-nothing helper built on SQLAlchemy.

Centralizes ON CONFLICT generation for both Postgres and SQLite so callers
avoid embedding backend-specific syntax. Designed for retry-safe/idempotent
persistence flows where duplicate inserts should be ignored.
"""
from typing import Dict, Iterable, List, Optional, Tuple

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql, sqlite

from .sql_helpers import get_engine_type


class InsertDoNothingBuilder:
    """Prepare an INSERT ... ON CONFLICT DO NOTHING statement once per table."""

    def __init__(
        self,
        table: str,
        columns: Iterable[str],
        *,
        conflict_columns: Optional[Iterable[str]] = None,
        schema: Optional[str] = None,
        engine_type: Optional[str] = None,
    ) -> None:
        self.engine_type = engine_type or get_engine_type()
        self.columns: Tuple[str, ...] = tuple(columns)
        self.conflict_columns: Tuple[str, ...] = tuple(conflict_columns or ())

        dialect = (
            postgresql.dialect(paramstyle="pyformat")
            if self.engine_type == "postgres"
            else sqlite.dialect(paramstyle="named")
        )

        metadata = sa.MetaData()
        tbl = sa.Table(
            table,
            metadata,
            *(sa.Column(col) for col in self.columns),
            schema=schema if self.engine_type == "postgres" else None,
        )

        insert = (
            sa.dialects.postgresql.insert(tbl)
            if self.engine_type == "postgres"
            else sa.dialects.sqlite.insert(tbl)
        )
        stmt = insert.values({col: sa.bindparam(col) for col in self.columns})

        if self.conflict_columns:
            stmt = stmt.on_conflict_do_nothing(index_elements=list(self.conflict_columns))
        else:
            stmt = stmt.on_conflict_do_nothing()

        self._compiled = stmt.compile(
            dialect=dialect, compile_kwargs={"render_postcompile": True}
        )

    @property
    def sql(self) -> str:
        """Return compiled SQL with dialect placeholders."""
        return self._compiled.string

    def params(self, values: Dict[str, object]) -> Dict[str, object]:
        """Build parameter mapping for execution for the provided values."""
        return {col: values[col] for col in self.columns}

    def render(self, values: Dict[str, object]) -> Tuple[str, Dict[str, object]]:
        """Return SQL + params tuple for cursor.execute."""
        return self.sql, self.params(values)

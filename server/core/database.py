"""SQLite lifecycle helpers for the story graph."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class DatabaseError(RuntimeError):
    pass


def initialize_database(db_path: str | Path, schema_path: str | Path) -> Path:
    """Create or migrate a graph database using the idempotent schema file."""
    database = Path(db_path).expanduser().resolve()
    schema = Path(schema_path).expanduser().resolve()
    if not schema.is_file():
        raise DatabaseError(f"스키마 파일이 없습니다: {schema}")
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(schema.read_text(encoding="utf-8"))
        connection.commit()
    except (OSError, sqlite3.Error) as exc:
        connection.rollback()
        raise DatabaseError(f"데이터베이스 초기화 실패: {database}: {exc}") from exc
    finally:
        connection.close()
    return database


@contextmanager
def connect_read_only(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a fail-closed, query-only SQLite connection."""
    database = Path(db_path).expanduser().resolve()
    if not database.is_file():
        raise DatabaseError(f"story 데이터베이스가 없습니다: {database}")
    uri = f"{database.as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA query_only = ON")
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def connect_bootstrap(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Writable connection reserved for the explicit P0 bible bootstrapper."""
    database = Path(db_path).expanduser().resolve()
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

"""Deterministic offline maintenance for search indexes and SQLite statistics."""

from __future__ import annotations

from pathlib import Path

from .database import connect_write
from .embedding import EmbeddingIndex


class OfflineConsolidator:
    def __init__(self, db_path: str | Path, embeddings: EmbeddingIndex) -> None:
        self.db_path = Path(db_path).resolve()
        self.embeddings = embeddings

    def run(self) -> dict[str, int | str]:
        result: dict[str, int | str] = dict(self.embeddings.sync_all())
        with connect_write(self.db_path) as connection:
            connection.execute("INSERT INTO node_fts(node_fts) VALUES ('optimize')")
            connection.execute("PRAGMA optimize")
        result["status"] = "consolidated"
        return result

"""Local deterministic embeddings persisted through sqlite-vec."""

from __future__ import annotations

import hashlib
import math
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import sqlite_vec

from .merkle import canonical_json


class EmbeddingIndex:
    DIMENSIONS = 384
    MODEL_ID = "storyai-char-ngram-v1u"
    MAX_DISTANCE = 1.27

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()
        with self._connect() as connection:
            self._ensure_vec_table(connection)

    def sync_all(self) -> dict[str, int]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._ensure_vec_table(connection)
                rows = connection.execute(
                    """
                    SELECT n.id, n.title, n.summary, n.cid,
                           COALESCE(f.aliases, '') AS aliases,
                           COALESCE(f.body, '') AS body
                    FROM live_node AS n
                    LEFT JOIN node_fts AS f ON f.id=n.id
                    ORDER BY n.id
                    """
                ).fetchall()
                changed = 0
                for row in rows:
                    text = "\n".join(
                        value
                        for value in (row["title"], row["aliases"], row["summary"], row["body"])
                        if value
                    )
                    content_hash = hashlib.sha256(
                        canonical_json([row["cid"], text, self.MODEL_ID]).encode("utf-8")
                    ).hexdigest()
                    current = connection.execute(
                        "SELECT vec_rowid, content_hash FROM node_embedding WHERE node = ?",
                        (row["id"],),
                    ).fetchone()
                    if current is not None and current["content_hash"] == content_hash:
                        continue
                    vector = self.embed(text)
                    packed = sqlite_vec.serialize_float32(vector)
                    now = datetime.now(UTC).isoformat()
                    if current is None:
                        cursor = connection.execute(
                            """
                            INSERT INTO node_embedding(
                              node, model, dims, content_hash, vector, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                row["id"],
                                self.MODEL_ID,
                                self.DIMENSIONS,
                                content_hash,
                                packed,
                                now,
                            ),
                        )
                        vec_rowid = int(cursor.lastrowid)
                    else:
                        vec_rowid = int(current["vec_rowid"])
                        connection.execute(
                            """
                            UPDATE node_embedding
                            SET model=?, dims=?, content_hash=?, vector=?, updated_at=?
                            WHERE vec_rowid=?
                            """,
                            (
                                self.MODEL_ID,
                                self.DIMENSIONS,
                                content_hash,
                                packed,
                                now,
                                vec_rowid,
                            ),
                        )
                        connection.execute("DELETE FROM node_vec WHERE rowid = ?", (vec_rowid,))
                    connection.execute(
                        "INSERT INTO node_vec(rowid, embedding) VALUES (?, ?)",
                        (vec_rowid, packed),
                    )
                    changed += 1
                stale = connection.execute(
                    """
                    SELECT e.vec_rowid
                    FROM node_embedding AS e
                    LEFT JOIN live_node AS n ON n.id=e.node
                    WHERE n.id IS NULL
                    """
                ).fetchall()
                for row in stale:
                    connection.execute("DELETE FROM node_vec WHERE rowid = ?", (row["vec_rowid"],))
                    connection.execute(
                        "DELETE FROM node_embedding WHERE vec_rowid = ?", (row["vec_rowid"],)
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {"indexed": len(rows), "changed": changed, "removed": len(stale)}

    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        packed = sqlite_vec.serialize_float32(self.embed(query))
        with self._connect() as connection:
            self._ensure_vec_table(connection)
            rows = connection.execute(
                """
                SELECT e.node, v.distance
                FROM node_vec AS v
                JOIN node_embedding AS e ON e.vec_rowid=v.rowid
                JOIN live_node AS n ON n.id=e.node
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance, e.node
                """,
                (packed, limit),
            ).fetchall()
        return [
            {
                "id": row["node"],
                "distance": round(float(row["distance"]), 6),
                "score": round(1.0 / (1.0 + float(row["distance"])), 6),
            }
            for row in rows
            if float(row["distance"]) <= self.MAX_DISTANCE
        ]

    @classmethod
    def embed(cls, text: str) -> list[float]:
        normalized = unicodedata.normalize("NFKC", text).casefold().strip()
        vector = [0.0] * cls.DIMENSIONS
        features: list[str] = []
        features.extend(normalized.split())
        compact = "".join(character for character in normalized if not character.isspace())
        for width in (2, 3):
            features.extend(
                compact[index : index + width] for index in range(len(compact) - width + 1)
            )
        if not features:
            return vector
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            raw = int.from_bytes(digest, "big")
            index = raw % cls.DIMENSIONS
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.enable_load_extension(True)
            try:
                sqlite_vec.load(connection)
            finally:
                connection.enable_load_extension(False)
            yield connection
        finally:
            connection.close()

    @classmethod
    def _ensure_vec_table(cls, connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS node_vec "
            f"USING vec0(embedding float[{cls.DIMENSIONS}])"
        )

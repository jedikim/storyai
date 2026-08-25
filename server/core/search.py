"""BM25 plus local dense retrieval fused with reciprocal-rank fusion."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from .database import connect_read_only
from .embedding import EmbeddingIndex
from .traverse import GraphStore


class HybridSearch:
    RRF_K = 60

    def __init__(
        self,
        *,
        db_path: str | Path,
        graph: GraphStore,
        embeddings: EmbeddingIndex,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.graph = graph
        self.embeddings = embeddings

    def find(
        self,
        query: str,
        *,
        kinds: Iterable[str] | None,
        tags: Iterable[str] | None,
        as_of: int | None,
        mode: Literal["lexical", "semantic", "hybrid"],
        limit: int,
    ) -> list[dict[str, Any]]:
        kind_values = tuple(kinds or ())
        tag_values = tuple(tags or ())
        pool_size = min(200, max(limit * 8, 40))
        lexical = (
            self.graph.find(
                query,
                kinds=kind_values,
                tags=tag_values,
                as_of=as_of,
                limit=pool_size,
            )
            if mode in {"lexical", "hybrid"}
            else []
        )
        if mode == "lexical":
            return lexical[:limit]
        allowed = self._allowed_ids(kinds=kind_values, tags=tag_values, as_of=as_of)
        dense = [
            item for item in self.embeddings.search(query, limit=pool_size) if item["id"] in allowed
        ]
        if mode == "semantic":
            return self._hydrate(dense[:limit])
        scores: dict[str, float] = {}
        for rank, item in enumerate(lexical, start=1):
            scores[item["id"]] = scores.get(item["id"], 0.0) + 1.0 / (self.RRF_K + rank)
        for rank, item in enumerate(dense, start=1):
            scores[item["id"]] = scores.get(item["id"], 0.0) + 1.0 / (self.RRF_K + rank)
        ranked = [
            {"id": node_id, "score": round(score, 8)}
            for node_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        ]
        return self._hydrate(ranked[:limit])

    def _allowed_ids(
        self,
        *,
        kinds: tuple[str, ...],
        tags: tuple[str, ...],
        as_of: int | None,
    ) -> set[str]:
        clauses = ["n.tx_to IS NULL"]
        params: list[Any] = []
        if kinds:
            placeholders = ",".join("?" for _ in kinds)
            clauses.append(f"n.kind IN ({placeholders})")
            params.extend(kinds)
        if as_of is not None:
            clauses.append("(n.reveal_at IS NULL OR n.reveal_at <= ?)")
            params.append(as_of)
        if tags:
            placeholders = ",".join("?" for _ in tags)
            clauses.append(
                f"""n.id IN (
                  SELECT node FROM node_tag WHERE tag IN ({placeholders})
                  GROUP BY node HAVING COUNT(DISTINCT tag) = ?
                )"""
            )
            params.extend(tags)
            params.append(len(tags))
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                f"SELECT n.id FROM node AS n WHERE {' AND '.join(clauses)} ORDER BY n.id",
                params,
            ).fetchall()
        return {row["id"] for row in rows}

    def _hydrate(self, ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not ranked:
            return []
        briefs = self.graph.get_nodes([item["id"] for item in ranked], include="brief", as_of=None)
        by_id = {item["id"]: item for item in briefs}
        return [
            {
                **by_id[item["id"]],
                "score": item["score"],
            }
            for item in ranked
            if item["id"] in by_id
        ]

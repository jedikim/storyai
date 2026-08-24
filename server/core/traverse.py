"""The single read-side SQL boundary for graph lookup and traversal."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

from .address import AddressCandidate
from .database import connect_read_only

Direction = Literal["in", "out", "both"]


class GraphStore:
    """Read-only graph repository with recursive CTE traversal hidden inside."""

    def __init__(self, db_path: str | Path, *, project_root: str | Path) -> None:
        self.db_path = Path(db_path).resolve()
        self.project_root = Path(project_root).resolve()

    def address_candidates(self) -> list[AddressCandidate]:
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                """
                SELECT n.id, n.kind, n.title, a.alias
                FROM live_node AS n
                LEFT JOIN node_alias AS a ON a.node = n.id
                ORDER BY n.id, a.alias
                """
            ).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = grouped.setdefault(
                row["id"],
                {"id": row["id"], "kind": row["kind"], "title": row["title"], "aliases": []},
            )
            if row["alias"] is not None:
                item["aliases"].append(row["alias"])
        return [
            AddressCandidate(
                id=item["id"],
                kind=item["kind"],
                title=item["title"],
                aliases=tuple(item["aliases"]),
            )
            for item in grouped.values()
        ]

    def outline(
        self,
        scope: str | None,
        *,
        depth: int,
        kinds: Iterable[str] | None,
    ) -> list[dict[str, Any]]:
        kind_values = tuple(kinds or ())
        kind_sql, kind_params = self._in_filter("n.kind", kind_values)
        with connect_read_only(self.db_path) as connection:
            if scope is None:
                seed_sql = """
                    SELECT n.id, 0, char(31) || n.id || char(31)
                    FROM live_node AS n
                    WHERE NOT EXISTS (
                        SELECT 1 FROM live_edge AS parent
                        WHERE parent.rel = 'contains' AND parent.dst = n.id
                    )
                """
                params: list[Any] = [depth]
            else:
                seed_sql = "SELECT ?, 0, char(31) || ? || char(31)"
                params = [scope, scope, depth]
            sql = f"""
                WITH RECURSIVE walk(id, depth, path) AS (
                    {seed_sql}
                    UNION ALL
                    SELECT e.dst, walk.depth + 1,
                           walk.path || e.dst || char(31)
                    FROM live_edge AS e
                    JOIN walk ON e.src = walk.id
                    WHERE e.rel = 'contains'
                      AND e.hard = 1
                      AND walk.depth < ?
                      AND instr(walk.path, char(31) || e.dst || char(31)) = 0
                )
                SELECT DISTINCT n.id, n.kind, n.title, n.summary,
                       MIN(walk.depth) AS depth,
                       n.story_from
                FROM walk
                JOIN live_node AS n ON n.id = walk.id
                WHERE 1 = 1 {kind_sql}
                GROUP BY n.id, n.kind, n.title, n.summary, n.story_from
                ORDER BY COALESCE(n.story_from, 2147483647), depth, n.id
            """
            rows = connection.execute(sql, [*params, *kind_params]).fetchall()
        return [
            {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "summary": row["summary"],
            }
            for row in rows
        ]

    def walk(
        self,
        start: str,
        *,
        direction: Direction = "out",
        relations: Iterable[str] | None = None,
        max_depth: int = 5,
        include_soft: bool = False,
        as_of: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return reachable nodes while preventing cycles inside the recursive CTE."""
        if direction not in {"in", "out"}:
            raise ValueError("walk direction은 in 또는 out이어야 합니다")
        next_id = "e.dst" if direction == "out" else "e.src"
        join_id = "e.src" if direction == "out" else "e.dst"
        rel_values = tuple(relations or ())
        rel_sql, rel_params = self._in_filter("e.rel", rel_values)
        soft_sql = "" if include_soft else "AND e.hard = 1"
        story_sql = "" if as_of is None else "AND (e.story_from IS NULL OR e.story_from <= ?)"
        params: list[Any] = [start, start, max_depth, *rel_params]
        if as_of is not None:
            params.append(as_of)
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                f"""
                WITH RECURSIVE reach(id, depth, path) AS (
                    SELECT ?, 0, char(31) || ? || char(31)
                    UNION ALL
                    SELECT {next_id}, reach.depth + 1,
                           reach.path || {next_id} || char(31)
                    FROM live_edge AS e
                    JOIN reach ON {join_id} = reach.id
                    WHERE reach.depth < ?
                      {soft_sql}
                      {rel_sql}
                      {story_sql}
                      AND instr(reach.path, char(31) || {next_id} || char(31)) = 0
                )
                SELECT id, MIN(depth) AS depth
                FROM reach
                GROUP BY id
                ORDER BY depth, id
                """,
                params,
            ).fetchall()
        return [{"id": row["id"], "depth": row["depth"]} for row in rows]

    def find(
        self,
        query: str,
        *,
        kinds: Iterable[str] | None,
        tags: Iterable[str] | None,
        as_of: int | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        kind_values = tuple(kinds or ())
        tag_values = tuple(tags or ())
        kind_sql, kind_params = self._in_filter("n.kind", kind_values)
        reveal_sql = "" if as_of is None else "AND (n.reveal_at IS NULL OR n.reveal_at <= ?)"
        tag_sql = ""
        tag_params: list[Any] = []
        if tag_values:
            placeholders = ",".join("?" for _ in tag_values)
            tag_sql = f"""
                AND n.id IN (
                    SELECT nt.node FROM node_tag AS nt
                    WHERE nt.tag IN ({placeholders})
                    GROUP BY nt.node HAVING COUNT(DISTINCT nt.tag) = ?
                )
            """
            tag_params = [*tag_values, len(tag_values)]
        fts_query = " AND ".join(
            f'"{token.replace(chr(34), chr(34) * 2)}"' for token in query.split()
        )
        scored: dict[str, dict[str, Any]] = {}
        with connect_read_only(self.db_path) as connection:
            params: list[Any] = [fts_query, *kind_params]
            if as_of is not None:
                params.append(as_of)
            params.extend(tag_params)
            params.append(limit)
            try:
                rows = connection.execute(
                    f"""
                    SELECT n.id, n.kind, n.title, n.summary,
                           (-bm25(node_fts, 0.0, 5.0, 4.0, 2.0, 1.0)) AS score
                    FROM node_fts
                    JOIN live_node AS n ON n.id = node_fts.id
                    WHERE node_fts MATCH ?
                      {kind_sql}
                      {reveal_sql}
                      {tag_sql}
                    ORDER BY bm25(node_fts, 0.0, 5.0, 4.0, 2.0, 1.0), n.id
                    LIMIT ?
                    """,
                    params,
                ).fetchall()
            except sqlite3.OperationalError as exc:
                raise ValueError(f"검색 질의를 해석할 수 없습니다: {query!r}") from exc
            for row in rows:
                scored[row["id"]] = {
                    "id": row["id"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "score": round(float(row["score"]), 6),
                }

            fallback_params: list[Any] = [*kind_params]
            if as_of is not None:
                fallback_params.append(as_of)
            fallback_params.extend(tag_params)
            fallback_rows = connection.execute(
                f"""
                SELECT DISTINCT n.id, n.kind, n.title, n.summary, a.alias
                FROM live_node AS n
                LEFT JOIN node_alias AS a ON a.node = n.id
                WHERE 1 = 1
                  {kind_sql}
                  {reveal_sql}
                  {tag_sql}
                ORDER BY n.id
                """,
                fallback_params,
            ).fetchall()

        normalized = query.casefold()
        for row in fallback_rows:
            values = [row["title"], row["id"].rsplit("/", 1)[-1], row["alias"]]
            normalized_values = [value.casefold() for value in values if value]
            exact = any(normalized == value for value in normalized_values)
            partial = any(normalized in value for value in normalized_values)
            if not exact and not partial:
                continue
            score = 100.0 if exact else 50.0
            existing = scored.get(row["id"])
            if existing is None or score > existing["score"]:
                scored[row["id"]] = {
                    "id": row["id"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "score": score,
                }
        return sorted(scored.values(), key=lambda item: (-item["score"], item["id"]))[:limit]

    def get_nodes(
        self,
        ids: Iterable[str],
        *,
        include: Literal["brief", "full", "body"],
        as_of: int | None,
    ) -> list[dict[str, Any]]:
        node_ids = tuple(ids)
        if not node_ids:
            return []
        placeholders = ",".join("?" for _ in node_ids)
        reveal_sql = "" if as_of is None else "AND (n.reveal_at IS NULL OR n.reveal_at <= ?)"
        params: list[Any] = [*node_ids]
        if as_of is not None:
            params.append(as_of)
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT n.* FROM live_node AS n
                WHERE n.id IN ({placeholders}) {reveal_sql}
                ORDER BY n.id
                """,
                params,
            ).fetchall()
            by_id = {row["id"]: row for row in rows}
            if include == "brief":
                return [self._brief(by_id[node_id]) for node_id in node_ids if node_id in by_id]
            aliases = self._group_rows(connection, "node_alias", "node", node_ids, "alias")
            tags = self._group_rows(connection, "node_tag", "node", node_ids, "tag")
            features = self._feature_rows(connection, node_ids)
            evidence = self._evidence_rows(connection, node_ids)
            visibility = self._visibility_rows(connection, node_ids)
        result: list[dict[str, Any]] = []
        for node_id in node_ids:
            row = by_id.get(node_id)
            if row is None:
                continue
            item = self._full(
                row,
                aliases=aliases[node_id],
                tags=tags[node_id],
                features=features[node_id],
                evidence=evidence[node_id],
                visibility=visibility[node_id],
            )
            if include == "body":
                item["body"] = self._read_evidence_body(evidence[node_id])
            result.append(item)
        return result

    def refs(
        self,
        node_id: str,
        *,
        direction: Direction,
        relations: Iterable[str] | None,
        include_soft: bool,
        as_of: int | None,
    ) -> list[dict[str, Any]]:
        if direction not in {"in", "out", "both"}:
            raise ValueError("dir은 in, out, both 중 하나여야 합니다")
        relation_values = tuple(relations or ())
        rel_sql, rel_params = self._in_filter("e.rel", relation_values)
        hard_sql = "" if include_soft else "AND e.hard = 1"
        story_sql = "" if as_of is None else "AND (e.story_from IS NULL OR e.story_from <= ?)"
        reveal_sql = "" if as_of is None else "AND (n.reveal_at IS NULL OR n.reveal_at <= ?)"
        selects: list[str] = []
        params: list[Any] = []
        requested = ("in", "out") if direction == "both" else (direction,)
        for current in requested:
            if current == "in":
                endpoint = "e.src"
                predicate = "e.dst = ?"
            else:
                endpoint = "e.dst"
                predicate = "e.src = ?"
            selects.append(
                f"""
                SELECT e.rel, n.id AS node_id, n.title, e.hard, e.story_from, e.story_to,
                       '{current}' AS direction
                FROM live_edge AS e
                JOIN live_node AS n ON n.id = {endpoint}
                WHERE {predicate} {rel_sql} {hard_sql} {story_sql} {reveal_sql}
                """
            )
            params.append(node_id)
            params.extend(rel_params)
            if as_of is not None:
                params.extend([as_of, as_of])
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                " UNION ALL ".join(selects) + " ORDER BY rel, node_id, direction", params
            ).fetchall()
        return [
            {
                "rel": row["rel"],
                "id": row["node_id"],
                "title": row["title"],
                "hard": bool(row["hard"]),
                "story_range": [row["story_from"], row["story_to"]],
                "direction": row["direction"],
            }
            for row in rows
        ]

    def tags(self) -> list[dict[str, Any]]:
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute("SELECT name, schema FROM tag ORDER BY name").fetchall()
        return [{"name": row["name"], "schema": self._json(row["schema"], {})} for row in rows]

    @staticmethod
    def _in_filter(column: str, values: tuple[str, ...]) -> tuple[str, list[Any]]:
        if not values:
            return "", []
        placeholders = ",".join("?" for _ in values)
        return f"AND {column} IN ({placeholders})", list(values)

    @staticmethod
    def _brief(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "summary": row["summary"],
            "rev": row["rev"],
        }

    @classmethod
    def _full(
        cls,
        row: sqlite3.Row,
        *,
        aliases: list[str],
        tags: list[str],
        features: dict[str, Any],
        evidence: list[dict[str, Any]],
        visibility: list[dict[str, Any]],
    ) -> dict[str, Any]:
        item = cls._brief(row)
        item.update(
            {
                "aliases": aliases,
                "tags": tags,
                "features": features,
                "props": cls._json(row["props"], {}),
                "story_from": row["story_from"],
                "story_to": row["story_to"],
                "reveal_at": row["reveal_at"],
                "visible_to": [entry["viewer"] for entry in visibility],
                "visibility": visibility,
                "evidence": evidence,
                "origin": row["origin"],
                "locked": bool(row["locked"]),
                "cid": row["cid"],
            }
        )
        return item

    @staticmethod
    def _group_rows(
        connection: sqlite3.Connection,
        table: str,
        key_column: str,
        ids: tuple[str, ...],
        value_column: str,
    ) -> defaultdict[str, list[str]]:
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"SELECT {key_column}, {value_column} FROM {table} "
            f"WHERE {key_column} IN ({placeholders}) ORDER BY {key_column}, {value_column}",
            ids,
        ).fetchall()
        result: defaultdict[str, list[str]] = defaultdict(list)
        for row in rows:
            result[row[key_column]].append(row[value_column])
        return result

    @classmethod
    def _feature_rows(
        cls, connection: sqlite3.Connection, ids: tuple[str, ...]
    ) -> defaultdict[str, dict[str, Any]]:
        placeholders = ",".join("?" for _ in ids)
        sql = (
            "SELECT node, name, data FROM feature "
            f"WHERE node IN ({placeholders}) ORDER BY node, name"
        )
        rows = connection.execute(
            sql,
            ids,
        ).fetchall()
        result: defaultdict[str, dict[str, Any]] = defaultdict(dict)
        for row in rows:
            result[row["node"]][row["name"]] = cls._json(row["data"], {})
        return result

    @staticmethod
    def _evidence_rows(
        connection: sqlite3.Connection, ids: tuple[str, ...]
    ) -> defaultdict[str, list[dict[str, Any]]]:
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""
            SELECT node, file, start_off, end_off, quote
            FROM evidence WHERE node IN ({placeholders})
            ORDER BY node, file, start_off
            """,
            ids,
        ).fetchall()
        result: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            result[row["node"]].append(
                {
                    "file": row["file"],
                    "start": row["start_off"],
                    "end": row["end_off"],
                    "quote": row["quote"],
                }
            )
        return result

    @staticmethod
    def _visibility_rows(
        connection: sqlite3.Connection, ids: tuple[str, ...]
    ) -> defaultdict[str, list[dict[str, Any]]]:
        placeholders = ",".join("?" for _ in ids)
        rows = connection.execute(
            f"""
            SELECT fact, viewer, learned_at, pathway
            FROM visibility WHERE fact IN ({placeholders})
            ORDER BY fact, viewer
            """,
            ids,
        ).fetchall()
        result: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            result[row["fact"]].append(
                {
                    "viewer": row["viewer"],
                    "learned_at": row["learned_at"],
                    "pathway": row["pathway"],
                }
            )
        return result

    def _read_evidence_body(self, evidence: list[dict[str, Any]]) -> str:
        spans: list[str] = []
        for item in evidence:
            candidate = (self.project_root / item["file"]).resolve()
            if self.project_root not in candidate.parents or not candidate.is_file():
                continue
            raw = candidate.read_bytes()
            start = item["start"] if isinstance(item["start"], int) else 0
            end = item["end"] if isinstance(item["end"], int) else len(raw)
            start = max(0, min(start, len(raw)))
            end = max(start, min(end, len(raw)))
            spans.append(raw[start:end].decode("utf-8", errors="replace"))
        return "\n\n".join(spans)

    @staticmethod
    def _json(value: str | None, fallback: Any) -> Any:
        if value is None:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

"""P3 graph paths, context neighborhoods, impact previews, and SQL escape hatch."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .database import connect_read_only
from .diagnostics import DiagnosticEngine
from .search import HybridSearch
from .traverse import GraphStore


class GraphAnalysis:
    def __init__(
        self,
        *,
        db_path: str | Path,
        graph: GraphStore,
        search: HybridSearch,
        diagnostics: DiagnosticEngine,
        device_kinds: set[str],
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.graph = graph
        self.search = search
        self.diagnostics = diagnostics
        self.device_kinds = device_kinds

    def trace(
        self,
        source: str,
        *,
        target: str | None,
        relations: list[str] | None,
        max_depth: int,
        k: int,
    ) -> list[dict[str, Any]]:
        relation_values = tuple(relations or ())
        relation_filter = ""
        params: list[Any] = [source, source, max_depth]
        if relation_values:
            placeholders = ",".join("?" for _ in relation_values)
            relation_filter = f"AND e.rel IN ({placeholders})"
            params.extend(relation_values)
        if target is not None:
            target_filter = "AND paths.current = ?"
            params.append(target)
        else:
            placeholders = ",".join("?" for _ in self.device_kinds)
            target_filter = f"AND n.kind IN ({placeholders})"
            params.extend(sorted(self.device_kinds))
        params.append(k)
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                f"""
                WITH RECURSIVE paths(current, path, rels, depth) AS (
                  SELECT ?, json_array(?), json_array(), 0
                  UNION ALL
                  SELECT e.dst,
                         json_insert(paths.path, '$[#]', e.dst),
                         json_insert(paths.rels, '$[#]', e.rel),
                         paths.depth + 1
                  FROM paths JOIN live_edge AS e ON e.src=paths.current
                  WHERE paths.depth < ? AND e.hard=1
                    {relation_filter}
                    AND NOT EXISTS (
                      SELECT 1 FROM json_each(paths.path) WHERE value=e.dst
                    )
                )
                SELECT paths.current, paths.path, paths.rels, paths.depth
                FROM paths JOIN live_node AS n ON n.id=paths.current
                WHERE paths.depth > 0 {target_filter}
                ORDER BY paths.depth, paths.path
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            {
                "path": json.loads(row["path"]),
                "rels": json.loads(row["rels"]),
                "depth": row["depth"],
            }
            for row in rows
        ]

    def neighborhood(
        self,
        intent: str,
        *,
        anchors: list[str],
        as_of: int | None,
        budget_tokens: int,
    ) -> dict[str, Any]:
        ranked = self.search.find(
            intent,
            kinds=None,
            tags=None,
            as_of=as_of,
            mode="hybrid",
            limit=8,
        )
        seeds: list[str] = []
        for node_id in [*anchors, *(item["id"] for item in ranked)]:
            if node_id not in seeds:
                seeds.append(node_id)
        rank_by_id = {item["id"]: item["score"] for item in ranked}
        candidates: dict[str, dict[str, Any]] = {}
        briefs = self.graph.get_nodes(seeds, include="brief", as_of=as_of)
        for index, item in enumerate(briefs):
            candidates[item["id"]] = {
                **item,
                "score": 1.0 if item["id"] in anchors else rank_by_id.get(item["id"], 0.0),
                "reason": "anchor" if item["id"] in anchors else "search_seed",
                "relations": [],
                "order": index,
            }
        if seeds:
            placeholders = ",".join("?" for _ in seeds)
            cutoff = ""
            params: list[Any] = [*seeds, *seeds]
            if as_of is not None:
                cutoff = (
                    "AND (e.story_from IS NULL OR e.story_from <= ?) "
                    "AND (e.story_to IS NULL OR e.story_to >= ?) "
                    "AND (n.reveal_at IS NULL OR n.reveal_at <= ?)"
                )
                params.extend([as_of, as_of, as_of])
            with connect_read_only(self.db_path) as connection:
                rows = connection.execute(
                    f"""
                    SELECT e.src, e.dst, e.rel,
                           CASE WHEN e.src IN ({placeholders})
                                THEN e.dst ELSE e.src END AS neighbor,
                           n.kind, n.title, n.summary, n.rev
                    FROM live_edge AS e
                    JOIN live_node AS n ON n.id =
                      CASE WHEN e.src IN ({placeholders}) THEN e.dst ELSE e.src END
                    WHERE e.hard=1 AND (e.src IN ({placeholders}) OR e.dst IN ({placeholders}))
                      {cutoff}
                    ORDER BY e.rel, neighbor
                    """,
                    [*seeds, *seeds, *seeds, *seeds, *(params[len(seeds) * 2 :])],
                ).fetchall()
            for row in rows:
                node_id = row["neighbor"]
                item = candidates.setdefault(
                    node_id,
                    {
                        "id": node_id,
                        "kind": row["kind"],
                        "title": row["title"],
                        "summary": row["summary"],
                        "rev": row["rev"],
                        "score": 0.0,
                        "reason": "one_hop",
                        "relations": [],
                        "order": len(candidates),
                    },
                )
                relation = {"rel": row["rel"], "from": row["src"], "to": row["dst"]}
                if relation not in item["relations"]:
                    item["relations"].append(relation)
                item["score"] = max(item["score"], 0.5)
        ordered = sorted(
            candidates.values(),
            key=lambda item: (-float(item["score"]), item["order"], item["id"]),
        )
        packet: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        used = 0
        for item in ordered:
            item.pop("order", None)
            estimate = max(1, (len(json.dumps(item, ensure_ascii=False)) + 3) // 4)
            if used + estimate <= budget_tokens:
                packet.append(item)
                used += estimate
            else:
                dropped.append({"id": item["id"], "reason": "budget", "estimated_tokens": estimate})
        return {"packet": packet, "used_tokens": used, "dropped": dropped}

    def impact(self, ref: str, *, change: dict[str, Any], max_depth: int) -> dict[str, Any]:
        field = change.get("field")
        if not isinstance(field, str) or not field.strip() or "to" not in change:
            raise ValueError("change에는 비어 있지 않은 field와 to가 필요합니다")
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                """
                WITH RECURSIVE affected(id, depth, path) AS (
                  SELECT ?, 0, char(31)||?||char(31)
                  UNION ALL
                  SELECT e.src, affected.depth+1,
                         affected.path||e.src||char(31)
                  FROM affected JOIN live_edge AS e ON e.dst=affected.id
                  WHERE affected.depth < ? AND e.hard=1
                    AND instr(affected.path,char(31)||e.src||char(31))=0
                )
                SELECT a.id, MIN(a.depth) AS depth, n.kind, n.title, n.summary
                FROM affected AS a JOIN live_node AS n ON n.id=a.id
                GROUP BY a.id, n.kind, n.title, n.summary
                ORDER BY depth, a.id
                """,
                (ref, ref, max_depth),
            ).fetchall()
        affected = [
            {
                "id": row["id"],
                "depth": row["depth"],
                "kind": row["kind"],
                "title": row["title"],
            }
            for row in rows
        ]
        affected_ids = {item["id"] for item in affected}
        diagnostics = self.diagnostics.check(scope=None, rule_ids=None, severity=None)
        broken = {item["rule"] for item in diagnostics if affected_ids.intersection(item["nodes"])}
        if field in {"story_from", "story_to", "reveal_at"}:
            broken.update({"timeline.absolute", "timeline.simultaneity"})
        if field == "visible_to":
            broken.update({"character.knowledge", "character.memory"})
        if field.startswith("props.status"):
            broken.update({"plot.abandoned", "promise.unearned", "promise.premature"})
        estimate = sum(
            max(1, (len(item["title"]) + len(item["id"]) + len(item["kind"])) // 4)
            for item in affected
        )
        return {
            "ref": ref,
            "change": change,
            "affected": affected,
            "broken_rules": sorted(broken),
            "est_cost_tokens": estimate,
            "cycle_detected": "graph.causal_cycle" in broken,
        }


class QueryService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()

    def execute(self, sql: str, *, params: dict[str, Any] | None, limit: int) -> dict[str, Any]:
        statement = sql.strip()
        if not statement or ";" in statement:
            raise ValueError("sql은 세미콜론 없는 단일 읽기 질의여야 합니다")
        if not statement.casefold().startswith(("select", "with")):
            raise ValueError("query는 SELECT 또는 WITH 읽기 질의만 허용합니다")
        with connect_read_only(self.db_path) as connection:
            budget = 500

            def progress() -> int:
                nonlocal budget
                budget -= 1
                return int(budget <= 0)

            connection.set_progress_handler(progress, 10_000)
            try:
                cursor = connection.execute(
                    f"SELECT * FROM ({statement}) AS storyai_query LIMIT {limit + 1}",
                    params or {},
                )
                columns = [item[0] for item in cursor.description or []]
                raw_rows = cursor.fetchall()
            except sqlite3.Error as exc:
                raise ValueError(f"읽기 질의 실행 실패: {exc}") from exc
            finally:
                connection.set_progress_handler(None, 0)
        truncated = len(raw_rows) > limit
        rows = [[self._json_value(value) for value in tuple(row)] for row in raw_rows[:limit]]
        return {"columns": columns, "rows": rows, "truncated": truncated}

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, bytes):
            return {"$blob": value.hex()}
        return value

"""Read models for the P4 UI, built over the same graph database and core service."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from storyai import __version__

from .core.database import connect_read_only
from .core.service import StoryService


class UIDataStore:
    MAX_GRAPH_NODES = 500

    def __init__(self, service: StoryService) -> None:
        self.service = service
        self.db_path = Path(service.db_path).resolve()

    @property
    def public_kinds(self) -> tuple[str, ...]:
        return tuple(
            name
            for name, specification in self.service.ontology.kinds.items()
            if not specification.internal
        )

    def _public_filter(self, column: str = "kind") -> tuple[str, list[str]]:
        placeholders = ",".join("?" for _ in self.public_kinds)
        return f"{column} IN ({placeholders})", list(self.public_kinds)

    def graph(self, *, as_of: int | None = None) -> dict[str, Any]:
        if as_of is not None and (isinstance(as_of, bool) or as_of < 0):
            raise ValueError("as_of는 0 이상의 정수여야 합니다")
        cutoff = "" if as_of is None else "AND (reveal_at IS NULL OR reveal_at <= ?)"
        public_filter, public_params = self._public_filter()
        params: list[Any] = [*([] if as_of is None else [as_of]), *public_params]
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT id, kind, title, summary, props, story_from, story_to, reveal_at,
                       origin, locked, rev
                FROM live_node
                WHERE 1=1 {cutoff} AND {public_filter}
                ORDER BY COALESCE(story_from, reveal_at, 2147483647), kind, id
                LIMIT ?
                """,
                [*params, self.MAX_GRAPH_NODES + 1],
            ).fetchall()
            truncated = len(rows) > self.MAX_GRAPH_NODES
            rows = rows[: self.MAX_GRAPH_NODES]
            node_ids = [row["id"] for row in rows]
            tags: dict[str, list[str]] = defaultdict(list)
            diagnostics: Counter[str] = Counter()
            edges: list[dict[str, Any]] = []
            if node_ids:
                placeholders = ",".join("?" for _ in node_ids)
                for row in connection.execute(
                    f"SELECT node, tag FROM node_tag WHERE node IN ({placeholders}) "
                    "ORDER BY node, tag",
                    node_ids,
                ).fetchall():
                    tags[row["node"]].append(row["tag"])
                for row in connection.execute(
                    f"""
                    SELECT node, COUNT(*) AS count FROM diagnostic
                    WHERE resolved_at IS NULL AND node IN ({placeholders})
                    GROUP BY node
                    """,
                    node_ids,
                ).fetchall():
                    diagnostics[row["node"]] = int(row["count"])
                edge_cutoff = "" if as_of is None else "AND (story_from IS NULL OR story_from <= ?)"
                edge_params = [*node_ids, *node_ids]
                if as_of is not None:
                    edge_params.append(as_of)
                edge_rows = connection.execute(
                    f"""
                    SELECT id, src, dst, rel, hard, origin, confidence
                    FROM live_edge
                    WHERE src IN ({placeholders}) AND dst IN ({placeholders})
                      {edge_cutoff}
                    ORDER BY src, rel, dst, id
                    """,
                    edge_params,
                ).fetchall()
                edges = [
                    {
                        "id": int(row["id"]),
                        "source": row["src"],
                        "target": row["dst"],
                        "rel": row["rel"],
                        "hard": bool(row["hard"]),
                        "origin": row["origin"],
                        "confidence": row["confidence"],
                    }
                    for row in edge_rows
                ]
            graph = connection.execute(
                "SELECT revision, root_cid, updated_at FROM graph_state WHERE singleton=1"
            ).fetchone()
        nodes = [
            {
                "id": row["id"],
                "kind": row["kind"],
                "layer": self.service.ontology.kinds[row["kind"]].layer,
                "title": row["title"],
                "summary": row["summary"],
                "props": self._json(row["props"], {}),
                "tags": tags[row["id"]],
                "story_from": row["story_from"],
                "story_to": row["story_to"],
                "reveal_at": row["reveal_at"],
                "origin": row["origin"],
                "locked": bool(row["locked"]),
                "rev": int(row["rev"]),
                "diagnostics": diagnostics[row["id"]],
            }
            for row in rows
        ]
        return {
            "nodes": nodes,
            "edges": edges,
            "kind_counts": dict(sorted(Counter(item["kind"] for item in nodes).items())),
            "truncated": truncated,
            "cap": self.MAX_GRAPH_NODES,
            "graph_revision": int(graph["revision"]) if graph else 0,
            "root_cid": graph["root_cid"] if graph else "",
            "updated_at": graph["updated_at"] if graph else None,
        }

    def node(self, ref: str, *, as_of: int | None = None) -> dict[str, Any]:
        node_id = self.service.addresses.resolve(ref)
        full_values = self.service.get(
            node_id,
            include="full",
            as_of=as_of,
            response_format="detailed",
        )
        body_values = self.service.get(
            node_id,
            include="body",
            as_of=as_of,
            response_format="detailed",
        )
        if not full_values or not body_values:
            raise ValueError(f"해당 시점에 공개되지 않은 노드입니다: {node_id}")
        full = full_values[0]
        body = body_values[0]
        if self.service.ontology.kinds[full["kind"]].internal:
            raise ValueError(f"UI에 공개되지 않는 운영 노드입니다: {node_id}")
        refs = self.service.refs(
            node_id,
            dir="both",
            include_soft=True,
            as_of=as_of,
            response_format="detailed",
        )
        ref_nodes = self.service.store.get_nodes(
            list(dict.fromkeys(item["id"] for item in refs)),
            include="brief",
            as_of=as_of,
        )
        ref_kinds = {item["id"]: item["kind"] for item in ref_nodes}
        refs = [
            {**item, "kind": ref_kinds[item["id"]]}
            for item in refs
            if item["id"] in ref_kinds
            and not self.service.ontology.kinds[ref_kinds[item["id"]]].internal
        ]
        with connect_read_only(self.db_path) as connection:
            history_rows = connection.execute(
                """
                SELECT r.rev, r.cid, r.tx_from, r.tx_to, r.proposal, r.snapshot,
                       p.actor_kind, p.model_id, p.host, p.rationale
                FROM node_revision AS r
                LEFT JOIN proposal AS p ON p.id=r.proposal
                WHERE r.node=? ORDER BY r.rev DESC LIMIT 50
                """,
                (node_id,),
            ).fetchall()
            field_rows = connection.execute(
                """
                SELECT rev, field, attributed_to, on_behalf_of, ts
                FROM field_provenance WHERE node=? ORDER BY rev DESC, field
                """,
                (node_id,),
            ).fetchall()
        fields: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in field_rows:
            fields[int(row["rev"])].append(
                {
                    "field": row["field"],
                    "attributed_to": row["attributed_to"],
                    "on_behalf_of": row["on_behalf_of"],
                    "ts": row["ts"],
                }
            )
        history = []
        for row in history_rows:
            snapshot = self._json(row["snapshot"], {})
            history.append(
                {
                    "rev": int(row["rev"]),
                    "cid": row["cid"],
                    "tx_from": row["tx_from"],
                    "tx_to": row["tx_to"],
                    "proposal": row["proposal"],
                    "origin": snapshot.get("origin", row["actor_kind"] or "human"),
                    "actor_kind": row["actor_kind"],
                    "model_id": row["model_id"],
                    "host": row["host"],
                    "rationale": row["rationale"],
                    "fields": fields[int(row["rev"])],
                }
            )
        return {
            **full,
            "body": body.get("body", ""),
            "evidence": body.get("evidence", []),
            "refs": refs,
            "history": history,
            "layer": self.service.ontology.kinds[full["kind"]].layer,
        }

    def promises(self, *, as_of: int | None = None) -> list[dict[str, Any]]:
        values = self.service.promise_store.list(statuses=None, as_of=as_of, sort="debt")
        if not values:
            return []
        ids = [item["id"] for item in values]
        briefs = self.service.store.get_nodes(ids, include="brief", as_of=as_of)
        titles = {item["id"]: item["title"] for item in briefs}
        return [{**item, "title": titles.get(item["id"], item["id"])} for item in values]

    def timeline(self) -> dict[str, Any]:
        public_filter, public_params = self._public_filter()
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT id, kind, title, story_from, story_to, reveal_at
                FROM live_node
                WHERE story_from IS NOT NULL AND reveal_at IS NOT NULL
                  AND {public_filter}
                ORDER BY reveal_at, story_from, id
                """,
                public_params,
            ).fetchall()
            maximum_row = connection.execute(
                f"""
                SELECT MAX(value) AS value FROM (
                  SELECT story_from AS value FROM live_node WHERE {public_filter}
                  UNION ALL SELECT story_to FROM live_node WHERE {public_filter}
                  UNION ALL SELECT reveal_at FROM live_node WHERE {public_filter}
                )
                """,
                [*public_params, *public_params, *public_params],
            ).fetchone()
        points = [
            {
                "id": row["id"],
                "kind": row["kind"],
                "title": row["title"],
                "story": int(row["story_from"]),
                "story_to": row["story_to"],
                "discourse": int(row["reveal_at"]),
                "flashback": int(row["story_from"]) < int(row["reveal_at"]),
            }
            for row in rows
        ]
        maximum = max(1, int(maximum_row["value"] or 0))
        return {"points": points, "max_chapter": maximum}

    def proposals(self, proposal_id: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE p.id=?" if proposal_id else "WHERE p.status != 'accepted'"
        params = (proposal_id,) if proposal_id else ()
        with connect_read_only(self.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT p.*, a.risk, a.reasons, a.conflicts, a.pending_overlap
                FROM proposal AS p
                JOIN proposal_assessment AS a ON a.proposal=p.id
                {where}
                ORDER BY p.ts DESC, p.id
                """,
                params,
            ).fetchall()
            result = []
            for row in rows:
                op_rows = connection.execute(
                    """
                    SELECT seq, verb, target, field, from_val, to_val, basis_rev, idem_key
                    FROM op WHERE proposal=? ORDER BY seq
                    """,
                    (row["id"],),
                ).fetchall()
                result.append(
                    {
                        "id": row["id"],
                        "status": row["status"],
                        "risk": row["risk"],
                        "reasons": self._json(row["reasons"], []),
                        "conflicts": self._json(row["conflicts"], []),
                        "pending_overlap": self._json(row["pending_overlap"], []),
                        "actor_kind": row["actor_kind"],
                        "model_id": row["model_id"],
                        "session_id": row["session_id"],
                        "host": row["host"],
                        "rationale": row["rationale"],
                        "read_set": self._json(row["read_set"], []),
                        "ts": row["ts"],
                        "ops": [
                            {
                                "seq": int(op["seq"]),
                                "verb": op["verb"],
                                "target": op["target"],
                                "field": op["field"],
                                "from": self._json(op["from_val"], None),
                                "to": self._json(op["to_val"], None),
                                "basis_rev": op["basis_rev"],
                                "idem_key": op["idem_key"],
                            }
                            for op in op_rows
                        ],
                    }
                )
        if proposal_id and not result:
            raise ValueError(f"제안을 찾을 수 없습니다: {proposal_id}")
        return result

    def status(self) -> dict[str, Any]:
        public_filter, public_params = self._public_filter()
        with connect_read_only(self.db_path) as connection:
            nodes = connection.execute(
                f"SELECT COUNT(*) AS count FROM live_node WHERE {public_filter}",
                public_params,
            ).fetchone()
            edges = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM live_edge AS edge
                JOIN live_node AS source ON source.id=edge.src
                JOIN live_node AS target ON target.id=edge.dst
                WHERE {public_filter.replace("kind", "source.kind")}
                  AND {public_filter.replace("kind", "target.kind")}
                """,
                [*public_params, *public_params],
            ).fetchone()
            diagnostics = connection.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM diagnostic AS diagnostic
                JOIN live_node AS node ON node.id=diagnostic.node
                WHERE diagnostic.resolved_at IS NULL
                  AND {public_filter.replace("kind", "node.kind")}
                """,
                public_params,
            ).fetchone()
            operations = connection.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM proposal WHERE status='open') AS pending,
                  (SELECT MAX(updated_at) FROM node_embedding) AS indexed_at
                """
            ).fetchone()
        eligible = len(self.service.promise_store.list(statuses=["eligible"], as_of=None))
        hypothetical = len(self.service.promise_store.list(statuses=["hypothetical"], as_of=None))
        return {
            "book": self.service.project_root.name,
            "version": __version__,
            "connected": True,
            "nodes": int(nodes["count"]),
            "edges": int(edges["count"]),
            "pending": int(operations["pending"]),
            "diagnostics": int(diagnostics["count"]),
            "eligible_promises": eligible,
            "open_promises": eligible + hypothetical,
            "indexed_at": operations["indexed_at"],
            "database_bytes": self.db_path.stat().st_size,
        }

    @staticmethod
    def _json(value: str | None, fallback: Any) -> Any:
        if value is None:
            return fallback
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return fallback

"""Deterministic graph invariants shared by bootstrap and proposal writes."""

from __future__ import annotations

import sqlite3
from heapq import heapify, heappop, heappush

from .ontology import Ontology


class InvariantError(ValueError):
    pass


def _assert_acyclic(relation: str, adjacency: dict[str, list[str]]) -> None:
    nodes = set(adjacency)
    nodes.update(child for children in adjacency.values() for child in children)
    indegree = dict.fromkeys(nodes, 0)
    for children in adjacency.values():
        for child in children:
            indegree[child] += 1
    ready = [node for node, degree in indegree.items() if degree == 0]
    heapify(ready)
    visited = 0
    while ready:
        node = heappop(ready)
        visited += 1
        for child in adjacency.get(node, []):
            indegree[child] -= 1
            if indegree[child] == 0:
                heappush(ready, child)
    if visited != len(nodes):
        cycle_nodes = sorted(node for node, degree in indegree.items() if degree > 0)
        raise InvariantError(f"{relation} 그래프에 순환이 있습니다: {', '.join(cycle_nodes[:5])}")


def would_create_cycle(
    connection: sqlite3.Connection,
    *,
    source: str,
    target: str,
    relation: str,
) -> bool:
    if source == target:
        return True
    row = connection.execute(
        """
        WITH RECURSIVE reach(id) AS (
          SELECT ?
          UNION
          SELECT e.dst
          FROM live_edge AS e JOIN reach AS r ON e.src = r.id
          WHERE e.rel = ?
        )
        SELECT 1 FROM reach WHERE id = ? LIMIT 1
        """,
        (target, relation, source),
    ).fetchone()
    return row is not None


def validate_new_edge(
    connection: sqlite3.Connection,
    ontology: Ontology,
    *,
    source: str,
    target: str,
    relation: str,
) -> None:
    spec = ontology.edges[relation]
    if spec.constraint in {"dag", "acyclic"} and would_create_cycle(
        connection,
        source=source,
        target=target,
        relation=relation,
    ):
        raise InvariantError(f"{relation} 간선은 순환을 만들 수 없습니다: {source} -> {target}")
    if spec.max_per_dst is not None:
        count = connection.execute(
            "SELECT COUNT(*) FROM live_edge WHERE dst = ? AND rel = ?",
            (target, relation),
        ).fetchone()[0]
        if int(count) >= spec.max_per_dst:
            raise InvariantError(
                f"{relation} 간선은 대상당 최대 {spec.max_per_dst}개입니다: {target}"
            )


def validate_graph(connection: sqlite3.Connection, ontology: Ontology) -> None:
    for relation, spec in ontology.edges.items():
        if spec.constraint not in {"dag", "acyclic"}:
            continue
        rows = connection.execute(
            "SELECT src, dst FROM live_edge WHERE rel = ? ORDER BY id", (relation,)
        ).fetchall()
        adjacency: dict[str, list[str]] = {}
        for row in rows:
            adjacency.setdefault(row["src"], []).append(row["dst"])
        _assert_acyclic(relation, adjacency)
    for relation, spec in ontology.edges.items():
        if spec.max_per_dst is None:
            continue
        row = connection.execute(
            """
            SELECT dst, COUNT(*) AS count
            FROM live_edge WHERE rel = ?
            GROUP BY dst HAVING COUNT(*) > ?
            ORDER BY dst LIMIT 1
            """,
            (relation, spec.max_per_dst),
        ).fetchone()
        if row is not None:
            raise InvariantError(
                f"{relation} 간선이 대상당 최대 {spec.max_per_dst}개를 넘었습니다: "
                f"{row['dst']} ({row['count']})"
            )

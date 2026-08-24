"""Canonical node snapshots and graph Merkle state for P1."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json(value: str | None, fallback: Any) -> Any:
    if value is None:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def node_content(connection: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM node WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        raise ValueError(f"노드를 찾을 수 없습니다: {node_id}")
    aliases = [
        item["alias"]
        for item in connection.execute(
            "SELECT alias FROM node_alias WHERE node = ? ORDER BY alias", (node_id,)
        ).fetchall()
    ]
    tags = [
        item["tag"]
        for item in connection.execute(
            "SELECT tag FROM node_tag WHERE node = ? ORDER BY tag", (node_id,)
        ).fetchall()
    ]
    features = {
        item["name"]: _json(item["data"], {})
        for item in connection.execute(
            "SELECT name, data FROM feature WHERE node = ? ORDER BY name", (node_id,)
        ).fetchall()
    }
    visibility = [
        {
            "viewer": item["viewer"],
            "learned_at": item["learned_at"],
            "pathway": item["pathway"],
        }
        for item in connection.execute(
            """
            SELECT viewer, learned_at, pathway
            FROM visibility WHERE fact = ? ORDER BY viewer
            """,
            (node_id,),
        ).fetchall()
    ]
    edge_values = [
        {
            "rel": item["rel"],
            "to": item["dst"],
            "props": _json(item["props"], {}),
            "story_from": item["story_from"],
            "story_to": item["story_to"],
            "confidence": item["confidence"],
        }
        for item in connection.execute(
            """
            SELECT dst, rel, props, story_from, story_to, confidence
            FROM live_edge WHERE src = ?
            ORDER BY rel, dst, id
            """,
            (node_id,),
        ).fetchall()
    ]
    edge_values.sort(key=canonical_json)
    fts = connection.execute(
        "SELECT body FROM node_fts WHERE id = ? ORDER BY rowid DESC LIMIT 1", (node_id,)
    ).fetchone()
    content = {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "summary": row["summary"],
        "aliases": aliases,
        "tags": tags,
        "features": features,
        "props": _json(row["props"], {}),
        "edges": edge_values,
        "story_from": row["story_from"],
        "story_to": row["story_to"],
        "reveal_at": row["reveal_at"],
        "locked": bool(row["locked"]),
        "body": fts["body"] if fts is not None else "",
    }
    if visibility:
        content["visible_to"] = visibility
    return content


def node_snapshot(connection: sqlite3.Connection, node_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM node WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        raise ValueError(f"노드를 찾을 수 없습니다: {node_id}")
    return {
        "content": node_content(connection, node_id),
        "rev": row["rev"],
        "cid": row["cid"],
        "origin": row["origin"],
        "tx_from": row["tx_from"],
        "tx_to": row["tx_to"],
    }


def refresh_node_cid(connection: sqlite3.Connection, node_id: str) -> str:
    cid = digest(node_content(connection, node_id))
    connection.execute("UPDATE node SET cid = ? WHERE id = ?", (cid, node_id))
    return cid


def record_revision(
    connection: sqlite3.Connection,
    node_id: str,
    *,
    proposal_id: str | None,
    replace: bool = False,
) -> None:
    snapshot = node_snapshot(connection, node_id)
    sql = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    connection.execute(
        f"""
        {sql} INTO node_revision(node, rev, snapshot, cid, tx_from, tx_to, proposal)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node_id,
            snapshot["rev"],
            canonical_json(snapshot),
            snapshot["cid"],
            snapshot["tx_from"],
            snapshot["tx_to"],
            proposal_id,
        ),
    )


def graph_root(connection: sqlite3.Connection) -> str:
    nodes = [
        {"id": row["id"], "cid": row["cid"]}
        for row in connection.execute("SELECT id, cid FROM live_node ORDER BY id").fetchall()
    ]
    return digest(nodes)


def ensure_graph_state(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT id FROM node ORDER BY id").fetchall():
        node_id = row["id"]
        refresh_node_cid(connection, node_id)
        record_revision(connection, node_id, proposal_id=None)
    now = datetime.now(UTC).isoformat()
    root = graph_root(connection)
    connection.execute(
        """
        INSERT INTO graph_state(singleton, revision, root_cid, updated_at)
        VALUES (1, 0, ?, ?)
        ON CONFLICT(singleton) DO UPDATE SET root_cid=excluded.root_cid,
          updated_at=excluded.updated_at
        """,
        (root, now),
    )


def advance_graph_state(connection: sqlite3.Connection, now: str) -> tuple[int, str]:
    root = graph_root(connection)
    connection.execute(
        "UPDATE graph_state SET revision = revision + 1, root_cid = ?, updated_at = ? "
        "WHERE singleton = 1",
        (root, now),
    )
    row = connection.execute(
        "SELECT revision, root_cid FROM graph_state WHERE singleton = 1"
    ).fetchone()
    if row is None:
        raise RuntimeError("graph_state 초기화에 실패했습니다")
    return int(row["revision"]), str(row["root_cid"])

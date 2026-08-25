"""Implicit graph-work branches keyed by proposal session_id."""

from __future__ import annotations

import sqlite3
from typing import Any


class BranchService:
    @staticmethod
    def backfill(connection: sqlite3.Connection, now: str) -> None:
        graph = connection.execute(
            "SELECT revision FROM graph_state WHERE singleton = 1"
        ).fetchone()
        current_revision = int(graph["revision"]) if graph is not None else 0
        rows = connection.execute(
            """
            SELECT p.session_id, MIN(p.ts) AS created_at,
                   MIN(cr.graph_revision) AS first_revision,
                   MAX(cr.graph_revision) AS head_revision
            FROM proposal AS p
            LEFT JOIN commit_record AS cr ON cr.proposal = p.id
            WHERE p.session_id IS NOT NULL AND p.session_id <> ''
            GROUP BY p.session_id
            ORDER BY p.session_id
            """
        ).fetchall()
        for row in rows:
            first = row["first_revision"]
            head = row["head_revision"]
            base = max(0, int(first) - 1) if first is not None else current_revision
            connection.execute(
                """
                INSERT OR IGNORE INTO session_branch(
                  id, parent, base_revision, head_revision, status, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, 'active', ?, ?)
                """,
                (
                    row["session_id"],
                    base,
                    int(head) if head is not None else base,
                    row["created_at"] or now,
                    now,
                ),
            )

    @staticmethod
    def ensure(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        parent: str | None,
        now: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM session_branch WHERE id = ?", (session_id,)
        ).fetchone()
        if row is not None:
            if parent is not None and row["parent"] != parent:
                raise ValueError(f"기존 session branch의 parent와 다릅니다: {session_id}")
            connection.execute(
                "UPDATE session_branch SET status = 'active', updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            return BranchService.get(connection, session_id)
        if parent is not None:
            parent_row = connection.execute(
                "SELECT id FROM session_branch WHERE id = ?", (parent,)
            ).fetchone()
            if parent_row is None:
                raise ValueError(f"parent session branch를 찾을 수 없습니다: {parent}")
        graph = connection.execute(
            "SELECT revision FROM graph_state WHERE singleton = 1"
        ).fetchone()
        revision = int(graph["revision"]) if graph is not None else 0
        connection.execute(
            """
            INSERT INTO session_branch(
              id, parent, base_revision, head_revision, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (session_id, parent, revision, revision, now, now),
        )
        return BranchService.get(connection, session_id)

    @staticmethod
    def accepted(
        connection: sqlite3.Connection,
        session_id: str,
        graph_revision: int,
        now: str,
    ) -> dict[str, Any]:
        connection.execute(
            """
            UPDATE session_branch
            SET head_revision = ?, status = 'active', updated_at = ?
            WHERE id = ?
            """,
            (graph_revision, now, session_id),
        )
        return BranchService.get(connection, session_id)

    @staticmethod
    def conflicted(
        connection: sqlite3.Connection,
        session_id: str,
        now: str,
    ) -> dict[str, Any]:
        connection.execute(
            "UPDATE session_branch SET status = 'conflicted', updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        return BranchService.get(connection, session_id)

    @staticmethod
    def get(connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT id, parent, base_revision, head_revision, status, created_at, updated_at
            FROM session_branch WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"session branch를 찾을 수 없습니다: {session_id}")
        return dict(row)

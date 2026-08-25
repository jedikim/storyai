"""Advisory, expiring work-scope leases for concurrent agents."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from .database import connect_write

LeaseMode = Literal["acquire", "release", "list"]


class LeaseService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()

    def manage(
        self,
        *,
        mode: LeaseMode,
        session_id: str,
        scope: str | None = None,
        ttl_sec: int = 900,
        model_id: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        if mode not in {"acquire", "release", "list"}:
            raise ValueError("lease.mode는 acquire, release, list 중 하나여야 합니다")
        session_id = self._required(session_id, "session_id", 300)
        if model_id is not None:
            model_id = self._required(model_id, "model_id", 200)
        if note is not None:
            note = self._required(note, "note", 1000)
        normalized_scope = self._scope(scope) if scope is not None else None
        if mode in {"acquire", "release"} and normalized_scope is None:
            raise ValueError(f"lease.{mode}에는 scope가 필요합니다")
        if not isinstance(ttl_sec, int) or isinstance(ttl_sec, bool) or not 1 <= ttl_sec <= 86400:
            raise ValueError("ttl_sec는 1..86400 범위의 정수여야 합니다")

        now_value = datetime.now(UTC)
        now = now_value.isoformat()
        with connect_write(self.db_path) as connection:
            connection.execute("DELETE FROM lease WHERE expires_at <= ?", (now,))
            if mode == "list":
                leases = self._list(connection, scope=normalized_scope)
                return {
                    "mode": "list",
                    "holder": session_id,
                    "scope": normalized_scope,
                    "leases": leases,
                    "conflicts": [],
                }
            if mode == "release":
                rows = connection.execute(
                    "SELECT id FROM lease WHERE session_id = ? AND scope = ? ORDER BY id",
                    (session_id, normalized_scope),
                ).fetchall()
                connection.execute(
                    "DELETE FROM lease WHERE session_id = ? AND scope = ?",
                    (session_id, normalized_scope),
                )
                return {
                    "mode": "release",
                    "lease_id": str(rows[0]["id"]) if rows else None,
                    "scope": normalized_scope,
                    "holder": session_id,
                    "released": len(rows),
                    "conflicts": [],
                }

            assert normalized_scope is not None
            conflicts = [
                item
                for item in self._list(connection)
                if item["session_id"] != session_id
                and self.scopes_overlap(normalized_scope, str(item["scope"]))
            ]
            if conflicts:
                return {
                    "mode": "acquire",
                    "acquired": False,
                    "lease_id": None,
                    "scope": normalized_scope,
                    "holder": session_id,
                    "expires_at": None,
                    "conflicts": conflicts,
                }
            expires_at = (now_value + timedelta(seconds=ttl_sec)).isoformat()
            existing = connection.execute(
                "SELECT id FROM lease WHERE session_id = ? AND scope = ?",
                (session_id, normalized_scope),
            ).fetchone()
            if existing is not None:
                lease_id = str(existing["id"])
                connection.execute(
                    """
                    UPDATE lease
                    SET model_id = ?, note = ?, acquired_at = ?, expires_at = ?
                    WHERE id = ?
                    """,
                    (model_id, note, now, expires_at, lease_id),
                )
                renewed = True
            else:
                lease_id = f"lease/{uuid.uuid4().hex}"
                connection.execute(
                    """
                    INSERT INTO lease(
                      id, scope, session_id, model_id, note, acquired_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (lease_id, normalized_scope, session_id, model_id, note, now, expires_at),
                )
                renewed = False
            return {
                "mode": "acquire",
                "acquired": True,
                "renewed": renewed,
                "lease_id": lease_id,
                "scope": normalized_scope,
                "holder": session_id,
                "expires_at": expires_at,
                "conflicts": [],
            }

    @staticmethod
    def _list(
        connection: sqlite3.Connection,
        *,
        scope: str | None = None,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT id, scope, session_id, model_id, note, acquired_at, expires_at
            FROM lease ORDER BY scope, session_id, id
            """
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["lease_id"] = item.pop("id")
            result.append(item)
        if scope is not None:
            result = [
                item for item in result if LeaseService.scopes_overlap(scope, str(item["scope"]))
            ]
        return result

    @staticmethod
    def scopes_overlap(left: str, right: str) -> bool:
        if left == "book" or right == "book":
            return True
        left_base, left_wild = LeaseService._scope_parts(left)
        right_base, right_wild = LeaseService._scope_parts(right)
        if left_base == right_base:
            return True
        if left_wild and LeaseService._within(right_base, left_base):
            return True
        return bool(right_wild and LeaseService._within(left_base, right_base))

    @staticmethod
    def _scope_parts(scope: str) -> tuple[str, bool]:
        return (scope[:-2], True) if scope.endswith(".*") else (scope, False)

    @staticmethod
    def _within(value: str, parent: str) -> bool:
        return value == parent or value.startswith(parent + ".") or value.startswith(parent + "/")

    @staticmethod
    def _scope(value: str) -> str:
        scope = LeaseService._required(value, "scope", 300)
        if scope.startswith("story://"):
            scope = scope[8:]
        if any(character.isspace() or ord(character) < 32 for character in scope):
            raise ValueError("lease.scope에는 공백이나 제어 문자를 사용할 수 없습니다")
        if "*" in scope and not scope.endswith(".*"):
            raise ValueError("lease.scope wildcard는 끝의 .*만 지원합니다")
        base = scope[:-2] if scope.endswith(".*") else scope
        if base == "book" and scope != "book":
            raise ValueError("book scope에는 wildcard를 사용할 수 없습니다")
        if base != "book" and "/" not in base:
            raise ValueError("lease.scope는 book 또는 kind/id 주소여야 합니다")
        if base.startswith(("/", ".")) or base.endswith(("/", ".")):
            raise ValueError("lease.scope 주소의 시작과 끝 구분자가 잘못되었습니다")
        if base != "book":
            kind, identifier = base.split("/", 1)
            if not kind or not identifier:
                raise ValueError("lease.scope는 kind/id 주소여야 합니다")
        return scope

    @staticmethod
    def _required(value: str, field: str, limit: int) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"lease.{field}는 비어 있지 않은 문자열이어야 합니다")
        result = value.strip()
        if len(result) > limit:
            raise ValueError(f"lease.{field}는 {limit}자를 넘을 수 없습니다")
        return result

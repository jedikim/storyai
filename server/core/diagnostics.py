"""Spec-driven, SQL-only P2 continuity diagnostics."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import connect_read_only
from .merkle import canonical_json


class DiagnosticSpecError(ValueError):
    pass


_WRITE_ACTIONS = {
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_ATTACH,
    sqlite3.SQLITE_DETACH,
}


def _read_only_authorizer(
    action: int,
    _arg1: str | None,
    _arg2: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    return sqlite3.SQLITE_DENY if action in _WRITE_ACTIONS else sqlite3.SQLITE_OK


class DiagnosticEngine:
    def __init__(self, db_path: str | Path, rules_path: str | Path) -> None:
        self.db_path = Path(db_path).resolve()
        self.rules_path = Path(rules_path).resolve()
        try:
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DiagnosticSpecError(f"진단 규칙을 읽을 수 없습니다: {self.rules_path}") from exc
        catalog = data.get("rules")
        statements = data.get("p2_sql")
        if not isinstance(catalog, list) or not isinstance(statements, dict):
            raise DiagnosticSpecError("rules와 p2_sql 정의가 필요합니다")
        self.rules = {
            item["id"]: item
            for item in catalog
            if isinstance(item, dict) and item.get("id") in statements
        }
        self.statements = {
            rule_id: statement for rule_id, statement in statements.items() if rule_id != "$comment"
        }
        if set(self.rules) != set(self.statements):
            missing_meta = sorted(set(self.statements) - set(self.rules))
            missing_sql = sorted(set(self.rules) - set(self.statements))
            raise DiagnosticSpecError(
                f"규칙/SQL 불일치: metadata={missing_meta}, sql={missing_sql}"
            )
        for rule_id, statement in self.statements.items():
            normalized = statement.strip().casefold()
            if not normalized.startswith(("select", "with recursive")) or ";" in statement:
                raise DiagnosticSpecError(f"{rule_id}는 단일 읽기 전용 SQL이어야 합니다")

    def check(
        self,
        *,
        scope: str | None,
        rule_ids: list[str] | None,
        severity: str | None,
    ) -> list[dict[str, Any]]:
        with connect_read_only(self.db_path) as connection:
            return self.evaluate(
                connection,
                scope=scope,
                rule_ids=rule_ids,
                severity=severity,
            )

    def evaluate(
        self,
        connection: sqlite3.Connection,
        *,
        scope: str | None = None,
        rule_ids: list[str] | None = None,
        severity: str | None = None,
    ) -> list[dict[str, Any]]:
        if severity is not None and severity not in {"error", "warn", "info"}:
            raise ValueError("severity는 error, warn, info 중 하나여야 합니다")
        if rule_ids is not None and not rule_ids:
            raise ValueError("rules는 비어 있지 않은 배열이어야 합니다")
        selected = list(rule_ids) if rule_ids is not None else list(self.rules)
        unknown = sorted(set(selected) - set(self.rules))
        if unknown:
            raise ValueError(f"P2에서 구현되지 않은 진단 규칙: {', '.join(unknown)}")
        scope_ids = self._scope_ids(connection, scope)
        results: list[dict[str, Any]] = []
        for rule_id in selected:
            metadata = self.rules[rule_id]
            if severity is not None and metadata["severity"] != severity:
                continue
            try:
                connection.set_authorizer(_read_only_authorizer)
                rows = connection.execute(self.statements[rule_id]).fetchall()
            except sqlite3.Error as exc:
                raise DiagnosticSpecError(f"{rule_id} SQL 실행 실패: {exc}") from exc
            finally:
                connection.set_authorizer(None)
            for row in rows:
                node = str(row["node"])
                related = self._related(row["related"])
                nodes = list(dict.fromkeys([node, *related]))
                if scope_ids is not None and not scope_ids.intersection(nodes):
                    continue
                detail = row["detail"]
                message = metadata["desc"]
                if detail and detail != message:
                    message = f"{message}: {detail}"
                results.append(
                    {
                        "rule": rule_id,
                        "severity": metadata["severity"],
                        "nodes": nodes,
                        "evidence": self._evidence(connection, nodes),
                        "message": message,
                    }
                )
        rank = {"error": 0, "warn": 1, "info": 2}
        unique: dict[str, dict[str, Any]] = {}
        for item in results:
            unique[canonical_json(item)] = item
        return sorted(
            unique.values(),
            key=lambda item: (rank[item["severity"]], item["rule"], item["nodes"]),
        )

    def synchronize(
        self, connection: sqlite3.Connection, diagnostics: list[dict[str, Any]], now: str
    ) -> None:
        active_rows = connection.execute(
            """
            SELECT id, rule, node, related, message
            FROM diagnostic WHERE resolved_at IS NULL
            """
        ).fetchall()
        active = {
            self._fingerprint(
                row["rule"],
                row["node"],
                json.loads(row["related"] or "[]"),
                row["message"],
            ): row["id"]
            for row in active_rows
            if row["rule"] in self.rules
        }
        current: dict[str, dict[str, Any]] = {}
        for item in diagnostics:
            node = item["nodes"][0]
            related = item["nodes"][1:]
            key = self._fingerprint(item["rule"], node, related, item["message"])
            current[key] = item
        resolved = [diagnostic_id for key, diagnostic_id in active.items() if key not in current]
        if resolved:
            placeholders = ",".join("?" for _ in resolved)
            connection.execute(
                f"UPDATE diagnostic SET resolved_at = ? WHERE id IN ({placeholders})",
                [now, *resolved],
            )
        for key, item in current.items():
            if key in active:
                continue
            connection.execute(
                """
                INSERT INTO diagnostic(
                  rule, severity, node, related, message, evidence, detected_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    item["rule"],
                    item["severity"],
                    item["nodes"][0],
                    canonical_json(item["nodes"][1:]),
                    item["message"],
                    canonical_json(item["evidence"]),
                    now,
                ),
            )

    @staticmethod
    def _scope_ids(connection: sqlite3.Connection, scope: str | None) -> set[str] | None:
        if scope is None:
            return None
        rows = connection.execute(
            """
            WITH RECURSIVE descendants(id) AS (
              SELECT ?
              UNION
              SELECT e.dst FROM live_edge AS e JOIN descendants AS d ON e.src=d.id
              WHERE e.rel='contains'
            )
            SELECT id FROM descendants
            """,
            (scope,),
        ).fetchall()
        return {row["id"] for row in rows}

    @staticmethod
    def _related(value: Any) -> list[str]:
        if value is None:
            return []
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        return [str(item) for item in parsed if item is not None]

    @staticmethod
    def _evidence(connection: sqlite3.Connection, nodes: list[str]) -> list[dict[str, Any]]:
        if not nodes:
            return []
        placeholders = ",".join("?" for _ in nodes)
        rows = connection.execute(
            f"""
            SELECT node, file, start_off, end_off, quote
            FROM evidence WHERE node IN ({placeholders})
            ORDER BY node, file, start_off
            """,
            nodes,
        ).fetchall()
        return [
            {
                "node": row["node"],
                "file": row["file"],
                "start": row["start_off"],
                "end": row["end_off"],
                "quote": row["quote"],
            }
            for row in rows
        ]

    @staticmethod
    def _fingerprint(rule: str, node: str, related: list[str], message: str) -> str:
        return canonical_json([rule, node, sorted(related), message])


def utc_now() -> str:
    return datetime.now(UTC).isoformat()

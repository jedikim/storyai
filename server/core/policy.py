"""Deterministic P1 mutation-risk classification."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Operation
from .ontology import Ontology


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    risk: str
    reasons: tuple[str, ...]
    forbidden: tuple[str, ...]


class RiskPolicy:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"정책 파일을 읽을 수 없습니다: {self.path}: {exc}") from exc
        rank = self.data.get("rank")
        if rank != {"auto": 0, "review": 1, "always": 2}:
            raise ValueError("policy.rank는 auto/review/always 순서를 정의해야 합니다")
        rules = self.data.get("rules")
        if not isinstance(rules, list) or not rules:
            raise ValueError("policy.rules는 비어 있지 않은 배열이어야 합니다")
        for rule in rules:
            if (
                not isinstance(rule, dict)
                or rule.get("risk") not in rank
                or rule.get("effect") not in {"allow", "forbid"}
                or not isinstance(rule.get("when"), dict)
            ):
                raise ValueError(f"잘못된 policy rule입니다: {rule!r}")

    def assess(
        self,
        connection: sqlite3.Connection,
        operations: list[Operation],
        ontology: Ontology,
    ) -> PolicyDecision:
        risk = "auto"
        reasons: list[str] = []
        forbidden: list[str] = []
        rank = self.data["rank"]
        for operation in operations:
            row = connection.execute(
                "SELECT kind, locked, props, title, summary, story_from, story_to, reveal_at "
                "FROM live_node WHERE id = ?",
                (operation.target,),
            ).fetchone()
            kind = row["kind"] if row is not None else self._add_kind(operation, ontology)
            spec = ontology.edges.get(operation.field or "")
            current = (
                self._field_value(connection, operation.target, row, operation.field or "")
                if row is not None and operation.verb == "UPDATE"
                else None
            )
            context = {
                "verb": operation.verb,
                "kind": kind,
                "locked": bool(row["locked"]) if row is not None else False,
                "field": operation.field,
                "rel": operation.field if operation.verb in {"LINK", "UNLINK"} else None,
                "hard": spec.hard if spec is not None else None,
                "existing_null": current is None,
                "existing_non_null": current is not None,
            }
            matched = False
            for rule in self.data["rules"]:
                if not self._matches(context, rule["when"]):
                    continue
                matched = True
                candidate = rule["risk"]
                if rank[candidate] > rank[risk]:
                    risk = candidate
                reasons.append(rule["id"])
                if rule["effect"] == "forbid":
                    forbidden.append(f"{rule['description']}: {operation.target}")
            if not matched:
                if rank["review"] > rank[risk]:
                    risk = "review"
                reasons.append(f"{operation.verb.casefold()}-default")
        affected = {operation.target for operation in operations}
        affected.update(
            str(operation.to_value)
            for operation in operations
            if operation.verb in {"LINK", "UNLINK"}
        )
        cascade = self.data.get("cascade", {})
        if len(affected) >= int(cascade.get("always_from_nodes", 40)):
            risk = "always"
            reasons.append("cascade-40-plus")
        elif len(affected) >= int(cascade.get("review_from_nodes", 3)):
            if rank["review"] > rank[risk]:
                risk = "review"
            reasons.append("cascade-3-plus")
        return PolicyDecision(
            risk=risk,
            reasons=tuple(dict.fromkeys(reasons)),
            forbidden=tuple(dict.fromkeys(forbidden)),
        )

    @staticmethod
    def _matches(context: dict[str, Any], expected: dict[str, Any]) -> bool:
        for key, wanted in expected.items():
            actual = context.get(key)
            if isinstance(wanted, list):
                if actual not in wanted:
                    return False
            elif actual != wanted:
                return False
        return True

    @staticmethod
    def _add_kind(operation: Operation, ontology: Ontology) -> str | None:
        if operation.verb != "ADD" or not isinstance(operation.to_value, dict):
            return None
        raw = operation.to_value.get("kind")
        if raw is None:
            raw = operation.target.split("/", 1)[0]
        try:
            return ontology.canonical_kind(str(raw))
        except ValueError:
            return None

    @staticmethod
    def _field_value(
        connection: sqlite3.Connection,
        node_id: str,
        row: sqlite3.Row,
        field: str,
    ) -> Any:
        if field.startswith("props."):
            try:
                props = json.loads(row["props"] or "{}")
            except json.JSONDecodeError:
                props = {}
            value: Any = props
            for part in field.split(".")[1:]:
                if not isinstance(value, dict):
                    return None
                value = value.get(part)
            return value
        collection_queries = {
            "aliases": ("SELECT alias AS value FROM node_alias WHERE node = ?", "list"),
            "tags": ("SELECT tag AS value FROM node_tag WHERE node = ?", "list"),
            "features": ("SELECT name AS value FROM feature WHERE node = ?", "list"),
            "visible_to": ("SELECT viewer AS value FROM visibility WHERE fact = ?", "list"),
        }
        if field in collection_queries:
            sql, _ = collection_queries[field]
            values = [item["value"] for item in connection.execute(sql, (node_id,)).fetchall()]
            return values or None
        try:
            return row[field]
        except IndexError:
            return None

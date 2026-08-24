"""Atomic P1 operation validation and application."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .address import parse_address
from .merkle import canonical_json, record_revision, refresh_node_cid
from .models import Operation
from .ontology import Ontology


class MutationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Actor:
    kind: str
    model_id: str | None
    host: str
    on_behalf_of: str | None

    @property
    def attribution(self) -> str:
        identity = self.model_id or self.host
        return f"{self.kind}:{identity}"


def normalize_node_id(value: str, ontology: Ontology) -> str:
    parsed = parse_address(value, ontology)
    if parsed.kind is None:
        raise MutationError(f"쓰기 대상은 타입이 포함된 절대 주소여야 합니다: {value!r}")
    return parsed.value


def field_value(content: dict[str, Any], field: str) -> Any:
    value: Any = content
    for part in field.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


class OperationApplier:
    NODE_FIELDS = {
        "title",
        "summary",
        "props",
        "story_from",
        "story_to",
        "reveal_at",
        "aliases",
        "tags",
        "features",
        "locked",
    }
    ADD_FIELDS = NODE_FIELDS | {"kind"}

    def __init__(self, ontology: Ontology) -> None:
        self.ontology = ontology

    def normalize(self, operation: Operation) -> Operation:
        data = operation.model_dump(by_alias=False, exclude_unset=True)
        data["target"] = normalize_node_id(operation.target, self.ontology)
        if operation.verb in {"LINK", "UNLINK"}:
            data["to_value"] = normalize_node_id(str(operation.to_value), self.ontology)
        return Operation.model_validate(data)

    def validate(self, connection: sqlite3.Connection, operation: Operation) -> None:
        if operation.verb == "ADD":
            self._validate_add(connection, operation)
            return
        source = self._live_node(connection, operation.target)
        if source is None:
            raise MutationError(f"현재 유효한 노드가 없습니다: {operation.target}")
        if bool(source["locked"]):
            raise MutationError(f"locked 노드는 변경할 수 없습니다: {operation.target}")
        if operation.verb == "UPDATE":
            self._validate_update(operation)
        elif operation.verb == "INVALIDATE":
            self._validate_invalidate(connection, operation)
        elif operation.verb == "LINK":
            self._validate_link(connection, operation, source)
        elif operation.verb == "UNLINK":
            self._validate_unlink(connection, operation)

    def apply(
        self,
        connection: sqlite3.Connection,
        operation: Operation,
        *,
        proposal_id: str,
        actor: Actor,
        now: str,
    ) -> list[dict[str, Any]]:
        self.validate(connection, operation)
        if operation.verb == "ADD":
            return [self._add(connection, operation, proposal_id, actor, now)]
        if operation.verb == "UPDATE":
            return [self._update(connection, operation, proposal_id, actor, now)]
        if operation.verb == "INVALIDATE":
            return self._invalidate(connection, operation, proposal_id, actor, now)
        if operation.verb == "LINK":
            return [self._link(connection, operation, proposal_id, actor, now)]
        return [self._unlink(connection, operation, proposal_id, actor, now)]

    def _validate_add(self, connection: sqlite3.Connection, operation: Operation) -> None:
        if connection.execute("SELECT 1 FROM node WHERE id = ?", (operation.target,)).fetchone():
            raise MutationError(f"이미 사용된 노드 주소입니다: {operation.target}")
        payload = operation.to_value
        if not isinstance(payload, dict):
            raise MutationError("ADD.to는 객체여야 합니다")
        unknown = sorted(set(payload) - self.ADD_FIELDS)
        if unknown:
            raise MutationError(f"ADD에서 지원하지 않는 필드: {', '.join(unknown)}")
        parsed = parse_address(operation.target, self.ontology)
        kind = self.ontology.canonical_kind(str(payload.get("kind") or parsed.kind))
        allowed = set(self.ontology.p0_kinds) | {"Session"}
        if kind not in allowed:
            raise MutationError(f"P1에서 추가할 수 없는 타입입니다: {kind}")
        if parsed.kind != kind:
            raise MutationError(f"주소 kind와 payload.kind가 다릅니다: {parsed.kind} / {kind}")
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise MutationError("ADD.to.title은 비어 있지 않은 문자열이어야 합니다")
        if payload.get("summary") is not None and not isinstance(payload["summary"], str):
            raise MutationError("ADD.to.summary는 문자열 또는 null이어야 합니다")
        if payload.get("locked") is not None and not isinstance(payload["locked"], bool):
            raise MutationError("ADD.to.locked는 bool이어야 합니다")
        reveal_at = payload.get("reveal_at")
        if reveal_at is not None and (
            not isinstance(reveal_at, int) or isinstance(reveal_at, bool) or reveal_at < 0
        ):
            raise MutationError("ADD.to.reveal_at은 0 이상의 정수 또는 null이어야 합니다")
        self._validate_detail_shapes(payload)
        self._validate_story_range(payload.get("story_from"), payload.get("story_to"))
        if kind == "Session":
            props = payload.get("props", {})
            for key in ("open_threads", "next"):
                value = props.get(key)
                if not self._string_list(value) or not value:
                    raise MutationError(
                        f"Session.props.{key}는 비어 있지 않은 문자열 배열이어야 합니다"
                    )

    def _validate_update(self, operation: Operation) -> None:
        field = operation.field or ""
        root = field.split(".", 1)[0]
        if root not in self.NODE_FIELDS or (root != "props" and "." in field):
            raise MutationError(f"UPDATE에서 지원하지 않는 필드입니다: {field}")
        if root in {"aliases", "tags"} and not self._string_list(operation.to_value):
            raise MutationError(f"{root}는 문자열 배열이어야 합니다")
        if (
            root in {"props", "features"}
            and "." not in field
            and not isinstance(operation.to_value, dict)
        ):
            raise MutationError(f"{root}는 객체여야 합니다")
        if root == "locked" and not isinstance(operation.to_value, bool):
            raise MutationError("locked는 bool이어야 합니다")
        if (
            root in {"story_from", "story_to", "reveal_at"}
            and operation.to_value is not None
            and (not isinstance(operation.to_value, int) or isinstance(operation.to_value, bool))
        ):
            raise MutationError(f"{root}는 정수 또는 null이어야 합니다")
        if root == "title" and (
            not isinstance(operation.to_value, str) or not operation.to_value.strip()
        ):
            raise MutationError("title은 비어 있지 않은 문자열이어야 합니다")
        if (
            root == "summary"
            and operation.to_value is not None
            and not isinstance(operation.to_value, str)
        ):
            raise MutationError("summary는 문자열 또는 null이어야 합니다")

    def _validate_link(
        self, connection: sqlite3.Connection, operation: Operation, source: sqlite3.Row
    ) -> None:
        target = self._live_node(connection, str(operation.to_value))
        if target is None:
            raise MutationError(f"간선 대상이 없습니다: {operation.to_value}")
        self.ontology.validate_edge(operation.field or "", source["kind"], target["kind"])
        duplicate = connection.execute(
            "SELECT 1 FROM live_edge WHERE src = ? AND dst = ? AND rel = ?",
            (operation.target, operation.to_value, operation.field),
        ).fetchone()
        if duplicate:
            raise MutationError(
                f"이미 유효한 간선입니다: {operation.target} -[{operation.field}]-> "
                f"{operation.to_value}"
            )

    def _validate_unlink(self, connection: sqlite3.Connection, operation: Operation) -> None:
        if operation.field not in self.ontology.edges:
            raise MutationError(f"알 수 없는 간선 타입: {operation.field}")
        exists = connection.execute(
            "SELECT 1 FROM live_edge WHERE src = ? AND dst = ? AND rel = ?",
            (operation.target, operation.to_value, operation.field),
        ).fetchone()
        if not exists:
            raise MutationError(
                f"해제할 간선이 없습니다: {operation.target} -[{operation.field}]-> "
                f"{operation.to_value}"
            )

    @staticmethod
    def _validate_invalidate(connection: sqlite3.Connection, operation: Operation) -> None:
        locked = connection.execute(
            """
            SELECT DISTINCT n.id
            FROM live_edge AS e JOIN live_node AS n ON n.id = e.src
            WHERE e.dst = ? AND e.src != ? AND n.locked = 1
            ORDER BY n.id
            """,
            (operation.target, operation.target),
        ).fetchall()
        if locked:
            names = ", ".join(row["id"] for row in locked)
            raise MutationError(
                f"INVALIDATE가 locked 노드의 간선을 바꾸므로 제안할 수 없습니다: {names}"
            )

    def _add(
        self,
        connection: sqlite3.Connection,
        operation: Operation,
        proposal_id: str,
        actor: Actor,
        now: str,
    ) -> dict[str, Any]:
        payload = dict(operation.to_value)
        parsed = parse_address(operation.target, self.ontology)
        kind = self.ontology.canonical_kind(str(payload.get("kind") or parsed.kind))
        title = str(payload["title"]).strip()
        summary = payload.get("summary")
        if summary is not None:
            summary = " ".join(str(summary).split())[:240]
        props = payload.get("props", {})
        locked = payload.get("locked")
        if locked is None:
            locked = self.ontology.kinds[kind].default_locked
        connection.execute(
            """
            INSERT INTO node(
              id, kind, title, summary, props, story_from, story_to, reveal_at,
              tx_from, tx_to, origin, locked, rev, cid
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, 'pending')
            """,
            (
                operation.target,
                kind,
                title,
                summary,
                canonical_json(props),
                payload.get("story_from"),
                payload.get("story_to"),
                payload.get("reveal_at"),
                now,
                actor.kind,
                int(bool(locked)),
            ),
        )
        self._replace_aliases(connection, operation.target, payload.get("aliases", []))
        self._replace_tags(connection, operation.target, payload.get("tags", []))
        self._replace_features(connection, operation.target, payload.get("features", {}))
        aliases = payload.get("aliases", [])
        connection.execute(
            "INSERT INTO node_fts(id, title, aliases, summary, body) VALUES (?, ?, ?, ?, '')",
            (operation.target, title, " ".join(aliases), summary),
        )
        cid = refresh_node_cid(connection, operation.target)
        record_revision(connection, operation.target, proposal_id=proposal_id)
        self._provenance(
            connection,
            operation.target,
            1,
            "*",
            proposal_id,
            actor,
            now,
            derived_from=None,
        )
        return {"verb": "ADD", "target": operation.target, "rev": 1, "cid": cid}

    def _update(
        self,
        connection: sqlite3.Connection,
        operation: Operation,
        proposal_id: str,
        actor: Actor,
        now: str,
    ) -> dict[str, Any]:
        field = operation.field or ""
        current = self.current_field(connection, operation.target, field)
        if "from_value" in operation.model_fields_set and current != operation.from_value:
            raise MutationError(
                f"조건부 UPDATE 불일치: {operation.target}.{field}: "
                f"현재={current!r}, 기대={operation.from_value!r}"
            )
        root = field.split(".", 1)[0]
        if root == "props":
            row = self._live_node(connection, operation.target)
            props = json.loads(row["props"] or "{}")
            if field == "props":
                props = operation.to_value
            else:
                self._set_nested(props, field.split(".")[1:], operation.to_value)
            connection.execute(
                "UPDATE node SET props = ? WHERE id = ?",
                (canonical_json(props), operation.target),
            )
        elif root == "aliases":
            self._replace_aliases(connection, operation.target, operation.to_value)
        elif root == "tags":
            self._replace_tags(connection, operation.target, operation.to_value)
        elif root == "features":
            self._replace_features(connection, operation.target, operation.to_value)
        else:
            connection.execute(
                f"UPDATE node SET {root} = ? WHERE id = ?",
                (
                    int(operation.to_value) if root == "locked" else operation.to_value,
                    operation.target,
                ),
            )
        row = connection.execute(
            "SELECT story_from, story_to FROM node WHERE id = ?", (operation.target,)
        ).fetchone()
        self._validate_story_range(row["story_from"], row["story_to"])
        self._refresh_fts(connection, operation.target)
        rev, cid = self._bump(
            connection,
            operation.target,
            field,
            proposal_id,
            actor,
            now,
        )
        return {
            "verb": "UPDATE",
            "target": operation.target,
            "field": field,
            "rev": rev,
            "cid": cid,
        }

    def _invalidate(
        self,
        connection: sqlite3.Connection,
        operation: Operation,
        proposal_id: str,
        actor: Actor,
        now: str,
    ) -> list[dict[str, Any]]:
        incoming_sources = [
            row["src"]
            for row in connection.execute(
                "SELECT DISTINCT src FROM live_edge WHERE dst = ? AND src != ? ORDER BY src",
                (operation.target, operation.target),
            ).fetchall()
        ]
        connection.execute(
            "UPDATE edge SET tx_to = ? WHERE tx_to IS NULL AND (src = ? OR dst = ?)",
            (now, operation.target, operation.target),
        )
        connection.execute("UPDATE node SET tx_to = ? WHERE id = ?", (now, operation.target))
        rev, cid = self._bump(
            connection,
            operation.target,
            "$invalidate",
            proposal_id,
            actor,
            now,
        )
        applied = [{"verb": "INVALIDATE", "target": operation.target, "rev": rev, "cid": cid}]
        for source in incoming_sources:
            source_rev, source_cid = self._bump(
                connection,
                source,
                f"edge:*:{operation.target}",
                proposal_id,
                actor,
                now,
            )
            applied.append(
                {
                    "verb": "UPDATE",
                    "target": source,
                    "field": f"edge:*:{operation.target}",
                    "rev": source_rev,
                    "cid": source_cid,
                    "derived": True,
                }
            )
        return applied

    def _link(
        self,
        connection: sqlite3.Connection,
        operation: Operation,
        proposal_id: str,
        actor: Actor,
        now: str,
    ) -> dict[str, Any]:
        spec = self.ontology.edges[operation.field or ""]
        connection.execute(
            """
            INSERT INTO edge(src, dst, rel, hard, props, story_from, story_to,
                             tx_from, tx_to, origin, confidence)
            VALUES (?, ?, ?, ?, '{}', NULL, NULL, ?, NULL, ?, 1.0)
            """,
            (
                operation.target,
                operation.to_value,
                operation.field,
                int(spec.hard),
                now,
                actor.kind,
            ),
        )
        rev, cid = self._bump(
            connection,
            operation.target,
            f"edge:{operation.field}:{operation.to_value}",
            proposal_id,
            actor,
            now,
        )
        return {
            "verb": "LINK",
            "target": operation.target,
            "rel": operation.field,
            "to": operation.to_value,
            "rev": rev,
            "cid": cid,
        }

    def _unlink(
        self,
        connection: sqlite3.Connection,
        operation: Operation,
        proposal_id: str,
        actor: Actor,
        now: str,
    ) -> dict[str, Any]:
        connection.execute(
            "UPDATE edge SET tx_to = ? WHERE tx_to IS NULL AND src = ? AND dst = ? AND rel = ?",
            (now, operation.target, operation.to_value, operation.field),
        )
        rev, cid = self._bump(
            connection,
            operation.target,
            f"edge:{operation.field}:{operation.to_value}",
            proposal_id,
            actor,
            now,
        )
        return {
            "verb": "UNLINK",
            "target": operation.target,
            "rel": operation.field,
            "to": operation.to_value,
            "rev": rev,
            "cid": cid,
        }

    def _bump(
        self,
        connection: sqlite3.Connection,
        node_id: str,
        field: str,
        proposal_id: str,
        actor: Actor,
        now: str,
    ) -> tuple[int, str]:
        row = connection.execute("SELECT rev FROM node WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            raise MutationError(f"노드를 찾을 수 없습니다: {node_id}")
        old_rev = int(row["rev"])
        connection.execute(
            "UPDATE node_revision SET tx_to = ? WHERE node = ? AND rev = ?",
            (now, node_id, old_rev),
        )
        new_rev = old_rev + 1
        connection.execute(
            "UPDATE node SET rev = ?, tx_from = ?, origin = ? WHERE id = ?",
            (new_rev, now, actor.kind, node_id),
        )
        cid = refresh_node_cid(connection, node_id)
        record_revision(connection, node_id, proposal_id=proposal_id)
        self._provenance(
            connection,
            node_id,
            new_rev,
            field,
            proposal_id,
            actor,
            now,
            derived_from=f"{node_id}:{old_rev}",
        )
        return new_rev, cid

    @staticmethod
    def _provenance(
        connection: sqlite3.Connection,
        node_id: str,
        rev: int,
        field: str,
        proposal_id: str,
        actor: Actor,
        now: str,
        *,
        derived_from: str | None,
    ) -> None:
        version = f"{node_id}:{rev}"
        connection.execute(
            """
            INSERT INTO provenance(node_version, generated_by, derived_from,
                                   attributed_to, on_behalf_of, ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (version, proposal_id, derived_from, actor.attribution, actor.on_behalf_of, now),
        )
        connection.execute(
            """
            INSERT INTO field_provenance(node, rev, field, generated_by, derived_from,
                                         attributed_to, on_behalf_of, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_id,
                rev,
                field,
                proposal_id,
                derived_from,
                actor.attribution,
                actor.on_behalf_of,
                now,
            ),
        )

    def current_field(self, connection: sqlite3.Connection, node_id: str, field: str) -> Any:
        row = connection.execute("SELECT * FROM live_node WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        if field.startswith("props"):
            content = {"props": json.loads(row["props"] or "{}")}
            return field_value(content, field)
        if field == "aliases":
            return [
                item["alias"]
                for item in connection.execute(
                    "SELECT alias FROM node_alias WHERE node = ? ORDER BY alias", (node_id,)
                ).fetchall()
            ]
        if field == "tags":
            return [
                item["tag"]
                for item in connection.execute(
                    "SELECT tag FROM node_tag WHERE node = ? ORDER BY tag", (node_id,)
                ).fetchall()
            ]
        if field == "features":
            return {
                item["name"]: json.loads(item["data"])
                for item in connection.execute(
                    "SELECT name, data FROM feature WHERE node = ? ORDER BY name", (node_id,)
                ).fetchall()
            }
        try:
            return row[field]
        except IndexError:
            return None

    @staticmethod
    def _live_node(connection: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
        return connection.execute("SELECT * FROM live_node WHERE id = ?", (node_id,)).fetchone()

    @classmethod
    def _validate_detail_shapes(cls, payload: dict[str, Any]) -> None:
        if not cls._string_list(payload.get("aliases", [])):
            raise MutationError("aliases는 문자열 배열이어야 합니다")
        if not cls._string_list(payload.get("tags", [])):
            raise MutationError("tags는 문자열 배열이어야 합니다")
        if not isinstance(payload.get("features", {}), dict):
            raise MutationError("features는 객체여야 합니다")
        if any(not isinstance(value, dict) for value in payload.get("features", {}).values()):
            raise MutationError("features의 각 값은 객체여야 합니다")
        if not isinstance(payload.get("props", {}), dict):
            raise MutationError("props는 객체여야 합니다")

    @staticmethod
    def _validate_story_range(story_from: Any, story_to: Any) -> None:
        for name, value in (("story_from", story_from), ("story_to", story_to)):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise MutationError(f"{name}은 정수 또는 null이어야 합니다")
        if story_from is not None and story_to is not None and story_to < story_from:
            raise MutationError("story_to는 story_from 이상이어야 합니다")

    @staticmethod
    def _string_list(value: Any) -> bool:
        return isinstance(value, list) and all(
            isinstance(item, str) and bool(item.strip()) for item in value
        )

    @staticmethod
    def _set_nested(target: dict[str, Any], path: list[str], value: Any) -> None:
        cursor = target
        for part in path[:-1]:
            child = cursor.get(part)
            if not isinstance(child, dict):
                child = {}
                cursor[part] = child
            cursor = child
        cursor[path[-1]] = value

    @staticmethod
    def _replace_aliases(connection: sqlite3.Connection, node_id: str, aliases: list[str]) -> None:
        values = list(dict.fromkeys(alias.strip() for alias in aliases))
        connection.execute("DELETE FROM node_alias WHERE node = ?", (node_id,))
        connection.executemany(
            "INSERT INTO node_alias(node, alias) VALUES (?, ?)",
            [(node_id, alias) for alias in values],
        )

    @staticmethod
    def _replace_tags(connection: sqlite3.Connection, node_id: str, tags: list[str]) -> None:
        values = list(
            dict.fromkeys(tag.strip() if tag.startswith("#") else f"#{tag.strip()}" for tag in tags)
        )
        connection.execute("DELETE FROM node_tag WHERE node = ?", (node_id,))
        for tag in values:
            connection.execute("INSERT OR IGNORE INTO tag(name) VALUES (?)", (tag,))
            connection.execute("INSERT INTO node_tag(node, tag) VALUES (?, ?)", (node_id, tag))

    @staticmethod
    def _replace_features(
        connection: sqlite3.Connection, node_id: str, features: dict[str, Any]
    ) -> None:
        connection.execute("DELETE FROM feature WHERE node = ?", (node_id,))
        connection.executemany(
            "INSERT INTO feature(node, name, data) VALUES (?, ?, ?)",
            [(node_id, name, canonical_json(value)) for name, value in sorted(features.items())],
        )

    @staticmethod
    def _refresh_fts(connection: sqlite3.Connection, node_id: str) -> None:
        row = connection.execute(
            "SELECT title, summary FROM node WHERE id = ?", (node_id,)
        ).fetchone()
        aliases = " ".join(
            item["alias"]
            for item in connection.execute(
                "SELECT alias FROM node_alias WHERE node = ? ORDER BY alias", (node_id,)
            ).fetchall()
        )
        current = connection.execute(
            "SELECT body FROM node_fts WHERE id = ? ORDER BY rowid DESC LIMIT 1", (node_id,)
        ).fetchone()
        body = current["body"] if current else ""
        connection.execute("DELETE FROM node_fts WHERE id = ?", (node_id,))
        connection.execute(
            "INSERT INTO node_fts(id, title, aliases, summary, body) VALUES (?, ?, ?, ?, ?)",
            (node_id, row["title"], aliases, row["summary"], body),
        )

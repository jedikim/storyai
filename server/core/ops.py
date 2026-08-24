"""Atomic P1 operation validation and application."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .address import parse_address
from .invariants import validate_new_edge
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
        "visible_to",
        "locked",
    }
    ADD_FIELDS = NODE_FIELDS | {"kind"}

    def __init__(self, ontology: Ontology) -> None:
        self.ontology = ontology

    def validate_initial_props(
        self,
        connection: sqlite3.Connection,
        *,
        node_id: str,
        kind: str,
        props: dict[str, Any],
    ) -> None:
        current = connection.execute(
            "SELECT kind, props FROM live_node WHERE id = ?", (node_id,)
        ).fetchone()
        current_props = (
            json.loads(current["props"] or "{}")
            if current is not None and current["kind"] == kind
            else None
        )
        self._validate_kind_props(
            connection,
            node_id,
            kind,
            props,
            current_props=current_props,
            require_references=False,
        )

    def normalize(self, operation: Operation) -> Operation:
        data = operation.model_dump(by_alias=False, exclude_unset=True)
        data["target"] = normalize_node_id(operation.target, self.ontology)
        if operation.verb in {"LINK", "UNLINK"}:
            data["to_value"] = normalize_node_id(str(operation.to_value), self.ontology)
        elif operation.verb == "ADD" and isinstance(operation.to_value, dict):
            payload = json.loads(canonical_json(operation.to_value))
            if "props" in payload:
                payload["props"] = self._normalize_structured_refs(payload["props"])
            if "visible_to" in payload:
                payload["visible_to"] = self._normalize_visibility(payload["visible_to"])
            data["to_value"] = payload
        elif operation.verb == "UPDATE":
            if operation.field == "props" and isinstance(operation.to_value, dict):
                data["to_value"] = self._normalize_structured_refs(operation.to_value)
            elif operation.field in {"props.F", "props.T", "props.P", "props.subject"}:
                if isinstance(operation.to_value, str):
                    data["to_value"] = normalize_node_id(operation.to_value, self.ontology)
            elif operation.field in {
                "props.pre",
                "props.post",
                "props.forbid",
                "props.claims",
                "props.mentions",
            }:
                key = str(operation.field).split(".", 1)[1]
                data["to_value"] = self._normalize_structured_refs({key: operation.to_value})[key]
            elif operation.field == "visible_to":
                data["to_value"] = self._normalize_visibility(operation.to_value)
        return Operation.model_validate(data)

    def _normalize_structured_refs(self, props: Any) -> Any:
        if not isinstance(props, dict):
            return props
        result = json.loads(canonical_json(props))
        for field in ("F", "T", "P", "subject"):
            if isinstance(result.get(field), str):
                result[field] = normalize_node_id(result[field], self.ontology)
        for phase in ("pre", "post", "forbid"):
            if isinstance(result.get(phase), list):
                for condition in result[phase]:
                    if isinstance(condition, dict) and isinstance(condition.get("subject"), str):
                        condition["subject"] = normalize_node_id(
                            condition["subject"], self.ontology
                        )
        if isinstance(result.get("claims"), list):
            for claim in result["claims"]:
                if not isinstance(claim, dict):
                    continue
                for field in ("speaker", "fact"):
                    if isinstance(claim.get(field), str):
                        claim[field] = normalize_node_id(claim[field], self.ontology)
        if isinstance(result.get("mentions"), list):
            for mention in result["mentions"]:
                if isinstance(mention, dict) and isinstance(mention.get("entity"), str):
                    mention["entity"] = normalize_node_id(mention["entity"], self.ontology)
        return result

    def _normalize_visibility(self, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        result = json.loads(canonical_json(value))
        for index, item in enumerate(result):
            if isinstance(item, str) and item != "reader":
                result[index] = normalize_node_id(item, self.ontology)
            elif (
                isinstance(item, dict)
                and item.get("viewer") != "reader"
                and isinstance(item.get("viewer"), str)
            ):
                item["viewer"] = normalize_node_id(item["viewer"], self.ontology)
        return result

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
            self._validate_update(connection, operation, source)
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
        allowed = set(self.ontology.kinds)
        if kind not in allowed:
            raise MutationError(f"추가할 수 없는 타입입니다: {kind}")
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
        props = dict(payload.get("props", {}))
        if kind == "Promise":
            props.setdefault("status", "hypothetical")
        self._validate_kind_props(
            connection,
            operation.target,
            kind,
            props,
            current_props=None,
        )
        self._validate_visibility(connection, kind, payload.get("visible_to", []))
        if kind == "Session":
            for key in ("open_threads", "next"):
                value = props.get(key)
                if not self._string_list(value) or not value:
                    raise MutationError(
                        f"Session.props.{key}는 비어 있지 않은 문자열 배열이어야 합니다"
                    )

    def _validate_update(
        self, connection: sqlite3.Connection, operation: Operation, source: sqlite3.Row
    ) -> None:
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
        if root == "visible_to":
            self._validate_visibility(
                connection,
                source["kind"],
                operation.to_value,
            )
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
        if root == "props":
            current_props = json.loads(source["props"] or "{}")
            prospective = json.loads(canonical_json(current_props))
            if operation.field == "props":
                prospective = operation.to_value
            else:
                self._set_nested(
                    prospective,
                    (operation.field or "").split(".")[1:],
                    operation.to_value,
                )
            self._validate_kind_props(
                connection,
                operation.target,
                source["kind"],
                prospective,
                current_props=current_props,
            )

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
        validate_new_edge(
            connection,
            self.ontology,
            source=operation.target,
            target=str(operation.to_value),
            relation=operation.field or "",
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
        self._replace_visibility(
            connection,
            operation.target,
            payload.get("visible_to", []),
        )
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
        elif root == "visible_to":
            self._replace_visibility(connection, operation.target, operation.to_value)
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
        if field == "visible_to":
            return [
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
        try:
            return row[field]
        except IndexError:
            return None

    @staticmethod
    def _live_node(connection: sqlite3.Connection, node_id: str) -> sqlite3.Row | None:
        return connection.execute("SELECT * FROM live_node WHERE id = ?", (node_id,)).fetchone()

    def _validate_kind_props(
        self,
        connection: sqlite3.Connection,
        node_id: str,
        kind: str,
        props: dict[str, Any],
        *,
        current_props: dict[str, Any] | None,
        require_references: bool = True,
    ) -> None:
        if not isinstance(props, dict):
            raise MutationError("props는 객체여야 합니다")
        if kind == "Promise":
            status = props.get("status", "hypothetical")
            allowed_statuses = {"hypothetical", "eligible", "actualized", "prevented"}
            if status not in allowed_statuses:
                raise MutationError(
                    "Promise.props.status는 hypothetical, eligible, actualized, prevented 중 "
                    "하나여야 합니다"
                )
            if current_props is not None:
                previous = current_props.get("status", "hypothetical")
                transitions = {
                    "hypothetical": {"hypothetical", "eligible", "prevented"},
                    "eligible": {"eligible", "actualized", "prevented"},
                    "actualized": {"actualized", "prevented"},
                    "prevented": {"prevented"},
                }
                if status not in transitions.get(previous, set()):
                    raise MutationError(
                        f"허용되지 않는 Promise 상태 전이입니다: {previous} -> {status}"
                    )
                if status == "eligible" and previous != "eligible":
                    trigger = connection.execute(
                        "SELECT 1 FROM live_edge WHERE src = ? AND rel = 'requires_trigger'",
                        (node_id,),
                    ).fetchone()
                    if props.get("T") is None and trigger is None:
                        raise MutationError("Promise를 eligible로 전이하려면 T가 정의되어야 합니다")
                if status == "actualized" and previous != "actualized":
                    payoff = connection.execute(
                        "SELECT 1 FROM live_edge WHERE dst = ? AND rel = 'pays_off'",
                        (node_id,),
                    ).fetchone()
                    if props.get("P") is None and payoff is None:
                        raise MutationError(
                            "Promise를 actualized로 전이하려면 P가 배치되어야 합니다"
                        )
            elif status == "eligible" and props.get("T") is None:
                raise MutationError("eligible Promise를 추가하려면 T가 정의되어야 합니다")
            elif status == "actualized" and (props.get("T") is None or props.get("P") is None):
                raise MutationError("actualized Promise를 추가하려면 T와 P가 정의되어야 합니다")
            foreshadow_type = props.get("foreshadow_type")
            if foreshadow_type is not None and foreshadow_type not in {
                "object",
                "event",
                "rule",
            }:
                raise MutationError(
                    "Promise.props.foreshadow_type은 object, event, rule 중 하나여야 합니다"
                )
            reference_kinds = {
                "F": {"Fact", "Scene"},
                "T": {"Scene"},
                "P": {"Scene"},
            }
            for field, expected_kinds in reference_kinds.items():
                value = props.get(field)
                if value is not None:
                    self._require_reference(
                        connection,
                        value,
                        field=f"Promise.props.{field}",
                        expected_kinds=expected_kinds,
                        require_exists=require_references,
                    )
            debt = props.get("debt")
            if debt is not None and (
                not isinstance(debt, (int, float)) or isinstance(debt, bool) or float(debt) < 0
            ):
                raise MutationError("Promise.props.debt는 0 이상의 수여야 합니다")
            for field in ("s_eff", "delta_coh"):
                value = props.get(field)
                if value is not None and (
                    not isinstance(value, (int, float))
                    or isinstance(value, bool)
                    or not 0 <= float(value) <= 1
                ):
                    raise MutationError(f"Promise.props.{field}는 0~1 범위의 수여야 합니다")
        if kind in {"Scene", "Rule"}:
            for phase in ("pre", "post", "forbid"):
                if phase in props:
                    self._validate_conditions(
                        connection,
                        props[phase],
                        phase,
                        require_references=require_references,
                    )
        if kind == "Scene":
            self._validate_claims(
                connection,
                props.get("claims", []),
                require_references=require_references,
            )
            self._validate_mentions(
                connection,
                props.get("mentions", []),
                require_references=require_references,
            )
        if kind == "Fact":
            subject = props.get("subject")
            predicate = props.get("predicate")
            if not isinstance(subject, str) or not subject.strip():
                raise MutationError("Fact.props.subject는 절대 노드 주소여야 합니다")
            self._require_reference(
                connection,
                subject,
                field="Fact.props.subject",
                require_exists=require_references,
            )
            if not isinstance(predicate, str) or not predicate.strip():
                raise MutationError("Fact.props.predicate는 비어 있지 않은 문자열이어야 합니다")
            if "object" not in props:
                raise MutationError("Fact.props.object가 필요합니다")

    def _validate_conditions(
        self,
        connection: sqlite3.Connection,
        value: Any,
        phase: str,
        *,
        require_references: bool,
    ) -> None:
        if not isinstance(value, list):
            raise MutationError(f"{phase}는 구조화 조건 배열이어야 합니다")
        allowed_ops = {"eq", "ne", "in", "not_in", "exists", "not_exists"}
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise MutationError(f"{phase}[{index}]는 객체여야 합니다")
            unknown = set(item) - {"subject", "field", "op", "value"}
            if unknown:
                raise MutationError(
                    f"{phase}[{index}]의 알 수 없는 필드: {', '.join(sorted(unknown))}"
                )
            self._require_reference(
                connection,
                item.get("subject"),
                field=f"{phase}[{index}].subject",
                require_exists=require_references,
            )
            field = item.get("field")
            if not isinstance(field, str) or not field.strip():
                raise MutationError(f"{phase}[{index}].field가 필요합니다")
            op = item.get("op", "eq")
            if op not in allowed_ops:
                raise MutationError(f"{phase}[{index}].op가 지원되지 않습니다: {op}")
            if op not in {"exists", "not_exists"} and "value" not in item:
                raise MutationError(f"{phase}[{index}]에 value가 필요합니다")
            if op in {"in", "not_in"} and not isinstance(item.get("value"), list):
                raise MutationError(f"{phase}[{index}].value는 배열이어야 합니다")

    def _validate_claims(
        self,
        connection: sqlite3.Connection,
        value: Any,
        *,
        require_references: bool,
    ) -> None:
        if not isinstance(value, list):
            raise MutationError("Scene.props.claims는 객체 배열이어야 합니다")
        for index, item in enumerate(value):
            if not isinstance(item, dict) or set(item) != {"speaker", "fact"}:
                raise MutationError(f"claims[{index}]는 speaker와 fact만 가져야 합니다")
            self._require_reference(
                connection,
                item["speaker"],
                field=f"claims[{index}].speaker",
                expected_kinds={"Character"},
                require_exists=require_references,
            )
            self._require_reference(
                connection,
                item["fact"],
                field=f"claims[{index}].fact",
                expected_kinds={"Fact"},
                require_exists=require_references,
            )

    def _validate_mentions(
        self,
        connection: sqlite3.Connection,
        value: Any,
        *,
        require_references: bool,
    ) -> None:
        if not isinstance(value, list):
            raise MutationError("Scene.props.mentions는 객체 배열이어야 합니다")
        for index, item in enumerate(value):
            if not isinstance(item, dict) or set(item) != {"entity", "name"}:
                raise MutationError(f"mentions[{index}]는 entity와 name만 가져야 합니다")
            self._require_reference(
                connection,
                item["entity"],
                field=f"mentions[{index}].entity",
                require_exists=require_references,
            )
            if not isinstance(item["name"], str) or not item["name"].strip():
                raise MutationError(f"mentions[{index}].name은 비어 있지 않은 문자열이어야 합니다")

    def _validate_visibility(self, connection: sqlite3.Connection, kind: str, value: Any) -> None:
        if not isinstance(value, list):
            raise MutationError("visible_to는 문자열 또는 객체 배열이어야 합니다")
        if kind != "Fact" and value:
            raise MutationError("visible_to는 Fact 노드에만 지정할 수 있습니다")
        seen: set[str] = set()
        for index, item in enumerate(value):
            if isinstance(item, str):
                viewer = item
                learned_at = None
                pathway = "direct"
            elif isinstance(item, dict):
                unknown = set(item) - {"viewer", "learned_at", "pathway"}
                if unknown:
                    raise MutationError(
                        f"visible_to[{index}]의 알 수 없는 필드: {', '.join(sorted(unknown))}"
                    )
                viewer = item.get("viewer")
                learned_at = item.get("learned_at")
                pathway = item.get("pathway", "direct")
            else:
                raise MutationError(f"visible_to[{index}] 형식이 잘못되었습니다")
            if viewer != "reader":
                if self._absolute_kind(viewer, field=f"visible_to[{index}].viewer") != "Character":
                    raise MutationError(f"visible_to[{index}].viewer는 Character 주소여야 합니다")
                row = connection.execute(
                    "SELECT 1 FROM live_node WHERE id = ? AND kind = 'Character'", (viewer,)
                ).fetchone()
                if row is None:
                    raise MutationError(f"visible_to viewer가 존재하지 않습니다: {viewer}")
            if viewer in seen:
                raise MutationError(f"visible_to viewer가 중복되었습니다: {viewer}")
            seen.add(viewer)
            if learned_at is not None and (
                not isinstance(learned_at, int) or isinstance(learned_at, bool) or learned_at < 0
            ):
                raise MutationError(f"visible_to[{index}].learned_at은 0 이상의 정수여야 합니다")
            if pathway not in {"direct", "observed", "told", "common"}:
                raise MutationError(f"visible_to[{index}].pathway가 지원되지 않습니다: {pathway}")

    def _require_reference(
        self,
        connection: sqlite3.Connection,
        value: Any,
        *,
        field: str,
        expected_kinds: set[str] | None = None,
        require_exists: bool,
    ) -> str:
        kind = self._absolute_kind(value, field=field)
        if expected_kinds is not None and kind not in expected_kinds:
            allowed = ", ".join(sorted(expected_kinds))
            raise MutationError(f"{field}는 {allowed} 주소여야 합니다")
        parsed = parse_address(str(value), self.ontology)
        if require_exists and self._live_node(connection, parsed.value) is None:
            raise MutationError(f"{field}가 존재하지 않는 노드를 가리킵니다: {parsed.value}")
        return parsed.value

    def _absolute_kind(self, value: Any, *, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise MutationError(f"{field}는 절대 노드 주소여야 합니다")
        parsed = parse_address(value, self.ontology)
        if parsed.kind is None:
            raise MutationError(f"{field}는 타입이 포함된 절대 노드 주소여야 합니다")
        return parsed.kind

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
    def _replace_visibility(
        connection: sqlite3.Connection,
        node_id: str,
        visible_to: list[str | dict[str, Any]],
    ) -> None:
        connection.execute("DELETE FROM visibility WHERE fact = ?", (node_id,))
        values: list[tuple[str, str, int | None, str]] = []
        for item in visible_to:
            if isinstance(item, str):
                values.append((node_id, item, None, "direct"))
            else:
                values.append(
                    (
                        node_id,
                        str(item["viewer"]),
                        item.get("learned_at"),
                        str(item.get("pathway", "direct")),
                    )
                )
        connection.executemany(
            "INSERT INTO visibility(fact, viewer, learned_at, pathway) VALUES (?, ?, ?, ?)",
            values,
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

"""Validated manifest-driven manuscript ingestion that only creates Proposals."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .database import connect_read_only
from .merkle import canonical_json
from .models import Operation
from .ontology import Ontology
from .ops import OperationApplier, normalize_node_id
from .write_service import WriteService


class IngestError(ValueError):
    pass


class Span(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=0)
    end: int = Field(ge=0)
    quote: str


class ExtractedNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    title: str
    summary: str | None = None
    props: dict[str, Any] = Field(default_factory=dict)
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    features: dict[str, dict[str, Any]] = Field(default_factory=dict)
    visible_to: list[str | dict[str, Any]] = Field(default_factory=list)
    story_from: int | None = None
    story_to: int | None = None
    reveal_at: int | None = None
    evidence: list[Span] = Field(min_length=1)

    @field_validator("id", "kind", "title")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("필수 문자열은 비어 있을 수 없습니다")
        return value.strip()


class ExtractedEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    src: str
    rel: str
    dst: str


class BindingManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    source_sha256: str
    nodes: list[ExtractedNode]
    edges: list[ExtractedEdge]
    unresolved: list[Any]

    @field_validator("source_sha256")
    @classmethod
    def digest_shape(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("source_sha256는 소문자 SHA-256이어야 합니다")
        return value


class SceneSplitter:
    """Suggest byte-exact structural boundaries without interpreting prose semantics."""

    BOUNDARY = re.compile(rb"(?m)^(?:#{2,3}[ \t]+[^\r\n]+|\*\*\*[ \t]*)\r?$", re.ASCII)

    @classmethod
    def suggest(cls, raw: bytes) -> list[dict[str, int]]:
        starts = [0]
        for match in cls.BOUNDARY.finditer(raw):
            if match.start() > 0 and match.start() not in starts:
                starts.append(match.start())
        starts = sorted(starts)
        return [
            {"start": start, "end": starts[index + 1] if index + 1 < len(starts) else len(raw)}
            for index, start in enumerate(starts)
            if start < len(raw)
        ]


class IngestService:
    NODE_FIELDS = (
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
        "body",
        "evidence",
    )

    def __init__(
        self,
        *,
        project_root: str | Path,
        db_path: str | Path,
        ontology: Ontology,
        writer: WriteService,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.manuscript_root = (self.project_root / "manuscript").resolve()
        self.db_path = Path(db_path).resolve()
        self.ontology = ontology
        self.writer = writer
        self.applier = OperationApplier(ontology)

    def ingest(self, chapter: str, *, mode: Literal["extract", "reindex"]) -> dict[str, Any]:
        if mode not in {"extract", "reindex"}:
            raise IngestError("mode는 extract 또는 reindex여야 합니다")
        started = time.perf_counter()
        source = self._chapter_path(chapter)
        raw = source.read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        manifest_path = source.with_suffix(".story.json")
        if not manifest_path.is_file():
            raise IngestError(
                "명시적 ID binding manifest가 없습니다: "
                f"{manifest_path.relative_to(self.project_root)}"
            )
        try:
            manifest = BindingManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
        except Exception as exc:
            raise IngestError(f"binding manifest 형식 오류: {manifest_path}: {exc}") from exc
        if manifest.source_sha256 != source_hash:
            raise IngestError("binding manifest의 source_sha256가 현재 원고와 다릅니다")
        if manifest.unresolved:
            raise IngestError(
                f"명시적 ID로 해결되지 않은 언급이 {len(manifest.unresolved)}개 있습니다"
            )
        chapter_file = source.relative_to(self.project_root).as_posix()
        normalized_nodes = self._normalize_nodes(manifest.nodes, raw, chapter_file)
        normalized_edges = self._normalize_edges(manifest.edges, set(normalized_nodes))
        self._validate_scene_partition(normalized_nodes, len(raw))
        operations, reads, invalidated = self._diff(
            normalized_nodes,
            normalized_edges,
            chapter_file=chapter_file,
            source_hash=source_hash,
            mode=mode,
        )
        if not operations:
            return {
                "proposal_id": None,
                "status": "unchanged",
                "extracted": {
                    "nodes": len(normalized_nodes),
                    "edges": len(normalized_edges),
                    "facts": sum(
                        1 for payload in normalized_nodes.values() if payload["kind"] == "Fact"
                    ),
                },
                "invalidated": [],
                "source_sha256": source_hash,
                "prompt_version": "v1",
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        if not reads:
            reads = [{"node": "book", "rev": self.writer.graph_revision()["revision"]}]
        proposal = self.writer.propose(
            ops=operations,
            read_set=reads,
            rationale=f"P3 ingest {mode}: {chapter_file} @ {source_hash[:12]}",
            session_id=f"session/ingest-{source_hash[:16]}",
            actor_kind="agent",
            model_id="storyai-extractor-v1",
            host="codex",
        )
        return {
            **proposal,
            "extracted": {
                "nodes": len(normalized_nodes),
                "edges": len(normalized_edges),
                "facts": sum(
                    1 for payload in normalized_nodes.values() if payload["kind"] == "Fact"
                ),
            },
            "invalidated": invalidated,
            "source_sha256": source_hash,
            "prompt_version": "v1",
            "suggested_boundaries": SceneSplitter.suggest(raw),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    def _chapter_path(self, chapter: str) -> Path:
        if not isinstance(chapter, str) or not chapter.strip():
            raise IngestError("chapter는 비어 있지 않은 상대 경로여야 합니다")
        raw = chapter.strip().replace("\\", "/")
        if raw.startswith("manuscript/"):
            raw = raw[len("manuscript/") :]
        candidate = (self.manuscript_root / raw).resolve()
        if self.manuscript_root not in candidate.parents or candidate.suffix.casefold() != ".md":
            raise IngestError("chapter는 manuscript 아래의 .md 파일이어야 합니다")
        if not candidate.is_file():
            raise IngestError(f"원고 파일이 없습니다: {candidate}")
        return candidate

    def _normalize_nodes(
        self, nodes: list[ExtractedNode], raw: bytes, chapter_file: str
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        ordered = sorted(nodes, key=lambda item: (self._kind_priority(item.kind), item.id))
        for node in ordered:
            node_id = normalize_node_id(node.id, self.ontology)
            kind = self.ontology.canonical_kind(node.kind)
            if node_id in result:
                raise IngestError(f"manifest 노드 ID가 중복되었습니다: {node_id}")
            if not node_id.startswith(f"{kind.casefold()}/"):
                raise IngestError(f"manifest id/kind가 일치하지 않습니다: {node_id} / {kind}")
            evidence: list[dict[str, Any]] = []
            bodies: list[str] = []
            for span in sorted(node.evidence, key=lambda item: (item.start, item.end)):
                if span.end > len(raw) or span.end < span.start:
                    raise IngestError(f"근거 span이 원고 범위를 벗어났습니다: {node_id}")
                excerpt = raw[span.start : span.end]
                try:
                    decoded = excerpt.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise IngestError(f"근거 span이 UTF-8 문자 경계를 자릅니다: {node_id}") from exc
                if decoded != span.quote:
                    raise IngestError(f"근거 quote가 원고 바이트와 다릅니다: {node_id}")
                evidence.append(
                    {
                        "file": chapter_file,
                        "start": span.start,
                        "end": span.end,
                        "quote": span.quote,
                    }
                )
                bodies.append(decoded)
            payload = node.model_dump(exclude={"id", "evidence"}, mode="json")
            payload["kind"] = kind
            payload["body"] = "\n\n".join(bodies)
            payload["evidence"] = evidence
            normalized = self.applier.normalize(
                Operation.model_validate(
                    {
                        "verb": "ADD",
                        "target": node_id,
                        "to": payload,
                        "idem_key": "normalize-only",
                    }
                )
            )
            result[node_id] = dict(normalized.to_value)
        return result

    def _normalize_edges(
        self, edges: list[ExtractedEdge], manifest_nodes: set[str]
    ) -> set[tuple[str, str, str]]:
        result: set[tuple[str, str, str]] = set()
        for edge in edges:
            source = normalize_node_id(edge.src, self.ontology)
            target = normalize_node_id(edge.dst, self.ontology)
            if source not in manifest_nodes:
                raise IngestError(
                    f"파생 간선의 source는 이 manifest가 소유한 노드여야 합니다: {source}"
                )
            if edge.rel not in self.ontology.edges:
                raise IngestError(f"알 수 없는 manifest 간선입니다: {edge.rel}")
            value = (source, edge.rel, target)
            if value in result:
                raise IngestError(f"manifest 간선이 중복되었습니다: {value}")
            result.add(value)
        return result

    @staticmethod
    def _validate_scene_partition(nodes: dict[str, dict[str, Any]], source_size: int) -> None:
        scenes = [payload for payload in nodes.values() if payload["kind"] == "Scene"]
        if not scenes:
            raise IngestError("manifest에는 Scene 노드가 하나 이상 필요합니다")
        ranges: list[tuple[int, int]] = []
        for scene in scenes:
            props = scene.get("props", {})
            if not all(key in props for key in ("story_time", "location", "characters")):
                raise IngestError(
                    "추출 Scene.props에는 story_time, location, characters가 필요합니다"
                )
            if not isinstance(props["characters"], list):
                raise IngestError("추출 Scene.props.characters는 ID 배열이어야 합니다")
            evidence = scene["evidence"]
            if len(evidence) != 1:
                raise IngestError("추출 Scene은 전체 장면을 덮는 evidence 하나를 가져야 합니다")
            ranges.append((evidence[0]["start"], evidence[0]["end"]))
        ranges.sort()
        cursor = 0
        for start, end in ranges:
            if start != cursor or end <= start:
                raise IngestError("Scene evidence는 원고 전체를 빈틈과 겹침 없이 분할해야 합니다")
            cursor = end
        if cursor != source_size:
            raise IngestError("Scene evidence가 원고 끝까지 덮지 않습니다")

    def _diff(
        self,
        nodes: dict[str, dict[str, Any]],
        edges: set[tuple[str, str, str]],
        *,
        chapter_file: str,
        source_hash: str,
        mode: str,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
        operations: list[dict[str, Any]] = []
        invalidation_ops: list[dict[str, Any]] = []
        reads: dict[str, int] = {}
        with connect_read_only(self.db_path) as connection:
            existing_rows = {
                row["id"]: row
                for row in connection.execute(
                    "SELECT * FROM live_node WHERE id IN ({})".format(
                        ",".join("?" for _ in nodes) or "NULL"
                    ),
                    list(nodes),
                ).fetchall()
            }
            owned = {
                row["id"]
                for row in connection.execute(
                    """
                    SELECT DISTINCT n.id
                    FROM live_node AS n JOIN evidence AS ev ON ev.node=n.id
                    WHERE n.origin='agent' AND ev.file=?
                    """,
                    (chapter_file,),
                ).fetchall()
            }
            for payload in nodes.values():
                for dependency in self._payload_references(payload):
                    self._read_endpoint(connection, dependency, reads, nodes)
            for node_id, payload in nodes.items():
                current = existing_rows.get(node_id)
                if current is None:
                    operations.append(self._operation("ADD", node_id, source_hash, to=payload))
                    continue
                if node_id not in owned:
                    raise IngestError(
                        f"manifest가 사람이 소유한 기존 노드를 재정의할 수 없습니다: {node_id}"
                    )
                if current["kind"] != payload["kind"]:
                    raise IngestError(
                        f"기존 노드 kind는 변경할 수 없습니다: {node_id} "
                        f"({current['kind']} -> {payload['kind']})"
                    )
                reads[node_id] = int(current["rev"])
                for field in self.NODE_FIELDS:
                    desired = payload.get(field)
                    current_value = self.applier.current_field(connection, node_id, field)
                    if desired != current_value:
                        operations.append(
                            self._operation("UPDATE", node_id, source_hash, field=field, to=desired)
                        )
            removed = sorted(owned - set(nodes)) if mode == "reindex" else []
            for node_id in removed:
                row = connection.execute(
                    "SELECT rev FROM live_node WHERE id = ?", (node_id,)
                ).fetchone()
                reads[node_id] = int(row["rev"])
                invalidation_ops.append(self._operation("INVALIDATE", node_id, source_hash))
            live_edges = {
                (row["src"], row["rel"], row["dst"])
                for row in connection.execute(
                    """
                    SELECT src, rel, dst FROM live_edge
                    WHERE origin='agent' AND src IN ({})
                    """.format(",".join("?" for _ in owned) or "NULL"),
                    sorted(owned),
                ).fetchall()
            }
            for source, relation, target in sorted(live_edges - edges):
                if source in removed:
                    continue
                self._read_endpoint(connection, source, reads, nodes)
                self._read_endpoint(connection, target, reads, nodes)
                operations.append(
                    self._operation("UNLINK", source, source_hash, field=relation, to=target)
                )
            operations.extend(invalidation_ops)
            for source, relation, target in sorted(edges - live_edges):
                self._read_endpoint(connection, source, reads, nodes)
                self._read_endpoint(connection, target, reads, nodes)
                operations.append(
                    self._operation("LINK", source, source_hash, field=relation, to=target)
                )
        return (
            operations,
            [{"node": node_id, "rev": rev} for node_id, rev in sorted(reads.items())],
            removed,
        )

    @staticmethod
    def _payload_references(payload: dict[str, Any]) -> set[str]:
        props = payload.get("props", {})
        result = {
            value
            for field in ("F", "T", "P", "subject", "location")
            if isinstance((value := props.get(field)), str)
        }
        characters = props.get("characters", [])
        if isinstance(characters, list):
            result.update(item for item in characters if isinstance(item, str))
        for phase in ("pre", "post", "forbid"):
            conditions = props.get(phase, [])
            if isinstance(conditions, list):
                result.update(
                    item["subject"]
                    for item in conditions
                    if isinstance(item, dict) and isinstance(item.get("subject"), str)
                )
        claims = props.get("claims", [])
        if isinstance(claims, list):
            for claim in claims:
                if isinstance(claim, dict):
                    result.update(
                        claim[field]
                        for field in ("speaker", "fact")
                        if isinstance(claim.get(field), str)
                    )
        mentions = props.get("mentions", [])
        if isinstance(mentions, list):
            result.update(
                item["entity"]
                for item in mentions
                if isinstance(item, dict) and isinstance(item.get("entity"), str)
            )
        visible_to = payload.get("visible_to", [])
        for item in visible_to if isinstance(visible_to, list) else []:
            viewer = (
                item
                if isinstance(item, str)
                else item.get("viewer")
                if isinstance(item, dict)
                else None
            )
            if isinstance(viewer, str) and viewer != "reader":
                result.add(viewer)
        return result

    @staticmethod
    def _read_endpoint(
        connection: Any,
        node_id: str,
        reads: dict[str, int],
        manifest_nodes: dict[str, dict[str, Any]],
    ) -> None:
        if node_id in manifest_nodes and node_id not in reads:
            row = connection.execute(
                "SELECT rev FROM live_node WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                return
            reads[node_id] = int(row["rev"])
            return
        if node_id in reads:
            return
        row = connection.execute("SELECT rev FROM live_node WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            if node_id not in manifest_nodes:
                raise IngestError(f"manifest 간선 대상이 존재하지 않습니다: {node_id}")
            return
        reads[node_id] = int(row["rev"])

    @staticmethod
    def _operation(
        verb: str,
        target: str,
        source_hash: str,
        *,
        field: str | None = None,
        to: Any = None,
    ) -> dict[str, Any]:
        identity = hashlib.sha256(
            canonical_json([verb, target, field, to, source_hash]).encode("utf-8")
        ).hexdigest()[:20]
        operation: dict[str, Any] = {
            "verb": verb,
            "target": target,
            "idem_key": f"ingest-{identity}",
        }
        if field is not None:
            operation["field"] = field
        if verb in {"ADD", "UPDATE", "LINK", "UNLINK"}:
            operation["to"] = to
        return operation

    def _kind_priority(self, kind: str) -> int:
        canonical = self.ontology.canonical_kind(kind)
        order = {
            "Character": 0,
            "Location": 1,
            "Object": 2,
            "Faction": 3,
            "Fact": 4,
            "Rule": 5,
            "Scene": 6,
            "Event": 7,
            "Promise": 8,
        }
        return order.get(canonical, 9)

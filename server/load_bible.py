"""Explicit YAML-frontmatter bible bootstrapper for P0."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .core.address import parse_address
from .core.database import connect_bootstrap, connect_read_only, initialize_database
from .core.merkle import advance_graph_state, ensure_graph_state, record_revision, refresh_node_cid
from .core.ontology import Ontology


class BibleFormatError(ValueError):
    pass


class EdgeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rel: str
    to: str
    props: dict[str, Any] = Field(default_factory=dict)
    story_from: int | None = None
    story_to: int | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class NodeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    kind: str | None = None
    title: str | None = None
    summary: str | None = None
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    features: dict[str, dict[str, Any]] = Field(default_factory=dict)
    props: dict[str, Any] = Field(default_factory=dict)
    edges: list[EdgeInput] = Field(default_factory=list)
    story_from: int | None = None
    story_to: int | None = None
    reveal_at: int | None = None
    locked: bool | None = None
    origin: Literal["human"] = "human"

    @field_validator("aliases", "tags")
    @classmethod
    def unique_nonempty(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("값은 비어 있지 않은 문자열이어야 합니다")
        return list(dict.fromkeys(value.strip() for value in values))


class BibleNode(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: Path
    relative_file: str
    body_start: int
    body_end: int
    body: str
    id: str
    kind: str
    title: str
    summary: str
    aliases: list[str]
    tags: list[str]
    features: dict[str, dict[str, Any]]
    props: dict[str, Any]
    edges: list[EdgeInput]
    story_from: int | None
    story_to: int | None
    reveal_at: int | None
    locked: bool
    origin: Literal["human"]
    cid: str


FOLDER_KINDS = {
    "characters": "Character",
    "locations": "Location",
    "objects": "Object",
    "scenes": "Scene",
    "rules": "Rule",
    "promises": "Promise",
}


class BibleLoader:
    def __init__(
        self,
        *,
        project_root: str | Path,
        bible_root: str | Path,
        db_path: str | Path,
        ontology: Ontology,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.bible_root = Path(bible_root).resolve()
        self.db_path = Path(db_path).resolve()
        self.ontology = ontology
        if (
            self.project_root not in self.bible_root.parents
            and self.bible_root != self.project_root
        ):
            raise BibleFormatError("bible 경로는 프로젝트 안에 있어야 합니다")

    def load(self) -> dict[str, int]:
        nodes = [self._parse_file(path) for path in self._source_files()]
        ids = [node.id for node in nodes]
        duplicates = sorted({node_id for node_id in ids if ids.count(node_id) > 1})
        if duplicates:
            raise BibleFormatError(f"중복 노드 주소: {', '.join(duplicates)}")
        self._validate_edges(nodes)
        return self._write(nodes)

    def _source_files(self) -> list[Path]:
        if not self.bible_root.is_dir():
            raise BibleFormatError(f"bible 폴더가 없습니다: {self.bible_root}")
        return sorted(
            path for path in self.bible_root.rglob("*.md") if path.name.casefold() != "readme.md"
        )

    def _parse_file(self, path: Path) -> BibleNode:
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BibleFormatError(f"UTF-8이 아닌 설정집 파일: {path}") from exc
        metadata, body, body_start = self._frontmatter(text, path)
        try:
            item = NodeInput.model_validate(metadata)
        except Exception as exc:
            raise BibleFormatError(f"설정집 메타데이터 오류: {path}: {exc}") from exc
        inferred_kind = FOLDER_KINDS.get(path.parent.name.casefold())
        if item.kind is None and inferred_kind is None:
            raise BibleFormatError(f"kind를 지정하거나 표준 하위 폴더에 넣으세요: {path}")
        kind = self.ontology.canonical_kind(item.kind or inferred_kind or "", p0_only=True)
        title = (item.title or self._first_heading(body) or path.stem).strip()
        if not title:
            raise BibleFormatError(f"title을 결정할 수 없습니다: {path}")
        node_id = item.id or f"{kind.casefold()}/{self._address_segment(title)}"
        parsed = parse_address(node_id, self.ontology)
        if parsed.kind != kind:
            raise BibleFormatError(f"id kind와 kind 필드가 다릅니다: {path}: {node_id} / {kind}")
        node_id = parsed.value
        summary = self._summary(item.summary, body, title)
        tags = [tag if tag.startswith("#") else f"#{tag}" for tag in item.tags]
        locked = item.locked
        if locked is None:
            locked = self.ontology.kinds[kind].default_locked
        relative_file = path.relative_to(self.project_root).as_posix()
        body_start_bytes = len(text[:body_start].encode("utf-8"))
        canonical_edges = [edge.model_dump(mode="json") for edge in item.edges]
        canonical_edges.sort(
            key=lambda value: json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
        )
        canonical = {
            "id": node_id,
            "kind": kind,
            "title": title,
            "summary": summary,
            "aliases": sorted(item.aliases),
            "tags": sorted(tags),
            "features": item.features,
            "props": item.props,
            "edges": canonical_edges,
            "story_from": item.story_from,
            "story_to": item.story_to,
            "reveal_at": item.reveal_at,
            "locked": locked,
            "body": body,
        }
        cid = hashlib.sha256(
            json.dumps(
                canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()
        return BibleNode(
            source=path,
            relative_file=relative_file,
            body_start=body_start_bytes,
            body_end=len(raw),
            body=body,
            id=node_id,
            kind=kind,
            title=title,
            summary=summary,
            aliases=item.aliases,
            tags=tags,
            features=item.features,
            props=item.props,
            edges=item.edges,
            story_from=item.story_from,
            story_to=item.story_to,
            reveal_at=item.reveal_at,
            locked=bool(locked),
            origin=item.origin,
            cid=cid,
        )

    @staticmethod
    def _frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str, int]:
        opening = re.match(r"^---\r?\n", text)
        if opening is None:
            raise BibleFormatError(f"YAML front matter가 없습니다: {path}")
        content_start = opening.end()
        match = re.search(r"(?m)^---\s*\r?$", text[content_start:])
        if match is None:
            raise BibleFormatError(f"YAML front matter 닫힘 구분자가 없습니다: {path}")
        header_end = content_start + match.start()
        body_start = content_start + match.end()
        if body_start < len(text) and text[body_start] == "\r":
            body_start += 1
        if body_start < len(text) and text[body_start] == "\n":
            body_start += 1
        try:
            metadata = yaml.safe_load(text[content_start:header_end]) or {}
        except yaml.YAMLError as exc:
            raise BibleFormatError(f"YAML 파싱 실패: {path}: {exc}") from exc
        if not isinstance(metadata, dict):
            raise BibleFormatError(f"front matter는 객체여야 합니다: {path}")
        return metadata, text[body_start:], body_start

    @staticmethod
    def _first_heading(body: str) -> str | None:
        match = re.search(r"(?m)^#\s+(.+?)\s*$", body)
        return match.group(1) if match else None

    @staticmethod
    def _address_segment(title: str) -> str:
        value = re.sub(r"\s+", "", title.strip())
        value = value.replace("/", "-").replace("\\", "-")
        if value in {"", ".", ".."}:
            raise BibleFormatError(f"주소로 바꿀 수 없는 title입니다: {title!r}")
        return value

    @staticmethod
    def _summary(summary: str | None, body: str, title: str) -> str:
        if summary:
            value = " ".join(summary.split())
        else:
            paragraphs = [
                " ".join(line.split())
                for line in body.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            value = paragraphs[0] if paragraphs else title
        return value[:240]

    def _validate_edges(self, nodes: list[BibleNode]) -> None:
        by_id = {node.id: node for node in nodes}
        with connect_read_only(self.db_path) as connection:
            existing = {
                row["id"]: row["kind"]
                for row in connection.execute("SELECT id, kind FROM live_node").fetchall()
            }
        for node in nodes:
            for edge in node.edges:
                target = self._edge_target(edge.to)
                target_kind = by_id[target].kind if target in by_id else existing.get(target)
                if target_kind is None:
                    raise BibleFormatError(
                        f"간선 대상이 없습니다: {node.id} -[{edge.rel}]-> {target}"
                    )
                self.ontology.validate_edge(edge.rel, node.kind, target_kind)

    def _write(self, nodes: list[BibleNode]) -> dict[str, int]:
        now = datetime.now(UTC).isoformat()
        edge_count = 0
        changed_ids: set[str] = set()
        with connect_bootstrap(self.db_path) as connection:
            ensure_graph_state(connection)
            for node in nodes:
                current = connection.execute(
                    "SELECT cid, tx_to, rev FROM node WHERE id = ?", (node.id,)
                ).fetchone()
                if current is not None and current["cid"] == node.cid and current["tx_to"] is None:
                    continue
                changed_ids.add(node.id)
                if current is not None:
                    connection.execute(
                        "UPDATE node_revision SET tx_to = ? WHERE node = ? AND rev = ?",
                        (now, node.id, current["rev"]),
                    )
                connection.execute(
                    """
                    INSERT INTO node (
                        id, kind, title, summary, props, story_from, story_to, reveal_at,
                        tx_from, tx_to, origin, locked, rev, cid
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 1, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        kind=excluded.kind, title=excluded.title, summary=excluded.summary,
                        props=excluded.props, story_from=excluded.story_from,
                        story_to=excluded.story_to, reveal_at=excluded.reveal_at,
                        tx_from=excluded.tx_from, tx_to=NULL, origin=excluded.origin,
                        locked=excluded.locked, rev=node.rev + 1, cid=excluded.cid
                    """,
                    (
                        node.id,
                        node.kind,
                        node.title,
                        node.summary,
                        self._json(node.props),
                        node.story_from,
                        node.story_to,
                        node.reveal_at,
                        now,
                        node.origin,
                        int(node.locked),
                        node.cid,
                    ),
                )
                self._replace_node_details(connection, node)
                connection.execute("DELETE FROM node_fts WHERE id = ?", (node.id,))
                connection.execute(
                    "INSERT INTO node_fts(id, title, aliases, summary, body) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (node.id, node.title, " ".join(node.aliases), node.summary, node.body),
                )
                connection.execute(
                    "UPDATE edge SET tx_to = ? "
                    "WHERE src = ? AND tx_to IS NULL AND origin = 'human'",
                    (now, node.id),
                )
            for node in nodes:
                if node.id not in changed_ids:
                    continue
                for edge in node.edges:
                    spec = self.ontology.edges[edge.rel]
                    connection.execute(
                        """
                        INSERT INTO edge (
                            src, dst, rel, hard, props, story_from, story_to,
                            tx_from, tx_to, origin, confidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, 'human', ?)
                        """,
                        (
                            node.id,
                            self._edge_target(edge.to),
                            edge.rel,
                            int(spec.hard),
                            self._json(edge.props),
                            edge.story_from,
                            edge.story_to,
                            now,
                            edge.confidence,
                        ),
                    )
                    edge_count += 1
            for node_id in sorted(changed_ids):
                refresh_node_cid(connection, node_id)
                record_revision(connection, node_id, proposal_id=None, replace=True)
            if changed_ids:
                advance_graph_state(connection, now)
        return {"nodes": len(nodes), "edges": edge_count}

    def _replace_node_details(self, connection: Any, node: BibleNode) -> None:
        for table in ("node_alias", "node_tag", "feature", "evidence"):
            key = "node"
            connection.execute(f"DELETE FROM {table} WHERE {key} = ?", (node.id,))
        connection.executemany(
            "INSERT INTO node_alias(node, alias) VALUES (?, ?)",
            [(node.id, alias) for alias in node.aliases],
        )
        for tag in node.tags:
            connection.execute("INSERT OR IGNORE INTO tag(name) VALUES (?)", (tag,))
            connection.execute("INSERT INTO node_tag(node, tag) VALUES (?, ?)", (node.id, tag))
        connection.executemany(
            "INSERT INTO feature(node, name, data) VALUES (?, ?, ?)",
            [(node.id, name, self._json(data)) for name, data in node.features.items()],
        )
        connection.execute(
            """
            INSERT INTO evidence(node, file, start_off, end_off, quote)
            VALUES (?, ?, ?, ?, ?)
            """,
            (node.id, node.relative_file, node.body_start, node.body_end, node.summary),
        )

    def _edge_target(self, value: str) -> str:
        parsed = parse_address(value, self.ontology)
        if parsed.kind is None:
            raise BibleFormatError(f"간선 대상은 절대 주소여야 합니다: {value!r}")
        return parsed.value

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Load explicit bible Markdown into story.db")
    parser.add_argument("--bible", default="bible", help="bible directory relative to project root")
    parser.add_argument(
        "--db", default=None, help="database path (defaults to STORYAI_DB/store/story.db)"
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    raw_db = args.db or os.environ.get("STORYAI_DB", str(root / "store" / "story.db"))
    raw_db = raw_db.replace("${PROJECT_DIR}", str(root))
    db_path = initialize_database(raw_db, root / "spec" / "schema.sql")
    loader = BibleLoader(
        project_root=root,
        bible_root=(root / args.bible),
        db_path=db_path,
        ontology=Ontology.load(root / "spec" / "ontology.json"),
    )
    print(json.dumps(loader.load(), ensure_ascii=False))


if __name__ == "__main__":
    main()

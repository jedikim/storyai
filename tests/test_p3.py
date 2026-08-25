from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest

from server.core.database import connect_read_only
from server.core.ingest import IngestError, SceneSplitter
from server.core.service import StoryService


def byte_span(raw: bytes, quote: str, *, start: int = 0) -> dict[str, Any]:
    encoded = quote.encode("utf-8")
    offset = raw.index(encoded, start)
    return {"start": offset, "end": offset + len(encoded), "quote": quote}


def write_chapter(
    service: StoryService,
    text: str,
    *,
    include_fact: bool = True,
    relation: str = "contains",
    unresolved: list[Any] | None = None,
) -> Path:
    chapter = service.project_root / "manuscript" / "A1" / "ch01.md"
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text(text, encoding="utf-8")
    raw = chapter.read_bytes()
    character = byte_span(raw, "미나")
    location = byte_span(raw, "항구")
    nodes: list[dict[str, Any]] = [
        {
            "id": "character/미나",
            "kind": "Character",
            "title": "미나",
            "summary": "열쇠를 추적하는 인물",
            "evidence": [character],
        },
        {
            "id": "location/항구",
            "kind": "Location",
            "title": "항구",
            "summary": "사건이 시작되는 항구",
            "evidence": [location],
        },
        {
            "id": "scene/A1.C01.S01",
            "kind": "Scene",
            "title": "항구의 열쇠",
            "summary": "미나가 항구에서 열쇠를 찾는다.",
            "props": {
                "story_time": 1,
                "location": "location/항구",
                "characters": ["character/미나"],
            },
            "story_from": 1,
            "reveal_at": 1,
            "evidence": [{"start": 0, "end": len(raw), "quote": text}],
        },
    ]
    if include_fact:
        nodes.append(
            {
                "id": "fact/미나가열쇠를찾음",
                "kind": "Fact",
                "title": "미나가 열쇠를 찾음",
                "summary": "항구에서 열쇠가 발견되었다.",
                "props": {
                    "subject": "character/미나",
                    "predicate": "found",
                    "object": "열쇠",
                },
                "visible_to": [{"viewer": "character/미나", "learned_at": 1, "pathway": "direct"}],
                "evidence": [byte_span(raw, text.rstrip("\n"))],
            }
        )
    edges = [
        {"src": "scene/A1.C01.S01", "rel": "occurs_at", "dst": "location/항구"},
        {
            "src": "scene/A1.C01.S01",
            "rel": "present_at",
            "dst": "character/미나",
        },
    ]
    if include_fact:
        edges.append(
            {
                "src": "scene/A1.C01.S01",
                "rel": relation,
                "dst": "fact/미나가열쇠를찾음",
            }
        )
    manifest = {
        "version": 1,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "nodes": nodes,
        "edges": edges,
        "unresolved": unresolved or [],
    }
    chapter.with_suffix(".story.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return chapter


def test_scene_splitter_uses_exact_structural_byte_boundaries() -> None:
    raw = "## 첫 장면\n가나다\n***\n## 둘째 장면\n라마바\n".encode()
    ranges = SceneSplitter.suggest(raw)
    assert ranges[0]["start"] == 0
    assert ranges[-1]["end"] == len(raw)
    assert b"".join(raw[item["start"] : item["end"]] for item in ranges) == raw
    assert len(ranges) == 3


def test_manifest_ingest_is_proposal_only_and_commits_under_half_second(
    service: StoryService,
) -> None:
    text = "미나는 항구에서 열쇠를 찾았다.\n"
    write_chapter(service, text)
    before = service.writer.graph_revision()["revision"]

    started = time.perf_counter()
    result = service.ingest("A1/ch01.md", mode="extract")
    elapsed = time.perf_counter() - started

    assert result["status"] == "open"
    assert result["extracted"] == {"nodes": 4, "edges": 3, "facts": 1}
    assert elapsed < 0.5
    assert service.writer.graph_revision()["revision"] == before
    with pytest.raises(ValueError, match="찾을 수 없"):
        service.get("scene/A1.C01.S01")

    commit_started = time.perf_counter()
    committed = service.commit(result["proposal_id"])
    assert time.perf_counter() - commit_started < 0.5
    assert committed["status"] == "accepted"
    body = service.get("scene/A1.C01.S01", include="body")[0]
    assert body["body"] == text
    assert body["evidence"][0]["start"] == 0
    assert service.find("항구 열쇠", mode="hybrid")[0]["id"] == "scene/A1.C01.S01"
    assert service.ingest("A1/ch01.md", mode="reindex")["status"] == "unchanged"


def test_reindex_updates_nodes_and_invalidates_removed_derived_state(
    service: StoryService,
) -> None:
    write_chapter(service, "미나는 항구에서 열쇠를 찾았다.\n")
    first = service.ingest("A1/ch01.md", mode="extract")
    service.commit(first["proposal_id"])

    write_chapter(service, "미나는 항구에서 낡은 지도를 찾았다.\n", include_fact=False)
    started = time.perf_counter()
    result = service.ingest("A1/ch01.md", mode="reindex")
    assert time.perf_counter() - started < 0.5
    assert result["invalidated"] == ["fact/미나가열쇠를찾음"]
    assert service.get("scene/A1.C01.S01")[0]["summary"] == "미나가 항구에서 열쇠를 찾는다."

    committed = service.commit(result["proposal_id"])
    assert committed["status"] == "accepted"
    assert "낡은 지도" in service.get("scene/A1.C01.S01", include="body")[0]["body"]
    with pytest.raises(ValueError, match="찾을 수 없"):
        service.get("fact/미나가열쇠를찾음")
    assert all(
        item["id"] != "fact/미나가열쇠를찾음"
        for item in service.refs("scene/A1.C01.S01", dir="out")
    )


def test_ingest_rejects_unresolved_ids_hash_drift_and_bad_spans(
    service: StoryService,
) -> None:
    chapter = write_chapter(
        service,
        "미나는 항구에서 열쇠를 찾았다.\n",
        unresolved=[{"surface": "그녀", "reason": "ambiguous"}],
    )
    with pytest.raises(IngestError, match="해결되지 않은"):
        service.ingest("A1/ch01.md", mode="extract")

    manifest_path = chapter.with_suffix(".story.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["unresolved"] = []
    manifest["source_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(IngestError, match="source_sha256"):
        service.ingest("A1/ch01.md", mode="extract")

    manifest["source_sha256"] = hashlib.sha256(chapter.read_bytes()).hexdigest()
    manifest["nodes"][0]["evidence"][0]["quote"] = "다른 이름"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(IngestError, match="quote"):
        service.ingest("A1/ch01.md", mode="extract")


def test_local_dense_hybrid_rrf_and_filters(service: StoryService) -> None:
    lexical = service.find("왼손잡이", mode="lexical")
    semantic = service.find("도영", mode="semantic")
    hybrid = service.find("도영", mode="hybrid")
    assert lexical[0]["id"] == "character/한도영"
    assert semantic[0]["id"] == "character/한도영"
    assert hybrid[0]["id"] == "character/한도영"
    assert hybrid[0]["score"] > 1 / 61
    assert service.find("도영", kind=["Location"], mode="semantic") == []
    assert service.find("도영", tag=["POV"], mode="semantic")[0]["id"] == "character/한도영"
    assert service.embeddings.sync_all()["changed"] == 0


def test_trace_neighborhood_impact_and_query_are_bounded_and_read_only(
    service: StoryService,
) -> None:
    paths = service.trace(
        "scene/A1.C03.S01",
        target="object/젖은장갑",
        via=["contains"],
        max_depth=3,
    )
    assert paths == [
        {
            "path": ["scene/A1.C03.S01", "object/젖은장갑"],
            "rels": ["contains"],
            "depth": 1,
        }
    ]
    packet = service.neighborhood(
        "젖은 장갑",
        anchors=["character/한도영"],
        budget_tokens=300,
    )
    assert packet["used_tokens"] <= 300
    assert packet["packet"][0]["id"] == "character/한도영"
    assert any(item["relations"] for item in packet["packet"])

    before = service.writer.graph_revision()
    preview = service.impact(
        "object/젖은장갑",
        change={"field": "reveal_at", "to": 9},
        max_depth=3,
    )
    assert preview["affected"][0]["id"] == "object/젖은장갑"
    assert any(item["id"] == "scene/A1.C03.S01" for item in preview["affected"])
    assert "timeline.absolute" in preview["broken_rules"]
    assert service.writer.graph_revision() == before

    queried = service.query(
        "SELECT id, kind FROM live_node WHERE kind = :kind ORDER BY id",
        params={"kind": "Character"},
        limit=1,
    )
    assert queried == {
        "columns": ["id", "kind"],
        "rows": [["character/한도영", "Character"]],
        "truncated": False,
    }
    with pytest.raises(ValueError, match="SELECT 또는 WITH"):
        service.query("DELETE FROM node")
    with pytest.raises(ValueError, match="세미콜론"):
        service.query("SELECT 1; SELECT 2")


def test_offline_consolidation_and_reopen_are_idempotent(service: StoryService) -> None:
    assert service.consolidate()["status"] == "consolidated"
    reopened = StoryService(
        project_root=service.project_root,
        db_path=service.db_path,
        ontology_path=service.project_root / "spec" / "ontology.json",
        rules_path=service.project_root / "spec" / "rules.json",
        schema_path=service.project_root / "spec" / "schema.sql",
        policy_path=service.project_root / "spec" / "policy.json",
    )
    assert reopened.find("도영", mode="semantic")[0]["id"] == "character/한도영"
    with connect_read_only(service.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM node_embedding").fetchone()[0] == 4

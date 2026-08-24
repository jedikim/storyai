from __future__ import annotations

import sqlite3

import pytest

from server.core.budget import serialized_size
from server.core.database import connect_read_only
from server.core.service import StoryService
from server.load_bible import BibleLoader


def test_p0_acceptance_queries(service: StoryService) -> None:
    outline = service.outline("book")
    assert {item["id"] for item in outline} == {
        "character/한도영",
        "object/젖은장갑",
        "promise/숨은열쇠",
        "scene/A1.C03.S01",
    }

    found = service.find("도영")
    assert found[0]["id"] == "character/한도영"

    brief = service.get("character/한도영", include="brief")
    assert brief == [
        {
            "id": "character/한도영",
            "kind": "Character",
            "title": "한도영",
            "summary": "등대지기. 3장부터 용의선상.",
            "rev": 1,
        }
    ]

    refs = service.refs("character/한도영", dir="in")
    assert [(item["rel"], item["id"]) for item in refs] == [("present_at", "scene/A1.C03.S01")]

    schema = service.graph_schema()
    assert any(item["name"] == "Character" for item in schema["kinds"])


def test_soft_refs_are_opt_in(service: StoryService) -> None:
    refs = service.refs("character/한도영", dir="in", include_soft=True)
    assert {item["rel"] for item in refs} == {"present_at", "mentioned_in"}


def test_recursive_walk_and_edge_cutoff(service: StoryService) -> None:
    reached = service.store.walk("scene/A1.C03.S01", relations=["contains"], max_depth=3)
    assert reached == [
        {"id": "scene/A1.C03.S01", "depth": 0},
        {"id": "object/젖은장갑", "depth": 1},
    ]
    assert service.refs("character/한도영", as_of=2) == []


def test_as_of_blocks_future_reveals(service: StoryService) -> None:
    with pytest.raises(ValueError, match="공개되지 않은"):
        service.get("promise/숨은열쇠", as_of=4)
    assert service.find("열쇠", as_of=4) == []


def test_fts_searches_bible_body(service: StoryService) -> None:
    assert service.find("왼손잡이")[0]["id"] == "character/한도영"


def test_body_reads_only_selected_evidence_span(service: StoryService) -> None:
    result = service.get("한도영", include="body")
    assert "왼손잡이 등대지기다." in result[0]["body"]
    assert "aliases:" not in result[0]["body"]
    with pytest.raises(ValueError, match="노드 하나"):
        service.get(["한도영", "젖은장갑"], include="body")


def test_response_degrades_without_hard_truncation(service: StoryService) -> None:
    result = service.graph_schema(max_chars=256)
    assert "guidance" in result
    assert serialized_size(result) <= 256


def test_read_connection_is_query_only(service: StoryService) -> None:
    with (
        connect_read_only(service.db_path) as connection,
        pytest.raises(sqlite3.OperationalError, match="readonly"),
    ):
        connection.execute("DELETE FROM node")


def test_outline_never_contains_body(service: StoryService) -> None:
    result = service.outline("book", response_format="detailed")
    assert all("body" not in item for item in result)


def test_reloading_unchanged_bible_is_idempotent(service: StoryService) -> None:
    result = BibleLoader(
        project_root=service.project_root,
        bible_root=service.project_root / "bible",
        db_path=service.db_path,
        ontology=service.ontology,
    ).load()
    assert result == {"nodes": 4, "edges": 0}
    assert service.get("한도영")[0]["rev"] == 1
    assert len(service.refs("한도영", include_soft=True)) == 2


def test_reloading_changed_bible_revises_only_changed_node(service: StoryService) -> None:
    scene_file = service.project_root / "bible" / "scenes" / "A1.C03.S01.md"
    scene_file.write_text(
        scene_file.read_text(encoding="utf-8").replace(
            "summary: 한도영이 부두에서 젖은 장갑을 발견한다.",
            "summary: 한도영이 방파제에서 젖은 장갑을 발견한다.",
        ),
        encoding="utf-8",
    )

    result = BibleLoader(
        project_root=service.project_root,
        bible_root=service.project_root / "bible",
        db_path=service.db_path,
        ontology=service.ontology,
    ).load()

    assert result == {"nodes": 4, "edges": 3}
    assert service.get("scene/A1.C03.S01")[0]["rev"] == 2
    assert service.get("한도영")[0]["rev"] == 1
    assert len(service.refs("한도영", include_soft=True)) == 2

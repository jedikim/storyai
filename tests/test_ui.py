from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server.core.service import StoryService
from server.runtime import manage_project, reset_service
from server.ui import create_ui_app


def client_for(service: StoryService, tmp_path: Path) -> TestClient:
    return TestClient(create_ui_app(service, dist_dir=tmp_path / "missing-dist"))


def test_ui_serves_bundled_react_app(service: StoryService) -> None:
    with TestClient(create_ui_app(service)) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text


def test_ui_rest_reuses_graph_core_and_exposes_read_models(
    service: StoryService, tmp_path: Path
) -> None:
    with client_for(service, tmp_path) as client:
        health = client.get("/api/health")
        graph = client.get("/api/graph", params={"as_of": 3})
        graph_at_one = client.get("/api/graph", params={"as_of": 1})
        node = client.get("/api/nodes/character/한도영")
        node_at_one = client.get("/api/nodes/character/한도영", params={"as_of": 1})
        promises = client.get("/api/promises")
        timeline = client.get("/api/timeline")
        search = client.get("/api/search", params={"q": "도영"})
        projects = client.get("/api/projects")
        fixed_switch = client.post("/api/projects/select", json={"name": "other"})
        root = client.get("/")

    assert health.status_code == 200
    assert health.json()["connected"] is True
    assert health.json()["nodes"] == 4
    assert graph.status_code == 200
    assert len(graph.json()["nodes"]) == 3
    assert len(graph.json()["edges"]) == 3
    assert graph.json()["kind_counts"]["Character"] == 1
    assert len(graph_at_one.json()["nodes"]) == 1
    assert graph_at_one.json()["edges"] == []
    assert node.status_code == 200
    assert node.json()["id"] == "character/한도영"
    assert "왼손잡이" in node.json()["body"]
    assert node.json()["evidence"]
    assert node.json()["history"][0]["origin"] == "human"
    assert node.json()["refs"]
    assert {item["direction"] for item in node.json()["refs"]} <= {"in", "out"}
    assert node_at_one.json()["refs"] == []
    assert promises.status_code == 200
    assert promises.json()[0]["title"] == "숨은 열쇠"
    assert timeline.status_code == 200
    assert any(item["id"] == "scene/A1.C03.S01" for item in timeline.json()["points"])
    assert timeline.json()["max_chapter"] == 5
    assert search.json()[0]["id"] == "character/한도영"
    assert search.json()[0]["layer"] == "substance"
    assert projects.json() == {
        "mode": "list",
        "selected": "storyai",
        "projects": [{"name": "storyai", "selected": True, "available": True}],
    }
    assert fixed_switch.status_code == 400
    assert "npm run dev" in root.json()["frontend"]


def test_review_queue_diff_impact_and_commit_flow(service: StoryService, tmp_path: Path) -> None:
    current = service.get("character/한도영", include="full")[0]
    proposal = service.propose(
        ops=[
            {
                "verb": "UPDATE",
                "target": current["id"],
                "field": "summary",
                "from": current["summary"],
                "to": "UI 검수 큐에서 승인할 변경",
                "idem_key": "p4-ui-review-flow-001",
            }
        ],
        read_set=[{"node": current["id"], "rev": current["rev"]}],
        rationale="P4 inline diff와 승인 흐름 검증",
        session_id="session/p4-ui-test",
        actor_kind="agent",
        model_id="pytest-ui",
        host="test",
    )

    with client_for(service, tmp_path) as client:
        queue = client.get("/api/proposals")
        impact = client.post("/api/proposals/impact", json={"proposal_id": proposal["proposal_id"]})
        preview = client.post(
            "/api/proposals/commit",
            json={"proposal_id": proposal["proposal_id"], "mode": "dry_run"},
        )
        committed = client.post(
            "/api/proposals/commit",
            json={"proposal_id": proposal["proposal_id"], "mode": "apply"},
        )
        queue_after = client.get("/api/proposals")

    assert queue.status_code == 200
    item = next(value for value in queue.json() if value["id"] == proposal["proposal_id"])
    assert item["risk"] == "review"
    assert item["actor_kind"] == "agent"
    assert item["ops"][0]["from"] == current["summary"]
    assert item["ops"][0]["to"] == "UI 검수 큐에서 승인할 변경"
    assert impact.status_code == 200 and impact.json()["previews"][0]["affected"]
    assert preview.json()["status"] == "dry_run"
    assert committed.json()["status"] == "accepted"
    assert all(value["id"] != proposal["proposal_id"] for value in queue_after.json())
    assert service.get("character/한도영")[0]["summary"] == "UI 검수 큐에서 승인할 변경"


def test_ui_edits_summary_through_human_proposal_and_commit(
    service: StoryService,
    tmp_path: Path,
) -> None:
    current = service.get("object/젖은장갑", include="full")[0]
    with client_for(service, tmp_path) as client:
        updated = client.post(
            "/api/nodes/object/젖은장갑/summary",
            json={"rev": current["rev"], "summary": "  UI에서 저장한 상세 설명.  "},
        )
        stale = client.post(
            "/api/nodes/object/젖은장갑/summary",
            json={"rev": current["rev"], "summary": "오래된 화면의 변경"},
        )
        queue = client.get("/api/proposals")

    assert updated.status_code == 200
    assert updated.json()["status"] == "accepted"
    assert updated.json()["node"]["summary"] == "UI에서 저장한 상세 설명."
    assert updated.json()["node"]["rev"] == current["rev"] + 1
    assert updated.json()["node"]["history"][0]["actor_kind"] == "human"
    assert updated.json()["node"]["history"][0]["host"] == "ui"
    assert updated.json()["node"]["history"][0]["rationale"] == "UI에서 노드 설명 편집"
    assert stale.status_code == 400
    assert "리비전 충돌" in stale.json()["detail"]
    assert queue.json() == []


def test_ui_refuses_summary_edits_for_locked_nodes(
    service: StoryService,
    tmp_path: Path,
) -> None:
    graph = service.writer.graph_revision()
    proposal = service.propose(
        ops=[
            {
                "verb": "ADD",
                "target": "rule/UI잠금",
                "to": {"kind": "Rule", "title": "UI 잠금 규칙", "summary": "변경 불가"},
                "idem_key": "ui-locked-rule-001",
            }
        ],
        read_set=[{"node": "book", "rev": graph["revision"]}],
        rationale="UI locked summary test",
        session_id="session/ui-locked-test",
        host="test",
    )
    service.commit(proposal["proposal_id"])

    with client_for(service, tmp_path) as client:
        response = client.post(
            "/api/nodes/rule/UI잠금/summary",
            json={"rev": 1, "summary": "잠금 우회"},
        )

    assert response.status_code == 400
    assert "canon 잠금" in response.json()["detail"]


def test_ui_api_fails_closed_for_invalid_input(service: StoryService, tmp_path: Path) -> None:
    with client_for(service, tmp_path) as client:
        assert client.get("/api/graph", params={"as_of": -1}).status_code == 422
        assert client.get("/api/nodes/character/한도영", params={"as_of": 0}).status_code == 400
        missing = client.get("/api/nodes/not-a-node")
        empty_search = client.get("/api/search", params={"q": ""})
        missing_proposal = client.post(
            "/api/proposals/impact", json={"proposal_id": "proposal/missing"}
        )
    assert missing.status_code == 400
    assert empty_search.status_code == 422
    assert missing_proposal.status_code == 400


def test_ui_hides_internal_session_nodes_from_story_graph_and_counts(
    service: StoryService,
    tmp_path: Path,
) -> None:
    graph = service.writer.graph_revision()
    proposal = service.propose(
        ops=[
            {
                "verb": "ADD",
                "target": "session/2026-08-25T16-00-00Z-ui-internal",
                "to": {
                    "kind": "Session",
                    "title": "UI 내부 운영 세션",
                    "props": {
                        "open_threads": ["다음 작업"],
                        "next": ["서사 노드 작업"],
                    },
                },
                "idem_key": "ui-internal-session-001",
            }
        ],
        read_set=[{"node": "book", "rev": graph["revision"]}],
        rationale="UI internal node filtering",
        session_id="session/ui-internal-test",
        host="test",
    )
    service.commit(proposal["proposal_id"])

    with client_for(service, tmp_path) as client:
        health = client.get("/api/health").json()
        graph_payload = client.get("/api/graph").json()
        search = client.get("/api/search", params={"q": "UI 내부 운영 세션"}).json()
        detail = client.get("/api/nodes/session/2026-08-25T16-00-00Z-ui-internal")

    assert (
        len(
            service.store.get_nodes(
                ["session/2026-08-25T16-00-00Z-ui-internal"], include="brief", as_of=None
            )
        )
        == 1
    )
    assert health["nodes"] == 4
    assert len(graph_payload["nodes"]) == 4
    assert "Session" not in graph_payload["kind_counts"]
    assert all(item["kind"] != "Session" for item in graph_payload["nodes"])
    assert all(item["kind"] != "Session" for item in search)
    assert detail.status_code == 400
    assert "운영 노드" in detail.json()["detail"]


def test_ui_lists_and_switches_projects(
    service: StoryService,
    tmp_path: Path,
    monkeypatch,
) -> None:
    registry = tmp_path / "ui-projects.json"
    target = tmp_path / "novel-ui"
    monkeypatch.setenv("STORYAI_PROJECT_ROOT", str(service.project_root))
    monkeypatch.setenv("STORYAI_DB", str(service.db_path))
    monkeypatch.setenv("STORYAI_PROJECTS_FILE", str(registry))
    reset_service()
    try:
        manage_project(mode="create", name="UI 테스트 소설", path=str(target))
        manage_project(mode="select", name="storyai")
        with TestClient(create_ui_app(dist_dir=tmp_path / "missing-dist")) as client:
            projects = client.get("/api/projects")
            selected = client.post(
                "/api/projects/select",
                json={"name": "UI 테스트 소설"},
            )
            health = client.get("/api/health")
            missing = client.post("/api/projects/select", json={"name": "missing"})

        assert projects.status_code == 200
        assert projects.json()["selected"] == "storyai"
        assert {item["name"] for item in projects.json()["projects"]} == {
            "storyai",
            "UI 테스트 소설",
        }
        assert all("root" not in item and "db" not in item for item in projects.json()["projects"])
        assert selected.status_code == 200
        assert selected.json()["selected"] == "UI 테스트 소설"
        assert "root" not in selected.json()["project"]
        assert health.json()["book"] == "novel-ui"
        assert health.json()["nodes"] == 0
        assert missing.status_code == 400
    finally:
        reset_service()

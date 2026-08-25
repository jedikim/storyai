from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from server.core.service import StoryService
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

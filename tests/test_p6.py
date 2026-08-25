from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from server.core.database import connect_write, initialize_database
from server.core.rederive import CascadeWorker, WebhookRederiveProvider
from server.core.service import StoryService


class FakeProvider:
    def __init__(self, values: list[Any]) -> None:
        self.values = list(values)
        self.requests: list[dict[str, Any]] = []

    def rederive(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        return {"value": self.values.pop(0), "model_id": "fake/rederive-v1"}


class FailingProvider:
    def rederive(self, request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("temporary provider failure")


class ClaimStealingProvider:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def rederive(self, request: dict[str, Any]) -> dict[str, Any]:
        with connect_write(self.db_path) as connection:
            connection.execute(
                "UPDATE cascade_job SET claim_token = 'new-owner' WHERE id = ?",
                (request["job_id"],),
            )
        return {"value": "must not become a proposal", "model_id": "fake/stolen"}


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/rederive",
        "ftp://127.0.0.1/rederive",
        "https://user:password@example.com/rederive",
    ],
)
def test_rederive_endpoint_requires_secure_transport(endpoint: str) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        WebhookRederiveProvider(endpoint)
    assert WebhookRederiveProvider("http://127.0.0.1:8766/rederive")


def _node(service: StoryService, node_id: str) -> dict[str, Any]:
    return service.get(node_id, include="full")[0]


def _add_concept(
    service: StoryService,
    name: str,
    *,
    value: int,
    session_id: str,
    actor_kind: str = "human",
    source: str | None = None,
    rederive: bool = False,
    summary: str | None = None,
) -> dict[str, Any]:
    props: dict[str, Any] = {"value": value}
    if rederive:
        assert source is not None
        props["_rederive"] = [
            {
                "sources": [source],
                "target_field": "summary",
                "instruction": "바뀐 구조 사실에 맞춰 한 줄 요약을 다시 작성한다.",
                "max_tokens": 200,
            }
        ]
    reads = (
        [{"node": source, "rev": _node(service, source)["rev"]}]
        if source is not None
        else [{"node": "book", "rev": service.writer.graph_revision()["revision"]}]
    )
    proposal = service.propose(
        ops=[
            {
                "verb": "ADD",
                "target": f"concept/{name}",
                "to": {
                    "kind": "Concept",
                    "title": name,
                    "summary": summary,
                    "props": props,
                },
                "idem_key": f"p6-add-{name}-0001",
            }
        ],
        read_set=reads,
        rationale=f"P6 add {name}",
        session_id=session_id,
        actor_kind=actor_kind,  # type: ignore[arg-type]
        host="test",
    )
    return service.commit(proposal["proposal_id"])


def _update_value(
    service: StoryService,
    node_id: str,
    old: int,
    new: int,
    suffix: str,
    *,
    session_id: str,
) -> dict[str, Any]:
    current = _node(service, node_id)
    proposal = service.propose(
        ops=[
            {
                "verb": "UPDATE",
                "target": node_id,
                "field": "props.value",
                "from": old,
                "to": new,
                "basis_rev": current["rev"],
                "idem_key": f"p6-update-{suffix}-0001",
            }
        ],
        read_set=[{"node": node_id, "rev": current["rev"]}],
        rationale=f"P6 update {suffix}",
        session_id=session_id,
        host="test",
    )
    return service.commit(proposal["proposal_id"])


def test_initialize_database_migrates_existing_p6_job_table(tmp_path: Path) -> None:
    database = tmp_path / "legacy-p6.db"
    schema = Path(__file__).parents[1] / "spec" / "schema.sql"
    initialize_database(database, schema)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE cascade_job")
        connection.execute(
            """
            CREATE TABLE cascade_job (
              id TEXT PRIMARY KEY, run TEXT NOT NULL, node TEXT NOT NULL,
              depth INTEGER NOT NULL, sources TEXT NOT NULL,
              target_field TEXT NOT NULL, instruction TEXT NOT NULL,
              original_rev INTEGER NOT NULL, source_revs TEXT NOT NULL,
              max_tokens INTEGER NOT NULL, status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, lease_until TEXT,
              proposal TEXT, error TEXT, ts TEXT NOT NULL, updated_at TEXT NOT NULL
            )
            """
        )
    initialize_database(database, schema)
    with sqlite3.connect(database) as connection:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(cascade_job)")}
    assert {"target_rev", "claim_token"} <= columns


def test_lease_overlap_renew_list_release_and_expiry(service: StoryService) -> None:
    with pytest.raises(ValueError, match="book scope"):
        service.lease(
            mode="acquire",
            scope="book.*",
            session_id="session/invalid",
        )
    acquired = service.lease(
        mode="acquire",
        scope="story://scene/A2.C14.*",
        ttl_sec=900,
        session_id="session/agent-a",
        model_id="agent-a",
        note="chapter 14",
    )
    renewed = service.lease(
        mode="acquire",
        scope="scene/A2.C14.*",
        ttl_sec=1200,
        session_id="session/agent-a",
    )
    conflict = service.lease(
        mode="acquire",
        scope="scene/A2.C14.S03",
        session_id="session/agent-b",
    )
    separate = service.lease(
        mode="acquire",
        scope="scene/A2.C15.*",
        session_id="session/agent-b",
    )

    assert acquired["acquired"] is True
    assert renewed["renewed"] is True
    assert renewed["lease_id"] == acquired["lease_id"]
    assert conflict["acquired"] is False
    assert conflict["conflicts"][0]["session_id"] == "session/agent-a"
    assert separate["acquired"] is True
    listed = service.lease(
        mode="list",
        scope="scene/A2.C14.S01",
        session_id="session/observer",
    )
    assert [item["lease_id"] for item in listed["leases"]] == [acquired["lease_id"]]

    wrong = service.lease(
        mode="release",
        scope="scene/A2.C14.*",
        session_id="session/agent-b",
    )
    released = service.lease(
        mode="release",
        scope="scene/A2.C14.*",
        session_id="session/agent-a",
    )
    assert wrong["released"] == 0
    assert released["released"] == 1

    with connect_write(service.db_path) as connection:
        connection.execute(
            "UPDATE lease SET expires_at = ? WHERE id = ?",
            ((datetime.now(UTC) - timedelta(seconds=1)).isoformat(), separate["lease_id"]),
        )
    assert service.lease(mode="list", session_id="session/observer")["leases"] == []


def test_concurrent_lease_acquire_has_one_winner(service: StoryService) -> None:
    def acquire(index: int) -> dict[str, Any]:
        return service.lease(
            mode="acquire",
            scope="scene/A3.C01.*",
            session_id=f"session/concurrent-{index}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(acquire, range(2)))

    assert [result["acquired"] for result in results].count(True) == 1
    assert [result["acquired"] for result in results].count(False) == 1


def test_session_branches_track_parent_head_and_conflict(service: StoryService) -> None:
    current = _node(service, "character/한도영")
    parent = service.propose(
        ops=[
            {
                "verb": "UPDATE",
                "target": "character/한도영",
                "field": "summary",
                "from": current["summary"],
                "to": "parent branch",
                "basis_rev": current["rev"],
                "idem_key": "p6-parent-branch-0001",
            }
        ],
        read_set=[{"node": "character/한도영", "rev": current["rev"]}],
        rationale="P6 parent branch",
        session_id="session/p6-parent",
        host="test",
    )
    parent_result = service.commit(parent["proposal_id"])
    assert parent_result["branch"]["head_revision"] == parent_result["graph_revision"]

    current = _node(service, "character/한도영")
    child = service.propose(
        ops=[
            {
                "verb": "UPDATE",
                "target": "character/한도영",
                "field": "summary",
                "from": current["summary"],
                "to": "child branch",
                "basis_rev": current["rev"],
                "idem_key": "p6-child-branch-0001",
            }
        ],
        read_set=[{"node": "character/한도영", "rev": current["rev"]}],
        rationale="P6 child branch",
        session_id="session/p6-child",
        parent_session_id="session/p6-parent",
        host="test",
    )
    assert child["branch"]["parent"] == "session/p6-parent"

    competing = service.propose(
        ops=[
            {
                "verb": "UPDATE",
                "target": "character/한도영",
                "field": "summary",
                "from": current["summary"],
                "to": "competing branch",
                "basis_rev": current["rev"],
                "idem_key": "p6-competing-branch-0001",
            }
        ],
        read_set=[{"node": "character/한도영", "rev": current["rev"]}],
        rationale="P6 competing branch",
        session_id="session/p6-competing",
        host="test",
    )
    service.commit(child["proposal_id"])
    rejected = service.commit(competing["proposal_id"])
    assert rejected["status"] == "rejected"
    assert rejected["branch"]["status"] == "conflicted"


def test_tier2_worker_uses_original_human_node_and_emits_proposal_only(
    service: StoryService,
) -> None:
    _add_concept(
        service,
        "TierSource",
        value=1,
        session_id="session/p6-human",
    )
    _add_concept(
        service,
        "TierTarget",
        value=1,
        source="concept/TierSource",
        rederive=True,
        summary="original human summary",
        session_id="session/p6-human",
    )
    trigger = _update_value(
        service,
        "concept/TierSource",
        1,
        2,
        "tier-source-2",
        session_id="session/p6-trigger",
    )

    assert trigger["cascade"]["proposals"] == []
    assert len(trigger["cascade"]["jobs"]) == 1
    assert trigger["cascade"]["items"][0]["status"] == "queued"
    provider = FakeProvider(["summary from source value 2", "summary from source value 3"])
    worker = CascadeWorker(
        db_path=service.db_path,
        writer=service.writer,
        provider=provider,
    )
    processed = worker.run_once()

    assert processed["status"] == "proposed"
    assert _node(service, "concept/TierTarget")["summary"] == "original human summary"
    request = provider.requests[0]
    assert request["original_human_node"]["summary"] == "original human summary"
    assert request["changed_sources"][0]["typed"]["props"]["value"] == 2
    proposal_id = processed["proposal_id"]
    proposal = service.query(
        """
        SELECT p.actor_kind, p.model_id, p.status, b.parent
        FROM proposal AS p JOIN session_branch AS b ON b.id = p.session_id
        WHERE p.id = :id
        """,
        params={"id": proposal_id},
    )["rows"][0]
    assert proposal == ["cascade", "fake/rederive-v1", "open", "session/p6-trigger"]

    service.commit(proposal_id)
    assert _node(service, "concept/TierTarget")["summary"] == "summary from source value 2"
    second = _update_value(
        service,
        "concept/TierSource",
        2,
        3,
        "tier-source-3",
        session_id="session/p6-trigger",
    )
    assert second["cascade"]["jobs"]
    worker.run_once()
    assert provider.requests[1]["original_human_node"]["summary"] == ("original human summary")


def test_tier2_requires_human_origin_and_stale_jobs_fail_closed(
    service: StoryService,
) -> None:
    _add_concept(
        service,
        "AgentSource",
        value=1,
        session_id="session/p6-agent",
        actor_kind="agent",
    )
    _add_concept(
        service,
        "AgentTarget",
        value=1,
        source="concept/AgentSource",
        rederive=True,
        summary="agent output",
        session_id="session/p6-agent",
        actor_kind="agent",
    )
    no_human = _update_value(
        service,
        "concept/AgentSource",
        1,
        2,
        "agent-source-2",
        session_id="session/p6-agent-trigger",
    )
    assert no_human["cascade"]["jobs"] == []
    assert no_human["cascade"]["items"][0]["reason"] == "tier2_requires_human_origin"

    _add_concept(
        service,
        "StaleSource",
        value=1,
        session_id="session/p6-stale-human",
    )
    _add_concept(
        service,
        "StaleTarget",
        value=1,
        source="concept/StaleSource",
        rederive=True,
        summary="human baseline",
        session_id="session/p6-stale-human",
    )
    queued = _update_value(
        service,
        "concept/StaleSource",
        1,
        2,
        "stale-source-2",
        session_id="session/p6-stale-trigger",
    )
    _update_value(
        service,
        "concept/StaleSource",
        2,
        3,
        "stale-source-3",
        session_id="session/p6-stale-trigger",
    )
    provider = FakeProvider(["must not be used"])
    result = CascadeWorker(
        db_path=service.db_path,
        writer=service.writer,
        provider=provider,
    ).run_once()
    assert result["job_id"] == queued["cascade"]["jobs"][0]
    assert result["status"] == "failed"
    assert "queue 이후 변경" in result["error"]
    assert provider.requests == []

    target = _node(service, "concept/StaleTarget")
    target_change = service.propose(
        ops=[
            {
                "verb": "UPDATE",
                "target": "concept/StaleTarget",
                "field": "summary",
                "from": target["summary"],
                "to": "new human baseline",
                "basis_rev": target["rev"],
                "idem_key": "p6-stale-target-human-0001",
            }
        ],
        read_set=[{"node": "concept/StaleTarget", "rev": target["rev"]}],
        rationale="P6 target changed after queue",
        session_id="session/p6-stale-human",
        actor_kind="human",
        host="test",
    )
    service.commit(target_change["proposal_id"])
    target_stale = CascadeWorker(
        db_path=service.db_path,
        writer=service.writer,
        provider=provider,
    ).run_once()
    assert target_stale["status"] == "failed"
    assert "target이 queue 이후 변경" in target_stale["error"]
    assert provider.requests == []


def test_tier2_retry_exhaustion_marks_job_and_item_failed(service: StoryService) -> None:
    _add_concept(
        service,
        "RetrySource",
        value=1,
        session_id="session/p6-retry-human",
    )
    _add_concept(
        service,
        "RetryTarget",
        value=1,
        source="concept/RetrySource",
        rederive=True,
        summary="retry baseline",
        session_id="session/p6-retry-human",
    )
    queued = _update_value(
        service,
        "concept/RetrySource",
        1,
        2,
        "retry-source-2",
        session_id="session/p6-retry-trigger",
    )
    job_id = queued["cascade"]["jobs"][0]
    failed = CascadeWorker(
        db_path=service.db_path,
        writer=service.writer,
        provider=FailingProvider(),
        max_attempts=1,
    ).run_once()
    state = service.query(
        """
        SELECT job.status, item.status
        FROM cascade_job AS job
        JOIN cascade_item AS item ON item.run = job.run AND item.node = job.node
        WHERE job.id = :id
        """,
        params={"id": job_id},
    )["rows"][0]
    assert failed["status"] == "failed"
    assert state == ["failed", "blocked"]


def test_tier2_worker_does_not_propose_after_losing_claim(service: StoryService) -> None:
    _add_concept(
        service,
        "ClaimSource",
        value=1,
        session_id="session/p6-claim-human",
    )
    _add_concept(
        service,
        "ClaimTarget",
        value=1,
        source="concept/ClaimSource",
        rederive=True,
        summary="claim baseline",
        session_id="session/p6-claim-human",
    )
    queued = _update_value(
        service,
        "concept/ClaimSource",
        1,
        2,
        "claim-source-2",
        session_id="session/p6-claim-trigger",
    )
    job_id = queued["cascade"]["jobs"][0]
    result = CascadeWorker(
        db_path=service.db_path,
        writer=service.writer,
        provider=ClaimStealingProvider(service.db_path),
    ).run_once()
    rows = service.query(
        "SELECT id FROM proposal WHERE session_id = :session_id",
        params={"session_id": f"tier2:{job_id}"},
    )["rows"]

    assert result == {"status": "lost_lease", "job_id": job_id}
    assert rows == []


def test_tier2_worker_recovers_proposal_created_at_claim_boundary(
    service: StoryService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _add_concept(
        service,
        "BoundarySource",
        value=1,
        session_id="session/p6-boundary-human",
    )
    _add_concept(
        service,
        "BoundaryTarget",
        value=1,
        source="concept/BoundarySource",
        rederive=True,
        summary="boundary baseline",
        session_id="session/p6-boundary-human",
    )
    queued = _update_value(
        service,
        "concept/BoundarySource",
        1,
        2,
        "boundary-source-2",
        session_id="session/p6-boundary-trigger",
    )
    job_id = queued["cascade"]["jobs"][0]
    original_propose = service.writer.propose

    def propose_then_lose_claim(**kwargs: Any) -> dict[str, Any]:
        result = original_propose(**kwargs)
        with connect_write(service.db_path) as connection:
            connection.execute(
                "UPDATE cascade_job SET claim_token = 'new-owner' WHERE id = ?",
                (job_id,),
            )
        return result

    monkeypatch.setattr(service.writer, "propose", propose_then_lose_claim)
    first = CascadeWorker(
        db_path=service.db_path,
        writer=service.writer,
        provider=FakeProvider(["first proposal"]),
    ).run_once()
    monkeypatch.setattr(service.writer, "propose", original_propose)
    with connect_write(service.db_path) as connection:
        connection.execute(
            """
            UPDATE cascade_job
            SET status = 'queued', lease_until = NULL, claim_token = NULL
            WHERE id = ?
            """,
            (job_id,),
        )
    second_provider = FakeProvider(["must not be requested"])
    recovered = CascadeWorker(
        db_path=service.db_path,
        writer=service.writer,
        provider=second_provider,
    ).run_once()
    proposals = service.query(
        "SELECT id FROM proposal WHERE session_id = :session_id ORDER BY id",
        params={"session_id": f"tier2:{job_id}"},
    )["rows"]

    assert first["status"] == "lost_lease"
    assert recovered["status"] == "proposed"
    assert recovered["proposal_id"] == first["orphan_proposal_id"]
    assert recovered["recovered"] is True
    assert second_provider.requests == []
    assert proposals == [[first["orphan_proposal_id"]]]

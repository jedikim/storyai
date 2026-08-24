from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from server.core.database import connect_read_only
from server.core.ontology import Ontology
from server.core.service import StoryService
from server.core.write_service import ProposalError
from server.load_bible import BibleLoader


def update_op(
    target: str,
    field: str,
    value,
    key: str,
    *,
    from_value=...,
) -> dict:
    result = {
        "verb": "UPDATE",
        "target": target,
        "field": field,
        "to": value,
        "idem_key": key,
    }
    if from_value is not ...:
        result["from"] = from_value
    return result


def propose(service: StoryService, ops: list[dict], read_set: list[dict], suffix: str):
    return service.propose(
        ops=ops,
        read_set=read_set,
        rationale=f"P1 test {suffix}",
        session_id=f"session/test-{suffix}",
        host="test",
        model_id="pytest",
    )


def test_empty_read_set_is_rejected(service: StoryService) -> None:
    with pytest.raises(ValidationError):
        propose(
            service,
            [update_op("character/한도영", "summary", "변경", "empty-read-001")],
            [],
            "empty",
        )


def test_propose_does_not_mutate_and_commit_records_provenance(service: StoryService) -> None:
    before = service.get("character/한도영", include="full")[0]
    proposal = propose(
        service,
        [
            update_op(
                "character/한도영",
                "summary",
                "수정된 한 줄 요약.",
                "update-summary-001",
                from_value=before["summary"],
            )
        ],
        [{"node": "character/한도영", "rev": before["rev"]}],
        "update-summary",
    )
    assert proposal["status"] == "open"
    assert proposal["risk"] == "review"
    assert service.get("한도영")[0]["summary"] == before["summary"]

    committed = service.commit(proposal["proposal_id"])
    after = service.get("한도영", include="full")[0]
    assert committed["status"] == "accepted"
    assert after["summary"] == "수정된 한 줄 요약."
    assert after["rev"] == before["rev"] + 1
    assert committed["root_cid"] != ""

    StoryService(
        project_root=service.project_root,
        db_path=service.db_path,
        ontology_path=service.project_root / "spec" / "ontology.json",
        rules_path=service.project_root / "spec" / "rules.json",
        schema_path=service.project_root / "spec" / "schema.sql",
        policy_path=service.project_root / "spec" / "policy.json",
    )

    with connect_read_only(service.db_path) as connection:
        history = connection.execute(
            "SELECT rev, tx_to, proposal FROM node_revision WHERE node = ? ORDER BY rev",
            ("character/한도영",),
        ).fetchall()
        provenance = connection.execute(
            "SELECT field, attributed_to FROM field_provenance WHERE node = ? AND rev = ?",
            ("character/한도영", after["rev"]),
        ).fetchone()
    assert [row["rev"] for row in history] == [1, 2]
    assert history[0]["tx_to"] is not None
    assert history[1]["proposal"] == proposal["proposal_id"]
    assert provenance["field"] == "summary"
    assert provenance["attributed_to"] == "agent:pytest"


def test_propose_retry_and_commit_are_idempotent(service: StoryService) -> None:
    operation = update_op("character/한도영", "summary", "재시도 결과", "retry-key-0001")
    reads = [{"node": "character/한도영", "rev": 1}]
    first = propose(service, [operation], reads, "retry")
    repeated = propose(service, [operation], reads, "retry")
    assert repeated["proposal_id"] == first["proposal_id"]
    assert repeated["idempotent"] is True

    committed = service.commit(first["proposal_id"])
    committed_again = service.commit(first["proposal_id"])
    assert committed_again == committed
    assert service.get("한도영")[0]["rev"] == 2


def test_same_field_conflicts_but_different_field_can_merge(service: StoryService) -> None:
    summary = propose(
        service,
        [update_op("character/한도영", "summary", "첫 변경", "same-field-0001")],
        [{"node": "character/한도영", "rev": 1}],
        "same-a",
    )
    competing = propose(
        service,
        [update_op("character/한도영", "summary", "둘째 변경", "same-field-0002")],
        [{"node": "character/한도영", "rev": 1}],
        "same-b",
    )
    assert competing["pending_overlap"]
    service.commit(summary["proposal_id"])
    rejected = service.commit(competing["proposal_id"])
    assert rejected["status"] == "rejected"
    assert rejected["rejected"][0]["reason"] == "revision_mismatch"
    assert service.commit(competing["proposal_id"]) == rejected

    service2 = service
    current = service2.get("한도영")[0]
    title = propose(
        service2,
        [update_op("character/한도영", "title", "한도영 선장", "different-0001")],
        [{"node": "character/한도영", "rev": current["rev"]}],
        "different-title",
    )
    other = propose(
        service2,
        [update_op("character/한도영", "summary", "독립 필드", "different-0002")],
        [{"node": "character/한도영", "rev": current["rev"]}],
        "different-summary",
    )
    service2.commit(title["proposal_id"])
    merged = service2.commit(other["proposal_id"])
    assert merged["status"] == "accepted"
    node = service2.get("character/한도영")[0]
    assert node["title"] == "한도영 선장"
    assert node["summary"] == "독립 필드"


def test_dry_run_rolls_back_every_change(service: StoryService) -> None:
    proposal = propose(
        service,
        [update_op("object/젖은장갑", "summary", "마른 장갑", "dry-run-key-01")],
        [{"node": "object/젖은장갑", "rev": 1}],
        "dry-run",
    )
    preview = service.commit(proposal["proposal_id"], mode="dry_run")
    assert preview["status"] == "dry_run"
    assert service.get("object/젖은장갑")[0]["rev"] == 1
    applied = service.commit(proposal["proposal_id"])
    assert applied["status"] == "accepted"
    assert service.get("object/젖은장갑")[0]["summary"] == "마른 장갑"


def test_add_session_and_latest_address(service: StoryService) -> None:
    graph = service.writer.graph_revision()
    operation = {
        "verb": "ADD",
        "target": "session/2026-08-25T09-00-00Z",
        "to": {
            "kind": "Session",
            "title": "P1 구현 세션",
            "props": {
                "host": "codex",
                "model": "gpt-5",
                "intent": "P1",
                "did": ["write-path"],
                "open_threads": ["P2"],
                "next": ["diagnostics"],
                "touched": ["server/core"],
            },
        },
        "idem_key": "session-add-0001",
    }
    proposal = propose(
        service,
        [operation],
        [{"node": "book", "rev": graph["revision"]}],
        "session",
    )
    assert proposal["risk"] == "auto"
    service.commit(proposal["proposal_id"])
    latest = service.get("story://session/latest", include="full")[0]
    assert latest["id"] == "session/2026-08-25T09-00-00Z"
    assert latest["props"]["next"] == ["diagnostics"]


@pytest.mark.parametrize(
    "props, missing",
    [
        ({"next": ["diagnostics"]}, "open_threads"),
        ({"open_threads": ["P2"]}, "next"),
        ({"open_threads": [], "next": ["diagnostics"]}, "open_threads"),
        ({"open_threads": ["P2"], "next": [""]}, "next"),
    ],
)
def test_session_requires_nonempty_handoff_fields(
    service: StoryService, props: dict, missing: str
) -> None:
    graph = service.writer.graph_revision()
    with pytest.raises(ValueError, match=missing):
        propose(
            service,
            [
                {
                    "verb": "ADD",
                    "target": f"session/missing-{missing}-{len(props)}",
                    "to": {"kind": "Session", "title": "불완전 세션", "props": props},
                    "idem_key": f"session-required-{missing}-{len(props)}",
                }
            ],
            [{"node": "book", "rev": graph["revision"]}],
            f"session-required-{missing}-{len(props)}",
        )


def test_locked_rule_cannot_be_proposed(service: StoryService) -> None:
    graph = service.writer.graph_revision()
    addition = propose(
        service,
        [
            {
                "verb": "ADD",
                "target": "rule/만조",
                "to": {"kind": "Rule", "title": "만조에는 섬이 고립된다"},
                "idem_key": "locked-add-0001",
            }
        ],
        [{"node": "book", "rev": graph["revision"]}],
        "locked-add",
    )
    service.commit(addition["proposal_id"])
    assert service.get("rule/만조", include="full")[0]["locked"] is True
    with pytest.raises(ProposalError, match="locked"):
        propose(
            service,
            [update_op("rule/만조", "summary", "변경", "locked-edit-001")],
            [{"node": "rule/만조", "rev": 1}],
            "locked-edit",
        )


def test_atomic_add_cannot_bypass_locked_node_policy(service: StoryService) -> None:
    graph = service.writer.graph_revision()
    with pytest.raises(ValueError, match="locked 노드는 변경할 수 없습니다"):
        propose(
            service,
            [
                {
                    "verb": "ADD",
                    "target": "rule/원자잠금",
                    "to": {"kind": "Rule", "title": "원자 잠금 규칙"},
                    "idem_key": "atomic-locked-add",
                },
                update_op("rule/원자잠금", "summary", "같은 제안에서 우회", "atomic-locked-update"),
            ],
            [{"node": "book", "rev": graph["revision"]}],
            "atomic-locked-bypass",
        )
    assert service.find("원자 잠금") == []


def test_invalidate_cannot_mutate_edge_owned_by_locked_rule(service: StoryService) -> None:
    rules = service.project_root / "bible" / "rules"
    rules.mkdir(parents=True)
    (rules / "보존.md").write_text(
        """---
id: rule/보존
title: 보존 규칙
edges:
  - rel: contains
    to: object/젖은장갑
---

# 보존 규칙

이 규칙이 참조하는 대상은 임의로 무효화하지 않는다.
""",
        encoding="utf-8",
    )
    result = BibleLoader(
        project_root=service.project_root,
        bible_root=service.project_root / "bible",
        db_path=service.db_path,
        ontology=Ontology.load(service.project_root / "spec" / "ontology.json"),
    ).load()
    assert result == {"nodes": 5, "edges": 1}
    assert service.get("rule/보존", include="full")[0]["locked"] is True

    with pytest.raises(ValueError, match="locked 노드의 간선"):
        propose(
            service,
            [
                {
                    "verb": "INVALIDATE",
                    "target": "object/젖은장갑",
                    "idem_key": "locked-dependent-invalidate",
                }
            ],
            [{"node": "object/젖은장갑", "rev": 1}],
            "locked-dependent",
        )


def test_policy_file_drives_risk_without_code_changes(service: StoryService) -> None:
    policy_path = service.project_root / "spec" / "policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    rule = next(item for item in policy["rules"] if item["id"] == "existing-value-change")
    rule["risk"] = "always"
    policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    configured = StoryService(
        project_root=service.project_root,
        db_path=service.db_path,
        ontology_path=service.project_root / "spec" / "ontology.json",
        rules_path=service.project_root / "spec" / "rules.json",
        schema_path=service.project_root / "spec" / "schema.sql",
        policy_path=policy_path,
    )

    proposal = propose(
        configured,
        [update_op("character/한도영", "summary", "정책 파일 판정", "policy-driven-001")],
        [{"node": "character/한도영", "rev": 1}],
        "policy-driven",
    )
    assert proposal["risk"] == "always"
    assert "existing-value-change" in proposal["reasons"]


def test_multi_op_add_then_link_is_atomic(service: StoryService) -> None:
    proposal = propose(
        service,
        [
            {
                "verb": "ADD",
                "target": "location/북쪽부두",
                "to": {"kind": "Location", "title": "북쪽 부두"},
                "idem_key": "atomic-add-0001",
            },
            {
                "verb": "LINK",
                "target": "scene/A1.C03.S01",
                "field": "occurs_at",
                "to": "location/북쪽부두",
                "idem_key": "atomic-link-001",
            },
        ],
        [{"node": "scene/A1.C03.S01", "rev": 1}],
        "atomic-success",
    )
    assert proposal["risk"] == "review"
    assert service.find("북쪽 부두") == []
    committed = service.commit(proposal["proposal_id"])
    assert committed["status"] == "accepted"
    assert service.find("북쪽 부두")[0]["id"] == "location/북쪽부두"
    assert service.refs("location/북쪽부두")[0]["rel"] == "occurs_at"


def test_invalid_multi_op_proposal_rolls_back_proposal_and_nodes(service: StoryService) -> None:
    with pytest.raises(ValueError, match="알 수 없는 간선"):
        propose(
            service,
            [
                {
                    "verb": "ADD",
                    "target": "location/사라질장소",
                    "to": {"kind": "Location", "title": "사라질 장소"},
                    "idem_key": "rollback-add-001",
                },
                {
                    "verb": "LINK",
                    "target": "scene/A1.C03.S01",
                    "field": "not_a_relation",
                    "to": "location/사라질장소",
                    "idem_key": "rollback-link-01",
                },
            ],
            [{"node": "scene/A1.C03.S01", "rev": 1}],
            "atomic-failure",
        )
    with connect_read_only(service.db_path) as connection:
        proposal_count = connection.execute(
            "SELECT COUNT(*) FROM proposal WHERE rationale = 'P1 test atomic-failure'"
        ).fetchone()[0]
        node_count = connection.execute(
            "SELECT COUNT(*) FROM node WHERE id = 'location/사라질장소'"
        ).fetchone()[0]
    assert proposal_count == 0
    assert node_count == 0


def test_contradiction_link_is_always_reviewed(service: StoryService) -> None:
    proposal = propose(
        service,
        [
            {
                "verb": "LINK",
                "target": "scene/A1.C03.S01",
                "field": "contradicts",
                "to": "promise/숨은열쇠",
                "idem_key": "contradict-link1",
            }
        ],
        [
            {"node": "scene/A1.C03.S01", "rev": 1},
            {"node": "promise/숨은열쇠", "rev": 1},
        ],
        "contradiction",
    )
    assert proposal["risk"] == "always"
    assert "contradiction-edge" in proposal["reasons"]


def test_link_unlink_and_invalidate_close_transaction_time(service: StoryService) -> None:
    unlink = propose(
        service,
        [
            {
                "verb": "UNLINK",
                "target": "scene/A1.C03.S01",
                "field": "present_at",
                "to": "character/한도영",
                "idem_key": "unlink-edge-001",
            }
        ],
        [
            {"node": "scene/A1.C03.S01", "rev": 1},
            {"node": "character/한도영", "rev": 1},
        ],
        "unlink",
    )
    service.commit(unlink["proposal_id"])
    assert service.refs("한도영") == []

    link = propose(
        service,
        [
            {
                "verb": "LINK",
                "target": "scene/A1.C03.S01",
                "field": "present_at",
                "to": "character/한도영",
                "idem_key": "link-edge-00001",
            }
        ],
        [
            {"node": "scene/A1.C03.S01", "rev": 2},
            {"node": "character/한도영", "rev": 1},
        ],
        "link",
    )
    service.commit(link["proposal_id"])
    assert service.refs("한도영")[0]["rel"] == "present_at"

    invalidate = propose(
        service,
        [
            {
                "verb": "INVALIDATE",
                "target": "object/젖은장갑",
                "idem_key": "invalidate-0001",
            }
        ],
        [{"node": "object/젖은장갑", "rev": 1}],
        "invalidate",
    )
    service.commit(invalidate["proposal_id"])
    with connect_read_only(service.db_path) as connection:
        row = connection.execute(
            "SELECT rev, tx_to FROM node WHERE id = 'object/젖은장갑'"
        ).fetchone()
    assert row["rev"] == 2
    assert row["tx_to"] is not None
    assert all(item["id"] != "object/젖은장갑" for item in service.outline("book"))


def test_commit_lane_serializes_concurrent_writes(service: StoryService) -> None:
    proposals = [
        propose(
            service,
            [update_op("character/한도영", "summary", "병렬 A", "parallel-key-001")],
            [{"node": "character/한도영", "rev": 1}],
            "parallel-a",
        ),
        propose(
            service,
            [update_op("object/젖은장갑", "summary", "병렬 B", "parallel-key-002")],
            [{"node": "object/젖은장갑", "rev": 1}],
            "parallel-b",
        ),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: service.commit(item["proposal_id"]), proposals))
    assert {item["status"] for item in results} == {"accepted"}
    assert service.writer.graph_revision()["revision"] == 3

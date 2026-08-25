from __future__ import annotations

from typing import Any

import pytest

from server.core.service import StoryService


def _node(service: StoryService, node_id: str) -> dict[str, Any]:
    return service.get(node_id, include="full")[0]


def _add_concept(
    service: StoryService,
    name: str,
    value: int,
    *,
    source: str | None = None,
    locked: bool = False,
    extra_reads: list[str] | None = None,
) -> dict[str, Any]:
    node_id = f"concept/{name}"
    props: dict[str, Any] = {"value": value}
    if source is not None:
        props["_derive"] = [
            {
                "source": source,
                "source_field": "props.value",
                "target_field": "props.value",
                "transform": "copy",
            }
        ]
        read_set = [{"node": source, "rev": _node(service, source)["rev"]}]
        for extra in extra_reads or []:
            read_set.append({"node": extra, "rev": _node(service, extra)["rev"]})
    else:
        read_set = [{"node": "book", "rev": service.writer.graph_revision()["revision"]}]
    proposal = service.propose(
        ops=[
            {
                "verb": "ADD",
                "target": node_id,
                "to": {
                    "kind": "Concept",
                    "title": name,
                    "props": props,
                    "locked": locked,
                },
                "idem_key": f"p5-add-{name}-0001",
            }
        ],
        read_set=read_set,
        rationale=f"P5 add {name}",
        session_id="session/test-p5",
        host="test",
    )
    return service.commit(proposal["proposal_id"])


def _update(
    service: StoryService,
    node_id: str,
    field: str,
    old: Any,
    new: Any,
    suffix: str,
    *,
    extra_reads: list[str] | None = None,
    mode: str = "apply",
) -> dict[str, Any]:
    reads = [{"node": node_id, "rev": _node(service, node_id)["rev"]}]
    for extra in extra_reads or []:
        reads.append({"node": extra, "rev": _node(service, extra)["rev"]})
    proposal = service.propose(
        ops=[
            {
                "verb": "UPDATE",
                "target": node_id,
                "field": field,
                "from": old,
                "to": new,
                "basis_rev": reads[0]["rev"],
                "idem_key": f"p5-update-{suffix}-0001",
            }
        ],
        read_set=reads,
        rationale=f"P5 update {suffix}",
        session_id="session/test-p5",
        host="test",
    )
    return service.commit(proposal["proposal_id"], mode=mode)  # type: ignore[arg-type]


def _chain(service: StoryService, names: list[str]) -> None:
    _add_concept(service, names[0], 1)
    for name, source_name in zip(names[1:], names, strict=False):
        _add_concept(service, name, 1, source=f"concept/{source_name}")


def test_transitive_dirty_order_and_proposal_gates(service: StoryService) -> None:
    _chain(service, ["A", "B", "C"])

    result = _update(service, "concept/A", "props.value", 1, 2, "a-value")

    assert result["cascade"]["status"] == "done"
    assert [(item["node"], item["depth"]) for item in result["cascade"]["items"]] == [
        ("concept/B", 1),
        ("concept/C", 2),
    ]
    assert len(result["cascade"]["proposals"]) == 1
    assert _node(service, "concept/B")["props"]["value"] == 1
    assert _node(service, "concept/C")["props"]["value"] == 1

    b_proposal = result["cascade"]["proposals"][0]
    candidate = service.query(
        """
        SELECT p.actor_kind, p.status, pa.on_behalf_of
        FROM proposal AS p JOIN proposal_actor AS pa ON pa.proposal = p.id
        WHERE p.id = :id
        """,
        params={"id": b_proposal},
    )["rows"][0]
    assert candidate == ["cascade", "open", None]

    b_result = service.commit(b_proposal)
    assert _node(service, "concept/B")["props"]["value"] == 2
    assert b_result["cascade"]["iteration"] == 2
    assert len(b_result["cascade"]["proposals"]) == 1
    c_proposal = b_result["cascade"]["proposals"][0]
    service.commit(c_proposal)
    assert _node(service, "concept/C")["props"]["value"] == 2


def test_prose_only_change_hits_typed_early_cutoff(service: StoryService) -> None:
    _chain(service, ["A", "B"])

    result = _update(service, "concept/A", "summary", None, "prose only", "a-summary")

    assert result["cascade"]["status"] == "done"
    assert result["cascade"]["cutoff_hits"] == 1
    assert result["cascade"]["proposals"] == []
    assert result["cascade"]["items"][0]["reason"].startswith(
        "typed_projection_unchanged"
    )


def test_items_keep_topological_order_when_shortest_depth_ties(
    service: StoryService,
) -> None:
    _add_concept(service, "Source", 1)
    _add_concept(service, "ZParent", 1, source="concept/Source")
    _add_concept(
        service,
        "AChild",
        1,
        source="concept/ZParent",
        extra_reads=["concept/Source"],
    )

    result = _update(service, "concept/Source", "props.value", 1, 2, "topological")

    assert [item["node"] for item in result["cascade"]["items"]] == [
        "concept/ZParent",
        "concept/AChild",
    ]


def test_cycle_rejects_by_default_and_opt_in_is_bounded(service: StoryService) -> None:
    _chain(service, ["A", "B"])
    _update(
        service,
        "concept/A",
        "summary",
        None,
        "dependency on B",
        "a-reads-b",
        extra_reads=["concept/B"],
    )

    preview = _update(
        service,
        "concept/A",
        "props.value",
        1,
        2,
        "a-cycle-value",
        mode="dry_run",
    )

    assert preview["cascade"]["status"] == "cycle"
    assert preview["cascade"]["proposals"] == []
    assert {item["node"] for item in preview["cascade"]["items"]} == {
        "concept/A",
        "concept/B",
    }
    with pytest.raises(ValueError, match="1..3"):
        service.commit(
            preview["proposal_id"],
            allow_cycles=True,
            max_iterations=4,
        )
    result = service.commit(
        preview["proposal_id"],
        allow_cycles=True,
        max_iterations=3,
    )
    assert result["cascade"]["status"] == "done"
    assert result["cascade"]["nodes_visited"] <= service.writer.cascade.max_nodes


def test_depth_budget_fails_closed(service: StoryService) -> None:
    _chain(service, ["A", "B", "C", "D", "E"])

    result = _update(service, "concept/A", "props.value", 1, 2, "depth-budget")

    assert result["cascade"]["status"] == "budget_exceeded"
    assert result["cascade"]["proposals"] == []
    assert all(item["status"] == "blocked" for item in result["cascade"]["items"])


def test_locked_derived_target_is_skipped(service: StoryService) -> None:
    _add_concept(service, "A", 1)
    _add_concept(service, "Locked", 1, source="concept/A", locked=True)

    result = _update(service, "concept/A", "props.value", 1, 2, "locked")

    assert result["cascade"]["proposals"] == []
    assert result["cascade"]["items"][0]["status"] == "locked"
    assert _node(service, "concept/Locked")["props"]["value"] == 1


def test_dry_run_rolls_back_cascade_and_commit_retry_is_idempotent(
    service: StoryService,
) -> None:
    _chain(service, ["A", "B"])
    before = service.query("SELECT count(*) AS n FROM cascade_run")["rows"][0][0]

    dry_run = _update(
        service,
        "concept/A",
        "props.value",
        1,
        2,
        "dry-run",
        mode="dry_run",
    )

    assert dry_run["status"] == "dry_run"
    assert dry_run["cascade"]["proposals"]
    assert service.query("SELECT count(*) AS n FROM cascade_run")["rows"][0][0] == before
    assert _node(service, "concept/A")["props"]["value"] == 1
    assert service.query(
        "SELECT count(*) AS n FROM proposal WHERE id = :id",
        params={"id": dry_run["cascade"]["proposals"][0]},
    )["rows"][0][0] == 0

    applied = _update(service, "concept/A", "props.value", 1, 2, "apply-once")
    count = service.query("SELECT count(*) AS n FROM cascade_run")["rows"][0][0]
    retried = service.commit(applied["proposal_id"])
    assert retried == applied
    assert service.query("SELECT count(*) AS n FROM cascade_run")["rows"][0][0] == count


def test_proposal_read_index_is_populated(service: StoryService) -> None:
    _chain(service, ["A", "B"])
    indexed = service.query(
        """
        SELECT pr.node, pr.rev
        FROM proposal_read AS pr
        JOIN op AS o ON o.proposal = pr.proposal
        WHERE o.target = 'concept/B'
        """
    )
    assert indexed["rows"] == [["concept/A", 1]]

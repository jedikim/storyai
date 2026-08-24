from __future__ import annotations

import json
from itertools import count

import pytest

from server.core.database import connect_read_only
from server.core.diagnostics import DiagnosticEngine, DiagnosticSpecError
from server.core.ontology import Ontology
from server.core.service import StoryService
from server.load_bible import BibleLoader

_KEYS = count(1)


def key(label: str) -> str:
    return f"p2-{label}-{next(_KEYS):04d}"


def add(target: str, kind: str, title: str, **fields) -> dict:
    payload = {"kind": kind, "title": title, **fields}
    return {
        "verb": "ADD",
        "target": target,
        "to": payload,
        "idem_key": key("add"),
    }


def link(source: str, relation: str, target: str) -> dict:
    return {
        "verb": "LINK",
        "target": source,
        "field": relation,
        "to": target,
        "idem_key": key("link"),
    }


def update(target: str, field: str, value) -> dict:
    return {
        "verb": "UPDATE",
        "target": target,
        "field": field,
        "to": value,
        "idem_key": key("update"),
    }


def submit(
    service: StoryService,
    operations: list[dict],
    *,
    reads: list[dict] | None = None,
    label: str,
) -> dict:
    if reads is None:
        reads = [{"node": "book", "rev": service.writer.graph_revision()["revision"]}]
    proposal = service.propose(
        ops=operations,
        read_set=reads,
        rationale=f"P2 test {label}",
        session_id=f"session/p2-{label}",
        host="test",
        model_id="pytest",
    )
    assert proposal["status"] == "open"
    committed = service.commit(proposal["proposal_id"])
    assert committed["status"] == "accepted"
    return committed


def test_promise_state_machine_and_metrics(service: StoryService) -> None:
    with pytest.raises(ValueError, match="T가 정의"):
        service.propose(
            ops=[update("promise/숨은열쇠", "props.status", "eligible")],
            read_set=[{"node": "promise/숨은열쇠", "rev": 1}],
            rationale="missing trigger",
            session_id="session/p2-missing-trigger",
            host="test",
        )

    submit(
        service,
        [
            link("scene/A1.C03.S01", "plants", "promise/숨은열쇠"),
            link("promise/숨은열쇠", "requires_trigger", "scene/A1.C03.S01"),
            update("promise/숨은열쇠", "props.status", "eligible"),
        ],
        reads=[
            {"node": "scene/A1.C03.S01", "rev": 1},
            {"node": "promise/숨은열쇠", "rev": 1},
        ],
        label="promise-eligible",
    )
    eligible = service.promises(status=["eligible"])
    assert len(eligible) == 1
    assert eligible[0]["F"] == ["scene/A1.C03.S01"]
    assert eligible[0]["T"] == ["scene/A1.C03.S01"]
    assert eligible[0]["P"] == []
    assert eligible[0]["delta_coh"] == 1.0

    with pytest.raises(ValueError, match="허용되지 않는 Promise 상태 전이"):
        service.propose(
            ops=[update("promise/숨은열쇠", "props.status", "hypothetical")],
            read_set=[{"node": "promise/숨은열쇠", "rev": 3}],
            rationale="backward state",
            session_id="session/p2-backward",
            host="test",
        )
    with pytest.raises(ValueError, match="P가 배치"):
        service.propose(
            ops=[update("promise/숨은열쇠", "props.status", "actualized")],
            read_set=[{"node": "promise/숨은열쇠", "rev": 3}],
            rationale="missing payoff",
            session_id="session/p2-missing-payoff",
            host="test",
        )

    submit(
        service,
        [
            link("scene/A1.C03.S01", "pays_off", "promise/숨은열쇠"),
            update("promise/숨은열쇠", "props.status", "actualized"),
        ],
        reads=[
            {"node": "scene/A1.C03.S01", "rev": 2},
            {"node": "promise/숨은열쇠", "rev": 3},
        ],
        label="promise-actualized",
    )
    actualized = service.promises(status=["actualized"])[0]
    assert actualized["P"] == ["scene/A1.C03.S01"]


def test_visibility_predicates_and_detailed_get(service: StoryService) -> None:
    submit(
        service,
        [
            add(
                "fact/등대점등",
                "Fact",
                "등대 점등 사실",
                props={
                    "subject": "object/젖은장갑",
                    "predicate": "signal_state",
                    "object": "lit",
                },
                visible_to=[
                    {"viewer": "character/한도영", "learned_at": 3, "pathway": "observed"},
                    {"viewer": "reader", "learned_at": 5, "pathway": "direct"},
                ],
            )
        ],
        label="visibility",
    )
    at_four = service.visibility("fact/등대점등", character="character/한도영", as_of=4)
    assert at_four["mystery"] is True
    assert at_four["twist"] is True
    assert at_four["dramatic_irony"] is False
    assert at_four["continuity_bug"] is False

    reader_only = service.visibility(
        "fact/등대점등",
        character="character/한도영",
        as_of=6,
        spoken=True,
    )
    assert reader_only["visible_to"] == ["character/한도영", "reader"]
    assert reader_only["continuity_bug"] is False
    before = service.get("fact/등대점등", include="full")[0]
    assert before["visible_to"] == ["character/한도영", "reader"]
    assert before["visibility"][0]["learned_at"] == 3

    proposal = service.propose(
        ops=[
            update(
                "fact/등대점등",
                "visible_to",
                [{"viewer": "reader", "learned_at": 0, "pathway": "direct"}],
            )
        ],
        read_set=[{"node": "fact/등대점등", "rev": 1}],
        rationale="visibility transition",
        session_id="session/p2-visibility-transition",
        host="test",
    )
    assert proposal["risk"] == "review"
    service.commit(proposal["proposal_id"])
    irony = service.visibility(
        "fact/등대점등",
        character="character/한도영",
        as_of=4,
        spoken=True,
    )
    assert irony["dramatic_irony"] is True
    assert irony["continuity_bug"] is True
    assert irony["mystery"] is False
    after = service.get("fact/등대점등", include="full")[0]
    assert after["cid"] != before["cid"]
    assert after["visible_to"] == ["reader"]


def test_scene_contract_feasible_and_forbid(service: StoryService) -> None:
    pre = {
        "subject": "character/한도영",
        "field": "props.주손",
        "op": "eq",
        "value": "왼손",
    }
    forbidden = {
        "subject": "character/한도영",
        "field": "props.상태",
        "op": "eq",
        "value": "dead",
    }
    submit(
        service,
        [
            add(
                "scene/계약검사",
                "Scene",
                "계약 검사",
                story_from=4,
                props={"pre": [pre], "post": [], "forbid": [forbidden]},
            )
        ],
        label="contract",
    )
    result = service.feasible("scene/계약검사")
    assert result["feasible"] is True
    assert result["failed_pre"] == []
    assert result["active_forbid"] == []

    submit(
        service,
        [
            add(
                "scene/불가능",
                "Scene",
                "불가능한 씬",
                story_from=4,
                props={"pre": [{**pre, "value": "오른손"}], "post": [], "forbid": []},
            )
        ],
        label="contract-failed",
    )
    failed = service.feasible("scene/불가능")
    assert failed["feasible"] is False
    assert failed["failed_pre"][0]["value"] == "오른손"


def test_completion_gate_five_injected_contradictions(service: StoryService) -> None:
    operations = [
        add(
            "fact/주손왼쪽",
            "Fact",
            "왼손 사실",
            story_from=1,
            story_to=6,
            props={
                "subject": "character/한도영",
                "predicate": "handedness",
                "object": "left",
            },
        ),
        add(
            "fact/주손오른쪽",
            "Fact",
            "오른손 사실",
            story_from=3,
            story_to=8,
            props={
                "subject": "character/한도영",
                "predicate": "handedness",
                "object": "right",
            },
        ),
        add(
            "fact/도영사망",
            "Fact",
            "도영 사망",
            story_from=2,
            props={
                "subject": "character/한도영",
                "predicate": "life_state",
                "object": "dead",
            },
            visible_to=[{"viewer": "reader", "learned_at": 2, "pathway": "direct"}],
        ),
        add(
            "fact/비밀항로",
            "Fact",
            "비밀 항로",
            props={
                "subject": "object/젖은장갑",
                "predicate": "route",
                "object": "north",
            },
            visible_to=[{"viewer": "reader", "learned_at": 1, "pathway": "direct"}],
        ),
        add(
            "scene/사후등장",
            "Scene",
            "사망 후 등장",
            story_from=4,
            props={"claims": [{"speaker": "character/한도영", "fact": "fact/비밀항로"}]},
        ),
        add(
            "promise/방치",
            "Promise",
            "방치된 복선",
            story_from=1,
            props={"status": "hypothetical", "foreshadow_type": "object"},
        ),
        add(
            "promise/무근거회수",
            "Promise",
            "무근거 회수",
            story_from=1,
            props={"status": "hypothetical", "foreshadow_type": "event"},
        ),
        link("scene/사후등장", "present_at", "character/한도영"),
        link("scene/사후등장", "pays_off", "promise/무근거회수"),
    ]
    committed = submit(
        service,
        operations,
        reads=[{"node": "character/한도영", "rev": 1}],
        label="five-contradictions",
    )
    selected = [
        "detail.appearance",
        "character.knowledge",
        "plot.abandoned",
        "promise.unearned",
    ]
    diagnostics = service.check("book", rules=selected, severity="error")
    by_rule: dict[str, list[dict]] = {}
    for item in diagnostics:
        by_rule.setdefault(item["rule"], []).append(item)
    appearance_messages = [item["message"] for item in by_rule["detail.appearance"]]
    assert any("값이 불일치" in message for message in appearance_messages)
    assert any("dead 상태" in message for message in appearance_messages)
    assert any("fact/비밀항로" in item["nodes"] for item in by_rule["character.knowledge"])
    assert any("promise/방치" in item["nodes"] for item in by_rule["plot.abandoned"])
    assert any("promise/무근거회수" in item["nodes"] for item in by_rule["promise.unearned"])
    assert committed["diagnostics"]

    with connect_read_only(service.db_path) as connection:
        persisted = connection.execute(
            "SELECT COUNT(*) FROM diagnostic WHERE resolved_at IS NULL"
        ).fetchone()[0]
    assert persisted >= len(diagnostics)


def test_world_rule_and_scoped_check(service: StoryService) -> None:
    condition = {
        "subject": "object/젖은장갑",
        "field": "props.상태",
        "op": "eq",
        "value": "burned",
    }
    submit(
        service,
        [
            add(
                "rule/불연성",
                "Rule",
                "장갑 불연성",
                props={"forbid": [condition]},
            ),
            add(
                "scene/규칙위반",
                "Scene",
                "규칙 위반",
                story_from=5,
                props={"pre": [], "post": [condition], "forbid": []},
            ),
        ],
        label="world-rule",
    )
    diagnostics = service.check("scene/규칙위반", rules=["world.core_rule"])
    assert len(diagnostics) == 1
    assert diagnostics[0]["nodes"] == ["scene/규칙위반", "rule/불연성"]


def test_dag_and_focalizer_invariants_reject_atomic_write(service: StoryService) -> None:
    with pytest.raises(ValueError, match="순환"):
        service.propose(
            ops=[
                add("event/원인A", "Event", "원인 A"),
                add("event/원인B", "Event", "원인 B"),
                link("event/원인A", "causes", "event/원인B"),
                link("event/원인B", "causes", "event/원인A"),
            ],
            read_set=[{"node": "book", "rev": service.writer.graph_revision()["revision"]}],
            rationale="cycle",
            session_id="session/p2-cycle",
            host="test",
        )
    assert service.find("원인 A") == []

    with pytest.raises(ValueError, match="대상당 최대 1개"):
        service.propose(
            ops=[
                add("character/두번째", "Character", "두 번째 인물"),
                link("character/한도영", "focalizes", "scene/A1.C03.S01"),
                link("character/두번째", "focalizes", "scene/A1.C03.S01"),
            ],
            read_set=[
                {"node": "character/한도영", "rev": 1},
                {"node": "scene/A1.C03.S01", "rev": 1},
            ],
            rationale="two focalizers",
            session_id="session/p2-focalizer",
            host="test",
        )


def test_bible_loader_rejects_causal_cycle(service: StoryService) -> None:
    events = service.project_root / "bible" / "events"
    events.mkdir(parents=True)
    (events / "A.md").write_text(
        """---
id: event/로더A
title: 로더 A
edges:
  - rel: causes
    to: event/로더B
---

# 로더 A
""",
        encoding="utf-8",
    )
    (events / "B.md").write_text(
        """---
id: event/로더B
title: 로더 B
edges:
  - rel: causes
    to: event/로더A
---

# 로더 B
""",
        encoding="utf-8",
    )
    loader = BibleLoader(
        project_root=service.project_root,
        bible_root=service.project_root / "bible",
        db_path=service.db_path,
        ontology=Ontology.load(service.project_root / "spec" / "ontology.json"),
    )
    with pytest.raises(ValueError, match="순환"):
        loader.load()
    assert service.find("로더 A") == []


def test_reachability_warns_for_orphan_event(service: StoryService) -> None:
    committed = submit(
        service,
        [
            add("event/시작", "Event", "시작", props={"is_start": True}),
            add("event/끝", "Event", "끝", props={"is_end": True}),
            add("event/고립", "Event", "고립"),
            link("event/시작", "causes", "event/끝"),
        ],
        label="reachability",
    )
    warnings = service.check("book", rules=["graph.unreachable"], severity="warn")
    assert "event/고립" in [item["nodes"][0] for item in warnings]
    assert any(item["rule"] == "graph.unreachable" for item in committed["diagnostics"])


def test_all_p2_sql_rules_execute_without_llm(service: StoryService) -> None:
    diagnostics = service.check("book")
    assert all(item["rule"] in service.diagnostics.statements for item in diagnostics)
    assert len(service.diagnostics.statements) == 14


def test_spec_sql_is_authorized_read_only(service: StoryService) -> None:
    rules_path = service.project_root / "spec" / "rules.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    rules["p2_sql"]["timeline.absolute"] = (
        "WITH RECURSIVE one(value) AS (SELECT 1) DELETE FROM node WHERE id='character/한도영'"
    )
    rules_path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    engine = DiagnosticEngine(service.db_path, rules_path)
    with pytest.raises(DiagnosticSpecError, match="SQL 실행 실패"):
        engine.check(scope=None, rule_ids=["timeline.absolute"], severity=None)
    assert service.get("character/한도영")[0]["title"] == "한도영"

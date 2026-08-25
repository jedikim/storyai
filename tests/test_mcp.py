from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from server.app import TOOL_DESCRIPTIONS, create_server
from server.runtime import reset_service


def payload(result):
    if result.data is not None:
        return result.data
    return json.loads(result.content[0].text)


def write_ingest_fixture(root: Path) -> None:
    text = "한도영은 북쪽 부두에 섰다.\n"
    raw = text.encode("utf-8")
    location_quote = "북쪽 부두"
    location_start = raw.index(location_quote.encode("utf-8"))
    chapter = root / "manuscript" / "A1" / "ch09.md"
    chapter.parent.mkdir(parents=True, exist_ok=True)
    chapter.write_text(text, encoding="utf-8")
    manifest = {
        "version": 1,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "nodes": [
            {
                "id": "location/북쪽부두",
                "kind": "Location",
                "title": "북쪽 부두",
                "evidence": [
                    {
                        "start": location_start,
                        "end": location_start + len(location_quote.encode("utf-8")),
                        "quote": location_quote,
                    }
                ],
            },
            {
                "id": "scene/A1.C09.S01",
                "kind": "Scene",
                "title": "북쪽 부두",
                "props": {
                    "story_time": 9,
                    "location": "location/북쪽부두",
                    "characters": ["character/한도영"],
                },
                "evidence": [{"start": 0, "end": len(raw), "quote": text}],
            },
        ],
        "edges": [
            {
                "src": "scene/A1.C09.S01",
                "rel": "occurs_at",
                "dst": "location/북쪽부두",
            }
        ],
        "unresolved": [],
    }
    chapter.with_suffix(".story.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_mcp_lists_tools_in_deterministic_order(
    service, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORYAI_DB", str(service.db_path))
    monkeypatch.setenv("STORYAI_PROJECT_ROOT", str(service.project_root))
    write_ingest_fixture(service.project_root)
    reset_service()
    server = create_server()
    async with Client(server) as client:
        tools = await client.list_tools()
        checked = await client.call_tool("check", {"scope": "book", "severity": "error"})
        schema = await client.call_tool("graph_schema", {"section": "kinds"})
        outline = await client.call_tool("outline", {"scope": "book"})
        found = await client.call_tool("find", {"q": "도영"})
        traced = await client.call_tool(
            "trace",
            {"from": "scene/A1.C03.S01", "to": "object/젖은장갑"},
        )
        context = await client.call_tool(
            "neighborhood",
            {"intent": "젖은 장갑", "anchors": ["character/한도영"]},
        )
        preview = await client.call_tool(
            "impact",
            {"ref": "object/젖은장갑", "change": {"field": "reveal_at", "to": 9}},
        )
        queried = await client.call_tool(
            "query",
            {
                "sql": "SELECT id FROM live_node WHERE kind=:kind ORDER BY id",
                "params": {"kind": "Character"},
            },
        )
        ingested = await client.call_tool("ingest", {"chapter": "A1/ch09.md", "mode": "extract"})
        node = await client.call_tool("get", {"ref": "character/한도영"})
        references = await client.call_tool("refs", {"ref": "character/한도영"})
        promise_board = await client.call_tool(
            "promises", {"status": ["hypothetical"], "sort": "debt"}
        )
        proposed = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "UPDATE",
                        "target": "character/한도영",
                        "field": "summary",
                        "to": "MCP 제안 경로",
                        "idem_key": "mcp-propose-0001",
                    }
                ],
                "read_set": [{"node": "character/한도영", "rev": 1}],
                "rationale": "MCP P1 integration",
                "session_id": "session/mcp-test",
                "host": "test",
            },
        )
        committed = await client.call_tool(
            "commit", {"proposal_id": payload(proposed)["proposal_id"]}
        )
    assert [tool.name for tool in tools] == sorted(TOOL_DESCRIPTIONS)
    assert checked.is_error is False
    assert any(item["rule"] == "plot.abandoned" for item in payload(checked))
    assert schema.is_error is False and schema.data["kinds"]
    assert outline.is_error is False and len(payload(outline)) == 4
    assert found.is_error is False and payload(found)[0]["id"] == "character/한도영"
    assert traced.is_error is False and payload(traced)[0]["depth"] == 1
    assert context.is_error is False and payload(context)["packet"]
    assert preview.is_error is False and payload(preview)["affected"]
    assert queried.is_error is False and payload(queried)["rows"] == [["character/한도영"]]
    assert ingested.is_error is False and payload(ingested)["status"] == "open"
    assert node.is_error is False and payload(node)[0]["title"] == "한도영"
    assert references.is_error is False and payload(references)[0]["rel"] == "present_at"
    assert promise_board.is_error is False
    assert payload(promise_board)[0]["id"] == "promise/숨은열쇠"
    assert proposed.is_error is False and payload(proposed)["status"] == "open"
    assert committed.is_error is False and payload(committed)["status"] == "accepted"
    by_name = {tool.name: tool for tool in tools}
    assert by_name["propose"].annotations.readOnlyHint is False
    assert by_name["commit"].annotations.destructiveHint is True
    assert by_name["check"].annotations.readOnlyHint is True
    assert by_name["promises"].annotations.readOnlyHint is True
    assert by_name["ingest"].annotations.readOnlyHint is False
    assert by_name["trace"].annotations.readOnlyHint is True


@pytest.mark.asyncio
async def test_stdio_process_connection(service) -> None:
    root = Path(__file__).resolve().parents[1]
    write_ingest_fixture(service.project_root)
    transport = StdioTransport(
        command="bash",
        args=[str(root / "server" / "run-mcp.sh")],
        env={
            "STORYAI_DB": str(service.db_path),
            "STORYAI_PROJECT_ROOT": str(service.project_root),
        },
        cwd=str(root),
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        checked = await client.call_tool("check", {"scope": "book"})
        result = await client.call_tool("find", {"q": "도영"})
        semantic = await client.call_tool("find", {"q": "도영", "mode": "semantic"})
        traced = await client.call_tool(
            "trace", {"from": "scene/A1.C03.S01", "to": "object/젖은장갑"}
        )
        context = await client.call_tool(
            "neighborhood", {"intent": "장갑", "anchors": ["character/한도영"]}
        )
        preview = await client.call_tool(
            "impact",
            {"ref": "object/젖은장갑", "change": {"field": "summary", "to": "변경"}},
        )
        queried = await client.call_tool("query", {"sql": "SELECT COUNT(*) AS n FROM live_node"})
        ingested = await client.call_tool("ingest", {"chapter": "A1/ch09.md"})
        promise_board = await client.call_tool("promises", {"status": ["hypothetical"]})
        proposed = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "ADD",
                        "target": "session/2026-08-25T10-00-00Z",
                        "to": {
                            "kind": "Session",
                            "title": "stdio 직접 테스트",
                            "props": {"open_threads": ["P2"], "next": ["진단 구현"]},
                        },
                        "idem_key": "stdio-session-001",
                    }
                ],
                "read_set": [{"node": "book", "rev": service.writer.graph_revision()["revision"]}],
                "rationale": "actual stdio mutation",
                "session_id": "session/stdio-test",
                "host": "test",
            },
        )
        committed = await client.call_tool(
            "commit", {"proposal_id": payload(proposed)["proposal_id"]}
        )
        latest = await client.call_tool(
            "get", {"ref": "story://session/latest", "include": "brief"}
        )
    assert [tool.name for tool in tools] == sorted(TOOL_DESCRIPTIONS)
    assert result.is_error is False
    assert semantic.is_error is False and payload(semantic)[0]["id"] == "character/한도영"
    assert traced.is_error is False and payload(traced)[0]["depth"] == 1
    assert context.is_error is False and payload(context)["packet"]
    assert preview.is_error is False and payload(preview)["affected"]
    assert queried.is_error is False and payload(queried)["rows"] == [[4]]
    assert ingested.is_error is False and payload(ingested)["status"] == "open"
    assert checked.is_error is False
    assert any(item["rule"] == "plot.abandoned" for item in payload(checked))
    assert promise_board.is_error is False
    assert payload(promise_board)[0]["status"] == "hypothetical"
    assert payload(result)[0]["id"] == "character/한도영"
    assert payload(committed)["status"] == "accepted"
    assert payload(latest)[0]["title"] == "stdio 직접 테스트"


def test_tool_descriptions_fit_two_kilobyte_budget() -> None:
    assert all(len(value.encode("utf-8")) < 2048 for value in TOOL_DESCRIPTIONS.values())

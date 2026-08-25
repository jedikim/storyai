from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


class RederiveHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    idempotency_keys: list[str | None] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        self.requests.append(json.loads(self.rfile.read(length)))
        self.idempotency_keys.append(self.headers.get("Idempotency-Key"))
        body = json.dumps({"value": "MCP Tier-2 summary", "model_id": "test/mcp-rederive"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


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


@pytest.mark.asyncio
async def test_stdio_process_runs_p5_cascade_through_real_mcp(service) -> None:
    root = Path(__file__).resolve().parents[1]
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
        add_a = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "ADD",
                        "target": "concept/McpA",
                        "to": {
                            "kind": "Concept",
                            "title": "MCP source",
                            "props": {"value": 1},
                        },
                        "idem_key": "stdio-p5-add-a-0001",
                    }
                ],
                "read_set": [{"node": "book", "rev": service.writer.graph_revision()["revision"]}],
                "rationale": "actual stdio P5 source",
                "session_id": "session/stdio-p5",
                "host": "test",
            },
        )
        await client.call_tool("commit", {"proposal_id": payload(add_a)["proposal_id"]})
        source = payload(await client.call_tool("get", {"ref": "concept/McpA", "include": "full"}))[
            0
        ]
        add_b = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "ADD",
                        "target": "concept/McpB",
                        "to": {
                            "kind": "Concept",
                            "title": "MCP derived",
                            "props": {
                                "value": 1,
                                "_derive": [
                                    {
                                        "source": "concept/McpA",
                                        "source_field": "props.value",
                                        "target_field": "props.value",
                                        "transform": "copy",
                                    }
                                ],
                            },
                        },
                        "idem_key": "stdio-p5-add-b-0001",
                    }
                ],
                "read_set": [{"node": "concept/McpA", "rev": source["rev"]}],
                "rationale": "actual stdio P5 derived node",
                "session_id": "session/stdio-p5",
                "host": "test",
            },
        )
        await client.call_tool("commit", {"proposal_id": payload(add_b)["proposal_id"]})
        source = payload(await client.call_tool("get", {"ref": "concept/McpA", "include": "full"}))[
            0
        ]
        update_a = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "UPDATE",
                        "target": "concept/McpA",
                        "field": "props.value",
                        "from": 1,
                        "to": 2,
                        "basis_rev": source["rev"],
                        "idem_key": "stdio-p5-update-a-0001",
                    }
                ],
                "read_set": [{"node": "concept/McpA", "rev": source["rev"]}],
                "rationale": "actual stdio P5 trigger",
                "session_id": "session/stdio-p5",
                "host": "test",
            },
        )
        cascade = await client.call_tool(
            "commit", {"proposal_id": payload(update_a)["proposal_id"]}
        )
        cascade_payload = payload(cascade)
        cascade_proposal = cascade_payload["cascade"]["proposals"][0]
        candidate = await client.call_tool(
            "query",
            {
                "sql": "SELECT actor_kind, status FROM proposal WHERE id=:id",
                "params": {"id": cascade_proposal},
            },
        )
        before = payload(await client.call_tool("get", {"ref": "concept/McpB", "include": "full"}))[
            0
        ]
        applied = await client.call_tool("commit", {"proposal_id": cascade_proposal})
        after = payload(await client.call_tool("get", {"ref": "concept/McpB", "include": "full"}))[
            0
        ]

    assert cascade.is_error is False
    assert cascade_payload["cascade"]["status"] == "done"
    assert payload(candidate)["rows"] == [["cascade", "open"]]
    assert before["props"]["value"] == 1
    assert payload(applied)["status"] == "accepted"
    assert after["props"]["value"] == 2


@pytest.mark.asyncio
async def test_stdio_p6_lease_branch_and_tier2_worker_end_to_end(service) -> None:
    root = Path(__file__).resolve().parents[1]

    def transport() -> StdioTransport:
        return StdioTransport(
            command="bash",
            args=[str(root / "server" / "run-mcp.sh")],
            env={
                "STORYAI_DB": str(service.db_path),
                "STORYAI_PROJECT_ROOT": str(service.project_root),
            },
            cwd=str(root),
        )

    async with Client(transport()) as client:
        tools = await client.list_tools()
        acquired = await client.call_tool(
            "lease",
            {
                "mode": "acquire",
                "scope": "scene/A6.C01.*",
                "session_id": "session/mcp-p6-a",
                "ttl_sec": 900,
            },
        )
        conflict = await client.call_tool(
            "lease",
            {
                "mode": "acquire",
                "scope": "scene/A6.C01.S01",
                "session_id": "session/mcp-p6-b",
            },
        )
        released = await client.call_tool(
            "lease",
            {
                "mode": "release",
                "scope": "scene/A6.C01.*",
                "session_id": "session/mcp-p6-a",
            },
        )
        add_source = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "ADD",
                        "target": "concept/McpP6Source",
                        "to": {
                            "kind": "Concept",
                            "title": "MCP P6 source",
                            "props": {"value": 1},
                        },
                        "idem_key": "mcp-p6-add-source-0001",
                    }
                ],
                "read_set": [{"node": "book", "rev": service.writer.graph_revision()["revision"]}],
                "rationale": "MCP P6 source",
                "session_id": "session/mcp-p6-human",
                "actor_kind": "human",
                "host": "test",
            },
        )
        await client.call_tool("commit", {"proposal_id": payload(add_source)["proposal_id"]})
        source = payload(
            await client.call_tool("get", {"ref": "concept/McpP6Source", "include": "full"})
        )[0]
        add_target = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "ADD",
                        "target": "concept/McpP6Target",
                        "to": {
                            "kind": "Concept",
                            "title": "MCP P6 target",
                            "summary": "MCP original human summary",
                            "props": {
                                "_rederive": [
                                    {
                                        "sources": ["concept/McpP6Source"],
                                        "target_field": "summary",
                                        "instruction": "Update the summary from the changed fact.",
                                        "max_tokens": 200,
                                    }
                                ]
                            },
                        },
                        "idem_key": "mcp-p6-add-target-0001",
                    }
                ],
                "read_set": [{"node": "concept/McpP6Source", "rev": source["rev"]}],
                "rationale": "MCP P6 human target",
                "session_id": "session/mcp-p6-human",
                "actor_kind": "human",
                "host": "test",
            },
        )
        await client.call_tool("commit", {"proposal_id": payload(add_target)["proposal_id"]})
        source = payload(
            await client.call_tool("get", {"ref": "concept/McpP6Source", "include": "full"})
        )[0]
        trigger = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "UPDATE",
                        "target": "concept/McpP6Source",
                        "field": "props.value",
                        "from": 1,
                        "to": 2,
                        "basis_rev": source["rev"],
                        "idem_key": "mcp-p6-trigger-0001",
                    }
                ],
                "read_set": [{"node": "concept/McpP6Source", "rev": source["rev"]}],
                "rationale": "MCP P6 Tier-2 trigger",
                "session_id": "session/mcp-p6-trigger",
                "host": "test",
            },
        )
        queued = await client.call_tool("commit", {"proposal_id": payload(trigger)["proposal_id"]})

    RederiveHandler.requests = []
    RederiveHandler.idempotency_keys = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), RederiveHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    worker_env = {
        **os.environ,
        "STORYAI_DB": str(service.db_path),
        "STORYAI_PROJECT_ROOT": str(service.project_root),
        "STORYAI_REDERIVE_ENDPOINT": f"http://127.0.0.1:{httpd.server_port}/rederive",
    }
    try:
        worker = subprocess.run(
            [sys.executable, "-m", "server.cascade_worker", "--limit", "1"],
            cwd=root,
            env=worker_env,
            check=True,
            capture_output=True,
            text=True,
        )
    finally:
        httpd.shutdown()
        thread.join(timeout=5)
        httpd.server_close()
    worker_result = json.loads(worker.stdout)[0]

    async with Client(transport()) as client:
        before = payload(
            await client.call_tool("get", {"ref": "concept/McpP6Target", "include": "full"})
        )[0]
        job = await client.call_tool(
            "query",
            {
                "sql": "SELECT status, proposal FROM cascade_job WHERE id=:id",
                "params": {"id": payload(queued)["cascade"]["jobs"][0]},
            },
        )
        committed = await client.call_tool("commit", {"proposal_id": worker_result["proposal_id"]})
        after = payload(
            await client.call_tool("get", {"ref": "concept/McpP6Target", "include": "full"})
        )[0]

    assert len(tools) == 16 and any(tool.name == "lease" for tool in tools)
    assert payload(acquired)["acquired"] is True
    assert payload(conflict)["acquired"] is False
    assert payload(released)["released"] == 1
    assert payload(queued)["cascade"]["jobs"]
    assert worker_result["status"] == "proposed"
    assert RederiveHandler.requests[0]["original_human_node"]["summary"] == (
        "MCP original human summary"
    )
    assert RederiveHandler.idempotency_keys == [payload(queued)["cascade"]["jobs"][0]]
    assert payload(job)["rows"] == [["proposed", worker_result["proposal_id"]]]
    assert before["summary"] == "MCP original human summary"
    assert payload(committed)["status"] == "accepted"
    assert after["summary"] == "MCP Tier-2 summary"


@pytest.mark.asyncio
async def test_stdio_multi_project_create_switch_isolate_and_restart(service) -> None:
    root = Path(__file__).resolve().parents[1]
    registry = service.project_root.parent / "projects.json"
    novel_a = service.project_root.parent / "novel-a"
    novel_b = service.project_root.parent / "novel-b"

    def transport() -> StdioTransport:
        return StdioTransport(
            command="bash",
            args=[str(root / "server" / "run-mcp.sh")],
            env={
                "STORYAI_DB": str(service.db_path),
                "STORYAI_PROJECT_ROOT": str(service.project_root),
                "STORYAI_PROJECTS_FILE": str(registry),
            },
            cwd=str(root),
        )

    async def add_shared(client: Client, title: str, idem_key: str) -> dict:
        revision = payload(
            await client.call_tool(
                "query", {"sql": "SELECT revision FROM graph_state WHERE singleton = 1"}
            )
        )["rows"][0][0]
        proposed = await client.call_tool(
            "propose",
            {
                "ops": [
                    {
                        "verb": "ADD",
                        "target": "concept/SharedProjectNode",
                        "to": {"kind": "Concept", "title": title},
                        "idem_key": idem_key,
                    }
                ],
                "read_set": [{"node": "book", "rev": revision}],
                "rationale": f"multi-project isolation: {title}",
                "session_id": f"session/{idem_key}",
                "host": "test",
            },
        )
        return payload(
            await client.call_tool("commit", {"proposal_id": payload(proposed)["proposal_id"]})
        )

    async with Client(transport()) as client:
        tools = await client.list_tools()
        initial = payload(await client.call_tool("project", {"mode": "current"}))
        created_a = payload(
            await client.call_tool(
                "project", {"mode": "create", "name": "novel-a", "path": str(novel_a)}
            )
        )
        committed_a = await add_shared(client, "Novel A value", "multi-project-a-0001")
        created_b = payload(
            await client.call_tool(
                "project", {"mode": "create", "name": "novel-b", "path": str(novel_b)}
            )
        )
        missing_in_b = await client.call_tool(
            "get",
            {"ref": "concept/SharedProjectNode", "include": "brief"},
            raise_on_error=False,
        )
        committed_b = await add_shared(client, "Novel B value", "multi-project-b-0001")
        selected_a = payload(
            await client.call_tool("project", {"mode": "select", "name": "novel-a"})
        )
        value_a = payload(
            await client.call_tool("get", {"ref": "concept/SharedProjectNode", "include": "brief"})
        )[0]

    async with Client(transport()) as client:
        restarted = payload(await client.call_tool("project", {"mode": "current"}))
        persisted_a = payload(
            await client.call_tool("get", {"ref": "concept/SharedProjectNode", "include": "brief"})
        )[0]
        async with Client(transport()) as second_client:
            selected_b = payload(
                await second_client.call_tool("project", {"mode": "select", "name": "novel-b"})
            )
            value_b = payload(
                await second_client.call_tool(
                    "get", {"ref": "concept/SharedProjectNode", "include": "brief"}
                )
            )[0]
        observed_switch = payload(await client.call_tool("project", {"mode": "current"}))
        value_b_from_first_process = payload(
            await client.call_tool("get", {"ref": "concept/SharedProjectNode", "include": "brief"})
        )[0]
        projects = payload(await client.call_tool("project", {"mode": "list"}))

    assert len(tools) == 16 and any(tool.name == "project" for tool in tools)
    assert initial["project"]["root"] == str(service.project_root)
    assert created_a["selected"] == "novel-a" and created_a["graph"]["revision"] == 0
    assert committed_a["status"] == "accepted"
    assert created_b["selected"] == "novel-b" and created_b["graph"]["revision"] == 0
    assert missing_in_b.is_error is True
    assert committed_b["status"] == "accepted"
    assert selected_a["project"]["root"] == str(novel_a)
    assert value_a["title"] == "Novel A value"
    assert restarted["selected"] == "novel-a"
    assert persisted_a["title"] == "Novel A value"
    assert selected_b["project"]["root"] == str(novel_b)
    assert value_b["title"] == "Novel B value"
    assert observed_switch["selected"] == "novel-b"
    assert value_b_from_first_process["title"] == "Novel B value"
    assert [item["name"] for item in projects["projects"]] == ["novel-a", "novel-b", "storyai"]
    assert (novel_a / ".storyai" / "project.json").is_file()
    assert (novel_b / "store" / "story.db").is_file()


@pytest.mark.asyncio
async def test_stdio_recovers_from_unavailable_selected_project(service) -> None:
    root = Path(__file__).resolve().parents[1]
    registry = service.project_root.parent / "unavailable-projects.json"
    missing = service.project_root.parent / "missing-project"
    registry.write_text(
        json.dumps(
            {
                "version": 1,
                "selected": "missing",
                "projects": {
                    "missing": {"root": str(missing), "db": str(missing / "store/story.db")},
                    "storyai": {
                        "root": str(service.project_root),
                        "db": str(service.db_path),
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    transport = StdioTransport(
        command="bash",
        args=[str(root / "server" / "run-mcp.sh")],
        env={
            "STORYAI_DB": str(service.db_path),
            "STORYAI_PROJECT_ROOT": str(service.project_root),
            "STORYAI_PROJECTS_FILE": str(registry),
        },
        cwd=str(root),
    )

    async with Client(transport) as client:
        listed = payload(await client.call_tool("project", {"mode": "list"}))
        selected = payload(await client.call_tool("project", {"mode": "select", "name": "storyai"}))
        node = payload(
            await client.call_tool("get", {"ref": "character/한도영", "include": "brief"})
        )[0]

    by_name = {item["name"]: item for item in listed["projects"]}
    assert listed["selected"] == "missing"
    assert by_name["missing"]["available"] is False
    assert selected["selected"] == "storyai"
    assert node["title"] == "한도영"


def test_tool_descriptions_fit_two_kilobyte_budget() -> None:
    assert all(len(value.encode("utf-8")) < 2048 for value in TOOL_DESCRIPTIONS.values())
    specification = json.loads(
        (Path(__file__).resolve().parents[1] / "spec" / "tools.json").read_text(encoding="utf-8")
    )
    assert sorted(item["name"] for item in specification["tools"]) == sorted(TOOL_DESCRIPTIONS)

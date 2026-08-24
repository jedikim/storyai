from __future__ import annotations

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


@pytest.mark.asyncio
async def test_mcp_lists_tools_in_deterministic_order(
    service, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORYAI_DB", str(service.db_path))
    reset_service()
    server = create_server()
    async with Client(server) as client:
        tools = await client.list_tools()
        schema = await client.call_tool("graph_schema", {"section": "kinds"})
        outline = await client.call_tool("outline", {"scope": "book"})
        found = await client.call_tool("find", {"q": "도영"})
        node = await client.call_tool("get", {"ref": "character/한도영"})
        references = await client.call_tool("refs", {"ref": "character/한도영"})
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
    assert schema.is_error is False and schema.data["kinds"]
    assert outline.is_error is False and len(payload(outline)) == 4
    assert found.is_error is False and payload(found)[0]["id"] == "character/한도영"
    assert node.is_error is False and payload(node)[0]["title"] == "한도영"
    assert references.is_error is False and payload(references)[0]["rel"] == "present_at"
    assert proposed.is_error is False and payload(proposed)["status"] == "open"
    assert committed.is_error is False and payload(committed)["status"] == "accepted"
    by_name = {tool.name: tool for tool in tools}
    assert by_name["propose"].annotations.readOnlyHint is False
    assert by_name["commit"].annotations.destructiveHint is True


@pytest.mark.asyncio
async def test_stdio_process_connection(service) -> None:
    root = Path(__file__).resolve().parents[1]
    transport = StdioTransport(
        command="bash",
        args=[str(root / "server" / "run-mcp.sh")],
        env={"STORYAI_DB": str(service.db_path)},
        cwd=str(root),
    )
    async with Client(transport) as client:
        tools = await client.list_tools()
        result = await client.call_tool("find", {"q": "도영"})
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
    assert payload(result)[0]["id"] == "character/한도영"
    assert payload(committed)["status"] == "accepted"
    assert payload(latest)[0]["title"] == "stdio 직접 테스트"


def test_tool_descriptions_fit_two_kilobyte_budget() -> None:
    assert all(len(value.encode("utf-8")) < 2048 for value in TOOL_DESCRIPTIONS.values())

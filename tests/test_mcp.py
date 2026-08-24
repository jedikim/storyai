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
    assert [tool.name for tool in tools] == sorted(TOOL_DESCRIPTIONS)
    assert schema.is_error is False and schema.data["kinds"]
    assert outline.is_error is False and len(payload(outline)) == 4
    assert found.is_error is False and payload(found)[0]["id"] == "character/한도영"
    assert node.is_error is False and payload(node)[0]["title"] == "한도영"
    assert references.is_error is False and payload(references)[0]["rel"] == "present_at"


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
    assert [tool.name for tool in tools] == sorted(TOOL_DESCRIPTIONS)
    assert result.is_error is False
    assert payload(result)[0]["id"] == "character/한도영"


def test_tool_descriptions_fit_two_kilobyte_budget() -> None:
    assert all(len(value.encode("utf-8")) < 2048 for value in TOOL_DESCRIPTIONS.values())

from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def graph_schema(
    section: Literal["kinds", "edges", "tags", "rules"] | None = None,
    response_format: Literal["concise", "detailed"] = "concise",
    max_chars: int = 32_000,
) -> Any:
    """Inspect the current node kinds, edge relations, tags, and diagnostic rules."""
    return get_service().graph_schema(section, response_format=response_format, max_chars=max_chars)

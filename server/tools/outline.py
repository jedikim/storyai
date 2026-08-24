from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def outline(
    scope: str = "book",
    depth: int = 1,
    kind: list[str] | None = None,
    response_format: Literal["concise", "detailed"] = "concise",
    max_chars: int = 32_000,
) -> Any:
    """List graph structure as addresses and one-line summaries; never includes prose bodies."""
    return get_service().outline(
        scope,
        depth=depth,
        kind=kind,
        response_format=response_format,
        max_chars=max_chars,
    )

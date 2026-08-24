from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def find(
    q: str,
    kind: list[str] | None = None,
    tag: list[str] | None = None,
    as_of: int | None = None,
    mode: Literal["lexical", "semantic", "hybrid"] = "hybrid",
    limit: int = 20,
    response_format: Literal["concise", "detailed"] = "concise",
    max_chars: int = 32_000,
) -> Any:
    """Search titles, aliases, summaries, and indexed bible text.

    Hybrid uses lexical search in P0.
    """
    return get_service().find(
        q,
        kind=kind,
        tag=tag,
        as_of=as_of,
        mode=mode,
        limit=limit,
        response_format=response_format,
        max_chars=max_chars,
    )

from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def refs(
    ref: str,
    dir: Literal["in", "out", "both"] = "in",
    rel: list[str] | None = None,
    include_soft: bool = False,
    as_of: int | None = None,
    response_format: Literal["concise", "detailed"] = "concise",
    max_chars: int = 32_000,
) -> Any:
    """Return incoming or outgoing references. Soft prose mentions are excluded by default."""
    return get_service().refs(
        ref,
        dir=dir,
        rel=rel,
        include_soft=include_soft,
        as_of=as_of,
        response_format=response_format,
        max_chars=max_chars,
    )

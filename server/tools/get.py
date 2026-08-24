from __future__ import annotations

from typing import Any, Literal

from server.runtime import get_service


def get(
    ref: str | list[str],
    include: Literal["brief", "full", "body"] = "brief",
    as_of: int | None = None,
    response_format: Literal["concise", "detailed"] = "concise",
    max_chars: int = 32_000,
) -> Any:
    """Read node details. Request body only for one chosen node after outline or brief."""
    return get_service().get(
        ref,
        include=include,
        as_of=as_of,
        response_format=response_format,
        max_chars=max_chars,
    )
